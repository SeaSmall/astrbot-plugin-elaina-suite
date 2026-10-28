# 阶段六报告：整合自洽性彻查（v1.7.0）

> 规格出处：`m03635`《拟人聊天插件重构工程提示词（最终版）》阶段六——五项彻查：
> ① 情感是否真流动 ② 主动消息意图与情感/记忆/人格是否一致 ③ Mem0 召回是否被情感信号合理调制
> ④ 有无「僵尸功能」 ⑤ 设定分层是否无污染。
> 判据不是"用了多少框架"，而是**拟人感可观测提升**；每一结论都附可复现证据。

## 0. 本阶段最重要的一件事：查出一个让整个情感链路静默失效的集成 bug

`main.py` 四处调用点写的是：

```python
getattr(core, "enabled", lambda: False)()
```

而 `AffectCore` 表达"启用"用的是 `_on()`（`main.py:6096`，配置键 `affect_enabled`），没有 `enabled` 属性。
于是 `getattr` 每次都取到默认的 `lambda: False` → **返回 False**，四个下游全部静默关闭：

| 调用点 | 失效的能力 |
| --- | --- |
| `main.py:5216` | 主动消息的**情感门控**（难过时不再压低"兴奋分享"） |
| `main.py:5251` | 傲娇的**加密档位**（永远停在 0 档） |
| `main.py:5782` | 记忆块的**情感上下文** |
| `main.py:6949` | Mem0 召回的**情感调制**（阶段六新功能） |

也就是说：阶段三的情感一直在更新、一直在落盘、命令也能看，但**没有任何下游真正读到它**——
这正是规格里最担心的"僵尸功能"，而且是"看起来全绿"的那种：单元测试用带 `enabled()` 的 Fake 打桩，
阶段四/五的真机验证也用的是同一个 Fake，于是 4 个阶段都没暴露。

修复：新增模块级 `_core_enabled(core)`（`main.py:575`），依次尝试 `enabled()` 与 `_on()`，异常一律 False：

```python
def _core_enabled(core: object) -> bool:
    """统一判断"这个核心是否启用"。

    历史坑：四处下游写 getattr(core, "enabled", lambda: False)()，
    而 AffectCore 只实现 _on()，于是情感门控/傲娇加密/召回调制全部静默失效。
    """
```

四处调用点改为 `_core_enabled(...)`。回归测试新增 A13（低落 PAD 真的传到下游）与
A14（`_core_enabled` 兼容 `enabled()`/`_on()`/异常，且旧写法在源码里 0 处残留）。

## 1. ① 情感是否真流动（正常 → 沉默 → 恢复 全周期）

真机（容器内、真实 `AffectCore`、真实 `assets/persona.json`、真实配置；`_scratch/audit6_live.py`）：

| 阶段 | PAD | 说明 |
| --- | --- | --- |
| 初始基线 | `p=0.15, a=0.25, d=0.70` | 来自 persona.json 的 `baseline_pad` |
| 被夸（"你今天真好看，谢谢你陪我"） | `p=0.39, a=0.37, d=0.80` | 快通道更新，poignancy 3.0 |
| 被斥责（"你真烦人，闭嘴行不行"） | `p=0.13, a=0.43, d=0.68` | 愉悦回落、唤醒上升 |
| 沉默 30 小时 | `p=-0.14, a=0.505, d=0.50`，`silence_sev=1.0` | `silence_check` → `{'checked':1,'injected':1}`，日志 `[affect] 沉默评估注入负面事件：audit6_probe 偏离度=1.50` |

关键点：沉默**不是直接改数值**，而是注入一条负面评估事件走正常更新通道（`_apply`），
所以衰减、基线、后续恢复都仍然由同一套动力学决定。恢复对话后 `p` 从 -0.14 回升，
语气描述同步变化（`describe` 里出现"对方很久没理你：想确认又不想显得在意"）。

## 2. ② 主动消息意图与情感/记忆/人格三者一致

同一会话、同一时刻、**只改 PAD**（排除沉默加成：`silence_sev=0`、`last_user_at` = 30 分钟前），
候选池经 `MotivationCore.reweight()` 后：

| 情感 | share | care | followup | curiosity | winner |
| --- | --- | --- | --- | --- | --- |
| 愉悦 `p=+0.45` | **0.694** | 0.338 | 0.404 | 0.254 | **share**（"心情轻快，正想分享"） |
| 低落 `p=-0.55` | 0.250 | 0.405 | **0.424** | 0.154 | **followup**（追问"日本看枫叶/买相机"） |

