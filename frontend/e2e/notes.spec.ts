import { expect, test, type APIRequestContext } from "@playwright/test";

async function createNote(request: APIRequestContext) {
  const base = "http://127.0.0.1:8081";
  await request.post(`${base}/test/reset`);
  const course = await (
    await request.post(`${base}/api/courses`, { data: { name: "网络复习" } })
  ).json();
  await request.post(`${base}/knowledge/ingest`, {
    data: {
      course_id: course.course_id,
      title: "TCP 讲义",
      chapter: "TCP",
      source_type: "teacher_ppt",
      markdown:
        "# 三次握手\n\nTCP 三次握手同步双方初始序列号并确认双方收发能力。",
    },
  });
  const documents = await (
    await request.get(`${base}/api/courses/${course.course_id}/documents`)
  ).json();
  const result = await (
    await request.post(`${base}/agent/invoke`, {
      data: {
        course_id: course.course_id,
        session_id: "review-note",
        intent: "note",
        message: "生成笔记",
        note_input: {
          note_type: "key_points",
          duration_minutes: 10,
          source_document_ids: [documents.items[0].document_id],
        },
      },
    })
  ).json();
  expect(result.status).toBe("completed");
  return { course, draft: result.draft };
}

test("confirmed note exports download three formats and print the selected revision", async ({ page, request }) => {
  const { draft } = await createNote(request);
  await page.goto(`/#note/${draft.asset_id}/${draft.revision_id}`);
  await expect(page.getByText("草稿需确认后才能导出。")).toBeVisible();
  await expect(page.getByRole("region", { name: "笔记导出" })).toHaveCount(0);
  await expect(page.locator(".note-point-sources")).toHaveCount(0);
  await page.getByRole("button", { name: "查看来源与引用" }).click();
  const sourcesDialog = page.getByRole("dialog", { name: "来源与引用", exact: true });
  await expect(sourcesDialog).toContainText("TCP 讲义");
  await expect(sourcesDialog.locator("blockquote")).toContainText("TCP 三次握手");
  await page.keyboard.press("Escape");
  await expect(sourcesDialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "查看来源与引用" })).toBeFocused();
  await page.getByRole("button", { name: "确认归档", exact: true }).click();
  await page.getByRole("button", { name: "确认成为正式笔记" }).click();
  await page.getByRole("button", { name: "打印", exact: true }).click();
  const panel = page.getByRole("region", { name: "笔记导出" });
  await expect(panel).toContainText("已确认第 1 版");
  for (const [label, suffix] of [["Markdown", ".md"], ["Word", ".docx"], ["PDF", ".pdf"]]) {
    await panel.getByRole("button", { name: label, exact: true }).click();
    const row = panel.getByRole("article", { name: `${label} 文件` });
    await expect(row.getByRole("status")).toHaveText("第 1 版导出已完成", { timeout: 30_000 });
    const downloading = page.waitForEvent("download");
    await panel.getByRole("button", { name: `下载 ${label}`, exact: true }).click();
    const download = await downloading;
    expect(download.suggestedFilename()).toContain("-v1");
    expect(download.suggestedFilename()).toMatch(new RegExp(`\\${suffix}$`));
    expect(await download.failure()).toBeNull();
  }
  await expect(panel.getByRole("article")).toHaveCount(3);
  await expect(panel.getByRole("combobox")).toHaveCount(0);
  await panel.getByRole("article", { name: "PDF 文件" }).getByRole("button", { name: "查看预览" }).click();
  const preview = page.getByRole("dialog", { name: "打印预览" });
  await expect(preview).toBeVisible({ timeout: 30_000 });
  const frame = page.frameLocator('iframe[title="笔记打印内容"]');
  await expect(frame.locator("body")).toContainText("TCP");
  await expect(frame.locator("body")).not.toContainText("引用摘录");
  await expect(frame.locator("body")).not.toContainText("出处与来源标记");
  await expect(frame.locator("body")).not.toContainText(draft.revision_id);
  await page.screenshot({ path: "test-results/m2-04-print-preview.png", fullPage: true });
  await preview.getByRole("button", { name: "关闭打印预览" }).click();
  await expect(panel.getByRole("article")).toHaveCount(4);
  await panel.getByRole("button", { name: "打印预览", exact: true }).click();
  await expect(preview).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(preview).toHaveCount(0);
  await page.screenshot({ path: "test-results/m2-04-export-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel.getByRole("button", { name: "PDF", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: "test-results/m2-04-export-mobile.png", fullPage: true });
  const dialog = page.getByRole("dialog", { name: "打印与导出", exact: true });
  expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBeTruthy();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "打印", exact: true })).toBeFocused();
  await page.reload();
  await page.getByRole("button", { name: "打印", exact: true }).click();
  await expect(panel.getByRole("article")).toHaveCount(4);
  await page.getByRole("button", { name: "关闭打印与导出" }).click();
  await page.screenshot({ path: "test-results/note-detail-toolbar-mobile.png", fullPage: true });
});

