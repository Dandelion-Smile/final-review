import { courseHref } from "./course-routes";

export type KnowledgeSource = {
  document_id: string; title: string; chapter: string; locator_id: string;
  position_kind: string | null; position: number | null; ordinal: number;
};
export type CatalogPlacement = "unclassified" | "pending_catalog";
export type KnowledgePoint = {
  id: string; name: string; description: string; importance_score: number | null;
  fact_count: number; fact_ids: string[]; sources: KnowledgeSource[];
  catalog_placement?: CatalogPlacement;
};
export type KnowledgeChapter = {
  id: string; title: string; description?: string; points: KnowledgePoint[];
};
export type CatalogStatus = "uninitialized" | "ready" | "stale";
export type CourseCatalog = {
  status: CatalogStatus;
  version_id: string | null;
  revision: number | null;
  knowledge_snapshot_hash: string | null;
  chapter_count: number;
  assigned_count: number;
  unassigned_count: number;
  is_updating: boolean;
};
export type CatalogJobStatus = "queued" | "running" | "retry_wait" | "succeeded" | "failed";
export type CatalogJob = {
  job_id: string;
  mode: "initial" | "incremental";
  status: CatalogJobStatus;
  stage: string | null;
  attempt: number;
  next_run_at: string | null;
  result_version_id: string | null;
  error_code: string | null;
};
export type KnowledgeTree = {
  chapters: KnowledgeChapter[]; point_count: number; updated_at: string | null;
  build_status: Partial<Record<"queued" | "running" | "failed" | "succeeded", number>>;
  catalog?: CourseCatalog;
  catalog_job?: CatalogJob | null;
};
export type CatalogNotice = { tone: "neutral" | "progress" | "attention" | "warning"; text: string };

const failureSummaries = new Map([
  ["CatalogOutputInvalid", "目录结果未通过校验。"],
  ["CatalogBudgetExceeded", "目录任务已达到生成预算上限。"],
]);

export function catalogFailureSummary(job: CatalogJob) {
  return failureSummaries.get(job.error_code ?? "") ?? "";
}

export const UNASSIGNED_CHAPTER_ID = "未分章";

export function isVirtualUnassigned(chapter: KnowledgeChapter) {
  return chapter.id === UNASSIGNED_CHAPTER_ID;
}

function hasFormalCatalog(catalog?: CourseCatalog | null) {
  return catalog?.status === "ready" || catalog?.status === "stale";
}

export function overviewChapterSummary(tree: KnowledgeTree) {
  const catalog = tree.catalog;
  const points = `${tree.point_count} 个知识点`;
  if (catalog && hasFormalCatalog(catalog)) return `${catalog.chapter_count} 章 · ${points}`;
  return tree.point_count > 0 ? `尚未生成章节 · ${points}` : "";
}

export function placementLabel(point: KnowledgePoint) {
  if (point.catalog_placement === "unclassified") return "暂未归入章节";
  if (point.catalog_placement === "pending_catalog") return "等待目录整理";
  return "";
}

export function visibleChapters(chapters: KnowledgeChapter[], query: string, catalog?: CourseCatalog | null) {
  const needle = query.trim().toLocaleLowerCase();
  const formal = hasFormalCatalog(catalog);
  return chapters.map(chapter => ({
    ...chapter,
    points: chapter.points.filter(point => !needle || chapter.title.toLocaleLowerCase().includes(needle) || point.name.toLocaleLowerCase().includes(needle)),
  })).filter(chapter => {
    if (isVirtualUnassigned(chapter)) return chapter.points.length > 0;
    if (needle) return chapter.points.length > 0 || chapter.title.toLocaleLowerCase().includes(needle);
    return formal || chapter.points.length > 0;
  });
}

export function overviewActivity(tree: KnowledgeTree | null, materials: OverviewMaterial[]) {
  const build = tree?.build_status;
  const job = tree?.catalog_job;
  return {
    parsing: materials.some(item => item.parse_status === "queued" || item.parse_status === "running"),
    building: Boolean(build?.queued || build?.running),
    catalog: job?.status === "queued" || job?.status === "running" || job?.status === "retry_wait",
  };
}

export function catalogFailed(job?: CatalogJob | null) {
  return job?.status === "failed" && job.error_code !== "CatalogNoChanges"
    && job.error_code !== "SupersededBeforeExecution";
}

export function catalogNotice(tree: KnowledgeTree): CatalogNotice | null {
  const catalog = tree.catalog;
  const job = tree.catalog_job;
  const formal = hasFormalCatalog(catalog);
  const previous = formal ? "，当前展示上一版本" : "";

  if (job?.status === "queued") return {
    tone: "progress", text: `${formal ? "课程知识目录更新已排队" : "课程知识目录生成已排队"}${previous}`,
  };
  if (job?.status === "running") return {
    tone: "progress", text: formal ? "正在更新知识目录，当前展示上一版本" : "正在生成课程知识目录",
  };
  if (job?.status === "retry_wait") return {
    tone: "attention", text: `整理暂时中断，等待自动重试${previous}`,
  };
  if (catalogFailed(job)) return {
    tone: "warning", text: formal ? "知识目录更新失败，当前展示上一版本" : "课程知识目录生成失败",
  };
  if (catalog?.status === "stale") return { tone: "attention", text: "课程资料已有更新，目录尚未同步" };
  if (job?.error_code === "CatalogNoChanges") return { tone: "neutral", text: "目录没有需要发布的新版本" };
  if (catalog?.status === "ready") return { tone: "neutral", text: "课程知识目录已更新" };
  if (tree.point_count > 0) return { tone: "neutral", text: "当前按资料章节展示" };
  return null;
}
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
