# 向量化与接口验收（2026-09-12）

- 模型：`qwen/qwen3-embedding-4b`，2560 维。
- Release：`demo-6c1a7913caf33172c82b3482`，8 篇、39 个真实向量。
- 本机 PostgreSQL 18.6 + pgvector 0.8.6；已保存并激活演示知识版本。
- 独立临时库测试：知识模块 5 项 + HTTP 回归 28 项，共 33 项通过。
- 真实模型检索：4/4 问题首位命中预期文档。

| 问题 | 首位文档 | 耗时 |
| --- | --- | --- |
| 昨晚只睡了四小时，今天训练会受什么影响？ | `sleep-loss-performance` | 2151 ms |
| 今天只剩十五分钟，怎么精简力量训练？ | `time-efficient-training` | 1347 ms |
| 增肌训练后需要注意蛋白质吗？ | `bda-sport-exercise-nutrition` | 1160 ms |
| 晚上喝咖啡和吃太晚会影响睡眠吗？ | `nhlbi-sleep-habits` | 1364 ms |

HTTP 路由实测通过：携带签名会话，返回原文、来源链接与版本；首查 1741 ms，缓存查询 24 ms。测试使用 FastAPI ASGI TestClient、真实 OpenRouter 和本次独立创建的本地持久库，不是前端浏览器验收。

向量批处理曾遇到供应商错误，复跑只补齐剩余片段；已完成的 32 个向量得到复用。平台返回模型名称含大小写差异，已验证为同一 Qwen3 4B 模型并加入回归测试。

## Agent 工具接入验收

已新增 `search_expert_knowledge` 第九个工具。Node 执行器强制上下文→知识检索；最终回答和训练候选必须带有效证据回执及返回的片段 ID。FastAPI 持久绑定 run/context/知识版本，拒绝假引用、跨轮引用、过期上下文、版本切换后的引用及空结果。后端添加可点击来源，对话展示“查阅专业资料”步骤；Today 摘要保存同一证据回执。

已将后端独立提交 `25b9fbe` 的 OpenRouter 兼容与实际操作校验合入本地开发目录，保留 RAG：官方 provider 2.10.0、关闭额外 reasoning、4096 输出 token、最终 JSON + Zod 校验。工具步骤不强制 JSON response_format。Node 最长 115 秒，受 Python 120 秒租约剩余时间约束；知识开启最多 8 步，未启用保持 6 步。新增步骤用于训练候选之后重读上下文、重新检索和最终回答，不跳过证据检查。

真实联调使用默认 `deepseek/deepseek-v4.1-flash`、真实 Qwen embedding、39 个已有向量，以及独立临时 PostgreSQL：上下文成功→知识检索成功→回答完成→Today 与回答证据回执一致。引用 `sleep-loss-performance` 与 `nhlbi-sleep-habits`，最终一次 52,949 ms。记录保存在本地 release 的 `agent-smoke.json`。这是 Node HTTP / FastAPI 实测，没有用前端浏览器操作，也不是医学内容准确性或长期稳定性验收。

调试中发现并修复：模型自由填写 topic 导致空召回，因此 Agent 工具只接受 query；原 20 秒时限不足；部分供应商流忽略 abort，因此增加独立的运行截止与失败落盘。早期一次成功为 33,936 ms，最终 provider 合并后为 52,949 ms，均为单次样本，不构成速度承诺。

回归：后端全量 258 项通过；随后新增训练候选证据刷新回归，知识工具 6 项通过。Node 41 项通过，类型检查和构建通过。前端相关渲染/传输 42 项及类型检查通过。受控完整 HTTP 链路覆盖第九工具、餐食修改后重读和重检索、Undo、会话隔离、重放、断开取消与进程重启；使用官方 SDK 测试模型与独立 PostgreSQL，不调用外部模型。

本次未提交或推送 GitHub。RAG 变更仍独立于已存在的 CopilotKit 基线 PR；选择性合并基线修复不等于把知识代码或私有数据上传。

原文、向量和本地数据库不进入源码；密钥仅保存在后端 `.env`（0600）。使用说明见 [INTEGRATION.md](INTEGRATION.md)。

验收后已停止本次本地 PostgreSQL，数据与向量保留；下次按 INTEGRATION.md 启动数据库和 FastAPI 即可查询。未遗留本次临时 API 服务。
