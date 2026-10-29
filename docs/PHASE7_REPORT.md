# 阶段七总报告：情感全局化 + 记忆源归一 + 服务器清理（v1.10.0 → v1.10.1）

> 对应任务：《情感全局化 + 记忆源归一清理 · 工程提示词》。
> 两条配套查档：客户端代码侧彻查见 [`PHASE7_AUDIT.md`](PHASE7_AUDIT.md)，服务器只读清单见 [`PHASE7_SERVER_INVENTORY.md`](PHASE7_SERVER_INVENTORY.md)。
> 执行纪律：先彻查后改造、先备份后清理、先决策后编码、单点变更逐步验证（情感先上线验证，再做记忆，最后清理）。

---

## 0. 三条硬约束与最终结论

| 约束 | 结论 | 证据 |
| --- | --- | --- |
| 情感状态全局唯一（不按会话/用户/轮次分区） | ✅ 已实现 | 单一 `global_affect.json`，无 `sessions` 键；任意会话 `/情感状态` 读到同一份 PAD |
| 记忆源唯一（Mem0 是唯一权威源） | ✅ 已实现 | 写入 / 召回 / `/记忆` 展示 / 计划水位四条路径同指 Mem0 单一 scope `u_<scope>`；KV 摘要、`user_profile.md`、旧 scope 已备份后清除 |
| 现有功能不退化 | ✅ 已验证 | 七套回归测试全绿 + 真机五轮对话、人格注入、情感、召回、主动消息调度均正常 |

清理规模：`data_v4.db` 9.7 MB → 2.5 MB，插件 7 个 → 3 个，可回收快照/备份/旧表情 zip 共约 57.6 MB（另有 v1.10.0/v1.10.1 两个部署回滚包，故意保留，见第 5 节）。

---

## 1. 阶段零：彻查（只读，未动任何数据）

- **客户端**：`docs/PHASE7_AUDIT.md` —— 情感状态的存储与全部读写点、沉默判定基准、`/记忆` 实现、记忆全部读写点、旧代码残留符号清单。
- **服务器**：`docs/PHASE7_SERVER_INVENTORY.md`（452 行）—— 插件清单与加载状态、所有记忆/情感相关文件与数据库、A/B/C 三张表（A=可清 9 项、B=无关勿动 10 项、C=需用户确认 9 项）、可复核命令。
- 彻查得到的三个硬事实（决定了后续动作顺序）：
  1. **第二套情感实况在跑**：`astrbot_plugin_affection` v1.2 已加载，`main.py:202` 的 `on_llm_request(priority=10)` **无开关**地把「好感度/力比多/攻击性」面板追加进主 LLM 的 `system_prompt`，与套件 PAD 并存且互不同步（该插件里对应用户好感度已封顶 100.0）。
  2. **记忆双源**：`data_v4.db` 的 `preferences(scope='plugin', key='memory_store')` 2144 字符、当天仍在写；Mem0 侧 history 56 行 / qdrant 56 点。`/记忆` 读 KV、AI 召回读 Mem0 —— 正是「AI 用 A 份、`/记忆` 显示 B 份」。
  3. **旧情感文件不能先删**：部署版仍是旧 `sessions` 结构（`affect_state.json` 2038 B，含旧会话 key + `shared_<scope>`），必须**先升级到含迁移逻辑的版本**再迁移。

---

## 2. 阶段一：情感全局化（v1.10.0）

### 2.1 代码改动

