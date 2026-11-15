import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import type { JSONContent } from "@tiptap/core";
import { api, ApiError, checkSession, downloadFile } from "./workbench-api";
import { courseHref } from "./course-routes";
import { bodyKey, notePath, plainNode, wireBody, type ImageManifest, type MaterialNote, type NoteRevision, type NoteStatus } from "./material-note-api";
import { MaterialNoteContext, materialNoteExtensions, NoteDialog } from "./material-note-editor";
import "./material-note-reader.css";

export default function MaterialNoteReader({ courseId, documentId }: { courseId: string; documentId: string }) {
  const [note, setNote] = useState<MaterialNote | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const base = notePath(courseId, documentId);
  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    async function load() {
      try {
        const value = await api<MaterialNote>(base + "/reading-note");
        if (active) { setNote(value); setError(""); }
      } catch (failure) {
        if (!active) return;
        if (failure instanceof ApiError && failure.status === 409) {
          try {
            const status = await api<NoteStatus>(base + "/reading-note/status");
            if (!active) return;
            if (status.note_status === "failed") { setError(status.parse_error || "资料提取失败，请返回资料页重试处理。"); return; }
          } catch (statusError) { if (active) setError(statusError instanceof Error ? statusError.message : "读取处理状态失败"); return; }
          timer = window.setTimeout(() => { void load(); }, 1500);
        } else setError(failure instanceof Error ? failure.message : "资料笔记加载失败");
      }
    }
    void load();
    return () => { active = false; window.clearTimeout(timer); };
  }, [base, attempt]);
  if (note) return <NoteWorkspace key={documentId} initial={note} base={base} courseId={courseId} />;
  return <section className="material-note-loading"><a href={courseHref(courseId, "materials")}>← 返回资料</a>
    <h1>{error ? "暂时无法打开资料笔记" : "正在整理这份课件"}</h1>
    <p role={error ? "alert" : "status"}>{error || "先提取正文和教学备注；原页图片会在后台补齐。"}</p>
    {error && <button type="button" onClick={() => { setError(""); setAttempt(value => value + 1); }}>重试加载</button>}
  </section>;
}

