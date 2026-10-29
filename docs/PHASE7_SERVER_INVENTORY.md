# 阶段零-B · 服务器侧彻查清单（只读）

- 检查对象：NAS 服务器 `ssh.example.com:22`（用户 `alice`）上的 AstrBot 容器 **`astrbot`**
- 数据根：宿主 `/opt/astrbot/data`（容器内 `/AstrBot/data`）
- 检查时间：**2026-10-29 18:2x–18:4x（CST）**，宿主 UTC 基准 `2026-10-29T10:29:30Z`
- 检查方式：全程**只读**。未执行任何 `rm` / `mv` / `docker stop|restart` / 改配置 / 改数据库。sqlite 一律 `cp` 到 `/tmp` 后查副本（`data_v4.db` 在容器运行中，绝不直连）。
- 配套代码侧报告：`docs/PHASE7_AUDIT.md`（情感/记忆读写点行号）。本文只回答「服务器上有什么、哪些是遗留物」。
- 复核方式：本文命令均形如
  ```bash
  cd /d/deepseek && PYTHONIOENCODING=utf-8 python nasvpn/ssh.py '<命令>'
  ```
  需要 root 的读操作统一用 `echo '<PASS>' | sudo -S -p '' <cmd>`（密码见 `nasvpn/ssh.py`，不落文档）。

---

## 0. 旁证速览

| 项目 | 实测值 | 复核命令 / 输出片段 |
| --- | --- | --- |
| AstrBot 版本 | **v4.27.5** | `docker logs astrbot` → `[Core] [INFO] [core.core_lifecycle:164]: AstrBot v4.27.5`；`AstrBot v4.27.5 WebUI is ready` |
| 套件版本 | **1.9.0** | 启动日志 `Plugin astrbot_plugin_elaina_suite (1.9.0) by SeaSmall: ...`；`metadata.yaml: version: 1.9.0` |
| 容器 uptime | **Up 21 hours**（StartedAt `2026-10-28T13:36:14Z`） | `docker ps` → `astrbot  Up 21 hours  soulter/astrbot:latest`；`docker inspect --format '{{.State.StartedAt}} {{.Config.Image}}'` |
| 宿主 uptime | `up 12 days, 6:55` | `uptime` |
| 容器内 `fc-list :lang=zh` | **0 条**（全部字体仅 8 个 DejaVu） | `docker exec astrbot sh -c 'fc-list :lang=zh \| wc -l'` → `0`；`fc-list \| wc -l` → `8`（DejaVuSerif/-Bold、DejaVuSans/-Bold、DejaVuSansMono 4 款） |
| 宿主 `fc-list` | **未安装**（`NO_FCLIST`） | `which fc-list` → 无输出 |
| 近 30 分钟 Traceback | **1 条**（唯一 1 条；非记忆/情感，来自 smart_segmentation） | `docker logs --since 30m astrbot \| grep -ac Traceback` → `1` |
| 该 Traceback 内容 | `astrbot.core.exceptions.ProviderNotFoundError: Provider xiaomi-token-plan/mimo-v2.5 not found` | `docker logs --since 40m ... \| grep -a -A6 Traceback` |
| 容器总数 | 5 个（`astrbot` + 4 个 frpc/natfrp，后者 Up 12 days） | `docker ps` |

> `fc-list :lang=zh = 0` 直接印证「插件必须自带 `font.ttf`」：`/opt/astrbot/data/font.ttf`（17 773 244 B，mtime 10-25）是**修复项，不是遗留物**。

---

## 1. 插件清单（`/opt/astrbot/data/plugins`）

### 1.1 目录原始清单

```bash
cd /opt/astrbot/data/plugins && ls -la
```

| 条目 | 类型 | 大小 | mtime(UTC) | 归属 |
| --- | --- | --- | --- | --- |
| `astrbot_plugin_affection` | 目录 `drwx------ alice` | 内容 68K | 2026-08-26 03:34 | 第三方 middcom/dream/deepseek |
| `astrbot_plugin_elaina_suite` | 目录 `drwxr-xr-x root` | 含 `meme/` 约 50 个素材 | 2026-10-28 13:37 | 本插件 SeaSmall |
| `astrbot_plugin_gif_vision_helper.zip` | **zip 文件** | 39 971 B | 2026-08-25 06:06 | 第三方 YanL & _Se14m |
| `astrbot_plugin_password` | 目录 | — | 2026-08-25 06:05 | 第三方 Zhalslar |
| `astrbot_plugin_smart_segmentation` | 目录 | — | 2026-08-07 14:39 | 第三方 薄暝 |

磁盘：`/opt` 456G，已用 23G，剩 414G（6%）→ **空间不是问题，清理是「干净度」问题不是容量问题**。

### 1.2 manifest 关键字段

| 插件 | name | display_name | version | author | repo |
| --- | --- | --- | --- | --- | --- |
| affection | `astrbot_plugin_affection` | 情绪管理 | `v1.2` | middcom,dream,deepseek | `github.com/middcom/astrbot_plugin_affection` |
| elaina_suite | `astrbot_plugin_elaina_suite` | Elaina 工具箱（六合一） | `1.9.0` | SeaSmall | `github.com/SeaSmall/astrbot-plugin-elaina-suite` |
| password | `astrbot_plugin_password` | 密码修改器 | `v1.0.1` | Zhalslar | `github.com/Zhalslar/astrbot_plugin_password` |
| smart_segmentation | `astrbot_plugin_smart_segmentation` | 智能分段 | `v0.1.0` | 薄暝 | `github.com/exynos967/astrbot_plugin_smart_segmentation` |
| gif_vision_helper(zip 内) | `astrbot_plugin_gif_vision_helper` | GIF Vision Helper | `1.3` | YanL & _Se14m | （zip 内 metadata.yaml） |

