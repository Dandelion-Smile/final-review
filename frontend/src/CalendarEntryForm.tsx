import { useEffect, useRef, useState, type FormEvent } from "react";
import { entryKinds, type CalendarEntry, type EntryKind } from "./review-api";
import { api, type Course } from "./workbench-api";

type Props = { date: string; onClose: () => void; onSaved: (entry: CalendarEntry) => void };

export default function CalendarEntryForm({ date, onClose, onSaved }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [kind, setKind] = useState<EntryKind>("study");
  const [courseId, setCourseId] = useState("");
  const [allDay, setAllDay] = useState(true);
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [courseError, setCourseError] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    dialog.current?.showModal();
    let active = true;
    api<{ items: Course[] }>("/api/courses")
      .then(data => { if (active) setCourses(data.items.filter(course => course.status !== "deleted")); })
      .catch(() => { if (active) setCourseError("课程列表加载失败，请关闭表单后重试。"); });
    return () => { active = false; };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const values = new FormData(event.currentTarget);
    const title = String(values.get("title") ?? "").trim();
    const start = allDay ? null : String(values.get("start_time"));
    const end = allDay ? null : String(values.get("end_time"));
    if (!title) { setError("请输入安排标题。"); return; }
    if (start && end && end <= start) { setError("结束时间须晚于开始时间。"); return; }
    setError("");
    setSaving(true);
    try {
      const entry = await api<CalendarEntry>("/api/calendar/entries", "POST", {
        kind,
        title,
        description: String(values.get("description") ?? "").trim(),
        course_id: kind === "other" ? null : courseId || null,
        scheduled_on: values.get("scheduled_on") || null,
        all_day: allDay,
        start_time: start,
        end_time: end,
      });
      dialog.current?.close();
      onSaved(entry);
    } catch (error) {
      setError(error instanceof Error ? error.message : "日程保存失败");
      setSaving(false);
    }
  }

  return (
    <dialog className="review-event-dialog" ref={dialog} aria-labelledby="review-event-title"
      onCancel={event => {
        event.preventDefault();
        if (!saving) {
          dialog.current?.close();
          onClose();
        }
      }}>
      <form onSubmit={event => void submit(event)}>
        <h2 id="review-event-title">添加复习安排</h2>
        <label>类型<select value={kind} onChange={event => {
          const value = event.target.value as EntryKind;
          setKind(value);
          if (value === "other") setCourseId("");
        }}>
          {Object.entries(entryKinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        {kind !== "other" && <>
          <label>课程<select value={courseId} required={kind === "exam"}
            onChange={event => setCourseId(event.target.value)}>
            <option value="">{kind === "exam" ? "请选择课程" : "不关联课程"}</option>
            {courses?.map(course => <option key={course.course_id} value={course.course_id}>{course.name}</option>)}
          </select></label>
          {courseError && <p role="alert">{courseError}</p>}
          {kind === "exam" && courses?.length === 0 && <p>添加考试前，请先在「我的课程」中创建课程。</p>}
        </>}
        <label>安排标题<input name="title" type="text" required maxLength={80} placeholder="例如：复习第三章" /></label>
        <label>日期<input name="scheduled_on" type="date" defaultValue={date} required={kind !== "todo" || !allDay} /></label>
        {kind === "todo" && <p>日期可留空，无日期的待办显示在主页。</p>}
        <label className="review-all-day"><input type="checkbox" checked={allDay} onChange={event => setAllDay(event.target.checked)} />全天</label>
        {!allDay && <div className="review-time-fields">
          <label>开始时间<input name="start_time" type="time" required defaultValue="09:00" /></label>
          <label>结束时间<input name="end_time" type="time" required defaultValue="10:00" /></label>
        </div>}
        <label>备注<textarea name="description" rows={2} maxLength={1000} /></label>
        {error && <p role="alert">{error}</p>}
        <div className="review-dialog-actions">
          <button className="review-button" type="button" disabled={saving} onClick={() => { dialog.current?.close(); onClose(); }}>取消</button>
          <button className="review-button review-button-primary" type="submit" disabled={saving || (kind === "exam" && !courseId)}>
            {saving ? "正在保存…" : "保存安排"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
