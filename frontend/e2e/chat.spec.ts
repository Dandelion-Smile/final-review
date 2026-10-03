import { expect, test } from "@playwright/test";

test("chat switches models and sends conversation history", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const calls: Record<string, unknown>[] = [];
  await page.route("**/api/chat/models", route => route.fulfill({
    json: { items: [{ id: "deepseek", label: "DeepSeek" }, { id: "gemini", label: "Gemini 3 Flash" }] },
  }));
  await page.route("**/api/chat", async route => {
    const body = route.request().postDataJSON();
    calls.push(body);
    await route.fulfill({ json: { reply: calls.length === 1 ? "第一条回复" : "第二条回复", model: body.model_id === "gemini" ? "Gemini 3 Flash" : "DeepSeek", citations: [] } });
  });
  await page.route("**/api/courses/*/conversations/*/messages", route => route.fulfill({ json: { items: calls.flatMap((call, index) => [
    { role: "user", content: call.message }, { role: "assistant", content: index === 0 ? "第一条回复" : "第二条回复" },
  ]) } }));

  await page.goto("/");
  await page.getByRole("button", { name: "＋ 新建课程" }).click();
  await page.getByRole("dialog", { name: "新建课程" }).getByRole("textbox", { name: "课程名称" }).fill("高等数学");
  await page.getByRole("button", { name: "保存课程" }).click();
  await page.getByRole("button", { name: "AI 对话" }).click();
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("DeepSeek");
  await page.getByRole("textbox", { name: /输入你的问题/ }).focus();
  const focusStyle = await page.getByRole("textbox", { name: /输入你的问题/ }).evaluate(element => {
    const textarea = getComputedStyle(element);
    const composer = getComputedStyle(element.parentElement!);
    return { outline: textarea.outlineStyle, shadow: composer.boxShadow, border: composer.borderColor };
  });
  expect(focusStyle).toEqual({ outline: "none", shadow: "none", border: "rgb(179, 198, 211)" });
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("第一问");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByText("第一条回复")).toBeVisible();

  await page.getByRole("button", { name: "选择聊天模型" }).click();
  await expect(page.getByRole("listbox", { name: "聊天模型" })).toBeVisible();
  await page.getByRole("option", { name: "Gemini 3 Flash" }).click();
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Gemini 3 Flash");
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("第二问");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByText("第二条回复")).toBeVisible();
  expect(calls.map(call => call.model_id)).toEqual(["deepseek", "gemini"]);
  expect(calls[1].conversation_id).toBe(calls[0].conversation_id);
  expect(calls[1].mode).toBe("direct");
});

