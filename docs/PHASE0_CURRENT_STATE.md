# 阶段零 · 主动消息现状与「一次性爆发」根因（证据版）

> 代码对象：`main.py`（5694 行，git HEAD `c4796ec` v1.1.8）——行号均指该文件。
> 线上现场：容器 `astrbot`（3 天前重启），插件 `/AstrBot/data/plugins/astrbot_plugin_elaina_suite`；
> 配置实测：`pg_enabled=True`、`block_proactive=True`、`missed_grace_minutes=30`、`msg_count 5~10`、
> `gen_time="20 3 * * *"`、`memory_cron="0 3 * * *"`、窗口 `07:00-23:00`、`pg_target_sessions` 只有 1 个 QQ 会话。
> 复现脚本：`D:\deepseek\_scratch\repro_burst.py`（用 AST 从 `main.py` 抽出**真实** `_dispatch_due/_send_silent/
> _random_times/_hhmm_to_min` 源码执行，假 KV/假时钟/假发送器，`python` 直接可跑，已有输出见 §2）。
> 标记：**[已复现]**＝本地跑出；**[静态]**＝代码可证但未跑出；**[现场]**＝线上日志/DB 证据。

## 1. 调度链路（文字版）

```
AstrBot 内置「主动型 Agent」job（cron_manager, job_type="active_agent"）
  └─ 插件 initialize 时一次性 update_job(enabled=False) 暂停        [静态 main.py:2932-2953]
     ⚠ 只暂停「当时已存在且 enabled」的；之后新建/重新启用的不受控

ProactiveGuardCore（main.py:2782）
  initialize 2800-2806
   ├─ _install_gate 2839-2860：包住 ctx.send_message 2858，放行条件 _allow_send 2862-2876
   │    ① owner._bypass_cnt>0 ② allow_senders 白名单 2885（默认 ["daily_digest"]）
   │    ③ strict_mode=False 且该会话 30min 内有用户活动 _recent_user_activity 2922-2927
   │    其余一律 return False + warning「已拦截非白名单主动消息」2851-2856
   └─ _setup_scheduler 2958-3069：cron_manager.add_basic_job × 3（persistent=False，timezone=Asia/Shanghai）
        ├─ proactive_guard_gen     cron=gen_time "20 3 * * *"    → _on_gen_time 3071-3096
        ├─ proactive_guard_minute  cron="* * * * *"              → _on_minute  3098-3104
        └─ proactive_guard_memory  cron=memory_cron "0 3 * * *"  → _on_memory_time 4228
      异常才回退 APScheduler 3038-3067（id=pg_gen/pg_min/pg_mem，无 replace_existing）
  terminate 2808-2834：恢复 ctx.send_message、按 _cron_job_ids 删 job、shutdown 回退调度器

两条独立路径（不共享同一把锁）：
  生成：_ensure_schedule 3130-3159 → _generate_and_store 3161-3258 → **只写 KV，不发送**
        _regenerate_today_if_stale 3521-3551（记忆水位变化时重建今天剩余）；手动 /重建计划 4316-4331
  派发：_on_minute 3098 → _dispatch_due 3398-3467 → 读 KV → 算 due → 逐条 _send_silent 3556-3574
        → **整轮循环结束后**才 put_kv_data 写回 3461-3467
锁：_gen_lock 只护生成（3154/3546/4327/4330），**派发全程无锁**（_on_minute 仅 try/except）
```

要点：`_dispatch_due` 与生成互不相干；`_on_minute` 每分钟触发一次，串行 await `_dispatch_due`，
APScheduler 侧默认 `max_instances=1`（`cron_manager.py:266-273` 未显式传），因此「同一 handler 进程内重入」概率低，
但**任务注册本身没有幂等**（见 §2④）。

## 2. 爆发根因（逐条证伪/证实）

### ① 计划条目时间点撞车 —— 不成立 [已复现 + 静态 + 现场]

- `_random_times` 3314-3322：`pool=list(range(start,end+1))` → `random.shuffle` → `sorted(pool[:count])`
  → 同一批时间点**天然互不重复**。
- `_parse_items` 3384-3390 按 `time` 去重（同一时间只留一条）；子代理审校 `_review_drafts` 4134-4160
  条数与时间点不一致时直接丢弃重稿。
