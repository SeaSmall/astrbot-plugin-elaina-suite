# Elaina 工具箱（六合一）· astrbot_plugin_elaina_suite

把 SeaSmall 的 **6 个 AstrBot 插件合并成一个**，一次安装、全部可用、互不冲突：

| # | 功能 | 来源插件 |
| --- | --- | --- |
| 1 | 🖼 图片问答桥接（小米 MiMo → 百度图像识别 → OCR.space 三级识别） | `astrbot_plugin_image_bridge` |
| 2 | 📰 每日简报（多城市天气 + 昨日国内/国际 + 科技/医药/政策前沿 + GitHub 日升榜，AI 总结定时推送） | `astrbot-plugin-daily-digest` |
| 3 | 🚪 主动消息门禁 + 每日人格消息（拦截非本插件主动发言；预生成「明天」的随机时间点消息） | `astrbot-plugin-proactive-guard` |
| 4 | 🎭 Elaina 表情包（本地表情库 AI 选图 / 关键词在线抓图，按概率随回复发送） | `astrbot_plugin_Elaina_meme_Bridge` + `astrbot_plugin_meme_responder` |
| 5 | 🛡 LLM 拦截兜底（挡住被泄漏的错误文案如 `content_filter` / `high risk`，可换备用 provider 重发） | `astrbot-plugin-llm-guard` |
| 6 | 🖥 系统健康报告（CPU/内存/磁盘/运行时长/进程 → 图片定时推送） | `astrbot-plugin-system-health` |

> 合并后**功能与原插件逐行等价**，只是收进一个插件目录，避免多插件抢钩子 / 抢配置键 / 互相拦截。

## ✨ 各功能要点

**1. 图片问答桥接**：用户发图先识别（小米 MiMo → 百度 → OCR.space 三级降级），随后文字提问时把识别内容注入 LLM；纯图片消息会挂起等待提问。

**2. 每日简报**：天气支持**多城市**（默认上海）且内置防高并发（结果缓存 / 地理编码缓存 7 天 / 城市单飞锁 / 请求节流 / 失败隔离）；新闻源为**实时免费源**（央视 JSONP、腾讯热榜、百度热搜、中国日报、WHO、Nature Medicine）；GitHub 日升榜 6 路降级（GitHub Search → gh-proxy 镜像 → 全站热门 → OSS Insight → gitterapp）；AI 总结被内容安全拦截时自动精简重试 → 模板兜底；到点未发会每 5 分钟补发（直到 `send_deadline`）；长简报可发图片 / md 文件。

**3. 主动消息门禁 + 人格消息**：拦截「非本插件的 AI 主动发言」（用户没说话时一律不发）；每天后台预生成**明天**的 5-10 条人格消息，随机时间点，到点发送并删除；每日 06:00 重新生成，日复一日。

**4. Elaina 表情包**：两种来源——
- `library`（本地表情库）：让 AI 从本地表情包目录里挑一张发送（原 Elaina 表情包行为）；
- `keyword`（关键词在线抓图）：AI 在回复里顺带给出表情关键词，从免费 API 抓图；
- `auto`（默认）：本地有表情库就用 library，否则用 keyword。
本地表情包目录**自动检测三顺位**：① 配置 `meme_dir` → ② 插件目录下 `meme/` → ③ AstrBot `plugins/*/meme`（可**直接复用**你已安装的 Elaina 表情包素材，无需拷贝）。

**5. LLM 拦截兜底**：发送前检查即将发出的文本，命中拦截特征时按 `guard_mode` 替换 / 丢弃 / 仅记录；可选 `guard_retry_provider_id` 用备用模型重新生成；`guard_dump_on_reject` 可把触发风控的完整 prompt 转存，便于定位根因。

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
| `meme_responder` 的 `enabled` | `meme_enabled` |
| `llm_guard` 的全部键 | 统一加 `guard_` 前缀（`guard_enabled` / `guard_mode` …） |
| `system_health` 的 `timezone` / `target_sessions` / `show_disk` / `show_network` | `health_timezone` / `health_target_sessions` / `health_show_disk` / `health_show_network` |

- **门禁放行自身**：合并插件内部的主动发送（日报、人格消息、健康报告、表情包）在发送期间把
  `_bypass_cnt` 加一，门禁直接放行；其它插件 / AstrBot 内置主动 Agent 仍被拦截。
  （单插件版曾出现的「日报被门禁拦掉」问题，在合并版里从结构上不存在。）
