export const entryKinds = {
  study: "学习时段",
  exam: "考试",
  class: "上课安排",
  other: "其他",
  todo: "待办",
} as const;

export type EntryKind = keyof typeof entryKinds;

export type CalendarEntry = {
  entry_id: string;
  kind: EntryKind;
  title: string;
  description: string;
  course_id: string | null;
  course_name: string | null;
  scheduled_on: string | null;
  all_day: boolean;
  start_time: string | null;
  end_time: string | null;
  completed_at: string | null;
};

export type CalendarSummary = {
  items: CalendarEntry[];
  upcoming: CalendarEntry[];
  next_exam: CalendarEntry | null;
};

type Activity = { title: string; course_id: string; course_name: string; updated_at: string };
type SavedAsset = Activity & { asset_id: string; revision_id: string };

export type HomeSummary = {
  recent_note: SavedAsset | null;
  recent_test: (SavedAsset & { kind: "paper" }) | (Activity & {
    kind: "attempt";
    attempt_id: string;
    status: "in_progress" | "submitted";
  }) | null;
  recent_courses: { course_id: string; name: string; last_activity_at: string | null; updated_at: string }[];
  next_exam: CalendarEntry | null;
  pending_todos: CalendarEntry[];
};

export function entryTime(entry: CalendarEntry) {
  if (!entry.scheduled_on) return "无日期";
  if (entry.all_day) return "全天";
  return `${entry.start_time?.slice(0, 5)}–${entry.end_time?.slice(0, 5)}`;
}