- repro 场景 C：真实 `_random_times` 跑 2000 次，重复时间点 **0** 次。
- [现场] 40 个 boot 段（282 条「已发送」）里没有「同一段内同一计划时间出现两次」。

### ② 停机/重启后补发 catch-up —— **成立，且是主机制** [已复现 + 现场]

- 补发判据 3415-3426：`grace = _cfg_int("missed_grace_minutes", 30)`（线上实测 30）；
  `it_min == now_min` 直接到期；`it_min < now_min 且 now_min-it_min <= grace` 也进 `due`（3422-3424 明确注释「补发」）。
- 发送循环 3445-3459：`for it in due:` → 逐条 `await self._send_silent(...)`（3457），条目之间只有
  `asyncio.sleep(0.3)`（3570）＋（每条一次）事实校验 LLM（3447-3456）。
- 写回 KV 在循环之后 3461-3467：**发送过程中 KV 里这些条目仍在**。
- repro 场景 A（真实 `_dispatch_due`）：计划 6 条（07:31/35/39/44/48/52），模拟 07:31–07:53 无 tick
  （容器重启/插件重载/任务异常），07:54 首个 tick **一次连发 6 条**，时间戳 07:54:00.300 → 07:54:01.800，
  每条间隔 0.3s —— 用户侧就是「一下子出来一大串」。
- 爆发上限 = grace 窗口内积压条目数，且 ≤ 计划条数 ≤ `msg_count_max=10`；多目标会话时再 × 会话数
  （`_send_silent` 3560 `for umo in targets`）。
- 触发条件线上真实存在：[现场] `guard_err` 里有 20+ 条
  `[proactive_guard] 分钟检查任务失败: 无法获取可用的 LLM 提供商`（约每 21 分钟一次）；
  容器 3 天前重启；40 个 boot 段（插件反复重载）；`cron_manager._schedule_job` 的 `misfire_grace_time=30` 秒。
- 但 [现场] 40 个 boot 段共 282 条「已发送」中**最大迟到只有 123 秒**（18:22:03 补发 18:20），即每次跳过都短于一个以上
  tick → 线上只补 1 条，从未观测到 N 条同发。**结论：机制成立、参数化可稳定复现，现场尚未满足触发条件。**

### ③ 生成路径与门禁路径双重派发 —— 不成立 [静态 + 现场]

- 生成路径只在 `_generate_and_store` 里写 KV（3245-3253），全文**没有**在生成里调用 `send_message`。
- 门禁 `_install_gate` 只做拦截（2856 `return False`），拦截的结果是「消息消失」，不是「重发」。
- [现场] 日志里「计划已生成」与「已发送」分属两处代码、时间上也分离（生成集中在 03:20 前后）。

### ④ 并发竞态 / 重复注册 —— **成立（结构性风险）** [已复现 + 现场痕迹]

- `_dispatch_due` 无锁；两份并发执行会各读同一份 KV、各发一遍：repro 场景 B（真实 `_dispatch_due` 并发）
  同一条 07:00 消息发出 **2** 次。
- 注册无幂等：`cron_manager.add_basic_job` 每次都 `db.create_cron_job(...)` 新建 job_id
  （`cron_manager.py:145-170`）；`_schedule_job` 的 `replace_existing=True` 只对**各自 job_id** 生效
  （`cron_manager.py:266-273`）；插件 `terminate` 只删自己 `_cron_job_ids` 里的（2821-2826）——
  多套分钟任务同时存在、各发一遍的路径成立。
- [现场] `cron_jobs` 表保留多代 boot 的 basic 行：`proactive_guard_minute` 同时在 id=10 与 id=16 两代出现
  （`daily_digest`/`daily_digest_check`/`elaina_health` 同样成对）。它们 `persistent=0`，
  `sync_from_db` 只加载 `enabled and persistent`（`cron_manager.py:132`）→ 现在靠「不重排」侥幸没炸；
  一旦某次启动把它们改成 persistent/enabled 后被同步，或 cron_manager 与 APScheduler 回退分支同时生效，
  就会一分钟跑两遍。
- 派发耗时 > 60s 时 APScheduler（max_instances=1 默认）会跳过该次触发，所以「单 handler 重入」风险低，
  但这依赖宿主默认值，不能作为插件自己的保证。
- [现场] 40 个 boot 段的发送日志里未观测到同 tick 重复文本 → 标注为未触发。

