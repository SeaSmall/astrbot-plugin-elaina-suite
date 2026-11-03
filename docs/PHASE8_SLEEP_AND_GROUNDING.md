# PHASE8 报告：睡眠时段不计数 + 主动消息「由头」必须来自真实记忆（v1.11.0）

## 一、问题陈述（用户原话）

1. 「在情感上问题，长时间不回复会有变化，但是人是要睡觉的，所以睡觉时间要包含在内，
   要不每天早上的主动人格总说已经七个小时没找我了什么什么的」
2. 「还有个动机队列，总说这个事，这个事是什么是完全自己认为的，这个需要结合记忆去做，
   而不是自己去猜」

两条其实是同一个毛病的两面：**插件把「它自己想象出来的事实」当成了事实**。
第一条是「沉默时长」这个事实算错了（把睡觉算成冷落），
第二条是「上次那件事」这个事实根本不存在（模型现编）。

## 二、改动一：沉默与情绪衰减只数清醒时间

### 配置

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `living_sleep_aware` | bool | `true` | 主动消息是否扣除睡眠 |
| `living_sleep_hours` | str | `""` | 睡眠窗口，留空沿用 `living_quiet_hours` |
| `affect_sleep_aware` | bool | `true` | 情感衰减/沉默严重度是否扣除睡眠 |
| `affect_sleep_hours` | str | `""` | 同上，留空则回落 `living_sleep_hours` → `living_quiet_hours` |

**为什么留空要回落而不是各自默认 `23:30-07:30`**：安静时段（`living_quiet_hours`）
本来就是「别在这个时段发消息」，和睡眠时段在现实里高度重合。若两套时间各配一份，
用户改了一处另一处不同步，会出现「安静时段没消息、情感却按醒着算沉默」的割裂。
交接口径是单向的：`affect_sleep_hours` → `living_sleep_hours` → `living_quiet_hours`，
一处设置即可。

### 实现

模块级纯函数（不依赖插件状态，可独立单测）：

```python
_parse_sleep_range(raw) -> tuple[int, int] | None      # "23:30-07:30" → (1410, 450)
_sleep_minutes_between(t0, t1, rng, tz) -> float       # 区间内落在睡眠窗口的分钟数
_awake_minutes_between(t0, t1, rng, tz) -> float       # 墙钟分钟 - 睡眠分钟（下限 0）
```

- `_sleep_minutes_between` 从 `local_lo.date() - 1` 起**逐日展开**睡眠窗口
  （`End <= Start` 视为跨午夜，窗口终点 +1 天），与查询区间求交后累加。
  因此跨午夜、跨多天都正确；迭代上限 400 天，防止异常时间戳打爆。夏令时
  （`America/New_York` 2026-03-08）实测 23:30→07:30 只得到 420 分钟，没有被
  「钟拨快」多算或多扣。`tz=None` 时退化为服务器本地时区。
- 解析容错：`-`、`~`、`—`、`至`、`到` 都算分隔符；空 / `off` / `none` / `0` /
  `false` / `no` / 起止相同 / 非法时间 → `None`（=不豁免，旧行为）。

接点：

| 位置 | 旧 | 新 |
| --- | --- | --- |
| `_living_should_initiate` | `silence_min = (now - last) / 60` | `self._living_awake_minutes(last, now)` |
| `_living_candidates` | `silence_h = (now - last) / 3600` | `self._living_awake_gap_min(s, now_dt) / 60` |
| `_living_compose` | `gap_min = (now - last) / 60` | `self._living_awake_gap_min(s, now_dt)` |
| `AffectCore.get_state` | `dt_min = (now - updated) / 60` | `self._awake_minutes(updated, now)` |
| `AffectCore.silence_severity` | `silence = (now - last_user_at) / 60` | `self._awake_minutes(last_user_at, now)` |

提示词：`LIVING_PROMPT_TEMPLATE` 的间隔行改为 `距离上次互动：{silence}{sleep_note}`，
`sleep_note` 只在被扣掉的睡眠 ≥ 30 分钟时出现：

> （其中约 8 小时是各自的睡眠时间，已经扣除；不要把睡觉当成对方冷落你）

## 三、改动二：由头必须来自真实记忆

### 旧行为为什么必然现编