- 新增**单一全局状态文件** `global_affect.json`：只有全局 PAD、`events`（最近事件）、`poignancy`、`silence_sev`、`last_any_at` / `last_user_at`、`baseline`、`version: 2`——**结构里不存在任何 session_id / user_id 分区键**。
- 删除全部按会话存取路径：`AffectCore._sid()` 与会话键派生逻辑移除，所有读写都落同一个对象（测试 A15 断言「语音与 QQ 读到同一份 PAD，已无 `_sid` 分区」）。
- **衰减按全局时间轴**：以「距全局最后一次任意交互」为输入，目标不是基线而是沉默目标 `_silence_target(sev)`；`_decay_to(pad, dt_min, target, half_life)` 用 `k = 2.0 ** (-dt_min / max(1.0, half_life))`，半衰期 `affect_decay_half_life_minutes` 默认 120 分钟。
- **沉默基准显式二选一**（写进代码注释与文档）：配置 `affect_primary_user_id` 非空 → **方案一**（单主人格：只评估主要对象的沉默，其他会话互动不重置该计时）；为空 → **方案二**（全局静默）。`_is_primary()` 支持逗号分隔多标识（同一个人 QQ openid + `webchat` 用户名都算），服务器配置为 `<openid>,<user>`。
- 迁移：启动时若发现旧 `affect_state.json`，按「优先 `shared_*` 作用域、否则最近更新」合并成**唯一一份**全局状态，旧文件改名为 `.migrated` 保留，日志 `[affect] 已迁移旧情感状态为全局单例（来源 …, 旧文件共 N 个会话）`。
- 慢通道反思、生成侧注入均读同一份全局 PAD（`【她此刻的心情】` 段落）。

### 2.2 真机验证

- 首次启动日志：`[affect] 情感状态已启动（全局单例 PAD，衰减 T½=120 分钟，沉默评估每 15 分钟，基准：主要对象 <openid>,<user>）`，同时 `.migrated` 生成、`global_affect.json` 落盘（1.6 KB，无 `sessions` 键）。
- `/情感状态`（语音会话 `webchat_phone!<user>!main`）：`愉悦-0.24 / 唤醒+0.09 / 掌控-0.11，poignancy 39.0，事件 13 条，沉默基准：主要对象 <openid>,<user>，状态文件：/AstrBot/data/global_affect.json`。
- 跨会话一致性：另开两个会话（`webchat_probe!<user>!main` 与 `webchat_other!stranger!main`）分别查 `/情感状态`，均返回「全局唯一 · Sentipolis PAD」，数值与上一条轨迹一致（陌生人会话不重置主要对象沉默计时，但情绪可观测）。

---

## 3. 阶段二：记忆源归一（v1.10.0，v1.10.1 补写入质量）

### 3.1 单一权威源与单一 scope

- **Mem0 是唯一权威源**，四条路径全部指向它：
  - 写入：用户+助手配成一轮，攒够 `mem0_flush_turns` 轮后后台批量写；
  - 召回：生成前检索，命中注入 `【你记得的事】`；
  - 展示：`/记忆` 直接调 `list_memories()` 列 Mem0 条目；
  - 水位：计划是否基于旧记忆，按 Mem0 最后一次成功写入时间（`mem0_meta.json`）判断。
- **scope 唯一**：`_uid()` 只产出 `u_` + 作用域（填了 `shared_scope_id` 就用它，否则 `owner`），代码里不再有按会话派生 user_id 的路径；`scope_id()` 返回同一值，`/记忆`、`/刷新记忆`、`/记忆重置` 都用它。跨渠道多标识归一由 `shared_scope_enabled=true` + `shared_scope_id=testuser` 保证。
- **删除的三条旧链路**：KV 里的 AI 摘要（`memory_store` / `refresh_memory` / `_on_memory_time` / `MEMORY_MERGE_PROMPT` 等一整套）、工作区 `user_profile.md` 用户笔记、裸历史回退。测试 M16 用死符号清单钉死「旧链路必须不存在」。
- **迁移与去重**：`migrate_scopes()` 幂等合并旧 scope（`mem0_migrate_legacy`），`seed_worldview()` 按文本去重，新增 `dedupe_scope()` 在合并后清理同文本重复条目。启动顺序修正为 **先合并 → 再播种 → 最后去重**，避免「播种 34 条 + 旧 scope 再合并 34 条 = 68 条重复」。
- `/刷新记忆` = `flush_now()`，`/记忆重置` = 只清当前 scope + 重播 34 条世界观。

### 3.2 服务器清理（用户确认后执行，全部先备份）

