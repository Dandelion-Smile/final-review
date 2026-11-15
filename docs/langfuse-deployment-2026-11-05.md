# Langfuse 同机试运行部署记录

部署日期：2026-11-05（北京时间）。服务器：`124.222.254.31`，Ubuntu 24.04，2 vCPU、约 3.6 GiB 可见内存、60 GB 系统盘。

后续复查发现并修正了运行期间的 Web heap 重启、查询并发与 query_log 配置问题，并完成 3 条真实 API 评测。详见 [真实评测可用性检查](langfuse-readiness-check-2026-11-05.md)；以下资源表已同步当前配置，首次部署验证记录保留作为历史证据。

## 入口与账号

- 管理入口：<https://124.222.254.31:8443/>
- 项目：<https://124.222.254.31:8443/project/final-review-eval>
- 账号：`admin@final-review.invalid`
- 随机生成的管理员密码：服务器 `/opt/langfuse/access.txt`；本机 `D:/final_review/backups/langfuse-20261105/access.txt`。本机文件 ACL 已限制为当前 Windows 用户访问。
- SDK 配置：服务器 `/opt/langfuse/sdk.env`，权限 600，包含项目 Public/Secret Key 及 HTTPS 地址。密钥未写入本文或业务仓库。
- 媒体对象存储 HTTPS 入口：`https://124.222.254.31:8444`。匿名访问不提供桶内容，浏览器媒体操作使用签名 URL。

已有 Final Review 继续使用 <https://124.222.254.31/>。Langfuse 部署没有修改业务代码、业务环境文件或业务数据库结构。

## 实际架构与版本

Langfuse 服务端为 **4.75.0 OSS**，验证使用 Python SDK **5.1.0**。

| 组件 | 部署方式 | 监听 / 数据 |
| --- | --- | --- |
| Web | Docker `langfuse-trial-web-1` | 宿主 `127.0.0.1:13000` → 容器 3000 |
| Worker | Docker `langfuse-trial-worker-1` | 容器内 3030，不映射公网 |
| ClickHouse | Docker，26.3.62.3 | 仅 Docker 网络 |
| Redis | Docker，7 系列 | 仅 Docker 网络，AOF 持久化 |
| S3 存储 | 官方 Compose 使用的 Chainguard MinIO 镜像 | 宿主 `127.0.0.1:19090` → 容器 9000 |
| PostgreSQL | 复用现有原生 PostgreSQL 16 实例 | 独立库与角色 `langfuse_trial` |
| HTTPS | 既有 Nginx 新增独立站点 | 公网 TCP 8443 / 8444 |

Docker 29.1.3、Compose 2.40.3 来自服务器 Ubuntu 软件源。Docker Hub 直连不可用，因此 Langfuse、ClickHouse、Redis 经腾讯云镜像加速拉取，MinIO 从 `cgr.dev` 拉取。所有运行镜像随后按实际 RepoDigest 锁定；记录位于 `/opt/langfuse/image-digests.txt`，SDK 依赖版本位于 `/opt/langfuse/sdk-requirements.txt`。

Docker 使用独立网络 `172.30.85.0/24`。PostgreSQL 仅追加允许该网段以独立角色访问独立库的 HBA 规则，并 reload；未重启数据库、未修改既有库的数据。

## 资源约束与适用范围

这是用户要求在原服务器尝试的低流量实例，低于官方推荐规格。为限制对现有业务的影响，新增 `langfuse.slice`，整组容器的 MemoryHigh 为 1900 MiB、MemoryMax 为 2 GiB、CPUWeight 为 25。

| 容器 | 内存硬限制 | 其他约束 |
| --- | --- | --- |
| Web | 1024 MiB | Node old-space 768 MiB |
| Worker | 640 MiB | Node old-space 384 MiB |
| ClickHouse | 512 MiB | 查询内存、线程、缓存和后台池缩小 |
| Redis | 96 MiB | maxmemory 64 MiB，noeviction |
| MinIO | 192 MiB | GOMEMLIMIT 128 MiB |

各容器上限不是同时保留的资源，整组仍受 2 GiB 总上限限制。每个容器的 memory+swap 上限与内存上限相同，不允许容器额外使用 swap；宿主原有 2 GB swap 保留，原生进程仍可使用。

初始过低的 Web Node heap 在初始化时发生过内存不足，随后已调高；运行配置不是“官方推荐生产配置”。当前仅验证单管理员、小样本追踪与实验，没有多人并发或资料转换/生成与评测同时运行的容量证明。后续运行真实评测先保持并发 1；出现容器重启、队列积压或业务延迟时，需要扩容或迁移观测服务。

