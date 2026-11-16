import { expect, test } from "@playwright/test";

const api = "http://127.0.0.1:8081";
test.beforeEach(async ({ request }) => { await request.post(`${api}/test/reset`); });

test("cards retain stage details and expose only detail and delete actions", async ({ page, request }) => {
  const course = await (await request.post(`${api}/api/courses`, { data: { name: "阶段详情验收" } })).json();
  await page.route(`**/api/courses/${course.course_id}/documents`, route => route.fulfill({
    json: { items: [{ document_id: "stage-proof", title: "正在解析的课件", file_name: "mixed.pptx",
      chapter: "第一章", source_type: "teacher_ppt", parse_status: "running", preview_status: "ready",
      parse_strategy: "text-polish-v1", note_status: "ready", polish_status: "queued" }] },
  }));
  await page.route(`**/api/courses/${course.course_id}/material-jobs`, route => route.fulfill({
    json: { items: [{ job_id: "job-stage", document_id: "stage-proof", status: "running",
      stage: "复杂页面 1/2", phase: "parse", attempts: 3 }] },
  }));
  await page.goto(`/#/courses/${course.course_id}/materials`);
  const card = page.locator(".materials-list li");
  await expect(card.getByText("mixed.pptx · 老师 PPT · 第一章", { exact: true })).toBeVisible();
  await expect(card.getByText("基础预览：已就绪")).toBeVisible();
  await expect(card.getByText("资料解析：处理中")).toBeVisible();
  await expect(card.getByText("知识库：等待资料解析完成")).toBeVisible();
  await expect(card.getByText("处理中 · 复杂页面 1/2", { exact: true })).toBeVisible();
  await expect(card.locator(".material-actions button")).toHaveText(["详情", "删除"]);
  await expect(card.getByRole("button", { name: "详情", exact: true })).toBeDisabled();
  await expect(card.locator("a")).toHaveCount(0);
});

