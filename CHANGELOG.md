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

## v1.6.0（2026-10-28）

### 问题
- 傲娇被写成了可以直接说出口的标签：人格块把 `negation_patterns` 里的句式原样塞给模型，
  结果模型学会了「才不是特意…」「我才没有…」这种替观众解读自己的元话语——傲娇太假，不是太强。
- 只在主动消息里做硬过滤，正常对话回复完全没有约束。

### 改动
- 人格块的傲娇段重写：不再给「嘴硬时可以这样写」的句式，只给三条**行为**路径
  （行动先行+借口收尾 / 毒舌包裹真实信息 / 关心以「泄露」出现）+ 铁律（不直接否认动机、不替对方解读自己）。
- 零容忍硬约束升级：`TSUNDERE_FORBIDDEN` 短语表 + `TSUNDERE_META_RE` 正则（覆盖「才不是…特意/为了/想」等变体），命中即丢弃并重生成。
- 新增傲娇审校代理 `_living_tsundere_review`：模型按三条铁律（零元话语 / 可追溯的关心 / 语气一致）判定，
  未通过用它的改写（改写仍要过静态检查），再不通过就丢弃这条。调用失败或结果解析失败时放行原文，不阻断发送。
- 情感耦合：`_tsundere_encrypt_level` 按 PAD 与沉默严重度分三档，情绪越负面越「加密」——
  不许直说难过、不许问「你是不是不想理我」，改用抱怨、挑刺、装作随口一问。
- 正常对话也覆盖：`@filter.on_llm_request(priority=99996)` 把三路径 + 铁律注入 system prompt（幂等标记，不重复注入）。

### 其它
- 新增 `tests/test_tsundere.py`（10 组：静态硬约束/人格块路径/加密档位/审校 PASS/审校改写/解析降级/开关/生成重试/配置与接线/对话注入）；
  `test_living.py` 11 组断言按新流程更新（生成提示词与审校提示词分开取样）；回归 merged 24 / living 12 / affect 12 / memory 10 / motivation 10 全过。
- 新增 `docs/PHASE5_REPORT.md`：诊断、三路径、硬约束、加密分档、审校、对话注入、偏离、测试证据。
- 配置面板 151 → 156 项（`tsundere_enabled` / `tsundere_review_enabled` / `tsundere_encrypt_negative` / `tsundere_max_retry` / `tsundere_chat_inject`）。

## v1.7.0（2026-10-28）

### 问题
- **情感从来没被下游读到**：四处调用点写 `getattr(core, "enabled", lambda: False)()`，
  而 `AffectCore` 用 `_on()` 表示启用 → 返回 False，情感门控、傲娇加密档位、记忆块情感上下文、
  召回情感调制**全部静默失效**。情感在更新、在落盘、命令也能看，但没有一个下游用它。
  之前的单元测试与真机验证都用带 `enabled()` 的 Fake 打桩，所以四个阶段都没暴露。
- Mem0 召回只按向量相似度排序：情绪低落时，最能说明处境的记忆（失眠、压力、吵架）
  未必比一条无关事实更靠前，等于"记得但不会用"。

### 改动
- 新增 `_core_enabled(core)`（`main.py:575`）：统一判断核心是否启用，依次尝试 `enabled()` 与 `_on()`，
  异常返回 False；四处调用点（`5216` / `5251` / `5782` / `6949`）改为调用它。
- 情感调制召回（`mem0_affect_recall` 默认开）：`_affect_context()` 把 PAD 与沉默严重度折成
  `negative = clamp(-p*1.6 + sev*0.6, 0, 1)`；`rerank_affect()` 给命中
  `MEMORY_NEGATIVE_HINTS`（情绪一致）与 `MEMORY_UNRESOLVED_HINTS`（未解决事项）的记忆加权；
  `recall_block()` 在 negative > 0.05 时先把候选扩到 `min(24, base_k*3)` 再收敛。
  首位有加成时打日志，便于观察。
- 三个新配置键：`mem0_affect_recall` / `mem0_affect_boost_pct`(18) / `mem0_unresolved_boost_pct`(12)。

### 其它
- `tests/test_affect.py` 新增 A13（低落 PAD 真的传到下游：share 系数 0.45、check_in 1.5、
  傲娇 2 档、`negative > 0.5`）、A14（`_core_enabled` 兼容两种写法 + 旧写法 0 残留）；
  `tests/test_memory.py` 新增 M11/M12/M13。回归 merged 24 / living 12 / affect 14 / memory 13 /
  motivation 10 / tsundere 10 全过。
