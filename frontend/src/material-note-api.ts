import type { JSONContent } from "@tiptap/core";

export type NoteBody = JSONContent & { type: "doc"; content: JSONContent[] };
export type NoteRevision = { revision_id: string; revision_no: number; body: NoteBody; created_at: string; input_hash: string };
export type PolishStatus = {
  polish_status: "none" | "queued" | "running" | "ready" | "candidate" | "applied" | "failed" | "not_required";
  polish_stage?: string | null;
  polish_batches_done?: number | null;
  polish_batches_total?: number | null;
  polish_error?: string | null;
  polish_error_code?: string | null;
  candidate_revision_id?: string | null;
};
export type NoteStatus = PolishStatus & {
  note_status: string; latest_saved_revision_id?: string; indexed_revision_id?: string | null;
  knowledge_revision_id?: string | null; revision_numbers?: Record<string, number>;
  index_status?: string; index_error?: string; knowledge_status?: string; knowledge_error?: string;
  images_status?: string; parse_error?: string;
};
export type MaterialNote = {
  revision: NoteRevision; initial_revision_id: string; latest_saved_revision_id: string;
  indexed_revision_id: string | null; knowledge_revision_id: string | null;
  editable: boolean; sync_status: string; status: NoteStatus;
  document: { document_id: string; title: string; file_name: string; source_type: string; parse_strategy?: string };
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
      const attrs: Record<string, unknown> = Object.fromEntries(["block_id", "page", "content_origin", "searchable", "exclusion_reasons", "source_refs"]
        .map(key => [key, node.attrs?.[key]]));
      if (Array.isArray(attrs.source_refs)) attrs.source_refs = attrs.source_refs.filter((ref): ref is string => typeof ref === "string" && ref.length > 0);
      else attrs.source_refs = [];
      if (typeof node.attrs?.content_role === "string") attrs.content_role = node.attrs.content_role;
      out.attrs = attrs;
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
    ["paragraph", "heading", "codeBlock"].includes(node.type ?? "") ? "" : node.type === "tableRow" ? "\t" : "\n");
}

export const bodyKey = (value: JSONContent) => JSON.stringify(wireBody(value));

export const NOTE_STRATEGIES = ["text-images-v1", "text-polish-v1"] as const;

export type NoteEntry = {
  parse_strategy?: string;
  note_status?: string;
  polish_status?: PolishStatus["polish_status"];
  preview_status?: string;
  parse_status?: string;
};

/** A supported strategy has published a real note version. Polish progress is separate. */
export function readableNoteVersion(item: NoteEntry): boolean {
  return (NOTE_STRATEGIES as readonly string[]).includes(item.parse_strategy ?? "") && item.note_status === "ready";
}

export function noteProgressLabel(item: NoteEntry): string {
  const readable = readableNoteVersion(item);
  if (item.polish_status === "candidate" && readable) return "新版笔记已就绪，可查看并应用";
  if (readable && (item.polish_status === "ready" || item.polish_status === "applied")) return "AI 润色完成，可查看详情";
  if (item.polish_status === "failed") return readable ? "原文可读，打开详情查看失败原因" : "AI 润色失败，暂时无法查看详情";
  if (item.polish_status === "not_required") return "无可提取文字，无法生成润色笔记";
  if (item.polish_status === "candidate") return "正在准备新版笔记";
  if (item.polish_status === "queued" || item.polish_status === "running") {
    return readable ? "原文可读，AI 润色进行中" : "AI 润色中，完成后可查看详情";
  }
  if (!readable && (item.note_status === "failed" || item.preview_status === "failed" || item.parse_status === "failed")) {
    return "资料提取失败，暂时无法查看详情";
  }
  if (readable) return "原文可读，可查看详情";
  if (!(NOTE_STRATEGIES as readonly string[]).includes(item.parse_strategy ?? "")) return "尚未生成 AI 润色笔记";
  return "正在提取文字，润色完成后可查看详情";
}

export type MaterialJobRef = { job_id: string; document_id: string; status: string; phase?: string | null };

export function failedProcessingJob(jobs: MaterialJobRef[], documentId: string): MaterialJobRef | null {
  return jobs.find(job => job.document_id === documentId && job.status === "failed" && job.phase !== "images") ?? null;
}

export function materialActionState(item: NoteEntry & { document_id: string }, jobs: MaterialJobRef[], busy: boolean) {
  const retry = failedProcessingJob(jobs, item.document_id);
  return {
    detailEnabled: readableNoteVersion(item) && !busy,
    retryJobId: retry?.job_id ?? null,
    retryEnabled: Boolean(retry) && !busy,
    label: noteProgressLabel(item),
  };
}
