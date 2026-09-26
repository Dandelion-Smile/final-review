import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type Course } from "./workbench-api";
import "./materials.css";

type SourceType = "past_exam" | "teacher_ppt" | "homework" | "other_practice" | "crash_course" | "ai_supplement";
type Material = { document_id: string; title: string; file_name?: string; source_type: SourceType; chapter?: string; parse_status: string; parse_error?: string };
type MaterialJob = { job_id: string; document_id: string; status: "queued" | "running" | "succeeded" | "failed"; stage?: string | null; error_message?: string | null; attempts: number };
const stageLabel: Record<string, string> = { parse: "解析", ocr: "文字识别", clean: "清洗", index: "建立索引" };

const sourceOptions: [SourceType, string][] = [
  ["past_exam", "历年真题"], ["teacher_ppt", "老师 PPT"], ["homework", "平时作业"],
  ["other_practice", "其他练习"], ["crash_course", "速成课"], ["ai_supplement", "AI 补充"],
];
const sourceLabel = Object.fromEntries(sourceOptions);

export default function Materials({ selectedCourse }: { selectedCourse: Course | null }) {
  const [courses, setCourses] = useState<Course[]>([]);
  const [courseId, setCourseId] = useState("");
  const [items, setItems] = useState<Material[]>([]);
  const [jobs, setJobs] = useState<MaterialJob[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const uploadKey = useRef<string>(crypto.randomUUID());
  const [title, setTitle] = useState("");
  const [chapter, setChapter] = useState("");
  const [sourceType, setSourceType] = useState<SourceType>("homework");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const hasActiveJobs = jobs.some(job => job.status === "queued" || job.status === "running");

  useEffect(() => {
    api<{ items: Course[] }>("/api/courses")
      .then(data => {
        const available = data.items.filter(item => item.status !== "deleted");
        setCourses(available);
        setCourseId(current => current || available.find(item => item.course_id === selectedCourse?.course_id)?.course_id || available[0]?.course_id || "");
      })
      .catch(error => setMessage(error instanceof Error ? error.message : "课程加载失败"));
  }, [selectedCourse?.course_id]);

  useEffect(() => {
    if (!courseId) { setItems([]); return; }
    let active = true;
    async function refresh() {
      try {
        const [documents, currentJobs] = await Promise.all([
          api<{ items: Material[] }>(`/api/courses/${encodeURIComponent(courseId)}/documents`),
          api<{ items: MaterialJob[] }>(`/api/courses/${encodeURIComponent(courseId)}/material-jobs`),
        ]);
        if (active) { setItems(documents.items); setJobs(currentJobs.items); }
      } catch (error) {
        if (active) setMessage(error instanceof Error ? error.message : "资料加载失败");
      }
    }
    void refresh();
    const timer = hasActiveJobs ? window.setInterval(() => { void refresh(); }, 2000) : undefined;
    return () => { active = false; window.clearInterval(timer); };
  }, [courseId, hasActiveJobs]);

  async function retry(jobId: string) {
    try {
      const response = await fetch(`/api/courses/${encodeURIComponent(courseId)}/material-jobs/${jobId}/retry`, { method: "POST", credentials: "same-origin" });
      if (!response.ok) throw new Error("重试失败，请刷新后再试");
      setMessage("已重新排队处理。");
      setJobs(current => current.map(job => job.job_id === jobId ? { ...job, status: "queued", stage: null, error_message: null } : job));
    } catch (error) { setMessage(error instanceof Error ? error.message : "重试失败"); }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!courseId || !file || busy) return;
    setBusy(true);
    setMessage("");
    const form = new FormData();
    form.append("course_id", courseId);
    form.append("title", title.trim() || file.name);
    form.append("chapter", chapter.trim());
    form.append("source_type", sourceType);
    form.append("file", file);
    try {
      const response = await fetch("/knowledge/upload", {
        method: "POST", credentials: "same-origin", body: form,
        headers: { "Idempotency-Key": uploadKey.current },
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "上传失败");
      setMessage("资料已接收，正在排队处理。");
      setFile(null);
      uploadKey.current = crypto.randomUUID();
      setTitle("");
      setChapter("");
      const input = document.getElementById("material-file") as HTMLInputElement | null;
      if (input) input.value = "";
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "上传失败");
    } finally {
      const [data, currentJobs] = await Promise.all([
        api<{ items: Material[] }>(`/api/courses/${encodeURIComponent(courseId)}/documents`).catch(() => null),
        api<{ items: MaterialJob[] }>(`/api/courses/${encodeURIComponent(courseId)}/material-jobs`).catch(() => null),
      ]);
      if (data) setItems(data.items);
      if (currentJobs) setJobs(currentJobs.items);
      setBusy(false);
    }
  }

  return <div className="materials-page">
    <header className="materials-heading"><h1>我的复习资料</h1><p>上传课程文件或学习通作业截图，将其中的文字整理为可检索资料。</p></header>
    <form className="materials-upload box" onSubmit={submit}>
      <h2>添加资料</h2>
      <div className="materials-fields">
        <label>课程<select value={courseId} onChange={event => setCourseId(event.target.value)} required><option value="">选择课程</option>{courses.map(item => <option key={item.course_id} value={item.course_id}>{item.name}</option>)}</select></label>
        <label>来源类型<select value={sourceType} onChange={event => setSourceType(event.target.value as SourceType)}>{sourceOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>标题<input value={title} onChange={event => setTitle(event.target.value)} placeholder="留空则使用文件名" maxLength={200}/></label>
        <label>章节<input value={chapter} onChange={event => setChapter(event.target.value)} placeholder="可选" maxLength={200}/></label>
        <label className="materials-file">文件<input id="material-file" type="file" accept=".md,.txt,.pdf,.ppt,.pptx,.doc,.docx,.png,.jpg,.jpeg,.webp" onChange={event => { setFile(event.target.files?.[0] ?? null); uploadKey.current = crypto.randomUUID(); }} required/><small>支持 MD、TXT、PDF、PPT、PPTX、DOC、DOCX、PNG、JPG、WebP；最大 10 MB。图片会先进行文字识别。</small></label>
      </div>
      <button className="primary" disabled={busy || !courseId || !file}>{busy ? "正在上传…" : "上传并处理"}</button>
      {message && <p className="materials-message" role="status">{message}</p>}
    </form>
    <section className="materials-list box"><h2>当前课程资料</h2>{items.length === 0 ? <p>暂无资料。选择文件开始上传。</p> : <ul>{items.map(item => {
      const job = jobs.find(candidate => candidate.document_id === item.document_id);
      const status = job?.status ?? item.parse_status;
      return <li key={item.document_id}><div><strong>{item.title}</strong><small>{item.file_name} · {sourceLabel[item.source_type] ?? item.source_type}{item.chapter ? ` · ${item.chapter}` : ""}</small></div><span className={`material-status ${status}`}>{status === "succeeded" || status === "ready" ? "可检索" : status === "failed" ? "处理失败" : status === "queued" ? "排队中" : `处理中${job?.stage ? ` · ${stageLabel[job.stage] ?? job.stage}` : ""}`}</span>{status === "failed" && <><p>{job?.error_message ?? item.parse_error}</p>{job && <button type="button" onClick={() => void retry(job.job_id)}>重试</button>}</>}</li>;
    })}</ul>}</section>
  </div>;
}