test("note request creates an openable sourced draft", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  await page.route("**/api/chat/models", route => route.fulfill({
    json: { items: [{ id: "review", label: "Review" }] },
  }));
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 新建课程" }).click();
  await page.getByRole("dialog", { name: "新建课程" }).getByRole("textbox", { name: "课程名称" }).fill("计算机网络");
  await page.getByRole("button", { name: "保存课程" }).click();
  await expect(page.getByRole("button", { name: /当前课程 计算机网络/ })).toBeVisible();
  const courses = await (await request.get("http://127.0.0.1:8081/api/courses")).json();
  const courseId = courses.items[0].course_id as string;
  const ingested = await request.post("http://127.0.0.1:8081/knowledge/ingest", {
    data: { course_id: courseId, title: "TCP 讲义", chapter: "TCP", source_type: "teacher_ppt",
      markdown: "# 三次握手\n\nTCP 三次握手同步双方初始序列号并确认双方收发能力。" },
  });
  expect(ingested.ok()).toBeTruthy();
  await page.getByRole("button", { name: "AI 对话" }).click();
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("生成笔记");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByText("补充笔记要求")).toBeVisible();
  await expect(page.getByRole("dialog", { name: "补充笔记要求" }).getByRole("button", { name: "生成笔记" })).toBeDisabled();
  await page.reload();
  await expect(page.getByText("补充笔记要求")).toBeVisible();
  await page.getByRole("button", { name: "选择资料" }).click();
  const picker = page.getByRole("dialog", { name: "选择生成依据" });
  await expect(picker).toBeVisible();
  await expect(picker).toHaveCSS("background-color", "rgb(255, 254, 250)");
  await picker.getByRole("checkbox", { name: /选择 TCP 讲义/ }).check();
  await picker.getByRole("button", { name: "确认选择" }).click();
  await expect(page.getByText("TCP 讲义", { exact: true })).toBeVisible();
  await page.getByPlaceholder(/侧重请求头和状态码/).fill("侧重 TCP 三次握手");
  await page.getByRole("dialog", { name: "补充笔记要求" }).screenshot({ path: "test-results/note-config.png" });
  await page.getByRole("dialog", { name: "补充笔记要求" }).getByRole("button", { name: "生成笔记" }).click();
  await expect(page.getByRole("dialog", { name: "补充笔记要求" })).toHaveCount(0);
  await page.getByRole("button", { name: "我的资料" }).click();
  await page.getByRole("button", { name: "AI 对话" }).click();
  await expect(page.getByRole("link", { name: /打开笔记草稿/ })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("link", { name: /打开笔记草稿/ })).toBeVisible();
  await expect(page.getByText(/指定资料 TCP 讲义/)).toBeVisible();
  await page.getByRole("button", { name: "生成笔记" }).click();
  await page.getByRole("textbox", { name: "对话名称" }).fill("TCP 笔记记录");
  await page.getByRole("button", { name: "保存" }).click();
  await expect(page.getByRole("button", { name: "TCP 笔记记录" })).toBeVisible();
  await page.getByRole("link", { name: /打开笔记草稿/ }).click();
  const preview = page.getByRole("dialog", { name: "笔记草稿预览" });
  await expect(preview).toContainText("三次握手");
  await expect(preview).toContainText("TCP 讲义");
  await expect(preview.getByRole("link", { name: /查看 \d+ 处引用/ })).toBeVisible();
  await page.route("**/api/courses/*/material-jobs", route => route.fulfill({ status: 500, json: {} }));
  await preview.getByRole("link", { name: /查看 \d+ 处引用/ }).first().click();
  await expect(page.getByRole("dialog", { name: "资料片段预览" })).toContainText("TCP 三次握手同步双方初始序列号");
  await expect(page.getByRole("status")).toContainText("处理状态加载失败");
});

test("one source file links to every cited location", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", {
    data: { name: "Web 服务端" },
  })).json();
  const chunkIds = ["chunk-one", "chunk-two", "chunk-three"];
  const chunks = chunkIds.map((chunk_id, index) => ({
    chunk_id, locator_id: chunk_id, position_kind: "slide", position: index + 2,
    text_start: null, text_end: null, excerpt: `第 ${index + 2} 张幻灯片内容`,
  }));
  await page.route("**/api/assets/example/revisions/draft", route => route.fulfill({ json: {
    asset: { title: "HTML 笔记", course_id: course.course_id },
    revision: { markdown: "", points: [{ point_id: "point-one", heading: "HTML 本质", content: "考点内容",
      provenance: "source", references: chunkIds.map(chunk_id => ({
        document_id: "file-one", chunk_id, file_name: "HTML.pptx", source_type: "teacher_ppt",
      })) }] }, references: [],
  } }));
  await page.route("**/api/courses/*/documents/file-one/chunks", route => route.fulfill({ json: {
    document_id: "file-one", material_version_id: "v1", file_name: "HTML.pptx",
    source_type: "teacher_ppt", items: chunks,
  } }));
  await page.route("**/api/courses/*/documents/file-one/chunks/*", route => {
    const id = route.request().url().split("/").pop()!;
    const chunk = chunks.find(item => item.chunk_id === id)!;
    return route.fulfill({ json: { ...chunk, content: chunk.excerpt } });
  });
  await page.goto("/#note/example/draft");
  const note = page.getByRole("dialog", { name: "笔记草稿预览" });
  await expect(note.getByRole("link", { name: /HTML.pptx/ })).toHaveCount(1);
  await note.getByRole("link", { name: /查看 3 处引用/ }).click();
  const source = page.getByRole("dialog", { name: "资料片段预览" });
  await expect(source).toContainText("本条笔记引用 3 处位置");
  await expect(source.getByRole("button", { name: /引用 \d/ })).toHaveCount(3);
  await source.getByRole("button", { name: /引用 3 · 第 4 张幻灯片/ }).click();
  await expect(source.locator(".material-preview-content")).toContainText("第 4 张幻灯片内容");
});

