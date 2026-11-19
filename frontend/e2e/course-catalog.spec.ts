import { expect, test, type Page, type Route } from "@playwright/test";

const api = "http://127.0.0.1:8081";
const APPLIED = "11111111-1111-4111-8111-111111111111";
const EMPTY = "22222222-2222-4222-8222-222222222222";

declare global {
  interface Window {
    __catalogPoll?: { max: number; size: number };
  }
}

function source(documentId = "doc-1", locatorId = "loc-1") {
  return {
    document_id: documentId, title: "HTTP课件", chapter: "第2章 HTTP", locator_id: locatorId,
    position_kind: "slide", position: 3, ordinal: 1,
  };
}

function point(id: string, name: string, extra: Record<string, unknown> = {}) {
  return {
    id, name, description: `${name}的说明`, importance_score: 0.86, fact_count: 1,
    fact_ids: [`fact-${id}`], sources: [source()], ...extra,
  };
}

function catalog(extra: Record<string, unknown> = {}) {
  return {
    status: "uninitialized", version_id: null, revision: null, knowledge_snapshot_hash: null,
    chapter_count: 0, assigned_count: 0, unassigned_count: 0, is_updating: false, ...extra,
  };
}

function job(extra: Record<string, unknown> = {}) {
  return {
    job_id: "job-1", mode: "initial", status: "succeeded", stage: "published", attempt: 1,
    next_run_at: null, result_version_id: "version-1", error_code: null, ...extra,
  };
}

function tree(extra: Record<string, unknown> = {}) {
  return {
    chapters: [], point_count: 0, updated_at: "2026-11-16T01:00:00Z", build_status: {},
    catalog: catalog(), catalog_job: null, ...extra,
  };
}

async function createCourse(request: { post: (url: string, options: { data: unknown }) => Promise<{ json: () => Promise<{ course_id: string; name: string }> }> }, name: string) {
  return (await request.post(`${api}/api/courses`, { data: { name } })).json();
}

async function showTree(page: Page, courseId: string, body: unknown) {
  const pattern = `**/api/courses/${courseId}/knowledge-tree`;
  try { await page.unroute(pattern); } catch { /* first visit */ }
  await page.route(pattern, route => route.fulfill({ json: body }));
  const url = `/#/courses/${courseId}/overview`;
  if (page.url().includes(`/courses/${courseId}/`)) await page.reload();
  else await page.goto(url);
}

function knowledge(page: Page) {
  return page.getByRole("region", { name: "知识目录", exact: true });
}

test.beforeEach(async ({ request }) => { await request.post(`${api}/test/reset`); });

test("legacy and uninitialized trees keep source chapters and the original empty state", async ({ page, request }) => {
  const course = await createCourse(request, "资料章节课");
  const empty = await createCourse(request, "空目录课");
  await showTree(page, course.course_id, {
    chapters: [
      { id: "第2章 HTTP", title: "第2章 HTTP", points: [point("http", "HTTP无状态性")] },
      { id: "未分章", title: "未分章", points: [point("loose", "零散定义", { importance_score: 0.2 })] },
    ],
    point_count: 2, updated_at: "2026-11-14T10:00:00Z", build_status: {},
  });
  const panel = knowledge(page);
  await expect(panel.locator(".co-tree-count")).toHaveText("尚未生成章节 · 2 个知识点");
  await expect(panel.getByText("当前按资料章节展示", { exact: true })).toBeVisible();
  await expect(panel.getByText("HTTP无状态性", { exact: true })).toBeVisible();
  await expect(panel.getByText("暂未归入章节")).toHaveCount(0);
  await page.getByRole("searchbox", { name: "搜索章节或知识点" }).fill("零散");
  await expect(panel.locator(".co-chapter")).toHaveCount(1);
  await page.getByRole("searchbox", { name: "搜索章节或知识点" }).fill("");
  await expect(panel.locator(".co-chapter")).toHaveCount(2);

  await showTree(page, course.course_id, tree({
    chapters: [{ id: "第2章 HTTP", title: "第2章 HTTP", points: [point("http", "HTTP无状态性")] }],
    point_count: 1,
    catalog: catalog({ status: "uninitialized" }),
  }));
  await expect(panel.getByText("当前按资料章节展示", { exact: true })).toBeVisible();
  await expect(panel.getByText("课程知识目录已更新")).toHaveCount(0);
  await expect(panel.getByText("HTTP无状态性", { exact: true })).toBeVisible();

  await showTree(page, empty.course_id, tree());
  await expect(knowledge(page).getByRole("heading", { name: "还没有已入库的知识点", exact: true })).toBeVisible();
  await expect(knowledge(page).locator(".co-catalog-note")).toHaveCount(0);
  await expect(knowledge(page).getByText("正在生成课程知识目录")).toHaveCount(0);
  await expect(knowledge(page).getByText("AI 分类失败")).toHaveCount(0);
});