备份目录 `/opt/astrbot/data/_cleanup_bak_20261029-105936/`：`user_profile.md`、`affect_state.json.migrated`、`memory_store` 原文 JSON、`affection_plugin_state.tgz`、`gif_vision_helper_zip.tgz`。

| 动作 | 对象 | 结果 |
| --- | --- | --- |
| 停用并删除旧情感插件 | `astrbot_plugin_affection`（v1.2）+ `plugin_data/astrbot_plugin_affection/` 68K | 日志中不再加载；面板注入消失 |
| 清空旧记忆 KV | `preferences(scope='plugin', key='memory_store')` 2144 字符 | 删除 + VACUUM，`data_v4.db` 9.7 MB → 2.5 MB |
| 删旧用户笔记 | `workspaces/default_<qq>_FriendMessage_…/user_profile.md` 455 B | 已备份后删除 |
| 删旧 skill 备份 | `_skill_bak_20261028-133600/` 38 MB（含 elaina-agent memory/index.sqlite3 27.8 MB） | 已删 |
| 删重叠回滚快照 | `_clear_bak_20261028-131545/` 9.6 MB、`backup_before_del_20261017_081325.db` 9.7 MB | 已删 |
| 删杂项 | `memory_store_backup_20261018_080835.json`、`probe_mem0.py`、`gif_vision_helper.zip`（从未加载） | 已删 |
| 清 Mem0 低质量条目 | 56 条中 22 条是原始对话原句（「<对话原句已脱敏>」…） | 已删，剩 34 条世界观事实 |

清理后 `plugins/` 只剩 `astrbot_plugin_elaina_suite`、`astrbot_plugin_password`、`astrbot_plugin_smart_segmentation`；重启日志 `1.10.0`、`mem0 就绪（2.7s）`、`世界观点种子记忆：新增 0 条`。

### 3.3 v1.10.1：写入前提炼事实（`mem0_distill`，默认开启）

- 问题：写入用 `infer=False`，mem0 不提炼 → 寒暄原句被当记忆（实测 56 条里 22 条）。
- 试过 mem0 自带 `mem0_infer=true`：真机直接失败 —— `LLM extraction failed: Could not resolve authentication method. Expected one of api_key, auth_token, or credentials to be set.`（mem0 要独立的 openai 兼容 key，而 AstrBot 的 provider 是运行期注入的，插件拿不到明文 key；默认 `mimo-v2.6-pro` 也不在 Token Plan 已知列表）。**结论：不走 mem0 自带抽取。**
- 改为**插件自己提炼**：用对话模型（`ctx.get_using_provider_async()` + `ctx.llm_generate`，失败回退 `get_using_provider().text_chat`）把每轮批量消息压成 1~3 条「关于用户的事实」（第三人称、每条 ≤40 字、没有就输出 `NONE`），再逐条 `mem.add(..., infer=False, metadata={"distilled": True})`。没有事实时整轮跳过并记 `[memory] 本轮未提炼出值得长期记住的事实（N 条消息，不写入）`，**不退回存原文**；提炼异常不影响聊天。
- 新增配置 `mem0_distill`（bool，默认 true），配置面板 154 → **155** 项；服务器同时满足 `mem0_infer=false`、`mem0_distill=true`。
- 真机验证：连发 6 条带事实的对话后日志 `[memory] 提炼写入 3 条事实（11.5s, scope=u_<scope>）：用户计划12月20日赴哈尔滨看冰雕，机票已订。 / 用户每天早上七点在阳台练习八段锦约二十分钟。 / 用户母亲某某62岁，膝盖不好；养的橘猫豆豆三岁，怕打雷。`，`/记忆` 从 34 条 → **37 条**，新增三条全部是提炼后的事实（不含对话原句）。

---

## 4. 阶段三：回归与自洽

### 4.1 测试（七套全绿，`PYTHONIOENCODING=utf-8 py tests/test_*.py`）

