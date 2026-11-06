# 当前项目 API、实现状态与重开发复用评估

核对日期：2026-10-26。范围：本地 `final-review` 工作树，分支 `lmy_dev`，HEAD `e5c593c`，并纳入当前未提交的 V2 设计材料。结论只针对本地代码，不代表线上部署状态。

## 1. 先看结论

**已有一套可以继续复用的复习业务后端，完成了“资料 → 问答/笔记/试卷 → 作答评分 → 错题”的主要链路；主页聚合、完整学习报告、日历和通知仍需开发。新版笔记和聊天流程需要调整现有合同。**

以实际路由及业务实现为依据，当前共 **96 个接口（method＋path），91 个不同路径**。其中 GET 37、POST 53、PATCH 4、PUT 2；删除操作通过 POST 命令接口完成，没有 DELETE 路由。不含框架自动提供的 Swagger、ReDoc 和 OpenAPI 路径。

配套文件：

- [完整 API 清单](api-audit-2026-10-26/README.md)：96 个接口逐项说明用途、参数、限制和源码位置。
- [OpenAPI JSON](api-audit-2026-10-26/openapi.json)：可导入接口调试工具，包含请求 schema。
- [路由 JSON](api-audit-2026-10-26/routes.json)：方法、完整路径、处理函数、行号及参数。
- [边界验证记录](api-audit-2026-10-26/verification.json)：报告、同名课程、空白笔记和取消出卷配置的内存 HTTP 探查结果。

“已实现”表示找到实际处理逻辑和相关数据读写，不表示已满足你们新版全部需求，也不等于真实模型内容质量、线上环境和人工验收均已通过。“部分实现”表示已有可用子集或接口，但缺少完整产品语义。“未实现”表示在正式业务源码、路由和迁移中未找到对应实现；设计稿及模拟数据单独列出。

## 2. 当前代码结构

| 层 | 当前技术/入口 | 重开发时的含义 |
|---|---|---|
| 正式前端 | React＋TypeScript＋Vite＋Ant Design，`frontend/src/main.tsx` | 课程管理、资料、笔记、聊天、测验和错题已连接后端；可以逐页改造 |
| HTTP API | FastAPI，`src/final_review/api.py`，约 2,900 行 | 所有现有业务路由集中在一个应用工厂；后续可按领域拆 router |
| 业务规则 | `domain.py`、`formal_*.py`、`mistakes.py`、`quiz_*.py` | 生命周期、版本、作答、评分等逻辑可脱离旧页面复用 |
| AI/RAG | LangChain＋LangGraph，`agent.py`、`llm.py`、`rag.py`、`course_chat.py` | 有课程过滤、资料选择、引用、结构化生成、有限重试和检查点 |
| 持久化 | `postgres.py`，PostgreSQL＋pgvector | 正式应用工厂使用 PostgresStore；`storage.py` 还保留旧 SurrealStore |
| 后台任务 | `material_jobs.py`、`note_jobs.py`、`quiz_jobs.py`、`export_jobs.py` | 上传、笔记、出卷、导出分别需要 worker；只启动 API 不会自动完成这些任务 |
| 数据库迁移 | `db/migrations/001`～`011` | 已有用户、课程、资料、版本、作答、错题等表及约束 |
| V2 演示 | `docs/ui-v2/prototype.*`、`mock-data.js` | 独立静态原型，本地模拟操作，不访问正式业务 API |

当前正式前端的 `home` 是聊天页，并不是新版跨学习集总览主页。V2 原型里看得到主页、报告、日历，不代表这些能力已接通。

## 3. 各模块完成程度

