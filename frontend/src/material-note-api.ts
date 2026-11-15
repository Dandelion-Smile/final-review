import type { JSONContent } from "@tiptap/core";

export type NoteBody = JSONContent & { type: "doc"; content: JSONContent[] };
export type NoteRevision = { revision_id: string; revision_no: number; body: NoteBody; created_at: string; input_hash: string };
export type NoteStatus = {
  note_status: string; latest_saved_revision_id?: string; indexed_revision_id?: string | null;
  knowledge_revision_id?: string | null; revision_numbers?: Record<string, number>;
  index_status?: string; index_error?: string; knowledge_status?: string; knowledge_error?: string;
  images_status?: string; parse_error?: string;
};
export type MaterialNote = {
  revision: NoteRevision; initial_revision_id: string; latest_saved_revision_id: string;
  indexed_revision_id: string | null; knowledge_revision_id: string | null;
  editable: boolean; sync_status: string; status: NoteStatus;
  document: { document_id: string; title: string; file_name: string; source_type: string };
  pages: { page_number: number; title: string }[];
};
export type PageImage = { page_number: number; status: string; url: string | null; error?: string };
export type ImageManifest = { status: string; items: PageImage[] };
export const notePath = (courseId: string, documentId: string) =>
  `/api/courses/${encodeURIComponent(courseId)}/documents/${encodeURIComponent(documentId)}`;

/** Strip editor defaults outside our server schema; keep image UI outside JSON. */
export function wireBody(value: JSONContent): NoteBody {
  function clean(node: JSONContent): JSONContent {
    const out: JSONContent = { type: node.type };
    if (node.type === "text") {
      out.text = node.text;
      if (node.marks?.length) out.marks = node.marks.map(mark => ({ type: mark.type }));
      return out;
    }
    if (["sourceBlock", "teachingNote", "nativeFragment"].includes(node.type ?? "")) {
      out.attrs = Object.fromEntries(["block_id", "page", "content_origin", "searchable", "exclusion_reasons"]
        .map(key => [key, node.attrs?.[key]]));
    } else if (node.type === "pageImage") {
      out.attrs = { block_id: node.attrs?.block_id, page: node.attrs?.page, hidden: !!node.attrs?.hidden };
    } else if (node.type === "heading") out.attrs = { level: node.attrs?.level ?? 1 };
    else if (node.type === "orderedList") out.attrs = { start: node.attrs?.start ?? 1 };
    if (node.content !== undefined) out.content = node.content.map(clean);
    return out;
  }
  return { type: "doc", content: (value.content ?? []).map(clean) };
}

export function plainNode(node: JSONContent): string {
  if (node.type === "text") return node.text ?? "";
  if (node.type === "hardBreak") return "\n";
  if (node.type === "pageImage") return "";
  return (node.content ?? []).map(plainNode).join(
    ["paragraph", "heading", "codeBlock"].includes(node.type ?? "") ? "" : "\n");
}

export const bodyKey = (value: JSONContent) => JSON.stringify(wireBody(value));
