# Final Review Frontend

React + TypeScript + Vite 前端。默认打开“我的课程”卡片列表，可搜索、排序并筛选课程。点击“进入课程”默认打开资料页，侧栏才显示该课程的“资料 / 笔记 / 测试 / AI 辅导”；返回“我的课程”可切换课程。课程卡片的“•••”入口保留课程编辑、归档、删除恢复及多场考试管理。AI 辅导页内提供历史对话、新建对话与重命名。笔记继续支持草稿恢复、编辑、引用、版本确认和导出。

实现边界：模拟测验和错题页已有真实接口；学习报告仍为示例展示，未连接报告 API。错题、报告等入口本轮暂不显示，原有页面与旧链接保留。`docs/ui-v2` 是独立模拟原型。重新开发前可查 [完整现状评估](../docs/current-api-and-capability-audit-2026-10-26.md)。

路由：`#/courses` 为课程列表，`#/courses/:courseId/materials|notes|quizzes|tutor` 为课程内页面；笔记详情及对话可继续追加内容 ID。原有 `#note/...`、`#materials/...`、`#chat/...` 链接仍兼容。侧栏沿用现有蓝色，展开 224px / 收起 64px，手机端使用抽屉导航。

启动：

    cd D:\final_review\final-review\frontend
    npm install
    npm run dev

资料上传依赖后端资料 worker。在仓库根目录另开终端运行
`uv run python -m final_review.material_jobs`，或使用 `docker compose up --build -d`
同时启动 API 与 worker。上传后页面显示排队、处理阶段及失败原因，刷新会从服务端恢复状态。

终端会显示本地访问地址；通常为 http://127.0.0.1:5173。日常前端将 `/api` 转发到运行在 8080 端口的后端。

生产构建：

    npm run build

UI 组件：前端已安装 Ant Design（`antd`）。应用入口 `src/main.tsx` 通过 `ConfigProvider` 设置中文和项目主题色；可在 React 组件中按需引入，例如 `import { Button, Table } from "antd"`。课程页的“新建课程”按钮是现有用法示例。现有页面可以逐步替换组件，不需要克隆 Ant Design 源码仓库。

前端端到端测试（需要本机 Chrome，命令会启动临时内存后端和 Vite）：

    npm run test:e2e

本轮课程导航回归：`npx playwright test course-navigation.spec.ts`，覆盖课程切换、路由刷新与历史、历史对话隔离、手机导航、课程管理和笔记编辑保护。

若 Windows 打包提示 esbuild 临时文件删除被拒绝，可把当前终端的 `TEMP`、`TMP` 指向工作区内独立的可写目录后重试；无需修改构建配置。
