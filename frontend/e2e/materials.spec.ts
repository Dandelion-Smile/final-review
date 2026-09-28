import { expect, test } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
});

test("student uploads course material and sees a readable failure", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 新建课程" }).click();
  await page.getByRole("dialog", { name: "新建课程" }).getByRole("textbox", { name: "课程名称" }).fill("计算机网络");
  await page.getByRole("button", { name: "保存课程" }).click();
  await page.getByRole("button", { name: "我的资料" }).first().click();

  await expect(page.getByRole("heading", { name: "添加资料" })).toBeVisible();
  await page.getByLabel("来源类型").selectOption("other_practice");
  await page.getByLabel("文件").setInputFiles({ name: "review.md", mimeType: "text/markdown", buffer: Buffer.from("网络三次握手") });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.getByText("资料已接收，正在排队处理。")).toBeVisible();
  await expect(page.getByText("review.md", { exact: false }).first()).toBeVisible();
  await expect(page.locator(".material-status.ready")).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "我的资料" }).first().click();
  await expect(page.locator(".material-status.ready")).toBeVisible();

  await page.getByLabel("文件").setInputFiles({ name: "bad.png", mimeType: "image/png", buffer: Buffer.from("invalid image") });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".material-status.failed")).toBeVisible();
  await expect(page.getByText("图片损坏或格式与扩展名不符")).toBeVisible();
  await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
});

test("student edits, filters and deletes an unreferenced material", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 新建课程" }).click();
  await page.getByRole("dialog", { name: "新建课程" }).getByRole("textbox", { name: "课程名称" }).fill("计算机网络");
  await page.getByRole("button", { name: "保存课程" }).click();
  await page.getByRole("button", { name: "我的资料" }).first().click();
  await page.getByLabel("章节", { exact: true }).fill("第一章");
  await page.getByLabel("文件").setInputFiles({ name: "lecture.md", mimeType: "text/markdown", buffer: Buffer.from("网络三次握手") });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".material-status.ready")).toBeVisible();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "编辑资料信息" });
  await dialog.getByLabel("标题").fill("网络讲义");
  await dialog.getByLabel("章节").fill("第二章");
  await dialog.getByLabel("来源类型").selectOption("teacher_ppt");
  await dialog.getByRole("button", { name: "保存资料信息" }).click();
  await expect(page.getByText("网络讲义")).toBeVisible();
  await page.getByLabel("筛选章节").selectOption("第二章");
  await page.getByLabel("筛选来源").selectOption("homework");
  await expect(page.getByText("没有符合筛选条件的资料。")).toBeVisible();
  await page.getByLabel("筛选来源").selectOption("teacher_ppt");
  await expect(page.getByText("网络讲义")).toBeVisible();
  await page.getByRole("button", { name: "删除", exact: true }).click();
  const deleteDialog = page.getByRole("dialog", { name: "删除“网络讲义”？" });
  await expect(deleteDialog.getByText("正式资产")).toBeVisible();
  await deleteDialog.getByRole("button", { name: "确认删除资料" }).click();
  await expect(page.getByText("资料已删除。")).toBeVisible();
  await expect(page.getByText("暂无资料。选择文件开始上传。")).toBeVisible();
});

test("referenced material requires an explicit source snapshot choice", async ({ page, request }) => {
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "数据库" } })).json();
  await page.goto("/");
  await page.getByRole("button", { name: "我的资料" }).first().click();
  await page.getByLabel("文件").setInputFiles({ name: "source.md", mimeType: "text/markdown", buffer: Buffer.from("事务与并发控制") });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".material-status.ready")).toBeVisible();
  const documents = await (await request.get(`http://127.0.0.1:8081/api/courses/${course.course_id}/documents`)).json();
  const material = documents.items[0];
  const created = await (await request.post(`http://127.0.0.1:8081/api/courses/${course.course_id}/assets`, {
    data: { asset_type: "note", title: "重点笔记", markdown: "# 重点", source_document_ids: [material.document_id] },
  })).json();
  await request.post(`http://127.0.0.1:8081/api/assets/${created.asset.asset_id}/revisions/${created.revision.revision_id}/confirm`);
  await page.getByRole("button", { name: "删除", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: /删除“/ });
  const confirmButton = dialog.getByRole("button", { name: "确认删除资料" });
  await expect(confirmButton).toBeDisabled();
  await expect(confirmButton).toHaveCSS("cursor", "not-allowed");
  const buttonBox = await confirmButton.boundingBox();
  expect(buttonBox).not.toBeNull();
  await page.mouse.click(buttonBox!.x + buttonBox!.width / 2, buttonBox!.y + buttonBox!.height / 2);
  await expect(dialog).toBeVisible();
  const beforeChoice = await (await request.get(`http://127.0.0.1:8081/api/courses/${course.course_id}/documents`)).json();
  expect(beforeChoice.items.some((item: { document_id: string }) => item.document_id === material.document_id)).toBe(true);
  await dialog.getByRole("checkbox", { name: "我了解影响，保留来源快照后删除" }).check();
  await dialog.getByRole("button", { name: "确认删除资料" }).click();
  await expect(page.getByText("资料已删除，正式内容的来源快照已保留。")).toBeVisible();
});

