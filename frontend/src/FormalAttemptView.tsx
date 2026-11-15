import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "./workbench-api";
import "./formal-attempt.css";
import "./quiz-drafts.css";
import QuizSources from "./QuizSources";
import QuizQuestionIndex from "./QuizQuestionIndex";
import QuizAnswerInput, { answerText, cleanAnswer, type QuizAnswer } from "./QuizAnswerInput";
import { courseHref } from "./course-routes";

type AnswerValue = QuizAnswer;
type SavedAnswer = { answer: AnswerValue; saved_at: string };
type Grade = { question_revision_id: string; score: number; max_score: number; correct: boolean; student_answer: AnswerValue; reference_answer: string; feedback: string; missing_points: string[]; improvement_advice: string; rubric: { criterion: string; met: boolean; reason: string }[] };
type GradeSnapshot = { total_score: number; max_score: number; disclaimer: string | null; items: Grade[] };
type ResultReference = { document_id: string; chunk_id: string; file_name: string; quote: string; available?: boolean };
type ResultItem = Grade & { explanation: string; source_label: string; references: ResultReference[]; mistake_id?: string | null };
type FollowUp = { followup_id: string; question_revision_id: string; message: string; answer: string; citations: ResultReference[] };
type ResultDetail = { items: ResultItem[]; followups: FollowUp[] };
type Attempt = {
  attempt_id: string; quiz_revision_id: string; status: "in_progress" | "submitted";
  started_at: string; deadline_at: string | null; version: number;
  answers: Record<string, SavedAnswer>; elapsed_seconds: number | null;
  submission_reason: string | null;
  mode?: "practice" | "exam";
  submitted_questions?: Record<string, string>;
  partial_grades?: Record<string, Grade>;
  grading_snapshot?: GradeSnapshot | null;
};
type Question = { question_revision_id: string; question_type: string; knowledge_point: string; stem: string; options: string[]; order: number; score: number; blank_count?: number };
type Detail = { attempt: Attempt; quiz: { title: string; total_score: number }; questions: Question[]; server_now?: string; grading_error?: string; practice_explanations?: Record<string, string> };

function isDirty(value: AnswerValue | undefined, saved: SavedAnswer | undefined) {
  return JSON.stringify(cleanAnswer(value)) !== JSON.stringify(saved?.answer ?? null);
}

