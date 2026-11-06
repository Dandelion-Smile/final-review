import { expect, test } from "@playwright/test";

const api = "http://127.0.0.1:8081";
test.beforeEach(async ({ request }) => { await request.post(`${api}/test/reset`); });

test("course cards open scoped modules and preserve course across reload and history", async ({ page, request }) => {
  const network = await (await request.post(`${api}/api/courses`, { data: { name: "计算机网络" } })).json();
  const math = await (await request.post(`${api}/api/courses`, { data: { name: "高等数学" } })).json();
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "课程导航" });
  await expect(nav.getByRole("button")).toHaveCount(3);
  const card = page.locator(".course-list-card").filter({ has: page.getByRole("heading", { name: "计算机网络", exact: true }) });
  await expect(card.getByRole("link", { name: "资料 0", exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/course-list-desktop.png", fullPage: true });
  await card.getByRole("button", { name: "进入课程" }).click();
  await expect(page).toHaveURL(new RegExp(`/courses/${network.course_id}/materials$`));
  await expect(page.getByRole("heading", { name: "我的复习资料" })).toBeVisible();
  await expect(nav.getByRole("button")).toHaveCount(7);
  await nav.getByRole("button", { name: "笔记", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/courses/${network.course_id}/notes$`));
  await page.reload();
  await expect(page.locator(".course-context")).toHaveText("计算机网络");
  await nav.getByRole("button", { name: "我的课程", exact: true }).click();
  await expect(nav.getByRole("button")).toHaveCount(3);
  await page.locator(".course-list-card").filter({ hasText: "高等数学" }).getByRole("button", { name: "进入课程" }).click();
  await expect(page).toHaveURL(new RegExp(`/courses/${math.course_id}/materials$`));
  await page.goBack();
  await expect(nav.getByRole("button")).toHaveCount(3);
  await page.goBack();
  await expect(page.locator(".course-context")).toHaveText("计算机网络");
  await expect(nav.getByRole("button", { name: "笔记", exact: true })).toHaveAttribute("aria-current", "page");
  await page.goForward();
  await expect(nav.getByRole("button")).toHaveCount(3);
});

test("tutor history supports rename, restore, new conversation and course isolation", async ({ page, request }) => {
  const first = await (await request.post(`${api}/api/courses`, { data: { name: "网络" } })).json();
  const second = await (await request.post(`${api}/api/courses`, { data: { name: "数学" } })).json();
  await page.goto(`/#/courses/${first.course_id}/tutor`);
  await page.getByRole("textbox", { name: "输入你的问题", exact: true }).fill("TCP 与 UDP 的区别");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.getByText("可以继续聊天", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/tutor\/chat-/);
  await page.getByRole("button", { name: "＋ 新建对话", exact: true }).click();
  await expect(page.getByRole("heading", { name: "今天想从哪里开始？" })).toBeVisible();
  await page.getByRole("button", { name: "历史对话", exact: true }).click();
  const history = page.getByRole("dialog", { name: "历史对话", exact: true });
  await history.getByRole("button", { name: "重命名TCP 与 UDP 的区别", exact: true }).click();
  await history.getByRole("textbox", { name: "对话名称" }).fill("传输层复习");
  await history.getByRole("button", { name: "保存", exact: true }).click();
  await history.getByRole("button", { name: /^传输层复习 / }).click();
  await expect(page.getByText("可以继续聊天", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "传输层复习", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("可以继续聊天", { exact: true })).toBeVisible();
  await page.goto(`/#/courses/${second.course_id}/tutor`);
  await page.getByRole("button", { name: "历史对话", exact: true }).click();
  await expect(history).toContainText("还没有对话");
  await expect(history).not.toContainText("传输层复习");
});

test("sidebar collapse persists and mobile drawer exposes the same course modules", async ({ page, request }) => {
  const course = await (await request.post(`${api}/api/courses`, { data: { name: "网络" } })).json();
  await page.goto(`/#/courses/${course.course_id}/materials`);
  await page.getByRole("button", { name: "收起导航栏", exact: true }).click();
  await expect(page.locator("#course-sidebar")).toHaveCSS("width", "64px");
  await page.reload();
  await expect(page.locator("#course-sidebar")).toHaveCSS("width", "64px");
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByRole("button", { name: "打开导航", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "打开导航", exact: true }).click();
  await expect(page.locator("#course-sidebar")).toHaveCSS("width", "224px");
  await page.getByRole("navigation", { name: "课程导航" }).getByRole("button", { name: "AI 辅导", exact: true }).click();
  await expect(page.locator("#course-sidebar")).toBeHidden();
  await expect(page.getByRole("button", { name: "历史对话", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/course-tutor-mobile.png", fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "打开导航", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "打开导航", exact: true })).toBeFocused();
});

test("unknown course routes do not fall back to another course", async ({ page, request }) => {
  await request.post(`${api}/api/courses`, { data: { name: "现有课程" } });
  await page.goto("/#/courses/missing/materials");
  await expect(page.getByRole("heading", { name: "课程暂不可用" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "课程导航" }).getByRole("button")).toHaveCount(3);
  await page.getByRole("link", { name: "返回我的课程" }).click();
  await expect(page.getByRole("heading", { name: "现有课程" })).toBeVisible();
});

test("course card management retains course and exam editing, deletion and recovery", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 新建课程", exact: true }).click();
  await page.getByRole("textbox", { name: "课程名称", exact: true }).fill("计算机网络");
  await page.getByRole("button", { name: "保存课程", exact: true }).click();
  await page.getByRole("button", { name: "管理计算机网络", exact: true }).click();
  const management = page.getByRole("region", { name: "课程详情", exact: true });
  await management.getByRole("button", { name: "编辑课程", exact: true }).click();
  await page.getByRole("textbox", { name: "课程名称", exact: true }).fill("网络原理");
  await page.getByRole("button", { name: "保存课程", exact: true }).click();
  await management.getByRole("button", { name: "＋ 添加考试", exact: true }).click();
  await page.getByRole("textbox", { name: "考试名称", exact: true }).fill("期末考试");
  await page.getByRole("button", { name: "保存考试", exact: true }).click();
  await expect(management).toContainText("期末考试");
  await management.getByRole("button", { name: "删除课程", exact: true }).click();
  await page.getByRole("button", { name: "确认删除课程", exact: true }).click();
  await expect(management).toContainText("课程处于回收期");
  await management.getByRole("button", { name: "恢复课程", exact: true }).click();
  await expect(management).toContainText("期末考试");
  await management.getByRole("button", { name: "收起管理", exact: true }).click();
  await page.locator(".course-list-card").filter({ hasText: "网络原理" }).getByRole("button", { name: "进入课程" }).click();
  await expect(page.getByRole("heading", { name: "我的复习资料" })).toBeVisible();
  await expect(page.locator(".material-current-course")).toHaveText("网络原理");
});

test("scoped note edits keep their route and cancelled navigation keeps the selected course", async ({ page, request }) => {
  const course = await (await request.post(`${api}/api/courses`, { data: { name: "网络笔记" } })).json();
  await request.post(`${api}/knowledge/ingest`, { data: { course_id: course.course_id, title: "TCP 讲义", chapter: "TCP", source_type: "teacher_ppt", markdown: "# TCP\n\n三次握手同步双方初始序列号。" } });
  const documents = await (await request.get(`${api}/api/courses/${course.course_id}/documents`)).json();
  const result = await (await request.post(`${api}/agent/invoke`, { data: { course_id: course.course_id, session_id: "scoped-note", intent: "note", message: "生成笔记", note_input: { note_type: "key_points", duration_minutes: 10, source_document_ids: [documents.items[0].document_id] } } })).json();
  expect(result.status).toBe("completed");
  const { asset_id, revision_id } = result.draft;
  await page.goto(`/#/courses/${course.course_id}/notes/${asset_id}/${revision_id}`);
  await page.getByRole("button", { name: "编辑笔记", exact: true }).click();
  await page.getByRole("textbox", { name: "笔记标题", exact: true }).fill("TCP 复习清单");
  page.once("dialog", dialog => dialog.dismiss());
  await page.getByRole("navigation", { name: "课程导航" }).getByRole("button", { name: "我的课程", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/notes/${asset_id}/${revision_id}$`));
  await expect(page.getByRole("textbox", { name: "笔记标题", exact: true })).toHaveValue("TCP 复习清单");
  await page.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(page).not.toHaveURL(new RegExp(revision_id));
  await expect(page).toHaveURL(new RegExp(`/courses/${course.course_id}/notes/${asset_id}/`));
  await page.reload();
  await expect(page.getByRole("heading", { name: "TCP 复习清单", exact: true })).toBeVisible();
  await page.locator(".note-back").click();
  await expect(page).toHaveURL(new RegExp(`/courses/${course.course_id}/notes$`));
});
