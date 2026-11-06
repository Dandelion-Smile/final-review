import { expect, test } from "@playwright/test";

test("quiz card prefills both steps and submits only after final confirmation", async ({ page, request }) => {
  const base = "http://127.0.0.1:8081";
  await request.post(`${base}/test/reset`);
  const course = await (await request.post(`${base}/api/courses`, { data: { name: "网络复习" } })).json();
  await request.post(`${base}/knowledge/ingest`, { data: {
    course_id: course.course_id, title: "TCP 讲义", chapter: "TCP", source_type: "teacher_ppt",
    markdown: "TCP 三次握手同步双方初始序列号并确认双方收发能力。",
  } });
  await page.goto(`/#chat/${course.course_id}`);
  const composer = page.getByRole("textbox", { name: "输入你的问题" });
  await composer.fill("模拟试卷是什么意思？");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.locator(".message.agent")).toContainText("可以继续聊天");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const message = "帮我测测TCP掌握得怎么样，8道选择题和2道简答，基础难度";
  await composer.fill(message);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "发送消息" }).click();
  const dialog = page.getByRole("dialog", { name: "补充试题要求" });
  await expect(dialog).toBeVisible();
  await expect(page.getByText("请在输入框上方分步确认试题要求和生成依据。")).toBeVisible();
  await expect(dialog).not.toHaveAttribute("aria-modal", "true");
  await expect(page.locator(".note-dialog-backdrop")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "历史对话" })).toBeEnabled();
  await expect(dialog.getByText("第 1 / 2 步 · 考什么")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "下一步" })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "确认并生成" })).toHaveCount(0);

  const assertComposerAlignment = async () => {
    const cardBox = (await dialog.boundingBox())!;
    const composerBox = (await page.locator(".composer").boundingBox())!;
    expect(cardBox.x).toBeCloseTo(composerBox.x, 0);
    expect(cardBox.width).toBeCloseTo(composerBox.width, 0);
    expect(cardBox.y + cardBox.height).toBeLessThan(composerBox.y);
  };
  await assertComposerAlignment();
  await page.reload();
  await expect(dialog.getByRole("textbox", { name: "章节", exact: true })).toHaveValue("TCP");

  const submissions: unknown[] = [];
  page.on("request", req => {
    if (req.url().endsWith("/api/chat/dispatch") && req.postDataJSON()?.quiz_input) {
      submissions.push(req.postDataJSON().quiz_input);
    }
  });
  const select = async (label: string, option: string) => {
    await dialog.getByRole("button", { name: label, exact: true }).click();
    await dialog.getByRole("option", { name: option, exact: true }).click();
  };
  await select("AI 补充题", "不允许");
  await select("纳入导入题", "不纳入");
  await dialog.getByRole("button", { name: "选择资料", exact: true }).click();
  const picker = page.getByRole("dialog", { name: "选择生成依据" });
  await expect(picker).toContainText("试题资料");
  await expect(picker).toContainText("最多 100 份");
  await picker.getByRole("checkbox", { name: /TCP 讲义/ }).check();
  await picker.getByRole("button", { name: /确认选择/ }).click();
  await expect(dialog.locator(".note-config-files")).toContainText("TCP 讲义");
  await page.screenshot({ path: "test-results/quiz-config-desktop.png" });
  await dialog.getByRole("button", { name: "下一步" }).click();
  await expect(dialog.getByText("第 2 / 2 步 · 怎么考")).toBeFocused();
  await expect(dialog.getByRole("spinbutton", { name: "选择题", exact: true })).toHaveValue("8");
  await expect(dialog.getByRole("spinbutton", { name: "简答题", exact: true })).toHaveValue("2");
  await expect(dialog.getByRole("button", { name: "难度", exact: true })).toContainText("基础");
  await expect(dialog.getByRole("button", { name: "确认并生成" })).toBeDisabled();
  await dialog.getByRole("button", { name: "难度", exact: true }).click();
  await expect(dialog.getByRole("listbox", { name: "难度" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog.getByRole("listbox")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await assertComposerAlignment();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: "test-results/quiz-config-mobile.png" });
  await select("计时方式", "不限时");
  await dialog.getByRole("spinbutton", { name: "选择题", exact: true }).fill("101");
  await expect(dialog.getByRole("button", { name: "确认并生成" })).toBeDisabled();
  await dialog.getByRole("spinbutton", { name: "选择题", exact: true }).fill("7");
  await dialog.getByRole("button", { name: "上一步" }).click();
  await expect(dialog.locator(".note-config-files")).toContainText("TCP 讲义");
  await dialog.getByRole("button", { name: "下一步" }).click();
  await expect(dialog.getByRole("spinbutton", { name: "选择题", exact: true })).toHaveValue("7");
  await expect(dialog.getByRole("button", { name: "计时方式", exact: true })).toContainText("不限时");
  expect(submissions).toHaveLength(0);
  await dialog.getByRole("textbox", { name: "重点 · 可选" }).fill("握手");
  await dialog.getByRole("textbox", { name: "排除内容 · 可选" }).fill("握手");
  await dialog.getByRole("button", { name: "确认并生成" }).click();
  await expect(dialog.getByRole("alert")).toContainText("冲突");
  expect(submissions).toHaveLength(1);
  expect(submissions[0]).toMatchObject({
    chapter: "TCP", duration_mode: "untimed", source_document_ids: [expect.any(String)],
    blueprint: [{ question_type: "choice", question_count: 7 }, { question_type: "short_answer", question_count: 2 }],
  });
  await dialog.getByRole("textbox", { name: "排除内容 · 可选" }).fill("");
  await dialog.getByRole("button", { name: "确认并生成" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText(/试卷配置已就绪/)).toBeVisible();
  await expect(composer).toBeFocused();
  await page.reload();
  await expect(dialog).toHaveCount(0);
  await composer.fill(message);
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.reload();
  await expect(dialog).toHaveCount(0);
});
