import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "./workbench-api";
import NoteConfigDialog from "./NoteConfigDialog";
import NoteMaterialPicker, { materialLabel, type NoteMaterial } from "./NoteMaterialPicker";

type ChatModel = { id: string; label: string };
type DraftCard = { asset_id: string; revision_id: string; title: string; note_type: string; url: string };
type AgentResult = { status: string; answer: string; prompt?: { message: string; required: string[] }; draft?: DraftCard };
type NotePreviewData = { asset: { title: string; course_id: string }; revision: { markdown: string; points: { point_id: string; heading: string; content: string; provenance: string; references: { document_id: string; chunk_id: string; file_name: string; source_type: string }[] }[] }; references: { point_id: string; locator_id: string }[] };
type Message = { from: "agent" | "user"; text: string; model?: string; draft?: DraftCard };
type ActiveNote = { session_id: string; status: "needs_input" | "running" | "failed"; prompt?: AgentResult["prompt"]; note_input?: { note_type?: string; scope?: string; duration_minutes?: number; source_document_ids?: string[] } };

async function noteRequest(path: string, body?: unknown): Promise<AgentResult> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 180_000);
  try {
    const response = await fetch(path, {
      method: "POST", credentials: "same-origin", signal: controller.signal,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "笔记请求失败");
    return data as AgentResult;
  } catch (error) {
    if (controller.signal.aborted) throw new Error("等待超过 3 分钟。任务可能仍在后台运行，请点击“恢复笔记任务”查看结果。");
    throw error;
  } finally { window.clearTimeout(timeout); }
}

