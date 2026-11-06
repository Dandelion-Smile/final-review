export type CoursePage = "materials" | "notes" | "quiz" | "home" | "mistakes" | "report";
export type Page = CoursePage | "workbench" | "dashboard" | "calendar" | "account" | "notifications";
export type CourseRoute = { page: Page; courseId?: string; detail?: string[] };
const sections: Record<string, CoursePage> = { materials: "materials", notes: "notes", quizzes: "quiz", tutor: "home", mistakes: "mistakes", report: "report" };
const paths: Record<CoursePage, string> = { materials: "materials", notes: "notes", quiz: "quizzes", home: "tutor", mistakes: "mistakes", report: "report" };
const decode = (value: string) => { try { return decodeURIComponent(value); } catch { return value; } };

export function courseHref(id: string, page: CoursePage = "materials", detail: string[] = []) {
  return `#/courses/${encodeURIComponent(id)}/${paths[page]}${detail.length ? "/" + detail.map(encodeURIComponent).join("/") : ""}`;
}

export function readRoute(hash: string): CourseRoute {
  if (hash === "#/home") return { page: "dashboard" };
  if (hash === "#/calendar") return { page: "calendar" };
  const calendarDate = hash.match(/^#\/calendar\/(\d{4}-\d{2}-\d{2})$/);
  if (calendarDate) return { page: "calendar", detail: [calendarDate[1]] };
  if (hash === "#/account") return { page: "account" };
  if (hash === "#/notifications") return { page: "notifications" };
  const parts = hash.replace(/^#\/?/, "").split("/").map(decode);
  if (parts[0] === "courses") {
    if (!parts[1]) return { page: "workbench" };
    return { page: sections[parts[2]] ?? "materials", courseId: parts[1], detail: parts.slice(3) };
  }
  // Keep links in existing notes, citations and chat cards usable.
  if (parts[0] === "chat") return { page: "home", courseId: parts[1], detail: parts.slice(2) };
  if (parts[0] === "materials") return { page: "materials", courseId: parts[1], detail: parts.slice(2) };
  if (parts[0] === "note") return { page: "notes", detail: parts.slice(1) };
  if (["notes", "quiz", "home", "mistakes", "report"].includes(parts[0])) return { page: parts[0] as CoursePage };
  return { page: "workbench" };
}

/** Adapt scoped URLs for the existing document and note detail readers. */
export function legacyHash(hash: string): string {
  if (!hash.startsWith("#/courses/")) return hash;
  const route = readRoute(hash);
  if (route.page === "notes") return route.detail?.length ? `#note/${route.detail.join("/")}` : "#notes";
  if (route.page === "materials") return route.detail?.length ? `#materials/${encodeURIComponent(route.courseId!)}/${route.detail.map(encodeURIComponent).join("/")}` : "#materials";
  return hash;
}
