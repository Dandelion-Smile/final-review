import { courseHref } from "./course-routes";

export type KnowledgeSource = {
  document_id: string; title: string; chapter: string; locator_id: string;
  position_kind: string | null; position: number | null; ordinal: number;
};
export type KnowledgePoint = {
  id: string; name: string; description: string; importance_score: number | null;
  fact_count: number; fact_ids: string[]; sources: KnowledgeSource[];
};
export type KnowledgeChapter = { id: string; title: string; points: KnowledgePoint[] };
export type KnowledgeTree = {
  chapters: KnowledgeChapter[]; point_count: number; updated_at: string | null;
  build_status: Partial<Record<"queued" | "running" | "failed" | "succeeded", number>>;
};
export type KnowledgeDetail = { facts: { id: string; fact_text: string; status: string }[] };
export type OverviewMaterial = {
  document_id: string; title: string; file_name?: string; chapter?: string;
  parse_status: string; updated_at?: string | null; uploaded_at?: string | null;
};
export type OverviewNote = {
  asset_id: string; title: string; latest_revision_id: string; status: string;
  updated_at: string; note_type: string; sources: string[];
};
export type OverviewQuiz = {
  asset_id: string; title: string; latest_revision_id: string; current_revision_id: string | null;
  latest_revision_state?: string; status: string; question_count: number | null; updated_at: string;
};
export type OverviewAttempt = {
  attempt_id: string; quiz_revision_id: string; title: string; status: string;
  started_at: string; question_count: number; answered_count: number;
  total_score: number | null; max_score: number; grading_status: string | null;
};
export type OverviewTestLink = { id: string; title: string; href: string; meta: string; status: string; score?: string };

export function sourcePosition(source: KnowledgeSource) {
  if (source.position == null) return "来源片段";
  if (source.position_kind === "slide") return `第 ${source.position} 张幻灯片`;
  if (source.position_kind === "page") return `第 ${source.position} 页`;
  return "来源片段";
}

export function overviewTests(courseId: string, quizzes: OverviewQuiz[], attempts: OverviewAttempt[]) {
  return quizzes.filter(quiz => quiz.latest_revision_id).map((quiz): OverviewTestLink => {
    const matching = attempts.filter(attempt => [quiz.latest_revision_id, quiz.current_revision_id].includes(attempt.quiz_revision_id));
    const latest = (items: OverviewAttempt[]) => [...items].sort((a, b) => b.started_at.localeCompare(a.started_at))[0];
    const attempt = latest(matching.filter(item => item.status === "in_progress")) ?? latest(matching);
    if (attempt) return {
      id: quiz.asset_id, title: attempt.title,
      href: courseHref(courseId, "quiz", ["attempts", attempt.attempt_id]),
      meta: attempt.status === "in_progress" ? `已保存 ${attempt.answered_count} / ${attempt.question_count} 题` : `${attempt.question_count} 道题`,
      status: attempt.status === "in_progress" ? "继续作答" : attempt.grading_status === "completed" ? "查看结果" : "待完成评分",
      score: attempt.grading_status === "completed" && attempt.total_score != null ? `${attempt.total_score} / ${attempt.max_score}` : undefined,
    };
    return {
      id: quiz.asset_id, title: quiz.title,
      href: courseHref(courseId, "quiz", ["papers", quiz.asset_id, quiz.latest_revision_id]),
      meta: quiz.question_count == null ? "查看试卷" : `${quiz.question_count} 道题`,
      status: quiz.status === "archived" ? "已归档" : quiz.latest_revision_state === "draft" || quiz.status === "draft" ? "草稿" : "查看试卷",
    };
  });
}