export default function ChatHome({ courseId, selectedConversationId, titleRefreshKey, onConversationCreated, onConversationRenamed, onNewConversation }: { courseId: string | null; selectedConversationId: string | null; titleRefreshKey: number; onConversationCreated: (id: string) => void; onConversationRenamed: () => void; onNewConversation: () => void }) {
  const [models, setModels] = useState<ChatModel[]>([]);
  const [modelId, setModelId] = useState("");
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [modelError, setModelError] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyFailed, setHistoryFailed] = useState(false);
  const [conversationTitle, setConversationTitle] = useState("AI 对话");
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [savingTitle, setSavingTitle] = useState(false);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [noteSession, setNoteSession] = useState<string | null>(null);
  const [notePrompt, setNotePrompt] = useState<AgentResult["prompt"]>();
  const [noteRecovery, setNoteRecovery] = useState<"running" | "failed" | null>(null);
  const [noteType, setNoteType] = useState("key_points");
  const [noteScope, setNoteScope] = useState("");
  const [noteDuration, setNoteDuration] = useState("10");
  const [noteMaterials, setNoteMaterials] = useState<NoteMaterial[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [noteCancelling, setNoteCancelling] = useState(false);
  const [preview, setPreview] = useState<NotePreviewData | null>(null);
  const messagesRef = useRef<HTMLDivElement>(null);
  const modelPickerRef = useRef<HTMLDivElement>(null);
  const modelTriggerRef = useRef<HTMLButtonElement>(null);
  const conversationId = useRef(`chat-${crypto.randomUUID()}`);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  function restoreNoteInput(activeNote: ActiveNote | null | undefined, currentCourse: string, active: () => boolean) {
    const input = activeNote?.note_input;
    if (!input) return;
    if (input.note_type) setNoteType(input.note_type);
    if (input.scope !== undefined) setNoteScope(input.scope);
    if (input.duration_minutes) setNoteDuration(String(input.duration_minutes));
    if (input.source_document_ids?.length) {
      void api<{ items: NoteMaterial[] }>(`/api/courses/${encodeURIComponent(currentCourse)}/documents`)
        .then(data => { if (active()) setNoteMaterials(data.items.filter(item => input.source_document_ids?.includes(item.document_id) && item.parse_status === "ready")); })
        .catch(() => {});
    }
  }

  useEffect(() => {
    let active = true;
    api<{ items: ChatModel[] }>("/api/chat/models").then(data => {
      if (!active) return;
      setModels(data.items);
      setModelId(current => data.items.some(item => item.id === current) ? current : data.items[0]?.id ?? "");
      setModelError(data.items.length ? "" : "尚未配置可用的聊天模型");
    }).catch(error => {
      if (active) setModelError(error instanceof Error ? error.message : "模型列表加载失败");
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    setMessages([]); setModelError(""); setHistoryFailed(false);
    setNoteSession(null);
    setNotePrompt(undefined);
    setNoteRecovery(null);
    setNoteMaterials([]);
    setNoteScope("");
    setPickerOpen(false);
    setNoteCancelling(false);
    setPreview(null);
    conversationId.current = selectedConversationId ?? `chat-${crypto.randomUUID()}`;
    if (courseId && selectedConversationId) {
      setHistoryLoading(true);
      api<{ items: { role: "user" | "assistant"; content: string; draft?: DraftCard }[]; active_note?: ActiveNote | null }>(`/api/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(selectedConversationId)}/messages`)
        .then(data => { if (active) {
          setMessages(data.items.map(item => ({ from: item.role === "user" ? "user" : "agent", text: item.content, draft: item.draft })));
          setNoteSession(data.active_note?.session_id ?? null);
          setNotePrompt(data.active_note?.status === "needs_input" ? data.active_note.prompt : undefined);
          setNoteRecovery(data.active_note?.status === "running" || data.active_note?.status === "failed" ? data.active_note.status : null);
          restoreNoteInput(data.active_note, courseId, () => active);
        } })
        .catch(error => { if (active) { setHistoryFailed(true); setModelError(error instanceof Error ? error.message : "对话读取失败"); } })
        .finally(() => { if (active) setHistoryLoading(false); });
    } else setHistoryLoading(false);
    return () => { active = false; };
  }, [courseId, selectedConversationId]);

  useEffect(() => {
    setEditingTitle(false);
    setConversationTitle("AI 对话");
    if (!courseId || !selectedConversationId) return;
    let active = true;
    api<{ items: { conversation_id: string; title: string }[] }>(`/api/courses/${encodeURIComponent(courseId)}/conversations`)
      .then(data => { if (active) setConversationTitle(data.items.find(item => item.conversation_id === selectedConversationId)?.title || "AI 对话"); })
      .catch(() => {});
    return () => { active = false; };
  }, [courseId, selectedConversationId, titleRefreshKey]);

  async function saveTitle() {
    if (!courseId || !selectedConversationId || savingTitle) return;
    const title = titleDraft.trim();
    if (!title) { setModelError("对话名称不能为空"); return; }
    setSavingTitle(true); setModelError("");
    try {
      await api(`/api/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(selectedConversationId)}`, "PATCH", { title });
      setConversationTitle(title); setEditingTitle(false); onConversationRenamed();
    } catch (error) { setModelError(error instanceof Error ? error.message : "重命名失败"); }
    finally { setSavingTitle(false); }
  }

  useEffect(() => {
    const openHash = () => {
      const match = /^#note\/([\w.-]+)\/([\w.-]+)$/.exec(window.location.hash);
      if (!match) { setPreview(null); return; }
      api<NotePreviewData>(`/api/assets/${match[1]}/revisions/${match[2]}`)
        .then(setPreview).catch(() => setModelError("无法打开这份笔记草稿"));
    };
    window.addEventListener("hashchange", openHash);
    openHash();
    return () => window.removeEventListener("hashchange", openHash);
  }, []);

  function showAgentResult(result: AgentResult) {
    setNoteRecovery(null);
    if (result.status === "needs_input") {
      setNotePrompt(result.prompt);
      setMessages(current => [...current, { from: "agent", text: result.prompt?.message ?? "请补充笔记要求" }]);
    } else {
      setNoteSession(null);
      setNotePrompt(undefined);
      setMessages(current => [...current, { from: "agent", text: result.answer || "笔记任务已完成", draft: result.draft }]);
    }
  }

  async function resumeNote() {
    if (!courseId || !noteSession || isSending || !noteMaterials.length) return;
    setIsSending(true);
    setModelError("");
    try {
      const result = await noteRequest(`/agent/resume-note?conversation_id=${encodeURIComponent(conversationId.current)}&event_id=${crypto.randomUUID()}`, {
        course_id: courseId, session_id: noteSession,
        note_input: { note_type: noteType, scope: noteScope.trim(), duration_minutes: Number(noteDuration),
          source_document_ids: noteMaterials.map(item => item.document_id) },
      });
      setMessages(current => [...current, { from: "user", text: `补充笔记要求：使用 ${noteMaterials.map(materialLabel).join("、")}；${noteScope.trim() ? `写作要求 ${noteScope.trim()}；` : ""}阅读时长 ${noteDuration} 分钟` }]);
      showAgentResult(result);
    } catch (error) {
      setModelError(error instanceof Error ? error.message : "笔记生成失败");
      setNoteRecovery("failed");
      setNotePrompt(undefined);
    } finally { setIsSending(false); }
  }

  async function cancelNote() {
    if (!courseId || !noteSession || isSending || noteCancelling) return;
    setNoteCancelling(true); setModelError("");
    try {
      await api(`/agent/cancel-note?conversation_id=${encodeURIComponent(conversationId.current)}`,
        "POST", { course_id: courseId, session_id: noteSession });
      setNotePrompt(undefined);
      setNoteSession(null);
      setNoteRecovery(null);
      setNoteMaterials([]);
      setNoteScope("");
      setMessages(current => [...current, { from: "agent", text: "已取消笔记生成。你可以继续聊天，或重新发起笔记任务。" }]);
    } catch (error) {
      setModelError(error instanceof ApiError && error.status === 404
        ? "取消接口尚未在当前后端生效。请重启后端并刷新页面，然后重试取消。"
        : error instanceof Error ? error.message : "取消笔记任务失败");
    }
    finally { setNoteCancelling(false); }
  }

  async function recoverNote() {
    if (!courseId || !noteSession || isSending) return;
    setIsSending(true); setModelError("");
    try {
      const result = await noteRequest(`/agent/recover?course_id=${encodeURIComponent(courseId)}&session_id=${encodeURIComponent(noteSession)}&conversation_id=${encodeURIComponent(conversationId.current)}`);
      showAgentResult(result);
      const history = await api<{ items: { role: "user" | "assistant"; content: string; draft?: DraftCard }[]; active_note?: ActiveNote | null }>(`/api/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(conversationId.current)}/messages`);
      setMessages(history.items.map(item => ({ from: item.role === "user" ? "user" : "agent", text: item.content, draft: item.draft })));
      setNoteSession(history.active_note?.session_id ?? null);
      setNotePrompt(history.active_note?.status === "needs_input" ? history.active_note.prompt : undefined);
      setNoteRecovery(history.active_note?.status === "running" || history.active_note?.status === "failed" ? history.active_note.status : null);
      restoreNoteInput(history.active_note, courseId, () => mounted.current);
    } catch (error) { setModelError(error instanceof Error ? error.message : "恢复笔记任务失败"); setNoteRecovery("failed"); }
    finally { setIsSending(false); }
  }

  useEffect(() => {
    const container = messagesRef.current;
    if (container) container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }, [messages, isSending]);

  useEffect(() => {
    if (!modelMenuOpen) return;
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (!modelPickerRef.current?.contains(event.target as Node)) setModelMenuOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    modelPickerRef.current?.querySelector<HTMLButtonElement>(".model-option[aria-selected='true']")?.focus();
    return () => document.removeEventListener("pointerdown", closeOnOutsideClick);
  }, [modelMenuOpen]);

  function handleModelKeys(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      setModelMenuOpen(false);
      modelTriggerRef.current?.focus();
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (!modelMenuOpen) { setModelMenuOpen(true); return; }
    const options = [...(modelPickerRef.current?.querySelectorAll<HTMLButtonElement>(".model-option") ?? [])];
    const current = options.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
      : (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
    options[next]?.focus();
  }

  async function send(value = draft) {
    const message = value.trim();
    if (!message || isSending) return;
    if (!courseId) { setModelError("请先到“课程管理”选择一门课程"); return; }
    if (historyLoading || historyFailed) return;
    if (noteSession) { setModelError("请先完成或恢复当前笔记任务，再发送新消息"); return; }
    if (!modelId) { setModelError("请先选择一个可用模型"); return; }
    const history = messages;
    const requestConversationId = conversationId.current;
    const selectedLabel = models.find(item => item.id === modelId)?.label ?? modelId;
    setModelError("");
    setModelMenuOpen(false);
    setMessages([...history, { from: "user", text: message }]);
    setDraft("");
    setIsSending(true);
    let noteStarted = false;
    try {
      if (/(?:生成|整理|制作).{0,8}笔记|章节笔记|考点清单|问答卡片|口诀|速记/.test(message)) {
        const sessionId = `note-${crypto.randomUUID()}`;
        setNoteSession(sessionId);
        noteStarted = true;
        const result = await noteRequest(`/agent/invoke?conversation_id=${encodeURIComponent(requestConversationId)}`, {
          course_id: courseId, session_id: sessionId, message, intent: "note",
        });
        if (!mounted.current || conversationId.current !== requestConversationId) return;
        showAgentResult(result);
        if (!selectedConversationId) onConversationCreated(requestConversationId);
        return;
      }
      const result = await api<{ reply: string; model: string }>("/api/chat", "POST", {
        message,
        course_id: courseId,
        conversation_id: requestConversationId,
        model_id: modelId,
        mode: "direct",
      });
      if (!mounted.current || conversationId.current !== requestConversationId) return;
      setMessages(current => [...current, { from: "agent", text: result.reply, model: result.model || selectedLabel }]);
      if (!selectedConversationId) onConversationCreated(requestConversationId);
    } catch (error) {
      if (!mounted.current || conversationId.current !== requestConversationId) return;
      const detail = error instanceof Error ? error.message : "模型服务暂时不可用";
      setMessages(history);
      setDraft(message);
      setModelError(`发送失败：${detail}`);
      if (noteStarted) {
        setNoteRecovery("failed");
        if (!selectedConversationId) {
          void api(`/api/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(requestConversationId)}/messages`)
            .then(() => onConversationCreated(requestConversationId)).catch(() => {});
        }
      }
    } finally {
      if (mounted.current && conversationId.current === requestConversationId) setIsSending(false);
    }
  }

  function newConversation() {
    if (isSending) return;
    onNewConversation();
  }

  return <div className="dialog-page" inert={Boolean(notePrompt)}><section className="box chat">
    <header><div className="chat-title-area"><small>AI 对话</small>{editingTitle ? <form className="chat-title-editor" onSubmit={event => { event.preventDefault(); void saveTitle(); }}><input autoFocus aria-label="对话名称" maxLength={100} value={titleDraft} onChange={event => setTitleDraft(event.target.value)} onKeyDown={event => { if (event.key === "Escape") setEditingTitle(false); }} /><button type="submit" disabled={savingTitle}>保存</button><button type="button" onClick={() => setEditingTitle(false)}>取消</button></form> : <button className="chat-title-button" type="button" disabled={!selectedConversationId} title={selectedConversationId ? "点击重命名对话" : "发送消息后可重命名"} onClick={() => { setTitleDraft(conversationTitle); setEditingTitle(true); }}><h2>{conversationTitle}</h2>{selectedConversationId && <span aria-hidden="true">✎</span>}</button>}</div><button className="new-chat" type="button" onClick={newConversation} disabled={isSending}>＋ 新对话</button></header>
    <div className="messages" ref={messagesRef} aria-live="polite">
      {historyLoading ? <div className="empty-chat" role="status">正在加载对话…</div> : messages.length === 0 && <div className="empty-chat"><i>✦</i><h1>今天想从哪里开始？</h1><p>可以让我整理重点、解析难题，或根据你的课程资料出一组练习题。</p></div>}
      {messages.map((item, index) => <article className={`message ${item.from}`} key={index}>
        <i aria-hidden="true">{item.from === "agent" ? "✦" : "你"}</i>
        <div><label>{item.from === "agent" ? item.model || "助手" : "你"}</label><p>{item.text}</p>{item.draft && <a className="note-draft-card" href={item.draft.url}><strong>{item.draft.title}</strong><span>打开笔记草稿 →</span></a>}</div>
      </article>)}
      {isSending && <div className="chat-thinking" role="status">✦　正在生成回复…</div>}
    </div>
    {modelError && <p className="chat-error" role="alert">{modelError}</p>}
    {noteRecovery && noteSession && <div className="note-clarification"><strong>{noteRecovery === "running" ? "笔记任务仍在处理中" : "笔记任务未完成"}</strong><button type="button" disabled={isSending} onClick={() => void recoverNote()}>恢复笔记任务</button></div>}
    {notePrompt && !pickerOpen && <NoteConfigDialog noteType={noteType} onNoteType={setNoteType} duration={noteDuration} onDuration={setNoteDuration} materials={noteMaterials} onChooseMaterials={() => setPickerOpen(true)} requirements={noteScope} onRequirements={setNoteScope} onGenerate={() => void resumeNote()} onCancel={() => void cancelNote()} busy={isSending || noteCancelling} error={modelError} promptMessage={notePrompt.message} />}
    {pickerOpen && courseId && <NoteMaterialPicker courseId={courseId} selected={noteMaterials} onConfirm={items => { setNoteMaterials(items); setPickerOpen(false); }} onClose={() => setPickerOpen(false)} />}
    {preview && <div className="note-preview-backdrop"><section className="note-preview" role="dialog" aria-modal="true" aria-label="笔记草稿预览"><button type="button" onClick={() => { window.location.hash = ""; setPreview(null); }}>关闭</button><small>草稿 · 尚未确认</small><h2>{preview.asset.title}</h2>{preview.revision.points.map(point => <article key={point.point_id}><h3>{point.heading}</h3><p>{point.content}</p><small>{point.provenance === "ai_supplement" ? "AI 补充" : point.provenance === "synthesis" ? "综合改编" : "资料来源"}</small>{point.references.map(ref => <a key={ref.chunk_id} href={`#materials/${preview.asset.course_id}/${ref.document_id}/${ref.chunk_id}`}>{ref.file_name} · {ref.source_type} · 查看片段</a>)}</article>)}</section></div>}
    <div className="composer">
      <textarea value={draft} disabled={isSending || historyLoading || historyFailed} onChange={event => setDraft(event.target.value)} onKeyDown={event => {
        if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); }
      }} placeholder={isSending ? "助手正在思考……" : "输入你的问题，或让 AI 帮你制定学习计划、解析难题、生成练习题……"} />
      <div><div className="model-picker" ref={modelPickerRef} onKeyDown={handleModelKeys}>
        <button className="model-trigger" ref={modelTriggerRef} type="button" aria-label="选择聊天模型" aria-haspopup="listbox" aria-expanded={modelMenuOpen} disabled={isSending || models.length === 0} onClick={() => setModelMenuOpen(open => !open)}>
          <span className="model-sparkle" aria-hidden="true">✦</span><span>{models.find(model => model.id === modelId)?.label ?? "加载模型中…"}</span><span className={`model-chevron ${modelMenuOpen ? "open" : ""}`} aria-hidden="true">⌄</span>
        </button>
        {modelMenuOpen && <div className="model-menu" role="listbox" aria-label="聊天模型">
          <div className="model-menu-title">选择聊天模型</div>
          {models.map(model => <button className="model-option" type="button" role="option" aria-selected={model.id === modelId} key={model.id} onClick={() => {
            setModelId(model.id);
            setModelMenuOpen(false);
            modelTriggerRef.current?.focus();
          }}><span className="model-option-icon" aria-hidden="true">✦</span><span>{model.label}</span>{model.id === modelId && <span className="model-check" aria-hidden="true">✓</span>}</button>)}
        </div>}
      </div><button className="send" disabled={isSending || historyLoading || historyFailed || !draft.trim() || !modelId} onClick={() => void send()} aria-label="发送消息">{isSending ? "…" : "➤"}</button></div>
    </div>
    <footer className="quick"><span>试试这样问：</span>{["制定今天的复习计划", "出 10 道需求分析题", "总结第 3 章重点"].map(text => <button disabled={isSending || !modelId} key={text} onClick={() => void send(text)}>{text}</button>)}</footer>
  </section></div>;
}
