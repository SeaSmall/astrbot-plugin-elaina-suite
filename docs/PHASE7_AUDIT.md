# 阶段零 · 彻查报告（代码侧）：情感读写点 + 记忆读写点

- 仓库：`D:\deepseek\astrbot-plugin-elaina-suite`，`main.py` 共 8702 行，版本 **1.9.0**。
- 服务器侧插件/数据库/文件清单见 `docs/PHASE7_SERVER_INVENTORY.md`（由 server-auditor 产出）。
- 本文件所有行号基于 1.9.0 的 `main.py`，改动后需同步更新。

---

## A. 情感状态（`class AffectCore`，main.py:6271–6826）

### A1 存储与分区键（结论：仍是「会话分区」，只是折叠成一个 key）

| 项目 | 事实 | 位置 |
| --- | --- | --- |
| 状态文件 | `<插件数据目录>/affect_state.json` | `_state_file()` main.py:6315 |
| 文件结构 | `{"version":1, "sessions":{<sid>:{umo, pad, baseline, updated, poignancy, events, silence_sev, last_user_at}}}` | `_load()` main.py:6334、`_entry()` main.py:6451 |
| 分区键 | `_sid(umo)`：开启 `shared_scope_enabled` 时返回 `"shared_"+shared_scope_id`（服务器当前 `shared_<scope>`），否则按 `umo` 清洗截断 | `_sid()` main.py:6319 |
| 锁 | 每个 sid 一把 `asyncio.Lock`（`_locks` main.py:6277） | `_lock()` main.py:6326 |
| 落盘 | `.part` + `os.replace` 原子写 | `_save()` main.py:6353 |

**判定：`shared_scope` 只是把 N 个会话重命名成 1 个 key，数据模型仍是 `sessions: {sid: 状态}`；真机 `affect_state.json` 里同时残留旧会话 key（`default_<qq>_FriendMessage_<openid>`）与 `shared_<scope>`。不符合「全局唯一、无分区键」。**

### A2 读点（全部经 `get_state()` main.py:6468 / `_entry()` main.py:6451）

| # | 读取方 | 用途 | 位置 |
| --- | --- | --- | --- |
| 1 | `_living_memory_block()` | PAD 传给 Mem0 召回做情感重排 | main.py:5256–5262 |
| 2 | `_tsundere_encrypt_level()` | 傲娇「加密档位」0/1/2 | main.py:5291–5306 |
| 3 | `_living_compose()` | `affect_core.describe(umo)` → 「此刻心情」块 | main.py:5494–5495 |
| 4 | `LivingMemoryCore._affect_context()` | 负面强度阈值、召回加宽 | main.py:5953–5972 |
| 5 | `MotivationCore._affect_ctx()` | 候选意图情感门控系数 | main.py:7114–7135 |
| 6 | `/情感状态` 命令 | `status()` 展示 | main.py:8632 |
| 7 | 内部 | `silence_severity`(6649)/`silence_check`(6660)/`describe`(6701) 均调 `get_state` | — |

### A3 写点

| # | 写入方 | 动作 | 位置 |
| --- | --- | --- | --- |
| 1 | `guard_on_user_message`（任何消息） | `note_user(umo)` → 重置 `last_user_at`、`silence_sev=0` | 调用 main.py:8489 / 定义 main.py:6478 |
| 2 | `affect_on_llm_response`（priority=99997，每轮回复后） | `fast_update(umo, user_text, reply)` → `_apply()` 更新 pad/poignancy/events | 调用 main.py:8529–8534 / 定义 main.py:6545、`_apply` 6566 |
| 3 | `_silence_tick()` 每 `affect_silence_check_minutes`(默认15) 分钟 | `silence_check()` 遍历 `sessions` 逐个 `_apply(kind="silence")` | main.py:6660–6691、job 6766–6799 |
| 4 | `initialize()` / `close()` | 建会话条目 / 收尾落盘 | main.py:6772–6802、6804–6826 |

### A4 沉默判定基准（结论：按**会话**，不是全局）

- `silence_severity(umo)` main.py:6649：`silence = now - st["last_user_at"]`（该 sid 自己的时间戳），`dev = silence / _expected_gap_minutes()`（main.py:6645，`affect_silence_threshold_minutes` 默认 720 分钟 = 12h），`sev = clamp(dev - 1, 0, 1.5)`。
- 冷却按 sid 记在内存 `_last_silence_at[sid]`（main.py:6688，`affect_silence_cooldown_minutes` 默认 120）。
- 注入通道：`_apply(delta, kind="silence", note=AFFECT_SILENCE_APPRAISAL, poignancy=8.0)` —— 走正常更新通道，未直接改 PAD（符合阶段三设计）。
- **新会话冷启动问题**：`initialize()` main.py:6782 会给 `_collect_target_sessions()` 每个会话 `_entry()` 建基线 → 新会话永远从基线开始，共享不到"当前情绪"。

### A5 情感影响生成的路径（改造后必须保持）

`describe()` → `{affect_block}` 注入 `LIVING_PROMPT_TEMPLATE`（main.py:5494）→ 主动消息；`persona_chat_block()` / `tsundere_chat_block()` 走 `on_llm_request`（main.py:8504 附近）；`_affect_ctx` → 候选评分（main.py:7114）；`_affect_context` → Mem0 召回重排（main.py:5953）。

