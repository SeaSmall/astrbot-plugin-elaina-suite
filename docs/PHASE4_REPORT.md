# 阶段四报告：自主性（注意力选择 + 内在动机）

对应《拟人聊天插件重构工程提示词（最终版）》阶段四：
「只选两个机制——① 全局工作空间的注意力选择（决定某时刻哪个念头/候选意图进生成焦点，
轻量候选意图评分-选择循环即可，不需要完整 GWA 多 Agent 群落）；② 内在动机驱动的任务生成
（沉默期生成值得主动发起的候选意图，带学习进度门控，防只追新不沉淀）。
耦合：候选意图进入选择前受当前情感状态门控。」
本报告逐条交代实现位置、公式、配置与验证方式。

## 1. 机制与论文对应

| 机制 | 采用的来源 | 本期落地形态 |
| --- | --- | --- |
| 排他性瓶颈 / 全局工作空间 | Mashour et al. 2020, *Neuron*（竞争→点火→广播，每轮只有一个 winner）；Shanahan 2006（affect 介导 action selection） | `MotivationCore.select()`：一次决策只在候选意图里选 **1 个** winner 进入 `_living_compose` 的生成焦点；落选者按 `motivation_loser_decay`（默认 0.85）降权记录，**不删除**，下次继续竞争 |
| 内在动机 | Oudeyer & Kaplan 2007（learning progress 作为内在奖励） | `learning_progress()` = `engaged / (attempts + 1)`；`learning_gate()` = `clamp(0.35 + 1.25×progress, 0.2, 1.4)`：反复发起却没回应的话题被压到 0.35 附近，得到回应的抬到 1.4 上限 |
| 情感门控 | Shanahan 2006（情绪状态影响候选动作的选择权重） | `affect_multiplier(intent, ctx)`：候选分数乘上情感系数，低落时「分享」降权、「试探性确认」升权 |

明确不做（与规格一致）：**不实现完整 GWA 的多 Agent 群落**，只有「候选生成 → 打分 → 单一 winner」的轻量循环；
**不引入训练**，学习进度用对话内的「发起/回应」比作代理量，而不是论文里的预测误差。

## 2. 候选意图池

`ProactiveGuardCore._living_candidates()` 产出的五类候选，进入 `MotivationCore.reweight()` 后统一打分：

| intent | 语义 | 基础分 | 说明 |
| --- | --- | --- | --- |
| `share` | 分享自己正在经历的一件小事（世界观内） | `1.0 × 分享权重 × 白天系数` | 由「人格 + 世界观」驱动，不是关心用户 |
| `care` | 具体地关心对方近况（要有由头） | `1.0 × 关心权重 × 夜间系数` | 有具体由头时 ×(1+0.25×由头权重) |
| `followup` | 追问之前没说完的话题 | `0.55 × 分享权重` | 必须挂到一条**待接话题**上，无由头时 ×0.35 |
| `curiosity` | 对对方提过的事产生新的好奇 | `0.45 × 分享权重` | 与 followup 共享学习进度门控 |
| `check_in` | 试探性确认对方还在不在（别扭、不直白示弱） | 基础 0.4 起，随沉默时长增长 | 额外加上 `0.25 × 沉默严重度`（来自 AffentCore 的 `silence_sev`） |

最终分数：

```
score = base × affect_multiplier(intent, ctx) × learning_gate(topic) [+ check_in 沉默加成]
```

## 3. 情感门控表（默认开启，`motivation_affect_gate`）

| intent | 条件（当日 PAD / 沉默严重度） | 系数 | 可读理由（写进日志与候选的 why） |
| --- | --- | --- | --- |
| share | p < −0.25 | 0.45 | 心情低落，提不起劲讲自己的事 |
| share | −0.25 ≤ p < 0 | 0.6 | 心情不太好，分享欲下降 |
| share | p > 0.2 且 a > 0.2 | 1.25 | 心情轻快，正想分享 |
| care | p < 0.05 | 1.2 | 自己不太好，反而更想关心对方 |
| followup | silence_sev > 0.3 | 1.15 | 在意之前没说完的事 |
| curiosity | p < −0.1 | 0.7 | 低落时好奇心收窄 |
| curiosity | a > 0.15 | 1.15 | 精神好，愿意多想一步 |
| check_in | p < −0.1 或 silence_sev > 0.2 | 1.5 | 心里不踏实，想确认对方还在 |
| check_in | p > 0.3 | 0.7 | 心情好，不急着确认 |

关闭 `motivation_affect_gate` 或 AffectCore 未启用时，所有系数恒为 1.0（可退回纯启发式）。

## 4. 学习进度与话题跟踪

- **话题抽取**：用户消息去掉标点后按 `motivation_topic_chars`（默认 24 字）截断成一条话题，同时进入 `topics`（带 `mentions/attempts/engaged/last_at`）与 `pending`（待接队列）。
- **话题合并**：`_mot_same_topic()` 先用子串包含、再用 2-gram 重叠率（`inter/min(len) ≥ 0.35`，至少共享 2 个 bigram）判定同一件事，避免「杭州出差」被拆成两条。
- **学习进度**：`progress = engaged / (attempts + 1)`；`gate = clamp(0.35 + 1.25 × progress, 0.2, 1.4)`。
- **回应判定**：主动消息发出后 `motivation_engage_window_minutes`（默认 180 分钟）内收到用户消息 → 该话题 `engaged += 1`（若新消息与该话题重叠 ≥0.3 则 +2）；超过窗口没回应，`attempts` 已经加过，于是下次门控自然下降。
- **由头选择**：`_best_pending_topic()` 在待接队列里按 `recency × learning_gate` 取最高，`recency = 0.5^(沉默小时数/48)`（48 小时半衰，越久越不急着接，但不会消失）。

