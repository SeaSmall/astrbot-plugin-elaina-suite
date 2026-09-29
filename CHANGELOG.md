# Changelog

## v1.0.0（2026-08-30）

### ✨ 六合一合并发布

由以下 6 个独立插件合并为单一插件（功能不变、互相兼容）：

1. `astrbot_plugin_image_bridge` —— 图片问答桥接（小米 → 百度 → OCR 三级识别）
2. `astrbot-plugin-daily-digest` —— 每日简报（多城市天气 / 昨日新闻 / 科技医药政策前沿 / GitHub 日升榜）
3. `astrbot-plugin-proactive-guard` —— 主动消息门禁 + 每日人格消息
4. `astrbot_plugin_Elaina_meme_Bridge` + `astrbot_plugin_meme_responder` —— Elaina 表情包（本地库 + 在线抓图）
5. `astrbot-plugin-llm-guard` —— LLM 拦截兜底（错误文案拦截 / 备用 provider 重发 / 请求转存）
6. `astrbot-plugin-system-health` —— 系统健康报告

### 🧩 合并与兼容处理

- **单一 Star 类 + 6 个功能核心**：`ElainaSuitePlugin` 持有 6 个核心并统一路由
  事件 / 指令 / LLM 钩子，避免同模块多 Star 类互相覆盖。
- **配置键加前缀**避免撞键：`guard_*`（拦截兜底）、`health_*`（健康报告）、
  `pg_*`（门禁）、`digest_*`（日报）、`meme_*`（表情包）；另新增
  `meme_source`（library / keyword / auto）与 `meme_dir`（本地表情库目录）。
- **门禁放行自身**：日报 / 人格消息 / 健康报告 / 表情包的发送统一走 `_gated_send`
  （`_bypass_cnt` 放行），从结构上避免"自己的日报被自己的门禁拦掉"。
- **依赖保护**：`httpx` 为 AstrBot 自带；`Pillow`、`psutil` 均为可选（try 导入），
  缺失时对应子功能降级/禁用，不影响插件加载与其它功能。
- **表情包素材复用**：本地表情库目录自动检测三顺位（配置 → 插件目录/meme →
  AstrBot plugins/*/meme），可直接复用已安装的 Elaina 表情包素材。

### 🛡 合并进来的修复（相对各独立插件的最新版本）

- 日报：AI 总结被内容安全拦截时自动「精简数据重试 → 模板兜底」，拦截文案不再进日报；
- 日报：图片/md 文件发送加 90s 超时，全部失败自动回退纯文本；
- 日报：每 5 分钟兜底补发 + 每天最多一次 + `send_deadline` 截止；
- 日报：陈旧死源（如已停更的人民网 RSS）不再被当作「昨日」新闻；
- 表情包：图片魔数/大小校验 + 最多 3 张轮换重试 + 发送超时；
- 门禁：默认放行日报插件（合并后不再需要，已由 `_gated_send` 结构化解决）。

### 🧪 测试

- `tests/test_merged.py`：14 组冒烟测试（常量/工具、唯一 Star 类、6 核心、
  handler 与指令唯一性、实例化、门禁拦截与放行、消息路由、指令路由、
  拦截判定、本地表情库、健康报告、生命周期）。
## v1.0.1（2026-08-30）

### ✨ 变更：LLM 拦截兜底改为「重新注入用户提示词再生成」

- **背景**：此前只有配置了 `guard_retry_provider_id` 才会重新生成；未配置时命中拦截只发一句兜底文案
  （等于把这一轮终止了）。用户要求：AI 回复是拦截文案时，把**用户提示词重新注入**再拿真回复。
- **新行为**（`guard_mode=replace`，默认）：
  1. provider **默认取当前会话模型**（`guard_retry_provider_id` 留空即可，开箱即用）；
     填了则用指定 provider（建议不同服务商，避开同一家风控）；
  2. **第 1 次尝试携带原始人设/系统提示词**；**第 2 次起只重发用户提示词**
     （去掉人设与历史，通常能绕开由人设/历史触发的风控）；
  3. 每次返回都二次校验，避免把错误文案又发出去；
  4. 全部尝试失败才退回 `guard_fallback_text`。
- **新配置**：`guard_retry_attempts`（默认 2，1~3）、`guard_retry_keep_system_prompt`（默认 true）；
  `guard_retry_provider_id` 语义改为「留空 = 当前会话模型」。
- `/llmguard`（`/拦截状态`）输出改为显示「重新注入 provider / 尝试次数 / 是否携带人设 / 重新注入成功数」。
- 同步改动：独立插件 `astrbot-plugin-llm-guard` 已发布 v1.1.0。

## v1.0.2（2026-08-30）

### ✨ 更新：识图默认模型升级到小米 MiMo V2.6

- **背景**：小米于 2026-09-22 发布 **MiMo-V2.6 系列**（`mimo-v2.6-pro` / `mimo-v2.6-flash` /
  `mimo-v2.6-pro-ultraspeed`）；且 `mimo-v2.5`、`mimo-v2.5-pro` 将于 **2026-10-21 10:00（北京时间）下线**。
- **变更**：
  - 识图默认模型 `mimo-v2.5` → **`mimo-v2.6-flash`**（全模态、成本低，适合高频识图）；
  - Base URL 不变（Token Plan 中国节点 `https://token-plan-cn.xiaomimimo.com/v1`），
    配置项说明补充新加坡 / 欧洲节点；
  - **模型名自动降级**：配置的模型若因「模型不存在/已下线」报错，会依次尝试
    `mimo-v2.6-flash` → `mimo-v2.6-pro` → `mimo-v2.5`（非模型类错误如 401 仍立即抛出，不做无意义重试），
    避免官方迭代模型名后识图直接失效。
- **⚠️ 合规提醒**：小米官方文档写明 Token Plan 额度**仅限编程工具使用**，禁止用于自动化脚本 /
  自定义应用后端；在机器人里使用 Token Plan Key 属受限场景，建议改用标准按量计费 API 的 Key
  （README 已补充说明）。