affection 的 desc 原文（说明它是什么）：
> 一个基于弗洛伊德心理动力学引入双驱情绪模型的 AstrBot 插件。它模拟了力比多（生本能/爱欲）与攻击性（死本能/毁灭欲）之间的相互作用。机器人的回复与交互将根据这两种核心心理能量的浮动平衡而动态演变……

### 1.3 是否被 AstrBot 实际加载

```bash
echo '<PASS>' | sudo -S -p '' docker logs astrbot 2>&1 | grep -a "Plugin "
```

最近一次启动（2026-10-28 21:37 CST）加载日志：

```
[21:37:03.797] [Core] [INFO] AstrBot v4.27.5
[21:37:05.147] Loading plugin astrbot_plugin_elaina_suite ...
[21:37:10.499] Plugin astrbot_plugin_elaina_suite (1.9.0) by SeaSmall: 六合一插件…
[21:37:11.293] Loading plugin astrbot_plugin_smart_segmentation ...
[21:37:11.520] Loading plugin astrbot_plugin_password ...
[21:37:11.780] Plugin astrbot_plugin_affection (v1.2) by middcom,dream,deepseek: 一个基于弗洛伊德…
        builtin_commands (0.0.1) / astrbot (4.4.0)
[21:37:xx] AstrBot v4.27.5 WebUI is ready
```

| 插件 | 是否加载 | 依据 |
| --- | --- | --- |
| `astrbot_plugin_elaina_suite` 1.9.0 | **是（当前版本）** | 启动日志；同日 21:28 那次启动仍是 **1.8.0**，21:37 重启后变 1.9.0 |
| `astrbot_plugin_affection` v1.2 | **是（且仍在写状态，见 §2.2）** | 启动日志 + `plugin_data/astrbot_plugin_affection/unknown_selfid/user_data.json` mtime 2026-10-29 10:32 UTC |
| `astrbot_plugin_smart_segmentation` v0.1.0 | **是** | 启动日志；30 分钟内有它的报错（说明有流量走到它） |
| `astrbot_plugin_password` v1.0.1 | **是** | 启动日志 |
| `astrbot_plugin_gif_vision_helper` 1.3 | **否（未加载）** | 只有 `.zip`，没有解压成目录，AstrBot 不会加载 zip |

### 1.4 启用/停用状态（数据侧）

`data_v4.db` 的 `preferences` 表（只读副本查询）：库中 15 行 = `scope='global'` 7 行 / `plugin` 5 行 / `umo` 3 行。

- `preferences.global.inactivated_plugins` = `["data.plugins.astrbot_plugin_private_companion.main","data.plugins.astrbot_plugin_mimo_tts_clone.main","data.plugins.astrbot_plugin_toolkit.main"]`
  → **这三个名字对应的插件在磁盘上都已经不存在**（早前删除后的记录残留）。
- `preferences.global.plugin_install_sources`（1 582 字符）：affection ← GitHub，installed `2026-08-07T14:52:11Z`、updated `2026-08-26T03:34:44Z`；elaina_suite installed `2026-09-29T11:56:02Z`、updated `2026-10-16T11:23:39Z`；password 走插件市场；smart_segmentation installed `2026-08-07T14:39:58Z`。
- `/opt/astrbot/data/plugins.json`（2 709 199 B）是**插件市场元数据镜像**（顶层键 `{timestamp, data, md5}`），**不是启用状态来源**，不能拿它当依据。

---

## 2. 每个非 elaina_suite 插件是否涉及「记忆 / 情感」

grep 关键词：`memory|affect|emotion|mood|好感|亲密|印象|heart|favor|relation`

```bash
cd /opt/astrbot/data/plugins/<plugin> && grep -nE "memory|affect|emotion|mood|好感|亲密|印象|heart|favor|relation" *.py
```

| 插件 | 命中 | 结论 |
| --- | --- | --- |
| `astrbot_plugin_password` | **rc=1，0 命中** | 不涉及记忆/情感 |
| `astrbot_plugin_smart_segmentation` | **rc=1，0 命中** | 不涉及记忆/情感 |
| `astrbot_plugin_gif_vision_helper.zip` 内 `main.py` | **0 命中** | 不涉及记忆/情感 |
| `astrbot_plugin_affection` | **大量命中** | **唯一一个旧的独立「情感/好感度」实现**，见下 |

### 2.1 affection 的关键代码行（它自带一整套情感系统）

文件：`/opt/astrbot/data/plugins/astrbot_plugin_affection/`

| 文件:行 | 代码 |
| --- | --- |
| `main.py:82` | `@register("astrbot_plugin_affection", "middcom,dream,deepseek", "弗洛伊德双驱情绪管理插件", "v1.2")` |
| `main.py:202-203` | `@filter.on_llm_request(priority=10)` / `async def inject_emotion_prompt(self, event: AstrMessageEvent, req):` |
| `main.py:226` | `emotion_desc = get_emotion_description(` |
| `main.py:236-247` | 拼装注入文本：`【当前情绪数值】\n他力比多：…/50（亲近/给予温暖的欲望）\n他攻击性：…/50（推开/伤害的冲动）\n好感度：…/100\n自力比多：…/50\n自攻击性：…/50\n参考标签：对用户「…」，自身「…」` |
| `main.py:248` | `emotion_prompt = values_text + reminder` |
| `main.py:250-252` | `if req.system_prompt: req.system_prompt += "\n\n" + emotion_prompt` ← **无条件追加进主 LLM 的 system_prompt，没有启用开关** |
| `main.py:260` | `@filter.on_waiting_llm_request(priority=10)` / `trigger_unconscious_analysis`（等 LLM 时后台更新「潜意识」数值） |
| `emotion_tables.py:9` | `def _get_affection_level(affection: float) -> int:`（12.5/37.5/62.5/87.5 四档） |
| `emotion_tables.py:93` | `(12.5, 0.0): "好感"` |
| `emotion_tables.py:190` | `def get_emotion_description(` |
| `storage.py:143` | `affection: float = 50.0` |
| `storage.py` | 类 `SelfDataStorage` / `UserDataStorage`，「用户数据存储模块 支持多机器人数据隔离」 |
| `unconscious.py:90-93` | prompt 要求「评估本次互动是否影响**长期印象（基线值）**……若 turn_count > 10，基线变化必须极小（增量 -0.2 ~ +0.2），因为初印象已形成」 |

