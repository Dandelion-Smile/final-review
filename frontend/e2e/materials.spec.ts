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
  await expect(page.getByText("可检索", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "我的资料" }).first().click();
  await expect(page.getByText("可检索", { exact: true })).toBeVisible();

  await page.getByLabel("文件").setInputFiles({ name: "bad.png", mimeType: "image/png", buffer: Buffer.from("invalid image") });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.getByText("处理失败")).toBeVisible();
  await expect(page.getByText("图片损坏或格式与扩展名不符")).toBeVisible();
  await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
});
