import { useEffect, useRef, useState } from "react";
import { Modal } from "antd";
import { courseHref } from "./course-routes";
import { api, apiResponse, type Course } from "./workbench-api";
import FormalAttemptView from "./FormalAttemptView";
import QuizDraftPreview, { quizStages, type QuizJob } from "./QuizDraftPreview";
import QuizConfigDialog, { type QuizInput, type PendingQuiz } from "./QuizConfigDialog";
import Mistakes from "./Mistakes";
import "./quiz-drafts.css";

type Asset = {
  asset_id: string; latest_revision_id: string; current_revision_id: string | null; title: string; status: string;
  latest_revision_state?: string; question_count: number | null; total_score: number | null;
  duration_minutes: number | null; difficulty: string | null; updated_at: string;
  blueprint?: { question_type: string; question_count: number }[];
};
type AttemptSummary = {
  mode?: "practice" | "exam" | null;
  attempt_id: string; quiz_revision_id: string; title: string; status: string; started_at: string;
  question_count: number; answered_count: number; total_score: number | null; max_score: number;
  elapsed_seconds: number | null; deadline_at: string | null; duration_minutes: number | null; grading_status: string | null;
};
type Tab = "all" | "in-progress" | "completed" | "mistakes";
const tabs: [Tab, string][] = [["all", "全部测试"], ["in-progress", "进行中"], ["completed", "已完成"], ["mistakes", "错题"]];
const typeNames: Record<string, string> = { choice: "单选", multiple_choice: "多选", true_false: "判断", fill_blank: "填空", short_answer: "简答", calculation: "计算", proof: "证明" };
const difficultyNames: Record<string, string> = { basic: "基础", standard: "标准", advanced: "进阶", mixed: "混合" };
const modeLabel = (minutes: number | null) => minutes ? `限时测试 · ${minutes} 分钟` : "不限时测试";
const dateLabel = (date: string) => new Date(date).toLocaleString("zh-CN", { hour12: false });
const initialConfig: PendingQuiz = { quiz_input: {
  scope_mode: "course", source_document_ids: [], allow_ai_supplement: false, include_imported_questions: false,
  blueprint: [{ question_type: "choice", question_count: 5 }], duration_mode: "untimed", difficulty: "standard",
} };

