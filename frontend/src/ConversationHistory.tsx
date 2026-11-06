import { useEffect, useRef, useState } from "react";
import { api } from "./workbench-api";
type Conversation = { conversation_id: string; title: string; updated_at: string };

export default function ConversationHistory({ courseId, selectedId, onSelect, onClose, onRenamed }: {
  courseId: string; selectedId: string | null; onSelect: (id: string) => void; onClose: () => void; onRenamed: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [items, setItems] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [editing, setEditing] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    api<{ items: Conversation[] }>(`/api/courses/${encodeURIComponent(courseId)}/conversations`)
      .then(data => { if (active) setItems(data.items); })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "历史对话加载失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [courseId, retry]);
  async function save() {
    if (!editing || !title.trim() || saving) return;
    setSaving(true); setError("");
    try {
      const updated = await api<Conversation>(`/api/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(editing)}`, "PATCH", { title: title.trim() });
      setItems(current => current.map(item => item.conversation_id === updated.conversation_id ? updated : item));
      setEditing(null); onRenamed();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "重命名失败"); }
    finally { setSaving(false); }
  }
  return <dialog ref={dialog} className="conversation-history" aria-labelledby="conversation-history-title" onCancel={event => { event.preventDefault(); if (!saving) onClose(); }} onClick={event => { if (event.target === event.currentTarget && !saving) onClose(); }}>
    <div className="conversation-history-heading"><div><h2 id="conversation-history-title">历史对话</h2><p>当前课程的全部对话</p></div><button type="button" aria-label="关闭历史对话" disabled={saving} onClick={onClose}>×</button></div>
    {error && <p className="chat-error" role="alert">{error} {!editing && <button type="button" onClick={() => setRetry(value => value + 1)}>重试</button>}</p>}
    {loading ? <p role="status">正在加载对话…</p> : !error && !items.length ? <p className="conversation-history-empty">还没有对话，关闭窗口后点击“新建对话”开始。</p> : <div className="conversation-history-list">{items.map(item => <div className="conversation-history-row" key={item.conversation_id}>
      {editing === item.conversation_id ? <form onSubmit={event => { event.preventDefault(); void save(); }}><input autoFocus aria-label="对话名称" maxLength={100} value={title} onChange={event => setTitle(event.target.value)} disabled={saving}/><div><button type="submit" disabled={saving || !title.trim()}>保存</button><button type="button" disabled={saving} onClick={() => setEditing(null)}>取消</button></div></form> : <><button type="button" className={selectedId === item.conversation_id ? "selected" : ""} aria-current={selectedId === item.conversation_id ? "true" : undefined} onClick={() => { onSelect(item.conversation_id); onClose(); }}><strong>{item.title || "未命名对话"}</strong><small>{new Date(item.updated_at).toLocaleString("zh-CN")}</small></button><button type="button" className="conversation-history-rename" aria-label={`重命名${item.title || "未命名对话"}`} onClick={() => { setEditing(item.conversation_id); setTitle(item.title); setError(""); }}>重命名</button></>}
    </div>)}</div>}
  </dialog>;
}