### ⑤ 解析/清洗把多条合并或拆错 —— 不成立 [静态 + 现场]

- `_parse_items` 3348-3396：JSON 分支 3366-3368，行解析 3371-3382 要求行首 `^(\d{1,2}:\d{2})` 且时间必须在
  `expected_times` 内（3363/3381）→ 一条消息里的普通换行不会被拆成多个时间点。
- `_normalize_time` 3332-3346 只做格式归一；`_clean_message` 4208 只清文本。
- `_send_silent` 3562-3569 只在文本 > 3800 字时 `_split_text` 拆条；主动消息 20~60 字 → 实际 1 条。
- [现场] 40 个 boot 段里无「同分钟多条不同文本」。

### 综合结论（一句话）

爆发不是「一次生成多条」、不是「双路径派发」、不是「解析拆错」，而是
**派发端在分钟 tick 被跳过后，按 `missed_grace_minutes=30` 的窗口把积压条目在同一个 tick 里逐条补发**；
`_dispatch_due` 无锁 + 任务注册无幂等会把这一批再放大 k 倍。线上 40 个 boot 段的发送日志证明历史停机都短于 1 个 tick
（最大迟到 123s，只补 1 条），所以这个 bug 至今是「潜伏 + 一旦长停机就爆」的形态。

## 3. 记忆读写路径

- 存储形式：AstrBot KV（`get_kv_data/put_kv_data`），非独立文件。键与结构：
  - `MEMORY_KEY`：`{updated: "%Y-%m-%d %H:%M", items:[{umo, summary, updated}], merged}` —— 写于 3734
  - `daily_plan` / `daily_plan_next`：`{date, items:[{time,text}], memory_updated}` —— 写于 3245-3253
- 写入点：`refresh_memory` 3648-3743（`_on_memory_time` 4228 的 03:00 cron、生成前 `refresh_memory_before_gen`、
  `_collect_memory` 冷却刷新 3870-3877、`/刷新记忆` 4264）。记忆源 = 会话历史 `_history_lines` 3936-3977
  （window_hours 默认 48、turns 默认 20）+ `_collect_user_notes` 3867。
- 召回/注入点：`_collect_memory` 3858-3892 → `_truncate_memory`（memory_max_chars 默认 1500）→ 在
  `_generate_and_store` 3188-3191 用 `MEMORY_BLOCK_TEMPLATE`（320）拼成 `{memory_block}`，
  单次生成分支拼进 prompt 3229-3230；子代理分支同样把 memory 拼进 PLANNER/WRITER（277-301、4043-4132）。
- 派发时二次校验：水位比对 3432-3443（计划 `memory_updated` ≠ 当前记忆 → 逐条复核），
  逐条 `_fact_check_message` 3447-3456（**每条一次 LLM**，3474-3519 用 FACT_CHECK_PROMPT 判「与记忆冲突」，
  冲突则丢弃/改写）。
- 回写历史：`_record_to_history` 3576-3600 把每条主动消息 append 到该会话 history，避免主动/被动输出记忆断层。

## 4. 人格注入

- 来源：AstrBot `persona_manager` 的默认人格，`_resolve_persona` 3260-3281 依次尝试
  `get_default_persona_v3()` → `selected_default_persona_v3` → `selected_default_persona`，全失败用
  `FALLBACK_PERSONA` 256-259；`_persona_text` 3283-3312 输出「人格名称/人格设定」文本。
- 线上实体：插件 config 的 `persona`/`world_setting` 均为空串；真正的设定在 AstrBot DB
  `personas.elaina_v2.system_prompt`（193 行/6588 字，已固化到 `assets/persona.json`）——
  详细拆解见 `docs/PHASE0_PERSONA.md`，本文不重复。
- 注入方式：**每次生成时动态取一次** persona 文本，`format` 进四个提示词的 `{persona}` 槽：
  `GUARD_DEFAULT_PROMPT` 261-270、`PLANNER_PROMPT` 277-289、`WRITER_PROMPT` 291-301、`REVIEWER_PROMPT` 303-317；
  世界观 `{world_block}` 由 `WORLD_BLOCK_TEMPLATE` 272-274 + `world_setting` 拼（3183 调用 `_world_block`）。
