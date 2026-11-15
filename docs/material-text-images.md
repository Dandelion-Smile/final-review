# PPT/PPTX 资料笔记前三步：实现与验收记录

后续状态：第四步页面与本机启用已完成，当前使用方式见 [第四步实现与本机启用](material-note-reader-step4.md)。下文保留前三步的实现与隔离验收历史。

日期：2026-11-15。范围：[经用户确认的前三步方案](plans/pptx-text-image-notes-first-three-steps-plan-2026-11-15.md)。

## 已实现的行为

```text
新上传、显式选用 text-images-v1 的 PPT/PPTX
  → 原生提取、块级文字准入
  → 同一事务保存基础预览、原始页、初始笔记 V1、文字/图片独立任务
  ├─ V1 可靠文字 → 原有分块/向量/正式版本/定位 → 原有知识构建算法
  └─ 复杂目标页 → PDF 缓存、逐页截图、独立检查点与重试
```

- 资料笔记保存为不可变结构 JSON 版本；来源正文、备注、危险原生片段和图片节点分开。
- 服务端根据父版本判断原文、用户编辑和用户补充；客户端不能声明自己提供的是已核验原文。表格、公式、流程图节点及顺序异常文字默认排除，备注独立检查。
- 多标签保存使用 `base_revision_id`，冲突返回 409 和最新版本 ID；幂等键重试返回原保存结果，相同键不同内容返回 409。
- 仅格式、图片隐藏变化不改变语义输入哈希，不生成向量或知识任务；空笔记允许保存。
- 新策略不调用视觉模型、OCR 或旧通用转换回退。旧 `.ppt` 仍需必要的 PPT→PPTX 转换；纯文字 PPTX 不转换 PDF，不截图。
- 无可靠文字时发布零分块及 `no_reliable_text` 正常结果，跳过向量与知识构建。重复上传和手动知识构建入口也会跳过此结果。
- 文字、图片使用独立任务 ID；图片领取、失败、超时、重试和完成均不改文字状态。文字发布保留并发图片状态和笔记指针。
- 图片只更新独立页清单，不更新编辑器 JSON；失败重试仅补未完成页。检查点和发布核对当前租约、原文件、策略及目标。
- 文件、PDF 和图片缓存用完整原文件哈希校验；新增缓存采用 `.analysis/ti1` 短路径，避免 Windows 长目录叠加失败。跨进程同源转换受数据库 advisory lock 保护。
- 初始 V1 完成知识入库后，更新对应知识版本指针；核心提取、核验、去重与合并算法保留。

## 前三步交付时的使用边界（第四步已更新）

前三步交付时没有网页编辑器；第四步现已交付并在本机开放编辑保存。编辑后自动同步索引、撤销旧知识证据仍未交付。

`MATERIAL_NOTE_EDIT_ENABLED=false` 是默认值。显式开启的测试环境允许保存 V2，但语义修改返回 `pending_integration`，不自动发起模型或索引更新。首次文字任务始终读取被绑定的不可变 V1，接口分别返回最新保存版本和已索引版本。格式变化可以沿用同一份文字索引。

日常使用在完成总方案步骤 4—6 之前继续采用旧默认策略；不能将测试保存接口当作已经完成自动知识同步的编辑功能。

## 数据库与文件

新增迁移：

- `018_material_notes.sql`：`material_note_heads`、`material_note_revisions`、`material_note_save_requests`；复合归属外键、不可变版本触发器、删除级联。
- `019_material_text_image_jobs.sql`：固化策略、任务用途、目标版本/代次、源哈希、图片目标集合、内部任务去重键。

001—017 迁移保持原样。应用启动检查资料笔记表，部署这份代码前需先应用新增迁移；前三步只在隔离数据库执行，第四步已在备份后应用到本机日常库。

主要实现：

- `material_notes.py`：结构校验、初始笔记、来源归属和语义哈希。
- `material_note_store.py`：保存事务、版本仓储、任务原子交接、文字发布和图片检查点。
- `material_note_routes.py`：授权读取、保存、历史和图片接口。
- `material_text_images.py`：保守文字准入及 sections 适配。
- `material_text_images_worker.py`：V1 索引、空文字正常完成和独立截图。
- 现有 `api.py`、`postgres.py`、`material_jobs.py`、`material_structure.py`、`config.py`、`schemas.py`、`rag.py`、`source_locators.py`、`knowledge_store.py` 接入上述能力。

前三步实施时没有新增前端依赖、切换实际配置或服务；第四步后续操作见上述最新记录。两轮均未提交或推送 Git。

## 接口契约

资料前缀：`/api/courses/{course_id}/documents/{document_id}`。所有接口检查用户、课程、资料归属及删除状态，新接口仅服务新策略资料。

| 接口 | 行为 |
| --- | --- |
| `GET /reading-note` | 最新结构、保存/索引/知识版本指针及同步状态；无需正式索引 ready |
| `PUT /reading-note` | `{base_revision_id, idempotency_key, body}`；默认关闭，仅测试开启；冲突 409 |
| `GET /reading-note/revisions/{revision_id}` | 本资料授权历史版本 |
| `GET /page-images` | 独立目标页状态、错误摘要和资源地址 |
| `GET /page-images/{page_number}` | 校验源哈希及缓存戳后返回 PNG，不依赖文字 ready |
| `POST /page-images/retry` | 幂等重试失败目标，保留已完成图片，不重建文字/知识 |