### 2.2 affection 把状态存哪（路径 + 实测）

定位逻辑：`main.py:95 self.base_data_path = StarTools.get_data_dir()`；`main.py:141 SelfDataStorage(bot_data_path / "self_data.json")`；`main.py:154 UserDataStorage(bot_data_path / "user_data.json", self_storage)`。
实测落在 **`<data>/plugin_data/astrbot_plugin_affection/<bot_id>/`**（不是 KV、不是 sqlite，是 JSON 文件）：

```bash
sudo ls -laR /opt/astrbot/data/plugin_data/astrbot_plugin_affection   # 合计 68K
```

| bot_id 子目录 | self_data.json | user_data.json | user_data mtime(UTC) | 用户数 |
| --- | --- | --- | --- | --- |
| `1000000003` | 219 B | 1 034 B | 2026-08-09 08:17 | 3 |
| `541E6A92A03718EDBB9946C748642854` | 195 B | 735 B | 2026-09-13 01:02 | 2 |
| `default_bot` | —（空目录） | — | 2026-08-07 14:52 | 0 |
| `qq_official` | 220 B | 762 B | 2026-09-29 23:54 | 2 |
| `unknown_selfid` | 218 B | 754 B | **2026-10-29 10:32** | 2 |
| `webchat` | 219 B | 1 075 B | 2026-10-28 13:27 | 3 |

内容抽样（`user_data.json`，键=发送者 id；数值取自文件）：

```
unknown_selfid: 0123456789ABCDEF0123456789ABCDEF → affection=100.0, libido_other=32.1, aggression_other=20.3, last_interaction=1793268188.7（≈2026-10-29T10:03Z）
webchat:       alice                            → affection=53.0, libido_other=27.4, aggression_other=24.0
1000000003:    0123456789ABCDEF… / 1000000002              → affection=100.0 / 50.0
```

> **重要**：`unknown_selfid` 的写入时间 ≈ 审计开始前 2 分钟 → **affection 此刻仍在正常跑并写状态**，不是死插件。它的 `inject_emotion_prompt` 无开关，每轮主 LLM 请求都会把「好感度 100/100、力比多/攻击性」面板塞进 system_prompt。

### 2.3 与 elaina_suite 的冲突（同一请求上两套情感并存）

- elaina_suite `main.py:8494 @filter.on_llm_request(priority=99996)` 注入「【身份底稿】+【傲娇怎么写】」人格块（实测日志：`[persona] 已向对话 system prompt 注入插件人格（1140 字）`）。
- affection `main.py:202 @filter.on_llm_request(priority=10)` 注入「【当前情绪数值】好感度/力比多/攻击性」。
- elaina_suite `main.py:8529 @filter.on_llm_response(priority=99997)` 自己还有一套 PAD 情感（`self.affect.fast_update`）。
→ **同一个 LLM 请求的 system_prompt 里同时存在两套情感数值体系**（affection 的「好感度 0–100 + 力比多/攻击性 0–50」与套件的「PAD + baseline」），且两套数值互不同步（affection 已封顶 100，套件另算）。这是本次彻查最实质的「重复链路」。

---

## 3. 数据库清单

```bash
sudo find /opt/astrbot/data \( -name '*.db' -o -name '*.sqlite*' \) -printf '%p\t%s\t%TY-%Tm-%Td %TH:%TM\n'
```

| 路径 | 大小 | mtime(UTC) | 性质 |
| --- | --- | --- | --- |
| `/opt/astrbot/data/data_v4.db` | 9 728 000 | 2026-10-28 13:28 | AstrBot 主库（**容器运行中，另有 `-wal` 5 351 912 / `-shm` 32 768**） |
| `/opt/astrbot/data/backup_before_del_20261017_081325.db` | 9 728 000 | 2026-10-17 00:13 | 删除前整库备份 |
| `/opt/astrbot/data/knowledge_base/kb.db` | 94 208 | 2026-08-09 08:19 | 知识库（非长期记忆） |
| `/opt/astrbot/data/mem0_history.db` | 36 864 | 2026-10-28 23:46 | **mem0 变更历史（活跃记忆）** |
| `/opt/astrbot/data/mem0_qdrant/collection/elaina_memories/storage.sqlite` | 352 256 | 2026-10-28 23:46 | **mem0 向量库（活跃记忆）** |
| `_clear_bak_20261028-131545/data_v4.db` | 9 728 000 | 2026-10-28 08:21 | 清空操作前备份 |
| `_clear_bak_20261028-131545/mem0_history.db` | 32 768 | 2026-10-28 11:35 | 同上 |
| `_clear_bak_20261028-131545/collection/elaina_memories/storage.sqlite` | 221 184 | 2026-10-28 11:35 | 同上 |
| `_clear_bak_20261028-131545/collection/mem0_probe/storage.sqlite` | 40 960 | 2026-10-28 11:13 | 探针 collection 残留 |
| `_skill_bak_20261028-133600/elaina-agent/memory/index.sqlite3` | 27 787 264 | 2026-10-12 09:33 | 旧 skill 的 RAG 索引备份 |