| 套件 | 覆盖点 | 结果 |
| --- | --- | --- |
| `test_global.py`（G1–G8） | 全局单例结构、无分区键、迁移策略、沉默基准二选一、多标识主要对象、代码无分区残留 | 全过 |
| `test_affect.py`（A1–A15） | PAD 规则/衰减/注入、跨会话同一份 | 全过 |
| `test_memory.py`（M1–M18） | 懒加载单例、召回契约、种子幂等、缓冲写入、降级、单一 scope、水位与迁移、`/记忆` 直查 Mem0、启动顺序与去重、**M18 事实提炼** | 全过 |
| `test_living.py`（L1–L13） | 主动消息决策、非目标会话不排消息 | 全过 |
| `test_motivation.py`（N1–N11） | 共享作用域同一份话题 | 全过 |
| `test_tsundere.py`（T1–T12） | 人格在前、傲娇在后、幂等 | 全过 |
| `test_merged.py`（冒烟 21 组） | 全链路接线、旧记忆链路死符号、日报防重复 | 全过 |

### 4.2 真机自洽检查

- **全局情感连续**：多会话交替访问，`/情感状态` 始终是同一份 `global_affect.json`，数值沿单一轨迹变化（不因切会话重置）。
- **记忆单一性**：`/记忆` 头 `🧠 记忆（共 N 条，来源 Mem0 scope=u_<scope>）`，与 AI 实际召回同源；服务器上已无 KV `memory_store`、无 `user_profile.md`、无旧插件记忆文件。
- **功能不退化**：
  - 人格注入 `[persona] 已向对话 system prompt 注入插件人格（1140 字）` 每轮出现；
  - 情感启动与注入正常（见第 2.2 节）；
  - Mem0 召回生效（「记得呀，11月15日左右去日本」）；
  - 主动消息调度 `[living] 主动消息 v2 调度已启动：1 个会话`（语音会话已被守卫排除，不会排没人收的消息）；
  - 世界观 34 条召回完好；`/记忆`、`/记忆状态`、`/刷新记忆`、`/记忆重置` 均可用。
- **无残留**：见第 5 节（残留均为有意保留的回滚包或无关文件）。

---

## 5. 有意保留 / 遗留事项

| 项 | 说明 | 建议 |
| --- | --- | --- |
| `_backup_v110_20261029-104028.tar.gz`（82 MB） | v1.10.0 部署前整包回滚点 | 观察一周稳定后可删 |
| `_backup_v1101_20261029-1130/`（84 MB） | v1.10.1 部署前插件目录 + 配置 | 同上 |
| `_cleanup_bak_20261029-105936/` | 清理前的小体积不可再生内容（笔记/旧情感/旧插件状态） | 长期保留或转存 |
| `affect_state.json.bak`（1710 B, Oct 28） | 迁移前的旧情感快照 | 可删 |
| `dashboard.zip`（5.6 MB）、`cmd_config.json.bak` | AstrBot 自带/无关 | 不动 |
| `personas` 三行（`elaina` / `elaina_v2` / `elaina_v2_notools`） | 是 AstrBot 人格配置文本，不是记忆源；`elaina_v2_notools` 的 skills 已摘成两项，插件人格由插件自己注入 | 保留（用户可自行删前两条） |
| `astrbot_plugin_smart_segmentation` 的 `ProviderNotFoundError: xiaomi-token-plan/mimo-v2.5 not found` | 该插件配置缺失，与记忆/情感无关 | 单独修 |
| 容器内中文字体 0 个（`fc-list :lang=zh` 空） | 日报/长文转图退回 PIL 默认字体，可能缺字 | 需要时挂载字体 |

---

## 6. 版本与提交

- `v1.10.0`（`684eb4d`）：情感全局化、记忆源归一、测试重写、文档阶段九/十。
- `v1.10.1`（本次）：写入前提炼事实 `mem0_distill`、配置面板 155 项、M18 测试、README/CHANGELOG/metadata 同步。
- 服务器当前：插件 `1.10.1`（`main.py` md5 与仓库一致）、`global_affect.json`（全局单例 PAD，无分区键）、`mem0_meta.json`、`/记忆` 37 条（34 条世界观 + 3 条提炼事实）。
