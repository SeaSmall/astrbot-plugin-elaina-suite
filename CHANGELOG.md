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

## v1.1.2

### 🐛 修复

- **日报图片 100% 发送失败（日志刷 `not a valid url`）**：`text_to_image(return_url=True)`
  在 AstrBot 网络渲染不可用/超时时会回退到**本地 PIL 渲染**，此时返回的是容器内文件路径
  （如 `/AstrBot/data/temp/io_temp_img_1792221389_8477ba.jpg`）而不是 URL。旧代码一律
  `MessageChain().url_image(...)`，平台侧 `Image.fromURL` 判为非法 → 图片全部失败后静默
  退回纯文本，`digest_also_send_md` 的「一份图片 + 一份 md」实际永远只到 md。
  新增模块级 `_image_chain()`，按 `http(s)://` / `file://` / `base64://` /
  `data:image;base64,` / 本地路径 分流构造消息链；`_send_image` 与系统健康报告同步修复。
- **主动消息「计划生成失败：无法解析 LLM 输出」，当天一条都不发**：`_parse_items` /
  `_parse_points` 只认严格 JSON 与半角 `HH:MM`。新增 `_loose_llm_text()`（剥 ```json 围栏、
  全角冒号/点号/中文「点」、全角括号、尾逗号）与 `_json_objects()`（整数组解析失败则逐个
  `{...}` 兜底），`_normalize_time()` 支持 `07：05` / `7.5` / `07点05`；行解析容错
  markdown 强调符与破折号；解析彻底为空时把原始输出前 300 字写进 warning 日志，便于定位。
- **国际新闻源 404**：`feeds_intl` 默认的 `chinadaily.com.cn/rss/world_rss.xml` 已 404，
  换成 `chinanews.com.cn/rss/world.xml`（实测 30 条）+ `cgtn.com/.../world.xml`（50 条）。

### ✅ 测试

- 新增第 [19] 组：`_image_chain` 四种输入分流（本地路径 / https / file:// / base64）、
  代码围栏 + 全角冒号 + 尾逗号 + 全角引号解析、带序号/加粗/反引号的行解析、
  `_normalize_time` 四种写法、`_parse_points` 宽松解析、国际源不含 404 域名。
  20 组冒烟全部通过（`ALL SMOKE TESTS PASSED`）。

### 🔧 其它

- 测试桩 `tests/stubs/astrbot/api/event/__init__.py` 的 `MessageChain` 补
  `file_image` / `base64_image`，与真实 AstrBot API 对齐。
- `metadata.yaml` 版本 → 1.1.2。

## v1.1.3（2026-10-17）

### 🐛 修复：同一天收到两遍简报

真机日志里同一天出现两轮发送（08:01:54 与 08:03:00 各一组「图片 + md」）。根因是防重靠
「读 KV `last_digest_date` → 生成（1~2 分钟）→ 写回 KV」，而 08:00 定时任务和每 5 分钟兜底检查
会**同时**触发，两个协程都能在写回之前通过检查。

- 新增 `self._digest_lock`（`asyncio.Lock`）：进入后再查一次 KV，真正挡住第二个协程；
- **先占位再生成**：进锁即把今天日期写入 KV，生成/发送抛异常时回滚，避免当天彻底不再补发；
- 新增 `force` 参数（手动 `/每日简报` 不受「今天已发」与时间窗限制）。

### ✨ 记忆库：改成「用户档案」而不是 AI 自述 + 跨会话去重

之前的记忆有三个问题：三个会话各一段并列（同一件事说三遍）、大量条目主语是助手自己
（「助手自称魔女」「助手会提醒喝水」）、一次性待办被当成长期事实。

- **重写 `MEMORY_PROMPT`**：明确「记什么」= 用户发生过的事 / 爱好与偏好 / 正在做的事与进度 /
  人际关系 / 情绪与状态 / 明确约定；明确「不记」= 助手自身的自称、语气、性格、做过什么、
  承诺过什么（这些由人格设定负责）、一次性指令、流水账；有日期的安排要带日期且超过 7 天不再保留；
  同一事实只写一次。
- **新增合并环节**：`refresh_memory` 在会话数 > 1 时调 `MEMORY_MERGE_PROMPT` 合并成**一份**去重档案，
  存 `memory_store["merged"]`；`_memory_text` 优先注入它（分会话明细仍保留作溯源）。
- **新增 `_filter_memory_lines`**：兜底剃掉主语是助手/AI 的行，并按「去标点后的正文」去重
  （跨会话重复行只留一条）。
- `/记忆` 显示改为先给「注入给 AI 的档案」，再折叠分会话明细。

### 🔧 其它

- 测试新增第 `[20]` 组：AI 自述过滤 / 跨会话去重 / merged 优先 / 并发只发一次 / 失败回滚可补发。
  `tests/test_merged.py` 20 组全过。
- `metadata.yaml` 版本 → 1.1.3。

## v1.1.4（2026-10-19）

### 🧠 记忆系统：修四个真机暴露的病因

真机上记忆表现为「用户明明说过做完了，AI 还一直问那件待办」，追下去是四条独立的问题：

**① 子代理看不到用户笔记。** 规划/写手代理的提示词只拼了 persona + KV 记忆摘要，而用户手写在
AstrBot 工作区里的 `user_profile.md`（形如 `<data>/workspaces/<会话>/user_profile.md`）从头到尾
没人读，所以用户在笔记里改的事实（「卡已领」）根本进不了上下文。

- 新增 `_collect_user_notes()` + `_workspaces_dir()` + `MEMORY_NOTES_TEMPLATE`：扫描
  `workspaces/*/user_profile.md`，去重后拼进记忆块**最前面**，并在提示词里标明「用户本人写的，
  优先级高于记忆摘要，冲突时以它为准」。
- 新增配置 `memory_notes_enabled`（默认开）、`memory_notes_chars`（默认 1200）。

**② 滚动摘要吃掉纠正。** 总结时只取「最近 20 条」，一句纠正很快滑出窗口；旧摘要里的
「待办：万事达卡」就被原样继承，之后每次刷新都继承。

- `_history_lines` 改为**按时间窗**取（`memory_window_hours`，默认 48 小时），时间从 AstrBot 注入在
  用户消息里的 `<system_reminder>Current datetime: …</system_reminder>` 解析，并在每行前标
  `[MM-DD HH:MM]`，让模型能判断「这条待办是哪天的」；同时剥掉 reminder 文本本身。
- `MEMORY_PROMPT` 增加「处理旧记忆里待办/未完成/计划」的规则：新对话说「领过了」就立刻删掉对应待办、
  用户笔记已写结果就以笔记为准、旧待办没被提起且不像长期目标也删掉。
- 新增配置 `memory_rewrite_stale`（默认开）：**没有新对话的会话，旧摘要也按新规则重写一次**
  （只整理不新增），否则历史遗留的脏条目永远清不掉。

**③ 记忆只在 03:00 刷新一次。** 白天发生的事最多滞后近 24 小时才可能进入主动消息。

- 新增 `_memory_refresh_due()` + `_memory_lock`：`_collect_memory` 在生成前按
  `memory_refresh_cooldown`（默认 30 分钟）冷却就地刷新一次，并做双检避免定时任务/手动/生成
  三处并发重复总结。

**④ 直接改数据库会被盖回去。** AstrBot 的 `preferences` 表在内存里有副本，直接改 sqlite 会被下一次
写回覆盖（删掉的记忆自己又回来了）。

- 新增 `/记忆重置`：走插件 KV 接口把 `memory_store` 写空，再按「近期对话 + 用户笔记」重建。
  洗历史脏记忆用这个，不要动数据库。

### 🔧 其它

- 测试新增第 `[21]` 组：时间窗过滤 + reminder 剥离 / 用户笔记读取 / 刷新冷却 / 笔记优先注入 /
  重置走 KV。`tests/test_merged.py` 21 组全过。
- `metadata.yaml` 版本 → 1.1.4。

## v1.1.5（2026-10-20）

### 🐛 修「记忆已经更新，主动消息还在问旧事」

**症状**：记忆库 03:00 就写明「万事达卡已领、不要再提」，白天的人格消息照样提醒去领卡。

**根因是计划与记忆的时间错位，三层叠加**：

1. **生成用的是历史快照**：今日计划是前一天 06:00 预生成的，用的还是当时的记忆，「待办：领卡」还没被纠正；
2. **派发阶段没有任何复核**：到点只把存好的文本发出去，从不回读记忆，记忆再新也追不上旧计划；
3. **记忆修复上线晚于计划生成**：注入 `user_profile.md` 笔记、30 分钟冷却刷新是 10-19 晚上才加上的，赶不上当天凌晨生成的那批计划。

**四处改动**：

- **计划写入记忆水位**：`_generate_and_store` 存计划时多记 `memory_updated`（生成时用的记忆版本）。
- **发送前逐条事实校验**：`_dispatch_due` 发现「当前记忆 ≠ 计划水位」就逐条调用 `_fact_check_message()`，
  拿最新记忆核对这条消息；冲突但可改写的就改写发送，前提完全垮掉的直接丢弃。
  LLM 调用失败时按原文发送——不能因为校验挂了整天不说话。
- **重建今日残留**：生成时点发现今日计划基于旧记忆，就按最新记忆重写「还没到点」的那些条目
  （`_regenerate_today_if_stale`，时间点保持不变，只改内容）。
- **生成挪到记忆刷新之后**：`gen_time` 默认 `0 6 * * *` → `20 3 * * *`，且生成前无条件先刷一次记忆
  （`memory_refresh_before_gen`）。顺序固定为「刷记忆 → 提升今日计划 → 重建残留 → 预生成明日」。

新增配置：`memory_refresh_before_gen` / `dispatch_fact_check` / `dispatch_regenerate_on_memory_change`。

### 🔧 其它

- 测试新增第 `[22]` 组：水位写入 / 冲突改写 / 冲突丢弃 / 不冲突原文发送 / LLM 异常降级 /
  重建今日残留且时间点不变 / `_on_gen_time` 调用顺序。`tests/test_merged.py` 22 组全过。
- `metadata.yaml` 版本 → 1.1.5。

## v1.1.6（2026-10-21）

### ✨ 主动消息改成「人格 + 世界观」驱动的自我分享

**问题**：之前的主动消息几乎全是「关心用户」——早饭吃了没、午休了吗、早点睡。一天下来像客服回访，
角色自己的经历、处境、世界观完全没有体现，人格设定也只剩一层语气。

**改动**：

- **新增 `world_setting`（世界观/场景设定）**：填角色所处的地方、身份、正在经历什么。填写后作为
  【世界观设定】块注入规划 / 写手 / 审校三个提示词，主动消息的分享内容围绕它展开；留空则只按人格设定生成。
- **新增 `self_share_ratio`（默认 70）**：自我分享 vs 关心用户的比例。规划代理据此把时间点分成
  「分享」与「关心」两类；关心类**至少保留 1 条**——要的是两者都有，不是把关心一刀切掉。
- **三个提示词重写**：规划代理要求要点落在「你自己正在经历什么」（在做什么、去了哪里、遇到谁、
  看到什么、心情如何），明确禁止「问候一下」「关心他吃饭」这类空泛指令；写手代理要求第一人称、
  有画面感，允许句末轻问但不要每条都反问；审校代理新增两条检查——分享/关心分布失衡就改写其中几条，
  出现「作为AI」「语言模型」「设定」「记忆」这类出戏词一律改掉。
- **兜底单次生成的 `message_prompt` 同步重写**，并修掉旧模板「你是{persona}」与 persona 自带
  「你是一个…」拼成「你是你是一个…」的问题。

### 🔧 其它

- 测试新增第 `[23]` 组：分享比例换算（含关心类下限）/ 世界观留空不产生空标题 / 四个提示词都带
  `{world_block}` 占位并能正常渲染。`tests/test_merged.py` 23 组全过。
- `metadata.yaml` 版本 → 1.1.6。

## v1.1.7（2026-10-25）

### 🐛 问题

- **简报图片全是乱码（中文全渲染成方框 □，英文数字正常）**：AstrBot 的 t2i 先走网络渲染
  （`t2i.soulter.top` 等），实测连续 `HTTP 503` / 连接超时后回退到**本地 PIL 渲染**；而 AstrBot
  官方 Docker 镜像里**没有任何 CJK 字体**（容器内 `fc-list :lang=zh` 为空，Debian 13 trixie），
  PIL 只能用内置位图字体，中文一律画成方框。
- **缺少「AI」「网安」两个板块**：原只有科技前沿，AI 进展与漏洞情报混在科技源里，经常被漏掉。
- **放宽时效窗口时当天条目被整体丢弃**：`_filter_yesterday(recent_days>0)` 的窗口末端取的是
  「今天 0 点」，早上 8 点跑的日报会把**当天 0 点之后发布的所有条目**全过滤掉，放宽窗口等于白放宽。

### ✨ 改动

- **新增板块「🤖 AI 界的新变化」**：默认源量子位 `https://www.qbitai.com/feed` + InfoQ 中文
  `https://www.infoq.cn/feed`（配置键 `feeds_ai` / 开关 `ai_enabled`），时效 `ai_recent_days`（默认 2 天）。
- **新增板块「🛡️ 网安漏洞与情报」**：默认源 FreeBuf `https://www.freebuf.com/feed`、安全客
  `https://api.anquanke.com/data/v1/rss`、先知社区 `https://xz.aliyun.com/feed`，外加 **CISA KEV
  已知被利用漏洞目录**（`cisa:kev`，配置文件里的特殊 token）；时效 `security_recent_days`（默认 2 天）。
  - KEV 单文件约 1.8MB（JSON），因此单独走 `security_kev_days`（默认 7 天，KEV 新增节奏不均匀，
    卡「昨日」会经常整块空白），并有 **6 小时下载缓存**；条目标题为
    `CVE-xxxx：厂商 产品 —— 漏洞名`，`knownRansomwareCampaignUse=known` 时摘要追加
    「⚠️ 已用于勒索软件」，链接指向 NVD 详情页。
  - 抓取失败只 warning 且返回空列表，绝不拖垮整份日报。
- **修复放宽窗口末端**：`recent_days>0` 时窗口末端改为「现在」，当天发布的条目能正常进入板块
  （政策前沿同样受益）。
- **中文字体自愈**：插件自带 GB2312 子集字体 `assets/fonts/digest_cjk.ttf`（6763 汉字 + 符号，
  约 2MB）。启动时与渲染图片前调用 `_ensure_cjk_font()`，目标 `<data>/font.ttf` 缺失才写入
  （**不覆盖**用户已有字体），写完打日志；字体资源缺失时静默跳过，不影响其它功能。这条同时修好了
  系统健康报告图片的中文乱码。
- **提示词同步**：`DIGEST_DEFAULT_PROMPT` 与 `_normalize_prompt` 的【强制要求】板块清单都加入
  新的两块，旧版 `llm_prompt` 配置无需手动改。

### 🔧 其它

- `_conf_schema.json` 101 → 110 键：`ai_enabled` / `security_enabled` / `feeds_ai` / `feeds_security` /
  `ai_recent_days` / `security_recent_days` / `security_kev_days`（`llm_prompt` 默认值同步）。
- 测试新增第 `[24]` 组：板块顺序与默认源、KEV 解析（7 天窗口 / 勒索标记 / NVD 链接 / 6 小时缓存 /
  窗口收窄重算 / 失败隔离）、`feeds_ai` 时效窗口、模板渲染新标题、schema 新键、
  字体自愈（缺失补 / 不覆盖 / 资源缺失静默）。`tests/test_merged.py` 24 组全过。
- `metadata.yaml` 版本 → 1.1.7。

## v1.1.8（2026-10-25）

### 问题
- 图片兜底渲染（网络 t2i 失败时走本地 PIL）中文已修复，但 emoji 仍显示为方框 □：自带的中文字体是 GB2312 子集，不含任何 emoji 字形。

### 改动
- `_sanitize_for_image` 增加 emoji 剔除：`_IMAGE_EMOJI_RE` 覆盖 `U+1F000–U+1FAFF`（🚀🤖💻💊🌤）、区域旗帜、ZWJ/变体选择符、`U+2600–U+27BF`（☀✅❌）、`U+2B00–U+2BFF`（⭐）、`U+2300–U+23FF`（⏳⏱）；同时收敛剔除后产生的连续空格。
- 网络渲染与本地兜底两条路径都会去掉 emoji，观感一致；**`.md` 文件不受影响，emoji 完整保留**。简报里的 emoji 只是板块装饰，标题文字本身还在。

### 其它
- tests 新增 `[24f]`（astral 字符 / 变体选择符 / ZWJ 全部剔除，正文文字保留）。
- `metadata.yaml` 1.1.7 → 1.1.8。

## v1.2.0（2026-10-25）

### 🎯 主动消息 v2（阶段一）：插件内部调度器 + 单会话决策循环

**问题**：旧实现是「每天 03:20 预生成明天 5-10 条 → 每分钟检查 → 到点发送」，有两个结构性缺陷：
1. **消息「一下子出来一大串」**：`_dispatch_due()` 会把 `missed_grace_minutes`（默认 30 分钟）窗口内所有过期条目在同一 tick 里逐条发出（间隔 0.3s），整轮结束才写回 KV；叠加 `_dispatch_due` 无锁、`cron_manager.add_basic_job` 不幂等（每次新 job_id）导致两套分钟任务并存，一次停机后可连发多条。复现：6 条计划 + 停机 23 分钟 → 单 tick 连发 6 条。
2. **内容与当下脱节**：正文在凌晨就生成好，早晨发出去的其实是「昨晚的情绪 + 昨晚的记忆」。

**改动**（新增 `living_*` 11 个配置项，默认关闭，关闭时行为与 v1.1.8 完全一致）：
- 插件内部 `AsyncIOScheduler`：`initialize()` 启动、`terminate()` 清 job 并 `shutdown(wait=False)`；开启后**完全不注册** AstrBot `cron_manager`（不依赖内置任务系统）。
- **每会话一个决策 job**（`proactive_decision_<sid>`，`replace_existing/coalesce/max_instances=1/misfire_grace_time=120`），决策完自己排下一次（自我延续），无外部节拍。
- **两阶段生成**：决策只产「意图标签 + 触发时间偏移」，到点的一次性 job 才读当下记忆/人格/世界观生成正文（不预生成）。
- **单槽 + token 幂等**：同一会话最多一个待触发；同一 token 重复触发只发 1 条；过期待触发（停机/休眠导致）**直接丢弃不补发**——从此不存在「补发批量」。
- 每会话一把 `asyncio.Lock` 覆盖「决策→生成→发送」；状态落 `<data>/proactive_state.json`（`os.replace` 原子写），重启恢复，损坏文件降级为空状态。
- 时间逻辑：`living_quiet_hours`（默认 23:30-07:30，支持跨午夜/off）内不触发，计划落在时段内自动顺延 +5~25 分钟；沉默下限 / 冷却 / 日上限 / 概率斜坡（`living_ramp_minutes`）四道门禁；时段词分八档注入提示词。
- 人格与世界观**固化为插件内置资源** `assets/persona.json`、`assets/worldview.json`（含 `tsundere.forbidden_meta_phrases`），运行时只读仓库内 JSON，不读服务器人格文件、不做同步/热更新。
- 最小硬约束：正文命中「才不是特意 / 我才没有 / 又不是为了你 / 别误会」或「作为 AI / 语言模型」直接丢弃本条。

**其它**：
- `_conf_schema.json` 110 → 121 键；`py tests/test_living.py` 12 组（含「100 次重复触发 = 1 条」「100 个周期 = 100 条」）；`py tests/test_merged.py` 24 组回归全过。
- 一整天模拟（含两次用户回话）验证：安静时段 0 条、相邻间隔均 ≥ 冷却、当日 5 条 ≤ 日上限。
- 文档：新增 `docs/PHASE1_REPORT.md`；README 补「主动消息 v2」说明与 11 个新配置键。
- 阶段零产物：`docs/PHASE0_RESEARCH.md`（Sentipolis/GWA/Mem0 论文与 API 核实）、`docs/PHASE0_CURRENT_STATE.md`（现网调度链路与爆发根因）、`docs/PHASE0_MEMO.md`（工程备忘录）、`docs/PHASE0_PERSONA.md`（人格改写说明）、`assets/persona.json`、`assets/worldview.json`、`assets/persona_source/`（原字节存档 + MANIFEST）。

## v1.2.1（2026-10-28）

### 🔍 主动消息 v2 可观测性 + 启动状态持久化

**问题**：
1. 开启 `living_enabled` 后，从外部（服务器日志 / 状态文件）看不出调度器是否真的在跑：启动时 `_living_start()` 只注册了 job 却**没有把下一次决策时间写回状态文件**，`proactive_state.json` 里 `next_decision_at` 一直是 0，看起来像「没排上」。
2. 没有在聊天里自查的入口，用户无法确认「下次什么时候会主动说话、今天已经说了几条」。

**改动**：
- `_living_start()` 在注册每个会话的决策 job 时，把算好的 `next_decision_at` 写回状态并落盘（与 job 实际触发时间一致，重启后恢复不漂移）。
- 新增 `/主动状态`（别名 `/主动消息状态`）命令：输出调度器是否运行、每个会话的「最后互动 / 待触发意图与时间 / 下次决策时间 / 今日已发条数 / 最近 5 条决策记录」。
- 关闭 `living_enabled` 时该命令明确提示「走旧版每日计划发送」，不会误导。

**其它**：`py tests/test_living.py` 12 组、`py tests/test_merged.py` 24 组全过。

## v1.3.0（2026-10-28）

### 🧠 长期记忆（阶段二）：接入 mem0 —— 写入 / 召回 / 种子记忆

**目标**：主动消息与对话不再只依赖「每天一次的记忆摘要」，而是有真正的长期记忆可写入、可召回；
按官方文档接入 mem0，不自己实现向量库与事实抽取。

**新增**：
- 新增核心 `LivingMemoryCore`（mem0 薄封装）：懒加载 + 进程内单例 + `asyncio.to_thread`；
  默认 `mem0_enabled=false`，关闭时行为与 v1.2.1 完全一致。
- **写入**：用户消息与助手回复配成一轮进缓冲，攒够 `mem0_flush_turns`（默认 4）轮后后台批量写入
  `user_id="u_<会话>"`；默认 `infer=False` 直接存原文（LLM 抽取单次约 13s 且可能写成英文）。
- **召回**：主动消息生成时用「最近几条用户发言」查询 mem0，命中结果作为【你记得的事】块拼进提示词，
  与原有 KV 记忆摘要并存（任一缺失都不影响）。
- **种子记忆**：`worldview.json` 的 34 条世界观事实在首次启动写入 `user_id="elaina_world"`，幂等不重复写。
- 新增 `/记忆状态`（别名 `/长期记忆状态`）命令：是否启用 / 是否就绪 / 种子是否已写 / 缓冲与后台任务数。
- `_conf_schema.json` 121 → **132** 键（新增 11 个 `mem0_*` 配置）。
- `requirements.txt` 新增依赖并钉版本：`numpy==1.26.4`（目标机 CPU 无 AVX，numpy 2.x 直接 SIGILL）、
  `mem0ai==2.2.1`、`fastembed==0.9.1`。

**约束与踩坑（已写进 docs/PHASE2_MEM0_PROBE.md / PHASE2_REPORT.md）**：
- mem0 2.2.1 的 `search`/`get_all` 只接受 `filters={"user_id": ...}`，传顶层 `user_id` 直接 `ValueError`；
- qdrant 嵌入式是**目录独占锁**，同进程第二个实例必炸 → 只能单例，`close()` 时先 `client.close()`；
- 数据路径固定落 `<data>/mem0_qdrant`、`<data>/mem0_history.db`、`<data>/fastembed_cache`（91MB 模型，避免重下）；
- 默认阈值从 mem0 的 0.1 提到 0.45（`mem0_threshold_pct`），否则无关记忆也会被召回。

**其它**：新增 `tests/test_memory.py`（9 组，注入假 mem0 模块跑契约）；`tests/test_living.py` 12 组、
`tests/test_merged.py` 24 组回归全过；文档新增 `docs/PHASE2_REPORT.md`。

## v1.3.1（2026-10-28）

### 问题
- 真机重启后 34 条世界观点种子记忆会被重复写入：`mem0` v2 的 `get_all()` 默认 `top_k=20`，
  而 `worldview.json` 有 34 条事实，判重集合只看到前 20 条 → 每次启动都补写剩下 14 条。

### 改动
- `LivingMemoryCore.seed_worldview()` 判重查询显式传 `top_k=1000`（不再依赖默认值）。
- `tests/test_memory.py` 新增 `[M10]`：34 条事实 + 34 条已存在 → 写入 0 条，且断言 `top_k>=1000`。
- `docs/PHASE2_REPORT.md` 补「真机召回验证」与 `get_all` 默认上限这条坑。

### 其它
- 真机验证：容器内复制 qdrant 目录后查询「魔法协会在哪里」top1 0.694（魔法统合协会）、
  「这个世界有互联网吗」top1 0.5716（没有手机网络电脑）、「我是谁」0.4661（自称我 / 本魔女），
  召回内容与世界观一致。

## v1.4.0（2026-10-28）

### 问题
- 角色没有「情绪」：同一句话在前一秒被夸、后一秒被骂，回复语气毫无变化；长时间没人说话也不会有一点低落。
- 主动消息的「此刻」只包含时间与时段的文字描述，不包含任何内部状态，内容自然不像一个「有心情的人」在说话。

### 改动
- 新增 `AffectCore`（「核心八」）：PAD 三维连续情感，按 Sentipolis 的双速动力学演化。
  快通道每轮更新（默认规则式，`affect_fast_mode=llm` 可换 LLM 评估）；
  慢通道在 poignancy 累计超过 `affect_reflect_poignancy`（默认 150）时做一次 reflection；
  衰减 `s(t+dt)=s(t)·2^(-dt/120min)`，每次读取按真实 Δt 补算。
- 新增**沉默评估**（对论文的显式扩展，原文没有 idle 机制）：`affect_silence` 定时检查「距上次交互 / 预期节奏」
  偏离度，超阈值时注入 `kind=silence` 的负面评估事件——走正常更新通道，不直接篡改 PAD；
  同时把衰减目标点按严重度拉向 `affect_silence_target`（默认 `-0.45,-0.10,-0.20`）。
- 情感注入生成：`LIVING_PROMPT_TEMPLATE` 增加 `{affect_block}`，内容是按 PAD 分档的「此刻心情」+ 行为提示
  （低愉悦低掌控 → 试探、留退路、别把话说满），不向用户宣布数值。
- 新增 `/情感状态`（别名 `/心情状态`）与 11 个 `affect_*` 配置键（默认全部关闭，行为与上一版一致）。

### 修复
- `_json_objects()` 原先只认「整段是数组」或平铺 `{...}`，**单个顶层对象且内含嵌套对象时会把内层对象当成结果**，
  慢通道的 appraisal / reflection 输出正好是这种形状 → 静默失效。改为先整体 `json.loads`（对象或数组），
  再按括号配对扫描（跳过字符串内括号、支持嵌套）兜底。
- `_persona_baseline()` 的字典别名回退写成 `(dim, dim+'leasure', 'pleasure')`，arousal / dominance 都会落到
  `pleasure` 上（真机基线 `0.15/0.15/0.15`，应为 `0.15/0.25/0.70`）→ 改为显式别名表。

### 其它
- 新增 `tests/test_affect.py`（12 组：衰减/快通道/慢通道/沉默评估/目标点偏移/分档描述/持久化/接线/配置/解析器/基线映射）；
  `test_merged.py` 24 组、`test_living.py` 12 组、`test_memory.py` 10 组回归全过。
- 新增 `docs/PHASE3_REPORT.md`：逐条对照规格、三处对论文的显式偏离及理由（沉默评估为新增、衰减目标点可偏移、
  PAD→文字用分档词表代替 KNN）、配置项表、测试证据、遗留项。
- 配置面板 132 → 143 项。

## v1.5.0（2026-10-28）— 阶段四：自主性（注意力选择 + 内在动机）

### 新增
- **注意力选择（GWA 式排他性瓶颈）**：新增 `MotivationCore`，把主动消息的候选意图放进一次竞争——
  每轮只有一个候选赢得生成焦点，落选者按 `motivation_loser_decay`（默认 0.85）降权记录、不删除，下次继续竞争；
  竞争结果（winner/分数/落选者）可在 `/动机状态` 与 `motivation_state.json` 里查。
- **内在动机 + 学习进度门控**：从用户消息里抽取「待接话题」，候选按 `progress = engaged/(attempts+1)`、
  `gate = clamp(0.35+1.25×progress, 0.2, 1.4)` 打分——反复发起却没回应的话题被压到 0.35 附近，
  得到回应的抬到 1.4 上限，避免「只追新不沉淀」。话题相似度用子串包含 + 2-gram 重叠（≥0.35）合并，
  不引入分词依赖。
- **情感门控耦合**：候选分数乘上 AffectCore 的情感系数（低落时 share 0.45、check_in 1.5；愉快时 share 1.25、
  check_in 0.7），对应 Shanahan 2006「affect 介导 action selection」。
- **具体由头注入**：选中的候选若挂到某条待接话题，`_living_compose` 的提示词会带上「具体到：xxx」，
  避免「追问」退化成空泛寒暄。
- 新增 `/动机状态`（别名 `/注意力状态`）与 8 个 `motivation_*` 配置键（默认全部关闭，行为与 v1.4.0 一致）。

### 其它
- 新增 `tests/test_motivation.py`（10 组：关闭空操作/话题合并/学习进度/情感门控/重加权/竞争/持久化/上限与数据面/接线/配置）；
  `test_merged.py` 24 组、`test_living.py` 12 组、`test_affect.py` 12 组、`test_memory.py` 10 组回归全过。
- `_living_plan_intent` 改为 async 以承载竞争选择；`_living_on_user_message` 增加文本参数用于话题抽取。
- 新增 `docs/PHASE4_REPORT.md`：候选池/评分公式/情感门控表/学习进度/配置/可观测性/测试证据/三处偏离/遗留。
- 配置面板 143 → 151 项。