test("student opens a material excerpt and returns to its link", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 新建课程" }).click();
  await page.getByRole("dialog", { name: "新建课程" }).getByRole("textbox", { name: "课程名称" }).fill("人工智能");
  await page.getByRole("button", { name: "保存课程" }).click();
  await page.getByRole("button", { name: "我的资料" }).first().click();
  await page.getByLabel("文件").setInputFiles({
    name: "lecture.md", mimeType: "text/markdown", buffer: Buffer.from("Source excerpt for review"),
  });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".material-status.ready")).toBeVisible();
  await page.getByRole("button", { name: "预览" }).click();
  const dialog = page.getByRole("dialog", { name: "资料片段预览" });
  await expect(dialog.getByText("lecture.md", { exact: false })).toBeVisible();
  await dialog.getByRole("button", { name: /文档片段/ }).click();
  await expect(dialog.locator("pre")).toHaveText("Source excerpt for review");
  const linked = page.url();
  expect(linked).toContain("#materials/");
  await page.reload();
  await expect(page.getByRole("dialog", { name: "资料片段预览" }).locator("pre")).toHaveText("Source excerpt for review");
});

test("preview panes scroll independently while actions stay visible", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 新建课程" }).click();
  await page.getByRole("dialog", { name: "新建课程" }).getByRole("textbox", { name: "课程名称" }).fill("长资料");
  await page.getByRole("button", { name: "保存课程" }).click();
  await page.getByRole("button", { name: "我的资料" }).first().click();
  const longText = Array.from({ length: 80 }, (_, index) => `第 ${index + 1} 段：${"资料片段内容。".repeat(30)}`).join("\n\n");
  await page.getByLabel("文件").setInputFiles({
    name: "long.md", mimeType: "text/markdown", buffer: Buffer.from(longText),
  });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".material-status.ready")).toBeVisible();
  await page.getByRole("button", { name: "预览" }).click();
  const dialog = page.getByRole("dialog", { name: "资料片段预览" });
  const left = dialog.locator(".material-preview-list");
  const right = dialog.locator(".material-preview-content");
  await left.getByRole("button").first().click();
  await expect(right.locator("pre")).toBeVisible();
  const footer = dialog.locator(".wb-dialog-actions");
  const before = await footer.boundingBox();
  expect(before).not.toBeNull();
  const leftBefore = await left.evaluate(element => element.scrollTop);
  const rightScrollable = await right.evaluate(element => element.scrollHeight > element.clientHeight);
  const leftScrollable = await left.evaluate(element => element.scrollHeight > element.clientHeight);
  expect(rightScrollable).toBe(true);
  expect(leftScrollable).toBe(true);
  await right.evaluate(element => { element.scrollTop = element.scrollHeight; });
  const rightAfter = await right.evaluate(element => element.scrollTop);
  expect(rightAfter).toBeGreaterThan(0);
  expect(await left.evaluate(element => element.scrollTop)).toBe(leftBefore);
  await left.evaluate(element => { element.scrollTop = element.scrollHeight; });
  expect(await left.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
  expect(await right.evaluate(element => element.scrollTop)).toBe(rightAfter);
  await expect(footer.getByRole("link", { name: "下载原文件" })).toBeVisible();
  await expect(footer.getByRole("button", { name: "关闭" })).toBeVisible();
  expect((await footer.boundingBox())?.y).toBe(before!.y);
});
