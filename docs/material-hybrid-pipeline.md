# PPTX 混合解析与 Langfuse（2026-11-15）

本文描述保留的旧混合策略。新增 `text-images-v1` 及第四步阅读编辑页现已启用到本机新增 PPT/PPTX，见 [第四步实现与本机启用](material-note-reader-step4.md)；旧任务和历史资料继续保持原策略。

## 已实现的链路

```text
上传 PPTX（PPT 先转换，按原文件哈希缓存）
  → preview 阶段：递归提取正文/备注/段落/坐标、结构分类
  → 原子保存基础预览与页面分类，旋转租约并交接 parse
  → native 页：保持原文，程序检查，标记 native_checked
  → visual 页：PDF 转换一次、仅渲染所需页、按需 OCR、理解和独立核验
  → 逐页保存检查点；重试复用同源、同策略、同模型的完整结果
  → 统一分块和向量化，事务发布正式版本/定位/索引并入队知识库
  → images 阶段后台补齐原页图片；知识库独立执行证据校验和两级去重
```

本次提供独立的只读基础预览；连续正文编辑器属于后续界面改造，不在本次提交范围。

## 数据与接口

- `017_material_hybrid_pipeline.sql` 增加 `material_page_results` 与任务阶段、阶段重试次数、租约令牌、指标。
- 页级缓存键：用户、资料、原文件 SHA-256、页码、策略版本。视觉结果另核对模型/提示词身份；不接受失败或待核对页作为完整缓存。
- `documents.data` 保存 `preview_status`、`preview_version`、`preview_strategy`、`preview_page_count`、预览错误与就绪时间。`analysis_pages` 保存正式质量及原页图片状态。
- `GET /api/courses/{course_id}/documents/{document_id}/basic-preview` 在正式解析未完成或失败时也可读，执行同样的用户/课程隔离。正文与教学备注分开；纯截图页保留目录和待识别提示。
- `/chunks` 和正式检索继续要求 `ready`。基础预览不创建正式分块、版本或知识任务；只有原子正式发布才入队知识库。
- `material_jobs.phase` 为 `preview`、`parse`、`images`。`phase_attempts` 是本阶段重试预算，`attempts` 是单调递增的执行栅栏，阶段交接和手动重试不回退。租约令牌只交给 worker，不经公开任务接口返回。
- 阶段交接、页结果写入、续租、失败回写、正式发布均核对执行代次/令牌/租约；已删除资料不能发布。
- 图片任务失败只影响 `images_status`，不会把已经可检索的资料改成解析失败。网页显示图片生成状态，并可重试。

## 分类与质量

快路径只接受可完整提取且顺序可靠的原生文字。支持组合对象的递归坐标、继承的项目符号、显式 `buNone`、列表层级和独立教学备注；否定、条件、代码及数值不交给模型改写。

图片、未知对象、连接线、SmartArt、表格、公式/上下标排版、旋转/越界/隐藏对象、提取异常或可疑阅读顺序均走视觉路径。只按精确哈希识别五张已逐一查看的课程母版装饰图片；未知母版图片和任何页面自身图片仍走视觉路径。这些哈希在 `material_structure.py`，不包含课件正文。后续修改分类规则或白名单须升级策略版本。

`native_checked` 只表示“原文提取已检查”；`verified` 表示视觉核验通过。知识点的证据支持性检查对两种来源都继续执行。

## 运行与回退

先同步依赖并运行数据库迁移，再重启 API、资料 worker 和知识库 worker：

```powershell
uv sync
uv run python -m final_review.migrations
uv run python -m final_review.material_jobs
uv run python -m final_review.knowledge_jobs
```

一个资料 worker 内有三个独立执行池，预览不会等待正式文件槽位：

```dotenv
MATERIAL_HYBRID_ENABLED=true
MATERIAL_PREVIEW_CONCURRENCY=2
MATERIAL_FILE_CONCURRENCY=5
MATERIAL_IMAGE_CONCURRENCY=1
```

原有转换、视觉、向量额度继续共享，未提高模型请求并发。Office 转换使用较浅的独立临时目录，避免 Windows 上传目录嵌套导致 UNO 注册路径超长；清理失败不覆盖原转换错误。

设置 `MATERIAL_HYBRID_ENABLED=false` 后，新上传恢复旧路径；已入队的混合任务继续按保存的策略完成。迁移不改写既有正式资料、引用或知识点。本机 `127.0.0.1:8080` 已完成迁移和服务重启；远端生产环境未发布本次代码。

## Langfuse 查看方法

运行依赖已包含 Langfuse SDK。项目密钥在服务 `.env` 中配置并重启 worker；不要写进前端或 Git：

```dotenv
LANGFUSE_BASE_URL=https://your-langfuse-host
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_ENVIRONMENT=development
```

本机已接入现有 `Final Review Evaluation` 项目。以后上传会自动生成追踪，无需每次手工运行测试。资料与知识库共用稳定 trace ID，Session 为 `material-{document_id}`；不同用户使用不同 trace ID。数据库任务 `metrics` 也按 `阶段:执行代次` 留存指标。

可看到：

