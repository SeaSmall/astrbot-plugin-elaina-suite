# Elaina 工具箱（六合一）· astrbot_plugin_elaina_suite

把 SeaSmall 的 **6 个 AstrBot 插件合并成一个**，一次安装、全部可用、互不冲突：

| # | 功能 | 来源插件 |
| --- | --- | --- |
| 1 | 🖼 图片问答桥接（小米 MiMo → 百度图像识别 → OCR.space 三级识别） | `astrbot_plugin_image_bridge` |
| 2 | 📰 每日简报（多城市天气 + 昨日国内/国际 + 科技/医药/政策前沿 + GitHub 日升榜，AI 总结定时推送） | `astrbot-plugin-daily-digest` |
| 3 | 🚪 主动消息门禁 + 每日人格消息（拦截非本插件主动发言；预生成「明天」的随机时间点消息；带记忆库，用规划→并行写手→审校三个子代理协作成文） | `astrbot-plugin-proactive-guard` |
| 4 | 🎭 Elaina 表情包（本地表情库 + AI 选图，按概率随回复发送，自带原版 50 个素材） | `astrbot_plugin_Elaina_meme_Bridge` |
| 5 | 🛡 LLM 拦截兜底（挡住被泄漏的错误文案如 `content_filter` / `high risk`，可换备用 provider 重发） | `astrbot-plugin-llm-guard` |
| 6 | 🖥 系统健康报告（CPU/内存/磁盘/运行时长/进程 → 图片定时推送） | `astrbot-plugin-system-health` |

> 合并后**功能与原插件逐行等价**，只是收进一个插件目录，避免多插件抢钩子 / 抢配置键 / 互相拦截。

## ✨ 各功能要点

**1. 图片问答桥接**：用户发图先识别（**小米 MiMo → 百度 → OCR.space** 三级降级），随后文字提问时把识别内容注入 LLM；纯图片消息会挂起等待提问。
MiMo 默认模型为 **`mimo-v2.6-flash`**（V2.6 系列，2026-09-22 发布；全模态、便宜、适合高频识图），
模型名不可用时会自动降级尝试（`mimo-v2.6-flash` → `mimo-v2.6-pro` → `mimo-v2.5`），
所以官方迭代/下线模型名时识图不会直接失效（`mimo-v2.5` / `mimo-v2.5-pro` 将于 2026-10-21 下线）。

