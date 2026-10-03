import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type Course } from "./workbench-api";
import "./materials.css";

type SourceType = "past_exam" | "teacher_ppt" | "homework" | "other_practice" | "external_upload" | "crash_course" | "ai_supplement";
type Material = { document_id: string; title: string; file_name?: string; source_type: SourceType; chapter?: string; parse_status: string; parse_error?: string; updated_at?: string | null };
type MaterialJob = { job_id: string; document_id: string; status: "queued" | "running" | "succeeded" | "failed"; stage?: string | null; error_message?: string | null; attempts: number };
type DeletePreview = { blocking_references: number; affected_assets: number; confirmation_id: string; expires_at: string };
type SourceChunk = { chunk_id: string; locator_id: string; position_kind: string; position: number | null; text_start: number | null; text_end: number | null; excerpt: string; content?: string };
type SourcePreview = { document_id: string; material_version_id: string; file_name: string; source_type: SourceType; items: SourceChunk[] };
const stageLabel: Record<string, string> = { parse: "解析", convert: "转换 PDF", ocr: "文字识别", clean: "清洗", index: "建立索引" };

const sourceOptions: [SourceType, string][] = [
  ["past_exam", "历年真题"], ["teacher_ppt", "老师 PPT"], ["homework", "平时作业"],
  ["other_practice", "其他练习"], ["external_upload", "外部上传"], ["crash_course", "速成课"], ["ai_supplement", "AI 补充"],
];
const sourceLabel = Object.fromEntries(sourceOptions);
const documentPath = (courseId: string, documentId: string) => `/api/courses/${encodeURIComponent(courseId)}/documents/${encodeURIComponent(documentId)}`;

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
  const [filterChapter, setFilterChapter] = useState("");
  const [filterSource, setFilterSource] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [editing, setEditing] = useState<Material | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editChapter, setEditChapter] = useState("");
  const [editSource, setEditSource] = useState<SourceType>("homework");
  const [deleting, setDeleting] = useState<{ item: Material; preview: DeletePreview } | null>(null);
  const [sourcePreview, setSourcePreview] = useState<SourcePreview | null>(null);
  const [selectedChunk, setSelectedChunk] = useState<SourceChunk | null>(null);
  const [referencedChunkIds, setReferencedChunkIds] = useState<string[]>([]);
  const [previewError, setPreviewError] = useState("");
  const [retainSnapshot, setRetainSnapshot] = useState(false);
  const [dialogError, setDialogError] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const openedHash = useRef("");
  const [sourceHash, setSourceHash] = useState(window.location.hash);
  const hasActiveJobs = jobs.some(job => job.status === "queued" || job.status === "running");
  const chapters = [...new Set(items.map(item => item.chapter ?? ""))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const visible = items.filter(item => (!filterChapter || (filterChapter === "__empty__" ? !item.chapter : item.chapter === filterChapter))
    && (!filterSource || item.source_type === filterSource)
    && (!filterStatus || item.parse_status === filterStatus));
  const citedChunks = sourcePreview?.items.filter(chunk => referencedChunkIds.includes(chunk.chunk_id)) ?? [];
  const previewChunks = citedChunks.length ? citedChunks : sourcePreview?.items ?? [];

  async function refresh() {
    const [documents, currentJobs] = await Promise.all([
      api<{ items: Material[] }>(`/api/courses/${encodeURIComponent(courseId)}/documents`),
      api<{ items: MaterialJob[] }>(`/api/courses/${encodeURIComponent(courseId)}/material-jobs`),
    ]);
    setItems(documents.items); setJobs(currentJobs.items);
  }

  useEffect(() => {
    const onHashChange = () => setSourceHash(window.location.hash);
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    if (!sourceHash.startsWith("#materials/")) return;
    const linkedCourse = decodeURIComponent(sourceHash.split("/")[1] || "");
    if (linkedCourse && linkedCourse !== courseId) setCourseId(linkedCourse);
  }, [sourceHash, courseId]);

  useEffect(() => {
    api<{ items: Course[] }>("/api/courses")
      .then(data => {
        const available = data.items.filter(item => item.status !== "deleted");
        setCourses(available);
        const linkedCourse = window.location.hash.startsWith("#materials/") ? decodeURIComponent(window.location.hash.split("/")[1] || "") : "";
        setCourseId(available.find(item => item.course_id === linkedCourse)?.course_id
          ?? available.find(item => item.course_id === selectedCourse?.course_id)?.course_id ?? available[0]?.course_id ?? "");
      })
      .catch(error => setMessage(error instanceof Error ? error.message : "课程加载失败"));
  }, [selectedCourse?.course_id]);

  useEffect(() => {
    if (!courseId) { setItems([]); return; }
    let active = true;
    async function refresh() {
      const [documents, currentJobs] = await Promise.allSettled([
        api<{ items: Material[] }>(`/api/courses/${encodeURIComponent(courseId)}/documents`),
        api<{ items: MaterialJob[] }>(`/api/courses/${encodeURIComponent(courseId)}/material-jobs`),
      ]);
      if (!active) return;
      if (documents.status === "fulfilled") setItems(documents.value.items);
      if (currentJobs.status === "fulfilled") setJobs(currentJobs.value.items);
      if (documents.status === "rejected") setMessage(`资料列表加载失败：${documents.reason instanceof Error ? documents.reason.message : "请稍后重试"}`);
      else if (currentJobs.status === "rejected") setMessage(`处理状态加载失败：${currentJobs.reason instanceof Error ? currentJobs.reason.message : "请稍后重试"}`);
    }
    void refresh();
    const timer = hasActiveJobs ? window.setInterval(() => { void refresh(); }, 2000) : undefined;
    return () => { active = false; window.clearInterval(timer); };
  }, [courseId, hasActiveJobs]);

  useEffect(() => {
    const hash = sourceHash;
    if (!hash.startsWith("#materials/") || hash === openedHash.current) return;
    const [, encodedCourse, encodedDocument, encodedChunks] = hash.slice(1).split("/");
    const linkedCourse = decodeURIComponent(encodedCourse || "");
    const linkedDocument = decodeURIComponent(encodedDocument || "");
    const linkedChunkIds = [...new Set((encodedChunks || "").split(",").filter(Boolean).map(decodeURIComponent))];
    if (linkedCourse !== courseId || !linkedDocument || !linkedChunkIds.length) return;
    openedHash.current = hash;
    const base = documentPath(courseId, linkedDocument);
    void api<SourcePreview>(base + "/chunks").then(async preview => {
      const availableIds = linkedChunkIds.filter(id => preview.items.some(chunk => chunk.chunk_id === id));
      setSourcePreview(preview);
      setReferencedChunkIds(availableIds);
      if (availableIds.length < linkedChunkIds.length) setPreviewError("部分引用片段已失效，以下显示仍可查看的位置。");
      if (!availableIds.length) return;
      const chunk = await api<SourceChunk>(base + `/chunks/${encodeURIComponent(availableIds[0])}`);
      setSelectedChunk(chunk);
    }).catch(error => setMessage(`来源片段加载失败：${error instanceof Error ? error.message : "请稍后重试"}`));
  }, [courseId, sourceHash]);

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

  function openEdit(item: Material) {
    setEditing(item); setEditTitle(item.title); setEditChapter(item.chapter ?? "");
    setEditSource(item.source_type); setDialogError("");
  }

  async function saveEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing || busy) return;
    setBusy(true); setDialogError("");
    try {
      await api(documentPath(courseId, editing.document_id), "PATCH", {
        title: editTitle.trim(), chapter: editChapter.trim(), source_type: editSource,
        expected_updated_at: editing.updated_at ?? null,
      });
      setEditing(null); setMessage("资料信息已更新。"); await refresh();
    } catch (error) { setDialogError(error instanceof Error ? error.message : "保存失败"); }
    finally { setBusy(false); }
  }

  async function openDelete(item: Material) {
    setBusy(true); setDialogError("");
    try {
      const preview = await api<DeletePreview>(documentPath(courseId, item.document_id) + "/deletion-preview", "POST");
      setDeleting({ item, preview }); setRetainSnapshot(false);
    } catch (error) { setMessage(error instanceof Error ? error.message : "删除预览失败"); }
    finally { setBusy(false); }
  }

  async function openSource(item: Material) {
    setPreviewError(""); setSelectedChunk(null); setReferencedChunkIds([]);
    try {
      const preview = await api<SourcePreview>(documentPath(courseId, item.document_id) + "/chunks");
      setSourcePreview(preview);
    } catch (error) { setMessage(error instanceof Error ? error.message : "预览失败"); }
  }

  async function selectChunk(chunk: SourceChunk) {
    if (!sourcePreview) return;
    setPreviewError("");
    try {
      const detail = await api<SourceChunk>(documentPath(courseId, sourcePreview.document_id) + `/chunks/${encodeURIComponent(chunk.chunk_id)}`);
      setSelectedChunk(detail);
      const ids = referencedChunkIds.length ? referencedChunkIds : [chunk.chunk_id];
      const hash = `#materials/${encodeURIComponent(courseId)}/${encodeURIComponent(sourcePreview.document_id)}/${ids.map(encodeURIComponent).join(",")}`;
      openedHash.current = hash;
      window.location.hash = hash;
    } catch (error) { setPreviewError(error instanceof Error ? error.message : "片段加载失败"); }
  }

  function locationLabel(chunk: SourceChunk) {
    if (chunk.position_kind === "page" && chunk.position) return `第 ${chunk.position} 页`;
    if (chunk.position_kind === "slide" && chunk.position) return `第 ${chunk.position} 张幻灯片`;
    return "文档片段";
  }

  async function confirmDelete() {
    if (!deleting || busy || (deleting.preview.blocking_references > 0 && !retainSnapshot)) return;
    setBusy(true); setDialogError("");
    try {
      await api(documentPath(courseId, deleting.item.document_id) + "/delete", "POST", {
        confirmation_id: deleting.preview.confirmation_id,
        mode: retainSnapshot ? "retain_source_snapshot" : "block",
      });
      setDeleting(null);
      setMessage(retainSnapshot ? "资料已删除，正式内容的来源快照已保留。" : "资料已删除。");
      await refresh();
    } catch (error) { setDialogError(error instanceof Error ? error.message : "删除失败，请重新预览"); }
    finally { setBusy(false); }
  }

  return <div className="materials-page">
    <header className="materials-heading"><h1>我的复习资料</h1><p>上传课程文件或学习通作业截图，将其中的文字整理为可检索资料。</p></header>
    <form className="materials-upload box" onSubmit={submit}>
      <h2>添加资料</h2>
      <div className="materials-fields">
        <label>课程<select value={courseId} onChange={event => { setCourseId(event.target.value); setFilterChapter(""); setFilterSource(""); setFilterStatus(""); }} required><option value="">选择课程</option>{courses.map(item => <option key={item.course_id} value={item.course_id}>{item.name}</option>)}</select></label>
        <label>来源类型<select value={sourceType} onChange={event => setSourceType(event.target.value as SourceType)}>{sourceOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>标题<input value={title} onChange={event => setTitle(event.target.value)} placeholder="留空则使用文件名" maxLength={200}/></label>
        <label>章节<input value={chapter} onChange={event => setChapter(event.target.value)} placeholder="可选" maxLength={200}/></label>
        <label className="materials-file">文件<input id="material-file" type="file" accept=".md,.txt,.pdf,.ppt,.pptx,.doc,.docx,.png,.jpg,.jpeg,.webp" onChange={event => { setFile(event.target.files?.[0] ?? null); uploadKey.current = crypto.randomUUID(); }} required/><small>支持 MD、TXT、PDF、PPT、PPTX、DOC、DOCX、PNG、JPG、WebP；最大 10 MB。图片会先进行文字识别。</small></label>
      </div>
      <button className="primary" disabled={busy || !courseId || !file}>{busy ? "正在上传…" : "上传并处理"}</button>
    </form>
    {message && <p className="materials-message" role="status">{message}</p>}
    <section className="materials-list box"><h2>当前课程资料</h2><p className="materials-count">共 {items.length} 份，当前显示 {visible.length} 份</p>
      <div className="materials-filters" aria-label="筛选资料">
        <label>筛选章节<select value={filterChapter} onChange={event => setFilterChapter(event.target.value)}><option value="">全部章节</option>{chapters.map(value => <option key={value || "__empty__"} value={value || "__empty__"}>{value || "未归类"}</option>)}</select></label>
        <label>筛选来源<select value={filterSource} onChange={event => setFilterSource(event.target.value)}><option value="">全部来源</option>{sourceOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>筛选状态<select value={filterStatus} onChange={event => setFilterStatus(event.target.value)}><option value="">全部状态</option><option value="queued">排队中</option><option value="running">处理中</option><option value="ready">可检索</option><option value="failed">处理失败</option></select></label>
      </div>
      {items.length === 0 ? <p className="materials-empty">暂无资料。选择文件开始上传。</p> : visible.length === 0 ? <p className="materials-empty">没有符合筛选条件的资料。</p> : <ul>{visible.map(item => {
      const job = jobs.find(candidate => candidate.document_id === item.document_id);
      const status = item.parse_status;
      const editable = status === "ready" || status === "failed";
      return <li key={item.document_id}><div className="material-detail"><strong>{item.title}</strong><small>{item.file_name} · {sourceLabel[item.source_type] ?? item.source_type} · {item.chapter || "未归类"}</small>{status === "failed" && <p className="material-error">{job?.error_message ?? item.parse_error}</p>}</div><span className={`material-status ${status}`}>{status === "ready" ? "可检索" : status === "failed" ? "处理失败" : status === "queued" ? "排队中" : `处理中${job?.stage ? ` · ${stageLabel[job.stage] ?? job.stage}` : ""}`}</span><div className="material-actions">{status === "ready" && <button type="button" onClick={() => void openSource(item)} disabled={busy}>预览</button>}<button type="button" onClick={() => openEdit(item)} disabled={!editable || busy} title={!editable ? "处理完成后可编辑" : undefined}>编辑</button>{status === "failed" && job && <button type="button" onClick={() => void retry(job.job_id)} disabled={busy}>重试</button>}<button type="button" className="material-delete" onClick={() => void openDelete(item)} disabled={busy}>删除</button></div></li>;
    })}</ul>}</section>
    {sourcePreview && <div className="wb-overlay" role="presentation"><section className="wb-dialog material-dialog material-preview" role="dialog" aria-modal="true" aria-labelledby="material-preview-title">
      <h2 id="material-preview-title">资料片段预览</h2>
      <p>{sourcePreview.file_name} · {sourceLabel[sourcePreview.source_type] ?? sourcePreview.source_type}</p>
      {referencedChunkIds.length > 0 && <p className="material-citation-count">本条笔记引用 {citedChunks.length} 处位置，选择下方位置查看对应内容。</p>}
      <div className="material-preview-layout">
        <div className="material-preview-list" aria-label={referencedChunkIds.length ? "笔记引用位置" : "资料片段"}>
          {previewChunks.length === 0 ? <p>暂无可用片段</p> : previewChunks.map((chunk, index) => <button type="button" key={chunk.chunk_id} className={selectedChunk?.chunk_id === chunk.chunk_id ? "selected" : ""} aria-current={selectedChunk?.chunk_id === chunk.chunk_id ? "location" : undefined} onClick={() => void selectChunk(chunk)}><strong>{referencedChunkIds.length ? `引用 ${index + 1} · ` : `${index + 1}. `}{locationLabel(chunk)}</strong><span>{chunk.excerpt}</span></button>)}
        </div>
        <article className="material-preview-content">{selectedChunk ? <><strong>{locationLabel(selectedChunk)}</strong><pre>{selectedChunk.content}</pre></> : <p>选择左侧片段查看完整内容。</p>}</article>
      </div>
      {previewError && <p className="material-dialog-error" role="alert">{previewError}</p>}
      <div className="wb-dialog-actions"><a href={documentPath(courseId, sourcePreview.document_id) + "/download"}>下载原文件</a><button type="button" onClick={() => { setSourcePreview(null); setSelectedChunk(null); setReferencedChunkIds([]); setPreviewError(""); openedHash.current = ""; setSourceHash(""); if (window.location.hash.startsWith("#materials/")) history.replaceState(null, "", window.location.pathname + window.location.search); }}>关闭</button></div>
    </section></div>}
    {editing && <div className="wb-overlay" role="presentation"><form className="wb-dialog material-dialog" role="dialog" aria-modal="true" aria-labelledby="material-edit-title" onSubmit={saveEdit}><h2 id="material-edit-title">编辑资料信息</h2><p>文件内容保持不变，章节和来源会同步用于资料检索。</p><label>标题<input value={editTitle} onChange={event => setEditTitle(event.target.value)} maxLength={200} required/></label><label>章节<input value={editChapter} onChange={event => setEditChapter(event.target.value)} maxLength={200} placeholder="可选"/></label><label>来源类型<select value={editSource} onChange={event => setEditSource(event.target.value as SourceType)}>{sourceOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>{dialogError && <p className="material-dialog-error" role="alert">{dialogError}</p>}<div className="wb-dialog-actions"><button type="button" onClick={() => setEditing(null)} disabled={busy}>取消</button><button className="wb-primary" disabled={busy || !editTitle.trim()}>{busy ? "正在保存…" : "保存资料信息"}</button></div></form></div>}
    {deleting && <div className="wb-overlay" role="presentation"><section className="wb-dialog material-dialog" role="dialog" aria-modal="true" aria-labelledby="material-delete-title"><span className="wb-eyebrow">删除影响</span><h2 id="material-delete-title">删除“{deleting.item.title}”？</h2><p>删除后，原文件和检索内容将不可再使用。</p><div className="material-impact"><span>正式资产 <b>{deleting.preview.affected_assets}</b></span><span>正式版本引用 <b>{deleting.preview.blocking_references}</b></span></div>{deleting.preview.blocking_references > 0 ? <div className="material-snapshot-choice"><p>这份资料仍被正式内容引用。默认禁止删除；若继续，系统会保留文件名、来源类型、当前可用的文档级定位和必要文本摘录，供这些内容说明来源。原文件与检索内容仍会删除。</p><label><input type="checkbox" checked={retainSnapshot} onChange={event => setRetainSnapshot(event.target.checked)}/> 我了解影响，保留来源快照后删除</label></div> : <p>目前没有正式内容引用这份资料。确认后将删除原文件和检索内容。</p>}<p className="wb-expiry">本次确认有效至 {new Date(deleting.preview.expires_at).toLocaleString("zh-CN")}</p>{dialogError && <p className="material-dialog-error" role="alert">{dialogError} <button type="button" onClick={() => { const item = deleting.item; setDeleting(null); void openDelete(item); }}>重新预览</button></p>}<div className="wb-dialog-actions"><button type="button" onClick={() => setDeleting(null)} disabled={busy}>取消</button><button type="button" className="wb-danger" onClick={() => void confirmDelete()} disabled={busy || (deleting.preview.blocking_references > 0 && !retainSnapshot)}>{busy ? "正在删除…" : "确认删除资料"}</button></div></section></div>}
  </div>;
}