export default function FormalAttemptView({ courseId, attemptId, onClose, onSubmitted }: {
  courseId: string; attemptId: string; onClose: () => void; onSubmitted?: () => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [drafts, setDrafts] = useState<Record<string, AnswerValue>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [savingBlocked, setSavingBlocked] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [grading, setGrading] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const [clockOffset, setClockOffset] = useState(0);
  const [result, setResult] = useState<ResultDetail | null>(null);
  const [followUpDrafts, setFollowUpDrafts] = useState<Record<string, string>>({});
  const timeoutRequested = useRef(false);
  const gradingRequested = useRef(false);
  const operation = useRef(false);
  const root = `/api/courses/${encodeURIComponent(courseId)}/formal-attempts/${encodeURIComponent(attemptId)}`;
  const dirtyIds = detail?.attempt.status === "in_progress"
    ? Object.keys(drafts).filter(id => !detail.attempt.submitted_questions?.[id] && isDirty(drafts[id], detail.attempt.answers[id]))
    : [];
  const unsaved = dirtyIds.length > 0;
  useEffect(() => {
    if (!unsaved) return;
    const guard = (event: Event) => {
      if (event.type === "beforeunload") { event.preventDefault(); (event as BeforeUnloadEvent).returnValue = ""; }
      else if (!window.confirm("有尚未保存的答案，离开后这些修改会丢失。确定离开吗？")) event.preventDefault();
    };
    window.addEventListener("note-navigation", guard);
    window.addEventListener("beforeunload", guard);
    return () => { window.removeEventListener("note-navigation", guard); window.removeEventListener("beforeunload", guard); };
  }, [unsaved]);

  async function load() {
    const value = await api<Detail>(root);
    if (value.server_now) setClockOffset(Date.parse(value.server_now) - Date.now());
    setDetail(value);
    setDrafts(Object.fromEntries(Object.entries(value.attempt.answers).map(([id, item]) => [id, item.answer])));
    if (value.attempt.status === "submitted") onSubmitted?.();
  }

  useEffect(() => {
    let active = true;
    timeoutRequested.current = false;
    gradingRequested.current = false;
    setDetail(null); setError("");
    setResult(null);
    void api<Detail>(root).then(value => {
      if (!active) return;
      if (value.server_now) setClockOffset(Date.parse(value.server_now) - Date.now());
      setDetail(value);
      setDrafts(Object.fromEntries(Object.entries(value.attempt.answers).map(([id, item]) => [id, item.answer])));
    }).catch(failure => { if (active) setError(failure instanceof Error ? failure.message : "作答读取失败"); });
    return () => { active = false; };
  }, [root]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const remaining = detail?.attempt.deadline_at
    ? Math.max(0, Math.ceil((Date.parse(detail.attempt.deadline_at) - now - clockOffset) / 1000)) : null;
  useEffect(() => {
    if (remaining !== 0 || detail?.attempt.status !== "in_progress" || busy || timeoutRequested.current) return;
    timeoutRequested.current = true;
    void load().then(() => setError(""))
      .catch(failure => setError(failure instanceof Error ? failure.message : "超时交卷状态读取失败"))
      .finally(() => { timeoutRequested.current = false; });
  }, [remaining, detail?.attempt.status, busy, now]);

  useEffect(() => {
    if (detail?.attempt.status !== "submitted" || detail.attempt.grading_snapshot || gradingRequested.current || busy) return;
    gradingRequested.current = true;
    void retryGrade();
  }, [detail?.attempt.status, detail?.attempt.grading_snapshot, busy, root]);

  useEffect(() => {
    if (!detail?.attempt.grading_snapshot) return;
    let active = true;
    void api<ResultDetail>(root + "/result")
      .then(value => { if (active) setResult(value); })
      .catch(failure => { if (active) setError(failure instanceof Error ? failure.message : "逐题结果读取失败"); });
    return () => { active = false; };
  }, [detail?.attempt.grading_snapshot, root]);

  async function persist(current: Detail, questionId: string) {
    if (!isDirty(drafts[questionId], current.attempt.answers[questionId])) return current;
    const updated = await api<Detail>(root + "/answers", "PUT", {
      question_revision_id: questionId,
      answer: cleanAnswer(drafts[questionId]),
      expected_version: current.attempt.version,
    });
    setDetail(updated);
    return updated;
  }

  useEffect(() => {
    if (!detail?.attempt.mode || busy || savingBlocked || !dirtyIds.length || remaining === 0) return;
    const timer = window.setTimeout(() => { void save(dirtyIds[0]); }, 400);
    return () => window.clearTimeout(timer);
  }, [drafts, detail, busy, savingBlocked, remaining === 0]);

  async function save(questionId: string) {
    if (!detail || operation.current) return;
    operation.current = true;
    setBusy(true); setError("");
    try {
      await persist(detail, questionId);
      setSavingBlocked(false);
    } catch (failure) {
      setSavingBlocked(true);
      setError(failure instanceof Error ? failure.message : "答案保存失败，请重新加载");
    } finally { operation.current = false; setBusy(false); }
  }

  async function submitQuestion(questionId: string) {
    if (!detail || operation.current) return;
    operation.current = true;
    setBusy(true); setError(""); setPendingQuestion(questionId);
    try {
      const current = await persist(detail, questionId);
      const updated = await api<Detail>(
        root + `/questions/${encodeURIComponent(questionId)}/submit`, "POST",
        { expected_version: current.attempt.version },
      );
      if (updated.grading_error) {
        gradingRequested.current = true;
        setError(updated.grading_error);
      }
      setDetail(updated);
      if (updated.attempt.status === "submitted") onSubmitted?.();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "本题提交失败");
    } finally { operation.current = false; setPendingQuestion(null); setBusy(false); }
  }

  async function submit() {
    if (!detail || operation.current) return;
    const unanswered = detail.questions.filter(question =>
      cleanAnswer(drafts[question.question_revision_id]) === null).length;
    if (unanswered > 0 && !window.confirm(`还有 ${unanswered} 题未作答，确认交卷吗？`)) return;
    operation.current = true; setBusy(true); setSubmitting(true); setError("");
    try {
      let current = detail;
      for (const id of dirtyIds) current = await persist(current, id);
      const updated = await api<Detail>(root + "/submit", "POST");
      setDetail(updated); onSubmitted?.();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "交卷失败");
    } finally { operation.current = false; setSubmitting(false); setBusy(false); }
  }

  async function retryGrade() {
    if (operation.current) return;
    operation.current = true; setBusy(true); setGrading(true); setError("");
    try { setDetail(await api<Detail>(root + "/grade", "POST")); onSubmitted?.(); }
    catch (failure) {
      setError(failure instanceof Error ? failure.message : "评分重试失败");
      const refreshed = await api<Detail>(root).catch(() => null);
      if (refreshed) setDetail(refreshed);
    }
    finally { operation.current = false; setBusy(false); setGrading(false); }
  }

  async function ask(questionId: string) {
    const message = followUpDrafts[questionId]?.trim();
    if (!message) return;
    setBusy(true); setError("");
    try {
      await api(root + `/result/questions/${encodeURIComponent(questionId)}/follow-up`, "POST", { message });
      setResult(await api<ResultDetail>(root + "/result"));
      setFollowUpDrafts(current => ({ ...current, [questionId]: "" }));
    } catch (failure) { setError(failure instanceof Error ? failure.message : "追问失败"); }
    finally { setBusy(false); }
  }

  const active = detail?.attempt.status === "in_progress";
  const snapshot = detail?.attempt.grading_snapshot;
  const practice = detail?.attempt.mode === "practice";
  const mode = practice ? "日常练习" : detail?.attempt.mode === "exam" ? "模拟考试" : "在线作答";
  return <section className="quiz-detail-page" aria-label={detail?.quiz.title ?? "在线测试"}>
    <button type="button" className="quiz-back" onClick={onClose}>← 返回测试列表</button>
    <header className="quiz-detail-header"><span className="quiz-eyebrow">{active ? mode : "作答记录"}</span><h1>{detail?.quiz.title ?? "在线测试"}</h1></header>
    {error && <p role="alert" className="formal-error">{error}</p>}
    {!detail ? <p role="status">正在读取作答…</p> : <div className="formal-attempt">
      <p>共 {detail.questions.length} 题 · 满分 {detail.quiz.total_score} 分 · {active ? "作答中" : "已交卷"}</p>
      <p role="timer">{!active ? `用时 ${detail.attempt.elapsed_seconds ?? 0} 秒`
        : remaining == null ? "不限时" : `剩余 ${Math.floor(remaining / 60)} 分 ${remaining % 60} 秒`}</p>
      {detail.attempt.submission_reason === "timeout" && <p>考试时间已到，系统已自动交卷。</p>}
      {snapshot && <section className="formal-grade-summary"><h3>本次得分：{snapshot.total_score} / {snapshot.max_score}</h3>
        {snapshot.disclaimer && <p>{snapshot.disclaimer}</p>}
        <a href={courseHref(courseId, "quiz", ["list", "mistakes"])}>查看我的错题 →</a></section>}
      <div className="quiz-paper-layout"><QuizQuestionIndex questions={detail.questions} prefix="attempt" answeredIds={Object.keys(detail.attempt.answers)} /><div className="quiz-question-content">
      {detail.questions.map(question => {
        const id = question.question_revision_id;
        const value = drafts[id];
        const saved = detail.attempt.answers[id];
        const locked = Boolean(detail.attempt.submitted_questions?.[id]);
        const grade = snapshot?.items.find(item => item.question_revision_id === id) ?? detail.attempt.partial_grades?.[id];
        const resultItem = result?.items.find(item => item.question_revision_id === id);
        return <article key={id} id={`attempt-${id}`} tabIndex={-1}>
          <h3>第 {question.order} 题 · {question.score} 分</h3>
          <small>考点：{question.knowledge_point}</small>
          <ReactMarkdown>{question.stem}</ReactMarkdown>
          <QuizAnswerInput question={question} name={id} value={value}
            disabled={!active || locked || submitting || pendingQuestion === id || remaining === 0}
            onChange={answer => { setSavingBlocked(false); setDrafts(current => ({ ...current, [id]: answer })); }} />
          {active && !locked && <div className="formal-save">
            {practice ? <button type="button" disabled={busy || cleanAnswer(value) === null} onClick={() => void submitQuestion(id)}>提交本题</button>
              : <button type="button" disabled={busy || remaining === 0} onClick={() => void save(id)}>保存本题答案</button>}
            {savingBlocked && practice && <button type="button" disabled={busy} onClick={() => void save(id)}>重试保存</button>}
            <span>{isDirty(value, saved) ? "有未保存修改" : saved ? "已保存" : "尚未作答"}</span>
          </div>}
          {pendingQuestion === id && <p role="status">本题正在评分…</p>}
          {locked && !grade && pendingQuestion !== id && active && <div className="formal-save">
            <span>本题已提交，评分待完成</span>
            <button type="button" disabled={busy} onClick={() => void submitQuestion(id)}>重试本题评分</button>
          </div>}
          {!active && !grade && <p>{saved ? "待评分" : "未作答"}</p>}
          {grade && <section className="formal-grade"><h4>得分 {grade.score} / {grade.max_score} · {grade.student_answer == null ? "未作答" : grade.correct ? "正确" : grade.score > 0 ? "部分得分" : "答错"}</h4>
            <p><strong>你的答案：</strong>{answerText(question, grade.student_answer)}</p>
            <p><strong>参考答案：</strong>{grade.reference_answer}</p>
            <p><strong>反馈：</strong>{grade.feedback}</p>
            {grade.rubric.length > 0 && <ul>{grade.rubric.map((item, index) => <li key={index}>{item.met ? "已覆盖" : "未覆盖"}：{item.criterion}（{item.reason}）</li>)}</ul>}
            {grade.missing_points.length > 0 && <p><strong>缺失点：</strong>{grade.missing_points.join("；")}</p>}
            {grade.improvement_advice && <p><strong>改进建议：</strong>{grade.improvement_advice}</p>}
            {!resultItem && detail.practice_explanations?.[id] && <ReactMarkdown>{detail.practice_explanations[id]}</ReactMarkdown>}
          </section>}
          {resultItem && <section className="formal-result-extra">
            <h4>解析</h4><ReactMarkdown>{resultItem.explanation}</ReactMarkdown>
            <p>来源：{resultItem.source_label}</p>
            <QuizSources courseId={courseId} references={resultItem.references} />
            {resultItem.mistake_id && <a href={courseHref(courseId, "quiz", ["mistakes", resultItem.mistake_id])}>复习这道错题 →</a>}
            <h4>针对本题追问</h4>
            {result?.followups.filter(item => item.question_revision_id === id).map(item => <div className="formal-followup" key={item.followup_id}>
              <p><strong>你问：</strong>{item.message}</p><p><strong>回答：</strong>{item.answer}</p>
            </div>)}
            <label>继续追问<input value={followUpDrafts[id] ?? ""} maxLength={4000} onChange={event => setFollowUpDrafts(current => ({ ...current, [id]: event.target.value }))} /></label>
            <button type="button" disabled={busy || !followUpDrafts[id]?.trim()} onClick={() => void ask(id)}>发送追问</button>
          </section>}
        </article>;
      })}</div></div>
      {active ? <div className="formal-submit"><p>{practice ? "全部题目提交后自动结束练习。" : "交卷后可以查看答案和解析。"}</p>
        <button type="button" disabled={busy || remaining === 0} onClick={() => void submit()}>{practice ? "提前结束练习" : "交卷"}</button></div>
        : !snapshot && <div className="formal-submit"><p role="status">{grading ? "正在评分…" : "作答已提交，评分待完成。"}</p>
          {!grading && <button type="button" disabled={busy} onClick={() => void retryGrade()}>重试评分</button>}</div>}
    </div>}
  </section>;
}