### 3.1 `data_v4.db`（只读副本查）

```bash
sudo cp /opt/astrbot/data/data_v4.db /opt/astrbot/data/data_v4.db-wal /opt/astrbot/data/data_v4.db-shm /tmp/dvaudit/
sqlite3 'file:/tmp/dvaudit/data_v4.db?mode=ro' '.tables'
```

表清单：
```
api_keys            attachments         chatui_projects     command_configs
command_conflicts   conversations       cron_jobs           dashboard_trusted_devices
persona_folders     platform_message_history  platform_sessions  platform_stats
preferences         provider_stats      session_project_relations  umo_aliases
personas            webchat_threads
```

行数：`conversations=2`，`platform_message_history=1044`，`preferences=15`，`personas=3`。

**`preferences`（记忆/情感相关部分）**：

| scope | 键 | 长度 | 说明 |
| --- | --- | --- | --- |
| `plugin` | **`memory_store`** | **2 144** | **旧 KV 长期记忆**（`/记忆` 命令读的就是它） |
| `plugin` | `geo_cache` / `last_digest_date` / `subscribed_sessions` / `weather_cache` | 21–751 | 与记忆无关（缓存/简报/订阅） |
| `global` | `inactivated_plugins`(158)、`plugin_install_sources`(1582)、`abconf_mapping`(177)、`umop_config_routing`(157)、3 个 migration 标记(13) | — | 与记忆无关 |
| `umo` | `sel_conv_id` × 3 | 47 | 与记忆无关 |

`memory_store` 值内结构 `{"val":{"updated","items":[{"umo","summary"}]}}`，实测：

```json
{"val": {"updated": "2026-10-29 15:52",
         "items": [{"umo": "default_1000000001:FriendMessage:0123456789ABCDEF0123456789ABCDEF",
                    "summary": "用户是吉林工程技术师范学院大一学生，家乡安徽宿州，住宿。军训已于 2026-10-21 结束……万事达卡已于 2026-10-17 在城里领回。用户习惯晚睡……用户昵称助手为\"伊蕾娜\"……"}]}}
```

→ **`updated = 2026-10-29 15:52`，今天还在写**：这份旧 KV 记忆是活数据，且与 mem0 并存（AI 召回 mem0、`/记忆` 显示这份 → 双记忆源）。细节见 `PHASE7_AUDIT.md` B4。

**`personas`（3 行，2 套内容）**：

| id | persona_id | system_prompt 长度 | 备注 |
| --- | --- | --- | --- |
| 1 | `elaina` | 4 102 | 旧版人设 |
| 2 | `elaina_v2` | 6 588 | 新版 |
| 3 | `elaina_v2_notools` | 6 588 | 与 id=2 同长，疑似仅差工具开关 |

三条均以「【系统提示词 · 伊蕾娜（Elaina）人格设定】」开头 → **人格配置三份并存**，属「需确认清理哪条」的歧义项（C 表）。

### 3.2 `mem0_history.db`（活跃长期记忆，不要动）

```sql
CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, old_memory TEXT, new_memory TEXT,
                      event TEXT, created_at DATETIME, updated_at DATETIME, is_deleted INTEGER,
                      actor_id TEXT, role TEXT);
CREATE TABLE messages (...);
```

- `history` = **56 行**，`messages` = **0 行**；全部 `event='ADD'`，最新 `2026-10-28T23:46`，`actor_id` 为 user/assistant。
- 质量抽样（`new_memory`）：`"<对话原句已脱敏>"`、`"<对话原句已脱敏>"`、`"<对话原句已脱敏>"` → **把对话原句当「记忆」存**（`mem0_infer: false`），质量存疑（见 C7）。

### 3.3 `mem0_qdrant`（活跃向量库）

- `collection/elaina_memories/storage.sqlite`：表 `points(id TEXT PRIMARY KEY, point BLOB)`，**`points` = 56 行**（与 history 一一对齐）。
- `meta.json`：`{"size":512,"distance":"Cosine","on_disk":true}`，sparse_vectors `{"bm25":{"modifier":"idf"}}`。
- 查 payload 会报 `Error: in prepare, no such column: payload` —— payload 在 BLOB 里，不是列。

### 3.4 `kb.db`（知识库，非长期记忆）

表：`kb_documents`、`kb_media`、`knowledge_bases`。属知识库 RAG，**与「记忆/情感」无关**。

---

## 4. 文件 / 目录清单（记忆、情感、人格相关）