| 模块 | 后端状态 | 正式前端状态 | 主要缺口/复用判断 |
|---|---|---|---|
| 账户认证 | 已实现基础认证 | 有登录、身份、退出入口；未找到注册页面调用 | 可复用会话与用户隔离；资料设置、改密、找回、验证码未实现 |
| 课程/学习集 | 已实现课程增改、列表、归档、恢复、删除预览与软删除 | 已接入工作台 | 可复用课程 ID 和归属关系；新版简介、同名校验、总览需补 |
| 考试画像 | 已实现日期、题型数量/分值、重点、排除项、偏好及归档 | 已接入工作台 | 可复用出卷配置；不等于日历考试日程 |
| 资料与知识库 | 已实现上传、转换、索引、状态、重试、预览片段、原文件下载、编辑、删除 | 已接入资料页及选择器 | 复用价值高；无跨集附件合同，无通用资料正文编辑/文件替换 API |
| AI 课程聊天 | 已实现普通交流、课程资料问答、引用、历史、重命名、范围继承、恢复 | 已接入聊天页 | 新版的跨集附件、笔记/测试知识检索、删会话、日程工具需补；生成行为要改 |
| AI 笔记 | 已实现配置、异步生成、覆盖说明、进度、失败重试、取消、草稿持久化 | 已接入聊天和笔记页 | 可复用取材和生成；旧配置与新版“详细程度”含义不同 |
| 笔记阅读/编辑 | 已实现考点 Markdown、引用、版本、差异、确认和历史 | 已接入 | 新版空白笔记、统一富文本、高亮/标记、自动保存、删除及直接可用需改造 |
| 笔记导出 | 已实现 Markdown、Word、PDF、打印；固定版本快照 | 已接入 | 可复用渲染与任务；当前只允许已确认/历史正式版本，草稿不允许导出 |
| 正式试卷 | 已实现配置澄清、异步生成、题目版本、编辑、语义复核、确认、两种打印正文 | 已接入列表、预览、编辑和确认 | 可复用整条链路；尚无题库导入、试卷专用 DOCX/PDF 导出任务 |
| 正式在线作答 | 已实现限时、答案保存、刷新恢复、交卷、评分及结果追问 | 已接入 | 可复用；正式作答必须绑定已确认版本，不能直接接受任意草稿 |
| 错题集 | 已实现正式评分生成错题、六维筛选、掌握标记、独立重练记录 | 已接入错题页 | 可复用；主观重练为自查，不自动评分/更新掌握；M4 人工验收仍待反馈 |
| 学习报告 | 部分实现：旧基础汇总接口 | `Report/RouteMap/Records` 为写死的展示数据 | 应重做指标投影与真实页面接入，不能将现有报告当已完成 |
| 每日计划/间隔复习 | 未实现产品链路 | 无真实计划页面 | 有错题、掌握事件、考试日期可作为输入；尚无计划算法、持久化及完成/忽略接口 |
| 日历/通知 | 未实现正式后端 | 仅 V2 静态演示 | 日程、重复例外、默认提醒、通知投递和聊天工具需新建 |
| 主页/课程总览 | 无专门聚合 API | 正式前端未实现新版；V2 有模拟展示 | 部分列表可组合复用；最近学习、跨集汇总、活动和公告仍需实现 |

### 3.1 账户、课程与考试

已实现：

- 邮箱密码注册/登录，Argon2 密码哈希，服务端会话与 HttpOnly Cookie。
- 认证用户绑定到业务 Store，所有权检查及数据库关系约束已有实现。
- 课程状态 `active / archived / deleted / purged`，编辑使用 `expected_updated_at` 防止覆盖旧数据。
- 删除先预览影响，再消费一次性确认令牌；课程软删除后可在回收期内恢复。
- 一个课程下维护多场考试画像，出卷可预填考试配置。

尚未实现或需改：

- `CourseCreate` 只接受 `name`；没有新版 `description`。编辑支持 `subject`，不能直接当简介使用。
- 创建和改名没有同名校验；本次探查同名创建两次均返回 200，ID 不同。
- 用户昵称、头像、修改密码、找回密码和通知偏好没有对应 API。
- 没有独立 `GET /api/courses/{course_id}`；当前详情主要从列表/其他业务上下文取得。
- 列表没有服务端分页/搜索合同；已有前端筛选可用于小规模列表，不等于全文搜索服务。
- `purge_expired_courses` 只做状态处理，不是定时物理清理方案；没有完整文件回收调度。

