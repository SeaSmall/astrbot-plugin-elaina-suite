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

## v1.1.0（2026-10-16）

本次把 `astrbot-plugin-daily-digest` v1.2.0 的日报改造同步进本合并插件，
保持「合并后功能与原插件逐行等价」。

### ✨ 政策前沿改接政府官方网站

- **背景**：原来的「政策前沿」用的是 `cctv:news`（央视要闻）+ `baidu:hot`（百度热搜），
  本质是新闻热榜，出现的内容经常与「政策文件」无关。
- **新增三个政府官网源**（默认全部启用）：
  - `gov:zuixin` —— 中国政府网「最新政策」`ZUIXINZHENGCE.json`（含中共中央/国务院最新政策文件）；
  - `ndrc:xwfb` —— 国家发改委「新闻发布」列表页；
  - `gov:yaowen` —— 中国政府网「要闻」`pushinfo.json`。
  > gov.cn 的政策栏目页面本身是 JS 空壳（条目由同目录 JSON 异步填充），
  > 所以直接抓 JSON，比解析 HTML 更稳也更快。
- **默认源变更**：`feeds_policy` 默认值由 `cctv:news\nbaidu:hot` 改为
  `gov:zuixin\nndrc:xwfb\ngov:yaowen`；`cctv:` / `baidu:hot` 语法仍然可用。
- **放宽时效窗口**：政策文件发布频率低（可能数日才有一条），硬卡「昨日」会让整个板块
  长期空白。新增配置项 `gov_policy_recent_days`（默认 30），政策板块按「最近 N 天」过滤；
  窗口内确实没有新政策时退回最新几条并记日志。其余板块仍严格卡「昨日」。
- 请求带上 `Referer: https://www.gov.cn/` 与 JSON Accept 头（`GOV_HEADERS`），
  避免被站点策略拦下；`_http_get_bytes` 相应增加 `headers` 形参。

### ✨ 图片与 .md 文件「两份都给」

- **背景**：原实现是「**二选一**」——图片发成功就 `return`，不再发 md；图片失败才回退 md。
  但图片便于手机阅读、md 便于保存/检索/二次加工，两者其实互补。
- **发送方式重做**：拆成 `_send_image()` 与 `_send_md()` 两个独立方法，
  `digest_send_mode` 扩为 `text` / `image` / `file` / `both` / `auto` 五档：
  - `both` 同时发图片与 md；`auto` 超过 `digest_long_threshold` 时按 `both` 处理；
  - 新增 `digest_also_send_md`（默认开）：只要发送方式包含图片，就**同时**补发一份 md；
  - **两者都失败**才回退纯文本（原来任一成功就结束）。
- 保留了原有的 90 秒单目标超时与「全目标失败回退」，避免媒体上传卡死导致日报整体丢失。

### 🐛 修复：渲染图片上出现「划去线」

- **根因**（AstrBot 两个渲染路径都会出错）：
  1. 本地 PIL 渲染器 `astrbot/core/utils/t2i/local_strategy.py` 的行内样式表里，
     `__(.*?)__` 被**同时**注册为 `BoldTextElement` 与 `UnderlineTextElement`
     （两条正则完全相同），匹配结果未做重叠去重 → 同一段文字被画两遍、多出一条线；
     且 `~~x~~` 会真的在文字中间画一条删除线。
  2. 网络渲染路径（`template/base.html` + `marked.js`）会把 `~~x~~` 渲染成 `<del>` 删除线。
- **修复**：新增 `_sanitize_for_image()`，在送 `text_to_image` 之前剥掉
  `~~` / `__` / `**` / `*` / `_` 这些内联标记（**只去标记、保留文字**）。
  日报是资讯文本，不需要删除线/下划线/斜体这些修饰。

### 🧪 测试

- `tests/test_merged.py` 新增第 **[17]** 组（并用第 [0] 组补充常量断言）：
  - 常量：`GOV_SOURCES` / `GOV_HEADERS` / `DEFAULT_FEEDS["feeds_policy"]` / `_sanitize_for_image`（含「只剥标记保留文字」断言）；
  - 源识别：`_is_json_source` 对三个 gov token 与 `cctv:news` 为真、对普通 RSS URL 为假；
  - `_fetch_gov_source` 三种解析（gov.cn JSON 的 `TITLE/URL/DOCRELPUBTIME`、pushinfo 的
    `title/link/pubDate`、发改委 HTML 的 `li/a/span`）与相对链接补全、Referer 下发；
  - 时效：`_filter_yesterday(items, 30)` 保留 20 天前条目，`(items, 0)` 丢弃；
  - 发送分流：`auto`(长文本) / `both` / `text` / 图片与 md 双失败回退文本，用方法替换统计调用次数。
- 全部 **19 组冒烟测试通过**（`ALL SMOKE TESTS PASSED`）。
- 联网实测（真实政府官网）：`gov:zuixin` 返回 10 条、`gov:yaowen` 10 条、`ndrc:xwfb` 9 条；
  `_fetch_category("feeds_policy", 5)` 端到端产出 5 条真实政策条目
  （如「2026年10月15日国内成品油价格不作调整」`https://www.ndrc.gov.cn/xwdt/xwfb/202610/t20261015_1408170.html`）。

