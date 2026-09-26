# Final Review Frontend

React + TypeScript + Vite 前端。默认打开“课程与考试”工作台，可创建、编辑、归档和恢复课程，在一门课程下维护多场考试，并预览删除影响后确认删除。其他入口包括 AI 对话、资料、模拟测验和学习报告。

启动：

    cd D:\final_review\final-review\frontend
    npm install
    npm run dev

终端会显示本地访问地址；通常为 http://127.0.0.1:5173。日常前端将 `/api` 转发到运行在 8080 端口的后端。

生产构建：

    npm run build

课程与考试端到端测试（需要本机 Chrome，命令会启动临时内存后端和 Vite）：

    npm run test:e2e
