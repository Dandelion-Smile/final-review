import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import QuizDraftActions from "./QuizDraftActions";
import { courseHref } from "./course-routes";
import { api } from "./workbench-api";
import "./quiz-drafts.css";
import QuizSources from "./QuizSources";
import QuizQuestionIndex from "./QuizQuestionIndex";

export type QuizDraftCard = { asset_id: string; revision_id: string; title: string; question_count: number; total_score: number; state?: string };
export type QuizIssue = { question_number?: number; code?: string; path?: string; message?: string; question_id?: string };
export type QuizJob = { job_id: string; status: "queued" | "running" | "failed" | "succeeded"; stage: string; progress?: { completed_questions: number; total_questions: number }; error?: string; issues?: QuizIssue[]; result?: QuizDraftCard };
function quizIssueText(issue: QuizIssue): string {
  const pathIndex = issue.path?.match(/questions\.(\d+)/);
  const idIndex = issue.question_id?.match(/^q(\d+)$/);
  const index = issue.question_number ?? (pathIndex ? Number(pathIndex[1]) + 1 : idIndex ? Number(idIndex[1]) : undefined);
  let message = issue.message || "";
  if (issue.code === "duplicate_citation" && !message.includes("同一片段被引用了两次")) {
    message = message ? `同一片段被引用了两次。${message}` : "同一片段被引用了两次";
  }
  if (!message) return "";
  return index ? `第 ${index} 题：${message}` : message;
}

export function quizFailureText(job: QuizJob): string {
  const details = (job.issues ?? []).map(quizIssueText).filter(Boolean);
  const head = `试卷生成失败：${job.error || "请稍后重试"}`;
  return details.length ? `${head} ${details.join("；")}` : head;
}

export const quizStages: Record<string, string> = { queued: "等待生成", retrieving: "读取资料", planning: "分配考点与题型", generating: "生成题目", validating: "检查题目结构与来源", reviewing: "复核答案与解析", repairing: "修复题目", publishing: "发布试卷" };
type Ref = { document_id: string; chunk_id: string; file_name: string; quote: string; available?: boolean; position_kind?: string; position?: number };
export type Question = { question_revision_id: string; order: number; score: number; question_type: string; knowledge_point: string; stem: string; options: string[]; correct_option: number | null; correct_options?: number[]; blank_answers?: string[][]; boolean_answer: boolean | null; accepted_answers: string[]; reference_answer: string; explanation: string; provenance: string; citations: { chunk_id: string; quote: string }[]; must_include: string[]; common_mistakes: string[]; scoring_tips: string; source_label: string; source_review_status?: string; references: Ref[]; candidate_references?: Ref[] };
type ReviewItem = { question_id: string; supported: boolean; issues: string[] };
export type Paper = { asset: { latest_revision_id: string; current_revision_id: string | null; status: string }; revision: { quiz_revision_id: string; title: string; state: string; total_score: number; duration_minutes?: number | null; questions: Question[]; coverage: { read_chunks: number; available_chunks: number; partial: boolean }; pending_semantic_review_question_ids?: string[]; confirmation_issues?: string[]; review_audit?: { items?: ReviewItem[] }[] } };
const typeLabels: Record<string, string> = { choice: "单选题", multiple_choice: "多选题", true_false: "判断题", fill_blank: "填空题", short_answer: "简答题", calculation: "计算题", proof: "证明题" };

export default function QuizDraftPreview({ courseId, card, openMode, onClose, onRevisionSaved, onAttemptStarted }: { courseId: string; card: QuizDraftCard; openMode?: boolean; onClose: () => void; onRevisionSaved?: (revisionId: string) => void; onAttemptStarted?: (attemptId: string) => void }) {
  const [paper, setPaper] = useState<Paper | null>(null);
  const [error, setError] = useState("");
  const [revisionId, setRevisionId] = useState(card.revision_id);
  useEffect(() => { setRevisionId(card.revision_id); }, [card.revision_id]);
  useEffect(() => {
    let active = true;
    setPaper(null); setError("");
    void api<Paper>(`/api/courses/${encodeURIComponent(courseId)}/quizzes/${encodeURIComponent(card.asset_id)}/revisions/${encodeURIComponent(revisionId)}`)
      .then(value => { if (active) setPaper(value); })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : "试卷读取失败"); });
    return () => { active = false; };
  }, [courseId, card.asset_id, revisionId]);
  return <section className="quiz-detail-page" aria-label={paper?.revision.state === "draft" ? "试卷草稿预览" : "试卷预览"}>
    <button type="button" className="quiz-back" onClick={onClose}>← 返回测试列表</button>
    {error ? <p role="alert">{error}</p> : !paper ? <p role="status">正在读取试卷…</p> : <div className="quiz-draft-preview">
      <header className="quiz-detail-header"><span className="quiz-eyebrow">{paper.revision.state === "draft" ? "试卷草稿" : "正式试卷"}</span><h1>{paper.revision.title}</h1>
      <p>{paper.revision.questions.length} 题 · 共 {paper.revision.total_score} 分 · {paper.revision.state === "draft" ? "草稿" : "正式版本"}</p>
      <p>已读取 {paper.revision.coverage.read_chunks} / {paper.revision.coverage.available_chunks} 个资料片段{paper.revision.coverage.partial ? "，本次仅覆盖部分资料。" : "。"}</p></header>
      {paper.asset.status === "confirmed" && paper.asset.current_revision_id && paper.asset.current_revision_id !== revisionId &&
        <a href={courseHref(courseId, "quiz", ["papers", card.asset_id, paper.asset.current_revision_id, "start"])}>使用当前正式版开始测试 →</a>}
      <QuizDraftActions courseId={courseId} assetId={card.asset_id} paper={paper} openMode={openMode} onAttemptStarted={onAttemptStarted} onUpdated={updated => {
        const nextId = updated.revision.quiz_revision_id;
        setPaper(updated);
        if (nextId !== revisionId) { setRevisionId(nextId); onRevisionSaved?.(nextId); }
      }} />
      <div className="quiz-paper-layout"><QuizQuestionIndex questions={paper.revision.questions} prefix="preview" /><div className="quiz-question-content">
      {paper.revision.questions.map(q => <article key={q.question_revision_id} id={`preview-${q.question_revision_id}`} tabIndex={-1}>
        <div className="quiz-question-meta"><strong>第 {q.order} 题 · {typeLabels[q.question_type]} · {q.score} 分</strong><span>{q.source_label}</span></div>
        <small>考点：{q.knowledge_point}</small>
        <ReactMarkdown>{q.stem}</ReactMarkdown>
        {q.options.length > 0 && <ol type="A">{q.options.map((option, index) => <li key={index}>{option.replace(/^[A-H][.．、:：]\s*/, "")}</li>)}</ol>}
        <details><summary>查看答案、解析与得分点</summary>
          <h4>参考答案</h4><ReactMarkdown>{q.reference_answer}</ReactMarkdown>
          <h4>解析</h4><ReactMarkdown>{q.explanation}</ReactMarkdown>
          <h4>必答点</h4><ul>{q.must_include.map((point, i) => <li key={i}>{point}</li>)}</ul>
          <h4>常见失分点</h4><ul>{q.common_mistakes.map((point, i) => <li key={i}>{point}</li>)}</ul>
          <p>{q.scoring_tips}</p>
        </details>
        <QuizSources courseId={courseId} references={q.references} />
      </article>)}</div></div>
    </div>}
  </section>;
}