| 观测名称 | 内容 |
| --- | --- |
| `material-worker` | 阶段、排队秒数、基础预览秒数、正式可检索秒数、请求计数与 token 汇总 |
| `material.native_extraction` / `material.classification` / `material.native_check` | 原生提取、分类数量、文字检查、缓存命中 |
| `material.pdf_conversion` / `material.render` / `material.ocr` | PDF 转换、逐页渲染、必要 OCR 的耗时与错误 |
| `resource.*.wait` | 等待转换、视觉或向量额度，和真正工作时间分开展示 |
| `vision.understand` / `vision.verify` | 逐次理解/核验请求、模型和供应商返回的输入/输出 token |
| `embedding.batch` / `embedding.provider_request` | 批次及实际供应商响应中的向量 token；向量请求不计为视觉请求 |
| `knowledge.*` / `llm.*` | 知识提取、语义证据校验、内部去重、课程匹配、发布及模型用量 |

模型用量只记录供应商返回值；无法获得时不估算。请求计数指应用 SDK 调用，SDK 内部传输重试包含在该调用耗时内。阶段耗时可包含子步骤，并发页的秒数之和不能直接当成墙钟总耗时。分类和原文提取不调用模型；分类计时与提取计时中，结构检查在提取过程中完成。

当前项目尚无 `qwen3-vl-plus` / `text-embedding-v4` 对应的计费定义，因此 token 已记录，金额为空；没有填造模型单价。配置 Langfuse Model Definitions 后可按相应计费规则显示费用。SDK/导出服务失败不阻塞业务，数据库仍保留任务阶段指标。

## 验证记录

真实三页样本取自现有 Docker 课件的第 6、13、14 页，两种模式使用相同抽样文件、相同模型和空缓存；不发布到原课程、不改变原版本。抽样包含两页原生文字和一页复杂图解。

| 指标 | 原全页视觉流程 | 混合流程 |
| --- | ---: | ---: |
| 基础预览生成（不含任务排队） | 无独立入口 | 0.062 秒 |
| 正式可检索（含真实向量请求，不含队列） | 109.39 秒 | 78.31 秒 |
| 总处理（混合侧含后台补齐原页） | 109.39 秒 | 80.56 秒 |
| 视觉 SDK 请求（含应用修复） | 8 | 2 |
| 供应商输入 token（含向量） | 30,444 | 8,772 |
| 供应商输出 token | 5,144 | 1,626 |

本次样本可检索时间减少约 28%，视觉请求减少 75%；不能据此保证整门课程相同幅度。转换仍占主要耗时。报告位于本机 `storage/material-pipeline-real-20261115.json`（私有运行数据，不提交 Git）。Langfuse 已反读确认两侧分别有 38 / 29 条观测、8 / 2 条 generation。

```powershell
uv run python eval/material_pipeline.py path/to/file.pptx --pages 6,13,14 --baseline --include-images
```

该命令调用真实模型和向量接口，报告保存到 `storage/material-pipeline-eval.json`；省略 `--baseline` 只验证新流程，省略 `--pages` 处理整份文件。不会发布资料或构建课程知识库。

另在已有专用验收账号下通过网页建立“资料解析链路验收 2026-11-15”课程并上传一页合成 PPTX：约 0.96 秒基础预览就绪、2.64 秒可检索、视觉请求为 0；后台知识库约 18.4 秒完成并产生 2 个知识点。初次原页生成暴露 Windows 长路径问题，修复后重试成功，期间预览和正式检索保持可用。资料、知识入库和图片重试均关联在同一追踪中：

[网页端完整链路与故障恢复记录](https://124.222.254.31:8443/project/final-review-eval/traces/905088d62497c27836e55e550e772b19)

独立视觉对照检查了六页真实快路径样本，未发现教学图表遗漏；发现并修复了一处继承的顶层圆点缺失，新增了继承样式与 `buNone` 测试。

测试命令：

```powershell
uv run pytest tests/test_material_observability.py tests/test_material_hybrid.py tests/test_m1_materials.py tests/test_slide_understanding.py tests/test_pipeline_reliability.py tests/test_course_knowledge.py tests/test_course_knowledge_tree.py tests/test_note_jobs.py -q
# 在单独的可丢弃数据库设置 TEST_DATABASE_URL，不能设置为业务数据库：
uv run pytest tests/test_material_hybrid_postgres.py tests/test_postgres_migrations.py tests/test_course_knowledge_postgres.py -q
cd frontend
npm run build
npm run test:e2e -- e2e/materials.spec.ts --grep "basic preview opens"
```

资料/知识库定向测试 105 通过、2 跳过；独立 PostgreSQL 验证 46 通过；新增基础预览网页测试通过，前端构建通过。覆盖分组、公式/上下标、截图、图表、表格、未知类型、阅读顺序、阶段交接、过期及错误租约、跨租户/源文件/策略缓存隔离、五个正式租约时仍可预览、失败重试、图片失败不降级正式版本。

全量非集成测试另有一轮记录为 404 通过、10 失败：9 项涉及当前工作区已有的试卷规划/修复契约，1 项是 PDF/Chromium 渲染环境。将本次 `ReviewModel.structured` 的追踪包装在独立测试进程移除后，同样的 9 项试卷测试仍失败（61 项通过）。未把这些问题算作本链路验收通过，未改动它们的业务逻辑。本仓库原有大量未提交改动，未提交、推送或覆盖这些改动。
