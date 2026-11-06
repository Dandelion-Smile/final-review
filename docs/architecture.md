# 架构与取舍

将规则型 Skill 工程化为可运行、可测试的单 Agent 后端。输入为课程资料、考试信息和复习需求；结果为有证据的回答、模拟题和得分反馈。当前目标规模：单进程、可信环境、小型课程语料。

当前产品能力、尚未实现项和新方案复用边界见 [代码与 API 评估](current-api-and-capability-audit-2026-10-26.md)；所有 96 个 HTTP 接口见 [完整清单](api-audit-2026-10-26/README.md)。

## Module 与 Interface

`FinalReviewAgent` 是外部主要 module，interface 为 `invoke / resume_profile / evaluate / recover / read`。调用者处理课程、会话和公开响应，不操作图节点或检查点。

内部 seam 有真实替代需求：Store（PostgreSQL / 测试内存 adapter）、模型与 Embeddings（实际提供商 / 测试 fixture）。测试从 Agent 和 HTTP interface 验证行为。

LangChain 负责模型、PromptTemplate、Tool、Retriever、结构化结果；LangGraph 负责控制流与持久状态。文件转换和数据库写入是确定性函数。检索阶段由模型决定检索词及是否继续检索，后续生成与评分使用固定结构化调用。

## 数据流

文件上传入库：API 校验并保存原文件，事务创建资料占位记录与 `material_jobs`，新任务返回 `202`。独立 worker 用 PostgreSQL 行锁领取 Job，执行 MD/TXT 读取、MarkItDown 或 LibreOffice 转换、图片 OCR、Markdown 清洗、分块与 Embedding。PPT/PPTX 保留原生文字并逐页渲染；开启 `MATERIAL_VISION_ENABLED` 时逐页整理知识块、核验原始证据，只有通过的块进入索引，OCR 作为辅助提示；关闭时使用旧提取流程并标记未视觉核验。完整资料、全部片段和 Job 成功状态在同一事务发布；失败记录阶段及原因，检索只读取 `ready` 资料。worker 中断后租约到期可重新领取，最多尝试 3 次。直接调用 `/knowledge/ingest` 仍同步入库。

直接调用 `/knowledge/ingest` 的默认文档键由课程、标题、章节、题源、清洗内容哈希构成，可以复用已入库内容。文件上传使用独立的资料 ID，并保留上传文件及状态；同课程、同文件名、同内容的重复上传复用已有有效资料，重复命中返回 `200`。幂等键重放复用原任务；相同键对应不同请求返回 `409`，失败资料须显式重试。

笔记、试卷和笔记导出另有持久任务与独立 worker。笔记生成按所选资料分批取材、核验、合并后发布草稿；专用编辑接口创建新 revision，确认令牌绑定最新内容和覆盖状态。正式试卷保存卷面与题目 revision，经过复核和确认后才能创建正式 Attempt。Attempt 固定题目版本，保存答案、交卷后评分并投影错题，错题重练单独记录。笔记导出从曾确认版本建立正文快照，worker 负责格式转换。聊天内的笔记状态通过消息历史接口的 `active_note` 读取。

检索：查询 Embedding → 数据库先过滤课程/章节 → 精确余弦排序召回 → 最低相关性阈值 → 题源 bonus → TopK。

```text
rank_score = similarity + 0.04 × priority
priority: past_exam=4, teacher_ppt=3, homework=2, other_practice=1, crash_course=1, ai_supplement=0
```

先排除不相关片段，再加权。权重是启发式，不是强制题源排序：高度相关 PPT 仍可排在弱相关真题之前。来源由上传者填写，后端不认证其真实性；`source_origin` 独立记录内容是用户上传还是直接录入，不能把自填来源当作教师认证。

当前使用精确向量计算，无 HNSW。小规模语料下易于验证元数据过滤；扩容时可替换 Store.search，先测带过滤的 ANN 召回率，再引入索引。

## PostgreSQL 表

笔记资产使用 `learning_assets`、`asset_revisions` 与 `source_references`。
生成即持久化草稿，编辑创建新版本；确认令牌绑定内容和版本指针，资产行锁防止并发
覆盖。正式版本与编辑中的草稿分别保存，历史来源保留资料版本与删除快照。
接口、状态与升级说明见 [笔记编辑与确认](note-review.md)。