> ⚠️ **Token Plan 使用范围提醒**：小米官方文档明确 Token Plan 订阅额度**仅限在编程工具中使用**，
> 禁止用于「自动化脚本、自定义应用后端」等非编码场景的 API 调用，违规可能被暂停服务/封禁 Key。
> 若你把 Token Plan 的 `tp-` Key 用在本插件的识图里，属于上述受限场景 —— 建议改用
> [标准按量计费 API](https://platform.xiaomimimo.com/) 的 Key（`xiaomi_base_url` 相应改为 `https://api.xiaomimimo.com/v1` 之类，
> 以控制台显示为准），或把识图交给百度/OCR.space 那两级。

**2. 每日简报**：天气支持**多城市**（默认上海）且内置防高并发（结果缓存 / 地理编码缓存 7 天 / 城市单飞锁 / 请求节流 / 失败隔离）；新闻源为**实时免费源**（央视 JSONP、腾讯热榜、百度热搜、中国新闻网、CGTN、WHO、Nature Medicine（原默认的中国日报 `world_rss.xml` 已 404，已替换））；**新增「🤖 AI 界的新变化」与「🛡️ 网安漏洞与情报」两个板块**：前者默认量子位 + InfoQ 中文（`feeds_ai`，时效 `ai_recent_days` 默认 2 天）；后者默认 FreeBuf + 安全客 + 先知社区 + **CISA KEV 已知被利用漏洞目录**（`feeds_security`，时效 `security_recent_days` 默认 2 天；KEV 走 `security_kev_days` 默认 7 天，1.8MB JSON 有 6 小时下载缓存），KEV 条目会标出「已被用于勒索软件」并附 NVD 详情链接，网安板块因此不依赖「当天恰好有大新闻」；**「政策前沿」直接接政府官方网站**（中国政府网最新政策 / 中国政府网要闻 / 国家发改委新闻发布），不再用新闻热榜凑数，且因为政策不是每天都有，该板块时效放宽为「最近 N 天」（`gov_policy_recent_days`，默认 30 天），窗口内无新政策则退回最新几条；GitHub 日升榜 6 路降级（GitHub Search → gh-proxy 镜像 → 全站热门 → OSS Insight → gitterapp）；AI 总结被内容安全拦截时自动精简重试 → 模板兜底；到点未发会每 5 分钟补发（直到 `send_deadline`），补发与 08:00 正点任务**并发触发时靠锁 + KV 先占位只发一次**；长简报**图片与 .md 文件两份都给**（`digest_send_mode=both`），图片便于手机阅读、md 便于存档检索。图片走 AstrBot 的 t2i 渲染：网络渲染服务（`t2i.soulter.top` 等）503 或超时会回退本地 PIL，而官方 Docker 镜像里**没有任何中文字体**（中文会整篇渲染成方框 □），因此**插件启动时会把自带的中文字体（GB2312 子集，约 2MB）补到 `<data>/font.ttf`**——已存在则不覆盖，尊重你自己放的字体。

**3. 主动消息门禁 + 人格消息**：拦截「非本插件的 AI 主动发言」（用户没说话时一律不发）；消息内容由插件内置的 `assets/persona.json`（人格）与 `assets/worldview.json`（世界观）驱动，按 `self_share_ratio` 分配「分享自己的经历」与「关心对方」。
> 🆕 **主动消息 v2（阶段一，`living_enabled`，默认关闭）**：把「每日预生成计划 + 每分钟批量派发」换成**插件内部调度器 + 单会话决策循环**——每个会话一个决策 job、决策完自己排下一次；决策只产出「意图 + 时间偏移」，**触发那一刻才读记忆生成正文**（不预生成）；同一会话单槽待触发 + token 幂等，重启后过期的待触发**直接丢弃不补发**；每会话一把锁覆盖「决策→生成→发送」；状态落 `<data>/proactive_state.json` 原子写。开启后**完全不注册** AstrBot 的 `cron_manager`（不再依赖内置任务系统）。详见 [`docs/PHASE1_REPORT.md`](docs/PHASE1_REPORT.md)。
> 旧版行为（每日预生成 5-10 条）：`living_enabled=false` 时保持原样，可随时回滚对照。
消息内容由**人格 + 世界观**驱动：在 `world_setting` 里写好角色所处的世界与处境（例如「旅行中的魔女，正坐火车去下一个国家」），AI 会以第一人称分享她这一刻的经历与见闻，而不是天天问你吃饭睡觉；按 `self_share_ratio`（默认 70%）分配「分享自己」与「关心你」的比例，关心类至少保留 1 条——两者都要，不是把关心删掉。
生成时会**先读记忆库**：每天 03:00 让 AI 把近期真实对话总结成一份「关于你这个人」的档案（发生过的事、爱好与偏好、正在做的事与进度、人际关系、情绪状态、明确约定）存进记忆库，写消息时注入提示词，AI 因此记得你是谁、在忙什么、聊过什么，而不是每天凭空寒暄；发出的消息再写回会话历史，形成闭环，避免今天重复昨天的话。记忆**只记用户**、不记 AI 自己（语气、称呼、性格、做过什么由人格设定负责）；多个会话的摘要会再合并成一份去重档案，注入前还会兜底剃掉「助手自称…」这类行并按正文去重——`/记忆` 里看到的第一段就是真正注入给 AI 的内容。成文由「规划 → 并行写手 → 审校」三个子代理协作完成（每个时间点一个独立写手，上下文更小、不易串味），规划或子代理失败时自动回退单次调用。
> 🆕 **动机与注意力（阶段四 · GWA 式候选竞争 + 内在动机，`motivation_enabled`，默认关闭）**：每次决定要不要主动说话时，先在内心列几个候选念头（分享自己的事 / 关心对方 / 追问没说完的话题 / 对对方提过的事产生好奇 / 试探对方还在不在），再按「情感状态 × 学习进度 × 有没有具体由头」打分，**只有一个念头赢得注意力**进入生成，其余降权留到下次。学习进度门控负责防「只追新不沉淀」：某话题反复发起却没回应，优先级下降；得到回应后回升。低落时「分享自己的事」降权、「试探性确认」升权。新增 `/动机状态`（别名 `/注意力状态`）可看上次竞争谁赢、落选者分数与每条话题的学习进度。详见 [`docs/PHASE4_REPORT.md`](docs/PHASE4_REPORT.md)。

> 🆕 **傲娇表达（阶段五 · 三条行为路径 + 零容忍元话语，`tsundere_enabled`，默认开启）**：傲娇不再被写成可以直接说出口的标签
> （「才不是特意…」「我才没有…」这种替对方解读你的句子，出现即丢弃并重生成），而只给三条**行为**路径：
> 行动先行 + 借口收尾、毒舌包裹真实信息、关心以「泄露」出现。审校代理按「零元话语 / 可追溯的关心 / 语气一致」三条判定，
> 未通过用它的改写或重生成。情绪越负面，傲娇越「加密」：不直说难过，改用抱怨、挑刺、装作随口一问。
> 正常对话也生效（`on_llm_request` 注入 system prompt）。详见 [`docs/PHASE5_REPORT.md`](docs/PHASE5_REPORT.md)。

> 🆕 **情感状态 v2（阶段三 · Sentipolis PAD，`affect_enabled`，默认关闭）**：角色有一套**连续的三维情感状态**（愉悦 P / 唤醒 A / 掌控 D，各 [-1,1]），按论文的双速动力学演化——每轮对话走一次**快通道**即时更新，重要感（poignancy）累计超过 150 时走**慢通道**做一次反思归纳；衰减按真实时间折算（半衰期 120 分钟）。另加一个**沉默评估**模块：距上次交互远超该角色平常节奏时，注入一条「对方很久没回应」的负面评估事件（走正常更新通道，不直接改数值），并把衰减目标点拉向低落的基线。生成主动消息时读当前情感，转成简短的「此刻心情」注入提示词——低愉悦低掌控时会提示「试探、留退路、别把话说满」。用 `/情感状态` 查看。详见 [`docs/PHASE3_REPORT.md`](docs/PHASE3_REPORT.md)。

> 🆕 **长期记忆（阶段二 · mem0，`mem0_enabled`，默认关闭）**：接入 mem0（嵌入式 qdrant + fastembed 中文向量，全部落 `<data>/` 持久卷）。三件事：**写入**（用户+助手配成一轮，攒够 `mem0_flush_turns` 轮后后台写入新记忆）、**召回**（主动消息生成前用最近几条发言做查询，命中结果作为【你记得的事】注入提示词）、**种子**（`worldview.json` 的 34 条世界观事实在首次启动时写入记忆）。实现上全程懒加载单例 + `asyncio.to_thread`（`import mem0` 冷启 13.7s、建实例 1.6~10.4s、qdrant 目录独占锁），任何失败只降级不打断聊天；默认 `infer=False` 直接存原文（开启 LLM 抽取会有 13 秒级延迟）。详见 [`docs/PHASE2_REPORT.md`](docs/PHASE2_REPORT.md)。

**4. Elaina 表情包**：完全对齐原版 `astrbot_plugin_Elaina_meme_Bridge` —— AI 每次回复后按
`meme_probability` 掷骰子，命中就把 **AI 自己的回复文本 + 表情包文件名列表** 交给 AI，让它挑一张最合适的本地表情包发送；AI 给出的名字不在库里时随机兜底一张。
**只发本地素材，不做联网表情包搜索**（在线搜图容易搜错、发错、被防盗链）。
插件**自带原版那 50 个表情包**（`meme/` 目录，装完即用），也可以换成自己的素材：
本地表情包目录**自动检测三顺位**：① 配置 `meme_dir` → ② 插件目录下 `meme/` → ③ AstrBot `plugins/*/meme`（可**直接复用**你已单独安装的 Elaina 表情包素材，无需拷贝）。

**5. LLM 拦截兜底**：发送前检查即将发出的文本，命中拦截特征（如 `content_filter` / `high risk`）时按 `guard_mode` 处理——默认 **替换**：把**用户提示词重新注入 LLM 再生成一次**（provider 默认用当前会话模型，无需配置；第 1 次带人设、第 2 次起只重发用户提示词，全部失败才用 `guard_fallback_text`）；也可选 `drop`（丢弃）/ `log`（仅记录）。`guard_dump_on_reject` 可把触发风控的完整 prompt 转存，便于定位根因。

**6. 系统健康报告**：定时渲染成图片推送（不可用时回退文本）。psutil 为**可选依赖**：未安装时该功能自动禁用，不影响其它五个功能。

## 📦 安装

AstrBot WebUI →「插件」→「安装插件」→「通过 Git 地址安装」：

```
https://github.com/SeaSmall/astrbot-plugin-elaina-suite
```

依赖：`httpx`、`Pillow`（AstrBot 自带，用于 gif 切帧）、`psutil`（仅系统健康报告需要；`pip install psutil` 后重载插件即可启用）。要求 **AstrBot 4.x（>= 4.16.0）**。

## 🧩 兼容性设计（为什么能全部一起用）

- **单一 Star 类 + 7 个功能核心**：AstrBot 一个插件目录只注册一个 Star 类，因此本插件用
  `ElainaSuitePlugin` 持有 7 个核心（ImageBridge / DailyDigest / ProactiveGuard / LivingMemory / ElainaMeme / LlmGuard / SystemHealth），
  由它统一路由事件、指令与 LLM 钩子，核心间无重复注册。
- **配置键按模块加前缀**，彻底避免撞键：

| 原插件 | 合并后 |
| --- | --- |
| `proactive_guard` 的 `enabled` / `timezone` / `target_sessions` | `pg_enabled` / `pg_timezone` / `pg_target_sessions` |
| `daily_digest` 的 `timezone` / `target_sessions` | `digest_timezone` / `digest_target_sessions` |
| `Elaina_meme_Bridge` 的 `enabled` | `meme_enabled`（`meme_probability` / `meme_dir` 沿用原版同名键） |
| `llm_guard` 的全部键 | 统一加 `guard_` 前缀（`guard_enabled` / `guard_mode` …） |
| `system_health` 的 `timezone` / `target_sessions` / `show_disk` / `show_network` | `health_timezone` / `health_target_sessions` / `health_show_disk` / `health_show_network` |

- **门禁放行自身**：合并插件内部的主动发送（日报、人格消息、健康报告、表情包）在发送期间把
  `_bypass_cnt` 加一，门禁直接放行；其它插件 / AstrBot 内置主动 Agent 仍被拦截。
  （单插件版曾出现的「日报被门禁拦掉」问题，在合并版里从结构上不存在。）
- **钩子清单**：`event_message_type(ALL)` × 2（图片桥接 / 门禁活跃记录）、
  `on_llm_request` × 2（图片注入 / 拦截兜底快照）、`on_llm_response` × 3（长期记忆写入 `priority=99998` / 情感快通道 `priority=99997` / 表情包 `priority=99999`）、
  `on_decorating_result` × 1（拦截兜底）——互不抢占，全部生效。
- **指令清单**：`/digest`(`/日报`)、`/订阅日报`、`/退订日报`、`/今日计划`、`/重建今日计划`、
  `/记忆`、`/刷新记忆`、`/主动状态`(`/主动消息状态`)、`/记忆状态`(`/长期记忆状态`)、`/情感状态`(`/心情状态`)、`/picreset`、`/health`(`/健康报告`)、`/llmguard`(`/拦截状态`)、`/llmguarddump`(`/转存请求`)——无重名。

## ⚙️ 配置说明（按模块）

配置面板里共 156 项，按前缀分组：

- **图片桥接**：`xiaomi_api_key` / `xiaomi_base_url` / `xiaomi_model` / `baidu_api_key` / `baidu_secret_key` / `ocr_api_url` / `ocr_api_key` / `ocr_language` / `ocr_timeout` / `pending_ttl` / `recognition_wait_timeout` / `emoji_wait_pending` / `prompt_template`
- **每日简报**：`send_cron` / `digest_timezone` / `send_deadline` / `weather_city`（多城市）/ `weather_enabled` / `weather_cache_minutes` / `weather_interval_seconds` / `news_cn_enabled` / `news_intl_enabled` / `tech_enabled` / `ai_enabled` / `security_enabled` / `medical_enabled` / `policy_enabled` / `github_trending_enabled` / `github_trending_days` / `github_trending_count` / `github_trending_min_stars` / `max_items_per_section` / `ai_summary_enabled` / `digest_send_mode` / `digest_long_threshold` / `digest_also_send_md` / `digest_target_sessions` / `gov_policy_recent_days` / `ai_recent_days` / `security_recent_days` / `security_kev_days` / `feeds_cn` / `feeds_intl` / `feeds_tech` / `feeds_ai` / `feeds_security` / `feeds_medical` / `feeds_policy` / `llm_prompt`
- **门禁 / 人格消息**：`pg_enabled` / `block_proactive` / `strict_mode` / `active_window_minutes` / `allow_senders` / `pause_active_agent_jobs` / `gen_time` / `pg_timezone` / `world_setting` / `self_share_ratio` / `message_prompt` / `subagent_enabled` / `subagent_parallel` / `subagent_concurrency` / `subagent_review` / `memory_enabled` / `memory_cron` / `memory_turns` / `memory_window_hours` / `memory_refresh_cooldown` / `memory_rewrite_stale` / `memory_notes_enabled` / `memory_notes_chars` / `memory_refresh_before_gen` / `dispatch_fact_check` / `dispatch_regenerate_on_memory_change` / `memory_max_chars` / `memory_raw_fallback` / `msg_count_min` / `msg_count_max` / `window_start` / `window_end` / `missed_grace_minutes` / `record_to_history` / `pg_target_sessions` / `only_private`
- **主动消息 v2（阶段一）**：`living_enabled` / `living_min_silence_minutes` / `living_cooldown_minutes` / `living_daily_max` / `living_quiet_hours` / `living_offset_min` / `living_offset_max` / `living_ramp_minutes` / `living_decision_check_minutes` / `living_persona_chars` / `living_worldview_chars`
- **长期记忆（阶段二 · mem0）**：`mem0_enabled` / `mem0_llm_model` / `mem0_llm_base_url` / `mem0_llm_api_key` / `mem0_embed_model` / `mem0_top_k` / `mem0_threshold_pct` / `mem0_seed_facts` / `mem0_flush_turns` / `mem0_recall_chars` / `mem0_infer`
- **动机与注意力（阶段四 · GWA 竞争 + 内在动机）**：`motivation_enabled` / `motivation_affect_gate` / `motivation_learning_gate` / `motivation_topic_max` / `motivation_topic_chars` / `motivation_engage_window_minutes` / `motivation_select_noise` / `motivation_loser_decay`
- **傲娇表达（阶段五 · 三路径 + 审校）**：`tsundere_enabled` / `tsundere_review_enabled` / `tsundere_encrypt_negative` / `tsundere_max_retry` / `tsundere_chat_inject`
- **情感状态（阶段三 · Sentipolis PAD）**：`affect_enabled` / `affect_fast_mode` / `affect_decay_half_life_minutes` / `affect_silence_threshold_minutes` / `affect_silence_check_minutes` / `affect_silence_cooldown_minutes` / `affect_silence_target` / `affect_reflect_poignancy` / `affect_slow_delta_cap` / `affect_inject_chars` / `affect_baseline`
- **Elaina 表情包**：`meme_enabled` / `meme_probability` / `meme_dir` / `send_timeout` / `max_meme_mb`
- **LLM 拦截兜底**：`guard_enabled` / `guard_mode` / `guard_retry_provider_id` / `guard_retry_attempts` / `guard_retry_keep_system_prompt` / `guard_retry_timeout` / `guard_fallback_text` / `guard_soft_scan` / `guard_soft_max_len` / `guard_extra_markers` / `guard_patch_custom_error_reply` / `guard_dump_on_reject` / `guard_dump_dir` / `guard_log_prompt_preview`
- **系统健康**：`health_enabled` / `health_cron` / `health_timezone` / `health_target_sessions` / `health_show_disk` / `health_show_network`

## ❓ 常见问题

**Q：表情包发不出去？**
- 插件启动时会打印 `[elaina_meme] 本地表情库就绪：…（N 个表情包）`；若打印的是「未找到本地表情库」，说明 `meme/` 缺失或 `meme_dir` 指错了；
- 一个都不发：确认 `meme_enabled` 打开、`meme_probability` > 0（默认 0.5）；
- 发的是「随机一张」而不是 AI 选的那张：说明 AI 回的文件名没对上（日志里会有 `AI 选择 \`xxx\` 不在库中，改用随机一张`），换个更强的对话模型通常就好；
- QQ 官方平台媒体上传偶发失败时，插件会校验图片魔数/大小并带 `send_timeout` 超时，不会卡死（详见更新日志）。
**Q：情感状态（`affect_enabled`）会不会让角色情绪乱跳？**

不会。快通道默认是规则式评估（夸赞/冷淡/冲突/极短回复四类，零 LLM 调用、零延迟），
每个维度单次增量有上限（`affect_slow_delta_cap`），并带 120 分钟半衰期的持续回落；
慢通道要等重要度累计超过 `affect_reflect_poignancy`（默认 150）才触发一次，且必须 LLM 可用。
开 `affect_fast_mode=llm` 可以换成更细的 LLM 评估，但每轮会多一次极短调用。
状态落在 `<data>/affect_state.json`，`/情感状态` 可随时查看当前 PAD、重要度与沉默严重度；
把开关关掉即完全回到旧行为（不读不写不注入）。

**Q：长期记忆（`mem0_enabled`）值得开吗？会不会拖慢回复？**
- 第一次开启或重启后首次触发生成时会有 10~20 秒的模型加载（日志 `[memory] mem0 就绪（Xs）`），这段时间聊天不受影响（后台线程）；
- 召回本身约 200ms（缓存命中更快）；写入是攒够轮数后后台批量跑，不阻塞对话；
- 事实抽取（`mem0_infer=true`）单次约 13 秒且有英文输出风险，默认关闭；想让它自动归纳偏好再开；
- 用 `/记忆状态` 可以看到是否已就绪、世界观种子是否写入。若日志出现「mem0 初始化失败」，本次运行会自动降级为无长期记忆模式，聊天照常。
**Q：日报里出现 `high risk` 之类的报错文案？**
- 那是服务商内容安全拦截，本插件的第 5 个功能（LLM 拦截兜底）会自动挡掉；日报侧还有「精简重试 + 模板兜底」双保险。
**Q：政策前沿为什么不是「昨日」的？**
- 政府官网的政策文件**不是每天都有**（可能数日才发一条），硬卡「昨日」会让整个板块长期空白。因此该板块单独放宽为「最近 `gov_policy_recent_days` 天」（默认 30 天）；窗口内确实没有新政策时，会退回最新几条并在日志里说明（`近 30 天无新政策，退回最新 N 条`）。其余板块仍严格卡「昨日」。
**Q：图片上的文字被划掉了（有横线穿过）？**
- 那是 AstrBot 文本转图渲染器对内联 Markdown 的处理问题：本地 PIL 渲染器把 `__文字__` 同时当成粗体和下划线（两条正则完全相同、未做重叠去重），并且会把 `~~文字~~` 真的画成删除线；网络渲染路径的 `marked.js` 也会把 `~~` 渲染成 `<del>`。
- 本插件在送渲染前会用 `_sanitize_for_image()` **剥掉 `~~` / `__` / `**` / `*` / `_` 这些标记（保留文字本身）**，日报是资讯文本不需要这些修饰，所以正常情况下不会再有划线。
**Q：简报太长被截断 / 漏板块？**
- 到点推送时按 `digest_send_mode` 发送：默认 `auto`（超过 `digest_long_threshold` 就**图片 + .md 两份都给**）；
- 两条消息平台的媒体上传可能各重试约 90 秒，插件对单个目标加了超时；**图片与 md 都失败**才会回退纯文本（会分块发送，`limit=3800` 字符/条）。
**Q：系统健康报告没发？**
- 多半是没装 psutil（日志有提示）；`pip install psutil` 后重载插件即可。
**Q：`/记忆` 显示「还没有记忆」/ 主动消息不提聊过的事？**
- 记忆是**后台攒出来**的：默认每天 03:00（`memory_cron`）全量总结一次，装好当天记忆库是空的属正常；想立刻要，用 `/刷新记忆` 手动触发一次。日常生成主动消息前，若记忆距上次刷新超过 `memory_refresh_cooldown`（默认 30 分钟）也会就地增量刷新一次，不会等到第二天。
- 记忆的来源是**会话历史**，所以要能枚举到会话：如果 `pg_target_sessions` 留空且 AstrBot 的会话库读不到当前会话，总结就会没有对象（日志里 `记忆已更新：N 个会话` 的 N 会是 0）。这种情况直接填 `pg_target_sessions`，或在群里发一条消息后再 `/刷新记忆`。
- 想让 AI 立刻知道某件事，最可靠的办法是写 **AstrBot 工作区里的 `user_profile.md`**（`<data>/workspaces/<会话>/user_profile.md`）：插件会把它读出来拼在记忆最前面，优先级高于 AI 的滚动摘要，改完下一条主动消息就生效，不用等总结。
- 记忆里留下了**已经过期/已完成的待办**（AI 还在追问昨天做完的事）？用 `/记忆重置` 清空后重建（走插件 KV 接口）。**不要直接改 data_v4.db**：`preferences` 在 AstrBot 内存里有副本，直接改数据库会被下一次写回覆盖，删掉的记忆会自己回来。

**计划比记忆旧**：主动消息的计划是**前一天预生成**的，记忆却随时在更新，于是出现过「记忆里写着卡已领、第二天消息还在提醒去领卡」。现在有三道保护：计划里存**记忆水位**（`memory_updated`），派发前与当前记忆比对；每条消息发出前用最新记忆做一次**事实校验**，冲突则改写或丢弃（`dispatch_fact_check`）；生成时点排在记忆刷新之后（默认 03:20）并**重建今日还没到点的条目**（`dispatch_regenerate_on_memory_change`）。
- 另外 `record_to_history` 打开时，插件自己发出的主动消息也会写回会话历史，这是**刻意**的：AI 才会记得自己昨天说过什么，不至于天天重复同一句。
- 记忆里**故意不写 AI 自己的事**（「AI 自称魔女」「AI 会提醒喝水」这类）：语气与行为风格由人格设定负责，写进记忆只会和人格设定重复、互相打架，所以提示词里明令不记，注入前还会再过滤一遍。

**Q：简报图片里的 emoji 不见了？**
A：v1.1.8 起图片文本会剔除 emoji（`_sanitize_for_image`）。原因同上：本地兜底渲染用的中文字体是 GB2312 子集，不含任何 emoji 字形，留着只会画成方框 □；而简报里的 emoji 只是板块装饰，标题文字本身都在。`.md` 文件不受影响，emoji 完整保留。

**Q：简报图片里中文全是方框 / 乱码，英文数字却正常？**
A：这是 AstrBot 本地 PIL 渲染缺 CJK 字体导致的（网络 t2i 服务 503 时才会走本地渲染）。v1.1.7 起插件会自动把自带中文字体补到 `<data>/font.ttf`；若你在容器里手动删过该文件，重启插件即可自动补回。要让网络渲染恢复，可检查容器到 `t2i.soulter.top` 的连通性。

## 📄 更新日志

- [CHANGELOG.md](CHANGELOG.md)

## ⚠️ 免责声明

- 抓取的新闻内容版权归原作者所有，仅供个人学习与自用。
- 内置的 50 个 Elaina 表情包来自 B 站「白之魔女-霜娜」（[b23.tv/CnZYwWY](https://b23.tv/CnZYwWY)），版权归原作者所有，**仅供个人学习，禁止商用**。
- AI 生成内容仅供参考，请自行甄别。