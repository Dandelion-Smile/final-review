import { expect, test } from "@playwright/test";

const api = "http://127.0.0.1:8081";
test.beforeEach(async ({ request }) => { await request.post(`${api}/test/reset`); });

test("overview reads saved course content and opens actual note, document, paper and attempt routes", async ({ page, request }) => {
  const course = await (await request.post(`${api}/api/courses`, { data: { name: "Web服务端技术" } })).json();
  const other = await (await request.post(`${api}/api/courses`, { data: { name: "空课程" } })).json();
  const root = `${api}/api/courses/${course.course_id}`;
  const material = await (await request.post(`${api}/knowledge/ingest`, { data: {
    course_id: course.course_id, title: "TCP 讲义", chapter: "第1章", source_type: "teacher_ppt",
    markdown: "TCP 三次握手同步双方初始序列号，并确认双方收发能力。",
  } })).json();
  const note = await (await request.post(root + "/assets", { data: {
    asset_type: "note", title: "请求处理复习笔记", markdown: "真实保存的课程笔记内容。",
  } })).json();
  const generation = await request.post(root + "/quiz-jobs", { data: { quiz_input: {
    scope_mode: "chapter", chapter: "第1章", difficulty: "standard", duration_mode: "untimed",
    blueprint: [{ question_type: "choice", question_count: 2 }],
    source_document_ids: [material.document_id], include_imported_questions: false, allow_ai_supplement: false,
  } } });
  expect(generation.status()).toBe(202);
  const queued = await generation.json();
  const getJob = async () => (await request.get(root + "/quiz-jobs/" + queued.job_id)).json();
  await expect.poll(async () => (await getJob()).status).toBe("succeeded");
  const paper = (await getJob()).result;
  const overview = `/#/courses/${course.course_id}/overview`;
  await page.goto(overview);
  const nav = page.getByRole("navigation", { name: "课程导航" });
  await expect(nav.getByRole("button", { name: course.name, exact: true })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("region", { name: "课程笔记", exact: true })).toContainText("请求处理复习笔记");
  const materialCard = page.getByRole("region", { name: "课程资料", exact: true });
  await expect(materialCard).toContainText("TCP 讲义");
  // In-memory E2E adapter has no PostgreSQL knowledge store; this failure must stay local.
  await expect(page.getByRole("region", { name: "知识目录", exact: true })).toContainText("课程知识库需要 PostgreSQL");
  await page.getByRole("region", { name: "课程笔记", exact: true }).getByRole("link", { name: /请求处理复习笔记/ }).click();
  await expect(page).toHaveURL(new RegExp(`/notes/${note.asset.asset_id}/${note.revision.revision_id}$`));
  await expect(page.getByRole("heading", { name: "请求处理复习笔记", exact: true })).toBeVisible();
  await nav.getByRole("button", { name: course.name, exact: true }).click();
  await materialCard.getByRole("link", { name: /TCP 讲义/ }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${material.document_id}$`));
  await expect(page.getByRole("dialog", { name: "整理后的资料" })).toContainText("同步双方初始序列号");
  await page.getByRole("button", { name: "关闭", exact: true }).last().click();
  await nav.getByRole("button", { name: course.name, exact: true }).click();
  await page.getByRole("region", { name: "课程测试", exact: true }).locator(".co-resource").click();
  await expect(page).toHaveURL(new RegExp(`/quizzes/papers/${paper.asset_id}/${paper.revision_id}$`));
  await expect(page.getByRole("region", { name: "试卷预览", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "开始测试", exact: true }).click();
  await page.getByRole("button", { name: "开始日常练习", exact: true }).click();
  await expect(page).toHaveURL(/\/quizzes\/attempts\//);
  const attemptUrl = page.url();
  await nav.getByRole("button", { name: course.name, exact: true }).click();
  await page.getByRole("region", { name: "课程测试", exact: true }).getByRole("link", { name: /继续作答/ }).click();
  await expect(page).toHaveURL(attemptUrl);
  await page.goto(`/#/courses/${other.course_id}`);
  await expect(page.getByRole("heading", { name: "空课程", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "课程笔记", exact: true })).toContainText("还没有课程笔记");
  await expect(page.getByRole("region", { name: "课程资料", exact: true })).not.toContainText("TCP 讲义");
});

test("knowledge tree searches, folds, follows source locations and handles errors without hiding content", async ({ page, request }) => {
  const course = await (await request.post(`${api}/api/courses`, { data: { name: "目录交互验收" } })).json();
  const material = await (await request.post(`${api}/knowledge/ingest`, { data: {
    course_id: course.course_id, title: "HTTP课件", chapter: "第2章", source_type: "teacher_ppt", markdown: "HTTP是无状态协议。",
  } })).json();
  const chunks = await (await request.get(`${api}/api/courses/${course.course_id}/documents/${material.document_id}/chunks`)).json();
  const source = { document_id: material.document_id, title: "HTTP课件", chapter: "第2章", locator_id: chunks.items[0].chunk_id, position_kind: "document", position: null, ordinal: 0 };
  const point = { id: "point-1", name: "HTTP无状态性", description: "请求之间不自动保留状态。", fact_count: 1, fact_ids: ["fact-1"], importance_score: 0.8, sources: [source] };
  let unavailable = false;
  // Only the PostgreSQL-specific contract is stubbed; source navigation uses the real API.
  await page.route(`**/api/courses/${course.course_id}/knowledge-tree`, route => route.fulfill({
    status: unavailable ? 503 : 200, json: unavailable ? { detail: "知识库暂时不可用" } : {
      chapters: [{ id: "chapter-2", title: "第2章 HTTP", points: [point] }, { id: "chapter-10", title: "第10章 实践", points: [{ ...point, id: "point-2", name: "Servlet实践" }] }],
      point_count: 2, updated_at: "2026-11-14T10:00:00Z", build_status: {},
    },
  }));
  await page.route(`**/api/courses/${course.course_id}/knowledge-points/point-1`, route => route.fulfill({ json: { facts: [{ id: "fact-1", fact_text: "HTTP是无状态协议。", status: "supported" }, { id: "fact-removed", fact_text: "已删除资料的历史事实", status: "supported" }] } }));
  await page.goto(`/#/courses/${course.course_id}/overview`);
  await expect(page.locator(".co-chapter[open]")).toHaveCount(2);
  await page.getByRole("button", { name: "全部收起", exact: true }).click();
  await expect(page.locator(".co-chapter[open]")).toHaveCount(0);
  await page.getByRole("button", { name: "全部展开", exact: true }).click();
  await expect(page.locator(".co-chapter[open]")).toHaveCount(2);
  await page.getByRole("searchbox", { name: "搜索章节或知识点" }).fill("HTTP");
  await expect(page.locator(".co-chapter")).toHaveCount(1);
  await page.getByRole("button", { name: "全部收起", exact: true }).click();
  await expect(page.locator(".co-chapter[open]")).toHaveCount(0);
  await page.getByRole("button", { name: "全部展开", exact: true }).click();
  await expect(page.locator(".co-chapter[open]")).toHaveCount(1);
  await page.locator(".co-chapter summary").click();
  await expect(page.locator(".co-chapter[open]")).toHaveCount(0);
  await page.locator(".co-chapter summary").click();
  await expect(page.locator(".co-chapter[open]")).toHaveCount(1);
  await page.getByRole("button", { name: "HTTP无状态性，查看知识点来源" }).click();
  await expect(page.getByRole("dialog")).toContainText("HTTP是无状态协议。");
  await expect(page.getByRole("dialog")).not.toContainText("已删除资料的历史事实");
  await page.getByRole("dialog").getByRole("link", { name: /HTTP课件/ }).click();
  await expect(page.getByRole("dialog", { name: "整理后的资料" })).toContainText("HTTP是无状态协议。");
  await page.getByRole("button", { name: "关闭", exact: true }).last().click();
  await page.getByRole("navigation", { name: "课程导航" }).getByRole("button", { name: course.name, exact: true }).click();
  for (const width of [1440, 375, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.locator(".co-chapter")).toHaveCount(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.getByRole("searchbox", { name: "搜索章节或知识点" }).fill("zzzz");
  await expect(page.getByText("没有找到相关章节或知识点", { exact: true })).toBeVisible();
  unavailable = true;
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("知识库暂时不可用");
  await expect(page.getByRole("region", { name: "课程资料", exact: true })).toContainText("HTTP课件");
  unavailable = false;
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect(page.locator(".co-chapter")).toHaveCount(2);
});