- 新增 `docs/PHASE6_REPORT.md`：五项彻查的规格对照、真机数字（PAD 轨迹、候选池与 winner、
  召回重排分数）、僵尸功能表、设定分层表、偏离与遗留。
- 真机验证（1.7.0）：`sha256=8fc1bcd4…` 与本地一致、重启 0 Traceback、
  `[memory] 情感调制召回：负面强度=1.00 首位加成=0.180（情绪一致）`。
- 配置面板 156 → 159 项。

## v1.8.0（2026-10-28）

### 问题
- 「像不像伊蕾娜」完全依赖服务器上那份 persona 是否配置正确：人格文档在 AstrBot 的 personas 表里，
  插件既不内置也不注入，换机器 / 换会话（例如语音通道）就可能变成一个「没有身份」的助手。
- 语音通道（`webchat_phone`）与 QQ 主会话是两套人格上下文，回复语气需要靠插件自己保证稳定。

### 改动
- 新增 `ProactiveGuardCore.persona_chat_block()`（`main.py` 约 `5289`）：读插件自带的 `assets/persona.json`，
  压成 `【身份底稿：你是伊蕾娜（灰之魔女）】` 六段（身份 / 性格 / 说话 / 习惯 / 关系 / 铁律），
  按 `persona_inject_chars` 在行边界截断；开头写明「这一段是你的身份与说话方式，不要复述它，
  也不要提设定/提示词/记忆/角色」。
- 新增 `@filter.on_llm_request(priority=99996) tsundere_on_llm_request`：每次请求先注入人格底稿
  （幂等判据 `【身份底稿`）再注入傲娇写法（判据 `【傲娇怎么写`），**追加**到 `req.system_prompt` 末尾、
  不覆盖原有提示词，并打日志 `[persona] 已向对话 system prompt 注入插件人格（N 字）`。
- 两个新配置键：`persona_inject_enabled`(默认 true) / `persona_inject_chars`(默认 2400，600~6000)。

### 其它
- `tests/test_tsundere.py` 新增 T11（人格块六段齐全、含前 3 条特质、关闭返回空、
  `persona_inject_chars=600` 时截断到 ≤600 且仍以标题行开头）、T12（真实调用
  `ElainaSuitePlugin.tsundere_on_llm_request`：原文保留、人格索引在傲娇之前、二次调用各标记只出现一次、
  两个开关都关时 system prompt 原样）；`tests/test_tsundere.py` 仍 12 组全过。
- 真机：容器内实测人格块 **1140 字**；从本地语音通道连发 4 句，日志 4 次
  `[persona] 已向对话 system prompt 注入插件人格（1140 字）`，回复为傲娇短句（「被说就难受成这样？……过来，先吃饭」）。
- 配置面板 159 → 161 项。

## v1.10.0（2026-10-29）

### 🫀 情感全局化（阶段一：一个人只有一份心情）

- **状态存储改为全局单例**：`<data>/affect_state.json`（`{"sessions": {<sid>: ...}}`）→
  `<data>/global_affect.json`（单对象，无任何 session / user 分区键）。
- **迁移**：首次启动读旧文件，优先取 `shared_<shared_scope_id>`，否则取 `updated` 最大的会话，
  写成全局状态；旧文件 `os.replace` 为 `.migrated` 留档，日志
  `[affect] 已迁移旧情感状态为全局单例（来源 …，旧文件共 N 个会话）`。
- **读写路径全部去分区**：`_sid()` / 按会话的 `_locks` 删除，改成一把全局锁；
  `_entry()` / `get_state()` / `note_user()` / `silence_severity()` / `describe()` / `status()`
  均无会话参数（保留可选形参向后兼容）。
- **衰减与反思**：衰减按「距全局最后一次任意交互」的时间折算；慢通道反思基于全局累积 poignancy。
- **沉默评估改为全局单次评估**：新增 `affect_primary_user_id`（方案一，推荐）——填了就只认这个人的
  `last_user_at`，别的会话来聊天只更新 `last_any_at`、不重置沉默计时；留空退化为方案二「全局静默」。
  该配置支持逗号分隔多个标识（同一个人跨 QQ / webchat 渠道时不必二选一），命中任意一个即算主要对象。
  日志 `[affect] 沉默评估注入负面事件：全局 偏离度=…（基准=主要对象/全局静默）`。
- **注入读全局 PAD**：`_living_memory_block` / `_tsundere_encrypt_level` / `_living_compose` /
  `_affect_context` / 动机竞争读到的都是同一份心情；`/情感状态` 重写为全局展示。

### 🧠 记忆源归一（阶段二：Mem0 是唯一记忆源）