- **钩子清单**：`event_message_type(ALL)` × 3（图片桥接 / 表情包 / 门禁活跃记录）、
  `on_llm_request` × 3（图片注入 / 表情关键词 / 拦截兜底快照）、`on_llm_response` × 1（表情包）、
  `on_decorating_result` × 1（拦截兜底）——互不抢占，全部生效。
- **指令清单**：`/digest`(`/日报`)、`/订阅日报`、`/退订日报`、`/今日计划`、`/重建今日计划`、
  `/picreset`、`/health`(`/健康报告`)、`/llmguard`(`/拦截状态`)、`/llmguarddump`(`/转存请求`)——无重名。

## ⚙️ 配置说明（按模块）

配置面板里共 88 项，按前缀分组：

- **图片桥接**：`xiaomi_api_key` / `xiaomi_base_url` / `xiaomi_model` / `baidu_api_key` / `baidu_secret_key` / `ocr_api_url` / `ocr_api_key` / `ocr_language` / `ocr_timeout` / `pending_ttl` / `recognition_wait_timeout` / `emoji_wait_pending` / `prompt_template`
- **每日简报**：`send_cron` / `digest_timezone` / `send_deadline` / `weather_city`（多城市）/ `weather_enabled` / `weather_cache_minutes` / `weather_interval_seconds` / `news_cn_enabled` / `news_intl_enabled` / `tech_enabled` / `medical_enabled` / `policy_enabled` / `github_trending_enabled` / `github_trending_days` / `github_trending_count` / `github_trending_min_stars` / `max_items_per_section` / `ai_summary_enabled` / `digest_send_mode` / `digest_long_threshold` / `digest_target_sessions` / `feeds_cn` / `feeds_intl` / `feeds_tech` / `feeds_medical` / `feeds_policy` / `llm_prompt`
- **门禁 / 人格消息**：`pg_enabled` / `block_proactive` / `strict_mode` / `active_window_minutes` / `allow_senders` / `pause_active_agent_jobs` / `gen_time` / `pg_timezone` / `message_prompt` / `msg_count_min` / `msg_count_max` / `window_start` / `window_end` / `missed_grace_minutes` / `record_to_history` / `pg_target_sessions` / `only_private`
- **Elaina 表情包**：`meme_enabled` / `meme_source` / `meme_dir` / `trigger_prob` / `meme_prompt` / `api_url_tangdouz` / `api_url_apihz_sogou` / `api_url_apihz_baidu` / `apihz_id` / `apihz_key` / `meme_count` / `send_timeout` / `max_meme_mb`
- **LLM 拦截兜底**：`guard_enabled` / `guard_mode` / `guard_retry_provider_id` / `guard_retry_timeout` / `guard_fallback_text` / `guard_soft_scan` / `guard_soft_max_len` / `guard_extra_markers` / `guard_patch_custom_error_reply` / `guard_dump_on_reject` / `guard_dump_dir` / `guard_log_prompt_preview`
- **系统健康**：`health_enabled` / `health_cron` / `health_timezone` / `health_target_sessions` / `health_show_disk` / `health_show_network`

## ❓ 常见问题

**Q：表情包发不出去？**
- 检查本地表情库是否被识别（日志 `[elaina_meme] 本地表情库: ...`）；未识别就把表情图放进插件目录 `meme/`，或把 `meme_dir` 指向已有素材目录；
- QQ 官方平台媒体上传偶发失败时，插件会校验图片格式/大小、最多换 3 张重试、超时放弃，不会卡死（详见更新日志）。
**Q：日报里出现 `high risk` 之类的报错文案？**
- 那是服务商内容安全拦截，本插件的第 5 个功能（LLM 拦截兜底）会自动挡掉；日报侧还有「精简重试 + 模板兜底」双保险。
**Q：系统健康报告没发？**
- 多半是没装 psutil（日志有提示）；`pip install psutil` 后重载插件即可。

## 📄 更新日志

- [CHANGELOG.md](CHANGELOG.md)

## ⚠️ 免责声明

- 抓取的新闻/表情包内容版权归原作者所有，仅供个人学习与自用。
- AI 生成内容仅供参考，请自行甄别。