| 路径 | 大小 | mtime(UTC) | 内容/条目数 |
| --- | --- | --- | --- |
| `/opt/astrbot/data/affect_state.json` | **2 038 B** | **2026-10-29 10:03** | 套件情感状态（**仍是旧 `sessions` 结构**：`{"sessions":{"<umo清洗键>":{...},"shared_alice":{...}}}`），同时残留旧会话 key 与 `shared_alice` |
| `/opt/astrbot/data/affect_state.json.bak` | 1 710 B | 2026-10-28 23:46 | 上一版备份 |
| `/opt/astrbot/data/motivation_state.json` | 6 176 B | 2026-10-29 10:03 | 动机状态（`sessions.shared_alice.topics`，记有 `（原句已脱敏）` 等话题） |
| `/opt/astrbot/data/proactive_state.json` | 5 422 B | 2026-10-29 10:03 | 主动消息门禁状态（`{"version":2,"sessions":{...}}`） |
| `/opt/astrbot/data/memory_store_backup_20261018_080835.json` | 10 510 B | 2026-10-18 00:08 | 旧 KV `memory_store` 手工备份（`{"val":{"updated":"2026-10-18 03:01","items":[...]}}`） |
| `/opt/astrbot/data/_clear_bak_20261028-131545/` | **9.6 M，14 项** | 2026-10-28 13:15 | `affect_state.json`(381B)、`motivation_state.json`(3845B)、`proactive_state.json`(2239B)、`data_v4.db`(9 728 000B)、`mem0_history.db`(32768B)、`mem0_qdrant/{.lock, meta.json 1199B, collection/elaina_memories/storage.sqlite 221184B, collection/mem0_probe/storage.sqlite 40960B}` |
| `/opt/astrbot/data/_skill_bak_20261028-133600/` | **38 M** | 2026-10-28 13:36 | `elaina-agent/`(38M) + `unclecheng-reduce-ai-perception-v2/`(60K) |
| └ `elaina-agent/` | 38 M | 10-12 | `LICENSE, README.md, SKILL.md, agents/, assets/(44K), evals/, memory/(28M), references/, scripts/, 小说/(8.0M Vol01~Vol0x), 文章概括/(2.0M)` |
| └ `elaina-agent/memory/` | 28 M | 2026-10-12 09:33 | `article-index.md`(53 794)、`canon-manifest.json`(176 960)、**`index.sqlite3`(27 787 264)**、`persona-evidence.md`(440 025)、`query-aliases.json`(755) |
| `/opt/astrbot/data/skills/` | 2.4 M，**23 个子目录** | — | `ai-news-daily-v2 … yijing-laozi`；**`elaina-agent` 已不在其中**（`ls .../skills/elaina-agent` → `No such file or directory`） |
| `/opt/astrbot/data/skills.json` | 1 957 B | — | 各 skill 的 `{"active": true}` 开关 |
| `/opt/astrbot/data/fastembed_cache/` | **91 M**（5 子项） | 2026-10-28 11:13 | mem0 本地嵌入模型缓存（bge-small-zh-v1.5） |
| `/opt/astrbot/data/workspaces/` | 12 M，**4 个目录** | — | 见下 |
| └ `default_1000000001_FriendMessage_0123456789ABCDEF0123456789ABCDEF/` | **31 项** | — | `user_profile.md`(455B)、`timetable.md`、`morning_report.md`、`project_README.md`、`data/cmd_config.json`、`data/t2i_templates`、大量 `crop_*/seg_*/tag_*/t_*/u_*/m_*.png` |
| └ `webchat_FriendMessage_webchat_alice_webchat_phone_alice_main/` | — | — | webchat 会话工作区 |
| └ `default_1000000001_GroupMessage_FEDCBA9876543210FEDCBA9876543210/` | — | — | 群会话工作区 |
| └ `_1_FriendMessage_1000000002/` | — | — | 另一会话 |
| `/opt/astrbot/data/plugins/astrbot_plugin_elaina_suite/assets/persona.json` | 46 016 B | 2026-10-28 10:34 | 顶层键 `schema_version, kind, generated_at, generated_by, runtime_policy, name, display_name, title, aliases, identity, traits, tone, speech_habits, tsundere, emotional_tendencies, silence_reaction, relationship, prohibitions, source_index, source_map`（**人格底稿，被注入 system prompt**） |
| `/opt/astrbot/data/plugin_data/astrbot_plugin_affection/` | 68 K，6 个 bot_id 子目录 | 2026-10-29 10:32（最新写） | 见 §2.2 |
| `/opt/astrbot/data/font.ttf` | 17 773 244 B | 2026-10-25 | 插件补的中文字体（容器 `fc-list :lang=zh` = 0） |
| `/opt/astrbot/data/probe_mem0.py` | 13 521 B | — | 一次性 mem0 排查脚本 |
| `/opt/astrbot/data/plugins/astrbot_plugin_elaina_suite_config.json` | 7 810 B | 2026-10-28 13:37 | 套件主配置（**含 xiaomi/baidu API key 明文，本文不抄**）；`.bak-20261017` 4 694 B |

`user_profile.md` 全文（455 B，用户档案，主动消息在用）：

```markdown
# 用户档案（伊蕾娜旅伴）

- 就读：吉工师（吉林工程技术师范学院）大一学生
- 家乡：安徽宿州
- 现况：住宿
- 军训中：2026-10-14 起；10-17（周六）休息，10-18 恢复训练；用户 10-21 称"今天结束"（以本人说法为准）
- 待办已完成：万事达卡已于 2026-10-17 在城里领回
- 备注：以后每日人格自主对话需记住以上信息（大一、军训期间作息与状态）
```

套件配置里与记忆/情感直接相关的开关（只读摘录，无密钥）：

```
memory_enabled: true, memory_cron: "0 3 * * *", memory_turns: 20, memory_max_chars: 1500,
memory_raw_fallback: true, memory_window_hours: 48, memory_refresh_cooldown: 30,
memory_rewrite_stale: true, memory_notes_enabled: true, memory_refresh_before_gen: true
living_enabled: true, living_min_silence_minutes: 90, living_cooldown_minutes: 180,
living_daily_max: 6, living_quiet_hours: "23:30-07:30"
mem0_enabled: true, mem0_llm_model: "mimo-v2.6-pro", mem0_embed_model: "BAAI/bge-small-zh-v1.5",
mem0_top_k: 6, mem0_threshold_pct: 45, mem0_affect_recall: true, mem0_affect_boost_pct: 18,
mem0_unresolved_boost_pct: 12, mem0_seed_facts: true, mem0_flush_turns: 4,
mem0_recall_chars: 1200, mem0_infer: false
affect_enabled: true, affect_fast_mode: "heuristic", affect_decay_half_life_minutes: 120,
affect_silence_threshold_minutes: 720, affect_inject_chars: 600
motivation_enabled: true, motivation_affect_gate: true, motivation_learning_gate: true
persona_inject_enabled: true, shared_scope_enabled: true, shared_scope_id: "alice",
tsundere_enabled: true, tsundere_review_enabled: true, tsundere_chat_inject: true,
meme_enabled: true, meme_probability: 0.5, guard_enabled: true
```

