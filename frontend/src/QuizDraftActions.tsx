import { useEffect, useRef, useState } from "react";
import { Modal } from "antd";
import ReactMarkdown from "react-markdown";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./workbench-api";
import type { Paper, Question } from "./QuizDraftPreview";

type PrintView = { variant: "questions" | "answers"; title: string; markdown: string };
const objective = new Set(["choice", "multiple_choice", "true_false", "fill_blank"]);
const lines = (value: string) => value.split("\n").map(item => item.trim()).filter(Boolean);

export default function QuizDraftActions({ courseId, assetId, paper, openMode, onUpdated, onAttemptStarted }: {
  courseId: string; assetId: string; paper: Paper; openMode?: boolean; onUpdated: (paper: Paper) => void;
  onAttemptStarted?: (attemptId: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [printView, setPrintView] = useState<PrintView | null>(null);
  const [chooseMode, setChooseMode] = useState(Boolean(openMode));
  const [duration, setDuration] = useState(String(paper.revision.duration_minutes ?? 30));
  const printFrame = useRef<HTMLIFrameElement>(null);
  const revisionId = paper.revision.quiz_revision_id;
  const canStart = paper.asset.status === "confirmed" && paper.revision.state === "confirmed" &&
    paper.asset.current_revision_id === revisionId;
  const root = `/api/courses/${encodeURIComponent(courseId)}/quizzes/${encodeURIComponent(assetId)}/revisions`;
  const revisionRoot = `${root}/${encodeURIComponent(revisionId)}`;
  const pending = paper.revision.pending_semantic_review_question_ids ?? [];
  const issues = paper.revision.confirmation_issues ?? [];
  useEffect(() => {
    if (!editing || busy) return;
    const guard = (event: Event) => {
      if (event.type === "beforeunload") { event.preventDefault(); (event as BeforeUnloadEvent).returnValue = ""; }
      else if (!window.confirm("正在编辑试卷，未保存的修改将丢失。确定离开吗？")) event.preventDefault();
    };
    window.addEventListener("note-navigation", guard);
    window.addEventListener("beforeunload", guard);
    return () => { window.removeEventListener("note-navigation", guard); window.removeEventListener("beforeunload", guard); };
  }, [editing, busy]);

  function beginEdit() {
    setQuestions(paper.revision.questions.map(question => ({ ...question,
      options: [...question.options], accepted_answers: [...question.accepted_answers],
    })).sort((a, b) => a.order - b.order));
    setError(""); setEditing(true);
  }

  function change(id: string, update: Partial<Question>) {
    setQuestions(current => current.map(question => question.question_revision_id === id
      ? { ...question, ...update } : question));
  }

  function move(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= questions.length) return;
    setQuestions(current => {
      const reordered = [...current];
      [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
      return reordered.map((question, position) => ({ ...question, order: position + 1 }));
    });
  }

  async function save() {
    setBusy(true); setError("");
    try {
      const edit = questions.map(question => ({
        question_revision_id: question.question_revision_id,
        question_type: question.question_type,
        order: question.order,
        score: question.score,
        stem: question.stem,
        options: question.options,
        correct_option: question.correct_option,
        correct_options: question.correct_options ?? [],
        boolean_answer: question.boolean_answer,
        accepted_answers: question.accepted_answers,
        blank_answers: question.blank_answers ?? [],
        ...(objective.has(question.question_type) ? {} : { reference_answer: question.reference_answer }),
        explanation: question.explanation,
        provenance: question.provenance,
        citations: question.citations,
      }));
      const updated = await api<Paper>(root, "POST", { base_revision_id: revisionId, questions: edit });
      onUpdated(updated); setEditing(false);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "试卷编辑保存失败");
    } finally { setBusy(false); }
  }

  async function review() {
    setBusy(true); setError("");
    try {
      const updated = await api<Paper>(revisionRoot + "/review", "POST");
      onUpdated(updated);
      const failed = updated.revision.review_audit?.at(-1)?.items?.filter(item => !item.supported) ?? [];
      if (failed.length) setError(failed.flatMap(item => item.issues).join("；"));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "题目复核失败");
    } finally { setBusy(false); }
  }

  async function publish() {
    setBusy(true); setError("");
    try {
      const { confirmation_id } = await api<{ confirmation_id: string }>(revisionRoot + "/confirm-preview", "POST");
      const updated = await api<Paper>(revisionRoot + "/confirm", "POST", { confirmation_id });
      onUpdated(updated);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "试卷发布失败"); }
    finally { setBusy(false); }
  }

  async function showPrint(variant: PrintView["variant"]) {
    setBusy(true); setError("");
    try { setPrintView(await api<PrintView>(revisionRoot + `/print?variant=${variant}`)); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "打印预览失败"); }
    finally { setBusy(false); }
  }

  async function startAttempt(mode: "practice" | "exam") {
    setBusy(true); setError("");
    try {
      const started = await api<{ attempt: { attempt_id: string } }>(
        `/api/courses/${encodeURIComponent(courseId)}/quizzes/${encodeURIComponent(assetId)}/attempts`, "POST",
        { mode, ...(mode === "exam" ? { duration_minutes: Number(duration) } : {}) }
      );
      if (onAttemptStarted) onAttemptStarted(started.attempt.attempt_id);
      else {
        sessionStorage.setItem(`open-formal-attempt-${courseId}`, started.attempt.attempt_id);
        window.location.hash = "#quiz";
      }
    } catch (failure) { setError(failure instanceof Error ? failure.message : "开始测试失败"); }
    finally { setBusy(false); }
  }

  const printBody = printView ? renderToStaticMarkup(<ReactMarkdown>{printView.markdown}</ReactMarkdown>) : "";
  const printDocument = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
    @page{size:A4;margin:20mm}body{font:11pt/1.6 "Microsoft YaHei",sans-serif;color:#17232a}
    h1,h2,h3{break-after:avoid}article,li{break-inside:avoid}pre{white-space:pre-wrap}
    </style></head><body>${printBody}</body></html>`;

  return <div className="quiz-actions">
    {error && <p role="alert" className="quiz-action-error">{error}</p>}
    {paper.revision.state === "draft" && <>
      {issues.map(issue => <p key={issue} role="status" className="quiz-action-warning">发布前需处理：{issue}</p>)}
      {pending.length > 0 && <p role="status" className="quiz-action-warning">{pending.length} 道编辑后的题目待语义复核，复核前来源仅作候选展示。</p>}
      {!editing && <>
        {paper.asset.current_revision_id && <p>发布新版后，新作答将使用新版，已有作答继续使用原版本。</p>}
        <div className="quiz-action-buttons">
          <button type="button" disabled={busy} onClick={beginEdit}>编辑试卷</button>
          {pending.length > 0 && <button type="button" disabled={busy} onClick={() => void review()}>{busy ? "正在复核…" : "复核编辑内容"}</button>}
          <button type="button" disabled={busy || pending.length > 0 || issues.length > 0} onClick={() => void publish()}>{paper.asset.current_revision_id ? "发布新版" : "发布试卷"}</button>
        </div>
      </>}
    </>}
    {paper.revision.state === "confirmed" || paper.revision.state === "superseded" ? <div className="quiz-action-buttons">
      {paper.asset.latest_revision_id === revisionId && <button type="button" disabled={busy} onClick={beginEdit}>编辑新草稿</button>}
      {canStart && <button type="button" disabled={busy} aria-expanded={chooseMode} onClick={() => setChooseMode(value => !value)}>开始测试</button>}
      <button type="button" disabled={busy} onClick={() => void showPrint("questions")}>打印仅题目版</button>
      <button type="button" disabled={busy} onClick={() => void showPrint("answers")}>打印题目与答案解析版</button>
    </div> : null}
    {canStart && chooseMode && <section className="quiz-confirm-summary" aria-label="选择测试模式">
      <p>日常练习逐题查看反馈。模拟考试在交卷后统一查看答案和解析。</p>
      <div className="quiz-action-buttons">
        <button type="button" disabled={busy} onClick={() => void startAttempt("practice")}>开始日常练习</button>
        <label>考试时长（分钟） <input type="number" min={1} max={240} step={1}
          value={duration} onChange={event => setDuration(event.target.value)} /></label>
        <button type="button" disabled={busy || !Number.isInteger(Number(duration)) || Number(duration) < 1 || Number(duration) > 240}
          onClick={() => void startAttempt("exam")}>开始模拟考试</button>
      </div>
    </section>}
    {editing && <div className="quiz-edit-form">
      <h3>编辑试卷</h3>
      <p>保存会创建新的草稿版本，原版本不会改变。</p>
      {questions.map((question, index) => <section key={question.question_revision_id}>
        <div className="quiz-question-meta"><strong>第 {index + 1} 题 · {question.question_type}</strong>
          <span><button type="button" disabled={index === 0} onClick={() => move(index, -1)}>上移</button>
            <button type="button" disabled={index === questions.length - 1} onClick={() => move(index, 1)}>下移</button></span></div>
        <label>题干<textarea value={question.stem} onChange={event => change(question.question_revision_id, { stem: event.target.value })} /></label>
        <label>分值<input type="number" min="0.01" max="10000" step="0.01" value={question.score} onChange={event => change(question.question_revision_id, { score: Number(event.target.value) })} /></label>
        {["choice", "multiple_choice"].includes(question.question_type) && <>
          <label>选项（每行一项）<textarea value={question.options.join("\n")} onChange={event => change(question.question_revision_id, { options: lines(event.target.value) })} /></label>
          {question.question_type === "choice" ? <label>正确选项<select value={question.correct_option ?? ""} onChange={event => change(question.question_revision_id, { correct_option: Number(event.target.value) })}>
            {question.options.map((_, optionIndex) => <option key={optionIndex} value={optionIndex}>{String.fromCharCode(65 + optionIndex)}</option>)}
          </select></label> : <fieldset><legend>正确选项</legend>
            {question.options.map((option, index) => {
              const selected = question.correct_options ?? [];
              return <label key={index}><input type="checkbox" checked={selected.includes(index)}
                onChange={event => change(question.question_revision_id, {
                  correct_options: event.target.checked ? [...selected, index] : selected.filter(item => item !== index),
                })} />{option}</label>;
            })}
          </fieldset>}
        </>}
        {question.question_type === "true_false" && <label>判断题答案<select value={String(question.boolean_answer)} onChange={event => change(question.question_revision_id, { boolean_answer: event.target.value === "true" })}>
          <option value="true">正确</option><option value="false">错误</option></select></label>}
        {question.question_type === "fill_blank" && (question.blank_answers?.length
          ? question.blank_answers.map((answers, blank) => <label key={blank}>第 {blank + 1} 空的可接受答案（每行一项）
            <textarea value={answers.join("\n")} onChange={event => change(question.question_revision_id, {
              blank_answers: question.blank_answers!.map((value, index) => index === blank ? lines(event.target.value) : value),
            })} />
          </label>)
          : <label>可接受答案（每行一项）<textarea value={question.accepted_answers.join("\n")} onChange={event => change(question.question_revision_id, { accepted_answers: lines(event.target.value) })} /></label>)}
        {!objective.has(question.question_type) && <label>参考答案<textarea value={question.reference_answer} onChange={event => change(question.question_revision_id, { reference_answer: event.target.value })} /></label>}
        <label>解析<textarea value={question.explanation} onChange={event => change(question.question_revision_id, { explanation: event.target.value })} /></label>
      </section>)}
      <div className="quiz-action-buttons"><button type="button" disabled={busy} onClick={() => void save()}>{busy ? "保存中…" : "保存新草稿"}</button>
        <button type="button" disabled={busy} onClick={() => setEditing(false)}>取消</button></div>
    </div>}
    <Modal open={Boolean(printView)} title={printView?.variant === "questions" ? "仅题目版" : "题目与答案解析版"} onCancel={() => setPrintView(null)} width={900}
      footer={<button type="button" onClick={() => printFrame.current?.contentWindow?.print()}>打印</button>}>
      <iframe ref={printFrame} title="试卷打印预览" className="quiz-print-frame" sandbox="allow-same-origin allow-modals" srcDoc={printDocument} />
    </Modal>
  </div>;
}