### 🔧 其它

- `_send_long()` 被 `_send_image()` / `_send_md()` 取代，不再保留（避免两套发送逻辑并存）。
- `_conf_schema.json`：82 → **84** 项（新增 `digest_also_send_md`、`gov_policy_recent_days`，
  `feeds_policy` 默认值与描述更新，`digest_send_mode` 描述补 `both` 档）。
- README 同步更新（日报特性段、配置项清单与数量、政策/划线/截断三条 FAQ）；metadata 版本 → 1.1.0。

## v1.1.1（2026-10-16）

### ✨ 主动消息接入记忆库 + 子代理流水线

把 `astrbot-plugin-proactive-guard` v1.1.0 的两大能力**原样移植进整合包**。
此前这两项只做进了独立仓库，整合包里的 `ProactiveGuardCore` 仍是 v1.0.4 的行为——
这正是「`/记忆` 没反应、主动消息像陌生人打招呼」的原因。

- **记忆库**：新增 `MEMORY_KEY = "memory_store"`。默认每天 03:00（`memory_cron`）把最近
  `memory_turns`(20) 轮真实对话交给 AI，**滚动重写**成一份长期记忆（称呼 / 身份职业 /
  正在做的事 / 长期偏好 / 情绪状态 / 人际关系 / 约定），存进 KV；生成主动消息时注入提示词
  （`MEMORY_BLOCK_TEMPLATE` 明确要求「只是背景，不要直接复述、不要提及『记忆』『记录』这类词」）。
  - **三级降级**保证第一次也有记忆：记忆库有内容 → 直接用；空 → 就地 `refresh_memory()`
    总结一次；仍空 → 退回原始对话历史（由 `memory_raw_fallback` 控制）。
  - **闭环**：`record_to_history` 打开时，发出的主动消息会以 assistant 角色写回会话历史，
    第二天总结时又会被读进去——AI 因此记得自己昨天说过什么，不会天天重复同一句。
  - 新增指令 `/记忆`（查看记忆库）、`/刷新记忆`（立刻总结一次）。
- **子代理流水线**：从「一次调用写完全部」改为「规划 → 并行写手 → 审校」三段式。
  规划代理给每个时间点定一句话要点；每个时间点由**独立写手**成文（`asyncio.Semaphore`
  限流，`subagent_concurrency` 默认 3，`subagent_parallel=false` 时串行）；最后审校代理
  统一语气并去重（`subagent_review`），条数不符时 warning 并回退写手原稿。
  每个写手只背一个时间点的上下文，比单次生成更不容易串味。

### 🐛 修复

- **`_generate_and_store()` 里的「回退单次生成」此前是假的**（独立版同样存在）：
  子代理流水线失败时返回空列表，调用方直接 `if not items:` 报「计划生成失败」并 return——
  日志里那句「回退单次生成」和 README 的描述都没兑现。现在 `if not items:` 会**真的**再走一次
  单次调用，并带 `try/except`（LLM 全挂时只记 warning，不把异常抛给调度器）。两个仓库同步修复。
- 三段子代理提示词在独立版 v1.1.0 里写作 `你是{persona}`，而 `persona` 本身以「你是一个…」开头，
  会渲染成「你是你是一个温柔…」；移植采用已修正的 `{persona}` 版本
  （`GUARD_DEFAULT_PROMPT` 里的 `你是{persona}` 保持不变——它要的本来就是一个完整句子）。
- `_parse_items()` / `_parse_points()` 对 `None` 会抛
  `TypeError: expected string or bytes-like object, got 'NoneType'`；`_review_drafts()` 未防空；
  `_clean_message()` 未做类型检查——均已补上显式类型判断。

### ⚙️ 配置

- `_conf_schema.json`：84 → **93** 项，新增 9 个：
  `subagent_enabled` / `subagent_parallel` / `subagent_concurrency`(3) / `subagent_review` /
  `memory_enabled` / `memory_cron`(`0 3 * * *`) / `memory_turns`(20) / `memory_max_chars`(1500) /
  `memory_raw_fallback`。默认值与原独立插件完全一致。

### 🧪 测试

- `tests/test_merged.py` 新增第 **[18]** 组「记忆库 + 子代理流水线（含回退单次生成）」：
  常量与提示词占位符、9 个新配置键、`_history_lines` 多模态只抽文本、`refresh_memory` 首次总结、
  `_collect_memory` 读取、记忆注入子代理、规划失败**真的**回退单次调用、关闭态提示、
  `_truncate_memory` 上限。全部 **19 组**冒烟测试通过（`ALL SMOKE TESTS PASSED`）。
- 独立仓库侧 `pgtest_full.py` **107 项断言全部通过**。

### 🔧 其它

- README 同步（功能描述、指令清单、配置项数量 84→93 与键清单、
  新增「`/记忆` 显示没有记忆怎么办」FAQ）；metadata 版本 → 1.1.1。
