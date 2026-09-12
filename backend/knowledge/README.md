# Wellio 演示知识资料：营养、睡眠与训练

2026-09-12 已完成资料收集。按最新要求，只在原有营养资料上补充 **6 篇睡眠、恢复与训练资料**，不再扩搜。展示优先使用其中 4 篇，见 [演示场景与引用入口](DEMO_KNOWLEDGE_MAP.md)。

累计尝试 43 个 URL，保存 37 项资源，整理为 **36 篇正文、554 个待入库片段**；中英对应版本合并后共 29 组独立内容，另有 1 个下载入口。片段数量包含方法和参考文献，不是独立建议条数。

采用 **FastAPI / Python + PostgreSQL + pgvector**。2026-09-12 已新增 OpenRouter 向量化、版本化入库和 HTTP 检索接口，先选 8 篇、39 个片段用于展示。真实召回验收结果见 [接口与运行说明](INTEGRATION.md)。已接入 Agent 工具、强制检索、回执校验和对话来源链接。

## 文件入口

| 内容 | 位置 |
| --- | --- |
| 本次逐项来源与采集报告 | [SOURCE_REPORT.md](SOURCE_REPORT.md) |
| 可维护的来源清单 | [sources.json](sources.json) |
| 全部本地正文、HTML 和 PDF | [资料目录](../.data/knowledge/nutrition-v1/) |
| PostgreSQL 待导入文档 | [documents.jsonl](../.data/knowledge/nutrition-v1/documents.jsonl) |
| 待生成向量的知识片段 | [chunks.jsonl](../.data/knowledge/nutrition-v1/chunks.jsonl) |
| 抓取元数据和失败记录 | [manifest.json](../.data/knowledge/nutrition-v1/manifest.json) |
| 文件校验与语料检查 | [validation.json](../.data/knowledge/nutrition-v1/validation.json) |
| 文件 SHA-256 清单 | [SHA256SUMS](../.data/knowledge/nutrition-v1/SHA256SUMS) |
| PostgreSQL 表结构提案，尚未执行 | [postgresql_schema.proposed.sql](postgresql_schema.proposed.sql) |
| 完整 RAG 接入规划 | [NUTRITION_KNOWLEDGE_PLAN.md](../../docs/delivery/NUTRITION_KNOWLEDGE_PLAN.md) |

原文保存在后端已忽略的 `.data/` 中，不进入前端仓库。`knowledge/` 只包含来源元数据、脚本、表结构提案和说明。各机构正文的再分发许可尚未逐项取得，不把原文打进公开源码或旧交付包。

## 应该读什么

| 优先级 | 问题场景 | 内容与来源 |
| --- | --- | --- |
| P0 | 增肌怎么吃、训练前后怎么安排 | BDA Sport and exercise；Sports Dietitians Australia 四页完整运动饮食 factsheet |
| P0 | 今天怎样搭配饮食、蛋白质食物怎么选 | 香港卫生署健康饮食、饮食原则、肉鱼蛋及替代品；BDA Healthy Eating；NHS Eatwell Guide |
| P0 | 香港外食、聚餐怎么选 | 香港卫生署 Healthy Chinese Banquet，中英文对应正文 |
| P0 | 份量、包装标签、糖盐油怎么判断 | BDA 份量、标签、脂肪、糖；NHS 盐；保留英港地域差异 |
| P1 | 喝水、蔬菜不足、素食怎么补全 | BDA 补水/素食；香港卫生署高纤饮食和均衡素食 |
| P1 | 补充剂、铁钙维生素 D 与饮食误区 | BDA 对应科普及香港卫生署饮食误区；需要按适用人群筛选，不自动给疾病或补剂剂量建议 |

不把热量/价格估算变成“官方数据”。本库提供建议原则；菜品事实继续来自实时 Exa 菜单结果，已吃数据和余量来自 PostgreSQL 业务账本，照片份量仍是估算。

## 抓取和清洗方法

新增 5 篇论文通过 Europe PMC 官方全文 API 保存完整 JATS XML，保留作者、DOI、原许可、章节、表格、图注和参考文献；图片不做视觉解读，补充附件不另行下载。NIH 睡眠习惯页面从原 HTML 提取正文。新增资料的人群、场景与限制也保留在切片元数据中。

