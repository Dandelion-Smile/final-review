# Langfuse 真实评测可用性检查

检查日期：2026-11-05（北京时间）。真实调用窗口：23:53:08—23:55:05。服务端 4.75.0 OSS，Python SDK 5.1.0。

## 结论

**可以开始低并发、小样本的真实 Final Review 评测。** 本次已执行真实接口、真实 Embedding 和聊天模型调用，运行外部 Python 规则评估器，并从网页回读每条分数。尚不能据此声称部署可稳定承载多人并发或长时间批量任务，也没有完成正式质量回归体系。

结果入口：[本次真实 API 实验](https://124.222.254.31:8443/project/final-review-eval/experiments/results?baseline=9b5651667798dfa2)。

## 复查发现及修正

此前部署的健康检查正常，但两小时运行后的复查发现：

1. Web 在原 512 MiB Node heap 配置下发生两次 JavaScript heap out of memory，Docker 自动重启。`OOMKilled=false` 不能排除 Node 自行因堆内存不足退出。
2. ClickHouse 的全局查询并发上限被压为 8，低于网页并行读取图表、过滤项和分数的需求，产生 `Too many simultaneous queries`，部分分数列未正确加载。
3. 为省资源禁用 `system.query_log`，但 Langfuse v4 的后台 legacy API usage 任务仍依赖该表，导致周期性错误。

本次已备份原配置并修正：

- Web 容器内存上限由 768 MiB 调至 1024 MiB，Node old-space 由 512 MiB 调至 768 MiB。
- 整组 `langfuse.slice` **继续维持 2 GiB 总上限**，没有增加整组预算。
- ClickHouse 全局查询并发从 8 调到 32；原每查询内存、线程及容器内存约束保留。
- 恢复 query_log，缓冲上限 1024 行，预分配 128 行，30 秒 flush，保留 1 天。

只重启了 Langfuse 相关组件，Final Review 的业务服务、代码和数据库结构没有调整。修复后的验证窗口内，Web 重启次数为 0，未再出现上述并发、heap 或 query_log scan 错误。这个窗口不足以替代长时间运行验证。

旧配置备份：服务器 `/opt/langfuse/readiness-backup-20261105/`。当前配置仍位于 `/opt/langfuse/compose.yaml` 与 `clickhouse-small.xml`。

## 本次真实评测

使用专用部署验收账号，新建两门测试课程，上传两份明确标注为合成材料的 TCP/DNS 片段，并调用 `/agent/invoke`。没有使用个人课程，也未将参考答案直接作为模型回答。

服务配置确认：默认聊天模型为 `qwen-plus`，Embedding 为 `text-embedding-v4`。本次资料入库和问答走运行中的真实后端；没有使用 fake 模型或内存知识库。评测按并发 1 顺序执行。

| 用例 | 实际 HTTP | 实际业务结果 | 基础规则结果 | 耗时 |
| --- | --- | --- | --- | --- |
| TCP 三次握手报文顺序 | 200 | `insufficient_evidence`，无引用 | 未通过；预期应根据已入库 TCP 材料回答 | 56.095 秒 |
| DNS 是否永远只用 UDP | 200 | `completed`，正确目标文档引用 | 通过；回答说明 UDP/TCP 均可使用 | 54.379 秒 |
| 合法但无资料的课程 | 200 | `insufficient_evidence`，无引用 | 通过；符合预期拒答契约 | 5.116 秒 |

三次 HTTP 均成功，必要规则通过 2/3。正向用例引用文档命中为 1/2。该失败保留在实验中，没有通过更换样例或反复运行来隐藏。

TCP 失败说明：已入库的材料含有正确报文顺序，但外部接口最终拒答。仅靠当前 API 级追踪，还不能确定是检索、生成还是证据核验阶段导致，需要业务节点追踪或后续专项诊断。此次检查未修改业务逻辑来使样例通过。

分数定义：

- `http_ok`：HTTP 是否为 200。
- `expected_status`：业务完成/拒答状态是否符合用例。
- `citation_document_hit`：正向用例是否引用预期文档。
- `keyword_coverage`：指定字面关键词出现比例。
- `abstention_contract`：空资料用例是否拒答且无伪造引用。
- `required_checks`：HTTP、预期状态，以及来源命中或拒答契约同时满足。

**这些是基础契约、引用文档和字面检查，不是语义正确率。** 2/3 也不是 Final Review 总体准确率。网页已回读三个用例的分数，说明上传、队列消费、ClickHouse 存储、分数查询和实验 UI 的链路可工作。

## 现在可以做什么

| 能力 | 当前状态 |
| --- | --- |
| 外部 Python 脚本调用 Final Review 真实 API 并记录实验 | 已实测 |
| 代码规则评分、通过/失败记录、查看原始输出 | 已实测 |
| 固定用例复跑、比较改动前后结果 | 平台可承载；正式使用还需固定数据集、材料/索引及评估器版本 |
| 在 Langfuse 内配置 LLM-as-a-Judge 自动语义评分 | 当前组织和项目均没有 LLM connection，尚未配置/验证 |
| 追踪 LangGraph 内部节点、所有模型调用与重试 | Final Review 尚未内置 SDK/callback，当前只记录外部 API 观察 |
| 自动完整统计模型 token、模型成本 | 当前 API 级适配器未提供模型内部 usage，尚未覆盖 |
| 笔记、试卷、评分和异步 worker 的回归 | 仍需加入专门用例、终态轮询和对应评估器 |
| PR 自动阻断质量退步 | 尚未配置 CI 门槛 |

正式开始时，先把这三条样例替换或扩展为人工核验的真实课程节选，固定参考得分点与来源。然后补业务调用追踪、裁判模型/评分标准、运行预算和失败阈值。继续保持并发 1，先处理本次已经暴露的 TCP 拒答问题。

## 资源与证据

本次实测后，Web 约 715 MiB、Worker 约 353 MiB、ClickHouse 约 128 MiB、MinIO 约 64 MiB、Redis 约 9 MiB；整机可用内存约 1453 MiB，宿主 swap 使用约 631 MiB，磁盘余量约 43 GB。原生 Final Review API 服务保持 active。上述是抽样数据，不能推导高并发容量。

本机证据目录：`D:/final_review/backups/langfuse-20261105/`：

- `readiness-real-eval.py`：本次实际执行的验收脚本。
- `readiness-real-eval.json`：完整报告及实际响应。
- `readiness-real-cases.json`：逐例输入、预期、输出与分数。
- `readiness-case-state.json`：测试课程与文档 ID。
- `readiness-system-state.txt`：修正后最终服务与资源状态。

服务器同名评测脚本和报告在 `/opt/langfuse/`。验收脚本复用了服务器受限目录中的专用验收账号，仅为本次可用性证明；正式 CI 应改为可配置、独立凭据和明确数据清理策略，不能把该脚本直接当成通用生产测试入口。
