import { courseHref } from "./course-routes";

export type QuizReference = { document_id: string; chunk_id: string; file_name: string; quote: string; available?: boolean; position_kind?: string; position?: number };

export default function QuizSources({ courseId, references }: { courseId: string; references: QuizReference[] }) {
  if (!references.length) return null;
  return <details className="quiz-sources">
    <summary>题目来源 · {references.length} 处引用</summary>
    <div className="quiz-sources-content">{references.map((ref, index) => <section key={`${ref.chunk_id}-${index}`}>
      <strong>{ref.file_name}{ref.position != null ? ` · 第 ${ref.position} ${ref.position_kind === "slide" ? "张幻灯片" : "页"}` : ""}</strong>
      <blockquote>{ref.quote}</blockquote>
      {ref.available !== false
        ? <a href={courseHref(courseId, "materials", [ref.document_id, ref.chunk_id])}>查看原文 →</a>
        : <span>原资料已不可用，保留来源摘录。</span>}
    </section>)}</div>
  </details>;
}
