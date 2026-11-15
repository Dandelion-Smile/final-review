# 课程知识库构建

资料解析成功后自动排队，原资料仍按原流程可检索。知识库任务独立失败、重试，不修改资料的解析状态。
重复上传复用同版本、同策略的构建任务；历史已解析资料再次上传也会补建任务。

## 数据与流程

- 迁移：`db/migrations/015_course_knowledge.sql`；新增 5 表，扩展 `knowledge_points`，保留旧字段和 JSON。
- 可靠性迁移：`016_knowledge_pipeline_reliability.sql`；新增比较记录表及向量、事实整理阶段。
- 原 `knowledge_points` 记录标记为 `legacy`，不自动成为正式出题知识点。
- `material_extraction_blocks` 按显式章节边界、页数和长度上限组织相邻证据，可跨页；保留定位 ID。
- LLM 逐块输出知识点及事实。先验证定位所属范围、逐字引文，再独立核验语义支持关系。
- `knowledge_candidates` 按块保存检查点。名称标准化保留概念后缀、否定、运算符和粒度。
- 一级：同任务候选的同名／向量召回及事实比较；合并事实和多来源证据。
- 二级：同课程正式知识点的同名匹配，否则余弦 Top-K；比较事实，复用等价项、补充新事实。
- 同名不强制唯一；包含、相关关系保留独立概念。冲突或多目标歧义进入 `review_needed`。
- 正式知识点、事实、证据及候选最终状态在同一事务提交；同课程事务锁和快照复核防并发重复。
  同批次后续候选也会看到前面候选计划新增的事实。LLM 调用在事务外。
- 原文定位以版本保存；构建期间资料删除、版本变化或账号注销会阻止发布。
- 向量模型配置随任务与新知识点保存；不同向量空间禁止混用，需重新构建。

## 运行

```powershell
.venv\Scripts\python.exe -m final_review.migrations
.venv\Scripts\python.exe -m final_review.knowledge_jobs
```

Docker Compose 已增加 `knowledge-worker` 服务。`KNOWLEDGE_MODEL_ID` 默认 `default`，其他配置见 `.env.example`。
Windows 转换子进程使用 Python 验证可写的临时目录，避免失效的系统 TEMP 导致 LibreOffice 导出失败。
`--once` 仅处理一个可领取任务。模型请求失败自动重试；租约心跳、尝试编号阻止过期 worker 写入。
提取返回后立即保存候选检查点；证据校验单独保存，向量按最多 10 条分批保存。
重试只生成缺失或内容哈希已变化的向量，已完成语义块直接跳过。
知识库单次请求默认 180 秒、最多输出 8192 token，分别由 `KNOWLEDGE_MODEL_TIMEOUT`、
`KNOWLEDGE_MAX_OUTPUT_TOKENS` 配置。请求层不隐式重试，统一交给任务层重试，避免多层重试叠加。
运行预算独立于聊天超时，不改变已有任务的语义策略指纹；修改语义构建配置则创建新策略任务。
任务和语义块的错误信息会明确区分模型超时与其他失败。

## 并发与去重可靠性

- 前端同时上传最多 3 个文件，一次选择上限仍为 5。
- 单个资料 worker 内同时处理最多 5 个文件，每个任务独立数据库访问实例、用户上下文及心跳。
- 单个知识库 worker 内最多 2 个构建任务，同课程发布仍使用事务锁及快照复核。
- 转换/页面渲染并发 2、视觉请求并发 4、向量请求并发 2；使用 PostgreSQL 会话锁槽位跨 worker
  协调资源预算，连接断开自动释放。统一配置见 `.env.example`，实例间应使用相同上限。
- 一对多事实比较触发定点拆分：检查原意覆盖、条件保留，并重新校验证据；保留 `raw_facts`、
  `source_fact_key`、`normalizations` 供追溯。不能确认时隔离相关候选，其他候选继续处理。
- 比较使用临时 L/R 编号；完全重复的匹配边去重，未知编号和歧义比较最多局部尝试 3 次。
- `knowledge_match_decisions` 保存比较对象、输入哈希、原始判定、映射结果、错误和尝试历史。
  同一输入和 `atomic-matcher-v2` 策略的成功比较直接复用，内容变化后重新比较。
- `review_needed` 保存明确的比较目标与理由。系统故障仍使任务失败，语义不确定性暂存候选。

## 接口

以下路径均以 `/api/courses/{course_id}` 开头，使用现有用户认证：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/documents/{document_id}/knowledge-build` | 为已解析资料创建或复用任务 |
| GET | `/knowledge-build-jobs` | 查看构建任务 |
| GET | `/knowledge-build-status` | 按资料查看最新构建及分块进度 |
| GET | `/knowledge-build-jobs/{job_id}` | 查看阶段、状态及错误 |
| POST | `/knowledge-build-jobs/{job_id}/retry` | 重试失败任务 |
| GET | `/knowledge-build-jobs/{job_id}/candidates` | 查看候选、事实及校验结果 |
| GET | `/knowledge-build-jobs/{job_id}/decisions` | 查看比较依据、错误和重试历史 |
| GET | `/knowledge-points` | 查看正式活动知识点 |
| GET | `/knowledge-points/{point_id}` | 查看知识点、事实和证据 |

任务、候选及比较列表支持 `limit` 和 `offset`；状态接口返回课程各资料的最新任务。
资料页面分别显示解析和知识库构建进度，失败任务可单独重试。现有正式出题流程尚未切换到新知识库。
未来出题应只使用 `active` 知识点下有充分证据的 `supported` 事实。

## 验证

- 单元测试：`tests/test_course_knowledge.py`。
- 向量分批、复合事实修复、局部隔离、五文件调度测试：`tests/test_pipeline_reliability.py`。
- PostgreSQL 链路／并发／回滚／重试／隔离测试：`tests/test_course_knowledge_postgres.py`。
- 集成测试必须设置独立的 `TEST_DATABASE_URL`；测试会重建该测试库的 public schema。
- 实际 PPT／OCR／模型验证：`scripts/check_course_knowledge_live.py`，仅允许独立的
  `final_review_knowledge_live*` 数据库。可在安装转换依赖的应用镜像中运行。
- 实测报告：`exports/course-knowledge-live-report.json`；两份 PPT 复用同一知识点，事实从 2 条增至 3 条，证据从 2 条增至 5 条。