- AnySearch 用于公开来源发现与正文提取；直接 HTTP 保存原 HTML，按域名串行、限时抓取，保留 robots 记录。这不改变产品运行时 Exa 的选型。
- BDA 和香港卫生署表格从原 HTML 恢复行列，保留 False/True 标签、合并单元格和营养数字的对应关系。原提取稿另存，不覆盖。
- SDA 下载页只存作导航证据，实际抓取完整 4 页、约 12.7 MB PDF。PDF 双栏按左栏后右栏恢复阅读顺序，每块保留页码和坐标。
- PDF 第 3 页插图文字存在重叠的隐藏文本；这些插图文字未用于生成片段。完整 PDF、最初文字提取与排除记录全部保留，不声称图片内容已完整结构化。
- 文档保存标题、机构、页面明确提供的作者/日期、抓取时间、原文 URL、正文 hash 与原文件 hash。未提供作者或日期的字段为空，不补造营养师署名。
- 切片按段落和章节，不打断表格；保留 `char_start`/`char_end`，每片可精确回到清洗全文。较长原子表格标记为需要父段落上下文，后续召回不能只抽一个数字。

所有正文当前是 `staging`，技术提取核对不等于营养师审校。资料中的儿童、孕期、疾病、运动员高训练量等条件需要在发布前做片段级适用性审核。来源冲突先判断地域/人群，不平均不同指南的数字。

## 没有抓到的内容

本次扩充另有 4 个候选因 HTTP 403、全文 API 404 或 TLS 错误未抓取成功，已排除且不继续重试；现有 6 篇足够覆盖展示。逐项记录见 SOURCE_REPORT.md。

WHO Healthy diet、Sodium reduction 两个候选 URL 被 robots 中对 Collector 的规则拒绝，已保留失败记录，没有更换身份绕过。它们不计入成功资源和待入库正文。均衡饮食和盐的主题已有 BDA、香港卫生署、NHS 资料覆盖。

“全部”在本次指本报告明确列出的最终入选清单，不表示镜像整个机构网站或互联网上全部营养知识。

## PostgreSQL 接法

**实现入口已更新**：当前实际代码、建表和命令见 [INTEGRATION.md](INTEGRATION.md)，实际 DDL 为 `wellio/data/knowledge_v1.sql`。下面的多表结构是早期提案，不是这次最小实现。

`postgresql_schema.proposed.sql` 给出独立 `knowledge` schema：

1. `documents` 保存全文、机构、来源和不可变内容版本。
2. `chunks` 保存可引用片段和全文定位，GIN 全文索引负责关键词召回。
3. `embedding_models` 记录真实模型名称、版本和维度；`chunk_embeddings` 用 pgvector 保存对应向量。
4. 首版使用精确向量召回与全文检索的 RRF 合并；数据增长后再按真实维度建立 HNSW 索引。

`embedding: null` 表示还没有生成，不能插入零向量替代。中文全文检索要使用预分词字段或经过验证的分词方案；英文配置不能直接视为中文分词。知识库发布版本、证据回执和 Agent 输出检查按完整接入规划继续实现。

SQL 为独立提案，不挂进现有自动迁移，也未在开发库执行。后续由后端迁移流程接纳，先验证目标 PostgreSQL 的 pgvector 扩展。前端不直接访问知识表。

## 复现

采集脚本需要 Python、`requests`、`beautifulsoup4`、`pymupdf`、`lxml` 和本机 AnySearch CLI；使用这次环境已有依赖，没有修改业务后端的依赖锁。`sources.json` 可在其他环境配合对应抓取适配器复用，Node/AnySearch 不是产品运行依赖。

从后端目录运行（CLI 路径按环境设置）：

```sh
python3 knowledge/collect.py \
  --output .data/knowledge/nutrition-v1 \
  --anysearch-cli /absolute/path/to/anysearch_with_fallback.js
python3 knowledge/prepare.py .data/knowledge/nutrition-v1
python3 -m unittest discover -s knowledge -p 'test_prepare.py' -v
```

采集默认复用已有成功文件；明确要重新采集时使用 `--refresh`。PDF 阅读顺序规则针对本次已目视核对的四页 SDA 文档，加入不同 PDF 时必须重新验证布局，不能假设所有 PDF 都是双栏。

## 下一步

向量化与 HTTP 检索接口已实现；接下来在目标 Agent 中强制“当天事实 → 检索 → 结论与引用检查 → 正文/Today/提案”，保留现有显式 Apply、记餐与撤销规则。
