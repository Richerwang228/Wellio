# CopilotKit / OpenRouter 集成交付

日期：2026-09-12。目标是 macOS Apple Silicon 本地演示，代码先于打包。

## 已实现

React 官方 CopilotKit Provider 和 SDK → 前端同源代理 → 本仓库 Node CopilotRuntime / BuiltInAgent factory → FastAPI 固定内部 RPC → PostgreSQL / Exa。Node 是唯一模型执行层，数据库是唯一持久业务和消息源。默认模型 `deepseek/deepseek-v4.1-flash`，固定 OpenRouter Chat Completions 接口；服务端配置 `OPENROUTER_API_KEY`。

对话、自动检查、提案生成均使用新运行路径；工具写入需来源授权、活动租约和版本校验，写入后强制重读。用户 Apply、Undo 继续使用明确业务 API。已覆盖取消后禁止迟到写入、同请求重放、不同会话隔离以及进程重启恢复。

Exa 使用 Python SDK 2.14.0、auto + highlights。训练动作目录由 FastAPI 提供，模型不猜动作 ID。当前八项工具不包含专业知识库检索；其他任务的 RAG 代码独立验收后再合入。

## 清理

前端旧 TypeScript 领域服务、SQLite、旧模型与搜索执行器、对应历史实现测试和 seed 导出脚本已移除；只保留两个服务端代理。Python 没有 PydanticAI 或 Python AG-UI 模型执行器。旧供应商模型配置已移除。旧业务 API 单容器 Dockerfile 已移除，Compose 仅作为可选本地 PostgreSQL 启动方式。退役 `/api/chat` 只返回迁移错误，不能运行模型。

## 验证证据

- FastAPI / PostgreSQL：247 项 pytest 通过。
- Node Runtime：34 项测试通过、TypeScript 构建通过、npm audit 0 漏洞。
- React 客户端：99 项测试、类型检查和隔离构建通过；npm audit 0 漏洞。
- Playwright：34 项浏览器测试通过，涵盖中英文、小屏、恢复检查、显式 Apply、训练生命周期和 SSE。
- 完整生产栈：两次重启持久化烟测通过。
- 真实 CopilotRuntime / BuiltInAgent → FastAPI → PostgreSQL HTTP 测试通过：上下文工具、保存回复、半份餐食修改、写后重读、Undo、会话隔离、重放、断开取消及进程重启。
- 测试生命周期：19 项客户端回执测试复跑通过，确认退出后没有新增遗留 PostgreSQL；本次发现的孤立测试实例已停止清理。
- Provider 测试经过真实 OpenAI chat provider 的受控 SSE（工具调用→工具结果→最终结构化输出），验证 OpenRouter 请求地址、Bearer header 和 HTTP 429 不自动重试。

上述模型与搜索测试使用受控传输 / 官方 SDK 测试模型，没有调用付费 OpenRouter 或 Exa。真实模型效果、图片理解和搜索联调尚需 API 密钥；这不等于真实 API 联调已通过。尚未制作 macOS 安装包。

## 复现

```sh
uv run --frozen pytest -q
npm --prefix agent-runtime ci
npm --prefix agent-runtime test
npm --prefix agent-runtime run build
uv run --frozen python tests/copilot_smoke.py /absolute/path/to/Wellio-Frontend/wellio-app
```

最后一个命令要求前端已在独立目录构建，避免覆盖运行中展示服务的 `.output`。前后端保持独立仓库；API、服务启动与环境变量见各 README。

OpenRouter 官方：[模型](https://openrouter.ai/deepseek/deepseek-v4.1-flash)、[接入](https://openrouter.ai/docs/quickstart)。2026-09-12 核实 exact ID 与 tools/tool_choice 支持，不把公开目录查询算作真实推理调用。

后续：2026-09-12 已完成基础真实 API 联调及相关修复，最新证据见 [真实 API 验证](LIVE_API_VERIFICATION.md)。上文受控测试记录为最初集成基线。
