import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Node, mergeAttributes } from "@tiptap/core";
import { NodeViewContent, NodeViewWrapper, ReactNodeViewRenderer, type NodeViewProps } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { plainNode, type PageImage } from "./material-note-api";

export const MaterialNoteContext = createContext<{
  images: Record<number, PageImage>; editable: boolean; originals: Record<string, string>;
  retryImages: () => void;
}>({ images: {}, editable: false, originals: {}, retryImages: () => {} });

function SourceView({ node }: NodeViewProps) {
  const { originals } = useContext(MaterialNoteContext);
  const [expanded, setExpanded] = useState(true);
  const note = node.type.name === "teachingNote";
  const origin = node.attrs.content_origin;
  const changed = origin === "native" || origin === "teaching_note"
    ? originals[node.attrs.block_id] !== undefined && originals[node.attrs.block_id] !== plainNode(node.toJSON()) : false;
  const label = origin === "user_added" ? "用户补充" : origin === "user_edit" || changed ? "用户编辑" : note ? "教学备注" : "课件原文";
  return <NodeViewWrapper className={`material-note-source${note ? " is-teaching-note" : ""}${node.attrs.searchable === false ? " is-fragment" : ""}`}
    data-note-block={node.attrs.block_id} data-page={node.attrs.page ?? "supplement"}>
    <div className="material-note-source-meta" contentEditable={false}>
      <span>{node.attrs.page ? `第 ${node.attrs.page} 页` : "补充说明"}</span><span>{label}</span>
      {node.attrs.searchable === false && <span className="material-note-fragment-label" title={(node.attrs.exclusion_reasons ?? []).join("；")}>原生片段 · 待补充</span>}
      {note && <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? "收起备注" : "展开备注"}</button>}
    </div>
    <div className={expanded ? "" : "material-note-folded"}><NodeViewContent className="material-note-source-content" /></div>
  </NodeViewWrapper>;
}

function PageImageView({ node, updateAttributes }: NodeViewProps) {
  const { images, editable, retryImages } = useContext(MaterialNoteContext);
  const [opened, setOpened] = useState(false);
  const [broken, setBroken] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const image = images[Number(node.attrs.page)];
  const hidden = !!node.attrs.hidden;
  return <NodeViewWrapper className="material-note-image" contentEditable={false}
    data-note-block={node.attrs.block_id} data-page={node.attrs.page}>
    <div className="material-note-image-label"><strong>第 {node.attrs.page} 页 · 原页图片</strong>
      <span>{hidden ? "已隐藏" : image?.status === "ready" ? "点击图片放大" : image?.status === "failed" ? "生成失败" : "后台生成中"}</span>
      {editable && <button type="button" onClick={() => updateAttributes({ hidden: !hidden })}>{hidden ? "恢复图片" : "隐藏图片"}</button>}
    </div>
    {!hidden && (image?.status === "ready" && image.url ? <>
      <button type="button" className="material-note-image-open" aria-label={`展开第 ${node.attrs.page} 页原图`} onClick={() => setOpened(true)}>
        <img key={`${image.url}:${loadAttempt}`} loading="lazy" src={image.url + (loadAttempt ? `?reload=${loadAttempt}` : "")} alt={`第 ${node.attrs.page} 页原页`} onLoad={() => setBroken(false)} onError={() => setBroken(true)} />
      </button>
      {broken && <p role="alert">原页图片暂时不可用，可重试加载。<button type="button" onClick={() => { setBroken(false); setLoadAttempt(value => value + 1); }}>重新加载图片</button></p>}
    </> : <div className="material-note-image-placeholder"><span aria-hidden="true">▧</span>
      <p>{image?.status === "failed" ? image.error || "原页生成失败，正文仍可阅读和编辑。" : "原页正在后台生成，你可以先阅读或编辑正文。"}</p>
      {image?.status === "failed" && <button type="button" onClick={retryImages}>重试图片</button>}
    </div>)}
    {opened && image?.url && <NoteDialog title={`第 ${node.attrs.page} 页原图`} onClose={() => setOpened(false)}>
      <img className="material-note-expanded-image" src={image.url} alt={`第 ${node.attrs.page} 页原页放大`} />
    </NoteDialog>}
  </NodeViewWrapper>;
}

export function NoteDialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); return () => { ref.current?.close(); }; }, []);
  return <dialog className="material-note-dialog" ref={ref} aria-label={title} onCancel={event => { event.preventDefault(); onClose(); }}>
    <header><h2>{title}</h2><button type="button" autoFocus onClick={onClose}>关闭</button></header>{children}
  </dialog>;
}

function sourceNode(name: string) {
  return Node.create({
    name, group: "materialBlock", content: "block+", defining: true, isolating: true,
    addAttributes: () => ({
      block_id: { default: null }, page: { default: null }, content_origin: { default: "user_added" },
      searchable: { default: true }, exclusion_reasons: { default: [] },
    }),
    parseHTML: () => [{ tag: `section[data-material-kind="${name}"]` }],
    renderHTML: ({ HTMLAttributes }) => ["section", mergeAttributes(HTMLAttributes, { "data-material-kind": name }), 0],
    addNodeView: () => ReactNodeViewRenderer(SourceView),
  });
}

export const materialNoteExtensions = [
  StarterKit.configure({ document: false, blockquote: false, horizontalRule: false, link: false,
    underline: false, trailingNode: false, heading: { levels: [1, 2, 3] } }),
  Node.create({ name: "doc", topNode: true, content: "materialBlock*" }),
  sourceNode("sourceBlock"), sourceNode("teachingNote"), sourceNode("nativeFragment"),
  Node.create({
    name: "pageImage", group: "materialBlock", atom: true, selectable: false,
    addAttributes: () => ({ block_id: { default: null }, page: { default: null }, hidden: { default: false } }),
    parseHTML: () => [{ tag: "figure[data-page-image]" }],
    renderHTML: ({ HTMLAttributes }) => ["figure", mergeAttributes(HTMLAttributes, { "data-page-image": "true" })],
    addNodeView: () => ReactNodeViewRenderer(PageImageView),
  }),
];
