# 演示用知识：少量实用资料

2026-09-12：按用户最新要求停止扩搜，在已有营养资料上只增加 **6 篇**。现场优先用下面的前 4 篇；另外 2 篇用于追问。来源全文已下载。后续已选 8 篇 / 39 个片段完成向量化与检索接口，见 [接口说明](INTEGRATION.md)；Agent 尚未接通 RAG。

| 演示输入 | 优先资料 | 用在什么地方 |
| --- | --- | --- |
| 昨晚只睡 4 小时，今天还练吗？ | [睡眠不足与运动表现：系统综述，2022](https://europepmc.org/articles/PMC9584849) · [本地全文](../.data/knowledge/nutrition-v1/documents/sleep-loss-performance/clean.md) | 解释睡眠不足可能影响表现，结合当天感受和训练安排，提出休息或调整候选。不能预测 Alex 会下降多少，也不能把演示的 4.2 分当作医学阈值。 |
| 原有 35 分钟训练、器械占用或更换 | [ACSM 力量训练立场声明，2026](https://europepmc.org/articles/PMC12965823) · [本地全文](../.data/knowledge/nutrition-v1/documents/acsm-resistance-2026/clean.md) | 为训练量、动作选择和渐进安排提供原则。具体动作可用性读器械清单，具体公斤数读同动作同设备历史；论文不提供 Alex 的个人负重。 |
| 突然只剩 15 分钟 | [No Time to Lift? 时间有限时如何训练，2021](https://europepmc.org/articles/PMC8449772) · [本地全文](../.data/knowledge/nutrition-v1/documents/time-efficient-training/clean.md) | 解释为什么优先保留关键动作、减少剩余内容。不能承诺 15 分钟等效 35 分钟，也不把一味缩短组间休息当作默认方案。 |
| 晚上吃什么、喝咖啡会不会影响睡眠？ | [NIH 健康睡眠习惯，2022](https://www.nhlbi.nih.gov/health/sleep-deprivation/healthy-sleep-habits) · [本地全文](../.data/knowledge/nutrition-v1/documents/nhlbi-sleep-habits/clean.md) | 给出作息、咖啡因和临睡前进食的实用提醒。只取成人相关段落，不把儿童或轮班工人的建议混进普通成人场景。 |
| 心率比平常高、自己也觉得累 | [训练反应监测与主观感受：系统综述，2016](https://europepmc.org/articles/PMC4789708) · [本地全文](../.data/knowledge/nutrition-v1/documents/training-response-monitoring/clean.md) | 为结合疲劳、压力和近期训练记录提供依据。它不验证本产品恢复算法；单次心率 72、基线 60 不能直接诊断过度训练。 |
| 为什么增肌也需要睡好？ | [睡眠限制与肌肉蛋白合成实验，2020](https://europepmc.org/articles/PMC7217042) · [本地全文](../.data/knowledge/nutrition-v1/documents/sleep-muscle-protein/clean.md) | 用于说明睡眠与恢复的关系。研究是健康年轻男性连续 5 晚限制卧床时间，不等于 Alex 单晚少睡必然掉肌肉，也不据此推荐用 HIIT 或蛋白质抵消缺觉。 |

## 与原有营养资料一起用

- **增肌、训练前后吃什么、喝水**：`bda-sport-exercise-nutrition`、`sda-exercise-fuel-pdf`、`bda-fluid-water-drinks`。
- **香港外食、预算从 100 改 70、不吃海鲜**：`chp-banquet-tc`、`chp-protein-foods-tc`、`bda-food-facts-portion-sizes`。知识库支持搭配原则，菜单、价格和售罄情况来自实时搜索；偏好和预算来自用户。
- **照片记餐、“薯条只吃一半”、撤销**：识别输入与 PostgreSQL 实际记录支持这些事实操作；只有附带新的饮食建议时才引用知识。
- **Today / Trends / 休息顺延**：历史与当前场次读业务数据；Apply、保留已完成动作、不重复计数和顺延队列是项目规则，不能声称来自论文。

## 展示时的最小用法

按当前场景检索对应资料，取 1–2 个相关章节，给一句贴合个人事实的解释并展示来源。睡眠问题优先看睡眠综述的 Conclusions；短时训练看时间效率综述的 Practical Applications、Exercise Selection、Rest Periods；训练原则看 ACSM 的实用建议与相关结果章节。

中文场景词和英文原文通过 `sources.json` 的 topics / demo_scenarios 关联；片段保留人群、来源类型和限制。它们帮助后续检索选材，不代表已经实现检索。无需为这次展示继续扩充论文数量或增加新页面。

原论文 XML、表格和图注均已保留；图片图形及补充附件未做解释或另行下载。全文包含方法和参考文献，不能把所有切片都当作独立建议。当前资料仍为 staging，PostgreSQL + pgvector 方向不变。