## HTTPS 与防火墙

沿用 Final Review 已有的 IP 证书 `/etc/letsencrypt/live/final-review-ip/`，Nginx 新配置位于 `/etc/nginx/sites-available/langfuse`。现有 `final-review-certbot-renew.timer` 调用 `certbot renew` 并通过已有 hook reload Nginx，8443/8444 同样使用续期后的证书。

腾讯云轻量实例 `lhins-3nvczcli` 新增一条 TCP `8443-8444` 允许规则，备注 `Langfuse HTTPS 8443 / S3 8444`。其他云防火墙规则保留。数据库、Redis、ClickHouse、Worker 没有新增公网端口映射。

曾尝试使用临时 sslip.io 域名，通过 HTTP ACME 校验时返回腾讯云域名拦截页，未签发证书；该临时域名配置已替换为上述 IP 端口入口。失败日志保存在 `/opt/langfuse/certificate.log`，没有创建可用的临时域名证书。

## 验证结果与边界

- 浏览器经可信 HTTPS 打开登录页，使用初始管理员账号成功登录并进入项目。
- Web `/api/public/health` 返回 `OK` 和 `4.75.0`。
- 五个容器的健康检查已配置，Web/Worker 显式监听 `0.0.0.0`，避免 Docker 默认 HOSTNAME 导致回环探针失败。
- Python SDK 5.1.0 项目鉴权通过，写入 `deployment-smoke` 追踪及 `deployment_probe` 分数。
- SDK 运行 `Deployment smoke 2026-11-05` 合成实验：2 个整数加法用例、0 错误，`exact_match` 均为 1。网页实验列表读取到相同结果。
- 合成样例明确标注为基础设施检查，不调用模型，不代表 Final Review 的问答、笔记、出题或评分质量已通过。
- 部署过程中多次检查原有 Final Review `/health` 为 200，五个应用 systemd 服务保持 active。
- 初始备份后停止并恢复 Langfuse，复核数据和服务持久化；没有重启整台服务器。
- 最终抽样五个容器合计约 1.29 GiB（Docker stats），整机可用内存约 1533 MiB，宿主 swap 已用约 562 MiB，磁盘余量约 43 GB。slice 包含可回收缓存，MemoryCurrent 约 1.80 GiB、观测峰值约 1.84 GiB；这仍是小样本数据，不代表并发容量。

最终机器可读证据见 `/opt/langfuse/verification.json` 和 `/opt/langfuse/smoke-sdk.json`，本机副本保存在 `D:/final_review/backups/langfuse-20261105/`。

当前**没有给 Final Review 内置 SDK、callback 或自动回归门槛**。后续复查已通过外部 Python 适配器完成真实接口评测；旧 `eval/run.py` 的登录/建课程问题仍待正式改造，直接 OpenAI 调用与独立 worker 的内部追踪尚待覆盖。

## 启动、查看与暂停

服务器新增并启用 `langfuse.service`，依赖 Docker 和原生 PostgreSQL；Docker 同时设置容器 `unless-stopped` 重启策略。Compose 和运行环境位于 `/opt/langfuse`，目录权限 700。

在服务器执行：

```bash
systemctl status langfuse.service --no-pager
cd /opt/langfuse
docker compose ps
docker compose logs --tail 100 web worker
docker stats --no-stream
systemctl show langfuse.slice -p MemoryCurrent -p MemoryPeak -p MemoryMax
curl https://124.222.254.31:8443/api/public/health
curl https://124.222.254.31/health
```

需要暂停并释放内存：

```bash
systemctl stop langfuse.service
```

恢复：

```bash
systemctl start langfuse.service
```

停止保留数据库和卷。不要使用 `docker compose down -v` 作为普通停机操作。

## 备份与恢复范围

初始一致性备份位于 `/srv/langfuse/backups/20261105T133649Z/`，含独立 PostgreSQL 逻辑备份、停止期间的四个 Docker 数据卷、运行配置及 Nginx 配置，附 SHA-256 清单；目录权限 700。配置归档包含密钥，不能提交 Git 或当成普通附件公开。

可手动运行 `/opt/langfuse/backup-instance.sh` 生成后续备份。该脚本短暂停止 Langfuse、完成备份后恢复，不停止 Final Review。尚未配置定时备份或跨机器副本，也未实际执行灾难恢复演练。

恢复须使用相同镜像 digest，先停止 Langfuse，再在核对目标数据库和卷路径后成套恢复 PostgreSQL、四个卷及配置/加密密钥。迁移到其他机器时需调整 PostgreSQL 地址、HBA 网段、HTTPS 入口和证书，不能直接覆盖现有 Final Review 数据库。
