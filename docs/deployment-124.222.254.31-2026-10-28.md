# Final Review 服务器部署记录

部署日期：2026-10-28（北京时间）。入口：<https://124.222.254.31/>。使用原来的应用账号和密码登录，迁移后需重新登录。

## 部署结果

服务器为腾讯云上海地域轻量应用服务器 `lhins-3nvczcli`，操作系统 Ubuntu 24.04，2 vCPU、约 4 GB RAM、60 GB 系统盘。来宾系统元数据里的计算实例 ID 为 `ins-jmyo7dkb`，云控制台操作使用前述轻量实例 ID。

采用 Nginx + systemd + 原生 PostgreSQL 16/pgvector。前端为 `frontend` 的 React/Vite 生产构建；API 维持单进程。模型使用原配置的外部 API，服务器无需 GPU。资料页并发降为 1。

| 服务 | systemd 单元 | 监听或职责 |
| --- | --- | --- |
| 前端与反向代理 | `nginx` | 公网 443；80 保留 ACME 验证并跳转 HTTPS |
| 后端 | `final-review-api` | `127.0.0.1:8080`，Uvicorn 单进程 |
| 资料 worker | `final-review-material-worker` | 转换、OCR、视觉核验、Embedding 入库 |
| 笔记 worker | `final-review-note-worker` | 笔记后台任务 |
| 试卷 worker | `final-review-quiz-worker` | 正式试卷后台任务 |
| 导出 worker | `final-review-export-worker` | Markdown、DOCX、PDF 导出 |
| 数据库 | `postgresql@16-main` | 原生 PostgreSQL 16，独立库 `final_review_prod` |

上述应用服务均已启用开机自启和失败重启。应用使用专用系统用户 `finalreview`，数据库使用独立登录角色 `final_review_prod`。模型 Key 仅保存在服务器环境文件，未写入前端、本文或发布清单。

## 文件与数据

```text
/opt/final-review/releases/20261028/     本次代码、前端 dist 和 Python 虚拟环境
/opt/final-review/current              指向本次 release 的符号链接
/opt/final-review/browsers/             Playwright Chromium Headless Shell
/opt/final-review/tooling/              uv、Certbot 工具虚拟环境
/srv/final-review/.env                 运行配置，finalreview 所有，权限 640
/srv/final-review/uploads/             原资料及 .analysis 页面缓存
/srv/final-review/exports/             私有导出文件
/srv/final-review/logs/                worker 文件日志
/srv/final-review/verification/        本次独立验收账号的样例和验证结果，权限 700
/root/final-review-deploy/             部署工具、日志、发布清单和备份
```

本机当前 PostgreSQL 的逻辑备份已恢复到新库 `final_review_prod`；没有覆盖服务器原来的 `final_review` 库。原库在部署前已单独备份。新库保留本机的原用户、课程、资料、学习资产与向量数据；原本机库和资料也保留。本机为稳定快照短暂停止的旧笔记容器，备份后已恢复运行。

迁移基线为 2 个原用户、5 门课程、50 条资料记录、757 个向量片段。50 条资料中 39 条未删除且有原文件路径，远端全部找到原件；已删除记录的缺失文件保留原状态。49 条资料路径从 Windows 绝对路径或反斜杠相对路径转为 `/srv/final-review/uploads/...`。另有不依赖原文件的资料记录。

上传与缓存发布包约 352 MB；数据库逻辑备份约 17.5 MB。传输后逐包验证 SHA-256。迁移副本清除了旧登录 Session，并把历史未完成任务置为失败，避免 worker 上线后自动重放历史模型调用。原有失败资料未擅自重跑。

验收使用单独的 `deployment-20261028@final-review.invalid` 测试用户及“部署验收 · 2026-10-28”课程，测试数据与原账号隔离；没有更改原账号密码。验收浏览器已退出测试账号。

## 解析与导出依赖

已安装 LibreOffice Writer/Impress、Poppler、Tesseract 及 `chi_sim`/`eng` 语言包、Noto 中英文字体；MarkItDown、Pandoc 和 Playwright 来自锁定的 Python 依赖。Chromium Headless Shell 版本与 Playwright 安装包匹配。

运行环境清除了本机 LibreOffice、Poppler、Tesseract、tessdata、浏览器和 Pandoc 的 Windows 路径，改用 Linux 软件路径或随依赖安装的转换器。

Python 运行依赖由 `uv.lock` 导出，在服务器通过可达的 PyPI 镜像安装，启用了依赖哈希校验。Chromium 官方 CDN 单连接下载较慢，本次通过并行 HTTP Range 下载相同官方版本，验证 ZIP 完整性后交给 Playwright 官方安装器安装。

## HTTPS 与云防火墙

