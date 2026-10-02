import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./workbench-api";
import "./note-material-picker.css";

export type NoteMaterial = {
  document_id: string;
  title: string;
  file_name?: string;
  source_type: string;
  chapter?: string;
  parse_status: string;
  parse_error?: string;
  uploaded_at?: string;
  file_size?: number;
};

const sourceNames: Record<string, string> = {
  past_exam: "历年真题", teacher_ppt: "老师 PPT", homework: "平时作业",
  other_practice: "其他练习", crash_course: "速成课", ai_supplement: "AI 补充",
};
const statusNames: Record<string, string> = {
  ready: "可检索", queued: "排队中", running: "处理中", failed: "处理失败",
};
const MAX_SELECTION = 100;

export function materialLabel(item: NoteMaterial): string {
  return item.file_name || item.title;
}

export default function NoteMaterialPicker({ courseId, selected, onConfirm, onClose }: {
  courseId: string;
  selected: NoteMaterial[];
  onConfirm: (items: NoteMaterial[]) => void;
  onClose: () => void;
}) {
  const [items, setItems] = useState<NoteMaterial[]>([]);
  const [checked, setChecked] = useState<string[]>(selected.map(item => item.document_id));
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const dialogRef = useRef<HTMLElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let active = true;
    api<{ items: NoteMaterial[] }>(`/api/courses/${encodeURIComponent(courseId)}/documents`)
      .then(data => { if (active) setItems(data.items.filter(item => item.parse_status !== "deleted")); })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "资料列表加载失败"); })
      .finally(() => { if (active) setLoading(false); });
    searchRef.current?.focus();
    return () => { active = false; };
  }, [courseId]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); return; }
      if (event.key !== "Tab") return;
      const controls = [...(dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled),input:not(:disabled),a[href]'
      ) ?? [])].filter(element => element.getClientRects().length > 0);
      if (!controls.length) return;
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const available = items.filter(item => item.parse_status === "ready");
  const filtered = useMemo(() => items.filter(item =>
    `${item.file_name ?? ""} ${item.title} ${item.chapter ?? ""} ${sourceNames[item.source_type] ?? ""}`
      .toLocaleLowerCase("zh-CN").includes(query.trim().toLocaleLowerCase("zh-CN"))
  ), [items, query]);
  const selectedItems = available.filter(item => checked.includes(item.document_id));

  return createPortal(<div className="note-source-backdrop" onMouseDown={event => {
    if (event.target === event.currentTarget) onClose();
  }}><section className="note-source-dialog" role="dialog" aria-modal="true" aria-labelledby="note-source-title" ref={dialogRef}>
    <header className="note-source-head"><div><small>当前课程 · 笔记资料</small><h2 id="note-source-title">选择生成依据</h2><p>笔记只会引用你在这里勾选的资料。</p></div><button type="button" className="note-source-close" aria-label="关闭资料选择" onClick={onClose}>×</button></header>
    <div className="note-source-tools"><label className="note-source-search"><span>查找资料</span><input ref={searchRef} type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索文件名、章节或来源" /></label><div className="note-source-select-all"><span>已选 {selectedItems.length} / {available.length} 份可用资料{available.length > MAX_SELECTION ? " · 单次最多 100 份" : ""}</span><button type="button" disabled={!available.length || available.length > MAX_SELECTION} onClick={() => setChecked(available.map(item => item.document_id))}>选择全部可用资料</button><button type="button" disabled={!checked.length} onClick={() => setChecked([])}>清空</button></div></div>
    <div className="note-source-list" role="group" aria-label="当前课程资料">
      {loading ? <p className="note-source-state">正在读取课程资料…</p> : error ? <p className="note-source-state" role="alert">{error}</p> : filtered.length === 0 ? <p className="note-source-state">{query ? "没有符合搜索条件的资料。" : "当前课程还没有资料。请先到“我的资料”上传。"}</p> : filtered.map(item => {
        const ready = item.parse_status === "ready";
        const isChecked = checked.includes(item.document_id);
        return <label key={item.document_id} className={`note-source-row${isChecked ? " selected" : ""}${ready ? "" : " unavailable"}`}>
          <input type="checkbox" checked={isChecked} disabled={!ready || (!isChecked && checked.length >= MAX_SELECTION)} onChange={() => setChecked(current => isChecked ? current.filter(id => id !== item.document_id) : [...current, item.document_id])} aria-label={`选择 ${materialLabel(item)}，编号 ${item.document_id.slice(0, 8)}`} />
          <span className="note-source-row-body"><strong>{materialLabel(item)}</strong><span>{sourceNames[item.source_type] ?? item.source_type} · {item.chapter || "未归类"} · {item.uploaded_at ? new Date(item.uploaded_at).toLocaleString("zh-CN") : "上传时间未知"} · 编号 {item.document_id.slice(0, 8)}</span>{!ready && item.parse_error && <em>{item.parse_error}</em>}</span>
          <span className={`note-source-status ${item.parse_status}`}>{statusNames[item.parse_status] ?? item.parse_status}</span>
        </label>;
      })}
    </div>
    <footer className="note-source-footer"><span>{selectedItems.length ? `将使用 ${selectedItems.length} 份资料` : "至少选择一份可检索资料"}</span><div>{!loading && !available.length && <button type="button" className="note-source-cancel" onClick={() => { onClose(); window.location.hash = `#materials/${courseId}`; }}>管理资料</button>}<button type="button" className="note-source-cancel" onClick={onClose}>取消</button><button type="button" className="note-source-confirm" disabled={!selectedItems.length} onClick={() => onConfirm(selectedItems)}>确认选择</button></div></footer>
  </section></div>, document.body);
}