---

## 5. 依赖与重复处理

### 5.1 跨插件 import

```bash
cd /opt/astrbot/data/plugins && grep -rn "astrbot_plugin_" astrbot_plugin_elaina_suite/main.py | grep -E "import|from "
# → 无输出
```

**无跨插件 import**：elaina_suite 不 import affection，affection 也不 import elaina_suite。它们是两套平行的独立实现 —— 这正是「数据各存一份、互不知情」的根因。

### 5.2 同一事件的多个订阅（真正的问题所在）

`on_llm_request`（优先级从小到大执行）：

| 订阅者 | 优先级 | 动作 |
| --- | --- | --- |
| `affection/main.py:202` `inject_emotion_prompt` | **10** | 向 system_prompt 追加「【当前情绪数值】好感度/力比多/攻击性」 |
| `elaina_suite/main.py:8494` `tsundere_on_llm_request` | 99996 | 向 system_prompt 追加「【身份底稿】+【傲娇怎么写】」人格块 |
| `elaina_suite/main.py:8519` `image_bridge_on_llm_request` | 默认 | 挂起图片识别结果注入 |
| `elaina_suite/main.py:8524` `llm_guard_on_llm_request` | 默认 | 兜底错误文案 + 请求快照 |

`on_llm_response`：

| 订阅者 | 优先级 | 动作 |
| --- | --- | --- |
| `elaina_suite/main.py:8529` `affect_on_llm_response` | 99997 | PAD 情感快通道更新（`self.affect.fast_update`） |
| `elaina_suite/main.py:8537` `memory_on_llm_response` | 99998 | 凑轮写 mem0 |
| `elaina_suite/main.py:8542` `meme_on_llm_response` | 99999 | 概率发表情包 |
| `smart_segmentation/main.py:75` `on_llm_response` | 默认 | 调 LLM 做智能分段（**就是它抛 `Provider ... not found`**） |

`on_decorating_result`：`elaina_suite/main.py:8547`（拦截泄漏文案）+ `smart_segmentation/main.py:120`（改写为多段）。
`on_waiting_llm_request`：`affection/main.py:260`（潜意识/基线后台更新）。

### 5.3 重复链路结论

1. **情感重复**：affection（好感度+力比多/攻击性，JSON 文件，`plugin_data/astrbot_plugin_affection/*`）与 elaina_suite（PAD+baseline，`affect_state.json`）**各自独立读写、各自注入同一个 system_prompt**，数值口径完全不同、互不同步 → 「旧情感实现没清干净」的核心证据。
2. **记忆重复**：elaina_suite **内部**就有两条（KV `memory_store` + mem0），再加 `workspaces/*/user_profile.md` 作为第三条素材源。三条都属本插件，不是遗留插件造成，但正是 `PHASE7_AUDIT.md` B4 要收敛的对象。
3. **无关重复**：smart_segmentation 与 elaina_suite 在 `on_llm_response`/`on_decorating_result` 上并存，属功能性叠加（分段 vs 拦截），不是记忆/情感重复；但它当前处于**持续报错**状态（provider 未配置）。
4. **不是重复**：password 只有两条 admin 命令（`main.py:43 修改用户名` / `main.py:66 修改密码`），无任何 LLM 钩子。

---

## 6. 结论表

### A 表 · 记忆/情感相关遗留物候选（可删 / 需清空，**仅建议，未执行**）