test("ready catalog uses formal chapters, uuid keys and chapter_count", async ({ page, request }) => {
  const course = await createCourse(request, "正式目录课");
  const material = await (await request.post(`${api}/knowledge/ingest`, { data: {
    course_id: course.course_id, title: "HTTP课件", chapter: "第2章 HTTP", source_type: "teacher_ppt", markdown: "HTTP是无状态协议。",
  } })).json();
  const chunks = await (await request.get(`${api}/api/courses/${course.course_id}/documents/${material.document_id}/chunks`)).json();
  const http = point("point-http", "HTTP无状态性", { sources: [source(material.document_id, chunks.items[0].chunk_id)] });
  await page.route(`**/api/courses/${course.course_id}/knowledge-points/point-http`, route => route.fulfill({
    json: { facts: [{ id: "fact-point-http", fact_text: "HTTP是无状态协议。", status: "supported" }, { id: "removed", fact_text: "不应展示的历史事实", status: "supported" }] },
  }));
  await showTree(page, course.course_id, tree({
    chapters: [
      { id: EMPTY, title: "空章", description: "", points: [] },
      { id: APPLIED, title: "应用层", description: "协议与接口", points: [http] },
    ],
    point_count: 1,
    catalog: catalog({ status: "ready", version_id: "version-1", revision: 1, knowledge_snapshot_hash: "hash-1", chapter_count: 2, assigned_count: 1, unassigned_count: 0 }),
    catalog_job: job(),
    build_status: { failed: 2 },
  }));
  const panel = knowledge(page);
  await expect(panel.locator(".co-tree-count")).toHaveText("2 章 · 1 个知识点");
  await expect(panel.getByText("课程知识目录已更新", { exact: true })).toBeVisible();
  await expect(panel.getByText("部分资料的知识整理未完成")).toHaveCount(0);
  await expect(panel.getByText("知识目录更新失败")).toHaveCount(0);
  await expect(panel.locator(".co-chapter")).toHaveCount(2);
  await expect(panel.locator(".co-chapter-number").nth(1)).toHaveText("02");
  await expect(panel.getByText("协议与接口", { exact: true })).toBeVisible();
  await expect(panel.getByText("当前没有可展示的知识点", { exact: true })).toBeVisible();
  await expect(panel.getByText("未分章", { exact: true })).toHaveCount(0);
  await expect(panel.getByText("80%")).toHaveCount(0);
  await page.getByRole("searchbox", { name: "搜索章节或知识点" }).fill("空章");
  await expect(panel.locator(".co-chapter")).toHaveCount(1);
  await expect(panel.getByText("当前没有可展示的知识点", { exact: true })).toBeVisible();
  await page.getByRole("searchbox", { name: "搜索章节或知识点" }).fill("HTTP");
  await expect(panel.locator(".co-chapter")).toHaveCount(1);
  await expect(panel.getByText("应用层", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "全部收起", exact: true }).click();
  await expect(panel.locator(".co-chapter[open]")).toHaveCount(0);
  await page.getByRole("button", { name: "全部展开", exact: true }).click();
  await page.getByRole("button", { name: "HTTP无状态性，查看知识点来源" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("HTTP是无状态协议。");
  await expect(dialog).not.toContainText("不应展示的历史事实");
  await expect(dialog).toContainText("第2章 HTTP");
  await dialog.getByRole("link", { name: /HTTP课件/ }).click();
  await expect(page.getByRole("dialog", { name: "整理后的资料" })).toContainText("HTTP是无状态协议。");
});

test("stale catalog stays readable and distinguishes unassigned placements", async ({ page, request }) => {
  const course = await createCourse(request, "过期目录课");
  const assigned = point("kept", "HTTP无状态性", { catalog_placement: undefined });
  delete (assigned as { catalog_placement?: string }).catalog_placement;
  await showTree(page, course.course_id, tree({
    chapters: [
      { id: APPLIED, title: "应用层", description: "上一版", points: [assigned] },
      { id: "未分章", title: "未分章", points: [
        point("old", "连接复用", { catalog_placement: "unclassified", importance_score: 0.2 }),
        point("new", "默认端口", { catalog_placement: "pending_catalog", importance_score: 0.2 }),
      ] },
    ],
    point_count: 3,
    catalog: catalog({ status: "stale", version_id: "version-1", revision: 1, knowledge_snapshot_hash: "old-hash", chapter_count: 1, assigned_count: 1, unassigned_count: 2 }),
    catalog_job: job({ status: "succeeded", mode: "incremental", result_version_id: "version-1" }),
  }));
  const panel = knowledge(page);
  await expect(panel.locator(".co-tree-count")).toHaveText("1 章 · 3 个知识点");
  await expect(panel.getByText("课程资料已有更新，目录尚未同步", { exact: true })).toBeVisible();
  await expect(panel.getByText("正在加载知识目录")).toHaveCount(0);
  await expect(panel.getByText("应用层", { exact: true })).toBeVisible();
  await expect(panel.locator(".co-chapter")).toHaveCount(2);
  await expect(panel.getByText("暂未归入章节", { exact: true })).toHaveCount(1);
  await expect(panel.getByText("等待目录整理", { exact: true })).toHaveCount(1);
  await expect(panel.getByRole("button", { name: "连接复用，查看知识点来源" })).toHaveCount(1);
  await expect(panel.getByRole("button", { name: "默认端口，查看知识点来源" })).toHaveCount(1);
  await expect(panel.locator(".co-placement")).toHaveCount(2);
  await expect(panel.getByRole("alert")).toHaveCount(0);
  await expect(panel.getByText("分类失败")).toHaveCount(0);
  for (const width of [1440, 375, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(panel.locator(".co-chapter")).toHaveCount(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
});

test("catalog jobs explain progress, no-changes and superseded work without hiding the tree", async ({ page, request }) => {
  const course = await createCourse(request, "目录任务课");
  const chapters = [{ id: "第1章", title: "第1章 概述", points: [point("intro", "课程概述", { importance_score: 0.2 })] }];
  const cases: Array<{ body: unknown; text: string; absent: string[] }> = [
    {
      body: tree({ chapters, point_count: 1, catalog: catalog({ is_updating: true }), catalog_job: job({ status: "queued", stage: "collecting", attempt: 0, result_version_id: null }) }),
      text: "课程知识目录生成已排队",
      absent: ["课程知识目录已更新", "分类失败", "80%"],
    },
    {
      body: tree({ chapters, point_count: 1, catalog: catalog({ is_updating: true }), catalog_job: job({ status: "running", stage: "assigning" }) }),
      text: "正在生成课程知识目录",
      absent: ["分类失败", "90%"],
    },
    {
      body: tree({
        chapters: [{ id: APPLIED, title: "应用层", points: [point("http", "HTTP无状态性")] }],
        point_count: 1,
        catalog: catalog({ status: "stale", version_id: "version-1", revision: 1, knowledge_snapshot_hash: "old", chapter_count: 1, assigned_count: 1, unassigned_count: 0, is_updating: true }),
        catalog_job: job({ status: "running", mode: "incremental", stage: "matching" }),
      }),
      text: "正在更新知识目录，当前展示上一版本",
      absent: ["正在加载知识目录", "分类失败"],
    },
    {
      body: tree({
        chapters: [{ id: APPLIED, title: "应用层", points: [point("http", "HTTP无状态性")] }],
        point_count: 1,
        catalog: catalog({ status: "stale", version_id: "version-1", revision: 1, knowledge_snapshot_hash: "old", chapter_count: 1, assigned_count: 1, is_updating: true }),
        catalog_job: job({ status: "retry_wait", mode: "incremental", stage: "model_call", attempt: 2, next_run_at: "2026-11-16T01:10:00Z", error_code: "APITimeoutError" }),
      }),
      text: "整理暂时中断，等待自动重试，当前展示上一版本",
      absent: ["分类失败", "50%"],
    },
    {
      body: tree({
        chapters: [{ id: APPLIED, title: "应用层", points: [point("http", "HTTP无状态性")] }],
        point_count: 1,
        catalog: catalog({ status: "ready", version_id: "version-1", revision: 1, knowledge_snapshot_hash: "same", chapter_count: 1, assigned_count: 1 }),
        catalog_job: job({ status: "failed", stage: "no_changes", error_code: "CatalogNoChanges", result_version_id: null }),
      }),
      text: "目录没有需要发布的新版本",
      absent: ["分类失败", "知识目录更新失败", "AI 分类失败"],
    },
    {
      body: tree({
        chapters: [
          { id: APPLIED, title: "应用层", points: [point("http", "HTTP无状态性")] },
          { id: "未分章", title: "未分章", points: [point("new", "新增知识点", { catalog_placement: "pending_catalog" })] },
        ],
        point_count: 2,
        catalog: catalog({ status: "stale", version_id: "version-1", revision: 1, knowledge_snapshot_hash: "old", chapter_count: 1, assigned_count: 1, unassigned_count: 1 }),
        catalog_job: job({ status: "failed", stage: "no_changes", error_code: "CatalogNoChanges", result_version_id: null }),
      }),
      text: "课程资料已有更新，目录尚未同步",
      absent: ["分类失败", "目录没有需要发布的新版本", "知识目录更新失败"],
    },
    {
      body: tree({
        chapters: [{ id: APPLIED, title: "应用层", points: [point("http", "HTTP无状态性")] }],
        point_count: 1,
        catalog: catalog({ status: "stale", version_id: "version-1", revision: 1, knowledge_snapshot_hash: "old", chapter_count: 1, assigned_count: 1 }),
        catalog_job: job({ status: "failed", stage: "collecting", error_code: "SupersededBeforeExecution", result_version_id: null }),
      }),
      text: "课程资料已有更新，目录尚未同步",
      absent: ["知识目录更新失败", "分类失败"],
    },
    {
      body: tree({
        chapters: [{ id: APPLIED, title: "应用层", points: [point("http", "HTTP无状态性")] }],
        point_count: 1,
        catalog: catalog({ status: "stale", version_id: "version-1", revision: 1, knowledge_snapshot_hash: "old", chapter_count: 1, assigned_count: 1 }),
        catalog_job: job({ status: "failed", mode: "incremental", stage: "assigning", error_code: "ModelOutputInvalid", result_version_id: null }),
      }),
      text: "知识目录更新失败，当前展示上一版本",
      absent: ["分类失败", "部分资料的知识整理未完成"],
    },
  ];
  let current = cases[0].body;
  await page.route(`**/api/courses/${course.course_id}/knowledge-tree`, route => route.fulfill({ json: current }));
  await page.goto(`/#/courses/${course.course_id}/overview`);
  for (const item of cases) {
    current = item.body;
    if (item !== cases[0]) await page.reload();
    const panel = knowledge(page);
    await expect(panel.getByText(item.text, { exact: true })).toBeVisible();
    for (const absent of item.absent) await expect(panel.getByText(absent, { exact: false })).toHaveCount(0);
    await expect(panel.locator(".co-chapter")).not.toHaveCount(0);
  }
});

async function acceleratePoll(page: Page) {
  await page.addInitScript(() => {
    const pollIds = new Set<number>();
    const originalSet = window.setTimeout.bind(window);
    const originalClear = window.clearTimeout.bind(window);
    window.__catalogPoll = { max: 0, size: 0 };
    window.setTimeout = ((fn: TimerHandler, delay?: number, ...args: unknown[]) => {
      const isPoll = delay === 10000;
      const id = originalSet(fn as () => void, isPoll ? 40 : delay, ...args) as unknown as number;
      if (isPoll) {
        pollIds.add(id);
        window.__catalogPoll!.size = pollIds.size;
        window.__catalogPoll!.max = Math.max(window.__catalogPoll!.max, pollIds.size);
      }
      return id;
    }) as typeof window.setTimeout;
    window.clearTimeout = ((id?: number) => {
      if (id != null) pollIds.delete(id);
      window.__catalogPoll!.size = pollIds.size;
      return originalClear(id);
    }) as typeof window.clearTimeout;
  });
}

function updatingTree(title: string) {
  return tree({
    chapters: [{ id: APPLIED, title, points: [point("http", "HTTP无状态性")] }],
    point_count: 1,
    catalog: catalog({ status: "ready", version_id: "version-1", revision: 1, knowledge_snapshot_hash: "hash", chapter_count: 1, assigned_count: 1, is_updating: true }),
    catalog_job: job({ status: "running", stage: "assigning" }),
  });
}

test("one poll loop follows catalog updates and stops when work ends", async ({ page, request }) => {
  const course = await createCourse(request, "轮询课");
  await acceleratePoll(page);
  const reads: string[] = [];
  page.on("request", req => {
    const path = new URL(req.url()).pathname;
    if (path.startsWith("/api/courses/" + course.course_id + "/")) reads.push(path.split("/").at(-1)!);
  });
  let calls = 0;
  await page.route(`**/api/courses/${course.course_id}/knowledge-tree`, (route: Route) => {
    calls += 1;
    const updating = calls === 1;
    return route.fulfill({ json: tree({
      ...updatingTree(updating ? "整理中的应用层" : "已发布的应用层"),
      catalog: catalog({
        status: "ready", version_id: updating ? "version-1" : "version-2", revision: updating ? 1 : 2,
        knowledge_snapshot_hash: "hash", chapter_count: 1, assigned_count: 1, is_updating: updating,
      }),
      catalog_job: job({ status: updating ? "running" : "succeeded", stage: updating ? "assigning" : "published", result_version_id: updating ? null : "version-2" }),
    }) });
  });
  await page.goto(`/#/courses/${course.course_id}/overview`);
  await expect(knowledge(page).getByText("正在更新知识目录，当前展示上一版本", { exact: true })).toBeVisible();
  await expect.poll(() => calls).toBe(2);
  await expect(knowledge(page).getByText("课程知识目录已更新", { exact: true })).toBeVisible();
  await expect(knowledge(page).getByText("已发布的应用层", { exact: true })).toBeVisible();
  await page.waitForTimeout(160);
  expect(calls).toBe(2);
  for (const endpoint of ["documents", "notes", "quizzes", "formal-attempts"]) {
    expect(reads.filter(path => path === endpoint)).toHaveLength(1);
  }
  expect(await page.evaluate(() => window.__catalogPoll?.max ?? 0)).toBe(1);
});

test("stale, uninitialized, failed and no-changes catalogs do not keep polling", async ({ page, request }) => {
  const cases = [
    ["stale", catalog({ status: "stale", version_id: "version-1", revision: 1, knowledge_snapshot_hash: "old", chapter_count: 1, assigned_count: 1 }), job({ status: "succeeded" })],
    ["uninitialized", catalog({ status: "uninitialized" }), null],
    ["failed", catalog({ status: "stale", version_id: "version-1", revision: 1, knowledge_snapshot_hash: "old", chapter_count: 1, assigned_count: 1 }), job({ status: "failed", stage: "assigning", error_code: "ModelOutputInvalid", result_version_id: null })],
    ["superseded", catalog({ status: "uninitialized" }), job({ status: "failed", error_code: "SupersededBeforeExecution", result_version_id: null })],
    ["no-changes", catalog({ status: "ready", version_id: "version-1", revision: 1, knowledge_snapshot_hash: "same", chapter_count: 1, assigned_count: 1 }), job({ status: "failed", stage: "no_changes", error_code: "CatalogNoChanges", result_version_id: null })],
  ] as const;
  await acceleratePoll(page);
  const prepared = [];
  for (const [name, catalogBody, catalogJob] of cases) {
    const course = await createCourse(request, `停止轮询-${name}`);
    let calls = 0;
    await page.route(`**/api/courses/${course.course_id}/knowledge-tree`, route => {
      calls += 1;
      return route.fulfill({ json: tree({
        chapters: catalogBody.status === "uninitialized"
          ? [{ id: "第1章", title: "第1章 概述", points: [point("intro", "课程概述")] }]
          : [{ id: APPLIED, title: "应用层", points: [point("http", "HTTP无状态性")] }],
        point_count: 1,
        catalog: catalogBody,
        catalog_job: catalogJob,
      }) });
    });
    prepared.push({ name, course, calls: () => calls });
  }
  await page.goto(`/#/courses/${prepared[0].course.course_id}/overview`);
  for (const [index, item] of prepared.entries()) {
    if (index > 0) await page.evaluate(courseId => { window.location.hash = `#/courses/${courseId}/overview`; }, item.course.course_id);
    await expect(page.getByRole("heading", { name: `停止轮询-${item.name}`, exact: true })).toBeVisible();
    await expect(knowledge(page).locator(".co-chapter")).toHaveCount(1);
    await page.waitForTimeout(160);
    expect(item.calls()).toBe(1);
    expect(await page.evaluate(() => window.__catalogPoll?.max ?? 0)).toBe(0);
  }
});

test("refresh keeps the current tree, ignores stale responses and clears timers on leave", async ({ page, request }) => {
  const course = await createCourse(request, "竞态课");
  const other = await createCourse(request, "另一门课");
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await acceleratePoll(page);
  let calls = 0;
  const waiting: Array<() => void> = [];
  const settled = tree({
    chapters: [{ id: EMPTY, title: "更新后的传输层", points: [point("tcp", "可靠传输")] }],
    point_count: 1,
    catalog: catalog({ status: "ready", version_id: "version-2", revision: 2, knowledge_snapshot_hash: "new", chapter_count: 1, assigned_count: 1 }),
    catalog_job: job({ status: "succeeded", mode: "incremental", result_version_id: "version-2" }),
  });
  await page.route(`**/api/courses/${course.course_id}/knowledge-tree`, async route => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ json: updatingTree("仍在屏幕上的应用层") });
      return;
    }
    await new Promise<void>(resolve => waiting.push(resolve));
    await route.fulfill({ json: settled }).catch(() => undefined);
  });
  await page.goto(`/#/courses/${course.course_id}/overview`);
  await expect(knowledge(page).getByText("仍在屏幕上的应用层", { exact: true })).toBeVisible();
  await expect.poll(() => waiting.length).toBeGreaterThan(0);
  await expect(knowledge(page).getByText("正在加载知识目录")).toHaveCount(0);
  await expect(knowledge(page).getByText("仍在屏幕上的应用层", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => window.__catalogPoll?.max ?? 0)).toBe(1);
  await page.waitForTimeout(160);
  expect(calls).toBe(2);
  for (const release of waiting) release();
  await expect(knowledge(page).getByText("更新后的传输层", { exact: true })).toBeVisible();
  await expect(knowledge(page).getByText("仍在屏幕上的应用层")).toHaveCount(0);
  await page.waitForTimeout(160);
  const settledCalls = calls;

  let late = false;
  await page.unroute(`**/api/courses/${course.course_id}/knowledge-tree`);
  let releaseLate: (() => void) | undefined;
  await page.route(`**/api/courses/${course.course_id}/knowledge-tree`, async route => {
    await new Promise<void>(resolve => { releaseLate = resolve; });
    late = true;
    await route.fulfill({ json: updatingTree("卸载后才返回的章节") }).catch(() => undefined);
  });
  await page.route(`**/api/courses/${other.course_id}/knowledge-tree`, route => route.fulfill({ json: tree({
    chapters: [{ id: "other-chapter", title: "乙课传输层", points: [point("other", "乙课知识点")] }],
    point_count: 1,
    catalog: catalog({ status: "uninitialized" }),
  }) }));
  await page.reload();
  await expect.poll(() => Boolean(releaseLate)).toBe(true);
  await page.evaluate(courseId => { window.location.hash = "#/courses/" + courseId + "/overview"; }, other.course_id);
  await expect(page.getByRole("heading", { name: "另一门课", exact: true })).toBeVisible();
  await expect(knowledge(page).getByText("乙课传输层", { exact: true })).toBeVisible();
  releaseLate?.();
  await expect.poll(() => late).toBe(true);
  await expect(page.getByText("卸载后才返回的章节")).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => window.__catalogPoll?.size ?? 0)).toBe(0);
  expect(calls).toBe(settledCalls);
  await expect(knowledge(page).getByText("卸载后才返回的章节")).toHaveCount(0);
  await expect(knowledge(page).getByText("更新后的传输层")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("parse and build activity can coincide and the final parse refresh reads the later tree", async ({ page, request }) => {
  const course = await createCourse(request, "解析入库状态");
  await acceleratePoll(page);
  let documentReads = 0;
  let treeReads = 0;
  let releaseDocuments: (() => void) | undefined;
  let releaseBuild: (() => void) | undefined;
  await page.route("**/api/courses/" + course.course_id + "/documents", async route => {
    const first = ++documentReads === 1;
    if (!first) await new Promise<void>(resolve => { releaseDocuments = resolve; });
    await route.fulfill({ json: { items: [{ document_id: "doc", title: "课件", parse_status: first ? "running" : "ready" }] } });
  });
  await page.route("**/api/courses/" + course.course_id + "/knowledge-tree", async route => {
    const current = ++treeReads;
    if (current === 3) await new Promise<void>(resolve => { releaseBuild = resolve; });
    await route.fulfill({ json: tree({
      build_status: current < 3 ? { running: 1 } : { succeeded: 1 },
      ...(current >= 2 ? {
        chapters: [{ id: "第1章", title: "资料章节", points: [point("intro", "入库知识点")] }],
        point_count: 1,
      } : {}),
    }) });
  });
  await page.goto("/#/courses/" + course.course_id + "/overview");
  await expect(page.getByText("资料正在解析", { exact: true })).toBeVisible();
  await expect(page.getByText("知识点正在入库", { exact: true })).toBeVisible();
  await expect(knowledge(page).getByText("正在生成课程知识目录")).toHaveCount(0);
  await expect.poll(() => Boolean(releaseDocuments)).toBe(true);
  await page.waitForTimeout(160);
  expect(treeReads).toBe(1);
  releaseDocuments?.();
  await expect(page.getByText("资料正在解析", { exact: true })).toHaveCount(0);
  await expect(page.getByText("知识点正在入库", { exact: true })).toBeVisible();
  await expect(knowledge(page).locator(".co-tree-count")).toHaveText("尚未生成章节 · 1 个知识点");
  await expect.poll(() => Boolean(releaseBuild)).toBe(true);
  releaseBuild?.();
  await expect(page.getByText("知识点正在入库", { exact: true })).toHaveCount(0);
  await page.waitForTimeout(160);
  expect([documentReads, treeReads]).toEqual([2, 3]);
});

test("initial catalog failure is visible on an empty tree and retries the existing job once", async ({ page, request }) => {
  const course = await createCourse(request, "首版失败课");
  const root = "**/api/courses/" + course.course_id;
  let status = "failed";
  let submissions = 0;
  let releaseRetry: (() => void) | undefined;
  await page.route(root + "/knowledge-tree", route => route.fulfill({ json: tree({
    catalog: catalog({ is_updating: status === "queued" }),
    catalog_job: job({ status, error_code: status === "failed" ? "CatalogOutputInvalid" : null, result_version_id: null }),
  }) }));
  await page.route(root + "/knowledge-catalog/jobs/job-1/retry", async route => {
    expect(route.request().method()).toBe("POST");
    submissions += 1;
    await new Promise<void>(resolve => { releaseRetry = resolve; });
    status = "queued";
    await route.fulfill({ json: job({ status }) });
  });
  await page.goto("/#/courses/" + course.course_id + "/overview");
  const panel = knowledge(page);
  await expect(panel.getByRole("alert")).toHaveText("课程知识目录生成失败。目录结果未通过校验。");
  await expect(panel.getByText("正在生成课程知识目录")).toHaveCount(0);
  await panel.getByRole("button", { name: "重试目录任务" }).click();
  await expect(panel.getByRole("button", { name: "正在提交…" })).toBeDisabled();
  await expect.poll(() => Boolean(releaseRetry)).toBe(true);
  releaseRetry?.();
  await expect(panel.getByText("课程知识目录生成已排队")).toBeVisible();
  await expect(panel.getByRole("alert")).toHaveCount(0);
  expect(submissions).toBe(1);
});

test("a catalog refresh transport error preserves the old tree and stops automatic requests", async ({ page, request }) => {
  const course = await createCourse(request, "刷新失败保留目录");
  await acceleratePoll(page);
  let calls = 0;
  await page.route("**/api/courses/" + course.course_id + "/knowledge-tree", route => {
    calls += 1;
    if (calls === 2) return route.fulfill({ status: 503 });
    if (calls === 1) return route.fulfill({ json: updatingTree("上一版章节") });
    const ready = updatingTree("重新加载的章节");
    return route.fulfill({ json: { ...ready, catalog_job: job({ status: "succeeded" }), catalog: { ...ready.catalog, is_updating: false } } });
  });
  await page.goto("/#/courses/" + course.course_id + "/overview");
  const panel = knowledge(page);
  await expect(panel.getByRole("alert")).toContainText("知识目录刷新失败，当前展示上次加载的内容");
  await expect(panel.getByText("上一版章节", { exact: true })).toBeVisible();
  await expect(panel.getByText("正在更新知识目录")).toHaveCount(0);
  await page.waitForTimeout(160);
  expect(calls).toBe(2);
  await panel.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect(panel.getByText("重新加载的章节", { exact: true })).toBeVisible();
  expect(calls).toBe(3);
});
