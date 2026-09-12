# Wellio frontend

TanStack Start + React + TypeScript，保留现有 Wellio 界面。

前端仓库：[Wellio-Frontend](https://github.com/xinyunfan2-dev/Wellio-Frontend)。业务与 Agent 服务位于独立[后端仓库](https://github.com/xinyunfan2-dev/Wellio-Backend)：FastAPI + PostgreSQL + Exa，以及 CopilotKit BuiltInAgent Node 子包。

## 开发与运行

要求 Node >=22.15、npm >=11。macOS Apple Silicon 为演示目标。先按后端 README 启动 PostgreSQL、FastAPI 和 Node Agent，再运行：

```sh
npm ci
npm run dev
```

浏览器打开 `http://127.0.0.1:3100`。构建并运行：

```sh
npm run typecheck
npm run build
npm start
```

业务代理 `WELLIO_API_BASE_URL` 默认 `http://127.0.0.1:8000`；Agent 代理 `WELLIO_AGENT_BASE_URL` 默认 `http://127.0.0.1:8001`。浏览器只访问同源 `/api/*`。后端 `WELLIO_PUBLIC_ORIGIN` 包含实际前端来源。模型密钥、Exa 密钥、数据库 URL 和内部服务 token 都在后端配置，不使用 `VITE_` 前缀。

需要显式加载前端 `.env` 时：

```sh
node --env-file=.env ./node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3100
# 构建后
node --env-file=.env .output/server/index.mjs
```

两个仓库在本机运行时，可用 `npm run dev:stack` / `npm run start:stack` 统一管理三个服务。要求 PostgreSQL 已运行、`DATABASE_URL` 已配置、已安装 `uv`、后端 `agent-runtime` 已构建。后端默认路径 `../wellio-backend`，可用 `WELLIO_BACKEND_DIR` 指定。启动器生成本次内部服务 token，退出时停止自己的子进程，不停止外部 PostgreSQL。

不要在运行中的生产 `.output` 上重新构建；测试或新版本先使用独立目录，再整体切换服务，避免页面缓存的静态资源与服务端版本不一致。

## 对话与业务边界

前端通过 CopilotKit React SDK 发现 `wellio` Agent，真实调用 `runAgent`；采用自定义 UI，不依赖 CopilotKit 云账号。Node BuiltInAgent 运行模型和工具循环，FastAPI 验证来源、运行状态、权限、版本与事务，PostgreSQL 保存事实及回执。模型与 Exa 仍需要外网 API。

`src/server` 仅保留两个同源代理。旧 TypeScript 领域服务、SQLite、Firecrawl 和旧 Agent 实现及对应依赖已移除；历史行为可查 Git 基线。生产没有第二条模型执行路径。

业务动作仍通过 `/api/actions`；生成提案通过 `/api/copilotkit/proposal`，成功候选仍需显式 Apply。对话使用 AG-UI SSE 与已校验的 Wellio 事件，保持会话、请求及 reset epoch 隔离；停止会取消 SDK 并等待本地收尾。最终显示与保存结果来自 FastAPI 快照，Markdown 不能直接记账。

未配置模型时 `capabilities.agent=false`，不生成假回复；业务记录仍可使用。当前知识检索是否开放以服务端实际工具清单为准，不把菜单搜索当专业知识库。

## 验证

```sh
npm test
npm run typecheck
npm run test:e2e
npm run test:backend:production
```

前端测试覆盖真实 SDK 事件处理、取消、UI 状态和 PostgreSQL 重试回执。数据库测试由后端 pytest 负责，未保留第二套 SQLite 测试后端。测试需要后端 checkout、uv 和本机 PostgreSQL 测试二进制；可配置 `WELLIO_BACKEND_DIR`。浏览器需要 `npx playwright install chromium` 或 `PLAYWRIGHT_EXECUTABLE_PATH`。完整 HTTP Agent 联调命令见后端 README。

`VITE_WELLIO_PREVIEW=1 npm run dev` 仅用于明确的视觉预览，不包含数据库、AI 或搜索；生产忽略该开关。