- `_living_candidates` 无条件把 `followup`（「追问之前没说完的话题」）与
  `curiosity`（「对对方提过的东西产生新的好奇」）放进池子，没由头时只把
  `MotivationCore.reweight` 的 gate 乘成 `0.35`——**它仍然在池子里，仍然能赢**；
- `care` 的 `reason` 写死为「记起了对方提过的事」，无论是否真有这件事；
- 生成时提示词里除了 `（由头：…；具体到：…）` 这个字符串，没有任何事实来源，
  模型只能照着 label 编一个「上次那个事」。

### 新数据流

```
用户原话 ──observe_user──> pending 话题池 ─┐
  （meaningful_topic 过滤寒暄）            ├─> _living_grounding_evidence(umo, limit=4)
mem0 召回 ──recent_text → search()───────┘        │ 去重（前 20 个 alnum 字符）+ 截断
                                                  ▼
                              _living_candidates: 无 evidence → 不生成 followup/curiosity
                                                   care.reason = evidence[0] 或 ""
                                                  ▼
                              mot.reweight(...) → mot.grounded_only(out, evidence)  # 兜底闸门
                                                  ▼
                    _living_plan_intent: evidence = _living_merge_evidence(cands)
                                                  ▼
                              _living_decide: s["pending"]["evidence"] = [...]
                                                  ▼
                              _living_fire → _living_compose(..., evidence)
                                                  ▼
                       提示词：【你确实记得的事（只能引用这些，不许扩写或替换）】
```

关键取值与理由：

- `_living_merge_evidence` 合并**所有候选**的证据而不是只取赢家。理由：赢的经常是
  `share`（分享自己），但「你确实记得的事」对任何意图都是同一份事实基础；只取赢家
  会让 `share` 场景下提示词里一条事实都没有，又退回「自己猜」。
- `_living_compose` 遇到 `followup`/`curiosity` 且 `evidence` 为空时**直接返回 `""`、
  不调用 LLM**。理由：宁可这一次不说，也不能让它编一句。这是最后一道闸门，
  独立于候选池的过滤逻辑。
- `meaningful_topic`：去标点空白后长度 < 4、命中停用词（`你好`/`在吗`/`嗯`/`晚安`/
  `哈哈哈`/`谢谢`/`没事`/`算了`/`不知道`/`拜拜` 等）、纯数字、或「重复音节」
  （`len(set(low)) <= 2 and len(low) <= 6`，覆盖 `嗯嗯嗯`/`好好好`）都判为无意义。
  不过滤的话，一句「晚安」就会在 48 小时后变成「由头」，照样是编的。
- 记忆召回失败（mem0 未开/初始化失败/查询异常）一律**按无由头处理**并记日志，
  不抛异常、不影响主动消息其它部分——没有记忆时退化为「只关心、不追问」。

## 四、验证

```
$ cd elaina-work && for f in tests/test_*.py; do python3 $f; done
tests/test_affect.py               PASS
tests/test_global.py               PASS
tests/test_grounded_intent.py      PASS   # 新增 G1~G7
tests/test_living.py               PASS
tests/test_memory.py               PASS
tests/test_merged.py               PASS
tests/test_motivation.py           PASS
tests/test_sleep_aware.py          PASS   # 新增 S1~S8
tests/test_tsundere.py             PASS
```

上游测试改了两处 fixture（显式关闭豁免），理由：上游用**真实** `time.time()`，
若不关闭，半夜跑测试时 `now + 120min` 会跨过 23:30 的睡眠边界，衰减断言就会随
运行时刻变化（实测 23:11 跑 a1 得到 `0.724` 而断言是 `0.4`，正是这个原因）。
睡眠逻辑本身由 `test_sleep_aware.py` 用固定时间戳单独覆盖，不受运行时刻影响。

### 已知限制

- 睡眠时段是**静态配置**，不支持「周末睡到十点」这类按星期变化；如需，后续可在
  `_parse_sleep_range` 外层做多段配置，纯函数无需改动。
- 「由头」的质量受限于上游记忆：`mem0_enabled` 关掉且用户没说过具体内容时，
  按设计就只剩关心类主动消息（这是刻意的，不是 bug）。
- 判断「有无由头」看的是 `evidence` 是否非空，不做语义相关性打分；代价是偶尔
  由头与当下时间氛围不搭，收益是绝不会编。