- **删除三套并行记忆里的两套半**：KV `memory_store`（AI 摘要 + 跨会话合并）、
  `workspaces/*/user_profile.md` 用户笔记、`_collect_raw_history` 裸历史回退、
  每日 03:00 的 `memory_cron` 总结任务（含 cron_manager / APScheduler / living 调度器三处注册）。
- **唯一 scope**：`_memory_scope_id(config)` = `shared_scope_id` 或固定 `owner`；`_uid()` 不再按会话派生，
  全插件只读写这一个 `user_id`（世界观观测也写进同一 scope，`metadata.kind="worldview"`）。
- **新增单一源接口**：`list_memories()` / `memory_text()` / `flush_now()` / `reset_scope()` /
  `migrate_scopes()` / `watermark()`，外加 `<data>/mem0_meta.json` 记录 `last_write_at` / `seeded_at` / `writes`。
- **旧 scope 自动合并**：启动时把散落在其它 `user_id` 下的 Mem0 条目并入唯一 scope（写入带
  `metadata.migrated_from`，再删旧 scope），日志 `[memory] 旧 scope 已合并：X → u_<scope>（N 条）`；
  开关 `mem0_migrate_legacy`（默认开，幂等）。
- **命令直查 Mem0**：`/记忆` 列出 `list_memories()`（worldview 标 ◆）；`/刷新记忆` → `flush_now()`；
  `/记忆重置` → `reset_scope()` + 重播世界观。
- **计划水位改指 Mem0**：`_memory_updated()` = Mem0 最后写入时间，"计划是否基于旧记忆"的判定不再看 KV。
- **配置瘦身**：删除 `memory_enabled` / `memory_cron` / `memory_turns` / `memory_max_chars` /
  `memory_raw_fallback` / `memory_window_hours` / `memory_refresh_cooldown` / `memory_rewrite_stale` /
  `memory_notes_enabled` / `memory_notes_chars` / `memory_refresh_before_gen`（11 项），
  新增 `mem0_migrate_legacy`；配置面板 164 → 154 项。

### ✅ 测试

- 新增 `tests/test_global.py`（G1–G8）：全局单例结构、跨会话读回、迁移优先级、方案一/方案二、
  单次沉默评估、无分区残留、钩子传 `sender_id`。
- `tests/test_memory.py` 重写 M3 / M5 / M14，新增 M15（水位 + 旧 scope 合并 + 重置）与
  M16（`/记忆` 直查 Mem0、旧链路物理移除）；`tests/test_merged.py` 的 [18] / [20a-c] / [21] / [22]
  改为「记忆源归一」回归。
- 全量：merged / living / affect / memory / motivation / tsundere / global 七套全过。

### ⚠️ 升级注意（破坏性变更）

- 旧 `affect_state.json` 会自动迁移并留档 `.migrated`；旧 KV `memory_store` 与
  `workspaces/*/user_profile.md` **不再被读取**（服务器侧已备份后清理）。
- 旧的按会话 Mem0 scope（`u_<umo>` / `u_shared_<id>` / `elaina_world`）会在首次启动时合并进唯一 scope，
  不需要手工迁移；合并后可重跑（幂等）。
- 若曾用 `/记忆重置` 或手改过 KV，请以 `/记忆` 输出为准（现在它就是 Mem0 的真实内容）。

## v1.9.0（2026-10-28）

### 问题
- 记忆与情感状态都按「会话 UMO」分桶：本机语音通道（`webchat_phone!<user>!main`）与 QQ 主会话
  是两份互不相通的记忆、两套 PAD。实测同一句话在语音里说完，QQ 会话的 PAD 仍是基线 0.15/0.25/0.70，
  记忆写入也落在 `u_webchat_...` 命名空间里 —— 等于"她在电话里认识的那个人"和"QQ 里认识的那个人"不是同一个。
- 语音会话因为用户说话被 `_living_on_user_message` 排上了主动消息决策，但该会话没有常驻监听方，
  一旦决策通过就会往没人收的会话发一条（占配额、无意义）。

### 改动
- 新增 `shared_scope_enabled`(默认 false) / `shared_scope_id`(默认空，空则用 shared) 两个配置键；
  开启后 `LivingMemoryCore._uid()` / `AffectCore._sid()` / `MotivationCore._sid()` 统一归一到
  `u_shared_<id>` 与 `shared_<id>`，QQ、语音、其他平台共用一份长期记忆与同一套情感 / 动机状态。
  **关闭时行为与 v1.8.0 完全一致**（按会话隔离），不影响多用户场景。