test("natural note request enters the note flow", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", {
    data: { name: "Web 服务端" },
  })).json();
  await page.route("**/api/chat/models", route => route.fulfill({
    json: { items: [{ id: "review", label: "Review" }] },
  }));
  await page.goto(`/#chat/${course.course_id}`);
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("给我生成一份笔记");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("dialog", { name: "补充笔记要求" })).toBeVisible();
});

test("note material picker distinguishes duplicates and excludes failed files", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "Web 服务端" } })).json();
  await page.route("**/api/chat/models", route => route.fulfill({ json: { items: [{ id: "review", label: "Review" }] } }));
  await page.route("**/api/courses/*/documents", route => route.fulfill({ json: { items: [
    { document_id: "same-one", title: "HTTP 讲义", file_name: "HTTP 讲义.pptx", source_type: "teacher_ppt", chapter: "第一章", parse_status: "ready", uploaded_at: "2026-10-01T08:00:00Z" },
    { document_id: "same-two", title: "HTTP 讲义", file_name: "HTTP 讲义.pptx", source_type: "homework", chapter: "", parse_status: "ready", uploaded_at: "2026-10-02T08:00:00Z" },
    { document_id: "broken-three", title: "损坏课件", file_name: "损坏课件.pptx", source_type: "teacher_ppt", chapter: "", parse_status: "failed", parse_error: "文件无法解析" },
  ] } }));
  await page.goto(`/#chat/${course.course_id}`);
  await expect(page.getByRole("button", { name: /当前课程 Web 服务端/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Review");
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("生成笔记");
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.getByRole("button", { name: "选择资料" }).click();
  const picker = page.getByRole("dialog", { name: "选择生成依据" });
  await expect(picker.getByText("HTTP 讲义.pptx", { exact: true })).toHaveCount(2);
  await expect(picker.getByText(/编号 same-one/)).toBeVisible();
  await expect(picker.getByText(/编号 same-two/)).toBeVisible();
  await expect(picker.getByRole("checkbox", { name: /选择 损坏课件/ })).toBeDisabled();
  await picker.getByRole("button", { name: "选择全部可用资料" }).click();
  await expect(picker.getByRole("checkbox", { checked: true })).toHaveCount(2);
  await picker.screenshot({ path: "test-results/note-source-picker.png" });
  await picker.getByRole("button", { name: "确认选择" }).click();
  await expect(page.locator(".note-config-files span")).toHaveCount(2);
  await expect(page.locator(".note-config-files")).toContainText("same-one");
  await expect(page.locator(".note-config-files")).toContainText("same-two");
  await expect(page.getByRole("dialog", { name: "补充笔记要求" }).getByRole("button", { name: "生成笔记" })).toBeEnabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "修改资料" }).click();
  await expect(page.getByRole("dialog", { name: "选择生成依据" })).toBeVisible();
  await page.getByRole("dialog", { name: "选择生成依据" }).screenshot({ path: "test-results/note-source-picker-mobile.png" });
});

