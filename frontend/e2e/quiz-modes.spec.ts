import { expect, test } from "@playwright/test";

test("mixed practice survives reload, scores each answer and opens same-page mistake review", async ({ page, request }) => {
  const base = "http://127.0.0.1:8081";
  await request.post(base + "/test/reset");
  const course = await (await request.post(base + "/api/courses", { data: { name: "作答模式验收" } })).json();
  const root = `${base}/api/courses/${course.course_id}`;
  const material = await (await request.post(base + "/knowledge/ingest", { data: {
    course_id: course.course_id, title: "TCP 讲义", chapter: "TCP", source_type: "teacher_ppt",
    markdown: "TCP 三次握手同步初始序列号，并确认双方收发能力。",
  } })).json();
  const response = await request.post(root + "/quiz-jobs", { data: { quiz_input: {
    scope_mode: "chapter", chapter: "TCP", difficulty: "standard", duration_mode: "untimed",
    blueprint: ["multiple_choice", "fill_blank", "short_answer"].map(question_type => ({ question_type, question_count: 1 })),
    source_document_ids: [material.document_id], include_imported_questions: false, allow_ai_supplement: false, total_score: 30,
  } } });
  expect(response.status()).toBe(202);
  const queued = await response.json();
  const getJob = async () => (await request.get(root + "/quiz-jobs/" + queued.job_id)).json();
  await expect.poll(async () => (await getJob()).status).toBe("succeeded");
  const { asset_id: assetId, revision_id: revisionId, state } = (await getJob()).result;
  expect(state).toBe("confirmed");
  const paperUrl = `/#/courses/${course.course_id}/quizzes/papers/${assetId}/${revisionId}`;
  await page.goto(paperUrl);
  await page.getByRole("button", { name: "开始测试", exact: true }).click();
  await page.getByRole("button", { name: "开始日常练习", exact: true }).click();
  const multi = page.locator(".quiz-question-content article").filter({ has: page.getByRole("checkbox") });
  await multi.getByRole("checkbox").first().check();
  await expect(multi.getByText("已保存", { exact: true })).toBeVisible();
  await page.reload();
  await expect(multi.getByRole("checkbox").first()).toBeChecked();
  await multi.getByRole("button", { name: "提交本题" }).click();
  await expect(multi).toContainText("得分 5 / 10 · 部分得分");
  await expect(multi.getByRole("checkbox").first()).toBeDisabled();
  const fill = page.locator(".quiz-question-content article").filter({ has: page.getByLabel("第 1 空") });
  await fill.getByLabel("第 1 空").fill("ｔｃｐ");
  await fill.getByLabel("第 2 空").fill("三");
  await fill.getByRole("button", { name: "提交本题" }).click();
  await expect(fill).toContainText("得分 10 / 10 · 正确");
  const short = page.locator(".quiz-question-content article").filter({ has: page.getByLabel("文字作答") });
  await short.getByLabel("文字作答").fill("同步初始序列号");
  await short.getByRole("button", { name: "提交本题" }).click();
  await expect(page.getByText("本次得分：25 / 30")).toBeVisible();
  await multi.getByRole("link", { name: /复习这道错题/ }).click();
  await expect(page.getByRole("heading", { name: "错题复习" })).toBeVisible();
  await expect(page.getByText("参考答案：A、B")).toBeVisible();
  await page.getByRole("button", { name: "重新练习", exact: true }).click();
  const practice = page.locator(".mistake-practice");
  await practice.getByRole("checkbox").nth(0).check();
  await practice.getByRole("checkbox").nth(1).check();
  await practice.getByRole("button", { name: "提交重练答案" }).click();
  await expect(practice).toContainText("重练正确");
  await page.getByRole("button", { name: "AI 生成举一反三" }).click();
  await expect(practice).toContainText("收到 SYN-ACK");
  await practice.getByRole("checkbox").nth(2).check();
  await practice.getByRole("button", { name: "提交重练答案" }).click();
  await expect(practice).toContainText("得分 0");
  await page.reload();
  await page.getByRole("button", { name: "练习记录", exact: true }).click();
  await expect(page.getByRole("button", { name: /举一反三.*已提交/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /原题重练.*已提交/ })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();

  await page.goto(paperUrl);
  await page.getByRole("button", { name: "开始测试", exact: true }).click();
  await page.getByLabel("考试时长（分钟）").fill("2");
  await page.getByRole("button", { name: "开始模拟考试", exact: true }).click();
  await expect(page.getByRole("timer")).toContainText("剩余");
  await expect(page.getByText("参考答案：", { exact: true })).toHaveCount(0);
  await multi.getByRole("checkbox").first().check();
  await expect(multi.getByText("已保存", { exact: true })).toBeVisible();
  await page.reload();
  await expect(multi.getByRole("checkbox").first()).toBeChecked();
  await fill.getByLabel("第 1 空").fill("TCP");
  await fill.getByLabel("第 2 空").fill("四");
  await expect(fill.getByText("已保存", { exact: true })).toBeVisible();
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "交卷", exact: true }).click();
  await expect(page.getByText("本次得分：10 / 30")).toBeVisible();
  await expect(fill).toContainText("得分 5 / 10 · 部分得分");
  await expect(page.getByRole("heading", { name: /得分.*未作答/ })).toHaveCount(1);
});
