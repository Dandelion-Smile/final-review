# 当前 API 完整清单（2026-10-26）

依据 `src/final_review/api.py` 的 AST 与应用工厂生成的 OpenAPI 双向核对：**96 个 method＋path，91 个不同路径**。不含 FastAPI 自带 `/docs`、`/redoc`、`/openapi.json`、`/docs/oauth2-redirect`。

这是代码现状清单；“存在接口”不等于新版需求已完成。能力缺口、复用建议和验证范围见 [项目现状报告](../current-api-and-capability-audit-2026-10-26.md)。

## 阅读与调用约定

- 全部路径为完整路径，不统一加 `/api`。`/knowledge/*`、`/agent/*`、`/assessment/*` 也存在。
- 除 `/health`、注册、登录、登出外，其余业务接口要求登录；自带文档入口默认公开。生产使用 HttpOnly Session Cookie。配置 `API_TOKEN` 时，受保护接口还需要 Bearer Token，Bearer 不能替代用户登录。
- `{...}` 是路径参数；表中 `Q` 是 query，`H` 是 header，`Body` 是 JSON 请求体模型名。上传为 multipart。
- 未声明专用返回模型的路由，OpenAPI 往往只给空响应 schema；精确业务响应应结合表中函数及现有调用方核对，不能由 OpenAPI 自动推导全部字段。
- 常见错误：401 未认证；404 不存在/无权访问；409 状态或版本冲突；422 输入或领域约束；502 模型/结构化输出失败；503 存储/配置不可用。实际错误以代码为准。
- 部分 `202` 由函数中的 JSONResponse 返回，静态 OpenAPI 不一定列出；例如资料上传/重试，应以实际 HTTP 响应为准。

## 分组数量

| 分组 | 接口数 |
|---|---:|
| 运行状态 | 1 |
| 账户认证 | 4 |
| 聊天与会话 | 9 |
| 课程与考试 | 12 |
| 通用资产兼容接口 | 4 |
| 笔记与导出 | 10 |
| 资料与知识库 | 13 |
| 旧练习与 Agent 会话 | 8 |
| Agent 与笔记生成 | 7 |
| 正式试卷 | 12 |
| 正式作答与结果 | 8 |
| 错题与重练 | 7 |
| 基础报告 | 1 |

## 运行状态

