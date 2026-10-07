# Elaina 工具箱（六合一）· astrbot_plugin_elaina_suite

把 SeaSmall 的 **6 个 AstrBot 插件合并成一个**，一次安装、全部可用、互不冲突：

| # | 功能 | 来源插件 |
| --- | --- | --- |
| 1 | 🖼 图片问答桥接（小米 MiMo → 百度图像识别 → OCR.space 三级识别） | `astrbot_plugin_image_bridge` |
| 2 | 📰 每日简报（多城市天气 + 昨日国内/国际 + 科技/医药/政策前沿 + GitHub 日升榜，AI 总结定时推送） | `astrbot-plugin-daily-digest` |
| 3 | 🚪 主动消息门禁 + 每日人格消息（拦截非本插件主动发言；预生成「明天」的随机时间点消息） | `astrbot-plugin-proactive-guard` |
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

**2. 每日简报**：天气支持**多城市**（默认上海）且内置防高并发（结果缓存 / 地理编码缓存 7 天 / 城市单飞锁 / 请求节流 / 失败隔离）；新闻源为**实时免费源**（央视 JSONP、腾讯热榜、百度热搜、中国日报、WHO、Nature Medicine）；GitHub 日升榜 6 路降级（GitHub Search → gh-proxy 镜像 → 全站热门 → OSS Insight → gitterapp）；AI 总结被内容安全拦截时自动精简重试 → 模板兜底；到点未发会每 5 分钟补发（直到 `send_deadline`）；长简报可发图片 / md 文件。

**3. 主动消息门禁 + 人格消息**：拦截「非本插件的 AI 主动发言」（用户没说话时一律不发）；每天后台预生成**明天**的 5-10 条人格消息，随机时间点，到点发送并删除；每日 06:00 重新生成，日复一日。

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

- **单一 Star 类 + 6 个功能核心**：AstrBot 一个插件目录只注册一个 Star 类，因此本插件用
  `ElainaSuitePlugin` 持有 6 个核心（ImageBridge / DailyDigest / ProactiveGuard / ElainaMeme / LlmGuard / SystemHealth），
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
  `on_llm_request` × 2（图片注入 / 拦截兜底快照）、`on_llm_response` × 1（表情包，`priority=99999`）、
  `on_decorating_result` × 1（拦截兜底）——互不抢占，全部生效。
- **指令清单**：`/digest`(`/日报`)、`/订阅日报`、`/退订日报`、`/今日计划`、`/重建今日计划`、
  `/picreset`、`/health`(`/健康报告`)、`/llmguard`(`/拦截状态`)、`/llmguarddump`(`/转存请求`)——无重名。

## ⚙️ 配置说明（按模块）

配置面板里共 82 项，按前缀分组：

- **图片桥接**：`xiaomi_api_key` / `xiaomi_base_url` / `xiaomi_model` / `baidu_api_key` / `baidu_secret_key` / `ocr_api_url` / `ocr_api_key` / `ocr_language` / `ocr_timeout` / `pending_ttl` / `recognition_wait_timeout` / `emoji_wait_pending` / `prompt_template`
- **每日简报**：`send_cron` / `digest_timezone` / `send_deadline` / `weather_city`（多城市）/ `weather_enabled` / `weather_cache_minutes` / `weather_interval_seconds` / `news_cn_enabled` / `news_intl_enabled` / `tech_enabled` / `medical_enabled` / `policy_enabled` / `github_trending_enabled` / `github_trending_days` / `github_trending_count` / `github_trending_min_stars` / `max_items_per_section` / `ai_summary_enabled` / `digest_send_mode` / `digest_long_threshold` / `digest_target_sessions` / `feeds_cn` / `feeds_intl` / `feeds_tech` / `feeds_medical` / `feeds_policy` / `llm_prompt`
- **门禁 / 人格消息**：`pg_enabled` / `block_proactive` / `strict_mode` / `active_window_minutes` / `allow_senders` / `pause_active_agent_jobs` / `gen_time` / `pg_timezone` / `message_prompt` / `msg_count_min` / `msg_count_max` / `window_start` / `window_end` / `missed_grace_minutes` / `record_to_history` / `pg_target_sessions` / `only_private`
- **Elaina 表情包**：`meme_enabled` / `meme_probability` / `meme_dir` / `send_timeout` / `max_meme_mb`
- **LLM 拦截兜底**：`guard_enabled` / `guard_mode` / `guard_retry_provider_id` / `guard_retry_attempts` / `guard_retry_keep_system_prompt` / `guard_retry_timeout` / `guard_fallback_text` / `guard_soft_scan` / `guard_soft_max_len` / `guard_extra_markers` / `guard_patch_custom_error_reply` / `guard_dump_on_reject` / `guard_dump_dir` / `guard_log_prompt_preview`
- **系统健康**：`health_enabled` / `health_cron` / `health_timezone` / `health_target_sessions` / `health_show_disk` / `health_show_network`

## ❓ 常见问题

**Q：表情包发不出去？**
- 插件启动时会打印 `[elaina_meme] 本地表情库就绪：…（N 个表情包）`；若打印的是「未找到本地表情库」，说明 `meme/` 缺失或 `meme_dir` 指错了；
- 一个都不发：确认 `meme_enabled` 打开、`meme_probability` > 0（默认 0.5）；
- 发的是「随机一张」而不是 AI 选的那张：说明 AI 回的文件名没对上（日志里会有 `AI 选择 \`xxx\` 不在库中，改用随机一张`），换个更强的对话模型通常就好；
- QQ 官方平台媒体上传偶发失败时，插件会校验图片魔数/大小并带 `send_timeout` 超时，不会卡死（详见更新日志）。
**Q：日报里出现 `high risk` 之类的报错文案？**
- 那是服务商内容安全拦截，本插件的第 5 个功能（LLM 拦截兜底）会自动挡掉；日报侧还有「精简重试 + 模板兜底」双保险。
**Q：系统健康报告没发？**
- 多半是没装 psutil（日志有提示）；`pip install psutil` 后重载插件即可。

## 📄 更新日志

- [CHANGELOG.md](CHANGELOG.md)

## ⚠️ 免责声明

- 抓取的新闻内容版权归原作者所有，仅供个人学习与自用。
- 内置的 50 个 Elaina 表情包来自 B 站「白之魔女-霜娜」（[b23.tv/CnZYwWY](https://b23.tv/CnZYwWY)），版权归原作者所有，**仅供个人学习，禁止商用**。
- AI 生成内容仅供参考，请自行甄别。