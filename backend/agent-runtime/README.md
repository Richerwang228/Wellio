# Wellio Agent Runtime

这是 Wellio-Backend 内的 Node 子包。CopilotRuntime 将请求交给 BuiltInAgent 的 `type: 'aisdk'` factory；真实 AI SDK 6 执行工具循环，FastAPI 验证会话、原始意图、版本和业务事务。每次运行创建独立 AgentRunner，只保存活动取消句柄，不保存共享对话历史。

## 本机运行

使用 Node.js 22.15+、npm 11+，先启动 PostgreSQL 与 FastAPI。

```sh
npm ci
npm run build
WELLIO_API_BASE_URL=http://127.0.0.1:8000 \
WELLIO_AGENT_PORT=8001 \
WELLIO_AGENT_TOKEN='<与 FastAPI 相同的本机凭证>' \
node dist/server.js
```

模型使用 `OPENROUTER_API_KEY`，固定请求 `https://openrouter.ai/api/v1/chat/completions`。`WELLIO_AI_MODEL` 默认 `deepseek/deepseek-v4.1-flash`，可显式指定其他 OpenRouter 模型 ID。缺少密钥时 `/info` 和 `/healthz` 可用，模型运行返回 503；不读取其他供应商的密钥，不自动切换模型，没有生产假模型开关。

使用 OpenRouter 官方 AI SDK provider 2.10.0（兼容 AI SDK 6），显式关闭该模型默认的高强度推理，每次生成最多 4096 tokens。Node 每轮最多 115 秒、知识开启 8 步（未启用为 6 步）、请求不自动重试；FastAPI 的 120 秒租约与 Node 的剩余租约检查共同限制运行，取消仍即时生效。首步强制 `get_day_context`，写入或提案后按服务端 `contextRequired` 再读。正文、推理和工具参数不直接发布；工具循环不附加 JSON response_format；最终 JSON（知识开启为五字段） 经本地 Zod 校验并通过 FastAPI `/finish` 后，才转换为 AG-UI 正文及 `CUSTOM wellio` 事件。已经保存的业务事实不因后续模型失败回滚。

接口：`GET /healthz`、`GET /api/copilotkit/info`、`POST /api/copilotkit/agent/wellio/run`、`POST /api/copilotkit/agent/wellio/stop/:threadId`、`POST /api/copilotkit/proposal`。浏览器使用同源前端代理。内部 RPC 仅调用 `open/tool/finish/cancel/status`，传递原签名 Cookie 与 Origin，并使用服务器凭证；模型参数无法覆盖身份。

主提示词版本为 `wellio-prompt/0.1.0`，运行正文位于 `src/prompts/wellio.md`，源自项目 `AGENT_PROMPT_SPEC.md` 第 2 节，工具说明源自第 4 节。生产入口现注册八项业务工具及第九项 `search_expert_knowledge`。执行器强制先读上下文再检索，后端校验 `evidenceReadId` 和 `evidenceChunkIds`，并用真实来源添加参考链接。参见 [知识集成说明](../knowledge/INTEGRATION.md)。

## 验证

```sh
npm test
npm run typecheck
npm audit
```

离线测试使用真实 `MockLanguageModelV3`、AI SDK 工具循环与 CopilotRuntime dispatcher；不访问外部模型。全栈测试专用进程为 `node tests/fixture-server.mjs`，读取同样的内部 RPC 配置并打印 `{url}`。包含 `WAIT_FOR_CANCEL` 的测试输入会在读取上下文后等待真实取消；生产入口不会加载这个测试模型。

依赖隔离锁定 CopilotKit 1.71.1、AG-UI 0.0.59、AI SDK 6 和 OpenRouter provider 2.10.0。有限 overrides 修复传递依赖 qs 与旧 provider-utils 的 undici；不与前端 AI SDK 版本混用。

真实凭据联调使用后端 `scripts/live_smoke.py --live`，不会被 pytest 自动运行。它只发送隔离的演示数据和测试菜单图片，使用临时 PostgreSQL，并在结束时停止自己的三个服务。
