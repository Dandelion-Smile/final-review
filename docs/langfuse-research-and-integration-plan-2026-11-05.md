# Langfuse 调研与 Final Review 自动化评测接入方案

调研日期：2026-11-05（北京时间）。依据：当天官方文档、本地工作区源码及 2026-10-28 部署记录。本文是调研及实施设计，尚未部署 Langfuse、修改运行服务或执行真实模型评测。

后续进展：用户随后要求在原服务器尝试部署，现已完成低资源试运行和合成链路验证，详见 [实际部署记录](langfuse-deployment-2026-11-05.md)。下文保留调研时的建议与证据边界；实际配置、版本和实时容量以部署记录为准。

## 1. 结论

**适合引入 Langfuse，用来追踪 AI 调用、建立固定评测集、比较模型/提示词/检索改动，并给发布增加质量门槛。现有 2 核 4 GB 服务器不适合同时承载完整 Langfuse 与 Final Review。**

优先方案：Langfuse 部署到另一台容量足够的机器，Final Review 保持现有部署并通过 SDK 上报。如果必须部署在当前服务器上，先扩容和复核磁盘、内存及转换任务峰值，再做单机试运行。预算未定时，可以先把 Langfuse 放在本机 Docker 做验证；部署在本机的服务要保持运行，现网应用才可持续上报。Cloud 可作为可选验证环境，但需要先确定课程材料和回答可否发送到托管服务。

Langfuse 不负责替代整个软件测试体系。建议分工：

| 测试内容 | 主要工具 | Langfuse 的作用 |
| --- | --- | --- |
| 权限隔离、接口状态、数据库事务、任务状态机 | pytest + 隔离 PostgreSQL | 可附带追踪，硬断言仍由测试代码执行 |
| 登录、上传、弹窗、作答、下载等用户流程 | Playwright | 对关联 AI 请求提供内部调用证据 |
| 问答、笔记、出题、评分的实际质量 | 真实应用调用 + Langfuse Experiments + 评估函数 | 管理输入/参考结果，记录结果、分数及版本，比较回归 |
| 线上 AI 故障、延迟、token、重试 | Langfuse tracing + 应用日志 | 定位发生问题的步骤；价格映射和模型 usage 完整时才计算可信成本 |