export default function QuizDrafts({ courseId, routeDetail, courses = [] }: { courseId: string | null; routeDetail?: string[]; courses?: Course[] }) {
  const [items, setItems] = useState<Asset[]>([]);
  const [jobs, setJobs] = useState<QuizJob[]>([]);
  const [attempts, setAttempts] = useState<AttemptSummary[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [generating, setGenerating] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [generationError, setGenerationError] = useState("");
  const submitKey = useRef(crypto.randomUUID());
  const submitting = useRef(false);
  const [detailType, detailId, revisionId, paperAction] = routeDetail ?? [];
  const tab: Tab = detailType === "list" && tabs.some(([id]) => id === detailId) ? detailId as Tab : "all";
  const isDetail = ["papers", "attempts", "jobs", "mistakes"].includes(detailType);
  const pending = jobs.some(job => ["queued", "running"].includes(job.status));
  const hasPendingAttempts = attempts.some(attempt => attempt.status === "in_progress");
  const root = `/api/courses/${encodeURIComponent(courseId ?? "")}`;

  useEffect(() => {
    if (!courseId) return;
    const key = `open-formal-attempt-${courseId}`;
    const id = sessionStorage.getItem(key);
    if (id) { sessionStorage.removeItem(key); window.location.hash = courseHref(courseId, "quiz", ["attempts", id]); }
  }, [courseId]);
  useEffect(() => { window.scrollTo(0, 0); }, [detailType, detailId, revisionId]);
  useEffect(() => {
    if (!courseId || isDetail || tab === "mistakes") return;
    let active = true;
    if (!refresh) setLoading(true);
    const paths = ["quizzes", "formal-attempts", "quiz-jobs"];
    void Promise.allSettled(paths.map(path => api<{ items: unknown[] }>(`${root}/${path}`))).then(results => {
      if (!active) return;
      const failures: string[] = [];
      results.forEach((result, index) => {
        if (result.status === "rejected") failures.push(result.reason instanceof Error ? result.reason.message : "测试数据读取失败");
        else if (index === 0) setItems((result.value.items as Asset[]).filter(item => item.latest_revision_id));
        else if (index === 1) setAttempts(result.value.items as AttemptSummary[]);
        else setJobs(result.value.items as QuizJob[]);
      });
      setErrors(failures); setLoading(false);
    });
    return () => { active = false; };
  }, [root, courseId, isDetail, tab, refresh]);
  useEffect(() => {
    if (isDetail || tab === "mistakes" || (!pending && !hasPendingAttempts)) return;
    const timer = window.setTimeout(() => setRefresh(value => value + 1), pending ? 2000 : 30000);
    return () => window.clearTimeout(timer);
  }, [pending, hasPendingAttempts, isDetail, refresh, tab]);

  async function generate(input: QuizInput) {
    if (!courseId || submitting.current) return;
    submitting.current = true; setBusy(true); setGenerationError("");
    try {
      const path = `${root}/quiz-jobs`;
      const response = await fetch(path, { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "Idempotency-Key": submitKey.current },
        body: JSON.stringify({ quiz_input: input }),
      });
      const job = await apiResponse<QuizJob>(response, path);
      setGenerating(false); submitKey.current = crypto.randomUUID();
      window.location.hash = courseHref(courseId, "quiz", ["jobs", job.job_id]);
    } catch (failure) { setGenerationError(failure instanceof Error ? failure.message : "生成请求失败，请重试"); }
    finally { setBusy(false); submitting.current = false; }
  }

  if (!courseId) return <section className="quiz-workspace"><h1>试卷与在线测试</h1><p>请先选择课程。</p></section>;
  const returnToList = () => { window.location.hash = courseHref(courseId, "quiz"); };
  if (detailType === "papers" && detailId && revisionId) return <QuizDraftPreview key={`${detailId}-${revisionId}-${paperAction ?? ""}`} courseId={courseId}
    card={{ asset_id: detailId, revision_id: revisionId, title: "", question_count: 0, total_score: 0 }} onClose={returnToList}
    openMode={paperAction === "start"}
    onAttemptStarted={id => { window.location.hash = courseHref(courseId, "quiz", ["attempts", id]); }}
    onRevisionSaved={id => { window.location.hash = courseHref(courseId, "quiz", ["papers", detailId, id]); }} />;
  if (detailType === "attempts" && detailId) return <FormalAttemptView key={detailId} courseId={courseId} attemptId={detailId} onClose={returnToList} />;
  if (detailType === "mistakes" && detailId) return <Mistakes key={detailId} courses={courses} selectedCourseId={courseId} mistakeId={detailId} />;
  if (detailType === "jobs" && detailId) return <section className="quiz-detail-page"><button className="quiz-back" type="button" onClick={returnToList}>← 返回测试列表</button><QuizJobNotice key={detailId} courseId={courseId} jobId={detailId} /></section>;
  if (isDetail) return <section className="quiz-detail-page"><h1>测试链接不完整</h1><button className="quiz-back" type="button" onClick={returnToList}>返回测试列表</button></section>;

  const activeAttempts = attempts.filter(attempt => attempt.status === "in_progress");
  const completedAttempts = attempts.filter(attempt => attempt.status !== "in_progress");
  const taskJobs = jobs.filter(job => job.status !== "succeeded" || !job.result);
  const shownAttempts = tab === "in-progress" ? activeAttempts : completedAttempts;
  return <section className="quiz-workspace">
    <header className="quiz-workspace-header"><div><span className="quiz-eyebrow">课程测试</span><h1>试卷与在线测试</h1><p>根据课程资料生成试卷，生成成功后即可开始测试。未完成的作答可随时继续。</p></div>
      <button type="button" className="quiz-primary" onClick={() => { setGenerationError(""); setGenerating(true); }}>＋ 生成测试</button></header>
    <div className="quiz-tabs" role="tablist" aria-label="测试分类">{tabs.map(([id, label], index) => <button key={id} id={`quiz-tab-${id}`} type="button" role="tab"
      aria-selected={tab === id} aria-controls="quiz-tab-panel" tabIndex={tab === id ? 0 : -1}
      onClick={() => { window.location.hash = courseHref(courseId, "quiz", ["list", id]); }}
      onKeyDown={event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        window.location.hash = courseHref(courseId, "quiz", ["list", tabs[next][0]]);
        document.getElementById(`quiz-tab-${tabs[next][0]}`)?.focus();
      }}>{label}{id === "in-progress" && activeAttempts.length > 0 && <span>{activeAttempts.length}</span>}{id === "completed" && completedAttempts.length > 0 && <span>{completedAttempts.length}</span>}</button>)}</div>
    <div id="quiz-tab-panel" role="tabpanel" aria-labelledby={`quiz-tab-${tab}`}>
      {errors.length > 0 && <div className="quiz-error" role="alert"><p>{[...new Set(errors)].join("；")}</p><button type="button" onClick={() => setRefresh(value => value + 1)}>重新加载</button></div>}
      {tab === "mistakes" ? <Mistakes courses={courses} selectedCourseId={courseId} /> : loading ? <p role="status">正在加载测试…</p> : tab === "all" ? <>
        <div className="quiz-list-caption"><h2>我的试卷</h2><span>{items.length} 份试卷{taskJobs.length > 0 ? ` · ${taskJobs.length} 个生成任务` : ""}</span></div>
        {taskJobs.map(job => <a className="quiz-workspace-card quiz-task-card" key={job.job_id} href={courseHref(courseId, "quiz", ["jobs", job.job_id])}>
          <div><span className={`quiz-status ${job.status === "failed" ? "is-failed" : "is-pending"}`}>{job.status === "failed" ? "生成失败" : job.status === "succeeded" ? "结果待检查" : "生成中"}</span><h3>试卷生成任务</h3><p>{job.status === "failed" ? job.error || "生成暂未完成，可查看原因后重试。" : quizStages[job.stage] || "正在处理"}{job.progress ? ` · ${job.progress.completed_questions}/${job.progress.total_questions} 题` : ""}</p></div><span className="quiz-card-action">{job.status === "failed" ? "查看并重试" : "查看进度"} →</span>
        </a>)}
        {items.map(item => {
          const draft = item.latest_revision_state === "draft" || item.status === "draft";
          return <a className="quiz-workspace-card" key={item.asset_id} href={courseHref(courseId, "quiz", ["papers", item.asset_id, item.latest_revision_id])}>
            <div><span className={`quiz-status ${draft ? "is-pending" : "is-published"}`}>{item.status === "archived" ? "已归档" : draft ? item.current_revision_id ? "有新版草稿" : "草稿" : "可作答"}</span><h3>{item.title}</h3>
              <p>{modeLabel(item.duration_minutes)}{item.question_count != null ? ` · ${item.question_count} 题` : ""}{item.total_score != null ? ` · ${item.total_score} 分` : ""}{item.difficulty ? ` · ${difficultyNames[item.difficulty] ?? item.difficulty}` : ""}</p>
              {Boolean(item.blueprint?.length) && <small>{item.blueprint!.map(part => `${typeNames[part.question_type] ?? part.question_type} ${part.question_count} 题`).join(" · ")}</small>}
            </div><span className="quiz-card-action">{draft ? "预览并编辑" : "查看试卷"} →</span>
          </a>;
        })}
        {!items.length && !taskJobs.length && errors.length === 0 && <div className="quiz-empty"><h3>还没有试卷</h3><p>选择课程资料，生成你的第一份测试。</p><button type="button" className="quiz-primary" onClick={() => setGenerating(true)}>生成测试</button></div>}
      </> : <>
        <div className="quiz-list-caption"><h2>{tab === "in-progress" ? "继续上次作答" : "作答记录"}</h2><span>{shownAttempts.length} 次作答</span></div>
        {shownAttempts.map(attempt => <a className="quiz-workspace-card" key={attempt.attempt_id} href={courseHref(courseId, "quiz", ["attempts", attempt.attempt_id])}>
          <div><span className={`quiz-status ${attempt.status === "in_progress" ? "is-pending" : "is-published"}`}>{attempt.status === "in_progress" ? "作答中" : attempt.grading_status === "completed" ? "已评分" : "待完成评分"}</span><h3>{attempt.title}</h3><p>{attempt.mode === "practice" ? "日常练习" : attempt.mode === "exam" ? "模拟考试 · " + attempt.duration_minutes + " 分钟" : modeLabel(attempt.duration_minutes)} · {dateLabel(attempt.started_at)}</p>
            <small>{attempt.status === "in_progress" ? `已保存 ${attempt.answered_count} / ${attempt.question_count} 题${attempt.deadline_at ? ` · 截止 ${dateLabel(attempt.deadline_at)}` : ""}` : `${attempt.total_score != null ? `得分 ${attempt.total_score} / ${attempt.max_score}` : "评分尚未完成"}${attempt.elapsed_seconds != null ? ` · 用时 ${Math.floor(attempt.elapsed_seconds / 60)} 分 ${attempt.elapsed_seconds % 60} 秒` : ""}`}</small>
          </div><span className="quiz-card-action">{attempt.status === "in_progress" ? "继续作答" : "查看结果"} →</span>
        </a>)}
        {!shownAttempts.length && errors.length === 0 && <div className="quiz-empty"><h3>{tab === "in-progress" ? "没有进行中的作答" : "还没有已完成的作答"}</h3><p>{tab === "in-progress" ? "开始一份已发布的试卷后，可以在这里继续。" : "完成测试后，这里会保留每次作答和评分结果。"}</p><a href={courseHref(courseId, "quiz")}>浏览全部测试 →</a></div>}
      </>}
    </div>
    <Modal open={generating} onCancel={() => { if (!busy) { setGenerating(false); setPickerOpen(false); } }} footer={null} closable={false} destroyOnHidden width={820} className="quiz-generate-modal" maskClosable={!busy} keyboard={!busy}>
      <QuizConfigDialog standalone courseId={courseId} pending={initialConfig} busy={busy} error={generationError} pickerOpen={pickerOpen}
        onPickerOpenChange={setPickerOpen} onConfirm={input => void generate(input)} onCancel={() => { setGenerating(false); setPickerOpen(false); }} />
    </Modal>
  </section>;
}

