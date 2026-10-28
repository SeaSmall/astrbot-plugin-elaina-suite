# 阶段一完成报告：插件内部调度器 + 单会话决策循环（两阶段生成）

> 对应工程规格「阶段一（最高优先级，完成才进下一阶段）」。
> 本文只记录**已实现、已验证**的内容；未做的部分单列在 §7，不混进来充当成果。

## 1. 目标回顾（规格原文要点）

1. 主动消息调度由插件自管，**不依赖 AstrBot 内置任务系统**；
2. 每个活跃会话一个「决策循环」job，`job_id = proactive_decision_<session>`、`replace_existing=True`，
   决策完**自己排下一次**（自我延续）；
3. 状态持久化，重启恢复；
4. 每会话一把 `asyncio.Lock` 覆盖「决策 → 生成 → 发送」全程；
5. **两阶段生成**：决策阶段只产「意图 + 时间偏移」，触发那一刻才读当下记忆生成正文，**不得预生成**；
6. 时间逻辑：深夜不发兴奋邀请；长时间沉默后的第一条与「刚聊完」有可辨识差异；
7. 停止条件：同一会话连续触发 100 次消息数严格等于预期；两阶段生成的时间点与内容情境一致性人工抽查通过。

## 2. 实现契约（逐条对照）

| 规格要求 | 落地位置 | 实现方式 |
| --- | --- | --- |
| 不依赖 AstrBot 任务系统 | `ProactiveGuardCore.initialize/_living_start` | `living_enabled=true` 时**完全不注册** `cron_manager`；内部 `AsyncIOScheduler` 由 `initialize()` 启动、`terminate()` 里 `remove_all_jobs()` + `shutdown(wait=False)` |
| 每会话决策 job + 幂等 | `_living_schedule_decision` / `_living_decide` | `proactive_decision_<sid>`，`replace_existing=True`、`coalesce=True`、`max_instances=1`、`misfire_grace_time=120`；决策末尾自己排下一次 |
| 两阶段生成 | `_living_decide` → `_living_fire` | 决策只写 `pending={token,fire_at,intent,label,reason}` 并注册 `proactive_fire_<sid>` 一次性 job；`_living_fire` 触发时才读 PAD 占位、Mem0/记忆与人格世界观并调 LLM |
| 单槽 + token 幂等 | `_living_fire` | `pending.token != token` 直接返回；同一 token 触发 100 次只发 1 条 |
| 不补发 | `_living_decide` | 重启后发现 `pending.fire_at` 已过期 → 丢弃写 `drop` 日志，**绝不批量补发**（旧实现正是 `missed_grace_minutes` 窗口批量补发导致「一大串」） |
| 每会话锁 | `_living_decision_tick` / `_living_fire_tick` | `async with self._living_lock(umo)`，决策与发送同一把锁 |
| 状态持久化 | `_living_load/_living_save` | `<data>/proactive_state.json`，`asyncio.to_thread` + `os.replace` 原子写；损坏 JSON 降级为空状态并告警 |
| 时间入口 | `_living_now/_living_dt/_living_period/_living_human_gap` | 全部走 `zoneinfo` 的 `pg_timezone`；时段词分八档（深夜/清晨/上午/中午/下午/傍晚/晚上/夜里） |
| 安静时段 | `_living_quiet_hours`（默认 `23:30-07:30`） | 支持跨午夜，`off` 关闭；计划落在时段内则顺延到时段结束 +5~25 分钟 |
| 沉默偏离 + 概率斜坡 | `_living_should_initiate` | 沉默下限 / 冷却 / 日上限 / 安静时段 / `p = clamp((silence-min_silence)/ramp, 0.05, 1)`，避免固定节拍 |
| 人格与世界观固化在插件内 | `assets/persona.json`、`assets/worldview.json` + `_living_persona_block/_living_world_block` | 运行时只读仓库内 JSON（mtime 缓存），**不读服务器人格文件、不做哈希比对/同步/热更新** |
| 反「标签化傲娇」最小硬约束 | `_living_check_text` | 命中「才不是特意 / 我才没有 / 又不是为了你 / 别误会」或「作为 AI / 语言模型」直接丢弃本条（阶段五升级为模型审校） |

## 3. 关键设计决策与理由

- **单槽而不是队列**：同一会话最多一个待触发。原因：队列是旧实现「积压 → 一次补发」的结构性来源；
  单槽 + token 让「重复派发」在数据层不可能成立，而不是靠加锁堵住。
- **未过期待触发时跳过决策**（`_living_decide` 早退并 300s 后重查），而不是并发再排一个；
  这样即使调度器重复触发 tick，也不会堆出多个待触发。
- **正文生成才读记忆**：决策时只选意图，避免「凌晨 3 点决定、早上 8 点发出用昨晚记忆写的文案」。
- **意图先用可解释启发式**（share / care / followup / curiosity / check_in，权重受 `self_share_ratio`
  与昼夜系数影响）：阶段四要换成真正的评分-选择循环，这里刻意保持「能被一句话解释」的简单形态，
  避免提前引入无法验证的复杂度。