test("export task restores after refresh and pending edits keep the confirmed version", async ({ page, request }) => {
  const { draft } = await createNote(request);
  const base = `http://127.0.0.1:8081/api/notes/${draft.asset_id}`;
  const confirmation = await (await request.post(`${base}/confirm-preview`, {
    data: { revision_id: draft.revision_id },
  })).json();
  await request.post(`${base}/confirm`, { data: {
    revision_id: draft.revision_id, confirmation_id: confirmation.confirmation_id,
  }});
  await page.goto(`/#note/${draft.asset_id}/${draft.revision_id}`);
  await page.getByRole("button", { name: "打印", exact: true }).click();
  const panel = page.getByRole("region", { name: "笔记导出" });
  await panel.getByRole("button", { name: "Word", exact: true }).click();
  await expect(panel.getByRole("status")).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "打印", exact: true }).click();
  await expect(panel.getByRole("button", { name: "下载 Word" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "关闭打印与导出" }).click();
  await page.getByRole("button", { name: "编辑笔记", exact: true }).click();
  await page.getByRole("textbox", { name: "笔记标题", exact: true }).fill("尚未确认的新标题");
  await page.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(page.getByText("草稿需确认后才能导出。")).toBeVisible();
  await page.getByRole("link", { name: "打开正式版本" }).click();
  await page.getByRole("button", { name: "打印", exact: true }).click();
  await expect(panel).toContainText("已确认第 1 版");
  await expect(page.getByRole("heading", { level: 1 })).not.toHaveText("尚未确认的新标题");
});