function QuizJobNotice({ courseId, jobId }: { courseId: string; jobId: string }) {
  const [job, setJob] = useState<QuizJob | null>(null);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const path = `/api/courses/${encodeURIComponent(courseId)}/quiz-jobs/${encodeURIComponent(jobId)}`;
  useEffect(() => {
    let active = true;
    let timer = 0;
    async function update() {
      try {
        const next = await api<QuizJob>(path);
        if (!active) return;
        setError(""); setJob(next);
        if (next.status === "queued" || next.status === "running") timer = window.setTimeout(update, 2000);
      } catch (failure) { if (active) setError(failure instanceof Error ? failure.message : "任务暂不可用"); }
    }
    void update();
    return () => { active = false; window.clearTimeout(timer); };
  }, [path, refresh]);
  async function retry() {
    setRetrying(true); setError("");
    try { setJob(await api<QuizJob>(`${path}/retry`, "POST")); setRefresh(value => value + 1); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "暂时无法重试"); }
    finally { setRetrying(false); }
  }
  return <section className="quiz-job-notice"><span className="quiz-eyebrow">后台生成</span><h1>试卷生成任务</h1>
    {error && <div role="alert"><p>{error}</p><button type="button" onClick={() => setRefresh(value => value + 1)}>重新加载</button></div>}
    {job?.status === "failed" ? <><p role="status">本次试卷生成失败：{job.error || "请稍后重试"}</p><button type="button" className="quiz-primary" disabled={retrying} onClick={() => void retry()}>重试生成</button></>
      : job?.status === "succeeded" && job.result ? <><p>{job.result.state === "confirmed" ? "试卷已生成，可以开始测试。" : "试卷已生成，可以查看试卷。"}</p><a className="quiz-primary" href={courseHref(courseId, "quiz", ["papers", job.result.asset_id, job.result.revision_id, ...(job.result.state === "confirmed" ? ["start"] : [])])}>{job.result.state === "confirmed" ? "开始测试" : "查看试卷"} →</a></>
        : <p role="status">{job ? `${quizStages[job.stage] || "正在处理"}${job.progress ? ` · 已完成 ${job.progress.completed_questions}/${job.progress.total_questions} 题` : ""}，可离开页面等待通知。` : "正在加载任务…"}</p>}
  </section>;
}