- 低落时 share 被压到 0.25（×0.45），符合"沮丧时不做兴奋分享"；
- 沉默 8 小时 + `sev=1.0` 时 `check_in` 得 1.885 胜出（"心里不踏实，想确认对方还在"）——
  沮丧 + 长沉默 ⇒ "试探性确认"升权，正是阶段四规格要求的方向；
- 生成提示词核对：`{persona_block}` `{world_block}` `{memory_block}` `{affect_block}` `{intent_label}`
  占位**全部已替换**（`仍在=False`），记忆块 4 行，由头行"本次开口的意图：…（由头：…）"，
  情感块"愉悦 -0.55（很低落、心里发闷）…"——意图、情感、记忆、人格四者同时进入同一次生成。

## 3. ③ Mem0 召回被情感信号调制（本阶段唯一新增功能）

### 实现

新增配置三键（默认开）：`mem0_affect_recall` / `mem0_affect_boost_pct`(18) / `mem0_unresolved_boost_pct`(12)。

- `MEMORY_NEGATIVE_HINTS`：累 / 难受 / 疼 / 失眠 / 压力 / 烦 / 吵架 / 失败 / 担心 / 生病 / 加班 /
  难过 / 哭 / 焦虑 / 委屈 / 熬夜 …（情绪一致的线索词）
- `MEMORY_UNRESOLVED_HINTS`：还没 / 没定 / 待 / 下次 / 回头 / 要记得 / 答应 / 说好 / 没做完 /
  没决定 / 过两天 / 再看 / 等消息 / 面试 / 体检 / 出差 / 考试 …（没解决的事项）
- `LivingMemoryCore._affect_context(umo, affect)` → `{"p", "sev", "negative"}`，
  `negative = clamp(-p*1.6 + sev*0.6, 0, 1)`；`negative ≤ 0.05` 时调制完全不生效（等于原行为）。
- `rerank_affect(hits, umo, affect)`：`_rank = score + 负面词加权(18%)·negative + 未解决加权(12%)·negative`，
  按 `_rank` 排序并写 `_rank / _boost / _why`；首位有加成时打一条
  `[memory] 情感调制召回：负面强度=… 首位加成=…（情绪一致/未解决）`。
- `recall_block(query, umo, chars, affect)`：负面强度 > 0.05 时把候选扩到 `min(24, base_k*3)` 再重排取前 `base_k`——
  先广撒网再按情绪收敛，避免"只召回 6 条还全不相关"。

### 真机证据（真实 qdrant + 真实 fastembed，独立脚本）

探针 3 条：失眠压力（负面）、雨天听爵士乐（中性）、体检还没定（未解决）。

| 查询"最近身体怎么样，心情如何" | 中性召回 | 低落（`p=-0.6, sev=0.4`，negative=1.0） |
| --- | --- | --- |
| 失眠压力 | 0.588 | **0.768**（`_why=情绪一致`） |
| 雨天爵士乐 | 0.608 | 0.608 |
| 体检没定 | 0.480 | **0.600**（`_why=未解决`） |

日志：`[memory] 情感调制召回：负面强度=1.00 首位加成=0.180（情绪一致）`。
结论：负面情绪下"情绪一致 + 未解决"的记忆被抬到前面，随机事实（爵士乐）不动——正是规格 ③ 的要求。

> 操作坑（写进报告供后来者）：运行中的 AstrBot 独占 `<data>/mem0_qdrant`，独立脚本要
> `docker exec … cp -r /AstrBot/data/mem0_qdrant /tmp/audit6_qdrant` 后把 `vector_store.config.path`
> 与 `history_db_path` 改到副本；`search(umo)` 内部会给 user_id 加 `u_` 前缀，探针 umo 传 `audit6probe`。

## 4. ④ 无僵尸功能：每个组件都要有「生产者 → 消费者 → 可观测出口」

静态审计脚本 `_scratch/audit6_static.py`（结论 PASS）：

