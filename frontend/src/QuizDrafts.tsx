import { useEffect, useState } from "react";
import { courseHref } from "./course-routes";
import { api } from "./workbench-api";
import FormalAttemptView from "./FormalAttemptView";
import QuizDraftPreview, { type QuizDraftCard, type QuizJob } from "./QuizDraftPreview";

type Asset = { asset_id: string; latest_revision_id: string; title: string; status: string };
type AttemptSummary = { attempt_id: string; quiz_revision_id: string; status: string; started_at: string };
export default function QuizDrafts({ courseId, routeDetail }: { courseId: string | null; routeDetail?: string[] }) {
  const [items, setItems] = useState<Asset[]>([]);
  const [selected, setSelected] = useState<QuizDraftCard | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [attempts, setAttempts] = useState<AttemptSummary[]>([]);
  const [activeAttemptId, setActiveAttemptId] = useState<string | null>(null);
  const [attemptRefresh, setAttemptRefresh] = useState(0);
  const [detailType, detailId, revisionId] = routeDetail ?? [];
  const linkedAttemptId = detailType === "attempts" ? detailId : undefined;
  const linkedJobId = detailType === "jobs" ? detailId : undefined;
  const linkedPaper = detailType === "papers" && detailId && revisionId
    ? { asset_id: detailId, revision_id: revisionId, title: "", question_count: 0, total_score: 0 }
    : null;
  const paper = linkedPaper ?? selected;
  const attemptId = linkedAttemptId ?? activeAttemptId;
  useEffect(() => {
    if (!courseId) return;
    const key = `open-formal-attempt-${courseId}`;
    const pendingId = sessionStorage.getItem(key);
    if (pendingId) { setActiveAttemptId(pendingId); sessionStorage.removeItem(key); }
  }, [courseId]);
  useEffect(() => {
    let active = true;
    setItems([]); setSelected(null); setError(""); setLoading(Boolean(courseId));
    if (courseId) void api<{ items: Asset[] }>(`/api/courses/${encodeURIComponent(courseId)}/quizzes`)
      .then(data => { if (active) setItems(data.items.filter(item => Boolean(item.latest_revision_id))); })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : "试卷列表读取失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [courseId]);
  useEffect(() => {
    let active = true;
    setAttempts([]);
    if (courseId) void api<{ items: AttemptSummary[] }>(`/api/courses/${encodeURIComponent(courseId)}/formal-attempts`)
      .then(value => { if (active) setAttempts(value.items); })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : "作答记录读取失败"); });
    return () => { active = false; };
  }, [courseId, attemptRefresh]);
  return <section className="quiz-drafts-list"><h1>试卷与在线测试</h1><p>在 AI 对话中生成试卷草稿，编辑确认后开始测试；作答记录可在下方找回。</p>
    {courseId && linkedJobId && <QuizJobNotice key={linkedJobId} courseId={courseId} jobId={linkedJobId} />}
    {error && <p role="alert">{error}</p>}
    {loading ? <p role="status">正在加载…</p> : !items.length && <p>{courseId ? "当前课程还没有试卷。" : "请先选择课程。"}</p>}
    {items.map(item => <button type="button" key={item.asset_id} onClick={() => setSelected({ asset_id: item.asset_id, revision_id: item.latest_revision_id, title: item.title, question_count: 0, total_score: 0 })}><strong>{item.title}</strong><span>{item.status === "confirmed" ? "查看最新版本 →" : "查看草稿 →"}</span></button>)}
    {attempts.length > 0 && <section className="quiz-attempt-list"><h2>我的作答</h2>
      {attempts.map(attempt => <button type="button" key={attempt.attempt_id} onClick={() => setActiveAttemptId(attempt.attempt_id)}>
        <strong>{new Date(attempt.started_at).toLocaleString("zh-CN")}</strong>
        <span>{attempt.status === "in_progress" ? "继续作答" : "查看已交卷记录"}</span>
      </button>)}
    </section>}
    {paper && !attemptId && courseId && <QuizDraftPreview key={paper.asset_id} courseId={courseId} card={paper}
      onClose={() => {
        setSelected(null);
        if (linkedPaper) window.location.hash = courseHref(courseId, "quiz");
      }}
      onAttemptStarted={id => {
        setSelected(null);
        if (linkedPaper) window.location.hash = courseHref(courseId, "quiz", ["attempts", id]);
        else setActiveAttemptId(id);
        setAttemptRefresh(value => value + 1);
      }}
      onRevisionSaved={id => {
        if (linkedPaper) window.location.hash = courseHref(courseId, "quiz", ["papers", paper.asset_id, id]);
        else setSelected(current => current ? { ...current, revision_id: id } : null);
        setItems(current => current.map(item => item.asset_id === paper.asset_id ? { ...item, latest_revision_id: id } : item));
      }} />}
    {attemptId && courseId && <FormalAttemptView key={attemptId} courseId={courseId} attemptId={attemptId}
      onClose={() => {
        setActiveAttemptId(null);
        if (linkedAttemptId) window.location.hash = courseHref(courseId, "quiz");
      }} onSubmitted={() => setAttemptRefresh(value => value + 1)} />}
  </section>;
}

function QuizJobNotice({ courseId, jobId }: { courseId: string; jobId: string }) {
  const [job, setJob] = useState<QuizJob | null>(null);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState(false);
  const path = `/api/courses/${encodeURIComponent(courseId)}/quiz-jobs/${encodeURIComponent(jobId)}`;
  useEffect(() => {
    let active = true;
    let timer = 0;
    async function refresh() {
      try {
        const next = await api<QuizJob>(path);
        if (!active) return;
        setJob(next);
        if (next.status === "queued" || next.status === "running") timer = window.setTimeout(refresh, 2000);
      } catch (failure) {
        if (active) setError(failure instanceof Error ? failure.message : "任务暂不可用");
      }
    }
    void refresh();
    return () => { active = false; window.clearTimeout(timer); };
  }, [path, retrying]);
  async function retry() {
    setRetrying(true);
    setError("");
    try { setJob(await api<QuizJob>(`${path}/retry`, "POST")); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "暂时无法重试"); }
    finally { setRetrying(false); }
  }
  return <section className="account-card quiz-job-notice" aria-label="试卷生成任务">
    <h2>试卷生成任务</h2>
    {error && <p role="alert">{error}</p>}
    {job?.status === "failed" ? <>
      <p role="status">本次试卷生成失败。</p>
      <button type="button" className="account-secondary" disabled={retrying} onClick={() => void retry()}>重试生成</button>
    </> : job?.status === "succeeded" && job.result
      ? <a className="account-text-link" href={courseHref(courseId, "quiz", ["papers", job.result.asset_id, job.result.revision_id])}>查看生成的试卷草稿</a>
      : <p role="status">{job ? "生成任务正在处理，可离开页面等待通知。" : "正在加载任务…"}</p>}
  </section>;
}