| # | 路径 | 证据 | 风险 | 建议动作 |
| --- | --- | --- | --- | --- |
| A1 | `/opt/astrbot/data/plugin_data/astrbot_plugin_affection/`（68K，6 个 bot_id 子目录、10 个 JSON） | affection 插件状态；今天 10:32 UTC 仍在写；`0123456789ABCDEF…` 好感度已封顶 100.0；与套件 PAD 双情感并行注入 | 中：这是**另一套情感系统的全部历史**；若用户还要 affection 的 `/mystatus`、`/reset_emotion`，删了即归零 | 先确认 affection 停用（C1），再把整个目录归档后删除；**不要在插件仍加载时删**（内存态会重写） |
| A2 | `/opt/astrbot/data/_clear_bak_20261028-131545/`（9.6M，14 项） | 名字即「清空操作前的备份」，内含旧 affect_state/motivation/proactive/data_v4.db/mem0_qdrant | 低：10-28 之后已有新数据；但它是唯一回滚点 | 确认无需回滚后整目录删除（或移到宿主离线备份） |
| A3 | `/opt/astrbot/data/_skill_bak_20261028-133600/`（38M） | `elaina-agent` skill 备份：`memory/index.sqlite3` 27.8M + `persona-evidence.md` 440K + `小说/` 8M + `文章概括/` 2M；线上 `skills/elaina-agent` 已删除 | 中：这是**人格/世界观 RAG 素材**（小说原文+索引），不是用户记忆；删了要重建索引（耗时/需重抓原文） | 归档到宿主离线存储后删除；若用户还想恢复 elaina-agent skill 则保留（C6） |
| A4 | `/opt/astrbot/data/memory_store_backup_20261018_080835.json`（10.5K） | 旧 KV `memory_store` 手工备份，`updated=2026-10-18 03:01` | 低：内容与 `preferences.memory_store` 同源且更旧 | 迁移进 mem0 并确认 KV 停写后删除 |
| A5 | `/opt/astrbot/data/backup_before_del_20261017_081325.db`（9.7M） | 10-17「删除前」整库备份，与当时 data_v4.db 同大小 | 低：12 天前整库快照，回滚价值已被 `_clear_bak` 覆盖 | 确认后删除 |
| A6 | `data_v4.db` → `preferences`(scope=`plugin`, key=`memory_store`)，2 144 字符 | 旧 KV 长期记忆，`updated=2026-10-29 15:52` **仍在写**；`/记忆` 只显示这份、AI 召回的是 mem0 → 双记忆源 | 中：清空前必须先让代码停写，否则下一轮 cron 又写回来 | 阶段五「Mem0 归一」完成后清空该 key（**属代码改动，本轮不动**） |
| A7 | `data_v4.db` → `personas` 表 3 行（`elaina` 4102 / `elaina_v2` 6588 / `elaina_v2_notools` 6588） | 三份人格、两套内容，开头均为「【系统提示词 · 伊蕾娜（Elaina）人格设定】」 | 中：删错会丢人格设定；`elaina_v2` 与 `elaina_v2_notools` 长度完全相同，可能仅差工具开关 | 需用户确认保留哪一条（C4），再删余下 |
| A8 | `/opt/astrbot/data/probe_mem0.py`（13.5K） | 一次性 mem0 排查脚本，非插件文件 | 低 | 删除 |
| A9 | `/opt/astrbot/data/affect_state.json`（2 038B，旧 `sessions` 结构） | 部署版 1.9.0 仍读写它；本地 repo 已改为 `global_affect.json`（`_state_file()` main.py:6315→`global_affect.json`，`_legacy_state_file()` main.py:6319→`affect_state.json`） | **高：现在删 = 情绪归零且迁移路径缺失** | **不要现在删**；等部署版升级到含迁移逻辑的版本，自动迁到 `global_affect.json` 后再删旧文件 |

可回收空间合计（A1+A2+A3+A4+A5+A8）：约 **57.6 MB**（68K + 9.6M + 38M + 10.5K + 9.7M + 13.5K）。

### B 表 · 与记忆/情感无关，不要动

| # | 插件/文件 | 理由 |
| --- | --- | --- |
| B1 | `astrbot_plugin_password` v1.0.1 | grep 关键词 **0 命中**；只有 `修改用户名`/`修改密码` 两个 admin 命令（`main.py:43/66`），是面板运维工具 |
| B2 | `astrbot_plugin_smart_segmentation` v0.1.0 | grep 关键词 **0 命中**；只订阅 `on_llm_response`(75)/`on_decorating_result`(120) 做分段，不读写记忆/情感。**但它当前报 `ProviderNotFoundError`（见 C9）** |
| B3 | `astrbot_plugin_gif_vision_helper.zip`（39 971B） | zip 内 `main.py` grep 关键词 **0 命中**；且从未解压、未被加载 → 与记忆/情感无关（是否启用见 C5） |
| B4 | `/opt/astrbot/data/knowledge_base/kb.db`（94K） | 表 `kb_documents/kb_media/knowledge_bases`，是知识库 RAG，不是长期记忆 |
| B5 | `/opt/astrbot/data/mem0_qdrant/` + `mem0_history.db`（56 条） + `fastembed_cache/`（91M） | **1.9.0 正在用的长期记忆**，删 = 失忆 + 下次启动重下模型 |
| B6 | `affect_state.json` / `motivation_state.json` / `proactive_state.json` | 1.9.0 每轮对话都在写（实测 10-29 10:03 UTC），是当前情感/主动消息的运行状态 |
| B7 | `workspaces/*/`（12M，4 目录；含 `user_profile.md` 455B、`timetable.md`、`morning_report.md`） | 用户档案与自主对话素材，1.9.0 的 `_collect_user_notes` 直接读 `user_profile.md` |
| B8 | `/opt/astrbot/data/font.ttf`（17.7M） | 中文字体修复（容器 `fc-list :lang=zh`=0）；删了简报图片中文变方框 |
| B9 | `/opt/astrbot/data/plugins.json`（2.7M） | 插件市场元数据镜像（`{timestamp,data,md5}`），与启用状态无关，动它无收益且有风险 |
| B10 | `/opt/astrbot/data/plugins/astrbot_plugin_elaina_suite_config.json`（7 810B） | 套件主配置（memory/mem0/affect/living 全开），任何改动先备份；**含明文 API key，注意保密** |

### C 表 · 需用户确认的歧义项