| 组件 | 生产者 | 消费者 | 可观测出口 |
| --- | --- | --- | --- |
| 情感（阶段三） | `_apply` / `_reflect` / `silence_check` | `_affect_ctx` / `_tsundere_encrypt_level` / `_affect_context` / `{affect_block}` | `[affect]` 日志、`/情感状态`、`affect_state.json` |
| 动机/注意力（阶段四） | `observe_user` / `note_attempt` / `_best_pending_topic` | `reweight` / `select` | `[motivation] 注意力竞争`、`/动机状态`、`motivation_state.json` |
| 长期记忆（阶段二/六） | `_write` / `seed_worldview` | `recall_block` / `rerank_affect` | `[memory]` 写入与调制日志、`/记忆状态` |
| 傲娇（阶段五） | `tsundere_chat_block` / `_living_tsundere_review` | `on_llm_request` / 生成重试 | `[tsundere]` 丢弃日志 |
| 主动调度（阶段一） | `_living_start` / `_living_decide` | `_living_fire` / `_living_compose` | `[living]` 日志、`/主动状态`、`proactive_state.json` |

- 旧写法 `getattr(core, "enabled", …)()` 残留：**0 处**（本阶段已全部换成 `_core_enabled`）。
- 配置键僵尸扫描：`_conf_schema.json` 159 键，`main.py` 里从未引用的 **0 个**。
  其中 14 个（`guard_retry_*` / `health_*` 等）是通过 `self.config.get("guard_" + key)` 与
  `self.config.get("health_" + key)` 动态前缀访问，脚本单列为"间接访问"而非僵尸。

## 5. ⑤ 设定分层无污染

| 检查项 | 结果 |
| --- | --- |
| persona 资源只经 `_load_plugin_json_asset` 加载 | OK |
| worldview 资源加载 | OK |
| 运行时不再读服务器人格文档（`/opt/astrbot` / NAS / `persona_source`） | OK |
| 人格进 system prompt（对话层常驻） | `tsundere_on_llm_request` → `req.system_prompt` |
| 生成层人格块 / 世界观块（主动消息） | `_living_persona_block` / `_living_world_block` |
| 世界观事实走 Mem0 而非硬编码 | `seed_worldview` 写入 34 条 `kind=worldview` 种子 |

分层结论：人格是**常驻核心层**（system），世界观事实是**可按需召回的背景层**（Mem0），
情境信息（记忆块 / 情感块 / 意图）作为独立段拼在提示词里，不与人格混写。

## 6. 测试与回归

| 文件 | 组数 | 结果 |
| --- | --- | --- |
| `tests/test_merged.py` | 24 | 全过（`ALL SMOKE TESTS PASSED`） |
| `tests/test_living.py` | 12 | 全过 |
| `tests/test_affect.py` | 14（新增 A13/A14） | 全过 |
| `tests/test_memory.py` | 13（新增 M11/M12/M13） | 全过 |
| `tests/test_motivation.py` | 10 | 全过 |
| `tests/test_tsundere.py` | 10 | 全过 |

运行：`PYTHONIOENCODING=utf-8 py tests/test_memory.py`（依此类推）。

## 7. 本阶段的偏离与取舍

1. **情感调制召回用"线索词加权"而不是 embedding 情绪分类**：规格要求"优先召回情绪一致或未解决事项"，
   没规定实现方式。用词表加权可解释、零额外依赖、可关（`mem0_affect_recall=false`）；
   代价是对反讽/间接表达不敏感。已在 `MEMORY_NEGATIVE_HINTS` 里尽量覆盖口语说法。
2. **候选池扩大 3 倍再重排**：若只重排 top_k 条，召回集里可能根本没有情绪一致的记忆，调制就成了摆设。
   上限 24 条防止负面状态下的额外开销失控。
3. **`silence_sev` 参与 negative 计算**：规格只提"情感信号"，沉默严重度本身就是情感状态的一部分
   （阶段三静默补丁写入），纳入后"很久没说话 + 心情差"会更强地偏向未解决事项。

## 8. 遗留

- Mem0 写入侧（`_write`）仍是 `flush_turns` 批量提交，没有在负面情绪下主动"记更多"；
  规格未要求，暂不做（避免无判据的复杂度）。
- 情感调制只作用于主动消息的记忆块；正常对话回复用的 Mem0 召回尚未接入调制（对话层由 LLM 自行判断）。
  若后续要接，入口在 `tsundere_on_llm_request` 同一处（`on_llm_request`）。

## 9. 相关文件

- 代码：`main.py`（`_core_enabled` @575、`MEMORY_NEGATIVE_HINTS`/`MEMORY_UNRESOLVED_HINTS`、
  `LivingMemoryCore._affect_context`/`rerank_affect`/`recall_block`、`_living_memory_block`）
- 测试：`tests/test_affect.py`（A13/A14）、`tests/test_memory.py`（M11–M13）
- 审计脚本：`_scratch/audit6_live.py`（真机五项）、`_scratch/audit6_static.py`（④⑤ 静态）
