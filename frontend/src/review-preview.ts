export function dateKey(date: Date) {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

export function addDays(date: Date, days: number) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days, 12);
}

export const shortDate = new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric" });
export const fullDate = new Intl.DateTimeFormat("zh-CN", { dateStyle: "full" });
export function localDate(date: string) {
  return new Date(`${date}T12:00:00`);
}

export function calendarQuery(now: Date) {
  return new URLSearchParams({
    today: dateKey(now),
    at: now.toTimeString().slice(0, 8),
  });
}

export function daysUntil(date: string, today: Date) {
  return (Date.parse(`${date}T00:00:00Z`) - Date.parse(`${dateKey(today)}T00:00:00Z`)) / 86_400_000;
}