| # | 歧义 | 为什么需要确认 | 建议默认 |
| --- | --- | --- | --- |
| C1 | `astrbot_plugin_affection` **整体去留**？（停用/卸载 vs 保留） | 若保留 → A1 的 68K 状态不能删，双情感注入仍在（无开关，每轮都注入好感度面板）；若停用 → A1 才成立。它还有 `/mystatus`、`/reset_emotion`、`/reset_current`、`/reset_all_emotions`、`/set_emotion` 等命令，用户可能仍在用 | **建议停用**（情感统一到套件 PAD）；停用后再清 A1 |
| C2 | `affect_state.json` 的**结构迁移时机** | 部署版 1.9.0 `main.py` md5 `51080b95e9e36162df62c3c22c268624` ≠ 本地 repo `0ce67be13f878fbf60bacff1040ce6af`；部署版写 `affect_state.json`（旧 `sessions` 结构），本地版已改 `global_affect.json` 并保留 legacy 迁移 | 先升级部署版 → 自动迁移 → 再删 `affect_state.json`；**不要手写迁移** |
| C3 | KV `memory_store` 是否清空 | 清空要配合「代码停写」，否则 cron 会写回来；且 `memory_store_backup_*.json` 还留着 | 阶段五 mem0 归一完成后一并清 |
| C4 | `personas` 三条人格留哪条 | `elaina`(4102) 与 `elaina_v2`(6588) 内容不同；`elaina_v2` 与 `elaina_v2_notools` 同长疑似仅差工具开关 | 保留 `elaina_v2_notools`（若非 tools 版本是当前在用），删另两条——需用户确认 |
| C5 | `gif_vision_helper.zip` 解压启用 还是 删除 | 解压后会多一个图片识别插件；套件自己已有 `image_bridge`（小米→百度→OCR 三级识别），功能重叠 | 与套件图桥重复 → 建议删除 zip；若要留 GIF 动图拆帧能力则解压 |
| C6 | `_skill_bak_.../elaina-agent`（38M，含 8M 小说 + 2M 文章概括） | 可能是用户当资料库看的素材，不单纯是垃圾 | 先问清是否还要 elaina-agent skill |
| C7 | mem0 里 56 条「记忆」质量 | 抽样看到 `"<对话原句已脱敏>"`、`"<对话原句已脱敏>"` 这类对话原句被当记忆入库（`mem0_infer: false` 导致直存原句） | 属清库/重灌范畴，需用户决定是否清理低质量记忆 |
| C8 | `_clear_bak`(9.6M) 与 `backup_before_del`(9.7M) 是否还有回滚价值 | 都是同一时期快照，重复度高 | 默认删，但先确认无回滚需求 |
| C9 | `smart_segmentation` 的 `ProviderNotFoundError: Provider xiaomi-token-plan/mimo-v2.5 not found` | 30 分钟内唯一 1 条 Traceback 就来自它；属**配置缺失**，不是记忆问题，但会持续刷错误日志、且分段功能实际已降级 | 修 provider 配置或先停用；**不属于本次「记忆清污」范围**，交回 lead 决定 |

---

## 7. 附：本次使用的关键复核命令

```bash
# 1) 插件目录
cd /d/deepseek && PYTHONIOENCODING=utf-8 python nasvpn/ssh.py 'ls -la /opt/astrbot/data/plugins'

# 2) 加载日志（必须 root，否则 docker.sock 权限拒绝：
#    permission denied while trying to connect to the docker API at unix:///var/run/docker.sock）
cd /d/deepseek && PYTHONIOENCODING=utf-8 python nasvpn/ssh.py "echo '<PASS>' | sudo -S -p '' docker logs astrbot 2>&1 | grep -a 'Plugin '"

# 3) 关键词扫描（非 elaina_suite 插件）
cd /d/deepseek && PYTHONIOENCODING=utf-8 python nasvpn/ssh.py 'cd /opt/astrbot/data/plugins/astrbot_plugin_affection && grep -nE "memory|affect|emotion|mood|好感|亲密|印象|heart|favor|relation" *.py'

# 4) 全部 db / sqlite
cd /d/deepseek && PYTHONIOENCODING=utf-8 python nasvpn/ssh.py "echo '<PASS>' | sudo -S -p '' find /opt/astrbot/data \( -name '*.db' -o -name '*.sqlite*' \) -printf '%p\t%s\t%TY-%Tm-%Td %TH:%TM\n'"

# 5) data_v4.db 只读（绝不直连运行中的库）
cd /d/deepseek && PYTHONIOENCODING=utf-8 python nasvpn/ssh.py "echo '<PASS>' | sudo -S -p '' cp /opt/astrbot/data/data_v4.db /opt/astrbot/data/data_v4.db-wal /opt/astrbot/data/data_v4.db-shm /tmp/dvaudit/ && sqlite3 'file:/tmp/dvaudit/data_v4.db?mode=ro' '.tables'"

# 6) handler 订阅全景
cd /d/deepseek && PYTHONIOENCODING=utf-8 python nasvpn/ssh.py 'cd /opt/astrbot/data/plugins && grep -nE "@filter\.(on_llm_request|on_llm_response|on_waiting_llm_request|on_decorating_result)" astrbot_plugin_affection/main.py astrbot_plugin_elaina_suite/main.py astrbot_plugin_smart_segmentation/main.py'

# 7) 旁证
cd /d/deepseek && PYTHONIOENCODING=utf-8 python nasvpn/ssh.py "echo '<PASS>' | sudo -S -p '' docker exec astrbot sh -c 'fc-list :lang=zh | wc -l'"
cd /d/deepseek && PYTHONIOENCODING=utf-8 python nasvpn/ssh.py "echo '<PASS>' | sudo -S -p '' docker logs --since 30m astrbot 2>&1 | grep -ac Traceback"
```

---

## 8. 给后续阶段的三条硬结论

1. **服务器上确实存在第二套情感实现且仍然活着**：`astrbot_plugin_affection` v1.2 已被加载，`on_llm_request(priority=10)` 每轮无条件把「好感度/力比多/攻击性」写进主 LLM 的 system_prompt，与套件 PAD 情感并存。**只清数据不够，必须处理插件本身（C1）**。
2. **记忆分裂已实测确认双源**：`preferences.memory_store`（KV，2 144 字符，今天 15:52 还在写）与 mem0（history 56 条 / qdrant 56 点）同时存在，`/记忆` 只读前者、AI 召回后者。
3. **`affect_state.json` 不能现在删**：部署版 1.9.0 与本地 repo 不是同一份代码（md5 不同），部署版仍写旧 `sessions` 结构，删除等于把情绪清零并触发迁移路径缺失。先升级、再迁移、最后删。
