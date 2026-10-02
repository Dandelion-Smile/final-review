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
  expect(calls[1].history).toEqual([
    { role: "user", content: "第一问" }, { role: "assistant", content: "第一条回复" },
  ]);
  expect(calls[1].mode).toBe("direct");
});
