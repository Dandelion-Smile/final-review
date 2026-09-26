export type CourseStatus = "active" | "archived" | "deleted";
export type Course = {
  course_id: string;
  name: string;
  subject?: string | null;
  status?: CourseStatus;
  updated_at: string;
  purge_after?: string | null;
};
export type BlueprintItem = { question_type: string; question_count: number; score: number };
export type ExamInput = {
  name: string;
  exam_at: string | null;
  total_score: number | null;
  blueprint: BlueprintItem[];
  emphasis: string[];
  exclusions: string[];
  notes: string;
  generation_preferences: Record<string, string | number | boolean>;
};
export type Exam = ExamInput & {
  exam_id: string;
  course_id: string;
  status: "active" | "archived";
  updated_at: string;
};
export type DeletionPreview = {
  impact: { materials: number; exams: number; learning_assets: number; attempts: number };
  confirmation_id: string;
  expires_at: string;
};

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function api<T>(path: string, method = "GET", data?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: data === undefined ? undefined : { "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : "请求失败，请稍后重试";
    throw new ApiError(detail, response.status);
  }
  return body as T;
}