test("single upload stays on the list until polishing finishes and detail opens the polished note", async ({ page, request }) => {
  const course = await (await request.post(`${api}/api/courses`, { data: { name: "润色详情验收" } })).json();
  const base = `/api/courses/${course.course_id}/documents/polish-proof`;
  let uploaded = false, finished = false, noteReads = 0;
  const document = () => ({ document_id: "polish-proof", title: "TCP 讲义", file_name: "tcp.md",
    source_type: "teacher_ppt", parse_strategy: "text-polish-v1", preview_status: "ready",
    parse_status: "ready", note_status: "ready", polish_status: finished ? "ready" : "queued" });
  await page.route(`**/api/courses/${course.course_id}/documents`, route => route.fulfill({
    json: { items: uploaded ? [document()] : [] },
  }));
  await page.route(`**/api/courses/${course.course_id}/material-jobs`, route => route.fulfill({ json: { items: [] } }));
  await page.route(`**/api/courses/${course.course_id}/knowledge-build-status`, route => route.fulfill({
    json: { items: [{ job_id: "knowledge-proof", document_id: "polish-proof", status: "failed", error_message: "知识库暂时不可用" }] },
  }));
  await page.route("**/knowledge/upload", route => {
    uploaded = true;
    return route.fulfill({ status: 202, json: { document_id: "polish-proof", status: "queued", parse_strategy: "text-polish-v1" } });
  });
  const noteStatus = { note_status: "ready", polish_status: "ready", latest_saved_revision_id: "polished-v2",
    revision_numbers: { "polished-v2": 2 }, index_status: "ready", images_status: "not_required" };
  await page.route(`**${base}/reading-note/status`, route => route.fulfill({ json: noteStatus }));
  await page.route(`**${base}/reading-note`, route => {
    noteReads += 1;
    return route.fulfill({ json: {
      document: document(), editable: true, pages: [{ page_number: 1, title: "TCP" }], status: noteStatus,
      initial_revision_id: "original-v1", latest_saved_revision_id: "polished-v2", indexed_revision_id: "original-v1",
      knowledge_revision_id: null, sync_status: "pending_integration",
      revision: { revision_id: "polished-v2", revision_no: 2, input_hash: "polished", created_at: "2026-11-15T10:00:00Z",
        body: { type: "doc", content: [{ type: "sourceBlock",
          attrs: { block_id: "polished-block", page: 1, content_origin: "model_polished", searchable: false, source_refs: [], exclusion_reasons: [] },
          content: [{ type: "heading", attrs: { level: 1 }, content: [{ type: "text", text: "TCP 知识整理" }] },
            { type: "paragraph", content: [{ type: "text", text: "这是直接加载的 AI 润色笔记正文。" }] }] }] } },
    } });
  });
  await page.goto(`/#/courses/${course.course_id}/materials`);
  await page.getByLabel("文件", { exact: true }).setInputFiles({ name: "tcp.md", mimeType: "text/markdown", buffer: Buffer.from("TCP 原文") });
  await page.getByRole("button", { name: "上传并处理", exact: true }).click();
  const detail = page.locator(".material-actions").getByRole("button", { name: "详情", exact: true });
  await expect(detail).toBeDisabled();
  await expect(page).toHaveURL(new RegExp(`/courses/${course.course_id}/materials$`));
  await expect(page.locator(".materials-upload-results a")).toHaveCount(0);
  await expect(page.getByText("笔记整理：AI 润色中，完成后可查看详情")).toBeVisible();
  expect(noteReads).toBe(0);
  finished = true;
  // Polish alone must keep fast polling alive when parse and knowledge jobs are inactive.
  await expect(detail).toBeEnabled({ timeout: 5000 });
  await expect(page).toHaveURL(new RegExp(`/courses/${course.course_id}/materials$`));
  await expect(page.getByText("知识库：构建失败：知识库暂时不可用")).toBeVisible();
  await detail.click();
  await expect(page).toHaveURL(new RegExp(`/materials/polish-proof/note$`));
  await expect(page.getByText("这是直接加载的 AI 润色笔记正文。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "载入润色笔记", exact: true })).toHaveCount(0);
  expect(noteReads).toBe(1);
});

test("unavailable and historical notes stay disabled while published notes do not wait for indexing", async ({ page, request }) => {
  const course = await (await request.post(`${api}/api/courses`, { data: { name: "详情边界验收" } })).json();
  const states = [
    { title: "尚未提取", note_status: "queued", parse_status: "queued" },
    { title: "正在润色", polish_status: "running" },
    { title: "润色失败", polish_status: "failed" },
    { title: "无原生文字", polish_status: "not_required" },
    { title: "待应用候选", polish_status: "candidate" },
    { title: "旧版图文笔记", parse_strategy: "text-images-v1" },
    { title: "历史资料", parse_strategy: undefined },
    { title: "缺少笔记版本", polish_status: "ready", note_status: "queued" },
    { title: "已应用润色", polish_status: "applied", parse_status: "failed" },
    { title: "已完成润色", polish_status: "ready", parse_status: "running" },
  ];
  await page.route(`**/api/courses/${course.course_id}/documents`, route => route.fulfill({ json: {
    items: states.map((state, i) => ({ document_id: `boundary-${i}`, source_type: "homework", parse_status: "ready",
      parse_strategy: "text-polish-v1", note_status: "ready", ...state })),
  } }));
  await page.goto(`/#/courses/${course.course_id}/materials`);
  for (const state of states) {
    const card = page.locator(".materials-list li").filter({ has: page.getByText(state.title, { exact: true }) });
    await expect(card.locator(".material-actions button")).toHaveText(["详情", "删除"]);
    const detail = card.getByRole("button", { name: "详情", exact: true });
    if (state.title.startsWith("已") || state.title === "待应用候选") await expect(detail).toBeEnabled();
    else await expect(detail).toBeDisabled();
  }
  await expect(page.getByText("笔记整理：无可提取文字，无法生成润色笔记")).toBeVisible();
  await expect(page.getByText("笔记整理：AI 润色失败，暂时无法查看详情")).toBeVisible();
});

test("learning note reads cleanly and rich tables remain editable and saveable", async ({ page, request }) => {
  const course = await (await request.post(`${api}/api/courses`, { data: { name: "完整学习笔记" } })).json();
  const base = `/api/courses/${course.course_id}/documents/learning-proof`;
  const text = (value: string, mark?: string) => ({ type: "text", text: value, ...(mark ? { marks: [{ type: mark }] } : {}) });
  const paragraph = (content: unknown[]) => ({ type: "paragraph", content });
  const source = (id: string, content: unknown[]) => ({ type: "sourceBlock", attrs: {
    block_id: id, page: 7, content_origin: "model_polished", searchable: false,
    exclusion_reasons: [], source_refs: ["native-original"], content_role: "source",
  }, content });
  let revision = { revision_id: "v2", revision_no: 2, input_hash: "h", created_at: "2026-11-15T00:00:00Z",
    body: { type: "doc", content: [
      source("title", [{ type: "heading", attrs: { level: 1 }, content: [text("Docker Swarm 学习笔记")] }]),
      source("chapter", [{ type: "heading", attrs: { level: 2 }, content: [text("集群与节点管理")] }]),
      source("intro", [paragraph([text("管理节点", "bold"), text("负责协调集群中的工作。")])]),
      source("table", [{ type: "table", content: [
        { type: "tableRow", content: ["操作", "命令"].map(t => ({ type: "tableHeader", content: [paragraph([text(t)])] })) },
        { type: "tableRow", content: [
          { type: "tableCell", content: [paragraph([text("初始化集群")])] },
          { type: "tableCell", content: [paragraph([text("docker swarm init", "code")])] },
        ] },
      ] }]),
    ] } };
  let savedBody: typeof revision.body | null = null;
  let candidateReady = false;
  let applyPayload: unknown = null;
  const status = () => ({ note_status: "ready", polish_status: candidateReady ? "candidate" : "ready", index_status: "ready",
    candidate_revision_id: candidateReady ? "candidate-v4" : null,
    latest_saved_revision_id: revision.revision_id, indexed_revision_id: "v1", revision_numbers: { v1: 1, v2: 2, v3: 3 } });
  await page.route(`**${base}/reading-note/status`, route => route.fulfill({ json: status() }));
  await page.route(`**${base}/reading-note/revisions/candidate-v4`, route => route.fulfill({ json: {
    ...revision, revision_id: "candidate-v4", revision_no: 4,
  } }));
  await page.route(`**${base}/reading-note/polish/candidate/apply`, route => {
    applyPayload = route.request().postDataJSON();
    return route.fulfill({ status: 409, json: { detail: "笔记或候选已更新，请重新查看后应用" } });
  });
  await page.route(`**${base}/reading-note`, async route => {
    if (route.request().method() === "PUT") {
      savedBody = route.request().postDataJSON().body;
      revision = { ...revision, revision_id: "v3", revision_no: 3, body: savedBody! };
      return route.fulfill({ json: { revision, revision_id: "v3", latest_saved_revision_id: "v3", indexed_revision_id: "v1" } });
    }
    return route.fulfill({ json: { revision, document: { document_id: "learning-proof", title: "原课件名称.pptx",
      file_name: "original.pptx", source_type: "teacher_ppt", parse_strategy: "text-polish-v1" },
      editable: true, pages: [{ page_number: 7, title: "管理集群" }], initial_revision_id: "v1",
      latest_saved_revision_id: revision.revision_id, indexed_revision_id: "v1", status: status() } });
  });
  await page.goto(`/#/courses/${course.course_id}/materials/learning-proof/note`);
  await expect(page.locator(".material-note-prose h1")).toHaveText("Docker Swarm 学习笔记");
  await expect(page.locator(".material-note-prose .material-note-source-meta")).toHaveCount(0);
  await expect(page.locator(".material-note-prose strong")).toHaveText("管理节点");
  await expect(page.getByRole("cell", { name: "docker swarm init", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "章节目录" }).getByRole("button")).toHaveText(["集群与节点管理"]);
  await expect(page.getByRole("button", { name: "查看来源", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "编辑笔记", exact: true }).click();
  const cell = page.getByRole("cell", { name: "初始化集群", exact: true });
  await cell.click();
  await page.keyboard.press("End");
  await page.keyboard.type("（首次）");
  await page.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(page.getByText("已保存 V3", { exact: true })).toBeVisible();
  expect(JSON.stringify(savedBody)).toContain("首次");
  expect(JSON.stringify(savedBody)).toContain('"tableHeader"');
  expect(JSON.stringify(savedBody)).toContain('"content_role":"source"');
  await page.reload();
  await expect(page.getByRole("cell", { name: "初始化集群（首次）", exact: true })).toBeVisible();
  candidateReady = true;
  await page.getByRole("button", { name: "查看润色版本", exact: true }).click();
  const candidateDialog = page.getByRole("dialog", { name: "润色版本（候选）" });
  await expect(candidateDialog.locator(".material-note-candidate-preview table")).toBeVisible();
  await expect(candidateDialog.locator(".material-note-source-meta")).toHaveCount(0);
  await candidateDialog.getByRole("button", { name: "应用润色版本", exact: true }).click();
  await expect(candidateDialog.getByRole("alert")).toHaveText("笔记或候选已更新，请重新查看后应用");
  expect(applyPayload).toEqual({ base_revision_id: "v3", candidate_revision_id: "candidate-v4" });
});