test("draft is recovered, edited with sources, confirmed and revised without replacing early", async ({
  page,
  request,
}) => {
  const { draft } = await createNote(request);
  await page.goto("/#notes");
  await expect(page.getByRole("link", { name: /TCP/ })).toBeVisible();
  await page.reload();
  await page.getByRole("link", { name: /TCP/ }).click();
  await expect(page.getByRole("region", { name: "笔记详情" })).toContainText(
    "待确认",
  );
  await page.getByRole("button", { name: "编辑笔记", exact: true }).click();
  await page
    .getByRole("textbox", { name: "笔记标题", exact: true })
    .fill("TCP 考前清单");
  await page
    .getByRole("textbox", { name: "考点 1 正文", exact: true })
    .fill("**三次握手**\n\n考试先写：同步双方初始序列号。");
  await expect(
    page.getByRole("button", { name: "确认归档", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(page).not.toHaveURL(new RegExp(draft.revision_id));
  await page.reload();
  const detail = page.getByRole("region", { name: "笔记详情" });
  await expect(detail.getByRole("heading", { level: 1 })).toHaveText(
    "TCP 考前清单",
  );
  await expect(detail.locator(".note-markdown strong")).toHaveText("三次握手");
  await detail.getByRole("button", { name: "查看来源与引用" }).click();
  const sources = page.getByRole("dialog", { name: "来源与引用", exact: true });
  await expect(sources.getByText("TCP 讲义", { exact: true })).toHaveCount(1);
  await expect(sources.locator("blockquote")).toHaveCount(1);
  await sources.getByRole("button", { name: "关闭来源与引用" }).click();
  const versions = detail.getByRole("combobox", { name: "查看版本" });
  await versions.click();
  await expect(page.getByRole("option", { name: /v1/ })).toBeVisible();
  await page.screenshot({ path: "test-results/note-version-menu.png", fullPage: true });
  await versions.press("Escape");
  await expect(versions).toBeFocused();
  await page.screenshot({
    path: "test-results/m2-03-note-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "确认归档", exact: true }).click();
  const confirm = page.getByRole("dialog", { name: "确认归档这份笔记？" });
  await expect(confirm).toContainText("TCP 讲义");
  await confirm.getByRole("button", { name: "确认成为正式笔记" }).click();
  await expect(detail.locator(".note-status")).toHaveText("已确认");
  const confirmedUrl = page.url();
  await page.getByRole("button", { name: "编辑笔记", exact: true }).click();
  await page
    .getByRole("textbox", { name: "笔记标题", exact: true })
    .fill("TCP 第二轮复习");
  await page.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(detail.getByRole("heading", { level: 1 })).toHaveText(
    "TCP 第二轮复习",
  );
  await page.locator(".note-back").click();
  await expect(
    page.getByRole("link", { name: /TCP 第二轮复习/ }),
  ).toContainText("已确认 · 有待确认修改");
  await page.getByRole("link", { name: /TCP 第二轮复习/ }).click();
  await page.getByRole("button", { name: "确认归档", exact: true }).click();
  await page
    .getByRole("dialog", { name: "确认替换正式版本？" })
    .getByRole("button", { name: "确认替换正式版本", exact: true })
    .click();
  await page.goto(confirmedUrl);
  await expect(detail.locator(".note-status")).toHaveText("历史版本");
  await expect(detail.getByRole("heading", { level: 1 })).toHaveText(
    "TCP 考前清单",
  );
  await detail.getByRole("combobox", { name: "查看版本" }).click();
  await page.getByRole("option", { name: /TCP 第二轮复习/ }).click();
  await expect(detail.getByRole("heading", { level: 1 })).toHaveText("TCP 第二轮复习");
  await page.goto(confirmedUrl);
  await detail.getByRole("button", { name: "查看来源与引用" }).click();
  await detail.getByRole("link", { name: "查看原文 →", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "整理后的资料" }),
  ).toContainText("TCP 三次握手");
});

test("reference picker, unsaved guard and mobile note list", async ({
  page,
  request,
}) => {
  const { draft } = await createNote(request);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/#note/${draft.asset_id}/${draft.revision_id}`);
  await page.getByRole("button", { name: "编辑笔记", exact: true }).click();
  await page.getByRole("button", { name: "移除此引用" }).click();
  await page.getByRole("button", { name: "添加资料引用" }).click();
  const picker = page.getByRole("dialog", { name: "选择引用片段" });
  await picker.getByRole("button", { name: /TCP 讲义/ }).click();
  await picker.getByRole("button", { name: "引用此片段" }).click();
  await expect(picker).toHaveCount(0);
  await page
    .getByRole("textbox", { name: "考点 1 引用 1 摘录" })
    .fill("TCP 三次握手");
  await expect(
    page.getByRole("textbox", { name: "考点 1 正文" }),
  ).toBeVisible();
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.locator(".note-back").click();
  await expect(page.getByRole("region", { name: "笔记详情" })).toBeVisible();
  await page.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "编辑笔记", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".note-reference blockquote")).toHaveCount(0);
  await page.screenshot({
    path: "test-results/m2-03-note-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.locator(".note-back").click();
  await page.getByRole("button", { name: "待确认", exact: true }).click();
  await expect(page.getByRole("link", { name: /TCP/ })).toBeVisible();
  await page.getByRole("button", { name: "已确认", exact: true }).click();
  await expect(page.getByText("这里会保存你的复习笔记。")).toBeVisible();
});

test("stale revision save is rejected and invalid note links are visible", async ({
  page,
  request,
}) => {
  const { draft } = await createNote(request);
  await page.goto(`/#note/${draft.asset_id}/${draft.revision_id}`);
  await page.getByRole("button", { name: "编辑笔记", exact: true }).click();
  await page
    .getByRole("textbox", { name: "笔记标题", exact: true })
    .fill("第二个标签页的修改");
  const detail = await (
    await request.get(
      `http://127.0.0.1:8081/api/assets/${draft.asset_id}/revisions/${draft.revision_id}`,
    )
  ).json();
  await request.post(
    `http://127.0.0.1:8081/api/notes/${draft.asset_id}/revisions`,
    {
      data: {
        base_revision_id: draft.revision_id,
        title: "另一个标签页先保存",
        points: detail.revision.points.map((point: any) => ({
          ...point,
          references: point.references.map((ref: any) => ({
            document_id: ref.document_id,
            chunk_id: ref.chunk_id,
            quote: ref.quote,
          })),
        })),
      },
    },
  );
  await page.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("已有更新");
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator(".note-back").click();
  await expect(
    page.getByRole("link", { name: /另一个标签页先保存/ }),
  ).toBeVisible();
  await page.goto("/#note/missing/revision");
  await expect(page.getByRole("alert")).toBeVisible();
});

test("note list refreshes when a background draft becomes available", async ({
  page,
  request,
}) => {
  const { course } = await createNote(request);
  let first = true;
  await page.route(`**/api/courses/${course.course_id}/notes`, (route) => {
    if (first) {
      first = false;
      return route.fulfill({ json: { items: [] } });
    }
    return route.continue();
  });
  await page.goto("/#notes");
  await expect(page.getByText("这里会保存你的复习笔记。")).toBeVisible();
  await expect(page.getByRole("link", { name: /TCP/ })).toBeVisible({
    timeout: 12000,
  });
});