使用 Let's Encrypt 直接签发覆盖该 IP 的公开信任证书，目录为 `/etc/letsencrypt/live/final-review-ip/`。由于使用短有效期证书，已配置 `final-review-certbot-renew.timer` 每天 02:00、14:00 附近检查续期，成功续期后检查并重载 Nginx。80 端口的 ACME 验证路径必须保留。

在用户登录的腾讯云控制台，为本轻量实例新增一条 `TCP 443 / 全部 IPv4 地址 / 允许` 的云防火墙规则，备注 `Final Review HTTPS`。既有其他规则未改动。

公网实测 HTTPS 首页和 `/health` 均为 200，证书使用默认系统信任验证成功。真实浏览器中 `window.isSecureContext === true`、`crypto.randomUUID` 可用，并通过登录及笔记列表读取。Certbot 模拟续期及 Nginx 重载钩子均已通过，日志保存在 `/root/final-review-deploy/certificate-renewal-test.log`。

## 验收证据与边界

- 本机 `npm run build` 成功；相关模型测试 `tests/test_llm.py` 为 10 项通过。
- HTTP API 的注册、登录、当前用户及 Secure/HttpOnly Cookie 验证通过。
- Markdown、DOCX 上传由资料 worker 完成解析和真实 Embedding 入库。
- 图片中英文 OCR 通过。
- 单页 PPT 完成 LibreOffice 转换、Poppler 渲染、OCR、视觉模型核验、Embedding 入库和原页预览；返回 `visual-slides-v1`、质量 `verified`、3 个知识片段。
- 笔记 worker 处理完成，生成正式资产并确认；本次合成样例触发产品原有的“资料摘录”回退，不能将此样例当成自由生成笔记质量通过的证据。
- 正式试卷 worker 使用原配置的真实 DeepSeek 提供商完成 1 道选择题的生成和校验。
- 导出 worker 完成 Markdown、DOCX、PDF 三种格式；PDF 的中文文本提取、页面渲染与视觉检查通过。
- 原有主账号的 5 门课程可读取，抽查 2 份原资料下载及 1 份已迁移课件原页预览成功，笔记和试卷列表接口正常。
- 5 个应用单元无自动重启；验收期间应用 slice 峰值约 880 MiB，整机仍有约 2.4 GiB 可用内存，Swap 未使用。这是小样本部署验收结果，未做多人并发容量测试。

部署时修正了 `src/final_review/llm.py` 的笔记参数提取提示：未指定数组必须返回 `[]`，可空标量返回 `null`，字符串范围返回空串。修正前 Qwen 把 `source_types` 返回为 `null`，导致笔记入口 502；修正后实际入队及 worker 链路通过。服务器保留修改前源码备份，本机保留同一修改和补丁清单，尚未提交 Git。

本机证据与部署工具目录：`D:/final_review/backups/deploy-20261028/`。其中 `verification/` 保存无密钥的检查报告、合成样例导出和页面 PNG；`deployed-manifest.json` 记录原发布包哈希、Git 基准和本次源码补丁哈希。运行配置及密码不在该清单中。

## 运维命令

以下命令在服务器执行：

```bash
systemctl status nginx final-review-api final-review-material-worker final-review-note-worker final-review-quiz-worker final-review-export-worker --no-pager
journalctl -u final-review-api -n 100 --no-pager
journalctl -u final-review-material-worker -n 100 --no-pager
journalctl -u final-review-note-worker -n 100 --no-pager
journalctl -u final-review-quiz-worker -n 100 --no-pager
journalctl -u final-review-export-worker -n 100 --no-pager
systemctl list-timers final-review-certbot-renew.timer --no-pager
curl --resolve 124.222.254.31:443:127.0.0.1 https://124.222.254.31/health
```

修改运行配置或升级代码后，先检查数据库备份与迁移，再重启对应单元：

```bash
systemctl restart final-review-api final-review-material-worker final-review-note-worker final-review-quiz-worker final-review-export-worker
```

API `ExecStartPre` 执行版本化迁移。`systemctl active` 仅表示进程已启动，重启后应等待 `/health` 正常再发送业务请求；`/health` 本身不证明模型可用。

## 备份与下线

部署前原远端库备份：`/root/final-review-deploy/backups/final_review-before-20261028.dump`；验收后新库备份：`/root/final-review-deploy/backups/final_review_prod-after-20261028.dump`；本机迁移源备份：`D:/final_review/backups/deploy-20261028/database.dump`。修改前 Nginx 默认站点和 `llm.py` 也保存在服务器备份目录。最终核对原远端库仍为 1 个用户、2 门课程、3 条资料、11 条迁移记录，与部署前一致。

需要下线本次应用时，保留数据库和文件执行：

```bash
systemctl stop final-review-api final-review-material-worker final-review-note-worker final-review-quiz-worker final-review-export-worker nginx
```

这不是数据恢复命令。升级后若数据库结构发生变化，代码与数据库应按同一版本的备份成套恢复；不要用删除数据库或清空 `/srv/final-review` 的方式尝试回退。