## 5. 代码位置

| 内容 | 位置 |
| --- | --- |
| `class MotivationCore(_PluginCoreBase)` | `main.py`「核心九」区块（在「核心四：Elaina 表情包」banner 之前） |
| 状态文件 | `<插件数据目录>/motivation_state.json`（`.part` + `os.replace` 原子写，写失败只 warning） |
| 候选重加权 | `ProactiveGuardCore._living_candidates()` 末尾调用 `mot.reweight(...)` |
| 竞争选择 | `ProactiveGuardCore._living_plan_intent()`（已改 async）→ `mot.select(...)`，winner 写入 `pending` 的 `topic`/`why` |
| 观察与记数 | `_living_on_user_message(umo, text)` → `mot.observe_user`；`_living_fire()` 发送成功后 → `mot.note_attempt` |
| 具体由头注入 | `_living_compose(..., topic="")` → 提示词 `{reason_block}` 追加「具体到：xxx」 |
| 可观测命令 | `/动机状态`（别名 `/注意力状态`） |
| 插件接线 | `self.motivation = MotivationCore(self)`；`initialize()` / `terminate()` 各一行；`self.motivation.close()` |

## 6. 配置（`_conf_schema.json`，143 → 151 键）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `motivation_enabled` | `false` | 总开关；关闭时 `reweight/select` 直接返回原候选，行为与 v1.4.0 一致 |
| `motivation_affect_gate` | `true` | 候选受当前情感门控 |
| `motivation_learning_gate` | `true` | 学习进度门控 |
| `motivation_topic_max` | `12` | 每会话待接话题上限 |
| `motivation_topic_chars` | `24` | 话题截断长度 |
| `motivation_engage_window_minutes` | `180` | 多久内的回话算「有效回应」 |
| `motivation_select_noise` | `0.15` | 竞争噪声（0 = 纯取最高分，便于复现） |
| `motivation_loser_decay` | `0.85` | 落选候选降权系数（只降不删） |

## 7. 可观测性

- `/动机状态` 输出：最近意图与话题、**上一次注意力竞争**（winner 与分数、落选者及降权后分数）、待接话题、每条话题的 `发起次数/回应次数/学习进度/门控系数`。
- 日志：`[motivation] 注意力竞争：胜出=share(1.184) 其余=care:0.98,followup:0.47`；`[motivation] 学习进度更新：话题「…」engaged+2`。
- 状态文件 `motivation_state.json` 可直接查看完整结构（`sessions → topics/pending/history/last_competition`）。

## 8. 测试证据

`tests/test_motivation.py` 10 组（`py tests/test_motivation.py`）：

| 组 | 验证点 |
| --- | --- |
| N1 | 关闭时不改候选、不写状态 |
| N2 | 话题抽取 / 相似话题合并 / 待接去重 |
| N3 | 学习进度：反复无回应门控不升权；得到回应后回升 |
| N4 | 情感门控：低落时 share≈0.45、check_in=1.5；愉快时相反；Affect 关闭时为 1.0 |
| N5 | 重加权：有由头挂 topic、无由头压到 0.35 且给出理由 |
| N6 | 排他性瓶颈：唯一 winner + 落选按 0.85 降权不删除 |
| N7 | 状态持久化：话题与 attempts 可被新实例读回 |
| N8 | 待接话题上限 + `/动机状态` 数据面 |
| N9 | 接线完整性（构造/初始化/收尾/观察/尝试/加权/竞争/命令/由头注入） |
| N10 | 配置面板 8 键齐备且默认关闭 |

回归：`test_merged.py` 24 组、`test_living.py` 12 组、`test_affect.py` 12 组、`test_memory.py` 10 组全过。

## 9. 与规格的对齐与偏离

- 对齐：轻量候选评分-选择循环（非完整 GWA 群落）、单一 winner 广播、落选降权不删、内在动机驱动沉默期候选、学习进度门控、情感门控耦合、全部新行为默认关闭且关闭时与 v1.4.0 行为一致。
- 偏离 ①：学习进度用「发起/回应」比作代理量，而非论文的预测误差（本项目无人训练型世界模型，规格硬约束禁止引入训练）。
- 偏离 ②：话题相似度用 2-gram 重叠而非分词/向量（不引入新依赖，误合并风险靠 0.35 阈值与「先包含后重叠」两级判定控制）。
- 偏离 ③：候选池仍是规则生成（share/care/followup/curiosity/check_in 五类），没有让 LLM 自由产出意图 —— 保持可解释与低成本，LLM 只负责把选中的意图写成消息。

## 10. 遗留

- 阶段五：傲娇重构（元话语式否定零容忍、行动先行 + 借口收尾、关心以「泄露」出现）。
- 阶段六：整合自洽性彻查，其中 **Mem0 召回被情感信号调制**（负面情绪下优先召回情绪一致/未解决事项）尚未实现——目前记忆召回仍是中性查询，这是阶段六的检查项之一。
