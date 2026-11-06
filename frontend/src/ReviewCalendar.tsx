import { useEffect, useState } from "react";
import CalendarEntryForm from "./CalendarEntryForm";
import { entryKinds, entryTime, type CalendarEntry, type CalendarSummary } from "./review-api";
import { addDays, calendarQuery, dateKey, daysUntil, fullDate, localDate, shortDate } from "./review-preview";
import { api } from "./workbench-api";

const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const monthFormat = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long" });
const eventTones = { study: "blue", exam: "orange", class: "blue", other: "green", todo: "green" };

function startOfWeek(date: Date) {
  return addDays(date, -((date.getDay() + 6) % 7));
}

export default function ReviewCalendar({ initialDate }: { initialDate?: string }) {
  const [selected, setSelected] = useState(() => {
    const date = initialDate ? localDate(initialDate) : new Date();
    return Number.isNaN(date.getTime()) ? new Date() : date;
  });
  const [view, setView] = useState<"month" | "week">("month");
  const [formOpen, setFormOpen] = useState(false);
  const [summary, setSummary] = useState<{ range: string; data: CalendarSummary } | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const today = new Date();
  const todayKey = dateKey(today);
  const selectedKey = dateKey(selected);
  const month = selected.getMonth();
  const start = startOfWeek(view === "week" ? selected : new Date(selected.getFullYear(), month, 1));
  const dayCount = view === "week" ? 7 : 42;
  const dates = Array.from({ length: dayCount }, (_, index) => addDays(start, index));
  const startKey = dateKey(start);
  const endKey = dateKey(addDays(start, dayCount));
  const range = `${startKey}/${endKey}/${reload}`;
  const data = summary?.range === range ? summary.data : null;
  const entriesByDate = new Map<string, CalendarEntry[]>();
  for (const entry of data?.items ?? []) {
    if (!entry.scheduled_on) continue;
    const entries = entriesByDate.get(entry.scheduled_on) ?? [];
    entries.push(entry);
    entriesByDate.set(entry.scheduled_on, entries);
  }
  const selectedEvents = entriesByDate.get(selectedKey) ?? [];
  const exam = data?.next_exam;
  const period = view === "month"
    ? monthFormat.format(selected)
    : `${monthFormat.format(start)}${start.getDate()}日 – ${fullWeekEnd(start)}`;

  useEffect(() => {
    let active = true;
    setError("");
    const query = calendarQuery(new Date());
    query.set("start", startKey);
    query.set("end", endKey);
    api<CalendarSummary>(`/api/calendar/entries?${query}`)
      .then(data => { if (active) setSummary({ range, data }); })
      .catch(reason => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [startKey, endKey, range]);

  function changePeriod(delta: number) {
    setSelected(current => view === "week"
      ? addDays(current, delta * 7)
      : new Date(current.getFullYear(), current.getMonth() + delta, 1, 12));
  }

  return (
    <div className="review-preview">
      <header className="review-page-heading">
        <div><h1>复习日历</h1><p>合理安排每周的学习任务，保持稳定的复习节奏。</p></div>
        <button className="review-button review-button-primary" type="button" onClick={() => setFormOpen(true)}>＋ 添加安排</button>
      </header>
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error} <button type="button" className="review-button" onClick={() => setReload(value => value + 1)}>重试</button></p>}
      <div className="review-calendar-layout">
        <section className="review-panel" aria-label="日历">
          <div className="review-calendar-controls">
            <div className="review-period-controls">
              <button className="review-button" type="button" aria-label={view === "week" ? "上一周" : "上个月"} onClick={() => changePeriod(-1)}>‹</button>
              <h2 aria-live="polite">{period}</h2>
              <button className="review-button" type="button" aria-label={view === "week" ? "下一周" : "下个月"} onClick={() => changePeriod(1)}>›</button>
              <button className="review-button" type="button" onClick={() => setSelected(new Date())}>今天</button>
            </div>
            <div className="review-view-toggle" role="group" aria-label="日历视图">
              <button type="button" aria-pressed={view === "month"} onClick={() => setView("month")}>月</button>
              <button type="button" aria-pressed={view === "week"} onClick={() => setView("week")}>周</button>
            </div>
          </div>
          <div className={`review-date-grid${view === "week" ? " review-week" : ""}`}>
            {weekdays.map(day => <div className="review-weekday" key={day}>{day}</div>)}
            {dates.map(date => {
              const key = dateKey(date);
              const events = entriesByDate.get(key) ?? [];
              return (
                <button
                  type="button"
                  key={key}
                  className={`review-day${view === "month" && date.getMonth() !== month ? " review-other-month" : ""}`}
                  aria-pressed={key === selectedKey}
                  aria-current={key === todayKey ? "date" : undefined}
                  aria-label={`${fullDate.format(date)}，${events.length} 项安排`}
                  onClick={() => setSelected(date)}
                >
                  <span className="review-day-number">{date.getDate()}</span>
                  {events.slice(0, 2).map(event => (
                    <span className={`review-event-chip tone-${eventTones[event.kind]}`} key={event.entry_id} aria-hidden="true">
                      <span>{event.course_name ?? entryKinds[event.kind]}</span>
                      <strong>{event.title}</strong>
                    </span>
                  ))}
                  {events.length > 2 && <small aria-hidden="true">+{events.length - 2}</small>}
                </button>
              );
            })}
          </div>
        </section>
        <div className="review-stack">
          <section className="review-panel" aria-label="所选日期的安排">
            <div className="review-panel-heading"><h2>{shortDate.format(selected)}</h2></div>
            <p>{fullDate.format(selected)}{data && ` · ${selectedEvents.length} 项复习安排`}</p>
            {!data && !error && <p className="review-empty" role="status">正在加载日程…</p>}
            {data && (selectedEvents.length ? (
              <ul className="review-agenda">
                {selectedEvents.map(event => (
                  <li className={`tone-${eventTones[event.kind]}`} key={event.entry_id}>
                    <small>{entryTime(event)} · {entryKinds[event.kind]}{event.course_name && ` · ${event.course_name}`}</small>
                    <strong className={event.completed_at ? "review-completed" : undefined}>{event.title}</strong>
                    {event.description && <p>{event.description}</p>}
                  </li>
                ))}
              </ul>
            ) : <p className="review-empty">这一天还没有安排。</p>)}
          </section>
          <section className="review-panel">
            <h2>下一场考试</h2>
            {exam?.scheduled_on ? <div className="review-exam">
              <div><h3>{exam.title}</h3><p>{shortDate.format(localDate(exam.scheduled_on))} · {entryTime(exam)}</p></div>
              <div className="review-countdown"><strong>{Math.max(0, daysUntil(exam.scheduled_on, today))}</strong><small> 天</small></div>
            </div> : data && <p className="review-empty">近期没有考试安排。</p>}
          </section>
          <section className="review-panel">
            <h2>近期安排</h2>
            {data && (data.upcoming.length ? <ul className="review-upcoming">
              {data.upcoming.map(event => event.scheduled_on && (
                <li key={event.entry_id}>
                  <button type="button" onClick={() => setSelected(localDate(event.scheduled_on!))}>
                    <small>{shortDate.format(localDate(event.scheduled_on))}</small><span>{event.title}</span>
                  </button>
                </li>
              ))}
            </ul> : <p className="review-empty">近期没有安排。</p>)}
          </section>
        </div>
      </div>
      {formOpen && <CalendarEntryForm date={selectedKey} onClose={() => setFormOpen(false)} onSaved={entry => {
        setFormOpen(false);
        setNotice(entry.scheduled_on ? `已添加「${entry.title}」。` : `已添加「${entry.title}」，可在主页待完成列表中查看。`);
        if (entry.scheduled_on) setSelected(localDate(entry.scheduled_on));
        setReload(value => value + 1);
      }} />}
    </div>
  );
}

function fullWeekEnd(start: Date) {
  const end = addDays(start, 6);
  if (end.getFullYear() !== start.getFullYear()) return `${monthFormat.format(end)}${end.getDate()}日`;
  return shortDate.format(end);
}
