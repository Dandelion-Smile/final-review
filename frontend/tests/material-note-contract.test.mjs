import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function loadTypeScript(path) {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  const result = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } });
  return import("data:text/javascript;base64," + Buffer.from(result.outputText).toString("base64"));
}
const { wireBody, bodyKey, plainNode, readableNoteVersion, noteProgressLabel, materialActionState } = await loadTypeScript("../src/material-note-api.ts");
const { materialNoteHref, materialNoteDocument, readRoute, legacyHash } = await loadTypeScript("../src/course-routes.ts");
const source = content => ({ type: "sourceBlock", attrs: { block_id: "native-1", page: 1,
  content_origin: "native", searchable: true, exclusion_reasons: [], source_refs: [] }, content });

test("editor code language defaults and ordered-list metadata obey the server contract", () => {
  const body = wireBody({ type: "doc", content: [source([
    { type: "codeBlock", attrs: { language: null }, content: [{ type: "text", text: "if (x > 0) return 53;" }] },
    { type: "orderedList", attrs: { start: 3, type: null }, content: [{ type: "listItem", content: [{ type: "paragraph" }] }] },
  ])] });
  assert.equal(body.content[0].content[0].attrs, undefined);
  assert.deepEqual(body.content[0].content[1].attrs, { start: 3 });
  assert.equal(plainNode(body).includes("if (x > 0) return 53;"), true);
});

test("image captions, URLs and transient background state never enter saved JSON", () => {
  const body = wireBody({ type: "doc", content: [{ type: "pageImage", attrs: {
    block_id: "image-2", page: 2, hidden: true, src: "/private/image.png", alt: "an image caption", image_status: "ready",
  } }] });
  assert.deepEqual(body.content, [{ type: "pageImage", attrs: { block_id: "image-2", page: 2, hidden: true } }]);
  assert.equal(plainNode(body), "");
});

test("formatting remains editable while source identity and exact text survive serialization", () => {
  const body = { type: "doc", content: [source([{ type: "heading", attrs: { level: 2 },
    content: [{ type: "text", text: "DNS 并非永远只使用 UDP；端口为 53。", marks: [{ type: "bold", attrs: {} }] }] }])] };
  const clean = wireBody(body);
  assert.deepEqual(clean.content[0].attrs, body.content[0].attrs);
  assert.equal(plainNode(clean), "DNS 并非永远只使用 UDP；端口为 53。");
  assert.equal(bodyKey(clean), bodyKey(body));
});

test("reading-note route has priority and existing citation URLs remain usable", () => {
  const href = materialNoteHref("course-a", "document-b");
  assert.equal(href, "#/courses/course-a/materials/document-b/note");
  assert.equal(materialNoteDocument(readRoute(href)), "document-b");
  assert.equal(legacyHash(href), href);
  assert.equal(materialNoteDocument(readRoute("#/courses/course-a/materials/document-b/chunk-1")), null);
  assert.equal(legacyHash("#/courses/course-a/materials/document-b/chunk-1"), "#materials/course-a/document-b/chunk-1");
  assert.equal(readRoute("#materials/course-a/document-b/chunk-1").page, "materials");
});

test("learning-note tables, inline marks and server-owned content roles survive serialization", () => {
  const body = wireBody({ type: "doc", content: [{ ...source([
    { type: "table", attrs: { style: "untrusted" }, content: [
      { type: "tableRow", content: [{ type: "tableHeader", content: [{ type: "paragraph", content: [
        { type: "text", text: "参数", marks: [{ type: "bold" }] },
      ] }] }] },
      { type: "tableRow", content: [{ type: "tableCell", attrs: { colspan: 1 }, content: [{ type: "paragraph", content: [
        { type: "text", text: "port", marks: [{ type: "code" }] },
      ] }] }] },
    ] },
  ]), attrs: { ...source([]).attrs, content_role: "explanation" } }] });
  assert.equal(body.content[0].attrs.content_role, "explanation");
  const table = body.content[0].content[0];
  assert.equal(table.attrs, undefined);
  assert.equal(table.content[1].content[0].attrs, undefined);
  assert.deepEqual(table.content[0].content[0].content[0].content[0].marks, [{ type: "bold" }]);
  assert.equal(plainNode(body), "参数\nport");
});

test("readable original is independent of polish, and retry stays on the failed processing job", () => {
  const base = { document_id: "doc-1", parse_strategy: "text-polish-v1", note_status: "ready" };
  const cases = [
    { item: { ...base, polish_status: "queued" }, detail: true, label: "原文可读，AI 润色进行中" },
    { item: { ...base, polish_status: "running" }, detail: true, label: "原文可读，AI 润色进行中" },
    { item: { ...base, polish_status: "failed" }, detail: true, label: "原文可读，打开详情查看失败原因" },
    { item: { ...base, polish_status: "not_required" }, detail: true, label: "无可提取文字，无法生成润色笔记" },
    { item: { ...base, polish_status: "candidate" }, detail: true, label: "新版笔记已就绪，可查看并应用" },
    { item: { ...base, polish_status: "applied" }, detail: true, label: "AI 润色完成，可查看详情" },
    { item: { ...base, polish_status: "ready" }, detail: true, label: "AI 润色完成，可查看详情" },
    { item: { document_id: "doc-1", parse_strategy: "text-images-v1", note_status: "ready" }, detail: true, label: "原文可读，可查看详情" },
    { item: { document_id: "doc-1", parse_strategy: "text-polish-v1", note_status: "queued", parse_status: "queued" }, detail: false, label: "正在提取文字，润色完成后可查看详情" },
    { item: { document_id: "doc-1", parse_strategy: "text-polish-v1", note_status: "queued", polish_status: "ready" }, detail: false, label: "正在提取文字，润色完成后可查看详情" },
    { item: { document_id: "doc-1", parse_strategy: undefined, note_status: "ready" }, detail: false, label: "尚未生成 AI 润色笔记" },
    { item: { document_id: "doc-1", parse_strategy: "text-polish-v1", note_status: "failed", preview_status: "failed", parse_status: "failed" }, detail: false, label: "资料提取失败，暂时无法查看详情" },
  ];
  const failed = { job_id: "job-failed", document_id: "doc-1", status: "failed", phase: "preview" };
  const images = { job_id: "job-images", document_id: "doc-1", status: "failed", phase: "images" };
  for (const entry of cases) {
    assert.equal(readableNoteVersion(entry.item), entry.detail);
    assert.equal(noteProgressLabel(entry.item), entry.label);
    const idle = materialActionState(entry.item, [failed, images], false);
    assert.equal(idle.detailEnabled, entry.detail);
    assert.equal(idle.retryJobId, "job-failed");
    assert.equal(idle.retryEnabled, true);
    const busy = materialActionState(entry.item, [failed], true);
    assert.equal(busy.detailEnabled, false);
    assert.equal(busy.retryEnabled, false);
    assert.equal(busy.retryJobId, "job-failed");
  }
  const readableFailed = materialActionState({ ...base, polish_status: "failed" }, [failed], false);
  assert.equal(readableFailed.detailEnabled, true);
  assert.equal(readableFailed.retryJobId, "job-failed");
  const running = materialActionState(
    { document_id: "doc-1", parse_strategy: "text-polish-v1", note_status: "failed", parse_status: "failed" },
    [{ job_id: "job-run", document_id: "doc-1", status: "running", phase: "preview" }],
    false,
  );
  assert.equal(running.retryJobId, null);
  assert.equal(materialActionState(base, [images], false).retryJobId, null);
});