function NoteWorkspace({ initial, base, courseId }: { initial: MaterialNote; base: string; courseId: string }) {
  const [revision, setRevision] = useState(initial.revision);
  const revisionRef = useRef(initial.revision);
  const [status, setStatus] = useState(initial.status);
  const [manifest, setManifest] = useState<ImageManifest>({ status: initial.status.images_status ?? "queued", items: [] });
  const [mode, setMode] = useState<"read" | "edit">("read");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [original, setOriginal] = useState<NoteRevision | null>(null);
  const [conflict, setConflict] = useState<NoteRevision | null>(null);
  const [tick, setTick] = useState(0);
  const [retrying, setRetrying] = useState(false);
  const initialKey = useRef(bodyKey(initial.revision.body));
  const lastSavedKey = useRef(initialKey.current);
  const applying = useRef(false);
  const savingRef = useRef(false);
  const saveRequest = useRef<{ fingerprint: string; key: string } | null>(null);
  const documentRef = useRef<HTMLDivElement>(null);
  const editor = useEditor({
    extensions: materialNoteExtensions, content: initial.revision.body, editable: false,
    immediatelyRender: false, shouldRerenderOnTransaction: true,
    editorProps: { attributes: { class: "material-note-prose", "aria-label": "资料笔记正文", role: "textbox", "aria-multiline": "true" } },
    onUpdate: ({ editor }) => {
      if (applying.current) return;
      setDirty(bodyKey(editor.getJSON()) !== lastSavedKey.current);
      setTick(value => value + 1);
      setMessage("");
    },
  }, []);

  useEffect(() => { editor?.setEditable(mode === "edit" && initial.editable && !saving, false); }, [editor, mode, saving, initial.editable]);
  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    async function poll() {
      const [nextStatus, images] = await Promise.allSettled([
        api<NoteStatus>(base + "/reading-note/status"), api<ImageManifest>(base + "/page-images"),
      ]);
      if (!active) return;
      if (nextStatus.status === "fulfilled") {
        const incoming = nextStatus.value;
        const number = incoming.revision_numbers?.[incoming.latest_saved_revision_id ?? ""] ?? 0;
        if (number >= revisionRef.current.revision_no) setStatus(incoming);
      }
      if (images.status === "fulfilled") setManifest(images.value);
      setPollError(nextStatus.status === "rejected" || images.status === "rejected" ? "状态暂时无法刷新，你的正文编辑仍保留。" : "");
      timer = window.setTimeout(() => { void poll(); }, 3000);
    }
    void poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, [base]);
  useEffect(() => {
    function beforeUnload(event: BeforeUnloadEvent) { if (dirty || saving) { event.preventDefault(); event.returnValue = ""; } }
    function beforeNavigate(event: Event) {
      if (saving) { event.preventDefault(); setMessage("正在保存，请稍候再离开。"); return; }
      if (dirty && !window.confirm("资料笔记有未保存的修改。确定放弃修改并离开吗？")) event.preventDefault();
    }
    window.addEventListener("beforeunload", beforeUnload);
    window.addEventListener("note-navigation", beforeNavigate);
    return () => { window.removeEventListener("beforeunload", beforeUnload); window.removeEventListener("note-navigation", beforeNavigate); };
  }, [dirty, saving]);

  const save = useCallback(async () => {
    if (!editor || savingRef.current || !dirty || !initial.editable) return;
    const body = wireBody(editor.getJSON());
    const fingerprint = revisionRef.current.revision_id + ":" + bodyKey(body);
    if (saveRequest.current?.fingerprint !== fingerprint) saveRequest.current = { fingerprint, key: crypto.randomUUID() };
    savingRef.current = true;
    editor.setEditable(false, false);
    setSaving(true); setError(""); setMessage("");
    try {
      const response = await fetch(base + "/reading-note", {
        method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_revision_id: revisionRef.current.revision_id, idempotency_key: saveRequest.current.key, body }),
      });
      checkSession(response, base + "/reading-note");
      const data = await response.json();
      if (response.status === 409 && data.latest_revision_id) {
        setConflict(await api<NoteRevision>(base + "/reading-note/revisions/" + encodeURIComponent(data.latest_revision_id)));
        setError("另一页面已保存新版本。当前编辑完整保留，请先对比最新版本。"); return;
      }
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "保存失败，请保留当前内容后重试。");
      const saved = data.revision as NoteRevision;
      if (!saved?.body) throw new Error("保存结果缺少版本信息，请重试确认保存。");
      // Update provenance only. No text replacement, image response, cursor reset or history reset.
      const attrs = new Map(saved.body.content.map(node => [node.attrs?.block_id, node.attrs]));
      const transaction = editor.state.tr;
      editor.state.doc.forEach((node, position) => {
        const canonical = attrs.get(node.attrs.block_id);
        if (canonical) transaction.setNodeMarkup(position, undefined, canonical);
      });
      applying.current = true;
      transaction.setMeta("addToHistory", false);
      editor.view.dispatch(transaction);
      applying.current = false;
      revisionRef.current = saved; setRevision(saved);
      lastSavedKey.current = bodyKey(saved.body);
      setDirty(bodyKey(editor.getJSON()) !== lastSavedKey.current);
      setStatus(current => ({ ...current, latest_saved_revision_id: data.latest_saved_revision_id,
        indexed_revision_id: data.indexed_revision_id,
        revision_numbers: { ...current.revision_numbers, [saved.revision_id]: saved.revision_no } }));
      saveRequest.current = null;
      setMessage(data.sync_status === "pending_integration" ? "资料笔记已保存；检索和知识库仍使用上传时文字。" : "资料笔记已保存；文字内容未变，无需重新构建。 ");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "保存失败，请重试。"); }
    finally { applying.current = false; savingRef.current = false; setSaving(false); }
  }, [editor, dirty, initial.editable, base]);
  useEffect(() => {
    function onKey(event: KeyboardEvent) { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); void save(); } }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  async function retryImages() {
    if (retrying) return;
    setRetrying(true); setError("");
    try {
      await api(base + "/page-images/retry", "POST");
      setManifest(await api<ImageManifest>(base + "/page-images"));
      setMessage("已重试原页图片，正文保持不变。");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "图片重试失败"); }
    finally { setRetrying(false); }
  }
  async function showOriginal() {
    try { setOriginal(await api<NoteRevision>(base + "/reading-note/revisions/" + encodeURIComponent(initial.initial_revision_id))); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "原文读取失败"); }
  }
  function loadLatest() {
    if (!editor || !conflict) return;
    applying.current = true;
    editor.commands.setContent(conflict.body, { emitUpdate: false });
    applying.current = false;
    revisionRef.current = conflict; setRevision(conflict);
    lastSavedKey.current = bodyKey(conflict.body); saveRequest.current = null;
    setDirty(false); setConflict(null); setError(""); setMessage("已载入最新保存版本。"); setTick(value => value + 1);
  }
  function addSupplement() {
    if (!editor || saving) return;
    editor.chain().focus().insertContentAt(editor.state.doc.content.size, {
      type: "sourceBlock", attrs: { block_id: "user-" + crypto.randomUUID(), page: null,
        content_origin: "user_added", searchable: true, exclusion_reasons: [] }, content: [{ type: "paragraph" }],
    }).focus("end").run();
  }
  function scrollPage(number: number | null) {
    const target = documentRef.current?.querySelector<HTMLElement>(`[data-page="${number ?? "supplement"}"]`);
    target?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "center" });
  }

  const images = useMemo(() => Object.fromEntries(manifest.items.map(image => [image.page_number, image])), [manifest]);
  const originals = useMemo(() => Object.fromEntries(revision.body.content.filter(node => node.type !== "pageImage")
    .map(node => [node.attrs?.block_id, plainNode(node)])), [revision]);
  const context = { images, originals, editable: mode === "edit" && initial.editable && !saving, retryImages: () => { void retryImages(); } };
  const remoteChanged = status.latest_saved_revision_id && status.latest_saved_revision_id !== revision.revision_id;
  const versionLabel = (id?: string | null) => id ? `V${status.revision_numbers?.[id] ?? (id === revision.revision_id ? revision.revision_no : "?")}` : "尚未完成";
  const hasSupplements = !!editor?.getJSON().content?.some(node => node.attrs?.page === null);
  const empty = !editor?.getJSON().content?.length;
  void tick;
  return <section className="material-note-reader" aria-label="资料笔记">
    <header className="material-note-header"><div><a className="material-note-back" href={courseHref(courseId, "materials")}>← 返回资料</a>
      <p className="material-note-eyebrow">资料笔记 · {initial.pages.length} 页课件</p><h1>{initial.document.title}</h1>
      <p className="material-note-file">{initial.document.file_name}</p></div>
      <div className="material-note-actions"><button type="button" onClick={() => { void showOriginal(); }}>查看原文</button>
        <button type="button" onClick={() => { void downloadFile(base + "/download").catch(failure => setError(failure instanceof Error ? failure.message : "下载失败")); }}>下载原文件</button>
        {initial.editable && <button type="button" disabled={saving} onClick={() => setMode(mode === "read" ? "edit" : "read")}>{mode === "read" ? "编辑笔记" : "阅读模式"}</button>}
        <button type="button" className="material-note-save" disabled={!dirty || saving || !initial.editable} onClick={() => { void save(); }}>{saving ? "正在保存…" : "保存修改"}</button>
      </div>
    </header>
    <div className="material-note-statusline" aria-live="polite"><strong>{saving ? "正在保存" : dirty ? "有未保存修改" : `已保存 V${revision.revision_no}`}</strong>
      <span>检索：{versionLabel(status.indexed_revision_id)}{status.index_status === "failed" ? " · 更新失败" : status.index_status !== "ready" ? " · 更新中" : ""}</span>
      <span>知识库：{versionLabel(status.knowledge_revision_id)}{status.knowledge_status === "no_reliable_text" ? " · 暂无可靠文字" : status.knowledge_status === "failed" ? " · 构建失败" : status.knowledge_status !== "succeeded" ? " · 处理中" : ""}</span>
      <span>原页图片：{manifest.status === "ready" ? "已就绪" : manifest.status === "not_required" ? "无需配图" : manifest.status === "partial" || manifest.status === "failed" ? "部分未完成" : "后台生成中"}</span>
    </div>
    <p className="material-note-sync-notice">修改只在点击“保存修改”后保存。检索和知识库使用上传时文字，编辑后的自动同步将在后续接入。</p>
    {remoteChanged && <div className="material-note-message" role="status">另一页面已有新保存版本，当前正文保持不变。
      <button type="button" onClick={() => { void api<NoteRevision>(base + "/reading-note/revisions/" + encodeURIComponent(status.latest_saved_revision_id!)).then(setConflict).catch(failure => setError(String(failure))); }}>对比最新版本</button></div>}
    {(message || pollError) && <p className="material-note-message" role="status">{message || pollError}</p>}
    {error && <p className="material-note-error" role="alert">{error}</p>}
    <div className="material-note-layout">
      <aside className="material-note-outline" aria-label="资料目录"><h2>目录</h2><p>按原课件页码定位</p>
        <nav aria-label="课件页码目录">{initial.pages.map(page => <button key={page.page_number} type="button" onClick={() => scrollPage(page.page_number)}>
          <span>{String(page.page_number).padStart(2, "0")}</span><strong>{page.title || `第 ${page.page_number} 页`}</strong></button>)}
          {hasSupplements && <button type="button" onClick={() => scrollPage(null)}><span>＋</span><strong>补充说明</strong></button>}
        </nav>
      </aside>
      <div className="material-note-document" ref={documentRef}>
        {mode === "edit" && <div className="material-note-toolbar" role="toolbar" aria-label="正文格式">
          <button type="button" disabled={saving} aria-pressed={!!editor?.isActive("bold")} onClick={() => editor?.chain().focus().toggleBold().run()}>加粗</button>
          <button type="button" disabled={saving} onClick={() => editor?.chain().focus().setParagraph().run()}>正文</button>
          <button type="button" disabled={saving} onClick={() => editor?.chain().focus().toggleHeading({ level: 2 }).run()}>标题</button>
          <button type="button" disabled={saving} onClick={() => editor?.chain().focus().toggleBulletList().run()}>无序列表</button>
          <button type="button" disabled={saving} onClick={() => editor?.chain().focus().toggleOrderedList().run()}>有序列表</button>
          <button type="button" disabled={saving} onClick={() => editor?.chain().focus().toggleCodeBlock().run()}>代码</button>
          <button type="button" disabled={saving || !editor?.can().undo()} onClick={() => editor?.chain().focus().undo().run()}>撤销</button>
          <button type="button" disabled={saving || !editor?.can().redo()} onClick={() => editor?.chain().focus().redo().run()}>重做</button>
          <button type="button" disabled={saving} className="material-note-add" onClick={addSupplement}>＋ 补充说明</button>
        </div>}
        {empty && <div className="material-note-empty"><h2>这份笔记还没有正文</h2><p>可以添加补充说明，原始课件仍可通过“查看原文”阅读。</p>
          {mode === "edit" && <button type="button" onClick={addSupplement}>添加补充说明</button>}</div>}
        <MaterialNoteContext.Provider value={context}><EditorContent editor={editor} /></MaterialNoteContext.Provider>
        <footer className="material-note-end">按原课件整理 · 原页图片只供对照，未做图片文字识别</footer>
      </div>
    </div>
    {original && <NoteDialog title="课件原文对照" onClose={() => setOriginal(null)}>
      <p>以下为上传时提取的原文；你保存的编辑不会覆盖这份记录。</p><div className="material-note-original">{original.body.content.map((node, index) => <OriginalBlock key={index} node={node} />)}</div>
    </NoteDialog>}
    {conflict && <NoteDialog title="对比最新保存版本" onClose={() => setConflict(null)}>
      <p>当前编辑完整保留。可返回继续编辑，或明确放弃本地修改并载入 V{conflict.revision_no}。</p>
      <div className="material-note-compare"><section><h3>我的当前内容</h3><pre>{plainNode(editor?.getJSON() ?? {})}</pre></section>
        <section><h3>最新保存 V{conflict.revision_no}</h3><pre>{plainNode(conflict.body)}</pre></section></div>
      <div className="material-note-compare-actions"><button type="button" onClick={() => setConflict(null)}>保留我的编辑，返回</button><button type="button" onClick={loadLatest}>放弃本地修改，载入最新</button></div>
    </NoteDialog>}
  </section>;
}

function OriginalBlock({ node }: { node: JSONContent }) {
  if (node.type === "pageImage") return <p className="material-note-original-image">第 {node.attrs?.page} 页 · 原页图片见正文对应位置</p>;
  return <section><small>{node.attrs?.page ? `第 ${node.attrs.page} 页` : "补充说明"} · {node.type === "teachingNote" ? "教学备注" : node.type === "nativeFragment" ? "原生片段（待核对）" : "课件原文"}</small><pre>{plainNode(node)}</pre></section>;
}