官方支持线上 trace 评估和离线数据集实验，二者可以共同形成迭代流程。[评测概览](https://langfuse.com/docs/evaluation/overview)

## 2. 产品能力、版本与成本

Langfuse 的核心价值是把一次 AI 任务中的输入、输出、模型调用、检索、工具使用和错误串起来，再对任务结果打分和比较。它不会安装后自动理解 Final Review 的业务，也不会自动形成可信参考答案。

核心自托管功能可免费使用，包括 LLM-as-a-Judge、提示词实验及人工标注队列；部分组织治理功能需要企业许可。实际支出主要是运行基础设施、业务模型、Embedding、裁判模型调用和维护。[官方功能澄清](https://langfuse.com/resources/engineering/clarifications)

当天文档已是 **Langfuse 服务端 v4 / Python SDK v5**。SDK v5 要求服务端 **v4.59.0 或更新版本**、Python 3.11+、LangChain Core 1+。本项目声明 Python >=3.11、LangChain >=1，方向上匹配；实施时仍须核对锁文件解析结果并锁定实际验证的服务端镜像、SDK 和 Action 版本。不要把 SDK v2/v3 的教程直接混进新接入代码。[SDK 兼容要求](https://langfuse.com/docs/observability/sdk/upgrade-path/python-v4-to-v5)

费用预算按实际 token 估算：每轮总费用 = 应用模型调用 + 必要的 Embedding + 裁判模型调用。笔记、视觉解析、分批出题及修复会使一次用例包含多次模型调用，不能把“40 个用例”当成“40 次请求”。先测 8 个样例的真实用量，再估算扩大数据集的成本；不在本调研中给出未经测量的金额。

## 3. 当前服务器是否能部署

本地部署记录显示当前服务器为 `124.222.254.31`，Ubuntu 24.04，2 vCPU、约 4 GB RAM、60 GB 系统盘；Nginx、原生 PostgreSQL 16，以及 API、资料、笔记、试卷、导出共 5 个应用进程已部署。小样本验收曾有约 2.4 GiB 可用内存，但没有多人并发容量验证。这是历史记录，不是今天的运行状态。[项目部署记录](deployment-124.222.254.31-2026-10-28.md)

本次尝试了只读 SSH 检查；已存在的私钥需要解锁，未取得有效登录，因此未实时核对 CPU、内存、空闲磁盘、Docker 或服务状态。

Langfuse 的常规架构需要 Web、Worker、PostgreSQL、Redis/Valkey、ClickHouse 和 S3 兼容对象存储。本项目已有 PostgreSQL 不能消除其他依赖，也不能用 pgvector 替代 ClickHouse。AI Gateway 是可选组件，首轮无须引入。[自托管架构](https://langfuse.com/self-hosting)

官方 Docker Compose 的 **VM** 页签建议至少 **4 核、16 GiB 内存**，并给出 **100 GiB 磁盘**示例。这个建议针对 Langfuse 单机部署，不是与 Final Review 同机后的容量保证；同机还要为 LibreOffice、OCR、Chromium 和各 worker 留出峰值空间。[官方单机部署指南](https://langfuse.com/self-hosting/deployment/docker-compose)

| 选择 | 评估 |
| --- | --- |
| 当前 2 核 4 GB 直接同机部署完整组件 | 不推荐，缺少内存与磁盘余量的证据，容易与已有转换和生成任务争抢资源 |
| 当前服务器扩容后同机运行 | 满足“同一服务器”目标；4 核 16 GiB 可作为低流量试运行评估起点，必须测峰值，不能承诺足够 |
| 单独机器运行 Langfuse | 优先建议；按官方规格起步评估，应用与观测平台的资源互相隔离 |
| 本机 Docker / Langfuse Cloud 验证 | 先验证价值，暂不调整现网容量；Cloud 方案另行确认数据范围 |

官方还有按组件分配资源的生产扩展表，不能把其中某一个容器的规格当成整套系统需求，也不应将它机械相加后当成低流量 Compose 的启动门槛。[Sizing & Scaling](https://langfuse.com/self-hosting/configuration/scaling)

## 4. 部署设计

建议单独 Compose 项目和目录，例如 `/opt/langfuse`，独立数据库凭据、数据卷和 Docker 网络。首轮使用标准对象存储组件，不引入 Kubernetes 或 AI Gateway。

通过既有 Nginx 提供独立 HTTPS 入口；有域名时优先专用子域名。没有域名时可以先用 SSH 隧道验证，或按官方自定义 base path 方案评估 `/langfuse`，不要只改一个反向代理路径就假设静态资源和回调全部可用。

部署前须完成的具体配置：

1. 核对磁盘、运行峰值、镜像仓库连通性；锁定服务端、依赖组件和 Compose 源版本。
2. 生成独立认证、加密及数据库密钥；将运行配置保存在受限环境文件中。
3. 处理现有 PostgreSQL 的 5432 端口冲突：Langfuse 自有数据库可仅在 Docker 内部访问，无须映射宿主端口。
4. Web 绑定宿主回环地址，由 Nginx 转发；ClickHouse、Redis 和数据库仅内部访问。需要浏览器多模态上传时，再为对象存储配置可达的 HTTPS 地址及匹配的媒体 endpoint。
5. 设置正确的外部访问地址、创建项目及 API Key；上线验收包括登录、trace 写入、Worker 消费、实验及分数在 UI 中可见。
6. 给数据库、ClickHouse、对象存储、关键配置和加密密钥制定备份与恢复步骤；Compose 本身不提供备份或高可用。配置容器日志轮转，并另外规划 trace 数据增长与保留，避免把日志轮转当成数据库清理。

正式部署时应以锁定的官方 Compose 为基础修改，而非复制本文中的架构清单自行拼装配置。[官方 Compose](https://github.com/langfuse/langfuse/blob/production/docker-compose.yml)

## 5. Final Review 接入位置

| 位置 | 接入方式及目的 |
| --- | --- |
| `src/final_review/agent.py` | 在图执行入口传入 Langfuse `CallbackHandler`，记录路由、检索、生成、核验、拒答和修复；保留现有 checkpoint 配置 |
| `src/final_review/llm.py` | 覆盖 `structured()`、工具调用和单独模型调用；核对重试、结构解析失败和 token usage 是否完整 |
| `src/final_review/api.py` | 当前普通聊天存在直接 `OpenAI()` 调用，不能仅靠 LangGraph callback 覆盖，需要 OpenAI 集成或手工 observation |
| `src/final_review/slide_understanding.py` | 视觉解析直接使用 OpenAI 客户端，需额外接入；记录页号、读取/复核阶段和缓存命中 |
| `material_jobs.py`、`note_jobs.py`、`quiz_jobs.py` | 独立进程需要初始化上报，并显式传递关联信息；以 job_id 连接请求与后台任务，必要时传播 trace context |
| `rag.py` | 记录候选及最终片段 ID、位置、排名、阈值，才能区分召回失败与生成失真 |
| `eval/run.py` | 改造为真实应用的实验适配器，保留 JSON 产物并上报分数 |

官方 LangChain/LangGraph 集成使用 `from langfuse.langchain import CallbackHandler`，在调用的 config 中传入 callback；直接 OpenAI 调用和非 LangChain 业务步骤需要另外覆盖。[集成文档](https://langfuse.com/integrations/frameworks/langchain)

追踪元数据建议包含：应用 commit、提示词修订、模型/提供商、检索配置、数据集版本、匿名用户 ID、course_id、session_id、job_id 和实验名称。Cookie、认证头、API Key 不进入 trace；课程内容按需要截取并设置数据范围。测试环境与线上使用独立项目或至少独立环境标记。

接入应可关闭；观测平台不可达时，普通业务继续工作并记录上报失败。评测/CI 则必须把实验或分数缺失视为失败，不可报成通过。服务正常退出和短脚本结束时需 flush。项目 `Settings` 从 `.env` 读值不等于给 SDK 自动写入进程环境，实施时要显式传入配置或由 systemd EnvironmentFile 注入。

## 6. 现有评测的可复用部分与缺口

已有 `eval/cases.json`：4 份合成材料、8 个固定问答，包括 6 个正常问答和空课程、空章节场景。已有指标为 HTTP 成功、引用文档命中、关键词覆盖和拒答。这是良好起点，但不等于语义正确率或完整检索 Recall@K。

`eval/run.py` 与当前 API 已有契约差异：

- 脚本仅支持可选 Bearer Token，没有先登录获取 Session Cookie；当前真实服务要求数据库用户认证，Token 不能替代用户登录。
- 脚本随机构造 course_id 后直接入库；当前 `/knowledge/ingest` 与 `/agent/invoke` 都调用 `require_course`，需要先通过 `/api/courses` 创建归属测试用户的课程。
- 空课程也必须先创建合法课程再保持无资料；不存在的 course_id 应按资源不存在测试，不能期待它变成“证据不足”的业务拒答。
- 多个质量指标仅统计 HTTP 成功项；新报告应同时输出全量成功率、质量分、缺失和失败明细，不能让调用失败从分母消失。
- 样例需要补参考答案、可验证得分点、预期引用片段与允许的拒答形式。文档级命中和关键词出现分别保留原名称，避免夸大含义。

项目已有 pytest、Playwright、真实数据库脚本与真实模型检查脚本，可复用其场景；但部分 live 脚本仍使用 MemoryStore/TestEmbeddings，不应作为完整真实 RAG 基准。当前 GitHub 工作流使用 SurrealDB 测试服务，接入 Langfuse 不会自动补齐 PostgreSQL 的持续集成覆盖。[现有评测说明](../eval/README.md)

## 7. 首轮测试集设计

先让修正后的 8 个用例完整跑通，再扩到以下 **40 个候选用例**。以下数量与验收口径为建议，尚未执行、未产生分数。

| 场景 | 数量 | 主要检查 |
| --- | --- | --- |
| 有依据问答与检索 | 10 | 参考得分点、引用真实性、所选资料和章节范围 |
| 无依据、越界、资料内注入 | 6 | 合理拒答、不编造来源、不执行材料中的指令；权限用确定性断言 |
| 笔记 | 6 | 考点覆盖、来源忠实、代码/公式、取消；区分正常生成与“资料摘录”回退 |
| 出题 | 6 | 题型、数量、总分、章节、唯一答案、题答对应及引用 |
| 评分 | 6 | 客观题精确判分；主观题按人工得分点验证，检查胡编和漏判 |
| 资料处理与异步链路 | 6 | 真文件解析、页码、核验/入库状态、失败重试和任务最终结果 |

最小数据项：稳定 case_id、输入、测试材料/索引版本、参考输出或得分点、预期来源、必须通过的规则、可接受的变化及评估器版本。优先使用人工核验的真实课程节选，合成样例始终标明来源。

对同一版本先跑基线，再用同一数据集、索引快照和评估器比较候选代码。模型输出有随机性，临界用例复跑并人工确认；样例更新后须重新审定基线。

## 8. 自动打分与发布门槛

先使用确定性检查：Schema、题数、总分、引用 chunk_id、quote 与原文对应、资料范围、权限隔离、任务状态及超时。之后用独立裁判模型检查语义正确性、忠实度、考点覆盖和主观题评分质量。裁判需要看到问题、实际证据及参考得分点，并输出理由；先与人工结果校准，不能把它的分数直接称为真实准确率。

第一版门槛建议：所有关键权限/引用约束逐例通过；HTTP/任务/评估器错误和分数缺失均失败；已批准通过的用例不得退步。语义阈值、主观题可接受误差和 p95 延迟预算在真实基线后确定，不能事先虚构“95% 准确率”。异步任务要轮询到终态并读取最终资产，HTTP 202 只代表入队。

**首轮评估函数在本机或 CI 的 Python 进程运行，再把分数写入 Langfuse。** 官方明确区分 SDK evaluator 与平台 UI 中执行的 code evaluator；SDK 实验可调用完整应用，并非只能测单个提示词。[SDK 实验](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk)

这一区分对腾讯云部署很重要：自托管平台 code evaluator 默认未启用，需要额外 dispatcher；官方生产方案为 AWS Lambda，`insecure-local` 目前仅支持 JS/TS 且没有沙箱隔离。首轮使用外部 Python 评估器即可，无须为此增加 Lambda 或启用本地代码执行器。[自托管 code evaluator 配置](https://langfuse.com/self-hosting/configuration/code-evaluators)

## 9. 实施顺序与验收产物

1. **评测脚本修复**：测试账号登录、课程创建、空课程修正、失败计数及清理策略；用独立测试库/实例，避免全量回归写进个人课程。
2. **Langfuse 试运行**：选择本机、独立机器或扩容后的当前机器；完成 trace、实验、分数的端到端验证。
3. **核心链路追踪**：问答/检索先接通，然后补直接 OpenAI 调用、视觉页分析及独立 worker；验证观测故障不阻塞业务。
4. **基线与质量评估**：8 个样例首轮真实运行，再扩至人工核验的 40 个场景；保留原始响应、分数理由和版本清单。
5. **CI 门槛**：确定性测试每次 PR 运行；真实模型评测先手动触发，再决定哪些稳定且成本可控的场景进入发布检查。隔离候选应用的 DB、上传目录、worker 与端口，不能只给生产服务发请求却声称测了 PR 代码。

官方提供 `langfuse/experiment-action`；可以按固定数据集及阈值让工作流失败。项目也可以先用现有 uv/pytest 工作流执行评测脚本，返回非零退出码，无须第一天就迁移全部流水线。[CI/CD 实验与回归门槛](https://langfuse.com/docs/evaluation/experiments/experiments-ci-cd)

验收应同时交付：可访问的 Langfuse 项目、服务端/SDK 版本清单、代表性完整 trace、可重复运行的命令、版本化测试集、真实基线报告、至少一个故意制造回归而失败的示例，以及部署恢复记录。界面能打开或出现一条 trace，都不足以代表自动化测试完成。

当前落地前仍需确认：服务器是否扩过容及实时余量、可用的部署访问方式、Langfuse 的运行位置/入口、独立测试数据环境，以及真实模型评测的调用预算。本次研究未读取或输出业务密钥，也未对现网执行安装、重启和数据写入。