test("note request keeps the form open when chapter does not match chosen material", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "Web 服务端" } })).json();
  await request.post("http://127.0.0.1:8081/knowledge/ingest", { data: {
    course_id: course.course_id, title: "3.HTTP协议.pptx", source_type: "teacher_ppt",
    markdown: "TCP 三次握手与 HTTP 状态码",
  } });
  await page.route("**/api/chat/models", route => route.fulfill({ json: { items: [{ id: "review", label: "Review" }] } }));
  await page.goto(`/#chat/${course.course_id}`);
  await expect(page.getByRole("button", { name: /当前课程 Web 服务端/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Review");
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("生成笔记");
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.getByRole("button", { name: "选择资料" }).click();
  await page.getByRole("dialog", { name: "选择生成依据" }).getByRole("checkbox", { name: /选择 3.HTTP协议.pptx/ }).check();
  await page.getByRole("button", { name: "确认选择" }).click();
  await page.getByPlaceholder(/侧重请求头和状态码/).fill("只写第一章");
  await page.getByRole("dialog", { name: "补充笔记要求" }).getByRole("button", { name: "生成笔记" }).click();
  await expect(page.getByText(/所选资料中未找到“第一章”/).first()).toBeVisible();
  await expect(page.locator(".note-config-files")).toContainText("3.HTTP协议.pptx");
  await expect(page.getByRole("link", { name: /打开笔记草稿/ })).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".note-config-files")).toContainText("3.HTTP协议.pptx");
  await expect(page.getByPlaceholder(/侧重请求头和状态码/)).toHaveValue("只写第一章");
  await page.getByPlaceholder(/侧重请求头和状态码/).fill("侧重 HTTP 状态码");
  await page.getByRole("dialog", { name: "补充笔记要求" }).getByRole("button", { name: "生成笔记" }).click();
  await expect(page.getByRole("link", { name: /打开笔记草稿/ })).toBeVisible();
});

test("note config keeps composer fixed and cancellation restores ordinary chat", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "计算机网络" } })).json();
  await page.route("**/api/chat/models", route => route.fulfill({ json: { items: [{ id: "review", label: "Review" }] } }));
  await page.route("**/api/chat", route => route.fulfill({ json: { reply: "可以继续聊天", model: "Review", citations: [] } }));
  await page.goto(`/#chat/${course.course_id}`);
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Review");
  const composer = page.locator(".composer");
  const before = await composer.boundingBox();
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("生成笔记");
  await page.getByRole("button", { name: "发送消息" }).click();
  const dialog = page.getByRole("dialog", { name: "补充笔记要求" });
  await expect(dialog).toBeVisible();
  const during = await composer.boundingBox();
  expect(during?.y).toBeCloseTo(before!.y, 0);
  await dialog.getByRole("button", { name: "考点清单" }).click();
  const options = dialog.getByRole("listbox", { name: "笔记类型" });
  await expect(options).toBeVisible();
  await expect(options).toHaveCSS("background-color", "rgb(255, 254, 250)");
  await dialog.screenshot({ path: "test-results/note-type-menu.png" });
  await options.getByRole("option", { name: /章节笔记/ }).focus();
  await page.keyboard.press("ArrowDown");
  await expect(options.getByRole("option", { name: /考点清单/ })).toBeFocused();
  await options.getByRole("option", { name: /问答卡片/ }).click();
  await expect(dialog.getByRole("button", { name: "问答卡片" })).toBeVisible();
  await dialog.screenshot({ path: "test-results/note-config-dialog.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(dialog).toBeVisible();
  await dialog.screenshot({ path: "test-results/note-config-dialog-mobile.png" });
  const oldBackend = (route: import("@playwright/test").Route) => route.fulfill({ status: 404, json: { detail: "Not Found" } });
  await page.route("**/agent/cancel-note*", oldBackend);
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(dialog.getByText(/取消接口尚未在当前后端生效/)).toBeVisible();
  await page.unroute("**/agent/cancel-note*", oldBackend);
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText(/已取消笔记生成/)).toBeVisible();
  await page.reload();
  await expect(page.getByRole("dialog", { name: "补充笔记要求" })).toHaveCount(0);
  await expect(page.getByText(/已取消笔记生成/)).toBeVisible();
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("现在可以聊天吗");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByText("可以继续聊天", { exact: true })).toBeVisible();
});