| 表 | 内容 |
| --- | --- |
| app_users / auth_sessions | 邮箱、Argon2id 密码哈希；Session 哈希、过期与撤销状态 |
| courses / exams | 课程生命周期；考试日期、题型分值蓝图、重点及生成偏好 |
| documents / material_jobs | 来源、章节、原始/清洗 Markdown、文件信息、解析阶段与失败重试 |
| document_chunks | 文档 ID、文本、pgvector Embedding、课程/章节/题源元数据 |
| conversations / messages | 课程聊天、消息、取材范围与生成任务关联 |
| learning_assets / asset_revisions | 笔记/试卷资产、草稿和不可变正式版本 |
| note_jobs / quiz_jobs / export_jobs | 笔记、出卷和笔记导出任务、进度与结果 |
| quiz_revision_payloads / question_revisions | 正式试卷配置、题目、分值、答案、解析、来源及复核信息 |
| attempts / attempt_answers / question_grades | 旧/正式作答记录；正式单题答案、评分与模型审计 |
| mistakes / mastery_observations / mistake_practices | 错题快照、掌握标记事件、独立重练历史 |
| material_versions / material_locators | 资料版本快照与位置 |
| source_references / source_reference_snapshots | 资产引用及来源删除后的追溯快照 |
| confirmation_requests / audit_events / learning_events | 一次性确认、领域审计及部分学习事件 |
| knowledge_points / review_sessions | 旧 Agent 知识点计划、最近公开结果、薄弱点和 Embedding 配置记录 |
| checkpoints / pending_writes | 图状态、父检查点、pending writes、元数据 |

checkpoint 是恢复的事实来源；review_session 是业务快照，两者职责不同。检查点使用 LangGraph typed serializer，base64 后存储；包含 pending writes 和父子关系。只实现同步 Checkpointer interface。

## 状态与失败处理

- course_id + session_id 哈希作为 thread_id。每会话一个活动任务，64 个条带锁保护进程内并发。
- needs_input 等待考试信息；awaiting_answers 等待完整作答。中断节点内不做外部写入。
- 每轮题目 ID 含独立 run_id；评分必须完整匹配。重复提交同一轮相同答案返回已有评分。
- 知识点均分低于 60 加入薄弱点，达到 60 则移除；用于排序，不是经过校准的掌握度模型。
- 确定性验证引用 ID、原文、题型、题量，再让模型核对语义。最多修复 MAX_REPAIRS 次，失败返回资料不足。
- 故障保留检查点，recover 从未完成节点继续。该节点内模型调用可能重跑并产生费用。
- 等待作答时不返回参考答案、评分规则或证据正文。评分完成后才返回完整解析。
- 笔记生成必须显式提供 `source_document_ids`；后端核验用户、课程和资料状态，只从已选资料取片段。可选的 `scope` 是写作要求，明确章节无匹配时提示用户修改；多文件候选片段按相关性排序并轮流选取，避免内部资料 ID 决定来源。
- 笔记配置及排队/运行中的后台任务可取消；开始发布草稿后拒绝取消。取消标记对应任务/会话、清除对话的 `active_note`，丢弃在途生成结果并记录取消消息；新请求使用新会话 ID。第三方已发出的模型请求不会因此立即中断。

## Skill 映射

| 规则 | 代码 |
| --- | --- |
| 真题 > PPT > 作业 > 其他练习/速成课 > AI | policy.SOURCE_PRIORITY + KnowledgeBase.search |
| 转 Markdown → 清洗 → 知识点 → 出题 | upload/ingest + generate 中 plan → quiz |
| 先确认考试信息 | exam_profile + interrupt |
| 每知识点 1～6 题，遵循考试题型 | Schema + verify 确定性校验 |
| 解析服务拿分 | Question Schema + DOMAIN_POLICY + grade Prompt |
| 正文题答分离 | rendering.render_markdown |
| 跨轮沿用考试信息 | 已确认 exam_profile 存入 checkpoint |

SKILL.md 是规则源文档，运行时稳定规则显式映射到代码和测试。修改 Skill 时应同步映射。HTML 交互规范尚未实现为后端渲染器。

## 扩展前提

认证使用 HttpOnly Cookie 和服务端 Session；请求中的 user_id 不被信任。多 worker/副本需数据库锁或队列；当前不可直接横向扩容。检查点历史、旧文档版本无自动清理。大规模精确扫描与检查点历史扫描可能变慢。

模型协议测试和数据库测试不等于模型质量评测。证明/计算题还需要学科校验，不把模型复核当成数学保证。后续扩展优先由真实失败样本决定。
