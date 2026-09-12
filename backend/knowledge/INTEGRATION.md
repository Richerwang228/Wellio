# 演示知识检索接口

本模块独立于 CopilotKit 基线变更；现已注册 `search_expert_knowledge`，由 Node BuiltInAgent 调用 FastAPI 内部工具 RPC。真实向量已生成并入库，4/4 演示检索通过；详见 [验收报告](IMPLEMENTATION_REPORT.md)。

## 模型

固定 `qwen/qwen3-embedding-4b`，原生 2560 维，OpenRouter `/api/v1/embeddings`。查询按 Qwen 官方格式添加检索任务说明；资料输入为原文标题、章节和片段。统一归一化，PostgreSQL 用余弦距离精确查询，最多返回 6 片、同篇最多 2 片。

选择依据：[OpenRouter 模型页](https://openrouter.ai/qwen/qwen3-embedding-4b)、[Qwen 官方模型说明](https://huggingface.co/Qwen/Qwen3-Embedding-4B)。2026-09-12 页面标价 $0.02 / 百万输入 token，供应商 P50 约 0.22 秒；不是本机实际端到端延迟承诺。没有自动改用其他向量模型，避免查询和文档向量不兼容。

本次只选 8 篇、39 个片段，见 `demo_selection.json`。其余已下载资料留存，不全部向量化。未增加重排模型、管理后台或定时采集。

## 配置与入库

后端本地 `.env` 中设置 `OPENROUTER_API_KEY`、`DATABASE_URL`；不要覆盖已有 `.env`。密钥、数据库、原文和向量均留在被 Git 忽略的本地目录。

PostgreSQL 需要 pgvector 扩展。macOS Homebrew 可安装 `pgvector`，并确认扩展对应正在运行的 PostgreSQL 主版本。普通 `postgres:18` Docker 镜像没有预装该扩展；现有业务 Compose 保持不变，使用知识模块前需要在目标数据库环境安装它。

在后端目录运行：

```sh
uv run python -m wellio.knowledge_ingest prepare
uv run python -m wellio.knowledge_ingest embed
uv run python -m wellio.knowledge_ingest import
uv run python -m wellio.knowledge_ingest evaluate
uv run python -m wellio.knowledge_ingest activate
```

- `prepare`：校验原文 hash、片段定位、选材范围，生成内容寻址的 release。
- `embed`：每批最多 8 个输入，结果写入本地缓存，中断后可继续；模型错误、错维度、零向量均不会冒充成功。
- `import`：显式创建独立 `knowledge` schema，事务导入完整 release；原文与向量一起保存，重复执行不重复写入。不会从业务启动自动建知识表。
- `evaluate`：用四个中文问题测试真实语义召回，记录命中文档和延迟；未通过返回非零状态。
- `activate`：要求同一 release 的召回检查通过，再切换唯一 active 版本；staging 和 retired 不对普通查询开放。

`wellio/data/knowledge_v1.sql` 是实际使用的 DDL；原 `postgresql_schema.proposed.sql` 仅保留历史提案，不要混合执行。这个小规模实现未采用提案中的多模型表或混合关键词排序。

切换 embedding 模型须重建整批文档向量并重新测试，不可只替换查询模型。当前模型与维度固定在 `wellio/knowledge.py`，防止部署环境无意混用。

## HTTP 接口

`POST /api/knowledge/search`，沿用现有签名会话和同源校验。先调用 `GET /api/state` 获取会话 cookie。

```json
{"query":"昨晚只睡四小时，今天训练会受什么影响？","topK":4}
```

可选 `topic` 是来源清单中的标签，如 `sleep`、`protein`、`time_constraints`，用于限定召回范围。中文提问无需先翻译。

响应包含 `knowledgeVersion`、`model`、`results`。每片返回 `chunkId`、`documentId`、标题、机构、`sourceUrl`、原文、章节、字符位置、文档版本、语言、人群、限制与相似度。相似度仅用于排序，不是医疗置信度。

未导入或未激活返回 `503 KNOWLEDGE_NOT_READY`；缺密钥、供应商错误和超时返回明确错误码；没有匹配的主题返回 `no_results`。不会退回模型自由编造。进程内仅缓存最近 128 个查询的向量，仍每次查当前 active release；不把查询保存到业务账本。

Swagger `/docs` 包含请求 schema。独立 HTTP 查询结果只是候选证据，不签发 Agent 回执；Agent 使用同一 `KnowledgeService`，通过已有的内部工具 RPC 调用，不绕回公开 HTTP 或新增前端检索代理。

## Agent 工具与引用

生产入口 `wellio.main:application` 注入知识服务，向 BuiltInAgent 注册第九个工具：

```json
{"name":"search_expert_knowledge","input":{"query":"睡眠不足会怎样影响今天的训练？"}}
```

执行器先强制 `get_day_context`，再强制检索；检索 query 使用当前问题，不传整份用户记录。演示库规模很小，Agent 工具不接受主题筛选，避免模型猜错标签导致空召回；独立 HTTP 接口仍保留可选 topic。

- 每次检索最多返回 4 片，保存当前 run、contextReadId、知识版本及来源的持久回执。
- 最终回答和 `propose_workout` 必须提供最新 `evidenceReadId` 与 1–4 个 `evidenceChunkIds`；后端拒绝伪造 ID、跨轮回执、过期上下文或已切换的版本。
- 状态写入后须重读上下文并重新检索。空结果或检索失败不能授权发布建议，每轮最多 4 次检索尝试。
- 后端从已保存来源添加参考链接；Agent 对话显示“查阅专业资料”步骤和链接。Today 摘要与回答共用证据回执。
- 已同步后端实测修复 `25b9fbe`：Node 最长 115 秒、Python 120 秒，Node 还受剩余租约限制；知识开启最多 8 个模型步骤（未启用为 6 步）。原 20 秒在新增真实检索链路中不足，延迟结果见验收报告。取消或超时会终止公开流并保存失败，即使供应商流没有及时结束。

聊天使用官方 OpenRouter provider，关闭额外 reasoning 并限制输出 4096 token。工具步骤不强制 JSON response_format，最终文本经过严格 JSON + Zod 解析，保留五字段证据契约；服务器验收后才显示。

这个校验确保“确实检索过且引用来自本轮结果”，不会自动判断每句建议是否被论文支持；模型仍须核对适用人群和限制。知识库尚未导入时，业务服务可启动，但 Agent 专业建议发布会失败，不静默回退为无依据回答。

## 本机持久数据库

本次若没有已有开发 `DATABASE_URL`，创建的是 `wellio-backend/.data/knowledge-postgres/data`，仅监听 `127.0.0.1:55432`，使用随机密码；连接串已保存到本地 `.env`。不是已有展示服务的数据库。数据目录会保留，不会上传 GitHub。

本机启停（从后端目录）：

```sh
/opt/homebrew/opt/postgresql@18/bin/pg_ctl -D "$PWD/.data/knowledge-postgres/data" -l "$PWD/.data/knowledge-postgres/server.log" -w start
uv run uvicorn wellio.main:application --factory --host 127.0.0.1 --port 8000 --no-proxy-headers --env-file .env
# 结束 uvicorn 后停止本次数据库；数据保留
/opt/homebrew/opt/postgresql@18/bin/pg_ctl -D "$PWD/.data/knowledge-postgres/data" -m fast -w stop
```

现有统一启动脚本从进程环境读取变量；如继续使用它，需由启动器加载 `.env`，不能认为该文件会自动传给所有子服务。

## 验证

```sh
uv run pytest tests/test_agent_knowledge.py tests/test_agent_service.py tests/test_knowledge.py tests/test_http.py -q
cd agent-runtime && npm test && npm run typecheck
```

自动测试只使用临时 PostgreSQL 和明确标记的假向量来验证存取/排序机制，不读取开发 `.env` 或调用付费模型；真实模型召回另由 `evaluate` 验证。通过召回测试不代表医疗正确性审核或 Agent 端到端验收。

真实 Agent 联调（会调用付费 OpenRouter；只从 `.env` 读取 API key，测试使用独立临时 PostgreSQL）：

```sh
WELLIO_KNOWLEDGE_LIVE=1 uv run python tests/knowledge_agent_smoke.py
```

此脚本复用本地已生成的向量，临时编译 Node runtime，验证上下文、知识工具、回答来源及 Today 回执，结束后停止子服务和临时数据库。
