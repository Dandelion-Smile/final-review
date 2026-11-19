import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function moduleUrl(path) {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  return outputText;
}
const routes = await moduleUrl("../src/course-routes.ts");
const toUrl = source => "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const source = (await moduleUrl("../src/course-overview-api.ts")).replace('"./course-routes"', JSON.stringify(toUrl(routes)));
const { catalogFailed, catalogFailureSummary, catalogNotice, overviewActivity, overviewChapterSummary, placementLabel, visibleChapters } = await import(toUrl(source));

const fallback = { id: "未分章", title: "未分章", points: [{ name: "HTTP" }] };
const formal = { id: "formal", title: "应用层", points: [] };
const empty = { chapters: [], point_count: 0, build_status: {}, catalog_job: null };
const published = { ...empty, chapters: [formal, fallback], point_count: 1, catalog: { status: "ready", chapter_count: 1 } };

test("only published chapters contribute to the course total", () => {
  assert.equal(overviewChapterSummary(empty), "");
  for (const catalog of [undefined, { status: "uninitialized", chapter_count: 0 }]) {
    assert.equal(overviewChapterSummary({ ...empty, chapters: [fallback], point_count: 1, catalog }), "尚未生成章节 · 1 个知识点");
  }
  assert.equal(overviewChapterSummary(published), "1 章 · 1 个知识点");
  assert.deepEqual(visibleChapters(published.chapters, "", published.catalog).map(x => x.id), ["formal", "未分章"]);
  assert.deepEqual(visibleChapters(published.chapters, "HTTP", published.catalog).map(x => x.id), ["未分章"]);
  assert.equal(overviewChapterSummary({ ...published, catalog: { status: "stale", chapter_count: 0 } }), "0 章 · 1 个知识点");
  assert.equal(placementLabel({ catalog_placement: "unclassified" }), "暂未归入章节");
  assert.equal(placementLabel({ catalog_placement: "pending_catalog" }), "等待目录整理");
});

test("material, build and catalog each report their own activity", () => {
  const tree = { ...published, build_status: { queued: 1, failed: 1 } };
  assert.deepEqual(overviewActivity(tree, [{ parse_status: "running" }]), { parsing: true, building: true, catalog: false });
  for (const status of ["queued", "running", "retry_wait"]) {
    assert.deepEqual(overviewActivity({ ...tree, catalog_job: { status } }, [{ parse_status: "queued" }]),
      { parsing: true, building: true, catalog: true });
  }
  for (const status of ["succeeded", "failed", undefined]) {
    assert.deepEqual(overviewActivity({
      ...tree,
      build_status: { failed: 1 },
      catalog: { ...tree.catalog, is_updating: true },
      catalog_job: status ? { status } : null,
    }, [{ parse_status: "failed" }]), { parsing: false, building: false, catalog: false });
  }
});

test("initial generation and updates preserve their meaning even without visible points", () => {
  const job = status => ({ status });
  assert.equal(catalogNotice({ ...empty, catalog_job: job("queued") }).text, "课程知识目录生成已排队");
  assert.equal(catalogNotice({ ...empty, catalog_job: job("running") }).text, "正在生成课程知识目录");
  assert.equal(catalogNotice({ ...empty, catalog_job: job("failed") }).text, "课程知识目录生成失败");
  assert.equal(catalogNotice({ ...published, catalog_job: job("running") }).text, "正在更新知识目录，当前展示上一版本");
  assert.equal(catalogNotice({ ...published, catalog_job: job("retry_wait") }).text, "整理暂时中断，等待自动重试，当前展示上一版本");
  assert.equal(catalogNotice({ ...published, catalog_job: job("failed") }).text, "知识目录更新失败，当前展示上一版本");
  assert.deepEqual(catalogNotice({ ...published, catalog: { ...published.catalog, status: "stale" } }),
    { tone: "attention", text: "课程资料已有更新，目录尚未同步" });
});

test("terminal no-op codes never use failure affordances or backend error messages", () => {
  for (const error_code of ["CatalogNoChanges", "SupersededBeforeExecution"]) {
    const catalog_job = { status: "failed", error_code, stage: null };
    assert.equal(catalogFailed(catalog_job), false);
    assert.equal(overviewActivity({ ...published, catalog_job }, []).catalog, false);
    assert.equal(catalogNotice({ ...published, catalog_job }).tone, "neutral");
    assert.equal(catalogNotice({ ...published, catalog_job, catalog: { status: "stale" } }).tone, "attention");
  }
  assert.equal(catalogFailureSummary({ error_code: "CatalogOutputInvalid" }), "目录结果未通过校验。");
  assert.equal(catalogFailureSummary({ error_code: "provider error", error_message: "internal" }), "");
});
