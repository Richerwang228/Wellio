# Wellio Backend

Python 3.12+ / FastAPI / PostgreSQL / Exa。后端仓库：[Wellio-Backend](https://github.com/xinyunfan2-dev/Wellio-Backend)，前端独立仓库：[Wellio](https://github.com/xinyunfan2-dev/Wellio-Frontend)。FastAPI 业务服务可独立启动；完整对话还需本仓库 `agent-runtime/` 中的 CopilotKit BuiltInAgent Node 服务。

## 本地启动

安装 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和 PostgreSQL 16+，然后：

```sh
uv sync --frozen
cp .env.example .env
# 修改 .env 中 DATABASE_URL 为你自己的 PostgreSQL 连接
uv run uvicorn wellio.main:application --factory --host 127.0.0.1 --port 8000 --no-proxy-headers --env-file .env
```

已有 PostgreSQL 的队员可以直接复用自己的开发数据库。也可以通过 Docker 创建本项目开发数据库：

```sh
docker compose up -d postgres
# PostgreSQL 就绪后使用上面的 uvicorn 命令
```

Compose 仅是可选 PostgreSQL 启动方式，不包含应用服务。目标 macOS 演示栈使用原生 Node / Python 进程；旧业务 API 单容器启动文件已移除。默认数据库密码仅为本地开发示例；日常 `docker compose down` 不删除数据库 volume。

服务启动时在 PostgreSQL 事务中检查并应用版本化建表迁移，多进程同时启动有数据库迁移锁。`DATABASE_URL` 必填，不会回退 SQLite。业务快照 `schemaVersion=4` 与数据库结构迁移版本分别管理。

验证服务：

```sh
curl http://127.0.0.1:8000/healthz
curl -c /tmp/wellio-cookie.txt http://127.0.0.1:8000/api/state
```

Swagger 文档：`http://127.0.0.1:8000/docs`，OpenAPI：`/openapi.json`。`/api/actions` 展示按 `kind` 区分的请求类型。

## 与前端连接

前端 `WELLIO_API_BASE_URL=http://127.0.0.1:8000`，浏览器仍请求前端同源 `/api/*`。后端 `WELLIO_PUBLIC_ORIGIN` 填实际前端来源，多个来源用逗号分隔。Cookie、Origin/Referer 与响应状态经前端代理保留。

| 接口 | 用途 |
| --- | --- |
| `GET /api/state` | 创建或恢复签名会话，返回已保存快照 |
| `POST /api/actions` | 语言、Reset、训练生命周期、Apply/Start、撤销等严格动作 |
| `POST /api/attachments` | multipart `file` + `purpose=food/menu`，私有图片上传 |
| `GET /api/attachments/{id}` | 读取当前会话和 epoch 的原始图片 |
| `POST /api/chat` | 已退役，未配置 Agent 时 503，配置后 410；对话改用前端 `/api/copilotkit/*` |

所有业务写入沿用 camelCase、版本冲突、请求去重和回执格式。数据库事务把快照、回执、授权消费、餐食操作链同时提交；跨进程请求在同一会话上顺序执行。失败不会留下部分餐食变更。Reset 只影响当前会话，旧 epoch 请求不能写回。

## Exa 搜索

设置后端 `EXA_API_KEY`，依赖锁定 `exa-py==2.14.0`。默认 `type="auto"`、`contents={"highlights": True}`。返回实际标题、URL、摘录与读取时间，菜单保留 `priceStatus="unknown"`，不从缺失证据编造价格或营养。未配置密钥时显式返回 `SEARCH_NOT_CONFIGURED`。内置 Agent 经受控 RPC 调用搜索工具。

搜索是后端工具服务，本阶段不新增公开付费搜索 HTTP 接口。Python 服务内可调用：

```python
from wellio.search import ExaSearchService

search = ExaSearchService()  # 从 EXA_API_KEY 读取
try:
    result = await search.search({"query": "Hong Kong restaurant menu", "numResults": 10})
    menu = await search.search_restaurant_menu({"restaurant": "餐厅名", "city": "Hong Kong"})
finally:
    await search.close()
```

规范：[Exa coding-agent search API guide](https://docs.exa.ai/reference/search-api-guide-for-coding-agents)。测试使用受控 SDK 传输，不消耗真实搜索配额；真实 Exa 调用需要部署环境的密钥。

## 代码与职责

- `wellio/app.py`：FastAPI HTTP、会话和同源校验。
- `wellio/database.py`、`migrate.py`、`data/001_initial.sql`：PostgreSQL、事务和版本化迁移。
- `workouts.py`、`read_services.py`：训练、提案、排期和营养汇总。
- `meals.py`、`authorization.py`、`conditions.py`：明确用户来源授权、餐食操作与单调版本撤销。
- `runtime.py`：持久化检查账本和过期运行恢复，不执行模型。
- `attachments.py`：图片完整解码、会话/epoch 隔离与私有权限。
- `search.py`：Exa 检索服务。

图片支持 JPEG、PNG、WebP，最多 10 MiB、4000 万解码像素。保存原始字节与校验和；附件根目录需要跨重启持久化。部署多个 API 实例时应挂载同一个受控附件卷或另外接对象存储。

演示数据明确标记 `demo_fixture_v1`，不是用户设备采集数据。PostgreSQL 从新数据库开始；原工作区旧 SQLite 文件不被本服务读取或改写，旧数据导入不是隐式启动操作。

## FastAPI 基线验证（CopilotKit 接入前）

后端全量 **222/222** 通过，数据库实测 PostgreSQL 18.6。包含原生 HTTP、事务/并发/回执、授权/撤销、图片隔离和 Exa 35 项受控 SDK 测试。前端 34 项浏览器测试与完整服务重启两次后的持久化烟测通过。Python wheel 构建及 Compose 语法检查通过；未运行 Docker 容器或真实付费搜索。

## 测试

```sh
uv run pytest -q
```

测试默认寻找本机 PostgreSQL `initdb` / `pg_ctl` / `postgres`，创建独立临时实例与独立测试 schema，结束后清理。可通过 `WELLIO_PG_BIN` 指定二进制目录。测试不会使用开发 `.env` 的数据库或真实 Exa 密钥。也可显式提供 `WELLIO_TEST_DATABASE_URL` 指向专用测试数据库，测试仅创建并清理自己的临时 schema。

需要前端构建产物的完整生产重启验证：

```sh
# 在前端先运行 npm run build，然后从本仓库运行
uv run python tests/production_smoke.py /absolute/path/to/Wellio-Frontend/wellio-app
```



## CopilotKit 内置 Agent

模型与工具循环只在 `agent-runtime/` 执行。FastAPI 的 `agent_service.py` 负责运行接纳、固定工具清单、授权和回执，不是 Python 模型执行器；不存在 PydanticAI 或 Python AG-UI 转接。Node 通过签名会话 Cookie 与私有 `WELLIO_AGENT_TOKEN` 调用 `/internal/agent/{open,tool,finish,cancel,status}`，浏览器不能直接代理这些接口。

原生本地开发先准备 PostgreSQL 与本文件前述 Python 依赖，然后构建 Node 服务：

```sh
cd agent-runtime
npm ci
npm run build
```

Node >=22.15、npm >=11。按 `.env.example` 在两个后端进程配置同一个私有 token；模型通过 OpenRouter 的 `https://openrouter.ai/api/v1/chat/completions` 接入，仅需配置服务端 `OPENROUTER_API_KEY`；默认模型为 `deepseek/deepseek-v4.1-flash`，可用 `WELLIO_AI_MODEL` 覆盖。没有密钥时明确不可用。不要把真实密钥提交 Git。

分别运行 FastAPI（8000）与 Node（8001），并允许前端实际来源：

```sh
# 后端仓库根目录；uvicorn 显式加载 .env
uv run --frozen uvicorn wellio.main:application --factory --env-file .env --host 127.0.0.1 --port 8000 --no-proxy-headers
# 另一个终端，仍在后端仓库根目录
node --env-file=.env agent-runtime/dist/server.js
```

也可使用前端仓库的统一 stack 启动脚本，继承相同的后端环境变量；该脚本生成内部 token 并管理本次三个应用进程。PostgreSQL 与附件目录独立持久化。完整应用部署目标是 macOS Apple Silicon 本机运行，不需要云服务器、云数据库或 CopilotKit 云账户。双击安装包属于代码验收后的独立交付，当前未打包。

Node 的独立版本锁避免旧 AI SDK 7 与 CopilotKit 内部 AI SDK 6 混用。领域数据、已成功的操作和原始输入保存在 PostgreSQL；Node 不保存第二份会话历史。停止或失败会终结运行，已保存的操作不会被伪装成失败或重复执行。生成内容须完成工具、上下文和输出校验后才发布；训练仍需用户 Apply。

新增验证（两仓库构建完成后）：

```sh
uv run --frozen pytest -q
npm --prefix agent-runtime test
uv run --frozen python tests/copilot_smoke.py /absolute/path/to/Wellio-Frontend/wellio-app
```

全链路测试启动真实 CopilotKit BuiltInAgent、FastAPI 与临时 PostgreSQL，用官方 SDK 测试模型替代外部模型；测试包括对话落盘、上下文工具、会话隔离、重放、断开取消和进程重启。受控测试不能代替真实模型效果、图片识别质量或付费 Exa 联调。

## 知识向量检索（独立功能）

`POST /api/knowledge/search`：带当前会话查询营养、睡眠和训练依据，返回原文与来源。使用 OpenRouter Qwen3 Embedding 4B + PostgreSQL/pgvector；配置、显式入库及验证见 [knowledge/INTEGRATION.md](knowledge/INTEGRATION.md)。已注册为 CopilotKit 的 `search_expert_knowledge` 工具；Agent 先读上下文和知识，再提交带有效回执的回答或训练候选，后端添加来源链接。

## 显式真实 API 联调

普通测试不会读取真实密钥。需要真实联调时，先在本机 `.env` 配置 `OPENROUTER_API_KEY`、`EXA_API_KEY`，然后显式运行（会产生 API 用量）：

```sh
uv run --frozen python scripts/live_smoke.py --live --env-file .env --frontend /absolute/path/to/isolated-frontend-build --scenario all
```

测试使用临时 PostgreSQL 和演示数据，不连接 `.env` 中的业务数据库。模型使用官方 OpenRouter provider（AI SDK 6 兼容版本），关闭默认高强度推理并限制单次输出 4096 tokens；完整工具循环上限 115 秒，FastAPI 租约 120 秒。原始模型参数、密钥和对话不写运行日志，日志只记录 token 数量、结束原因和错误类型。