- 关键发现：**`main.py` 里没有「傲娇」二字**（`grep 傲娇|毒舌|才没有|嘴硬` 无命中）。人格提示词对语气的约束只有
  弱描述：`FALLBACK_PERSONA` 256-259「说话像真实的朋友…不油腻…不滥用表情，不叫用户『亲』『宝』」、
  WRITER_PROMPT 300「允许句末自然带一句轻问」、REVIEWER_PROMPT 307「语气统一、自然…不要每条都像模板」。
  傲娇属性完全由 persona 正文承载，代码侧没有任何结构（无开关、无强度、无状态）。
- 静态 vs 动态：字符串每轮重拼，但**内容在运行期恒定**——没有人格状态、没有关系阶段、没有情绪变量参与。

## 5. 情感状态（现状：零）

- `grep -n "emotion|mood|PAD|affect|valence|arousal|sentiment|情感|情绪" main.py` 只命中提示词/注释性文字：
  356「5. **情绪与状态**：最近的心情、身体状态、压力来源」（记忆整理 MEMORY_PROMPT）、
  386「…情绪状态」（用户画像取舍）、786（表情包关键词表 `emotion`）、3658（`refresh_memory` docstring）。
- 代码中**没有任何**情绪状态变量、没有 PAD/效价-唤醒度、没有生成前读取、也没有发送后更新/衰减。
- 已有的「情绪相关」仅两处：记忆整理要求保留用户的情绪状态；事实校验只判事实冲突（3474-3519），不判语气。
- 结论：情感系统需要从零建（状态存储 + 时间衰减 + 生成前注入 + 交互后更新），当前没有任何可复用状态。

## 6. 阶段一改造必须处理的点（按优先级）

| 优先级 | 问题 | 证据 | 处理方向 |
| --- | --- | --- | --- |
| P0 | 补发会批量连发 | 3415-3426 / 3445-3459 / repro A | 每 tick 最多补 1~2 条；其余显式丢弃并记日志；或把 grace 改为「只补最近一条」 |
| P0 | 派发无锁、KV 末尾才写 | 3398-3467 / repro B | `_on_minute` 用 asyncio.Lock 罩住 dispatch 全程；改「逐条发送即写回」 |
| P0 | 任务注册无幂等 | 2958-3069 / cron_manager.py:145-170,266-273 | 注册前按 name 清理旧 job；启动时清 stale basic 行；回退分支先 shutdown 再 start |
| P1 | active_agent 只暂停一次 | 2932-2953 | 每次 boot 与新建 job 后都暂停；或改为订阅式拦截 |
| P1 | 生成与派发并发时水位/条目不一致 | 3521-3551 vs 3398-3467 | 生成与派发共用锁或引入 plan 版本号 |
| P2 | 单 tick 耗时被 LLM 拖长 | 3447-3456（每条一次 LLM）+ 3570 sleep 0.3 | 批量一次校验或并发发送；发送与校验解耦 |
| P2 | 可观测性缺失 | 3459 只有逐条日志 | 增加「本 tick 发出 N 条 / KV 写失败」告警与重试 |
| P3 | 情感/人格静态 | 全文件无情绪变量 | 阶段二：PAD 状态 + 衰减 + 注入 + 更新 |

## 附：证据与环境限制

- 日志证据：`_scratch/guard_all.txt`（27.6MB，620 条插件日志，其中「已发送」282 条，按时间倒退 >30min
  切成 40 段 ≈ 每次插件重载一段，每段 5~10 条，无段内重复计划时间、无同分钟连发）；
  `_scratch/pg_raw.txt`（120h 带时间戳版本，39 次发送，
  分布 10-23 3 / 10-24 9 / 10-25 9 / 10-26 7 / 10-27 5 / 10-28 6）。
- 调度器实现：`_scratch/cron_manager.py`（AstrBot `cron/manager.py` 516 行）。
- 受限说明：本次复查后期服务器 SSH 横幅失败（`Error reading SSH protocol banner`），原因是本机 Clash
  订阅已切回「外网加速」，家里 NAS 隧道不可达；为不打断 Lead 的外网检索未做订阅切换。
  因此「线上同一分钟多条 assistant 记录」这一项只做到表结构确认（`platform_message_history` 列
  `created_at/user_id/platform_id/sender_id/content`，1035 行），未做逐会话分桶查询；
  上述爆发结论由**代码 + 派发日志 + 本地复现**三方支撑，不依赖该查询。