---

## B. 记忆：当前有 **三条并行路径** + 一个外部文件源

### B1 旧 KV 记忆（`/记忆` 显示的那份）

| 项目 | 事实 |
| --- | --- |
| 存储 | AstrBot KV key `memory_store`（`MEMORY_KEY` main.py:325），落在 `data_v4.db` 的 `preferences`（`scope='plugin'`） |
| 结构 | `{updated, items:[{umo, summary, updated}], merged}` |
| 写入 | `refresh_memory()` main.py:3854 → `put_kv_data(MEMORY_KEY, store)` main.py:3940 |
| 写入触发 | ①`_on_memory_time` cron（main.py:4434，`memory_cron`）②`/刷新记忆` main.py:4470 ③`/记忆重置` main.py:4478 ④`_collect_memory` 冷却到期就地刷新 main.py:4076–4083 ⑤记忆库为空首次生成 main.py:4087 |
| 读取 | `_load_memory_store` main.py:3951 → `_memory_text` main.py:3983 → `_collect_memory` main.py:4064 → 注入 |
| 展示 | `memory_command` main.py:4441（`/记忆`），只读 KV，**看不到 Mem0** |
| 上游 | `_history_lines` main.py:4142（conversation_manager 历史）；`_collect_user_notes` main.py:4020（读 `<data>/workspaces/*/user_profile.md`） |

### B2 Mem0（长期记忆，主动消息实际在用）

| 项目 | 事实 |
| --- | --- |
| 存储 | `<data>/mem0_qdrant`（qdrant 嵌入式） + `<data>/mem0_history.db`，collection `elaina_memories` |
| 写入 | `on_user_message` main.py:6113 缓冲 → `on_llm_response` main.py:6128 每 `mem0_flush_turns`(4) 轮 → `_write` main.py:6149 → `mem.add(..., user_id=_uid(umo), infer=mem0_infer)` |
| 种子 | `seed_worldview()` main.py:6064 → `assets/worldview.json` 的 facts 写入 `user_id="elaina_world"`（34 条） |
| 读取 | `recall_block` main.py:6038 ← `search` main.py:6011 ← `_living_memory_block` main.py:5247；`rerank_affect` main.py:5975 情感重排 |
| scope | `_uid(umo)` main.py:5827 = `"u_"+_sid(umo)`；`_sid` main.py:5821（受 `shared_scope` 影响）→ 当前 `u_shared_<scope>`；世界观固定 `elaina_world` |

### B3 原始历史回退

`_collect_raw_history()` main.py:4122，开关 `memory_raw_fallback`（默认 true）；仅在 B1 为空且允许回退时兜底注入原始对话行。

### B4 分裂现状（这就是要修的）

- **AI 实际看到**：`_living_memory_block()` main.py:5247–5285 = Mem0 召回块 **+** KV `merged` 块（空行拼接）。
- **用户 `/记忆` 看到**：只有 KV `items`/`merged`（main.py:4441），**完全看不到 Mem0**。
- **结论**：同一段对话产生两份记忆（KV 摘要 + Mem0 事实），召回时两份都塞进提示词（可能互相矛盾），展示只有一份 → 正是规格里「AI 用 A 份、`/记忆` 显示 B 份」。

---

## C. 旧插件清单

见 `docs/PHASE7_SERVER_INVENTORY.md`：服务器 `plugins/` 现有 `astrbot_plugin_affection`（疑似旧情感/好感度实现，重点嫌疑）、`astrbot_plugin_password`、`astrbot_plugin_smart_segmentation`、`astrbot_plugin_gif_vision_helper.zip`（未解压）、`astrbot_plugin_elaina_suite`（本插件）。

---

## D. 写代码前必须先定的决策（已按规格记录）

1. **沉默基准 = 方案一（主要交互对象）**：新增配置 `affect_primary_user_id`（空 = 回退方案二：全局最后一次任意交互）。沉默评估只针对主要对象；其他会话交互更新 PAD 但不重置该计时。
   - 理由：单主人格部署（QQ + 语音两条通道同属一个人）；主人格的「被忽视感」必须绑定主人，方案二语义太弱。
2. **Mem0 scope 归一**：`user_id` 固定为 `shared_scope_id`（服务器 `<user>`），不再按 `umo` 派生；`agent_id` 留空（单人格不额外分区）。世界观事实迁进同一 scope，用 `metadata={"kind":"worldview"}` 标记；展示时按 kind 分组。
   - 理由：spec 要求「全插件只读写这一个 scope，不得混用多个 scope 导致记忆分裂」；单用户部署固定单一 `user_id` 是被允许的简化。
3. **KV `memory_store` 与 `workspaces/*/user_profile.md`**：迁移内容进 Mem0 后**停止读写**（代码路径删除，或在文档声明废弃并默认关闭），`/记忆` 改为直查 Mem0；迁移后 `memory_enabled` / `memory_notes_enabled` 相关键标记废弃，避免第二记忆源复活。
4. **情感文件**：`affect_state.json` → `global_affect.json`，结构从 `{"sessions":{...}}` 变为单对象 `{pad, baseline, updated, poignancy, events, silence_sev, last_user_at, version}`；迁移取**最近一次更新值**（`shared_<scope>` 优先，缺失则取 mtime 最新会话），并在日志与文档写明。
