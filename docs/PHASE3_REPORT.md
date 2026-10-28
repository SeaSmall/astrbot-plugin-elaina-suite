# 阶段三报告：Sentipolis PAD 连续情感 + 双速动力学 + 沉默评估

日期：2026-10-28　版本：v1.4.0　实现：`main.py` 内 `AffectCore`（「核心八」区块）

## 1. 与用户规格的逐条对照

| 规格要求 | 实现 | 证据 |
| --- | --- | --- |
| PAD 三维连续状态 | `pad = {p,a,d}` 各 ∈[-1,1]，存 REAL×3 | `_clamp`、A3 |
| 按论文实现双速动力学 | 快通道每轮即时更新；慢通道 poignancy 累计 > 150 触发 reflection | `fast_update` / `_maybe_reflect`、A3/A4 |
| 衰减（默认保留原文设定） | `s(t+dt)=s(t)·2^(−dt/120min)`，每次读取按真实 Δt 补算，无条件施加 | `_decay_to` / `get_state`、A1 |
| 沉默评估模块（新增，独立于对话轮次） | `affect_silence` interval job，周期性检查「距上次交互 / 该角色预期节奏」偏离度 | `silence_check`、A5 |
| 超阈值向情感系统注入负面评估事件 | 注入 `kind="silence"` 的 appraisal 事件，走 `_apply()` **正常更新通道**，不直接改 PAD | `_apply` 是唯一改 PAD 入口、A5 |
| 衰减目标方向朝负面基线偏移 | `affect_silence_target`（可配，默认 `-0.45,-0.10,-0.20`）按严重度缩放后作为衰减目标点，动力学形式不变 | `_silence_target`、A6 |
| persona 情感倾向校准基线 | 基线取 `persona.json` 的 `emotional_tendencies.baseline_pad`（0.15/0.25/0.70），可用 `affect_baseline` 覆盖 | `_persona_baseline`、A8 |
| 情感影响主动回复 | 生成前读当前 PAD → 分档「此刻心情」块注入提示词；低 P 低 D 时提示「试探、留退路、别把话说满」 | `describe`、`{affect_block}` 注入位、A7 |
| 可观测性 | `/情感状态`（别名 `/心情状态`）输出每会话 PAD、poignancy、沉默严重度、事件数 | `affect_status_cmd`、A9 |

## 2. 三处对论文的显式偏离（均已在代码注释与本文件标注）

1. **沉默评估模块是新增机制**。`docs/PHASE0_RESEARCH.md` §1.5 核对原文后确认：Sentipolis 没有独立的 idle / 沉默处理机制，只有「衰减软化强情绪」一句。因此本模块**超出原文**，只借用原文的「PAD + 衰减」框架，事件注入方式仍是原文的 appraisal 通道。
2. **衰减目标点可偏移**。原文衰减目标是中性点；本实现把目标点按 `silence_sev` 拉向可配置的负面基线（只换目标点，`2^(−dt/T½)` 的形式与半衰期不变），以便「长期沉默 → 情感缓慢转低」而不是只靠一次性事件。
3. **PAD → 文字用分档词表**。原文用 KNN(k=3) 在 MSP-Podcast 语料上做情绪归类；本项目没有该语料与标注，改为手写分档词表 + 行为提示（低 P 低 D → 试探/克制；高 P → 轻快）。这只影响「注入给生成模型的文字」形式，不影响 PAD 数值动力学。

## 3. 关键设计决策与理由