依据：[api.py](../src/final_review/api.py#L852)、[schemas.py](../src/final_review/schemas.py#L36)、[domain.py](../src/final_review/domain.py#L73)、[auth.py](../src/final_review/auth.py)。

### 3.2 资料与知识库

已实现：

- MD/TXT/PDF/PPT/PPTX/DOC/DOCX/PNG/JPG/JPEG/WebP 上传；默认单文件 10 MB，配置范围 1～50 MB。
- 上传保存文件并建立解析任务；前端轮询状态，失败可重试。`Idempotency-Key` 支持重放，同课程同文件名同内容可复用资料。
- 文本提取、Markdown 清洗、来源/页码定位、分块、Embedding、课程/章节限定检索及题源加权。
- PPT 可选视觉理解、块级核验、页级缓存和重建脚本；未核验通过的块排除检索。
- 列表筛选、来源片段和摘录回看、PPT 原页图片、原文件下载、元数据编辑、删除引用影响预览及历史来源快照。

边界：

- 图片 OCR/PPT 视觉理解属于“资料处理”，不能据此认为手写作答图片批改已完成。
- `PATCH documents` 只改元数据，不替换上传原文件或全文。
- 重建主要通过脚本执行；没有独立公开重建 API。没有解析任务取消入口。
- 当前资料选择在同一课程内校验；新版“引用其他学习集的指定附件”需要增加明确授权范围，不能直接删除课程隔离。
- 检索为精确向量距离排序，当前没有 HNSW 等近似索引方案。

依据：[material_conversion.py](../src/final_review/material_conversion.py)、[material_jobs.py](../src/final_review/material_jobs.py)、[slide_understanding.py](../src/final_review/slide_understanding.py)、[rag.py](../src/final_review/rag.py)、[api.py 上传](../src/final_review/api.py#L1353)。

### 3.3 聊天与 Agent

已实现：

- 模型列表、普通交流、基于当前课程资料的回答、来源引用和资料概览。
- 指定资料/章节、仅依据资料模式、读取预算和覆盖范围说明；会话保存取材范围供后续追问继承。
- 消息历史、会话列表、会话重命名、失败恢复；普通聊天发送首条有效消息时可创建会话。
- `dispatch` 识别问答、笔记、出卷与澄清；旧流程会进入配置并生成可在资产列表找回的笔记/试卷。
- 旧 Agent 有检查点恢复、考试信息中断、题答分离和测评闭环。

尚未实现或需改：

- 没有删除会话接口。
- `CourseMaterials` 读取的是 `document` 和其片段，未把已有笔记、正式测试作为统一聊天知识源。
- 没有跨学习集附件选择/检索合同。
- `ChatDecision` 只有 `note / quiz / ask / clarify`，没有日程工具和日历写入。
- 没有 SSE/WebSocket 流式输出路由；页面“正在思考”不等于流式生成。
- 新版要求“聊天生成内容留在聊天中，不自动建笔记/测试”；旧 `dispatch` 的资产生成行为需要调整。
- `/cancel-quiz` 仅清除 `pending_quiz`，不会取消排队或运行中的 `quiz_job`；本次内存请求后 job 仍为 queued。

依据：[course_chat.py](../src/final_review/course_chat.py#L89)、[ChatDecision](../src/final_review/schemas.py#L330)、[dispatch_chat](../src/final_review/api.py#L1796)、[取消配置](../src/final_review/api.py#L1333)。

### 3.4 笔记

已实现的旧产品流程：

1. 选择当前课程就绪资料，确认类型、预计阅读时间、范围和重点。
2. 进入后台任务，按文件轮流取材并分批生成、核验、合并，保留实际覆盖情况。
3. 将结果存为笔记草稿；编辑标题、考点 Markdown 和引用时创建新 revision。
4. 差异预览、确认后形成正式版本；保留不可变历史版本。
5. 从曾确认的版本导出 Markdown/DOCX/PDF/打印。

现有类型为 `chapter / key_points / qa_cards / mnemonic`；还有 `audience_level`、`duration_minutes`、`scope`、`emphasis`。这与新版“详细程度”的语义不完全一致，不能只换字段标签。新笔记请求最多选择 5 份资料，schema/旧任务兼容允许更大列表不代表新请求无限制。

手动笔记需要分清：`POST /api/courses/{course_id}/assets` 已能接收**非空** Markdown 创建通用笔记草稿；这可以作为复用底座。当前没有空白文档创建、统一富文本编辑器或自动保存闭环。本次空正文请求返回 422。

新版要求的笔记删除、独立重命名、自动保存、高亮/标记、AI 生成后直接可编辑可导出尚未完整实现。现有标题可随保存 revision 修改，但没有笔记列表上的独立改名 API。现有归档也不能替代删除。

依据：[domain.py create_asset](../src/final_review/domain.py#L255)、[笔记编辑](note-review.md)、[笔记导出](note-exports.md)、[NoteInput](../src/final_review/schemas.py#L284)、[Notes.tsx](../frontend/src/Notes.tsx)。

### 3.5 试卷、正式作答与错题

已实现：

- 正式配置校验/澄清，课程或章节或知识点范围，题型蓝图、难度、时长、来源和 AI 补充开关。
- 选择/判断/填空/简答/计算/证明六类题型；正式配置总题数最多 100。
- 后台分批出卷、失败恢复、题目/卷面版本、来源验证、逐题语义复核。
- 草稿题干、选项、答案、解析、分值、顺序编辑；复核与确认令牌。
- 确认后生成两种打印 Markdown：仅题目、题目＋答案解析；前端可打印。
- 从已确认试卷创建正式 Attempt，固定 `quiz_revision_id`；计时、逐题答案保存、版本冲突、刷新恢复、交卷。
- 客观题确定性评分；文字主观题按得分点参考评分，保存模型/rubric/反馈快照；逐题结果与追问。
- 评分后生成错题，保留原题/答案/评分/来源快照；按课程、考试、章节、知识点、题型、掌握状态筛选。
- 手动掌握状态事件及独立单题重练记录；不改写原考试成绩。

尚未实现或需改：

- 导入题库：虽然配置里有 `include_imported_questions`，设为 true 会返回能力冲突；只有 false 可用。
- 试卷专用 Word/PDF 文件导出任务没有实现；当前是 Markdown 打印内容＋前端打印，不能套用笔记导出接口。
- 手写/公式图片作答及批改没有实现。
- 错题主观重练为 `self_review`，不给新的 AI 分数，也不自动改变掌握状态。
- 间隔复习、提醒、每日计划和“报告转针对性小测/计划”未实现。
- M4 第一轮报告明确仍待用户前端手动验收；本次自动核对不替用户确认这一步。

依据：[quiz_config.py](../src/final_review/quiz_config.py)、[quiz_jobs.py](../src/final_review/quiz_jobs.py)、[quiz_print.py](../src/final_review/quiz_print.py)、[formal_attempt.py](../src/final_review/formal_attempt.py)、[formal_grading.py](../src/final_review/formal_grading.py)、[mistakes.py](../src/final_review/mistakes.py)、[M4 报告](m4-round1-development-report-2026-10-21.md)。

## 4. 名称存在但不能算完整实现的部分

### 4.1 学习报告：后端旧汇总与前端示例未打通

`GET /api/courses/{course_id}/report` 已存在，返回 `attempt_count / question_count / average_score / topics / attempts`，所以不能简单说“完全没有报告 API”。但它仍按旧 Attempt 字段读取 `score / question_count / assessment.items`。

正式 Attempt 采用 `question_revision_ids` 与 `grading_snapshot.total_score / max_score / items`。旧报告扫描同一 `attempt` 集合时，没有按新版字段投影，也没有过滤为已完成评分的正式记录。因此可能把未完成/正式 Attempt 计入次数，却返回 0 题、0 均分或空知识点，不能作为可靠学习统计。

本次在内存中创建并评分一份含 1 道题、满分 10 的正式 Attempt，报告 API 返回 200，但 `question_count=0`、`topics=[]`。这验证了题数和知识点字段缺口；该样例分数为 0，不单独证明非零成绩的平均分误差。

正式前端 `main.tsx` 的 `Report`、`RouteMap`、`Records` 使用固定数字和示例记录，没有调用报告 API。趋势图、薄弱点和最近测试因此也不能作为真实学习结果。

缺失：真实趋势、日期/考试筛选、学习时长、正确率统一口径、已观察与未检验区分、掌握度/置信度、目标达成、预计分数条件与解释、AI 报告操作卡。

### 4.2 日历：考试日期不等于日程系统

现有 `Exam.exam_at` 只表示考试画像日期。正式代码没有日历事件、系列/例外、提醒设置、通知投递的路由和迁移。

新版 P07 的以下内容均需开发：四种日程类型、全天/时间段、关联学习集规则、颜色、跨集查询、每周重复与截止日期、仅本次/全系列编辑删除、默认与单条多提醒、全天提醒时刻、站内通知/浏览器通知、主页近期日程以及聊天创建日程。

V2 原型有模拟日历和 localStorage 操作，适合复用交互参考，不能视为已有后端。

### 4.3 旧练习和正式测试是不同合同

| 路径/对象 | 实际用途 | 新开发注意 |
|---|---|---|
| `/api/quiz/generate`、`/api/quiz/{session_id}/submit` | 旧快速练习，`fast_quiz_session` | 可用于聊天内练习，但不等于可编辑的正式试卷 |
| `/agent/invoke`、`/assessment/evaluate` | 旧 Agent 会话测评与 legacy Attempt | 保留兼容或逐步适配，避免与正式作答结果混算 |
| `/quizzes/.../revisions`＋`/formal-attempts/...` | 正式资产、题目版本、作答及评分 | 新测试模块建议以这套业务能力为底座 |
| `/api/courses/{course_id}/attempts` | 旧摘要和 session_id 读取 | 列表同样扫 Attempt，正式项字段可能不完整；详情键也不同 |
| `/agent/export` | Agent 会话 Markdown | 不是笔记正式版本导出，也不是试卷文件导出 |
| `/assets/.../confirm` | 通用兼容确认 | 正式试卷和结构化笔记走各自专用复核/确认入口 |

## 5. 对照新版需求：能复用什么，还差什么

本节对照工作区 `P01-P03-P05-P07-模块需求汇总.md` 及 `docs/product-modules/p01-home-and-study-sets.md`，不将讨论稿的所有建议扩张为已批准任务。

| 新版模块/要求 | 现有可复用 | 需要新增或修改 |
|---|---|---|
| P01 学习集增改、归档、删除 | Course、状态机、确认删除、所有权 | 界面统一“学习集”；简介字段、同名校验；新增总览；列表搜索/筛选按规模决定后端支持 |
| P01 主页四区、近期活动、日程、公告 | 课程和课程内资料/笔记/试卷列表 | 跨集聚合、最近使用/继续学习、活动事件展示、公告、日程查询；当前没有对应专用 API |
| P03 生成笔记 | 资料选择、分批生成、来源验证、后台任务 | 新配置适配；直接保存可编辑的产品状态；解除旧“确认后才可导出”的门槛需同步设计 |
| P03 手动新建、统一编辑、高亮标记、自动保存 | 通用 Markdown 草稿、revision、来源快照、导出渲染 | 空文档创建、编辑内容模型、自动保存并发/冲突、独立改名/删除、手动与 AI 笔记统一合同 |
| P03 目录、正文、使用资料栏 | 现有 Markdown 阅读和引用信息 | 新阅读布局及基于标题的目录同步；新版只要求资料级展示，可保留内部来源关系 |
| P05 课程/通用答疑、历史和附件 | ChatRequest、消息、会话、范围继承、同集资料读取 | 删除会话、进入即新会话的导航适配、跨集附件范围、笔记/测试知识适配 |
| P05 聊天生成内容只保留在聊天 | 旧问答、旧练习可提供部分组件 | 调整 dispatch，不自动进入正式资产生成工作流；不能只换返回文案 |
| P05 聊天创建日程 | 已有澄清式对话结构可参考 | 新日程 schema、确定性写入服务、工具意图、缺信息追问和写入回执 |
| P07 日历与通知 | Exam 日期可辅助迁移/映射 | 事件、重复系列、例外、多提醒、通知、筛选、主页联动均需开发；考试画像与日程的关联需明确 |
| P04 测试 | 正式试卷/作答/结果/错题能力 | 最终新版测试需求未在本次汇总中单列，不据此删减确认、版本与审计；先明确对应体验 |
| P06 学习报告 | AttemptAnswer、QuestionGrade、Mistake、MasteryObservation | 新报告指标投影＋真实 UI，统一旧/新记录口径；是否含旧练习要明确 |
| P08 账户设置 | Auth 与用户身份 | 个人资料、密码与偏好接口；具体首版范围仍需产品细化 |

## 6. 重开发的复用建议

### 6.0 尚未提供的 API 能力

以下是缺口的接口化表达，**路径仅为建议命名，不是已存在接口或已承诺的最终设计**。扩展旧接口也可能满足需求，不必每项新增独立路由。

| 缺少的能力 | 可考虑的接口/合同 | 现状 |
|---|---|---|
| 主页和学习集总览 | `GET /api/home`、`GET /api/courses/{course_id}/overview` | 无专门聚合入口；基础列表可组合复用 |
| 最近活动与公告 | `GET /api/activities`、`GET /api/announcements` | 有部分事件写入，无这类用户查询 API |
| 学习集简介、同名约束 | 扩展现有课程 POST/PATCH schema 和领域校验 | 现有 name/subject 不能完整覆盖 |
| 空白笔记与自动保存 | 专用笔记创建、内容更新合同，例如 `POST /api/courses/{course_id}/notes`、`PUT /api/notes/{asset_id}/content` | 通用非空 Markdown 草稿可以复用，但不满足新版闭环 |
| 笔记改名/删除 | 专用 PATCH/DELETE 或领域命令 | 仅随 revision 修改标题、归档；无独立删除 |
| 删除聊天会话 | `DELETE /api/courses/{course_id}/conversations/{conversation_id}` | 无对应路由 |
| 跨集附件与多种知识资产 | 扩展 ChatRequest 为授权资源引用，并扩展检索服务 | 当前强制当前课程资料；未检索笔记/测试资产 |
| 日历日程 CRUD 与范围查询 | `GET/POST /api/calendar/events` 及单项修改/删除 | 无路由、正式模型和迁移 |
| 重复日程系列与单次例外 | 日程更新/删除中明确 occurrence/series 操作范围 | 无对应合同 |
| 提醒偏好、站内通知 | `GET/PUT /api/reminder-settings`、通知查询/已读合同 | 无提醒调度及通知投递链路 |
| 聊天创建日程 | dispatch 的日程意图＋统一日程服务 | 无日程意图和写入工具 |
| 学习报告、计划与复习提醒 | 重写现有 report 投影；新增计划/提醒读写 | 旧 report 仅基础汇总，计划与提醒未实现 |
| 个人资料与密码 | profile/password 更新与必要验证合同 | 只有注册、登录、退出和 me |
| 运行中出卷取消 | 例如 `POST /api/courses/{course_id}/quiz-jobs/{job_id}/cancel` | cancel-quiz 仅取消配置 |
| 试卷 Word/PDF 文件导出 | 试卷专用快照/导出合同 | 只有打印 Markdown；笔记导出不能直接代用 |

这些缺口大多不是已注册后返回 501 的空接口，而是还没有路由、领域服务或产品链路。新流程应先确定数据和状态语义，再确定最终路径。

### 6.1 优先保留的底座

- **资料管道**：文件转换、OCR/视觉核验、分块、来源定位、Job 状态与失败重试。
- **RAG 和来源关系**：owner/course 过滤、资料范围、引用摘录、MaterialVersion/SourceReference 快照。
- **生成能力**：笔记分批取材与覆盖说明；试卷合同、题量题型验证、修复和复核。
- **正式作答/评分/错题**：版本固定、答案保存、确定性判分、评分审计和历史回放。
- **身份与持久化**：Auth、PostgresStore、迁移、用户隔离和数据库不变量。
- **导出渲染**：同一内容快照生成 Markdown/DOCX/PDF/打印的转换链路。

这些能力包含大量边界处理，比旧页面布局更值得保留。新版学习集可先继续使用 `course_id`，通过适配层统一前端名称，不必立即迁移所有表和路径。

### 6.2 需要先设计再修改的部分

1. **笔记保存模型**：明确自动保存、可编辑文档与历史 revision 的关系。可去掉用户必须点击确认的步骤，但不应顺带丢弃历史、引用和并发保护。
2. **聊天输出与资产生成分开**：聊天内容、笔记文档、正式试卷各有自己的写入动作；新版聊天生成文字可以复用模型能力，但应走新的返回/持久化路径。
3. **报告统一数据口径**：优先从正式 Attempt/QuestionGrade 投影，明确旧练习是否纳入，并区分作答中、已交卷、已评分；先修数据再接图表。
4. **日历统一领域服务**：手动创建与聊天创建调用同一套校验和写入规则，主页只读取同一份日程数据。
5. **API 分模块**：逐步拆 auth/courses/materials/chat/notes/quizzes/attempts/mistakes 等 router；页面通过统一客户端调用，保留必要旧路径兼容。

### 6.3 建议的首轮协作切分

| 工作包 | 输入/输出边界 | 可独立推进条件 |
|---|---|---|
| 学习集与主页 | 学习集 DTO、聚合列表、最近活动 | 先对齐简介、唯一性和最近使用规则 |
| 笔记 | 文档模型、自动保存、生成/编辑/导出 | 先确定旧 revision 与新文档保存的关系 |
| 聊天 | 消息/附件范围、聊天内生成、日程工具 | 附件授权合同、日程服务合同确定后接入 |
| 日历 | 事件、系列/例外、提醒及通知 | 可先独立定义领域服务与 API，再接主页/聊天 |
| 测试与报告 | 复用正式作答，统一统计投影 | 先固定 Attempt/Grade 口径，避免重复实现两套成绩 |

这是一种拆分建议，并非本次已经启动的开发任务。本次未修改业务代码或数据库。

## 7. 运行与交接时容易踩的点

- 当前正式数据源是 PostgreSQL＋pgvector。`SurrealSaver` 的类名、旧 SurrealStore 和旧英文 README 不能据此推断当前生产用 SurrealDB。
- 路由前缀混用 `/api`、`/knowledge`、`/agent`、`/assessment`。Vite 当前只代理前三种；若新页面调用 `/assessment/evaluate`，需补代理或走统一适配。
- API 与四类 worker 需要读取一致的数据库/目录配置；导出还依赖 Pandoc 与 Chromium，资料转换依赖相应本地工具。
- 主 API 按当前单进程 Agent 锁设计运行；不能仅增加 uvicorn workers 就宣称已支持多副本并发。
- `/health` 只返回固定版本/状态，不证明数据库、模型、转换工具和 worker 正常。
- 有迁移、事务、审计和多项测试，但完整上线门槛仍未完成：备份恢复演练、模型成本/任务指标、全面授权回归、真实资料质量评测与最终 MVP 演练仍在 Backlog M5。
- 旧开发验收记录证明当时对应环境，不证明当前本机服务还在运行，也不证明新需求自动满足。

## 8. 文档冲突与本次验证

### 8.1 已识别的文档误导

| 文档 | 问题 | 本次处理 |
|---|---|---|
| `README.en.md` | 仍以 SurrealDB 和早期 Agent 为主，启动命令过时 | 顶部增加历史版本提示，指向中文启动指南和本报告 |
| `docs/plans/mvp-roadmap.md` | 2026-09-24 基线仍写“没有考试/笔记/正式试卷/错题” | 标明是历史差距盘点，不能作为当前完成度 |
| `docs/architecture.md` | 重复上传说明与现有去重行为冲突；数据表与生成链路不完整 | 修正上传/视觉核验说明和主要数据表，增加现状报告入口 |
| `docs/backlog.md` M3 阶段小结 | 历史段落写“M4 仍待开发”，与后文 M4 第一轮状态冲突 | 澄清后文已有第一轮实现；保留历史记录 |
| `docs/ui-v2/*` | 容易被当作已接入后端的新版应用 | 原型本身已有边界说明；本报告再次明确正式实现与演示区别 |

### 8.2 本次实际执行

- AST 枚举与 FastAPI 工厂 OpenAPI **集合完全一致：96 个 method＋path，无遗漏和重复计数**。
- 未进入生产应用 lifespan；导出 OpenAPI 没有连接数据库、运行迁移、启动 worker 或调用模型。
- 运行 14 个相关后端测试文件：**132 passed，1 条第三方 TestClient 弃用提示**。覆盖 API、领域、资料任务、笔记生成/编辑、出卷、正式作答/评分/结果、错题；不代表所有 96 个接口都逐个执行成功。
- 额外内存 HTTP 探查：正式 Attempt 在旧报告中题数/知识点遗漏；允许同名课程；空白手工笔记 422；取消配置不取消 quiz_job。
- 本次未重跑 PostgreSQL 集成、前端构建/E2E、转换工具实际文件链路或真实模型质量评测；此前验收记录作为历史证据单独引用。
- 未触碰 `.env`、真实课程文件和数据库。原有未提交的设计与备份保留。

测试命令（仓库根目录）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=D:\final_review\analysis\api-audit-pytest-20261026 tests/test_api.py tests/test_m0.py tests/test_m1_management.py tests/test_m1_jobs.py tests/test_m2_note_draft.py tests/test_m2_note_review.py tests/test_note_jobs.py tests/test_m3_quiz_config.py tests/test_m3_quiz_generation.py tests/test_m3_quiz_edit.py tests/test_m3_formal_attempt.py tests/test_m3_formal_grading.py tests/test_m3_formal_result.py tests/test_m4_mistakes.py
```

后续查实现优先读本报告、完整 API 清单、实际源码及对应专项验收；阅读历史规划时保留它的日期上下文。