- `_living_decide()` 增加目标会话守卫：不在 `_collect_target_sessions()` 里的会话直接跳过，
  不再排「发了也没人收」的主动消息。
- 新增测试：M14（跨通道同一 user_id / 关闭隔离 / 空值回退 / 非法字符清洗）、
  A15（语音与 QQ 同一份 PAD）、N11（同一份话题与学习进度）、L13（非目标会话不排主动消息不写状态）。

### 其它
- 配置面板 161 → 163 项。
- 真机：语音通道实发 4 句确认人格注入 ×4（`[persona] ...（1140 字）`）、情感与动机均按会话更新；
  开启共享后两个通道读到的 PAD 与记忆命名空间一致。
- 服务器同步清理：删除 `skills/elaina-agent` 与 `skills/unclecheng-reduce-ai-perception-v2`
  （备份在 `<data>/_skill_bak_*`），并把人 `personas.elaina_v2_notools.skills` 中的这两项摘掉。

## v1.10.1（2026-10-29）

### 🧹 写入前提炼事实（`mem0_distill`，默认开启）

- 真机清理时发现 mem0 里 56 条记忆中有 **22 条是原始对话原句**（「<对话原句已脱敏>」…）——因为写入走 `infer=False`，mem0 不做任何归纳。
- 试过打开 mem0 自带的 `mem0_infer=true`，服务器上直接失败：`LLM extraction failed: Could not resolve authentication method. Expected one of api_key, auth_token, or credentials to be set.`（mem0 要一份独立的 openai 兼容 key，而 AstrBot 的 provider 是运行期注入的，插件侧拿不到可用的明文 key；另外默认模型 `mimo-v2.6-pro` 不在 Token Plan 已知列表）。
- 改为**插件自己提炼**：复用对话模型（`ctx.get_using_provider_async()` + `ctx.llm_generate`，失败回退 `get_using_provider().text_chat`），把每轮批量消息压成 1~3 条「关于用户的事实」（第三人称、每条 ≤40 字、最多 3 条、没有就输出 NONE），再逐条 `mem.add(..., infer=False, metadata={"distilled": True})`。
- 没有事实时整轮跳过并记日志，**不退回存原文**；提炼失败（无模型 / 调用异常）同样跳过，不影响聊天。
- 新增配置 `mem0_distill`（bool，默认 true，仅在 `mem0_infer` 关闭时生效）；配置面板 154 → **155** 项。

### 🧪 测试

- `tests/test_memory.py`：M6 改为显式 `mem0_distill=False`（验证「原样批量写入」旧路径仍可用）；新增 **M18** `m18_distill_facts`（正常提炼两条事实逐条写入且 `infer=False`/`metadata.distilled=True`、`llm_generate` 抛错回退 `text_chat`、`NONE` 时不写原句）；新增 `make_engine` 默认挂 `FakeCtx`（水位/迁移等用例依赖真实写入路径）。

### ⬆️ 升级注意

- 服务器配置需同时满足：`mem0_infer=false`、`mem0_distill=true`（若之前为排查打开过 `mem0_infer`，务必关掉）。

## v1.10.2（2026-10-29）

### 🔧 MiMo 2.5 全面下线：降级链与旧配置迁移

- 小米 `mimo-v2.5` / `mimo-v2.5-pro` 已于 2026-10-21 下线，`XIAOMI_MODEL_FALLBACKS` 里仍留着
  `mimo-v2.5` 作为兜底，命中时会再吃一次 404/400。
- 降级链改为纯 2.6：`mimo-v2.6-flash` → `mimo-v2.6-pro` → `mimo-v2.6-pro-ultraspeed`。
- 新增 `XIAOMI_RETIRED_MODELS` + `_normalize_xiaomi_model()`：配置里残留的旧名字在请求前自动映射
  （`mimo-v2.5` → `mimo-v2.6-flash`、`mimo-v2.5-pro` → `mimo-v2.6-pro`、
  `mimo-v2.5-pro-ultraspeed` → `mimo-v2.6-pro-ultraspeed`），并记一条
  `[image_bridge] 配置的 MiMo 模型 … 已下线，改用 …` 日志。
- `_conf_schema.json` 的 `xiaomi_model` 提示与 README 同步改写（不再写「将于下线」，改为「已下线 + 自动迁移」）。

### 🧪 测试

- `tests/test_merged.py` [15] 增加 15c：旧名映射三分支 + `XIAOMI_MODEL_FALLBACKS` 不含 2.5 +
  配置写 `mimo-v2.5` 时**实际请求体里的 model 为 `mimo-v2.6-flash`**。
