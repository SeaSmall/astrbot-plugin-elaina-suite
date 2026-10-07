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

## v1.0.3（2026-10-05）

### 🗑 移除：联网表情包搜索发送

- **背景**：在线表情包 API（`api.tangdouz.com` 关键词搜图 + `cn.apihz.cn` 的百度/搜狗表情包接口）
  搜出来的图经常跟对话不搭、甚至搜不到，使用体验是「那个不准」，按要求整条链路删除。
- **移除的代码**：`ElainaMemeCore` 里的 `_fetch_meme_images` / `_fetch_from_source` / `_pick_meme_url` /
  `_download_meme` / `_http_get_json` / `_http_get_bytes`，以及
  `on_message`（用户消息阶段掷骰）、`on_llm_request`（注入关键词提示词）、
  `_strip_meme_mark` / `_parse_keyword` / `_set_llm_text`；常量 `MEME_PROMPT_TEMPLATE` /
  `DEFAULT_TRIGGER_PROB` / `DEFAULT_MEME_KEYWORDS` / `DEFAULT_API_URL_TANGDOUZ` /
  `DEFAULT_API_URL_APIHZ_SOGOU` / `DEFAULT_API_URL_APIHZ_BAIDU`。
- **删除的配置项（9 个）**：`meme_source` / `trigger_prob` / `meme_prompt` / `api_url_tangdouz` /
  `api_url_apihz_sogou` / `api_url_apihz_baidu` / `apihz_id` / `apihz_key` / `meme_count`
  （配置面板 90 → 82 项）。
  > `trigger_prob` 若你还留着旧值，插件会把它当作 `meme_probability` 的**兼容回退**读取，不会静默失效。
- **钩子减少**：`event_message_type(ALL)` × 3 → × 2、`on_llm_request` × 3 → × 2；
  表情包仍走 `on_llm_response(priority=99999)`。

### ✨ 对齐原版：Elaina 表情包按 `astrbot_plugin_Elaina_meme_Bridge` 行为重做

- **触发时机**：从「用户消息阶段掷骰」改为「**AI 回复之后**掷骰」（原版行为），
  且判定上下文取 **AI 自己的回复文本**（为空才回退用户消息），选出的表情包因此更贴当前回复。
- **AI 选图**：把**对话内容 + 完整的表情包文件名列表**交给 AI，要求只回复文件名；
  概率键名改回原版的 **`meme_probability`**（默认 0.5），并做 0~1 钳制。
- **文件名对齐增强**：原版只认「完全相等」，AI 多回一个反引号/书名号、或带一句「我选 `无语.png`」，
  就会挑不中而随机兜底（表现为「发得不准」）。现在先剥掉引号/书名号再全等匹配，
  再退化为「回答里包含唯一文件名」，命中多个时取最长的那个，仍不中才随机兜底。
  日志会打印 `AI 选择 \`xxx\`` 或 `AI 选择 \`xxx\` 不在库中，改用随机一张`。
- **自带素材**：插件内置原版那 **50 个表情包**（`meme/` 目录，约 80 MB），装完即用，无需另装素材插件。
- **素材目录三顺位保留**：① 配置 `meme_dir` → ② 插件目录 `meme/` → ③ AstrBot `plugins/*/meme`，
  想换素材或复用已有素材都不用拷贝。
- **保留的安全网**：图片魔数/大小校验（`_is_valid_image`，默认 8 MB 上限）与 `send_timeout` 发送超时。
  （不再需要「最多换 3 张重试」——只发本地文件，不存在远端下载失败。）

### 🔧 其它

- `tests/test_merged.py`：第 12 组测试改为覆盖新行为（目录检测 / 文件名对齐 / 概率键名与钳制 /
  `on_llm_response` 端到端发送与总开关关闭），并去掉已删除的 `on_message` 调用。
- `.gitattributes`：新增 `meme/*.gif|jpg|jpeg|png|webp binary`，避免素材被当成文本做换行符转换。
- README / metadata.yaml 同步更新（配置项清单、钩子清单、FAQ、素材版权声明：
  50 个表情包来自 B 站「白之魔女-霜娜」，仅供个人学习、禁止商用）。
