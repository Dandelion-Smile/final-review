import { useEffect, useState } from "react";
import NavigationIcon from "./NavigationIcon";
import { courseHref } from "./course-routes";
import { entryTime, type CalendarEntry, type HomeSummary } from "./review-api";
import { calendarQuery, daysUntil, localDate, shortDate } from "./review-preview";
import { api } from "./workbench-api";

const courseTones = ["blue", "green", "orange"];

export default function ReviewDashboard() {
  const [summary, setSummary] = useState<HomeSummary | null>(null);
  const [loadError, setLoadError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [retry, setRetry] = useState(0);
  const [saving, setSaving] = useState<Set<string>>(() => new Set());
  const now = new Date();
  const hour = now.getHours();
  const greeting = hour < 12 ? "上午好" : hour < 18 ? "下午好" : "晚上好";

  useEffect(() => {
    let active = true;
    setLoadError("");
    api<HomeSummary>(`/api/home?${calendarQuery(new Date())}`)
      .then(data => { if (active) setSummary(data); })
      .catch(error => { if (active) setLoadError(error.message); });
    return () => { active = false; };
  }, [retry]);

  async function completeTodo(entry: CalendarEntry, completed: boolean) {
    const id = entry.entry_id;
    setSaving(current => new Set(current).add(id));
    setSaveError("");
    try {
      const updated = await api<CalendarEntry>(`/api/calendar/entries/${encodeURIComponent(id)}/completion`, "PATCH", { completed });
      setSummary(current => current && {
        ...current,
        pending_todos: current.pending_todos.map(todo => todo.entry_id === id ? updated : todo),
      });
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "待办更新失败");
    } finally {
      setSaving(current => new Set([...current].filter(key => key !== id)));
    }
  }

  const note = summary?.recent_note;
  const test = summary?.recent_test;
  const exam = summary?.next_exam;

  return (
    <div className="review-preview">
      <div className="review-dashboard-layout">
        <div className="review-stack">
          <header className="review-panel review-welcome">
            <div>
              <h1>{greeting}，开始复习吧</h1>
              <p>从上次停下的地方继续复习，一点一点接近目标。</p>
            </div>
            <div className="review-welcome-art" aria-hidden="true"><NavigationIcon name="courses" /></div>
          </header>
          <section className="review-panel">
            <h2>继续学习</h2>
            {loadError && <p role="alert">{loadError} <button type="button" className="review-button" onClick={() => setRetry(value => value + 1)}>重试</button></p>}
            {!summary && !loadError && <p role="status">正在加载学习记录…</p>}
            {summary && <div className="review-activity-grid">
              <article className="review-activity">
                <div className="review-row">
                  <span className="review-tile"><NavigationIcon name="notes" /></span>
                  <div><span className="review-tag">最近笔记</span><h3>{note?.title ?? "还没有保存的笔记"}</h3></div>
                </div>
                {note && <>
                  <p>{note.course_name}</p>
                  <a className="review-button" href={courseHref(note.course_id, "notes", [note.asset_id, note.revision_id])}>继续阅读</a>
                </>}
              </article>
              <article className="review-activity">
                <div className="review-row">
                  <span className="review-tile"><NavigationIcon name="quiz" /></span>
                  <div><span className="review-tag">最近测试</span><h3>{test?.title ?? "还没有保存的测试"}</h3></div>
                </div>
                {test && <>
                  <p>{test.course_name}</p>
                  <a className="review-button" href={courseHref(test.course_id, "quiz", test.kind === "attempt"
                    ? ["attempts", test.attempt_id] : ["papers", test.asset_id, test.revision_id])}>
                    {test.kind === "paper" ? "查看试卷" : test.status === "in_progress" ? "继续作答" : "查看作答记录"}
                  </a>
                </>}
              </article>
            </div>}
          </section>
        </div>
        <div className="review-stack">
          <section className="review-panel">
            <div className="review-panel-heading">
              <h2>近期考试</h2>
              <a href="#/calendar">查看日历</a>
            </div>
            {exam?.scheduled_on ? <div className="review-exam">
              <span className="review-tile"><NavigationIcon name="calendar" /></span>
              <div><h3>{exam.title}</h3><p>{shortDate.format(localDate(exam.scheduled_on))} · {entryTime(exam)}</p></div>
              <div className="review-countdown"><strong>{Math.max(0, daysUntil(exam.scheduled_on, now))}</strong><small> 天</small></div>
            </div> : summary && <p>近期没有考试安排。</p>}
          </section>
          <section className="review-panel">
            <div className="review-panel-heading">
              <h2>待完成</h2>
              <a href="#/calendar">查看安排</a>
            </div>
            {saveError && <p role="alert">{saveError}</p>}
            {summary && (summary.pending_todos.length ? <ul className="review-tasks">
              {summary.pending_todos.map(todo => <li key={todo.entry_id}>
                <div><span className={todo.completed_at ? "review-completed" : undefined}>{todo.title}</span>
                  <small>{todo.scheduled_on ? shortDate.format(localDate(todo.scheduled_on)) : "无日期"}{todo.course_name && ` · ${todo.course_name}`}</small>
                </div>
                <input type="checkbox" aria-label={`${todo.completed_at ? "撤销完成" : "标记完成"} ${todo.title}`}
                  checked={Boolean(todo.completed_at)} disabled={saving.has(todo.entry_id)}
                  onChange={event => void completeTodo(todo, event.target.checked)} />
              </li>)}
            </ul> : <p>没有待完成事项。</p>)}
          </section>
        </div>
      </div>
      <section className="review-panel review-recent-courses">
        <div className="review-panel-heading">
          <h2>最近课程</h2>
          <a href="#/courses">查看全部课程</a>
        </div>
        {summary && (summary.recent_courses.length ? <div className="review-recent-grid">
          {summary.recent_courses.map((course, index) => (
            <a className="review-recent-card" href={courseHref(course.course_id)} key={course.course_id}>
              <span className={`review-book tone-${courseTones[index % courseTones.length]}`}><NavigationIcon name="courses" /></span>
              <div>
                <h3>{course.name}</h3>
                <small>{course.last_activity_at ? `最近学习 · ${shortDate.format(new Date(course.last_activity_at))}` : "进入课程 →"}</small>
              </div>
            </a>
          ))}
        </div> : <p>还没有进行中的课程。</p>)}
      </section>
    </div>
  );
}
