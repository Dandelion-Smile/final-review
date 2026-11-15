export default function QuizQuestionIndex({ questions, prefix, answeredIds = [] }: {
  questions: { question_revision_id: string; order: number }[]; prefix: string; answeredIds?: string[];
}) {
  return <aside className="quiz-question-index" aria-label="题目导航">
    <strong>题目导航</strong>
    <p>{questions.length} 道题{answeredIds.length > 0 ? ` · 已保存 ${answeredIds.length} 题` : ""}</p>
    <div>{questions.map(q => <button key={q.question_revision_id} type="button"
      className={answeredIds.includes(q.question_revision_id) ? "is-answered" : ""}
      aria-label={`跳转到第 ${q.order} 题`}
      onClick={() => {
        const target = document.getElementById(`${prefix}-${q.question_revision_id}`);
        target?.scrollIntoView({ block: "start", behavior: "instant" });
        target?.focus({ preventScroll: true });
      }}>{q.order}</button>)}</div>
  </aside>;
}
