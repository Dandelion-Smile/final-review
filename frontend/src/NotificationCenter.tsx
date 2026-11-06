import { useEffect, useState } from "react";
import { errorMessage } from "./account-api";
import { courseHref } from "./course-routes";
import { api } from "./workbench-api";

type Kind = "quiz_ready" | "quiz_failed" | "grading_ready" | "calendar";
type Target = {
  course_id?: string; asset_id?: string; revision_id?: string; job_id?: string;
  conversation_id?: string; attempt_id?: string; date?: string;
};
type Item = {
  id: number; kind: Kind; title: string; visible_at: string; read_at: string | null; target: Target;
};
type Feed = { items: Item[]; unread: number; next_before: number | null };
const feedPath = "/api/account/notifications";
const changeEvent = "final-review-notifications-read";
const kindLabels: Record<Kind, string> = {
  quiz_ready: "试卷待确认", quiz_failed: "生成任务", grading_ready: "评分结果", calendar: "日程提醒",
};
const dateFormat = new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" });

function destination({ kind, target }: Item) {
  if (kind === "calendar") return target.date ? `#/calendar/${encodeURIComponent(target.date)}` : "#/calendar";
  if (!target.course_id) return "#/courses";
  const detail = kind === "quiz_ready" && target.asset_id && target.revision_id
    ? ["papers", target.asset_id, target.revision_id]
    : kind === "grading_ready" && target.attempt_id ? ["attempts", target.attempt_id]
      : kind === "quiz_failed" && target.job_id ? ["jobs", target.job_id] : [];
  return courseHref(target.course_id, "quiz", detail);
}

export function NotificationBell() {
  const [unread, setUnread] = useState(0);
  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const feed = await api<Feed>(`${feedPath}?limit=1`);
        if (active) setUnread(feed.unread);
      } catch {
        // Keep the navigation entry available when the badge cannot refresh.
      }
    }
    void refresh();
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, 30_000);
    window.addEventListener(changeEvent, refresh);
    window.addEventListener("focus", refresh);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener(changeEvent, refresh);
      window.removeEventListener("focus", refresh);
    };
  }, []);

  return <a className="notification-bell" href="#/notifications"
    aria-label={unread ? `通知中心，${unread} 条未读` : "通知中心"} title="通知中心">
    <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9m-8 12a2 2 0 0 0 4 0" />
    </svg>
    {unread > 0 && <span aria-hidden="true">{unread > 99 ? "99+" : unread}</span>}
  </a>;
}

export default function NotificationCenter() {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [marking, setMarking] = useState(false);

  async function load(before?: number) {
    setLoading(true);
    setError("");
    try {
      const updated = await api<Feed>(before ? `${feedPath}?before=${before}` : feedPath);
      setFeed(previous => before && previous
        ? { ...updated, items: [...previous.items, ...updated.items] } : updated);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void load(); }, []);

  async function markRead(item?: Item) {
    setMarking(true);
    setError("");
    try {
      await api(item ? `${feedPath}/${item.id}/read` : `${feedPath}/read-all`, "POST");
      const readAt = new Date().toISOString();
      setFeed(previous => previous && {
        ...previous,
        unread: item ? Math.max(0, previous.unread - 1) : 0,
        items: previous.items.map(row => !row.read_at && (!item || row.id === item.id)
          ? { ...row, read_at: readAt } : row),
      });
      window.dispatchEvent(new Event(changeEvent));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setMarking(false);
    }
  }

  return <section className="account-center notification-center" aria-labelledby="notification-heading">
    <div className="account-center-heading notification-heading">
      <div><h1 id="notification-heading">通知中心</h1><p>{feed ? `${feed.unread} 条未读通知` : "查看测试进度、评分结果和复习提醒。"}</p></div>
      <div className="notification-actions">
        <button className="account-secondary" type="button" onClick={() => void load()} disabled={loading || marking}>刷新</button>
        <button className="account-secondary" type="button" onClick={() => void markRead()} disabled={!feed?.unread || marking || loading}>全部标为已读</button>
      </div>
    </div>
    {error && <p className="account-error" role="alert">{error}</p>}
    <div className="account-card notification-feed">
      {feed?.items.length ? <ul>{feed.items.map(item => <li key={item.id} className={item.read_at ? "" : "notification-unread"}>
        <div className="notification-copy"><span className="notification-kind">{kindLabels[item.kind]}{!item.read_at && " · 未读"}</span>
          <a href={destination(item)} onClick={() => { if (!item.read_at && !marking) void markRead(item); }}>{item.title}</a>
          <time dateTime={item.visible_at}>{dateFormat.format(new Date(item.visible_at))}</time></div>
        {!item.read_at && <button className="account-text-link" type="button" onClick={() => void markRead(item)} disabled={marking}>标为已读</button>}
      </li>)}</ul> : !loading && !error && <p className="notification-empty">目前没有通知。试卷生成、AI 评分完成和到点的日程提醒会显示在这里。</p>}
      {loading && <p className="account-hint" role="status">正在加载通知…</p>}
      {feed?.next_before && <button className="account-secondary notification-more" type="button" disabled={loading || marking}
        onClick={() => void load(feed.next_before ?? undefined)}>加载更多</button>}
    </div>
  </section>;
}
