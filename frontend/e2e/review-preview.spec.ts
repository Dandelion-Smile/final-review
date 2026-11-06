import { expect, test } from "@playwright/test";

test("home resumes real notes and courses, and calendar entries persist across pages and reload", async ({ page, request }) => {
  const base = "http://127.0.0.1:8081";
  await request.post(`${base}/test/reset`);
  const course = await (await request.post(`${base}/api/courses`, { data: { name: "网络原理" } })).json();
  const note = await (await request.post(`${base}/api/courses/${course.course_id}/assets`, {
    data: { asset_type: "note", title: "传输层复习笔记", markdown: "TCP 提供可靠传输。" },
  })).json();
  await page.clock.setFixedTime(new Date(2028, 1, 29, 10));
  await page.goto("/#/home");

  const navigation = page.getByRole("navigation", { name: "课程导航" });
  await expect(navigation.getByRole("button")).toHaveText(["主页", "我的课程", "日历"]);
  await expect(page.getByRole("link", { name: "查看全部课程" })).toHaveAttribute("href", "#/courses");
  await page.getByRole("link", { name: "继续阅读" }).click();
  await expect(page).toHaveURL(new RegExp(`/notes/${note.asset.asset_id}/${note.revision.revision_id}$`));
  await page.reload();
  await expect(page.getByRole("heading", { name: "传输层复习笔记", exact: true })).toBeVisible();
  const newCourse = await (await request.post(`${base}/api/courses`, { data: { name: "另一个窗口新建的课程" } })).json();
  await navigation.getByRole("button", { name: "主页", exact: true }).click();
  await page.locator(".review-recent-courses").getByRole("link", { name: /另一个窗口新建的课程/ }).click();
  await expect(page).toHaveURL(new RegExp(`/courses/${newCourse.course_id}/materials$`));
  await expect(page.getByRole("heading", { name: "我的复习资料" })).toBeVisible();
  await navigation.getByRole("button", { name: "日历", exact: true }).click();

  const calendar = page.getByRole("region", { name: "日历", exact: true });
  const agenda = page.getByRole("region", { name: "所选日期的安排" });
  const today = calendar.locator('[aria-current="date"]');
  await expect(today).toHaveAccessibleName(/2028年2月29日.*0 项安排/);
  await expect(agenda).toContainText("这一天还没有安排。");
  await calendar.getByRole("button", { name: "下个月" }).click();
  await expect(calendar.getByRole("heading", { name: "2028年3月" })).toBeVisible();
  await calendar.getByRole("button", { name: "周", exact: true }).click();
  await expect(calendar.getByRole("button", { name: /项安排$/ })).toHaveCount(7);
  await calendar.getByRole("button", { name: "下一周" }).click();
  await expect(today).toHaveCount(0);
  await calendar.getByRole("button", { name: "今天" }).click();
  await expect(today).toHaveAttribute("aria-pressed", "true");

  const add = page.getByRole("button", { name: /添加安排/ });
  const dialog = page.getByRole("dialog", { name: "添加复习安排" });
  await add.click();
  await expect(dialog.getByLabel("日期", { exact: true })).toHaveValue("2028-02-29");
  await dialog.getByRole("combobox", { name: "类型", exact: true }).selectOption("exam");
  await expect(dialog.getByRole("button", { name: "保存安排" })).toBeDisabled();
  await dialog.getByRole("combobox", { name: "课程", exact: true }).selectOption(course.course_id);
  await dialog.getByLabel("安排标题").fill("网络复习考试");
  await dialog.getByLabel("全天", { exact: true }).uncheck();
  await dialog.getByLabel("开始时间").fill("11:00");
  await dialog.getByLabel("结束时间").fill("12:00");
  await dialog.getByRole("button", { name: "保存安排" }).click();
  await expect(agenda).toContainText("网络复习考试");
  await page.reload();
  await expect(today).toHaveAccessibleName(/2028年2月29日.*1 项安排/);
  await expect(agenda).toContainText("11:00–12:00");

  await add.click();
  await dialog.getByRole("combobox", { name: "类型", exact: true }).selectOption("todo");
  await dialog.getByLabel("安排标题").fill("整理期末资料");
  await dialog.getByLabel("日期", { exact: true }).fill("");
  await dialog.getByRole("button", { name: "保存安排" }).click();
  await expect(dialog).toBeHidden();
  await expect(agenda).not.toContainText("整理期末资料");
  await navigation.getByRole("button", { name: "主页", exact: true }).click();
  await expect(page.getByRole("heading", { name: "网络复习考试", exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: "标记完成 整理期末资料" }).click();
  await expect(page.getByRole("checkbox", { name: "撤销完成 整理期末资料" })).toBeChecked();
  await page.reload();
  await expect(page.getByText("没有待完成事项。", { exact: true })).toBeVisible();

  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "打开导航", exact: true }).click();
  await navigation.getByRole("button", { name: "日历", exact: true }).click();
  await expect(navigation).toBeHidden();
  await expect(agenda).toContainText("网络复习考试");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