JSON 的根节点是 `{type: "doc", content: [...]}`。顶层为 `sourceBlock`、`teachingNote`、`nativeFragment`、`pageImage`；文字内部可使用段落、标题、列表、代码、基础 marks。来源属性和原页绑定由服务器维护；图片不接受任意 URL。

配置定义和 `.env.example`：

```dotenv
MATERIAL_PPTX_PARSE_MODE=text_images
MATERIAL_NOTE_EDIT_ENABLED=true
```

第四步已在本机启用上述配置。上传时将策略写入资料和任务，后续改开关不改变已入队任务或历史资料。回退开关不删除已保存笔记。

## 自动验证

184 项专项与相关回归全部通过，覆盖以下文件：

```text
test_material_notes.py              test_material_notes_postgres.py
test_material_text_images.py        test_material_text_images_postgres.py
test_material_hybrid.py             test_material_hybrid_postgres.py
test_m1_jobs.py                     test_m1_materials.py
test_m1_locators.py                 test_api.py
test_material_observability.py      test_rag.py
test_pipeline_reliability.py        test_course_knowledge.py
test_postgres_migrations.py         test_course_knowledge_postgres.py
test_account_postgres.py
```

真实 SQL 测试在本次新建的 `final-review-pptx-test-20261115` 容器、端口 55439 的独立数据库运行。未对日常 5432 数据库执行新迁移；测试 fixture 的清空 schema 操作仅针对隔离的 `pptx_test` 数据库。

关键证明包括：保存幂等、并发一成功一冲突、不可变版本、跨用户隔离、删除级联；预览交接整体回滚；文字/图片任一方先完成以及实际并发提交；五文件并发一份失败隔离；纯截图空索引；旧租约写回拒绝；新连接接续检查点；部分图片重试不增加向量和知识任务；首次 V1 在图片未完成时通过原有知识算法入库。视觉/OCR/旧转换入口在新策略测试中替换为调用即失败的哨兵。

本轮修改涉及的 Python 文件 Ruff 检查通过。全仓 Ruff 另有 5 条 E501，位于未在本轮修改的 `quiz_contract.py` 和 `quiz_generation.py`。

另运行全量非集成测试：429 通过、10 失败、90 个集成项未在该命令中运行。失败集中于 PDF 导出环境和试卷生成预期，未为本阶段扩大修改：

- `test_exports.py::test_actual_formats_keep_body_math_table_and_exclude_sources`：Chromium PDF 子进程失败。
- `test_m3_quiz_contract.py::test_plan_names_files_left_out_before_generation`：coverage 为空。
- `test_m3_quiz_generation.py`：4 个语义失败预算参数用例、SDK repair roundtrip、共享 repair budget，共 6 项。
- `test_quiz_batches.py`：旧检查点错误提示、按批修复调用次数，共 2 项。

上述全量检查不记为通过，也未声称已通过复跑完整旧工作区快照确认这些失败均为基线问题。

## 真实课件对照

可复跑脚本：`eval/material_text_images.py`。脚本仅接受名称以 `pptx_acceptance` 开头的显式隔离数据库，不读取日常 `DATABASE_URL`，不删除 schema，不请求实际模型服务，也不导出 Langfuse 数据。

原始输入使用既有 `storage/material-classification-check.json` 中的 6 份课件，共 241 页。原生提取、保守准入和笔记生成全部完成：

| 原样本号 | 页数 | 可靠文字块（含备注） | 排除正文块 | 提取、分类及笔记生成秒数 |
| --- | ---: | ---: | ---: | ---: |
| 0 | 44 | 74 | 30 | 0.203 |
| 1 | 70 | 108 | 86 | 0.406 |
| 2 | 35 | 55 | 63 | 0.203 |
| 3 | 40 | 48 | 57 | 0.250 |
| 4 | 35 | 53 | 16 | 0.141 |
| 5 | 17 | 27 | 10 | 0.078 |

样本 5 还在独立的 `pptx_acceptance` 数据库跑通完整新策略：

- 原文件 SHA-256、各页目标、图片路径及状态见 [机器验收报告](../storage/pptx-ti1-acceptance/report.json)。
- 17 页中生成 13 张目标页 PNG，其余页不截图；图片逐一通过 PNG 可读检查，抽查第 5 页课程图原貌清晰，未把图片内容识别成正文。
- 初始笔记/预览处理 0.281 秒，文字索引处理 0.156 秒，图片处理 41.641 秒。
- 产生 27 个检索分块和 1 个待处理知识任务；索引使用离线确定性向量替身，因此 0.156 秒不代表真实向量服务耗时。
- 视觉、OCR、实际向量提供商、实际知识模型请求均为 0。知识提取、核验、去重及入库的完整接线由上述真实数据库加离线模型测试验证，未将其冒充为真实模型质量或 token 测试。

没有承诺提速倍数，没有在本阶段执行真实模型成本评测、全部六份资料的截图任务或网页光标验收。网页编辑光标、撤销历史及编辑后的旧证据退出，仍分别属于后续步骤 4、5—6。