| 方法 | 完整路径 | 实现与边界 | 请求参数 | 源码 |
|---|---|---|---|---|
| GET | `/health` | 健康响应；不检测数据库、模型或 worker 就绪情况 | — | [api.py:399](../../src/final_review/api.py#L399) |

## 账户认证

| 方法 | 完整路径 | 实现与边界 | 请求参数 | 源码 |
|---|---|---|---|---|
| POST | `/api/auth/sign-up` | 邮箱密码注册并写入会话 Cookie | Body: `Credentials` | [api.py:403](../../src/final_review/api.py#L403) |
| POST | `/api/auth/sign-in` | 邮箱密码登录并写入会话 Cookie | Body: `Credentials` | [api.py:411](../../src/final_review/api.py#L411) |
| POST | `/api/auth/sign-out` | 撤销会话并清理 Cookie | — | [api.py:420](../../src/final_review/api.py#L420) |
| GET | `/api/auth/me` | 读取当前登录用户 | — | [api.py:427](../../src/final_review/api.py#L427) |

## 聊天与会话

| 方法 | 完整路径 | 实现与边界 | 请求参数 | 源码 |
|---|---|---|---|---|
| GET | `/api/chat/models` | 列出可用聊天模型及笔记模型名称 | — | [api.py:431](../../src/final_review/api.py#L431) |
| POST | `/api/chat` | 普通聊天、课程资料问答、引用及消息持久化 | Body: `ChatRequest` | [api.py:451](../../src/final_review/api.py#L451) |
| POST | `/api/chat/recover` | 恢复会话内未完成的问答 | Q: `course_id` 必填；Q: `conversation_id` 必填；Q: `model_id` 可选 | [api.py:829](../../src/final_review/api.py#L829) |
| GET | `/api/courses/{course_id}/conversations` | 读取当前课程会话列表 | — | [api.py:1246](../../src/final_review/api.py#L1246) |
| POST | `/api/courses/{course_id}/conversations` | 显式创建会话；普通聊天也可在首条消息时创建 | Body: `ConversationCreate` | [api.py:1252](../../src/final_review/api.py#L1252) |
| PATCH | `/api/courses/{course_id}/conversations/{conversation_id}` | 重命名会话 | Body: `ConversationRename` | [api.py:1274](../../src/final_review/api.py#L1274) |
| GET | `/api/courses/{course_id}/conversations/{conversation_id}/messages` | 消息历史及 active_note、pending_quiz、active_quiz；兼作笔记任务状态入口 | — | [api.py:1297](../../src/final_review/api.py#L1297) |
| POST | `/api/courses/{course_id}/conversations/{conversation_id}/cancel-quiz` | 清除 pending_quiz 配置；不取消已经排队或运行的出卷 Job | — | [api.py:1337](../../src/final_review/api.py#L1337) |
| POST | `/api/chat/dispatch` | 识别普通问答/笔记/试卷意图；旧产品流程会启动资产生成配置 | Body: `ChatRequest`；H: `idempotency-key` 可选 | [api.py:1797](../../src/final_review/api.py#L1797) |

## 课程与考试

| 方法 | 完整路径 | 实现与边界 | 请求参数 | 源码 |
|---|---|---|---|---|
| GET | `/api/courses` | 当前用户课程列表，含可恢复的删除项，不含 purged | — | [api.py:853](../../src/final_review/api.py#L853) |
| POST | `/api/courses` | 创建课程；只接收名称，不做同名检查 | Body: `CourseCreate` | [api.py:864](../../src/final_review/api.py#L864) |
| PATCH | `/api/courses/{course_id}` | 修改名称、学科；检查 expected_updated_at | Body: `CourseUpdate` | [api.py:880](../../src/final_review/api.py#L880) |
| POST | `/api/courses/{course_id}/archive` | 归档课程 | — | [api.py:890](../../src/final_review/api.py#L890) |
| POST | `/api/courses/{course_id}/restore` | 恢复归档课程或回收期内删除的课程 | — | [api.py:894](../../src/final_review/api.py#L894) |
| POST | `/api/courses/{course_id}/deletion-preview` | 读取关联资源影响并签发一次性删除确认令牌 | — | [api.py:898](../../src/final_review/api.py#L898) |
| POST | `/api/courses/{course_id}/delete` | 消费确认令牌并软删除课程 | Body: `ConfirmationConsume` | [api.py:902](../../src/final_review/api.py#L902) |
| GET | `/api/courses/{course_id}/exams` | 课程考试画像列表 | — | [api.py:908](../../src/final_review/api.py#L908) |
| POST | `/api/courses/{course_id}/exams` | 创建考试日期、题型分值蓝图、重点及偏好 | Body: `ExamCreate` | [api.py:912](../../src/final_review/api.py#L912) |
| GET | `/api/exams/{exam_id}` | 读取考试画像 | — | [api.py:918](../../src/final_review/api.py#L918) |
| PATCH | `/api/exams/{exam_id}` | 修改考试画像；检查 expected_updated_at | Body: `ExamUpdate` | [api.py:922](../../src/final_review/api.py#L922) |
| POST | `/api/exams/{exam_id}/archive` | 归档考试画像；没有独立恢复或删除端点 | — | [api.py:932](../../src/final_review/api.py#L932) |

## 通用资产兼容接口

| 方法 | 完整路径 | 实现与边界 | 请求参数 | 源码 |
|---|---|---|---|---|
| POST | `/api/courses/{course_id}/assets` | 手工提交非空 Markdown 创建 note/quiz 草稿；不等于正式试卷或空白笔记编辑器 | Body: `AssetCreate` | [api.py:936](../../src/final_review/api.py#L936) |
| POST | `/api/assets/{asset_id}/revisions` | 通用 Markdown 草稿创建新版本；拒绝替代专用笔记/试卷编辑合同 | Body: `AssetRevisionCreate` | [api.py:1055](../../src/final_review/api.py#L1055) |
| POST | `/api/assets/{asset_id}/revisions/{revision_id}/confirm` | 确认旧通用资产；不能绕过正式笔记/试卷的专用复核流程 | — | [api.py:1063](../../src/final_review/api.py#L1063) |
| POST | `/api/assets/{asset_id}/archive` | 归档已确认资产；没有笔记删除或恢复接口 | — | [api.py:1069](../../src/final_review/api.py#L1069) |

## 笔记与导出

| 方法 | 完整路径 | 实现与边界 | 请求参数 | 源码 |
|---|---|---|---|---|
| GET | `/api/assets/{asset_id}/revisions/{revision_id}` | 读取指定笔记版本及来源、版本相关信息 | — | [api.py:942](../../src/final_review/api.py#L942) |
| GET | `/api/courses/{course_id}/notes` | 列出课程笔记及版本信息 | — | [api.py:948](../../src/final_review/api.py#L948) |
| POST | `/api/notes/{asset_id}/exports` | 从已确认的笔记版本创建或复用导出任务 | Body: `NoteExportCreate` | [api.py:952](../../src/final_review/api.py#L952) |
| GET | `/api/exports/{export_id}` | 查询导出任务状态及完成后的下载信息 | — | [api.py:976](../../src/final_review/api.py#L976) |
| POST | `/api/exports/{export_id}/retry` | 重试失败导出任务，复用原内容快照 | — | [api.py:981](../../src/final_review/api.py#L981) |
| GET | `/api/exports/{export_id}/download` | 授权下载已完成的导出文件 | — | [api.py:1029](../../src/final_review/api.py#L1029) |
| GET | `/api/exports/{export_id}/preview` | 读取已完成 print 格式任务的 HTML 预览 | — | [api.py:1033](../../src/final_review/api.py#L1033) |
| POST | `/api/notes/{asset_id}/revisions` | 保存标题、考点正文和引用为新草稿版本；显式保存 | Body: `NoteRevisionEdit` | [api.py:1037](../../src/final_review/api.py#L1037) |
| POST | `/api/notes/{asset_id}/confirm-preview` | 笔记差异/覆盖确认预览与一次性确认令牌 | Body: `NoteConfirmPreview` | [api.py:1043](../../src/final_review/api.py#L1043) |
| POST | `/api/notes/{asset_id}/confirm` | 消费令牌，将笔记草稿确认为正式版本 | Body: `NoteConfirm` | [api.py:1049](../../src/final_review/api.py#L1049) |

## 资料与知识库

| 方法 | 完整路径 | 实现与边界 | 请求参数 | 源码 |
|---|---|---|---|---|
| GET | `/api/courses/{course_id}/documents` | 按章节、题源及处理状态筛选当前课程资料 | Q: `chapter` 可选；Q: `source_type` 可选；Q: `status` 可选 | [api.py:1073](../../src/final_review/api.py#L1073) |
| GET | `/api/courses/{course_id}/documents/{document_id}/chunks` | 列出已就绪资料的片段，可选择包含正文 | Q: `include_content` 可选 | [api.py:1135](../../src/final_review/api.py#L1135) |
| GET | `/api/courses/{course_id}/documents/{document_id}/pages/{page_number}` | 读取 PPT 转换产生的指定原页图片；非任意格式原文阅读器 | — | [api.py:1157](../../src/final_review/api.py#L1157) |
| GET | `/api/courses/{course_id}/documents/{document_id}/chunks/{chunk_id}` | 读取授权来源片段与位置/来源信息 | — | [api.py:1181](../../src/final_review/api.py#L1181) |
| GET | `/api/courses/{course_id}/documents/{document_id}/download` | 下载上传的原文件；直接录入资料没有上传原文件 | — | [api.py:1217](../../src/final_review/api.py#L1217) |
| PATCH | `/api/courses/{course_id}/documents/{document_id}` | 修改资料标题、章节和来源类型；不替换文件正文 | Body: `MaterialUpdate` | [api.py:1236](../../src/final_review/api.py#L1236) |
| POST | `/knowledge/ingest` | 同步录入 Markdown、清洗分块、Embedding 并写入知识库 | Body: `MaterialInput` | [api.py:1349](../../src/final_review/api.py#L1349) |
| POST | `/knowledge/upload` | multipart 上传并创建后台解析任务；支持幂等和同名同内容复用 | multipart: `Body_upload_knowledge_upload_post` | [api.py:1354](../../src/final_review/api.py#L1354) |
| GET | `/api/courses/{course_id}/material-jobs` | 列出课程资料解析任务 | — | [api.py:1484](../../src/final_review/api.py#L1484) |
| GET | `/api/courses/{course_id}/material-jobs/{job_id}` | 读取单个解析任务与阶段/错误 | — | [api.py:1489](../../src/final_review/api.py#L1489) |
| POST | `/api/courses/{course_id}/material-jobs/{job_id}/retry` | 重新排队失败的资料解析任务 | — | [api.py:1501](../../src/final_review/api.py#L1501) |
| POST | `/api/courses/{course_id}/documents/{document_id}/deletion-preview` | 读取资料引用影响并签发删除确认令牌 | — | [api.py:1703](../../src/final_review/api.py#L1703) |
| POST | `/api/courses/{course_id}/documents/{document_id}/delete` | 删除资料；block 或保留来源快照模式 | Body: `MaterialDelete` | [api.py:1712](../../src/final_review/api.py#L1712) |

## 旧练习与 Agent 会话

| 方法 | 完整路径 | 实现与边界 | 请求参数 | 源码 |
|---|---|---|---|---|
| POST | `/api/quiz/generate` | 快速练习出题；使用 fast_quiz_session，不创建正式 QuizRevision | Body: `FastQuizRequest` | [api.py:1515](../../src/final_review/api.py#L1515) |
| GET | `/api/courses/{course_id}/chat-quizzes/{session_id}` | 找回聊天练习；include_answers 控制练习答案展示 | Q: `include_answers` 可选 | [api.py:1599](../../src/final_review/api.py#L1599) |
| POST | `/api/quiz/{session_id}/submit` | 提交快速练习并保存评分/练习记录 | Body: `FastQuizSubmission` | [api.py:1629](../../src/final_review/api.py#L1629) |
| POST | `/agent/resume` | 补齐旧 Agent 考试画像并继续流程 | Body: `ResumeRequest` | [api.py:2475](../../src/final_review/api.py#L2475) |
| POST | `/assessment/evaluate` | 旧 Agent 会话整轮评分并保存 legacy Attempt | Body: `Submission` | [api.py:2774](../../src/final_review/api.py#L2774) |
| GET | `/api/courses/{course_id}/attempts` | 旧格式作答摘要；不能替代 formal-attempts 的稳定合同 | — | [api.py:2810](../../src/final_review/api.py#L2810) |
| GET | `/api/courses/{course_id}/attempts/{session_id}` | 按 course_id/session_id 稳定键读取旧 Attempt | — | [api.py:2833](../../src/final_review/api.py#L2833) |
| GET | `/agent/export` | 导出 Agent 会话 Markdown；不是正式笔记/试卷文件导出 | Q: `course_id` 必填；Q: `session_id` 必填 | [api.py:2931](../../src/final_review/api.py#L2931) |

## Agent 与笔记生成

| 方法 | 完整路径 | 实现与边界 | 请求参数 | 源码 |
|---|---|---|---|---|
| POST | `/agent/invoke` | 启动 ask/quiz/note 流程；可关联课程对话 | Body: `AgentRequest`；Q: `conversation_id` 可选 | [api.py:1734](../../src/final_review/api.py#L1734) |
| POST | `/agent/resume-note` | 同步继续笔记配置/生成流程；页面通常使用 queue-note | Body: `ResumeNoteRequest`；Q: `conversation_id` 可选；Q: `event_id` 可选 | [api.py:2485](../../src/final_review/api.py#L2485) |
| POST | `/agent/queue-note` | 校验笔记配置并创建后台笔记任务 | Body: `ResumeNoteRequest`；Q: `conversation_id` 必填；Q: `event_id` 必填 | [api.py:2560](../../src/final_review/api.py#L2560) |
| POST | `/agent/retry-note` | 重试会话中的失败笔记任务 | Q: `course_id` 必填；Q: `conversation_id` 必填；Q: `job_id` 必填 | [api.py:2644](../../src/final_review/api.py#L2644) |
| POST | `/agent/cancel-note` | 取消笔记配置或排队/运行任务；发布开始后拒绝取消 | Body: `CancelNoteRequest`；Q: `conversation_id` 必填 | [api.py:2691](../../src/final_review/api.py#L2691) |
| POST | `/agent/recover` | 恢复 Agent 检查点或对话内的笔记流程 | Q: `course_id` 必填；Q: `session_id` 必填；Q: `conversation_id` 可选 | [api.py:2874](../../src/final_review/api.py#L2874) |
| GET | `/agent/session` | 读取 Agent 会话公开状态 | Q: `course_id` 必填；Q: `session_id` 必填 | [api.py:2924](../../src/final_review/api.py#L2924) |

## 正式试卷

| 方法 | 完整路径 | 实现与边界 | 请求参数 | 源码 |
|---|---|---|---|---|
| POST | `/api/courses/{course_id}/quiz-config/resolve` | 合并考试默认值和资料范围，澄清或返回 ready 配置 | Body: `QuizConfigRequest` | [api.py:2056](../../src/final_review/api.py#L2056) |
| POST | `/api/courses/{course_id}/quiz-jobs` | 配置 ready 后创建或复用后台出卷任务 | Body: `QuizJobRequest`；H: `idempotency-key` 可选 | [api.py:2079](../../src/final_review/api.py#L2079) |
| GET | `/api/courses/{course_id}/quiz-jobs/{job_id}` | 查询出卷状态和成功后的资产/版本入口 | — | [api.py:2106](../../src/final_review/api.py#L2106) |
| POST | `/api/courses/{course_id}/quiz-jobs/{job_id}/retry` | 重试失败出卷任务并复用可恢复进度 | — | [api.py:2117](../../src/final_review/api.py#L2117) |
| GET | `/api/courses/{course_id}/quizzes` | 列出课程试卷资产 | — | [api.py:2124](../../src/final_review/api.py#L2124) |
| POST | `/api/courses/{course_id}/quizzes/{asset_id}/revisions` | 编辑题目、答案、解析、分值和顺序，保存新草稿版本 | Body: `QuizRevisionEdit` | [api.py:2144](../../src/final_review/api.py#L2144) |
| POST | `/api/courses/{course_id}/quizzes/{asset_id}/revisions/{revision_id}/review` | 调用模型对改动题目做语义复核 | — | [api.py:2157](../../src/final_review/api.py#L2157) |
| POST | `/api/courses/{course_id}/quizzes/{asset_id}/revisions/{revision_id}/confirm-preview` | 检查可确认性并签发确认令牌 | — | [api.py:2176](../../src/final_review/api.py#L2176) |
| POST | `/api/courses/{course_id}/quizzes/{asset_id}/revisions/{revision_id}/confirm` | 消费令牌确认试卷版本 | Body: `QuizConfirm` | [api.py:2189](../../src/final_review/api.py#L2189) |
| GET | `/api/courses/{course_id}/quizzes/{asset_id}/revisions/{revision_id}` | 读取指定试卷及题目版本、答案与来源，用于卷面管理 | — | [api.py:2210](../../src/final_review/api.py#L2210) |
| GET | `/api/courses/{course_id}/quizzes/{asset_id}/revisions/{revision_id}/print` | 返回 questions 或 answers 版 Markdown；不是 DOCX/PDF 文件导出任务 | Q: `variant` 必填 | [api.py:2223](../../src/final_review/api.py#L2223) |
| POST | `/agent/resume-quiz` | 补齐 Agent 正式试卷配置；不是排队生成 Job 的替代入口 | Body: `ResumeQuizRequest` | [api.py:2480](../../src/final_review/api.py#L2480) |

## 正式作答与结果

| 方法 | 完整路径 | 实现与边界 | 请求参数 | 源码 |
|---|---|---|---|---|
| POST | `/api/courses/{course_id}/quizzes/{asset_id}/attempts` | 从当前已确认的正式试卷创建 Attempt，固定版本 | — | [api.py:2239](../../src/final_review/api.py#L2239) |
| GET | `/api/courses/{course_id}/formal-attempts` | 列出正式作答历史 | — | [api.py:2249](../../src/final_review/api.py#L2249) |
| GET | `/api/courses/{course_id}/formal-attempts/{attempt_id}` | 读取正式作答状态和已保存答案，作答中隐藏标准答案 | — | [api.py:2257](../../src/final_review/api.py#L2257) |
| PUT | `/api/courses/{course_id}/formal-attempts/{attempt_id}/answers` | 保存单题答案；expected_version 乐观并发控制 | Body: `FormalAnswerSave` | [api.py:2267](../../src/final_review/api.py#L2267) |
| POST | `/api/courses/{course_id}/formal-attempts/{attempt_id}/submit` | 交卷后尝试评分；提交成功不代表评分一定成功 | — | [api.py:2288](../../src/final_review/api.py#L2288) |
| POST | `/api/courses/{course_id}/formal-attempts/{attempt_id}/grade` | 单独执行或重试评分；客观题确定性判分，文字主观题模型参考评分 | — | [api.py:2324](../../src/final_review/api.py#L2324) |
| GET | `/api/courses/{course_id}/formal-attempts/{attempt_id}/result` | 读取交卷评分后的逐题结果、解析、得分点及来源 | — | [api.py:2334](../../src/final_review/api.py#L2334) |
| POST | `/api/courses/{course_id}/formal-attempts/{attempt_id}/result/questions/{question_revision_id}/follow-up` | 带该题及其已授权来源进行 AI 追问 | Body: `QuizFollowUpRequest` | [api.py:2432](../../src/final_review/api.py#L2432) |

## 错题与重练

| 方法 | 完整路径 | 实现与边界 | 请求参数 | 源码 |
|---|---|---|---|---|
| GET | `/api/mistakes` | 跨课程或指定课程的六维组合筛选错题 | Q: `course_id` 可选；Q: `exam_id` 可选；Q: `chapter` 可选；Q: `knowledge_point` 可选；Q: `question_type` 可选；Q: `mastery_status` 可选 | [api.py:2341](../../src/final_review/api.py#L2341) |
| GET | `/api/courses/{course_id}/mistakes/{mistake_id}` | 读取错题及原题、作答、评分、来源快照 | — | [api.py:2364](../../src/final_review/api.py#L2364) |
| PUT | `/api/courses/{course_id}/mistakes/{mistake_id}/mastery` | 手工标记 mastered/unmastered，记录掌握事件，不改原成绩 | Body: `MasteryMark` | [api.py:2373](../../src/final_review/api.py#L2373) |
| POST | `/api/courses/{course_id}/mistakes/{mistake_id}/practices` | 创建独立的单题重练记录 | — | [api.py:2386](../../src/final_review/api.py#L2386) |
| GET | `/api/courses/{course_id}/mistakes/{mistake_id}/practices` | 列出某道错题的重练历史 | — | [api.py:2396](../../src/final_review/api.py#L2396) |
| GET | `/api/courses/{course_id}/mistake-practices/{practice_id}` | 恢复指定重练记录 | — | [api.py:2406](../../src/final_review/api.py#L2406) |
| POST | `/api/courses/{course_id}/mistake-practices/{practice_id}/submit` | 提交重练；客观题判分，主观题 self_review，不自动更新掌握状态 | Body: `MistakePracticeAnswer` | [api.py:2416](../../src/final_review/api.py#L2416) |

## 基础报告

| 方法 | 完整路径 | 实现与边界 | 请求参数 | 源码 |
|---|---|---|---|---|
| GET | `/api/courses/{course_id}/report` | 旧格式 Attempt 汇总；正式作答统计存在字段不兼容 | — | [api.py:2843](../../src/final_review/api.py#L2843) |

## 请求体字段速查

`*` 为模型必填；省略字段时的默认值、长度、枚举和嵌套结构以 [openapi.json](openapi.json) 为准。部分字段还受服务层约束，例如笔记新请求最多 5 份资料。

| 模型 | 字段（类型） |
|---|---|
| `AgentRequest` | `course_id*`: string；`session_id*`: string；`message*`: string；`intent`: auto / ask / quiz / note；`chapter`: string；`exam_profile`: ExamProfile / null；`note_input`: NoteInput / null；`quiz_input`: QuizInput / null |
| `AssetCreate` | `asset_type*`: note / quiz；`title*`: string；`markdown*`: string；`source_document_ids`: list[string] |
| `AssetRevisionCreate` | `base_revision_id*`: string；`title*`: string；`markdown*`: string；`source_document_ids`: list[string] |
| `Body_upload_knowledge_upload_post` | `course_id*`: string；`title*`: string；`source_type*`: SourceType；`chapter`: string；`file*`: string |
| `CancelNoteRequest` | `course_id*`: string；`session_id*`: string |
| `ChatRequest` | `message*`: string；`history`: list[ChatMessage]；`course_id`: string；`conversation_id`: string；`model_id`: string / null；`attachment_document_ids`: list[string]；`mode`: auto / direct；`source_document_ids`: list[string] / null；`chapter`: string；`materials_only`: boolean；`retrieval_action`: general / overview / search / read；`retrieval_query`: string；`overview_kind`: course / statistics / list / all；`quiz_input`: QuizInput / null |
| `ConfirmationConsume` | `confirmation_id*`: string |
| `ConversationCreate` | `title*`: string |
| `ConversationRename` | `title*`: string |
| `CourseCreate` | `name*`: string |
| `CourseUpdate` | `name`: string / null；`subject`: string / null；`expected_updated_at*`: string |
| `Credentials` | `email*`: string；`password*`: string |
| `ExamCreate` | `name*`: string；`exam_at`: string / null；`total_score`: number / null；`blueprint`: list[ExamBlueprintItem]；`emphasis`: list[string]；`exclusions`: list[string]；`notes`: string；`generation_preferences`: object |
| `ExamUpdate` | `name*`: string；`exam_at`: string / null；`total_score`: number / null；`blueprint`: list[ExamBlueprintItem]；`emphasis`: list[string]；`exclusions`: list[string]；`notes`: string；`generation_preferences`: object；`expected_updated_at*`: string |
| `FastQuizRequest` | `course_id*`: string；`chapter`: string；`question_types*`: list[choice / fill_blank / true_false / short_answer / calculation / proof]；`question_count`: integer；`model_id`: string / null；`source_document_ids`: list[string] / null；`query`: string；`random`: boolean |
| `FastQuizSubmission` | `course_id*`: string；`answers*`: object |
| `FormalAnswerSave` | `question_revision_id*`: string；`answer`: integer / boolean / string / null；`expected_version*`: integer |
| `MasteryMark` | `status*`: mastered / unmastered |
| `MaterialDelete` | `confirmation_id*`: string；`mode`: block / retain_source_snapshot |
| `MaterialInput` | `document_id`: string / null；`course_id*`: string；`title*`: string；`source_type*`: SourceType；`chapter`: string；`markdown*`: string |
| `MaterialUpdate` | `title*`: string；`chapter`: string；`source_type*`: SourceType；`expected_updated_at`: string / null |
| `MistakePracticeAnswer` | `answer*`: integer / boolean / string |
| `NoteConfirm` | `revision_id*`: string；`confirmation_id*`: string |
| `NoteConfirmPreview` | `revision_id*`: string |
| `NoteExportCreate` | `revision_id*`: string；`format*`: markdown / docx / pdf / print |
| `NoteRevisionEdit` | `base_revision_id*`: string；`title*`: string；`points*`: list[NotePointEdit] |
| `QuizConfigRequest` | `quiz_input*`: QuizInput；`conversation_id`: string / null |
| `QuizConfirm` | `confirmation_id*`: string |
| `QuizFollowUpRequest` | `message*`: string |
| `QuizJobRequest` | `quiz_input*`: QuizInput；`conversation_id`: string / null；`model_id`: string / null |
| `QuizRevisionEdit` | `base_revision_id*`: string；`questions*`: list[QuizQuestionEdit] |
| `ResumeNoteRequest` | `course_id*`: string；`session_id*`: string；`note_input`: NoteInput |
| `ResumeQuizRequest` | `course_id*`: string；`session_id*`: string；`quiz_input*`: QuizInput |
| `ResumeRequest` | `course_id*`: string；`session_id*`: string；`exam_profile*`: ExamProfile |
| `Submission` | `course_id*`: string；`session_id*`: string；`answers*`: object |

## 机器可读附件

- [openapi.json](openapi.json)：由应用工厂生成，可导入接口工具；未启动服务生命周期，未连接数据库或模型。
- [routes.json](routes.json)：96 个接口的方法、路径、函数、源码行号、参数及 OpenAPI 响应声明。
- [verification.json](verification.json)：内存 HTTP 探查的现象记录，专门验证报告/同名课程/空笔记/取消试卷配置边界。