- **随机数统一走 `self._living_rng`**：调度与意图选择都可注入种子，测试可复现（可调试性判据）。

## 4. 测试证据

### 4.1 阶段一专属测试 `tests/test_living.py`（12 组全过）

```
[L1] living 开启：零 cron_manager 注册 + 内部 scheduler 启动 OK
[L2] living 关闭：走旧 cron_manager 路径，内部调度器未启动 OK
[L3] job 幂等参数（replace_existing/coalesce/max_instances/misfire）+ 单槽 OK
[L4] 同一 token 重复触发只发 1 条（跨重启幂等）OK
[L5] 过期待触发丢弃、不补发 OK
[L6] 连续 100 次重复触发 → 消息数严格 = 1 OK
[L7] 100 个周期 → 发送数严格 = 100 OK
[L8] 用户回话：取消待触发 + 下次决策推到沉默下限之后 OK
[L9] 安静时段：不触发 + 计划自动顺延出时段 OK
[L10] 状态原子写（无 .part 残留）+ 损坏文件降级为空状态 OK
[L11] 人格/世界观/傲娇机制注入 + 元话语式否定与 AI 自称硬过滤 OK
[L12] 时段词 / 人话时长 / quiet=off 解析 OK
阶段一（living）测试全部通过
```

测试方式：`apscheduler` 用假模块注入（`FakeScheduler` 记录每次 `add_job` 的参数），
发送器/LLM/目标会话/记忆全部打桩，状态文件指向临时目录。
因此 L1/L3 是**结构性断言**（数 cron_manager 注册数、查 job 参数），不是行为模拟。

### 4.2 回归

`py tests/test_merged.py` → 24 组 `ALL SMOKE TESTS PASSED`（含 [24f] emoji 剔除）。

### 4.3 一整天人工抽查（`_scratch/living_coherence.py`）

模拟 2026-10-25 06:00 → 次日 00:05，期间 08:00 与 20:00 两次用户回话（回话会取消待触发并把决策推后 90 分钟）：

| 时刻 | 时段 | 与上一条间隔 | 说明 |
| --- | --- | --- | --- |
| 07:55 | 清晨 | — | 启动时沉默 3 小时 + 安静时段结束（07:30）后顺延 25 分钟，第一条 |
| 11:36 | 中午 | 221 分钟 | 08:00 用户回话后重新计时 |
| 16:04 | 下午 | 268 分钟 | 概率斜坡未过时反复顺延，不是固定节拍 |
| 19:14 | 晚上 | 190 分钟 | 20:00 回话前最后一条 |
| 23:02 | 夜里 | 228 分钟 | 之后进入安静时段，不再触发 |

校验：安静时段内发送数 **0**；相邻间隔全部 ≥ 冷却 180 分钟；当日 `sent_today=5 ≤ living_daily_max=6`。
（生成正文由桩函数返回固定句，内容层面的一致性属阶段五，本阶段只验证「时间点/间隔/时段/意图选择」的合理性。）

## 5. 配置项（`_conf_schema.json` 110 → 121 键，全部默认关闭兼容旧行为）

| 键 | 默认 | 含义 |
| --- | --- | --- |
| `living_enabled` | `false` | 总开关。**关=完全保持 v1.1.8 行为**；开=走本阶段新调度 |
| `living_min_silence_minutes` | 90 | 沉默下限 |
| `living_cooldown_minutes` | 180 | 冷却（用户回话也按此时长推后） |
| `living_daily_max` | 6 | 每日上限 |
| `living_quiet_hours` | `23:30-07:30` | 安静时段，`off` 关闭 |
| `living_offset_min` / `living_offset_max` | 5 / 45 | 决策到发送的随机延迟 |
| `living_ramp_minutes` | 240 | 概率斜坡 |
| `living_decision_check_minutes` | 10 | 未通过门禁时的重查间隔 |
| `living_persona_chars` / `living_worldview_chars` | 2400 / 1200 | 注入片段字数上限 |

## 6. 与旧实现的关系（不是并存两套）

- `living_enabled=false`：`initialize()` 走 `_setup_scheduler()`，行为与 v1.1.8 完全一致；
- `living_enabled=true`：`_setup_scheduler()` **不执行**，`cron_manager` 零注册；
  旧的 `daily_plan` / `_dispatch_due` / 每日预生成计划不再被调度（代码保留，供回滚与对照）。
- 记忆刷新：living 模式下由内部 scheduler 的 `living_memory` cron job 接管（否则 `memory_cron` 会随
  `cron_manager` 一起消失）。

## 7. 本阶段明确未做（留给后续阶段）

- 情感（PAD）状态、沉默评估注入、衰减目标偏移 —— 阶段三；`_living_compose` 只预留了注入位，当前不读 PAD；
- Mem0 读写与召回 —— 阶段二；`_living_memory_block` 仍复用旧的 KV 记忆摘要；
- 候选意图的评分-选择循环与学习进度门控 —— 阶段四；当前是加权随机 + 可解释权重；
- 傲娇的模型级审校 —— 阶段五；当前只有关键词硬过滤；
- 正文二次审校、跨会话全局工作空间、长期人格漂移检测 —— 未开始。
