import { useEffect, useRef, useState } from "react";
import { api } from "./workbench-api";

type ChatModel = { id: string; label: string };
type Message = { from: "agent" | "user"; text: string; model?: string };

export default function ChatHome({ courseId }: { courseId: string | null }) {
  const [models, setModels] = useState<ChatModel[]>([]);
  const [modelId, setModelId] = useState("");
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [modelError, setModelError] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);
  const messagesRef = useRef<HTMLDivElement>(null);
  const modelPickerRef = useRef<HTMLDivElement>(null);
  const modelTriggerRef = useRef<HTMLButtonElement>(null);
  const conversationId = useRef(`chat-${crypto.randomUUID()}`);

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
    setMessages([]);
    conversationId.current = `chat-${crypto.randomUUID()}`;
  }, [courseId]);

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
    if (!courseId) { setModelError("请先到“课程与考试”选择一门课程"); return; }
    if (!modelId) { setModelError("请先选择一个可用模型"); return; }
    const history = messages;
    const selectedLabel = models.find(item => item.id === modelId)?.label ?? modelId;
    setModelError("");
    setModelMenuOpen(false);
    setMessages([...history, { from: "user", text: message }]);
    setDraft("");
    setIsSending(true);
    try {
      const result = await api<{ reply: string; model: string }>("/api/chat", "POST", {
        message,
        course_id: courseId,
        conversation_id: conversationId.current,
        model_id: modelId,
        mode: "direct",
        history: history.slice(-20).map(item => ({
          role: item.from === "agent" ? "assistant" : "user",
          content: item.text,
        })),
      });
      setMessages(current => [...current, { from: "agent", text: result.reply, model: result.model || selectedLabel }]);
    } catch (error) {
      const detail = error instanceof Error ? error.message : "模型服务暂时不可用";
      setMessages(history);
      setDraft(message);
      setModelError(`发送失败：${detail}`);
    } finally {
      setIsSending(false);
    }
  }

  function newConversation() {
    if (isSending) return;
    setMessages([]);
    setDraft("");
    setModelError("");
    conversationId.current = `chat-${crypto.randomUUID()}`;
  }

  return <div className="dialog-page"><section className="box chat">
    <header><div><h2>AI 对话</h2></div><button className="new-chat" type="button" onClick={newConversation} disabled={isSending}>＋ 新对话</button></header>
    <div className="messages" ref={messagesRef} aria-live="polite">
      {messages.length === 0 && <div className="empty-chat"><i>✦</i><h1>今天想从哪里开始？</h1><p>可以让我整理重点、解析难题，或根据你的课程资料出一组练习题。</p></div>}
      {messages.map((item, index) => <article className={`message ${item.from}`} key={index}>
        <i aria-hidden="true">{item.from === "agent" ? "✦" : "你"}</i>
        <div><label>{item.from === "agent" ? item.model || "助手" : "你"}</label><p>{item.text}</p></div>
      </article>)}
      {isSending && <div className="chat-thinking" role="status">✦　正在生成回复…</div>}
    </div>
    {modelError && <p className="chat-error" role="alert">{modelError}</p>}
    <div className="composer">
      <textarea value={draft} disabled={isSending} onChange={event => setDraft(event.target.value)} onKeyDown={event => {
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
      </div><button className="send" disabled={isSending || !draft.trim() || !modelId} onClick={() => void send()} aria-label="发送消息">{isSending ? "…" : "➤"}</button></div>
    </div>
    <footer className="quick"><span>试试这样问：</span>{["制定今天的复习计划", "出 10 道需求分析题", "总结第 3 章重点"].map(text => <button disabled={isSending || !modelId} key={text} onClick={() => void send(text)}>{text}</button>)}</footer>
  </section></div>;
}