test("course switcher restores ordinary chat and shows five recent conversations", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const first = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "数学" } })).json();
  const second = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "物理" } })).json();
  const records = Array.from({ length: 7 }, (_, index) => ({ conversation_id: `chat-${index}`, title: `数学对话 ${index}`, updated_at: new Date(2026, 0, 7 - index).toISOString() }));
  const calls: Record<string, string>[] = [];
  await page.route("**/api/chat/models", route => route.fulfill({ json: { items: [{ id: "review", label: "Review" }] } }));
  await page.route("**/api/courses/*/conversations", route => route.fulfill({ json: { items: route.request().url().includes(first.course_id) ? records : [{ conversation_id: "physics", title: "物理对话", updated_at: new Date().toISOString() }] } }));
  await page.route("**/api/courses/*/conversations/*/messages", route => route.fulfill({ json: { items: [{ role: "user", content: "旧问题" }, { role: "assistant", content: "旧回答" }, ...calls.map(call => ({ role: "user", content: call.message }))] } }));
  await page.route("**/api/chat", route => { calls.push(route.request().postDataJSON()); return route.fulfill({ json: { reply: "继续回答", model: "Review", citations: [] } }); });
  await page.goto(`/#chat/${first.course_id}/chat-0`);
  await expect(page.getByRole("button", { name: /课程管理/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /课程与考试/ })).toHaveCount(0);
  await expect(page.getByText("旧回答")).toBeVisible();
  await page.getByRole("button", { name: /当前课程 数学/ }).click();
  const panel = page.getByRole("dialog", { name: "课程与对话" });
  await expect(panel).toHaveCSS("position", "fixed");
  await expect(panel).toHaveCSS("z-index", "10000");
  expect(await panel.evaluate(element => element.parentElement === document.body)).toBeTruthy();
  await expect(panel.locator(".course-conversations button")).toHaveCount(5);
  await panel.getByRole("button", { name: "查看更多" }).click();
  await expect(panel.locator(".course-conversations button")).toHaveCount(7);
  await panel.getByRole("button", { name: /数学对话 6/ }).click();
  await expect(page).toHaveURL(new RegExp(`#chat/${first.course_id}/chat-6$`));
  await page.reload();
  await expect(page.getByText("旧回答")).toBeVisible();
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("接着说");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByText("继续回答")).toBeVisible();
  expect(calls[0].conversation_id).toBe("chat-6");
  await page.getByRole("button", { name: /当前课程 数学/ }).click();
  await panel.getByRole("button", { name: "切换课程" }).click();
  await panel.getByRole("option", { name: "物理" }).click();
  await expect(page.getByRole("button", { name: /当前课程 物理/ })).toBeVisible();
  await expect(panel.getByRole("button", { name: /物理对话/ })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: /当前课程 物理/ }).click();
  await expect(page.getByRole("dialog", { name: "课程与对话" })).toBeVisible();
});

test("conversation can be renamed from chat title and history context menu", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "高等数学" } })).json();
  const conversation = await (await request.post(`http://127.0.0.1:8081/api/courses/${course.course_id}/conversations`, { data: { title: "未命名对话" } })).json();
  await page.route("**/api/chat/models", route => route.fulfill({ json: { items: [{ id: "review", label: "Review" }] } }));
  await page.goto(`/#chat/${course.course_id}/${conversation.conversation_id}`);
  await page.getByRole("button", { name: "未命名对话" }).click();
  await page.getByRole("textbox", { name: "对话名称" }).fill("微积分复习");
  await page.getByRole("button", { name: "保存" }).click();
  await expect(page.getByRole("button", { name: "微积分复习" })).toBeVisible();
  await page.getByRole("button", { name: /当前课程 高等数学/ }).click();
  const panel = page.getByRole("dialog", { name: "课程与对话" });
  await panel.getByRole("button", { name: /微积分复习/ }).click({ button: "right" });
  await page.getByRole("menuitem", { name: "重命名对话" }).click();
  await panel.getByRole("textbox", { name: "对话名称" }).fill("期末重点");
  await panel.getByRole("button", { name: "保存" }).click();
  await expect(panel.getByRole("button", { name: /期末重点/ })).toBeVisible();
  await panel.getByRole("button", { name: "关闭切换面板" }).click();
  await expect(page.getByRole("button", { name: "期末重点" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "期末重点" })).toBeVisible();
});