- **快通道默认 `heuristic`（规则式）**：零延迟、不额外调用 LLM，行为可预测可测；`affect_fast_mode=llm` 为可选（每轮多一次极短调用，评估更准）。理由：拟人感来自「反应对得上」，规则式已能区分夸赞/冷淡/冲突，且不会因 LLM 抖动让情感乱跳。
- **慢通道必须走 LLM**：reflection 要跨事件归纳，规则式做不了；LLM 不可用时回退成一次温和回落并把 poignancy 归零，避免累积值无限增长后突然巨变。
- **沉默检查独立调度**：`affect_silence` 是 AffectCore 自己的 interval job，不依赖 living 是否开启——情感状态是底层能力，主动消息只是消费者之一。
- **单写入口 + 原子落盘**：`_apply()` 是唯一改 PAD 的地方（沉默、对话、reflection 全走它），状态文件 `.part` + `os.replace`，损坏时降级为空状态。
- **默认关闭**：`affect_enabled=False`，关闭时零写入、零注入，行为与 v1.3.1 完全一致（A2）。

## 4. 配置项（11 个，`_conf_schema.json` 132 → 143 键）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `affect_enabled` | false | 总开关 |
| `affect_fast_mode` | heuristic | 快通道评估：heuristic / llm |
| `affect_decay_half_life_minutes` | 120 | 衰减半衰期（论文默认） |
| `affect_silence_threshold_minutes` | 720 | 沉默基线：多久不回算反常 |
| `affect_silence_check_minutes` | 15 | 沉默检查间隔 |
| `affect_silence_cooldown_minutes` | 120 | 两次沉默事件的冷却 |
| `affect_silence_target` | -0.45,-0.10,-0.20 | 沉默时衰减目标点（留空=中性） |
| `affect_reflect_poignancy` | 150 | 慢通道触发阈值（论文默认） |
| `affect_slow_delta_cap` | 0.6 | 单次更新最大 PAD 增量 |
| `affect_inject_chars` | 600 | 注入的「此刻心情」块字数上限 |
| `affect_baseline` | （空） | PAD 基线覆盖，空则用 persona.json |

## 5. 测试证据

`tests/test_affect.py`，11 组全过（`py tests/test_affect.py`）：

- A1 衰减按真实 Δt 折算（120 分钟减半、240 分钟四分之一）
- A2 关闭时不动状态、不注入
- A3 快通道：夸赞抬 P、斥责压 P、PAD 恒在 [-1,1]
- A4 慢通道：poignancy 过阈触发 reflection 且归零（LLM 两次调用）
- A5 沉默评估：走正常更新通道注入负面事件 + 冷却生效
- A6 沉默时衰减目标点偏移；配置留空则回中性
- A7 心情块分档（低 P 低 D 出「试探」、高 P 出「轻快」）
- A8 状态持久化：落盘后可被新实例读回
- A9 接线完整：构造/初始化/收尾/回复钩子/注入位/状态命令
- A10 11 个 `affect_*` 配置键齐全且默认关闭
- A11 `_json_objects` 支持单个顶层对象/代码围栏/嵌套对象

回归：`test_merged.py` 24 组、`test_living.py` 12 组、`test_memory.py` 10 组全过。

## 6. 顺带修掉的真 bug

`_json_objects()` 原实现只尝试「整段是数组」与 `\{[^{}]*\}` 平铺匹配，**单个顶层对象且内含嵌套对象时会把内层对象当成结果返回**（如 `{"delta":{"p":-0.3},"poignancy":12}` 只捞出 `{"p":-0.3}`）。慢通道 appraise/reflection 的输出正是这种形状，会静默失效。现改为：先整体 `json.loads`（对象或数组），再按**括号配对扫描**（跳过字符串内的括号，支持嵌套）逐个兜底。A11 覆盖。

## 7. 明确未做 / 遗留

- PAD → 情绪的 KNN(k=3) 归类（缺 MSP-Podcast 语料，见 §2.3）。
- 情感对 Mem0 召回的调制（规格要求在阶段四做：负面情绪优先召回情绪一致/未解决事项），目前召回查询只由最近用户发言驱动。
- 情感对候选意图的门控（阶段四：沮丧时「兴奋分享」降权、「试探性询问」升权）。
- 多会话情感相互独立（按 `unified_msg_origin` 分桶），未做「同一用户多平台身份合并」。
