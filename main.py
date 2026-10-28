"""
astrbot_plugin_elaina_suite —— Elaina 工具箱（六合一合并插件）

由 SeaSmall 的 6 个独立插件合并而来（功能不变，互相兼容）：
1. 图片问答桥接（ImageBridgeCore）：图片按「小米 MiMo → 百度图像识别 → OCR.space」
   三级优先链识别，用户随后发送文字提问时把识别内容一并注入 LLM（图片门控问答）。
2. 每日简报（DailyDigestCore）：天气（多城市）+ 昨日国内/国际新闻 + 科技/AI/网安/医药/政策前沿、
   GitHub 日升榜，AI 总结后定时推送（含补发兜底与拦截降级）。
3. 主动消息门禁 + 每日人格消息（ProactiveGuardCore）：拦截非本插件的 AI 主动发言；
   每天后台预生成「明天」的 5-10 条随机时间点人格消息，到点自动发送。
4. Elaina 表情包（ElainaMemeCore）：本地表情库 + AI 选图 —— LLM 回复后按概率让 AI
   根据对话内容挑一张最合适的表情包发送（对齐原版 astrbot_plugin_Elaina_meme_Bridge，
   插件自带原版 50 个素材，也可指向自己的表情包目录）。
5. LLM 拦截兜底（LlmGuardCore）：拦截被泄漏到用户面前的 LLM 错误文案
   （content_filter / high risk 等），可换备用 provider 重发，并支持转存请求定位根因。
6. 系统健康报告（SystemHealthCore）：CPU/内存/磁盘/运行时长/进程 → 图片定时推送
   （psutil 为可选依赖，缺失时该核心自动禁用，不影响其它功能）。

说明：
- AstrBot 一个插件目录只注册一个 Star 类，因此本插件采用
  「单一 Star 类 + 六个功能核心」结构：ElainaSuitePlugin 持有 6 个核心实例并负责
  事件/指令/LLM 钩子路由，核心逻辑与原插件逐行一致。
- 配置键冲突处理（原插件 -> 合并后）：
  proactive_guard: enabled -> pg_enabled, timezone -> pg_timezone, target_sessions -> pg_target_sessions
  daily_digest   : timezone -> digest_timezone, target_sessions -> digest_target_sessions
  llm_guard      : 全部加 guard_ 前缀（enabled -> guard_enabled, mode -> guard_mode ...）
  system_health  : timezone -> health_timezone, target_sessions -> health_target_sessions,
                   show_disk -> health_show_disk, show_network -> health_show_network
  Elaina 表情包  : enabled -> meme_enabled（meme_dir / meme_probability 沿用原版键名；
                   合并前 meme_responder 时代的 trigger_prob 仍作为兼容回退读取）
- 主动消息门禁：合并插件内部主动发送（人格消息、日报、健康报告、表情包）通过
  门禁放行计数（owner._bypass_cnt）直接放行，其余插件/内置 Agent 的主动发送仍被拦截。

要求：AstrBot 4.x（>= 4.16.0）；psutil 可选（仅系统健康报告需要）
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import inspect
import json
import mimetypes
import os
import platform
import random
import re
import socket
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import File, Image, Plain
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api.star import Context, Star

try:
    from astrbot.api.event import MessageChain
except ImportError:  # 旧版本兼容
    from astrbot.api.message_components import MessageChain

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# 图片问答桥接：默认配置（用户可在 AstrBot WebUI 的插件配置弹窗中修改）
# ---------------------------------------------------------------------------
DEFAULT_OCR_API_URL = "https://api.ocr.space/parse/image"
DEFAULT_OCR_API_KEY = "helloworld"  # OCR.space 公开测试 key（免费、有限流）
DEFAULT_OCR_LANGUAGE = "chs"  # chs=简体中文 / eng=英文 / chinese_tra=繁体中文 ...
DEFAULT_OCR_TIMEOUT = 60  # 识别请求超时（秒）
DEFAULT_PENDING_TTL = 1800  # 图片识别内容有效期（秒），超时后需重新发送图片
DEFAULT_EMOJI_WAIT_PENDING = True  # 表情是否也参与「发送后等待提问」门控
DEFAULT_RECOGNITION_WAIT = 30  # 用户提问时等待图片识别完成的最长时间（秒）
# 小米 MiMo Token Plan（OpenAI 兼容；Key 格式 tp-xxxxx）
# 中国节点 https://token-plan-cn.xiaomimimo.com/v1；新加坡 token-plan-sgp / 欧洲 token-plan-ams
DEFAULT_XIAOMI_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
# MiMo-V2.6 系列（2026-09-22 发布）：mimo-v2.6-pro / mimo-v2.6-flash / mimo-v2.6-pro-ultraspeed
# 识图用 flash 性价比最高（全模态、支持图片理解）；mimo-v2.5 / mimo-v2.5-pro 将于
# 2026-10-21 下线，故默认改用 2.6。模型名不可用时会按 XIAOMI_MODEL_FALLBACKS 自动降级。
DEFAULT_XIAOMI_MODEL = "mimo-v2.6-flash"
XIAOMI_MODEL_FALLBACKS: tuple[str, ...] = (
    "mimo-v2.6-flash",
    "mimo-v2.6-pro",
    "mimo-v2.5",
)
# 百度智能云图像识别（通用物体和场景识别 advanced_general）
DEFAULT_BAIDU_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
DEFAULT_BAIDU_API_URL = "https://aip.baidubce.com/rest/2.0/image-classify/v2/advanced_general"
DEFAULT_PROMPT_TEMPLATE = (
    "<用户上传的图片识别内容>\n"
    "{image_content}\n"
    "</用户上传的图片识别内容>\n"
    "以上是用户刚上传图片的识别结果（文字 OCR / 图像识别描述），请结合该图片内容回答用户的问题。"
)

# QQ 官方平台表情标记：<faceType=...> 被 _parse_face_message 转成 [表情] 或 [表情:赞]
EMOJI_RE = re.compile(r"\[表情(?::([^\]]+))?\]")

# ---------------------------------------------------------------------------
# 每日简报：WMO 天气代码 -> 中文描述（Open-Meteo）
# ---------------------------------------------------------------------------
WMO_CODES = {
    0: "晴", 1: "基本晴朗", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨", 56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "强阵雨", 82: "暴雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "雷暴伴冰雹",
}

# 各板块默认数据源（全部免费、免 Key；可在插件配置中自定义）
# 支持两种条目：
#   - URL            ：RSS/Atom 源
#   - "cctv:频道"    ：央视新闻 JSONP（china/world/news 等）
#   - "tencent:hot"  ：腾讯新闻热榜 JSON
#   - "baidu:hot"    ：百度热搜 JSON
# 注：人民网官方 RSS 已于 2025 年停更（内容停留在 2025-06-05），默认不再使用；
#     若自行配置 RSS，务必确认源仍在更新（插件有严格时效过滤，陈旧条目不会显示）。
DEFAULT_FEEDS = {
    "feeds_cn": [
        "cctv:china",
        "tencent:hot",
        "baidu:hot",
    ],
    "feeds_intl": [
        "cctv:world",
        # chinadaily 的 world_rss.xml 已 404（实测），换成中国新闻网与 CGTN
        "https://www.chinanews.com.cn/rss/world.xml",
        "https://www.cgtn.com/subscribe/rss/section/world.xml",
    ],
    "feeds_tech": [
        "https://www.ithome.com/rss/",
        "https://sspai.com/feed",
        "https://www.ifanr.com/feed",
        "https://www.geekpark.net/rss",
    ],
    # AI 板块：量子位（更新频繁）+ InfoQ 中文（含 AI 栏目）
    # 实测不可用：机器之心 www.jiqizhixin.com/rss -> 302 跳 data-service；
    #             36kr.com/feed -> 返回 HTML 而不是 RSS
    "feeds_ai": [
        "https://www.qbitai.com/feed",
        "https://www.infoq.cn/feed",
    ],
    # 网安板块：FreeBuf + 安全客 + 先知社区（Atom）+ CISA KEV（结构化漏洞目录）
    # 实测不可用：www.secrss.com/rss -> 404；paper.seebug.org 需用 /rss（非 /rss/）
    "feeds_security": [
        "https://www.freebuf.com/feed",
        "https://api.anquanke.com/data/v1/rss",
        "https://xz.aliyun.com/feed",
        "cisa:kev",
    ],
    "feeds_medical": [
        "https://www.who.int/rss-feeds/news-english.xml",
        "https://www.nature.com/nm.rss",
    ],
    "feeds_policy": [
        "gov:zuixin",
        "ndrc:xwfb",
        "gov:yaowen",
    ],
}

# 板块顺序：(启用开关配置项, feed 配置项, 板块名, 图标)
SECTIONS = [
    ("news_cn_enabled", "feeds_cn", "昨日国内", "🇨🇳"),
    ("news_intl_enabled", "feeds_intl", "昨日国际", "🌍"),
    ("tech_enabled", "feeds_tech", "科技前沿", "💻"),
    ("ai_enabled", "feeds_ai", "AI 界的新变化", "🤖"),
    ("security_enabled", "feeds_security", "网安漏洞与情报", "🛡️"),
    ("medical_enabled", "feeds_medical", "医药前沿", "💊"),
    ("policy_enabled", "feeds_policy", "政策前沿", "📜"),
    ("github_trending_enabled", "github_trending", "GitHub 日升榜", "🚀"),
]

# GitHub 日升榜抓取尝试顺序（免费免 Key，依次降级，全部失败则省略板块）
GITHUB_TRENDING_SOURCES = (
    "github_search_created",
    "ghproxy_search_created",
    "github_search_alltime",
    "ghproxy_search_alltime",
    "ossinsight",
    "gitterapp",
)

# 政策板块：政府官方网站源（取代原来的央视要闻 + 百度热搜）
#   gov:zuixin  -> 中国政府网「最新政策」列表 JSON（含中共中央/国务院最新政策文件）
#   gov:yaowen  -> 中国政府网「要闻」推送 JSON
#   ndrc:xwfb   -> 国家发改委「新闻发布」列表页
# 说明：gov.cn 的政策栏目页面本身是 JS 空壳，条目由同目录下的 JSON 填充，
#      因此这里直接抓 JSON（ZUIXINZHENGCE.json / pushinfo.json），更稳更快。
GOV_SOURCES = ("gov:zuixin", "gov:yaowen", "ndrc:xwfb")

GOV_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.gov.cn/",
}

# 网安板块：CISA KEV（Known Exploited Vulnerabilities，已知被利用漏洞目录）
#   cisa:kev -> https://www.cisa.gov/.../known_exploited_vulnerabilities.json
# 结构：{"title": ..., "vulnerabilities": [{cveID, vendorProject, product,
#        vulnerabilityName, dateAdded, shortDescription, knownRansomwareCampaignUse, ...}]}
# 说明：KEV 是「已被实锤在野利用」的漏洞清单，收录节奏不均匀（有时几天没有新增），
#      且单文件约 1.8MB，所以两条特殊处理：窗口放宽到 security_kev_days 天 + 6 小时下载缓存。
KEV_SOURCES = ("cisa:kev",)

CISA_KEV_URL = (
    "https://www.cisa.gov/sites/default/files/feeds/"
    "known_exploited_vulnerabilities.json"
)

# 复用 gov 那套「带 Referer 的 JSON 请求头」风格，避免被 CISA 前置 WAF 当爬虫挡掉
CISA_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    "Referer": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
}

# KEV 下载缓存时长（秒）：1.8MB 的文件不该一天拉两次
KEV_CACHE_TTL_SECONDS = 6 * 3600

# 天气：每日 Open-Meteo 调用次数上限（保险，防止误触发限流）
WEATHER_DAILY_CALL_LIMIT = 120

DIGEST_DEFAULT_PROMPT = """你是一名严谨、简洁的中文每日简报编辑。今天是 {date}。
请根据下面的原始资讯生成【每日简报】，要求：
1. 按板块输出：🌤️ 天气、🇨🇳 昨日国内、🌍 昨日国际、💻 科技前沿、🤖 AI 界的新变化、🛡️ 网安漏洞与情报、💊 医药前沿、📜 政策前沿、🚀 GitHub 日升榜（有数据的板块必须全部输出，不要遗漏任何板块）
2. 每个板块先用 1-2 句话概括，再列 3-5 条要点（标题 + 一句话说明），重要条目附原文链接
3. 🚀 GitHub 日升榜的每个仓库要结合给出的描述，用一句话说明它「是什么、有什么功能」（如：XX —— 一个用于……的开源项目，提供……能力）
4. 客观简洁、不夸张、不编造；总长度控制在 1800 字内
原始资讯：
{data}"""

# ---------------------------------------------------------------------------
# 主动消息门禁：人格兜底与提示词
# ---------------------------------------------------------------------------
FALLBACK_PERSONA = (
    "你是一个温柔、自然、不油腻的朋友型 AI 陪伴者。说话像真实的朋友，"
    "简洁自然，会关心人但不过度肉麻，不滥用表情，不叫用户「亲」「宝」。"
)

GUARD_DEFAULT_PROMPT = """{persona}{world_block}
今天是{date}。请在下面 {count} 个时间点，各写一条你主动发给用户的简短消息（20~60字）。
要求：
1. 大部分（约 {share_ratio}%）写**你自己正在经历的事**：此刻在做什么、去了哪里、遇到谁、看到什么、心情如何；要具体、有画面感，符合上面的【世界观设定】（若已配置）与你的身份
2. 其余写**对用户的关心**：接着他最近在做的事（军训、作业、熬夜、身体不舒服…）具体问一句或提醒一句，不要「吃了吗」「多喝水」这类模板话
3. 每条贴合对应时间点（早晨/上午/中午/下午/傍晚/晚上/睡前），但不要写成定时问候模板
4. 每条内容必须不同，第一人称，自然口语化
5. 只输出一个 JSON 数组，格式：[{"time": "07:35", "text": "消息内容"}, ...]
6. 不要输出 JSON 以外的任何文字
时间点：{time_list}"""

WORLD_BLOCK_TEMPLATE = """
【世界观设定】（你在其中的世界、身份与所处环境；你的分享必须落在这个框架内，不要出现设定之外的概念）：
{world}"""

# ---- 记忆库 / 子代理流水线（与独立版 proactive-guard 同步）----
PLANNER_PROMPT = """{persona}{world_block}
今天是{date}。请为下面 {count} 个时间点各规划**一句话要点**，说明那一刻你要主动发给用户什么。
{memory_block}

核心：你是主动分享的一方，不是客服。要点要落在「你自己正在经历什么」上。
1. 其中约 {share_count} 条的要点必须是**你自己的经历与见闻**：此刻在做什么、去了哪里、遇到谁、看到什么、用了什么魔法、天气与心情、忽然想到的事。要具体到小事，不要写「问候一下」「关心他吃饭」这种空泛指令。
2. 其余约 {care_count} 条的要点是**对用户的关心或呼应**：基于记忆里他最近在做的事（军训、作业、熬夜、身体不舒服…）具体地问一句或提醒一句，不要模板话。
3. 所有要点都必须落在上面的【世界观设定】（若已配置）与人格身份之内，不要出现设定之外的现代概念（除非设定里就有）。
4. 若有「近期对话记忆」，关心类要点可以呼应它；分享类要点不要每条都从记忆起头。
5. 各时间点不要重复，一天下来像一条自然流动的线：分享与关心交替，别连着几条都在问同一件事。
6. 只输出一个 JSON 数组：[{{"time": "07:35", "point": "要点"}}, ...]
7. 不要输出 JSON 以外的任何文字
时间点：{time_list}"""

WRITER_PROMPT = """{persona}{world_block}
今天是{date}，现在是{time}。请用第一人称给用户写一条 20~60 字的主动消息。
{memory_block}
这条消息的要点：{point}

要求：
1. 要点若是「分享自己的经历」，就具体讲你此刻正在做的事、看到的东西、心里的感觉，让人有画面感；不要写成问候。
2. 要点若是「关心用户」，就问得具体、有来由（接着记忆里他正在忙的事），不要泛泛的「吃了吗」「多喝水」。
3. 符合上面的【世界观设定】（若已配置）与你的身份，不要出现设定外的概念；不要自称 AI，不要提及「设定」「记忆」「提示词」。
4. 允许句末自然带一句轻问，但不要每条都以问句结尾，更不要连珠炮式反问。
5. 只输出这条消息的正文，不要输出引号、不要输出时间、不要任何解释说明"""

REVIEWER_PROMPT = """{persona}{world_block}
下面是一天里要发给用户的一批消息草稿（JSON 数组），请你审校后输出最终版本。

审校要求：
1. 语气统一、自然，像一个真实的人在同一天里陆续发的，不要每条都像模板
2. 去掉重复、雷同、空泛的内容；同一件事不要反复提
3. 修掉与时间点场景不符的内容（如早晨发「晚安」）
4. 每条保持 20~60 字，不要加时间前缀，不要加编号
5. 条数、时间点必须与输入**完全一致**，不要增删
6. 检查「分享自己 vs 关心用户」的分布：如果几乎每条都在问用户吃饭睡觉、缺少分享自己经历的内容，就把其中几条改写成第一人称分享自己的见闻；如果一条关心都没有，把最贴合场景的一条改成具体的关心
7. 所有内容必须在【世界观设定】（若已配置）与人格之内；出现「作为AI」「语言模型」「设定」「记忆」这类出戏或元叙述的词，一律改掉
8. 只输出一个 JSON 数组：[{{"time": "07:35", "text": "消息内容"}}, ...]，不要输出 JSON 以外的任何文字

草稿：
{draft}"""


MEMORY_BLOCK_TEMPLATE = """
近期对话记忆（用户与你的真实聊天片段，按时间从早到晚）：
{memory}
（以上只是背景，不要直接复述、不要提及「记忆」「记录」这类词）"""

MEMORY_KEY = "memory_store"

# 用户自己写在 AstrBot 工作区里的笔记（workspaces/<会话>/user_profile.md）。
# 这份是「事实」而不是「AI 的滚动摘要」：用户改了笔记就该立刻生效。
MEMORY_NOTES_TEMPLATE = """
【用户笔记】用户本人写下的事实（优先级高于下面的记忆摘要，两者冲突时以笔记为准）：
{notes}"""

# AstrBot 会把当前时间塞进用户消息里：
# <system_reminder>Current datetime: 2026-10-13 20:33 (CST), Weekday: Tuesday</system_reminder>
# 记忆必须知道「哪句话是哪天说的」，否则旧待办会一直被当成仍然成立。
_TS_RE = re.compile(r"Current datetime:\s*(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})")
_SYS_REMINDER_RE = re.compile(r"<system_reminder>.*?</system_reminder>", re.S)

MEMORY_PROMPT = """你是一个记忆整理助手。你的任务是把对话里**关于这位用户这个人**的信息记下来，
写成一份随时能查的个人档案。

已有记忆（上一轮的总结，可能为空）：
{old}

用户笔记（用户本人写的，事实优先级最高；与记忆冲突时以它为准）：
{notes}

新的对话记录（从早到晚，每行前的 [日期 时间] 是这句话说出的时间）：
{raw}

要记什么（这是重点）：
1. **发生过的事**：用户做过什么、遇到什么、去了哪、事情进展到哪一步
2. **爱好与偏好**：喜欢/讨厌什么、口味、习惯、作息、喜欢的表达方式与称呼
3. **正在做的事**：项目、学业、考试、工作、行程、长期目标，以及各自的进度
4. **人际关系**：家人、朋友、同学、同事，用户怎么称呼他们、关系如何、发生过什么
5. **情绪与状态**：最近的心情、身体状态、压力来源
6. **明确约定**：用户和你说好要做的事、提过的计划

不要记什么：
- **不要写任何关于你自己（助手）的描述**：你的自称、口头禅、语气、性格、你做过什么、你承诺过什么。
  你的行为风格由人格设定负责，写进记忆只会和人格设定重复、互相打架
- 不要记「用户问了你一个问题」「你回答了」这类流水账
- 不要把「用户这一次让你做什么」当成长期事实（一次性指令不是档案）
- 不要记一次性琐事；有日期的事可以记但要带日期，超过 7 天的旧安排不再保留
- 不要编造对话里没出现过的信息

处理旧记忆里「待办 / 未完成 / 计划」这类条目的规则（最容易出错，务必遵守）：
- 如果新对话里出现了「已经做了 / 领过了 / 办完了」这类说法，立刻删掉对应待办，改成「已…（日期）」
- 如果用户笔记里已经写了结果，以笔记为准，旧的待办一律删掉
- 旧待办如果在新对话里完全没被提起，且看不出是长期目标，也删掉——不要假设它仍然成立
- 宁可少记，也不要把已经过期的事当成正在发生

写法：
- 第三人称，主语是「用户…」，短句分条，每行一条
- **同一件事只写一次**；旧记忆与新对话重复的合并成一条，不要两处都留
- 新旧信息冲突时以新的为准
- 整份档案不超过 {max_chars} 字；超了先删最不重要、最可能过期的条目
- 只输出记忆正文，不要标题、不要编号、不要解释你做了什么"""

MEMORY_MERGE_PROMPT = """下面是同一个用户在不同会话里留下的记忆摘要，内容有重复，也可能互相矛盾。
请把它们合并成**一份**去重后的个人档案。

{items}

合并要求：
1. 只保留关于**用户这个人**的信息：发生过的事、爱好与偏好、正在做的事、人际关系、情绪状态、明确约定
2. **删掉所有关于助手（你）自己的描述**：自称、语气、性格、做过什么、承诺过什么——这些由人格设定负责
3. 同一件事只留一条；不同会话说法冲突时，保留更新、更具体的那条
4. 有日期的安排保留日期，超过 7 天的旧安排删掉
5. 第三人称「用户…」，短句分条，每行一条，不超过 {max_chars} 字
6. 只输出记忆正文，不要标题、不要解释"""

# 记忆里出现这些开头的行 = 在描述助手自己，注入前直接丢掉（兜底，防止模型不听话）
_MEMORY_SELF_SUBJECTS = ("助手", "ai", "你", "机器人", "bot", "assistant", "模型")

# 发送前的事实复核：计划是提前生成的，记忆却随时可能更新，
# 于是会出现「记忆里写着卡已领、消息还在提醒去领卡」。这条提示词只做一件事：
# 拿最新记忆核对这一条待发消息，冲突就改写或丢弃。
FACT_CHECK_PROMPT = """你要在一条主动关心消息**发出去之前**做最后核对。

【最新记忆】
{memory}

【待发送的消息】
{message}

请判断这条消息是否与记忆矛盾，尤其是：
- 提醒用户去做记忆里已经**完成**的事（最典型：卡已经领了还在提醒去领）
- 描述与人设/记忆里的身份、地点、作息明显不符的状态
- 把已经过期的安排当成还没发生

只输出一个 JSON，不要任何解释：
{{"conflict": true/false, "reason": "一句话原因", "rewritten": "改写后的消息"}}

要求：
- conflict 为 false 时：rewritten 留空字符串
- conflict 为 true 时：如果这条消息改掉冲突部分后仍然值得发，就给出 rewritten（保持原本的语气、长度、口语风格，不要解释你改了什么）；如果整条消息的前提都不成立了，rewritten 留空字符串
- 不要输出 JSON 以外的任何字符"""

# ---------------------------------------------------------------------------
# Elaina 表情包：默认配置（对齐原版 astrbot_plugin_Elaina_meme_Bridge）
# ---------------------------------------------------------------------------
DEFAULT_MEME_PROBABILITY = 0.5  # 表情包发送概率（原版键名 meme_probability）
DEFAULT_SEND_TIMEOUT = 30  # 表情包发送超时（秒）：平台媒体上传可能重试较久，超时即跳过
DEFAULT_MAX_MEME_MB = 8  # 单个表情包文件大小上限（MB），超出跳过
MEME_EXTENSIONS = (".gif", ".jpg", ".jpeg", ".png", ".webp")  # 原版支持的素材后缀

# 图片格式魔数校验（防止把 HTML 错误页/防盗链响应当图片上传 -> QQ 官方接口拒收返回 None）
MAX_MEME_BYTES = 8 * 1024 * 1024  # 8MB 上限，超出跳过（QQ 官方媒体有大小限制）


def _is_valid_image(data: bytes, max_bytes: int = MAX_MEME_BYTES) -> bool:
    """校验下载内容是否为常见图片格式且大小合理。"""
    if not data or len(data) < 12 or len(data) > max_bytes:
        return False
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


def _local_name(tag: str) -> str:
    """取 XML 标签本地名（去掉命名空间）。"""
    return tag.rsplit("}", 1)[-1]


def _strip_html(text: str) -> str:
    """去除 HTML 标签并反转义实体，压缩空白。"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


_IMAGE_EMOJI_RE = re.compile(
    "["
    "🀀-🫿"   # 大多数 emoji（含 🚀🤖💻💊🌤）
    "🇦-🇿"   # 区域旗帜
    "‍️"            # ZWJ / 变体选择符
    "☀-➿"           # ☀✅❌ 等杂项符号
    "⬀-⯿"           # ⭐ 等
    "⌀-⏿"           # ⏳⏱ 等
    "]"
)


def _sanitize_for_image(text: str) -> str:
    """净化用于 t2i 渲染的文本，去掉会导致「划线」的内联 markdown 标记。

    背景（AstrBot 两个渲染路径都会出错）：
      1) 本地 PIL 渲染器 astrbot/core/utils/t2i/local_strategy.py 的行内样式表里
         `__(.*?)__` 被同时注册为 BoldTextElement 与 UnderlineTextElement（两条正则
         完全相同），匹配结果未做重叠去重，导致同一段文字被画两遍、多出一条线；
         且 `~~x~~` 会真的在文字中间画一条删除线。
      2) 网络渲染路径（template/base.html + marked.js）会把 `~~x~~` 渲染成 <del> 删除线。
    日报是资讯文本，不需要删除线/下划线/斜体这些修饰，直接剥掉标记最稳。
    """
    if not text:
        return ""
    # 删除线：~~文字~~ -> 文字
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    # 下划线 / 粗体：__文字__ -> 文字
    text = re.sub(r"__(.+?)__", r"\1", text)
    # 粗体：**文字** -> 文字
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    # 斜体：*文字* -> 文字（先处理粗体，避免误伤）
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    # 单下划线斜体：_文字_ -> 文字
    text = re.sub(r"(?<![A-Za-z0-9_])_(.+?)_(?![A-Za-z0-9_])", r"\1", text)
    # emoji：本地兜底字体（GB2312 子集）不含 emoji 字形，会画成方框 □。
    # 简报里的 emoji 只是板块装饰（标题里已有文字），直接去掉最稳；
    # 网络渲染路径一并去掉，保证两条路径观感一致。.md 文件不受影响。
    text = _IMAGE_EMOJI_RE.sub("", text)
    text = re.sub(r"[ 	]{2,}", " ", text)
    return text


# ---------------------------------------------------------------------------
# 中文字体自愈（图片渲染乱码）
# ---------------------------------------------------------------------------
# 背景：AstrBot 的 t2i 先走网络渲染（t2i.soulter.top 等），失败/503 时回退到
# 本地 PIL 渲染。而 AstrBot 官方 Docker 镜像里**没有任何 CJK 字体**
# （fc-list :lang=zh 为空），PIL 只能用内置位图字体，中文全部渲染成方框
# （□），表现为「简报图片全是乱码、英文数字正常」。
# AstrBot 的 FontManager.get_font() 会**优先加载 <data>/font.ttf**，因此这里
# 自带一份 GB2312 子集字体（约 2MB），缺失时补到 data 目录即可，无外部依赖。
# 已存在则不动 —— 尊重用户自己放的字体。
CJK_FONT_ASSET = os.path.join("assets", "fonts", "digest_cjk.ttf")


def _plugin_data_dir() -> str | None:
    """从插件目录反推 AstrBot data 目录：<data>/plugins/<plugin> -> <data>。"""
    try:
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.dirname(os.path.dirname(plugin_dir))
        if os.path.isdir(os.path.abspath(plugin_dir)):
            return data_dir
    except Exception:
        pass
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        return str(get_astrbot_data_path())
    except Exception:
        return None


def _ensure_cjk_font() -> str | None:
    """确保本地 t2i 渲染器有中文字体可用；返回字体路径（失败返回 None）。

    只在目标字体缺失时写入，不覆盖用户已有字体；任何异常都只 warning 不抛。
    """
    try:
        data_dir = _plugin_data_dir()
        if not data_dir:
            return None
        target = os.path.join(data_dir, "font.ttf")
        if os.path.exists(target):
            return target
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        src = os.path.join(plugin_dir, CJK_FONT_ASSET)
        if not os.path.exists(src):
            logger.warning("[font] 自带中文字体缺失，图片中文可能显示为方框: %s", src)
            return None
        os.makedirs(data_dir, exist_ok=True)
        tmp = target + ".part"
        with open(src, "rb") as fsrc:
            data = fsrc.read()
        with open(tmp, "wb") as fdst:
            fdst.write(data)
        os.replace(tmp, target)
        logger.info(f"[font] 已补充中文字体到 {target}（{len(data)} 字节），修复图片中文乱码")
        return target
    except Exception as e:
        logger.warning(f"[font] 补充中文字体失败（图片中文可能乱码）: {e}")
        return None


# ===========================================================================
# 插件内置人格资源（阶段一B）
# 开发期把服务器上的人格设定文档（含世界观）一次性改写为脚本同级的
# assets/persona.json 与 assets/worldview.json；运行期只读插件自带文件：
# 不读服务器原文档、不做哈希比对、不做热更新。要改人格走开发流程发新版本。
# ===========================================================================
PERSONA_ASSET = os.path.join("assets", "persona.json")
WORLDVIEW_ASSET = os.path.join("assets", "worldview.json")
_ASSET_CACHE: dict[str, dict] = {}


def _load_plugin_json_asset(rel_path: str) -> dict:
    """读插件目录下的 JSON 资源（按 mtime 缓存）；任何异常都返回 {}，绝不抛出。"""
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(plugin_dir, rel_path)
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        logger.warning(f"[living] 人格资源缺失: {path}")
        return {}
    cached = _ASSET_CACHE.get(rel_path)
    if cached and cached.get("mtime") == mtime:
        return cached.get("data") or {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("顶层不是对象")
        _ASSET_CACHE[rel_path] = {"mtime": mtime, "data": data}
        return data
    except Exception as e:
        logger.warning(f"[living] 读取人格资源失败 {path}: {e}")
        return {}


def load_persona_asset() -> dict:
    return _load_plugin_json_asset(PERSONA_ASSET)


def load_worldview_asset() -> dict:
    return _load_plugin_json_asset(WORLDVIEW_ASSET)


# 主动消息正文的生成模板（阶段一）。{xxx} 一律用 str.replace 填充，不用 format。
LIVING_PROMPT_TEMPLATE = """{persona_block}
{world_block}
{memory_block}
【此刻】
现在：{now}（{period}）
距离上次互动：{silence}
本次开口的意图：{intent_label}{reason_block}
【要求】
- 只输出你要发给对方的那一条消息正文：不要解释、不要前缀、不要引号、不要多条。
- 内容必须落在上面的人格与世界观之内；不自称 AI／模型／助手，不提「设定」「记忆」「提示词」。
- 长度遵守人格里的铁律（默认 25 字以内、最多两句；例外也不超过 50 字）。
- 傲娇按人格里的机制表达：不解释自己在傲娇，不出现被禁止的元话语。
- 现在是{period}，已经{silence}没聊了，语气要和这个时间与这份沉默相称。
"""


def _image_chain(render_result) -> MessageChain:
    """把 text_to_image(return_url=True) 的返回值转成可发送的图片消息链。

    AstrBot 的 render_t2i 会先走网络渲染（成功时返回 https URL），
    网络不可用或超时时回退到本地 PIL 渲染，此时返回的是**容器内的文件路径**
    （例如 /AstrBot/data/temp/io_temp_img_1792221389_8477ba.jpg）。
    旧代码一律用 MessageChain().url_image(...)，本地路径会被 Image.fromURL
    判定为非法 URL 并抛 "not a valid url"，导致日报/健康报告的图片
    100% 发送失败、静默退回纯文本。
    """
    if render_result is None:
        raise ValueError("渲染结果为空")
    r = str(render_result).strip()
    if not r:
        raise ValueError("渲染结果为空")
    low = r.lower()
    if low.startswith(("http://", "https://")):
        return MessageChain().url_image(r)
    if low.startswith("file://"):
        r = urllib.parse.unquote(r[len("file://"):])
        return MessageChain().file_image(r)
    if low.startswith("base64://"):
        return MessageChain().base64_image(r[len("base64://"):])
    if low.startswith("data:image") and ";base64," in r:
        return MessageChain().base64_image(r.split(";base64,", 1)[1])
    if os.path.isfile(r):
        return MessageChain().file_image(r)
    # 兜底：交给 AstrBot 自己判断（可能是不带协议的 URL）
    return MessageChain().url_image(r)


def _loose_llm_text(text, for_json: bool = False) -> str:
    """把 LLM 输出里常见的非标准写法拉回可解析形态。

    实测踩过的雷（NAS 上 00:00 的计划生成直接失败）：
      * 输出被 ```json 代码围栏包住；
      * 时间写成全角冒号「07：35」或「07.35」；
      * JSON 里用了全角引号/花括号/方括号；
      * 数组末尾多一个逗号（json.loads 直接抛）。
    这里只做「结构层」归一，正文措辞一个字都不改（引号归一仅在 for_json 时做）。
    """
    if not text:
        return ""
    t = str(text).replace("\ufeff", "")
    # 去掉 ```json / ``` 围栏
    t = re.sub(r"```[A-Za-z0-9_+-]*", "", t)
    # 时间里的全角冒号 / 点号 / 中点：07：35 -> 07:35
    t = re.sub(r"(\d{1,2})\s*[：﹕．·]\s*(\d{2})", r"\1:\2", t)
    # JSON 结构用的全角括号
    t = (
        t.replace("｛", "{")
        .replace("｝", "}")
        .replace("［", "[")
        .replace("］", "]")
    )
    # 尾逗号：{"a":1,} / [1,2,]
    t = re.sub(r",\s*([}\]])", r"\1", t)
    if for_json:
        t = (
            t.replace("“", '"')
            .replace("”", '"')
            .replace("‘", "'")
            .replace("’", "'")
        )
    return t


def _json_objects(text: str) -> list[dict]:
    """从文本里尽力捞出 JSON 对象：先整数组，再逐个 {...} 兜底。"""
    out: list[dict] = []
    m = re.search(r"\[.*\]", text, re.S)
    if m:
        try:
            arr = json.loads(m.group(0))
        except Exception:
            arr = None
        if isinstance(arr, list):
            out.extend(o for o in arr if isinstance(o, dict))
            return out
        for chunk in re.findall(r"\{[^{}]*\}", m.group(0), re.S):
            try:
                obj = json.loads(chunk)
            except Exception:
                continue
            if isinstance(obj, dict):
                out.append(obj)
        if out:
            return out
    for chunk in re.findall(r"\{[^{}]*\}", text, re.S):
        try:
            obj = json.loads(chunk)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _parse_date(text: str) -> datetime | None:
    """解析 RSS(RFC822)/Atom(ISO8601) 日期。"""
    if not text:
        return None
    text = text.strip()
    try:
        return parsedate_to_datetime(text)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


class _PluginCoreBase:
    """四个功能核心的公共基类。

    持有 owner（ElainaSuitePlugin 实例），并把 Star 提供的能力
    （context / config / KV 存储 / text_to_image 等）转发给核心，
    使各核心代码与原独立插件逐行保持一致。
    """

    def __init__(self, owner: "ElainaSuitePlugin") -> None:
        self.owner = owner

    @property
    def context(self):
        return self.owner.context

    @property
    def config(self):
        return self.owner.config

    async def get_kv_data(self, key: str, default=None):
        return await self.owner.get_kv_data(key, default)

    async def put_kv_data(self, key: str, value) -> None:
        await self.owner.put_kv_data(key, value)

    async def text_to_image(self, text: str, return_url=True):
        return await self.owner.text_to_image(text, return_url=return_url)

    def __getattr__(self, name):
        # 其他 Star 能力（如 delete_kv_data 等）按需转发给 owner
        return getattr(self.owner, name)

# ===========================================================================
# 核心一：图片问答桥接（原 astrbot_plugin_image_bridge.ImageBridgePlugin）
# 图片先识别（小米 MiMo → 百度图像识别 → OCR.space 三级优先链），
# 用户随后发文字提问时把识别内容一并注入 LLM（图片门控问答）。
# ===========================================================================
class ImageBridgeCore(_PluginCoreBase):
    """图片问答桥接插件：图片先识别，文字提问后 AI 一并作答。"""

    def __init__(self, owner: "ElainaSuitePlugin") -> None:
        super().__init__(owner)
        # 挂起的图片识别内容：{key: {"text": str | None, "ts": float, "event": asyncio.Event}}
        # text 为 None 表示识别仍在进行（收到图片即占位挂起，识别完成后填充）；
        # event 用于让 on_llm_request 等待识别完成，避免用户提问过快时漏注入
        # key = 会话 + 发送者，避免群聊中 A 的图片被 B 的问题消费
        self._pending: dict[str, dict] = {}
        # 后台识别任务集合（防止被 GC，完成后自动移除）
        self._tasks: set = set()
        # 百度 access_token 缓存：{"token": str, "exp": float}
        self._baidu_token: dict | None = None

    # ------------------------------------------------------------------ 工具
    def _cfg(self, key: str, default):
        """读取插件配置，异常时回退默认值。"""
        try:
            return self.config.get(key, default)
        except Exception:
            return default

    def _pending_key(self, event: AstrMessageEvent) -> str:
        """构造挂起内容的键：同一会话内按发送者区分。"""
        return f"{event.unified_msg_origin}:::{event.get_sender_id()}"

    def _gate_event(self, event: AstrMessageEvent) -> None:
        """静默拦截消息：禁止默认 LLM 请求并停止事件传播（双重保险）。

        - `stop_event()`：停止事件继续传播；
        - `should_call_llm(True)`：AstrBot 官方「禁止默认 LLM 请求」接口，
          在事件管道中显式跳过 LLM 调用，避免不同版本管道行为差异导致
          纯图片消息仍被 LLM 处理（AI 直接回复）。
        """
        event.stop_event()
        should_call_llm = getattr(event, "should_call_llm", None)
        if callable(should_call_llm):
            try:
                should_call_llm(True)
            except Exception as e:
                logger.debug(f"[image_bridge] should_call_llm 调用失败: {e}")
        logger.info(
            f"[image_bridge] 已拦截消息等待提问 (session={event.unified_msg_origin})"
        )

    @staticmethod
    def _extract_emoji_desc(text: str) -> str | None:
        """从消息文本提取 QQ 表情语义描述（AI 可理解），无表情标记返回 None。

        - `[表情:赞]` -> "用户发送了一个表情：[赞]"
        - `[表情]`（自定义表情包，无具体名）-> "用户发送了一个表情包（内容见下方 OCR 识别文字）"
        """
        m = EMOJI_RE.search(text or "")
        if not m:
            return None
        name = (m.group(1) or "").strip()
        if name:
            return f"用户发送了一个表情：[{name}]"
        return "用户发送了一个表情包（具体内容见下方 OCR 识别文字）"

    @staticmethod
    def _is_emoji_image(comp, text: str) -> bool:
        """判断图片组件是否疑似平台表情图（如 QQ 官方把表情解析为 [表情] 文本 + 图片附件）。

        命中条件（任一）：
        - 消息文本含 QQ 表情标记 `[表情`（`_parse_face_message` 输出，如 `[表情]`、`[表情:赞]`）；
        - 图片 url/file 带常见表情特征（emoticon / sticker / qqface / face / emoji / biaoqing 等）。
        """
        if "[表情" in (text or ""):
            return True
        url = str(getattr(comp, "url", "") or getattr(comp, "file", "") or "").lower()
        for kw in ("emoticon", "sticker", "qqface", "face/", "face_", "emoji", "biaoqing", "emotion"):
            if kw in url:
                return True
        return False

    def _extract_images(self, event: AstrMessageEvent, text: str = "") -> list:
        """从消息链中提取所有 Image 组件。

        表情图（QQ 官方把表情解析为 [表情] 文本 + 图片附件）**不跳过**——
        表情包 gif 会切帧后进 OCR，让 AI 识别出表情包上的文字。
        """
        components = getattr(event.message_obj, "message", None) or []
        images = []
        for comp in components:
            ctype = getattr(comp, "type", None)
            if ctype == "image" or type(comp).__name__ == "Image":
                if self._is_emoji_image(comp, text):
                    logger.debug("[image_bridge] 表情图进入切帧 OCR（识别表情包文字）")
                images.append(comp)
        return images

    @staticmethod
    def _sniff_image_ext(data: bytes) -> str:
        """根据文件头判断图片扩展名，供无扩展名的缓存文件使用。"""
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "png"
        if data[:3] == b"\xff\xd8\xff":
            return "jpg"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "webp"
        if data[:2] == b"BM":
            return "bmp"
        return ""

    @staticmethod
    def _gif_to_static_frame(file_path: str) -> str:
        """GIF 动图取第一帧转为 JPEG 静态图（供 OCR 识别）。

        表情包多为 gif 动图，OCR 无法直接识别动图；切第一帧后即可识别
        表情包上的文字（参考 AstrBot 生态 smart_imagechat_hub 的做法）。
        返回临时 JPEG 路径；失败时返回原路径。
        """
        try:
            from PIL import Image as PILImage
        except ImportError:
            logger.warning("[image_bridge] 未安装 Pillow，gif 切帧不可用（pip install Pillow）")
            return file_path
        try:
            with PILImage.open(file_path) as img:
                img.seek(0)  # 第一帧
                frame = img.convert("RGB")
                tmp = Path(tempfile.gettempdir()) / f"image_bridge_frame_{uuid.uuid4().hex}.jpg"
                frame.save(tmp, "JPEG", quality=92)
                frame.close()
                logger.debug(f"[image_bridge] gif 已切帧: {file_path} -> {tmp}")
                return str(tmp)
        except Exception as e:
            logger.warning(f"[image_bridge] gif 切帧失败，使用原图: {e}")
            return file_path

    async def _recognize_image(self, data: bytes, ext: str) -> str:
        """按优先级链识别单张图片字节：小米 MiMo → 百度图像识别 → OCR.space。

        - 配置了对应 key 的服务才启用：小米填了 xiaomi_api_key 才启用，
          百度填了 baidu_api_key + baidu_secret_key 才启用；OCR.space 兜底。
        - 某服务返回异常（抛错）自动降级到下一个；全部失败才报错。
        - 图片字节已在事件生命周期内读取完毕（gif 已切帧），
          后台识别不再依赖平台临时文件（事件结束后会被 AstrBot 清理）。
        """
        mime, _ = mimetypes.guess_type(f"image.{ext}") if ext else (None, None)
        mime = mime or "application/octet-stream"

        # 三级优先链（小米 → 百度 → OCR.space）
        providers: list[tuple[str, object]] = []
        if self._cfg("xiaomi_api_key", ""):
            providers.append(("小米 MiMo", self._recognize_xiaomi))
        if self._cfg("baidu_api_key", "") and self._cfg("baidu_secret_key", ""):
            providers.append(("百度图像识别", self._recognize_baidu))
        providers.append(("OCR.space", self._recognize_ocr))

        errors: list[str] = []
        for name, fn in providers:
            try:
                text = (await fn(data, ext, mime)).strip()
                if text:
                    logger.debug(f"[image_bridge] 使用 {name} 识别成功")
                    return text
            except Exception as e:
                errors.append(f"{name}: {e}")
                logger.warning(f"[image_bridge] {name} 识别失败，尝试下一服务: {e}")
        if errors:
            raise RuntimeError("；".join(errors))
        raise RuntimeError("未配置任何可用的识别服务")

    async def _recognize_xiaomi(self, data: bytes, ext: str, mime: str) -> str:
        """小米 MiMo Token Plan 识图模型（OpenAI 兼容，图片以 base64 data URI 传入）。

        模型名支持自动降级：配置的模型若因「已下线/不存在」报错，会依次尝试
        XIAOMI_MODEL_FALLBACKS（V2.6 系列优先），避免官方迭代模型名后识图直接失效。
        """
        api_key = str(self._cfg("xiaomi_api_key", "") or "").strip()
        base_url = str(
            self._cfg("xiaomi_base_url", DEFAULT_XIAOMI_BASE_URL) or DEFAULT_XIAOMI_BASE_URL
        ).rstrip("/")
        model = str(self._cfg("xiaomi_model", DEFAULT_XIAOMI_MODEL) or DEFAULT_XIAOMI_MODEL).strip()
        timeout = int(self._cfg("ocr_timeout", DEFAULT_OCR_TIMEOUT) or DEFAULT_OCR_TIMEOUT)
        if not api_key:
            raise RuntimeError("未配置小米 API Key（xiaomi_api_key）")

        # 未知 mime 时按 JPEG 处理，保证 data URI 合法
        if not str(mime).startswith("image/"):
            mime = "image/jpeg"

        b64 = base64.b64encode(data).decode("ascii")
        prompt = (
            "请识别并描述这张图片，用中文简洁回答：\n"
            "1) 图片中的文字内容（如有，请原样输出）；\n"
            "2) 图片的主要内容、场景或物体。"
        )
        headers = {
            "api-key": api_key,  # 官方文档 curl 示例使用的鉴权头
            "Content-Type": "application/json",
        }
        url = f"{base_url}/chat/completions"

        models: list[str] = []
        for cand in (model, *XIAOMI_MODEL_FALLBACKS):
            cand = str(cand or "").strip()
            if cand and cand not in models:
                models.append(cand)

        last_err = ""
        for idx, cand in enumerate(models):
            payload = {
                "model": cand,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{b64}"},
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                "max_completion_tokens": 1024,
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                result = resp.json()
                try:
                    content = (
                        ((result.get("choices") or [{}])[0].get("message") or {}).get(
                            "content"
                        )
                        or ""
                    )
                except Exception:
                    raise RuntimeError(
                        f"小米接口返回异常: {str(result)[:200]}"
                    ) from None
                if not content.strip():
                    raise RuntimeError("小米接口未返回识别内容")
                if idx > 0:
                    logger.info(
                        f"[image_bridge] 小米模型已自动降级为 `{cand}`（原 `{model}` 不可用）"
                    )
                return content.strip()

            body = resp.text[:200]
            last_err = f"小米接口 HTTP {resp.status_code}: {body}"
            low = body.lower()
            model_issue = resp.status_code in (400, 404) and any(
                k in low
                for k in ("model", "not found", "unsupported", "invalid", "不存在", "下线")
            )
            if model_issue and idx < len(models) - 1:
                logger.warning(
                    f"[image_bridge] 小米模型 `{cand}` 不可用，改用下一个候选: {last_err}"
                )
                continue
            raise RuntimeError(last_err)
        raise RuntimeError(last_err or "小米接口调用失败")

    async def _baidu_access_token(self) -> str:
        """获取（并缓存）百度 access_token，有效期 30 天，提前 10 分钟自动刷新。"""
        ak = str(self._cfg("baidu_api_key", "") or "").strip()
        sk = str(self._cfg("baidu_secret_key", "") or "").strip()
        timeout = int(self._cfg("ocr_timeout", DEFAULT_OCR_TIMEOUT) or DEFAULT_OCR_TIMEOUT)
        if not ak or not sk:
            raise RuntimeError("未配置百度 API Key/Secret Key")
        cached = self._baidu_token
        if cached and cached["exp"] > time.time() + 600:
            return cached["token"]
        params = {"grant_type": "client_credentials", "client_id": ak, "client_secret": sk}
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.post(DEFAULT_BAIDU_TOKEN_URL, params=params)
        resp.raise_for_status()
        result = resp.json()
        token = result.get("access_token")
        if not token:
            raise RuntimeError(
                f"百度获取 access_token 失败: "
                f"{result.get('error_description') or result.get('error_msg') or result}"
            )
        expires_in = int(result.get("expires_in") or 2592000)
        self._baidu_token = {"token": token, "exp": time.time() + expires_in}
        return token

    async def _recognize_baidu(self, data: bytes, ext: str, mime: str) -> str:
        """百度智能云图像识别（通用物体和场景识别 advanced_general）。"""
        token = await self._baidu_access_token()
        timeout = int(self._cfg("ocr_timeout", DEFAULT_OCR_TIMEOUT) or DEFAULT_OCR_TIMEOUT)
        b64 = base64.b64encode(data).decode("ascii")
        params = {"access_token": token}
        form = {"image": b64}
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.post(DEFAULT_BAIDU_API_URL, params=params, data=form)
        resp.raise_for_status()
        result = resp.json()
        if result.get("error_code"):
            raise RuntimeError(
                f"百度接口错误 {result.get('error_code')}: {result.get('error_msg')}"
            )
        items = result.get("result") or []
        parts = []
        for it in items:
            keyword = str(it.get("keyword") or "").strip()
            if not keyword:
                continue
            score = int(float(it.get("score") or 0) * 100)
            root = str(it.get("root") or "").strip()
            line = f"{keyword}（{score}%）" + (f"[{root}]" if root else "")
            parts.append(line)
        if not parts:
            raise RuntimeError("百度接口未返回有效识别结果")
        return "百度图像识别：" + "、".join(parts)

    async def _recognize_ocr(self, data: bytes, ext: str, mime: str) -> str:
        """OCR.space 免费 OCR 接口（三级链的兜底服务）。"""
        api_url = self._cfg("ocr_api_url", DEFAULT_OCR_API_URL)
        api_key = self._cfg("ocr_api_key", DEFAULT_OCR_API_KEY)
        language = self._cfg("ocr_language", DEFAULT_OCR_LANGUAGE)
        timeout = int(self._cfg("ocr_timeout", DEFAULT_OCR_TIMEOUT) or DEFAULT_OCR_TIMEOUT)
        if not api_key:
            raise RuntimeError("未配置 OCR API Key（ocr_api_key）")

        filename = f"image.{ext}" if ext else "image.bin"
        form = {
            "apikey": api_key,
            "language": language,
            "isOverlayRequired": "false",
            "scale": "true",
        }
        timeout_cfg = httpx.Timeout(timeout)
        async with httpx.AsyncClient(timeout=timeout_cfg) as client:
            files = {"file": (filename, data, mime)}
            resp = await client.post(api_url, data=form, files=files)
        resp.raise_for_status()
        result = resp.json()

        if result.get("IsErroredOnProcessing") or result.get("OCRExitCode") not in (1, 2):
            err = result.get("ErrorMessage") or result.get("ErrorDetails") or "未知错误"
            raise RuntimeError(f"OCR 接口返回错误: {err}")

        parsed = result.get("ParsedResults") or []
        if not parsed:
            raise RuntimeError("OCR 接口未返回识别结果")
        return (parsed[0].get("ParsedText") or "").strip()

    async def _prepare_media(self, images: list) -> list[tuple[bytes, str]]:
        """在事件生命周期内把图片读成内存字节（含 gif 切帧），返回 [(data, ext), ...]。

        平台临时图片文件在事件处理结束后会被 AstrBot 清理，
        因此必须在 handler 内（事件还活着时）把字节读取出来，
        后台识别任务只做网络请求、不再依赖临时文件。
        """
        media: list[tuple[bytes, str]] = []
        for img in images:
            try:
                file_path = await img.convert_to_file_path()
                data = Path(file_path).read_bytes()
                ext = Path(file_path).suffix.lower().lstrip(".")
                if ext not in ("png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp"):
                    ext = self._sniff_image_ext(data)
                # gif 动图：切第一帧为 JPEG 静态图，所有识别服务都能处理
                if ext == "gif":
                    jpg_path = self._gif_to_static_frame(file_path)
                    if jpg_path != file_path:
                        data = Path(jpg_path).read_bytes()
                        ext = "jpg"
                        Path(jpg_path).unlink(missing_ok=True)  # 清理切帧临时文件
                media.append((data, ext))
            except Exception as e:
                logger.warning(f"[image_bridge] 图片读取失败: {e}")
        return media

    async def _recognize_media(self, media: list[tuple[bytes, str]]) -> str:
        """逐张识别内存中的图片字节，拼接多图识别结果。"""
        parts = []
        for i, (data, ext) in enumerate(media, start=1):
            text = await self._recognize_image(data, ext)
            if len(media) > 1:
                parts.append(f"图片{i}:\n{text}")
            else:
                parts.append(text)
        return "\n\n".join(p for p in parts if p)

    def _prune_pending(self) -> None:
        """清理过期项，并防止挂起字典无限增长。"""
        now = time.time()
        ttl = int(self._cfg("pending_ttl", DEFAULT_PENDING_TTL) or DEFAULT_PENDING_TTL)
        expired = [k for k, v in self._pending.items() if now - v["ts"] > ttl]
        for k in expired:
            self._pending.pop(k, None)
        if len(self._pending) > 500:  # 兜底：防止极端情况下内存膨胀
            self._pending.clear()

    def _spawn_recognition(
        self, entry: dict, media: list[tuple[bytes, str]], emoji_desc: str | None,
        had_images: bool,
    ) -> None:
        """后台异步识别图片字节，完成后把结果写入该挂起条目并唤醒等待者。

        直接持有 entry 引用：连发多张图片时旧任务只写旧条目，不会覆盖新条目。
        """
        task = asyncio.create_task(
            self._recognize_and_store(entry, media, emoji_desc, had_images)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _recognize_and_store(
        self, entry: dict, media: list[tuple[bytes, str]], emoji_desc: str | None,
        had_images: bool,
    ) -> None:
        """识别并写入挂起内容（失败/为空时写入占位文本），随后唤醒 on_llm_request。"""
        content_parts: list[str] = []
        if emoji_desc:
            content_parts.append(emoji_desc)
        if media:
            try:
                ocr = await self._recognize_media(media)
                if ocr:
                    content_parts.append(ocr)
            except Exception as e:
                logger.error(f"图片识别失败: {e}")
                # 识别失败也不打扰用户：挂起占位内容，让 AI 知道图片没读到
                content_parts.append("[图片识别失败，未能获取图片内容]")
        elif had_images:
            # 图片存在但字节读取失败（如临时文件已被清理/下载失败）
            content_parts.append("[图片读取失败，未能获取图片内容]")
        content = "\n\n".join(p for p in content_parts if p)
        if not content:
            content = "[用户发送了一张图片，但未识别到其中的文字内容]"
        entry["text"] = content
        entry["event"].set()

    # ---------------------------------------------------------------- 事件
    async def on_message(self, event: AstrMessageEvent):
        """处理图片/表情消息：识别并挂起内容；纯图片/表情消息不让 AI 回答（等待提问）。

        表情处理：QQ 官方把表情解析为 `[表情]`/`[表情:赞]` 文本 + 图片附件。
        - `[表情:赞]`（带名字）：文本本身有语义，AI 可直接理解 -> 永不拦截，直接回复；
        - `[表情]`（无名字，自定义表情包）：切帧 OCR 识别文字后，默认拦截等待提问，
          受 `emoji_wait_pending` 开关控制（关闭则直接回复）；
        - 表情包图片（多为 gif 动图）**切帧为静态图后进 OCR**，识别出表情包上的文字。
        """
        text = (event.message_str or "").strip()
        # 去掉表情标记后的"真实文字"：QQ 把表情转成 [表情]/[表情:赞] 文本，
        # 只有真实文字才算"文字提问"；纯表情标记视为"只发了表情"
        pure_text = EMOJI_RE.sub("", text).strip()
        key = self._pending_key(event)
        emoji_desc = self._extract_emoji_desc(text)
        images = self._extract_images(event, text)

        if not images and not emoji_desc:
            return  # 纯文字消息：由 on_llm_request 判断是否有挂起的图片内容

        # 收到图片/表情立即占位挂起（识别在后台异步进行），
        # 避免用户提问过快时 on_llm_request 检查时识别结果尚未写入而漏注入
        entry = {"text": None, "ts": time.time(), "event": asyncio.Event()}
        self._pending[key] = entry
        self._prune_pending()
        # 事件生命周期内读取图片字节（临时文件在事件结束后会被 AstrBot 清理），
        # 后台任务只做网络识别、不再依赖临时文件
        media = await self._prepare_media(images)
        self._spawn_recognition(entry, media, emoji_desc, bool(images))

        if pure_text:
            # 图片/表情 + 真实文字同时发送：不拦截，on_llm_request 会把内容注入本次提问
            logger.info(
                f"[image_bridge] 收到图片+文字消息 (session={event.unified_msg_origin})"
            )
            return

        # 表情放行规则（仅当无真实文字时判断）：
        # - [表情:xxx]（带名字）：文本本身有语义，AI 可直接理解 -> 永不拦截，直接回复
        # - [表情]（无名字，自定义表情包）：需 OCR 才能理解 -> 默认拦截等待提问，
        #   受 emoji_wait_pending 开关控制（关闭则放行，AI 直接回复）
        if emoji_desc:
            m = EMOJI_RE.search(text)
            named = bool(m and (m.group(1) or "").strip())
            if named:
                logger.debug("[image_bridge] 带名字表情 [表情:xxx]，AI 可直接理解，放行")
                return
            if not bool(self._cfg("emoji_wait_pending", DEFAULT_EMOJI_WAIT_PENDING)):
                logger.debug("[image_bridge] 表情包不参与等待（emoji_wait_pending=false），放行")
                return

        # 只发了图片/无名字表情包：静默挂起识别内容，拦截本次消息（AI 不回答），后台等待用户提问
        self._gate_event(event)

    async def picreset(self, event: AstrMessageEvent):
        """清除本会话挂起的图片识别内容。"""
        self._pending.pop(self._pending_key(event), None)
        yield event.plain_result("🗑 已清除挂起的图片识别内容，请重新发送图片。")

    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 请求前：若本会话有挂起的图片识别内容，注入并消费（一次性）。"""
        key = self._pending_key(event)
        pending = self._pending.get(key)
        if not pending:
            return
        ttl = int(self._cfg("pending_ttl", DEFAULT_PENDING_TTL) or DEFAULT_PENDING_TTL)
        if time.time() - pending["ts"] > ttl:
            self._pending.pop(key, None)
            return

        # 若识别仍在进行（用户提问过快），限时等待识别完成后再注入
        if not pending["event"].is_set():
            wait_timeout = int(
                self._cfg("recognition_wait_timeout", DEFAULT_RECOGNITION_WAIT)
                or DEFAULT_RECOGNITION_WAIT
            )
            try:
                await asyncio.wait_for(pending["event"].wait(), timeout=wait_timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    f"[image_bridge] 等待图片识别完成超时（{wait_timeout}s），按当前内容注入"
                )

        image_text = pending.get("text")
        if not image_text:
            image_text = "[用户上传了图片，但识别内容未就绪]"
        template = self._cfg("prompt_template", DEFAULT_PROMPT_TEMPLATE)
        content = template.format(image_content=image_text)
        try:
            from astrbot.core.agent.message import TextPart  # v4.16+ 推荐方式

            part: object = TextPart(text=content).mark_as_temp()
        except Exception:
            # 兜底：旧版本以 dict 形式追加
            part = {"type": "text", "text": content}
        req.extra_user_content_parts.append(part)
        self._pending.pop(key, None)
        logger.info(
            f"[image_bridge] 已将图片识别内容注入 LLM 请求 (session={event.unified_msg_origin})"
        )

    async def terminate(self) -> None:
        """插件卸载/停用时清空挂起内容并取消后台识别任务。"""
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        self._pending.clear()

# ===========================================================================
# 核心二：每日简报（原 astrbot-plugin-daily-digest.DailyDigestPlugin）
# 定时抓取天气/新闻/前沿/GitHub 日升榜，AI 总结后推送给所有用户。
# ===========================================================================
class DailyDigestCore(_PluginCoreBase):
    """每日简报插件"""

    def __init__(self, owner: "ElainaSuitePlugin") -> None:
        super().__init__(owner)
        self._fallback_sched = None
        self._cron_job_id = None
        self._cron_check_job_id = None
        # 发送防重：08:00 定时任务与 5 分钟兜底检查会同时触发，靠 KV 读改写挡不住
        # （生成一份简报要 1~2 分钟，两个协程都能在写回之前通过检查 -> 一天发两遍）
        self._digest_lock = asyncio.Lock()
        # 天气防高并发状态：每城市单飞锁 + 全局请求节流 + 每日调用计数
        self._weather_locks: dict[str, asyncio.Lock] = {}
        self._last_weather_call = 0.0
        self._weather_call_day = ""
        self._weather_call_count = 0
        # CISA KEV 下载缓存：(下载时间戳, 解析后的条目列表)；6 小时内不重复下载 1.8MB
        self._kev_cache: tuple[float, list[dict]] | None = None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def initialize(self) -> None:
        """插件被激活时调用：注册定时任务。"""
        await self._setup_scheduler()

    async def terminate(self) -> None:
        """插件被禁用/重载时调用：清理定时任务。"""
        try:
            cm = getattr(self.context, "cron_manager", None)
            if cm is not None and hasattr(cm, "delete_job"):
                for jid in (self._cron_job_id, self._cron_check_job_id):
                    if jid:
                        try:
                            await cm.delete_job(jid)
                        except Exception:
                            pass
                self._cron_job_id = None
                self._cron_check_job_id = None
        except Exception as e:
            logger.warning(f"[daily_digest] 注销 cron 任务失败: {e}")
        try:
            if self._fallback_sched is not None:
                self._fallback_sched.shutdown(wait=False)
                self._fallback_sched = None
        except Exception as e:
            logger.warning(f"[daily_digest] 关闭调度器失败: {e}")

    # ------------------------------------------------------------------ #
    # 定时任务注册
    # ------------------------------------------------------------------ #
    async def _setup_scheduler(self) -> None:
        cron = str(self.config.get("send_cron") or "0 8 * * *").strip()
        tz = str(self.config.get("digest_timezone") or "Asia/Shanghai").strip()
        # 优先使用 AstrBot 内置 cron_manager（AstrBot 4.x）
        try:
            cm = getattr(self.context, "cron_manager", None)
            if cm is not None and hasattr(cm, "add_basic_job"):
                try:
                    job = await cm.add_basic_job(
                        name="daily_digest",
                        cron_expression=cron,
                        handler=self._on_schedule,
                        description="每日简报定时发送",
                        enabled=True,
                        persistent=False,
                        timezone=tz,
                    )
                    j2 = await cm.add_basic_job(
                        name="daily_digest_check",
                        cron_expression="*/5 * * * *",
                        handler=self._on_check,
                        description="每日简报兜底补发检查（每 5 分钟）",
                        enabled=True,
                        persistent=False,
                        timezone=tz,
                    )
                except TypeError:
                    # 旧版 cron_manager 不支持 timezone 参数
                    job = await cm.add_basic_job(
                        name="daily_digest",
                        cron_expression=cron,
                        handler=self._on_schedule,
                        description="每日简报定时发送",
                        enabled=True,
                        persistent=False,
                    )
                    j2 = await cm.add_basic_job(
                        name="daily_digest_check",
                        cron_expression="*/5 * * * *",
                        handler=self._on_check,
                        description="每日简报兜底补发检查（每 5 分钟）",
                        enabled=True,
                        persistent=False,
                    )
                self._cron_job_id = getattr(job, "job_id", None)
                self._cron_check_job_id = getattr(j2, "job_id", None)
                logger.info(
                    f"[daily_digest] 已注册定时任务（cron_manager）: {cron} + 每5分钟兜底检查（时区 {tz}）"
                )
                return
        except Exception as e:
            logger.warning(
                f"[daily_digest] cron_manager 注册失败，回退 APScheduler: {e}"
            )
        # 回退：直接使用 APScheduler（AstrBot 自带依赖，显式指定时区）
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger

            self._fallback_sched = AsyncIOScheduler(timezone=tz)
            self._fallback_sched.add_job(
                self._on_schedule,
                CronTrigger.from_crontab(cron, timezone=tz),
                id="daily_digest",
                misfire_grace_time=60,
            )
            self._fallback_sched.add_job(
                self._on_check,
                CronTrigger.from_crontab("*/5 * * * *", timezone=tz),
                id="daily_digest_check",
                misfire_grace_time=60,
            )
            self._fallback_sched.start()
            logger.info(
                f"[daily_digest] 已注册定时任务（APScheduler）: {cron} + 每5分钟兜底检查（时区 {tz}）"
            )
        except Exception as e:
            logger.error(f"[daily_digest] 定时任务注册失败: {e}")

    # ------------------------------------------------------------------ #
    # 指令
    # ------------------------------------------------------------------ #
    async def digest_command(self, event: AstrMessageEvent):
        """立即生成并推送一份每日简报"""
        await self._remember_session(event.unified_msg_origin)
        asyncio.create_task(self._generate_and_send([event.unified_msg_origin]))
        yield event.plain_result("⏳ 正在抓取数据并生成今日简报，请稍候…")

    async def subscribe_command(self, event: AstrMessageEvent):
        """订阅每日简报（定时推送到当前会话）"""
        await self._remember_session(event.unified_msg_origin)
        yield event.plain_result("✅ 已订阅每日简报，将按配置时间推送到当前会话。")

    async def unsubscribe_command(self, event: AstrMessageEvent):
        """退订每日简报"""
        await self._forget_session(event.unified_msg_origin)
        yield event.plain_result("✅ 已退订每日简报。")

    # ------------------------------------------------------------------ #
    # 定时任务入口（08:00 cron + 每 5 分钟兜底补发，每天最多发一次）
    # ------------------------------------------------------------------ #
    async def _on_schedule(self) -> None:
        """定时任务入口（cron_manager 以 handler() 方式调用，无需参数）"""
        await self._maybe_send_digest()

    async def _on_check(self) -> None:
        """兜底检查（每 5 分钟）：错过 08:00（设备休眠/网络中断/cron misfire）时补发"""
        await self._maybe_send_digest()

    async def _maybe_send_digest(self, force: bool = False) -> None:
        """每天最多发送一次：到达发送时间且在补发窗口内则生成并发送，成功后记录已发日期。

        force=True 时忽略「今天已发」与时间窗（供手动 /每日简报 使用）。

        并发防护分两层（缺一不可）：
        1. ``self._digest_lock``：08:00 定时任务与每 5 分钟兜底检查会**同时**触发，
           生成一份简报要 1~2 分钟，旧的「先读 KV 再写回」会让两个协程都通过检查，
           实测就是早上收到两遍简报。锁内二次读 KV 才能真正挡掉第二个。
        2. **先占位再生成**：进锁后立刻把今天的日期写进 KV，异常时清空回滚。
           这样即使进程在生成途中被重启，也不会在同一天重复发送。
        """
        try:
            now = self._now_local()
            today = now.strftime("%Y-%m-%d")
            if not force:
                start_h, start_m = self._send_window_start()
                deadline_h, deadline_m = self._send_deadline()
                now_min = now.hour * 60 + now.minute
                if now_min < start_h * 60 + start_m:
                    return  # 还没到发送时间
                if now_min > deadline_h * 60 + deadline_m:
                    return  # 超过补发截止时间，今天放弃（避免深夜误发）
            async with self._digest_lock:
                if not force:
                    try:
                        last = (await self.get_kv_data("last_digest_date", "")) or ""
                    except Exception:
                        last = ""
                    if last == today:
                        return  # 别的协程刚发完
                targets = await self._collect_target_sessions()
                if not targets:
                    logger.warning("[daily_digest] 没有可推送的会话，跳过本次发送")
                    return
                # 原子占位：先写 KV 再生成，避免第二个协程在生成期间挤进来
                try:
                    await self.put_kv_data("last_digest_date", today)
                except Exception as e:
                    logger.debug(f"[daily_digest] 预占发送日期失败: {e}")
                try:
                    await self._generate_and_send(targets)
                except Exception:
                    # 生成/发送失败必须回滚，否则今天不会再补发
                    try:
                        await self.put_kv_data("last_digest_date", "")
                    except Exception:
                        pass
                    raise
                logger.info(f"[daily_digest] 今日简报已发送（{today}）")
        except Exception as e:
            logger.error(f"[daily_digest] 定时发送失败: {e}", exc_info=True)

    def _now_local(self) -> datetime:
        """按配置时区取当前时间（zoneinfo 失败时回退系统本地时间）。"""
        tz_name = str(self.config.get("digest_timezone") or "Asia/Shanghai").strip()
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            return datetime.now().astimezone()

    def _send_window_start(self) -> tuple[int, int]:
        """从 send_cron 解析发送时刻 (hour, minute)。"""
        cron = str(self.config.get("send_cron") or "0 8 * * *").strip()
        parts = cron.split()
        try:
            minute = int(parts[0]) if parts[0].isdigit() else 0
            hour = int(parts[1]) if parts[1].isdigit() else 8
            return (hour, minute)
        except Exception:
            return (8, 0)

    def _send_deadline(self) -> tuple[int, int]:
        """补发截止时刻 (hour, minute)，默认 13:00。"""
        dl = str(self.config.get("send_deadline") or "13:00").strip()
        try:
            h, m = dl.split(":")
            return (int(h), int(m))
        except Exception:
            return (13, 0)

    # ------------------------------------------------------------------ #
    # 生成与发送
    # ------------------------------------------------------------------ #
    async def _generate_and_send(self, targets: list[str]) -> None:
        try:
            digest = await self._build_digest()
        except Exception as e:
            logger.error(f"[daily_digest] 生成简报失败: {e}", exc_info=True)
            digest = f"⚠️ 每日简报生成失败：{e}"
        await self._send_digest(digest, targets)

    async def _send_digest(self, text: str, targets: list[str]) -> None:
        """按 digest_send_mode 发送简报：
        text   -> 纯文本分条发送
        image  -> 渲染为图片发送（AstrBot t2i）
        file   -> 保存为 .md 文件发送（需平台支持本地文件）
        both   -> 同时发送图片 + .md 文件（图片失败则自动降级为「md + 文本」）
        auto   -> 短文本直接发文本；超过 digest_long_threshold 时按 both 发送
                  （即图片与 .md 都发；图片失败回退 md/文本）

        注：只要发送方式包含图片，就**同时**补发一份 .md 文件——图片便于手机阅读，
        md 文件便于保存/检索/二次加工，两者互补。
        """
        mode = str(self.config.get("digest_send_mode") or "auto").strip().lower()
        threshold = self._cfg_int("digest_long_threshold", 2000)
        if mode not in ("text", "image", "file", "both", "auto"):
            mode = "auto"

        send_image = False
        send_md = False
        if mode == "text":
            pass
        elif mode == "image":
            send_image = True
            send_md = bool(self._cfg_bool("digest_also_send_md", True))
        elif mode == "file":
            send_md = True
        elif mode == "both":
            send_image = True
            send_md = True
        elif mode == "auto":
            if len(text) > threshold:
                send_image = True
                send_md = bool(self._cfg_bool("digest_also_send_md", True))

        if not send_image and not send_md:
            for umo in targets:
                await self._send_text(umo, text)
            return

        img_ok = await self._send_image(text, targets) if send_image else False
        md_ok = await self._send_md(text, targets) if send_md else False

        # 图片与文件都失败 -> 回退纯文本，保证简报一定送达
        if not img_ok and not md_ok:
            logger.warning("[daily_digest] 图片与 md 文件均发送失败，回退纯文本")
            for umo in targets:
                await self._send_text(umo, text)

    async def _send_image(self, text: str, targets: list[str]) -> bool:
        """渲染为图片并发送；任一目标成功即返回 True。

        注意：图片发送时 QQ 官方等平台的媒体上传可能长时间重试（如
        upload_group_and_c2c_image 5 次指数退避约 90s），这里加超时与「全目标失败回退」，
        避免日报因图片上传失败而整体丢失。"""
        try:
            render_text = _sanitize_for_image(text)
            # 网络 t2i 偶发 503 会回退本地 PIL；没有中文字体时中文会变方框
            _ensure_cjk_font()
            url = await self.text_to_image(render_text, return_url=True)
            if not url:
                logger.warning("[daily_digest] 简报渲染图片返回空")
                return False
            try:
                chain = _image_chain(url)
            except Exception as e:
                logger.error(f"[daily_digest] 渲染结果无法转成图片消息链: {e}（原始值={str(url)[:120]}）")
                return False
            ok_cnt = 0
            for umo in targets:
                try:
                    await asyncio.wait_for(
                        self._gated_send(umo, chain),
                        timeout=90,
                    )
                    ok_cnt += 1
                except Exception as e:
                    logger.error(f"[daily_digest] 图片发送到 {umo} 失败: {e}")
                await asyncio.sleep(0.3)
            if ok_cnt == 0:
                logger.warning(
                    "[daily_digest] 图片发送全部失败（可能为平台媒体上传问题）"
                )
                return False
            logger.info(
                f"[daily_digest] 简报已渲染为图片发送（成功 {ok_cnt}/{len(targets)}）"
            )
            return True
        except Exception as e:
            logger.warning(f"[daily_digest] 简报渲染图片失败: {e}")
            return False

    async def _send_md(self, text: str, targets: list[str]) -> bool:
        """保存为 .md 文件并发送；任一目标成功即返回 True。"""
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            data_dir = get_astrbot_data_path()
            temp_dir = os.path.join(data_dir, "temp")
            os.makedirs(temp_dir, exist_ok=True)
            fpath = os.path.join(
                temp_dir,
                f"daily_digest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
            )
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(text)
            ok_cnt = 0
            for umo in targets:
                try:
                    chain = MessageChain(chain=[File(name="每日简报.md", file=fpath)])
                    await asyncio.wait_for(
                        self._gated_send(umo, chain), timeout=90
                    )
                    ok_cnt += 1
                except Exception as e:
                    logger.error(f"[daily_digest] 文件发送到 {umo} 失败: {e}")
                await asyncio.sleep(0.3)
            if ok_cnt:
                logger.info(
                    f"[daily_digest] 简报已作为 md 文件发送（成功 {ok_cnt}/{len(targets)}）"
                )
                return True
            logger.warning("[daily_digest] md 文件发送全部失败")
            return False
        except Exception as e:
            logger.warning(f"[daily_digest] 发送 md 文件失败: {e}")
            return False

    async def _gated_send(self, *args) -> bool:
        """发送消息（带门禁放行标记）。

        日报的定时推送属于本合并插件自己的主动发送：发送期间把
        owner._bypass_cnt 加一，让 proactive_guard 的主动消息门禁放行，
        避免被误判为「外部插件主动发言」而拦截。
        """
        self.owner._bypass_cnt += 1
        try:
            return bool(await self.context.send_message(*args))
        finally:
            self.owner._bypass_cnt -= 1

    async def _build_digest(self) -> str:
        weather = None
        if self._cfg_bool("weather_enabled", True):
            try:
                weather = await self._fetch_weather()
            except Exception as e:
                logger.warning(f"[daily_digest] 天气获取失败: {e}")

        max_items = self._cfg_int("max_items_per_section", 5)
        sections: dict[str, list[dict]] = {}
        for enabled_key, feed_key, _label, _emoji in SECTIONS:
            if not self._cfg_bool(enabled_key, True):
                continue
            if feed_key == "github_trending":
                items = await self._fetch_github_trending()
            else:
                items = await self._fetch_category(feed_key, max_items)
            if items:
                sections[feed_key] = items

        if self._cfg_bool("ai_summary_enabled", True):
            try:
                ai_text = await self._ai_summarize(weather, sections)
                if ai_text:
                    return ai_text
            except Exception as e:
                logger.warning(f"[daily_digest] AI 总结失败，使用模板: {e}")

        return self._template_digest(weather, sections)

    # ------------------------------------------------------------------ #
    # 会话收集
    # ------------------------------------------------------------------ #
    async def _collect_target_sessions(self) -> list[str]:
        """收集推送目标：手动指定 > 数据库枚举（所有用户）> 订阅会话。"""
        sessions: set[str] = set()

        cfg_targets = self.config.get("digest_target_sessions")
        if cfg_targets:
            for line in str(cfg_targets).splitlines():
                line = line.strip()
                if line and ":" in line:
                    sessions.add(line)
            if sessions:
                return sorted(sessions)

        # 与 AstrBot 面板「会话管理」同源的数据库枚举
        try:
            db = self.context.get_db()
            from sqlalchemy import select

            from astrbot.core.db.po import ConversationV2

            async with db.get_db() as sess:
                res = await sess.execute(select(ConversationV2.user_id).distinct())
                for row in res:
                    v = row[0]
                    if v and ":" in str(v):
                        sessions.add(str(v))
            logger.info(f"[daily_digest] 数据库枚举到 {len(sessions)} 个会话")
        except Exception as e:
            logger.warning(f"[daily_digest] 数据库枚举会话失败: {e}")

        try:
            tracked = await self.get_kv_data("subscribed_sessions", []) or []
            for s in tracked:
                if ":" in str(s):
                    sessions.add(str(s))
        except Exception as e:
            logger.debug(f"[daily_digest] 读取订阅会话失败: {e}")

        return sorted(sessions)

    async def _remember_session(self, umo: str) -> None:
        if not umo or ":" not in umo:
            return
        try:
            tracked = await self.get_kv_data("subscribed_sessions", []) or []
            if umo not in tracked:
                tracked.append(umo)
                await self.put_kv_data("subscribed_sessions", tracked)
                logger.info(f"[daily_digest] 已记录订阅会话 {umo}")
        except Exception as e:
            logger.debug(f"[daily_digest] 记录订阅会话失败: {e}")

    async def _forget_session(self, umo: str) -> None:
        try:
            tracked = await self.get_kv_data("subscribed_sessions", []) or []
            if umo in tracked:
                tracked.remove(umo)
                await self.put_kv_data("subscribed_sessions", tracked)
                logger.info(f"[daily_digest] 已移除订阅会话 {umo}")
        except Exception as e:
            logger.debug(f"[daily_digest] 移除订阅会话失败: {e}")

    # ------------------------------------------------------------------ #
    # 数据抓取
    # ------------------------------------------------------------------ #
    async def _fetch_category(self, feed_key: str, max_items: int) -> list[dict]:
        items: list[dict] = []
        # 抓取器内部已按自身窗口过滤好的条目（如 cisa:kev），不再参与下面的时效过滤
        prewindowed: list[dict] = []
        for entry in self._feed_urls(feed_key):
            for attempt in (1, 2):  # 抖动网络重试一次
                try:
                    if self._is_json_source(entry):
                        got = await self._fetch_json_source(entry, max_items)
                    else:
                        data = await asyncio.to_thread(self._http_get_bytes, entry)
                        got = self._parse_feed(data)
                    if entry in KEV_SOURCES:
                        prewindowed.extend(got)
                    else:
                        items.extend(got)
                    break
                except Exception as e:
                    if attempt == 1:
                        logger.warning(
                            f"[daily_digest] 抓取 {feed_key} 源失败 {entry}，重试一次: {e}"
                        )
                    else:
                        logger.warning(
                            f"[daily_digest] 抓取 {feed_key} 源失败 {entry}: {e}"
                        )
        # 政策板块来自政府官网，发文频率低（可能数日才有一条），
        # 因此放宽时效窗口，避免「政策前沿」整块空白；其余板块仍严格卡「昨日」。
        # AI / 网安板块作为新增板块，单日条目数偏少（尤其网安源更新不规律），
        # 单独放宽到 ai_recent_days / security_recent_days（默认 2 天），仍走同一套过滤。
        recent_days = 0
        if feed_key == "feeds_policy":
            recent_days = max(1, self._cfg_int("gov_policy_recent_days", 30))
        elif feed_key == "feeds_ai":
            recent_days = max(1, self._cfg_int("ai_recent_days", 2))
        elif feed_key == "feeds_security":
            recent_days = max(1, self._cfg_int("security_recent_days", 2))
        merged = self._filter_yesterday(items, recent_days)
        if prewindowed:
            # cisa:kev 在抓取器里已按 security_kev_days 过滤（KEV 收录当天也要能显示），
            # 再卡一次「近 2 天」会把 7 天窗口缩成 2 天，所以这里只做合并排序。
            now = datetime.now().astimezone()
            epoch = datetime.min.replace(tzinfo=now.tzinfo)
            merged = merged + prewindowed
            merged.sort(key=lambda it: it.get("pub_date") or epoch, reverse=True)
        return merged[:max_items]

    @staticmethod
    def _is_json_source(entry: str) -> bool:
        return (
            entry.startswith("cctv:")
            or entry in ("tencent:hot", "baidu:hot")
            or entry in GOV_SOURCES
            or entry in KEV_SOURCES
        )

    async def _fetch_json_source(self, token: str, max_items: int) -> list[dict]:
        """抓取免费中文 JSON 新闻源（央视 / 腾讯热榜 / 百度热搜 / 政府官网）。
        热榜类条目按「最新」处理（pub_date=当前时间），通过时效过滤；
        政府官网条目带真实发布日期，按真实日期参与时效过滤。"""
        now = datetime.now().astimezone()
        if token in GOV_SOURCES:
            return await self._fetch_gov_source(token, max_items)
        if token in KEV_SOURCES:
            return await self._fetch_cisa_kev(max_items)
        if token.startswith("cctv:"):
            channel = token.split(":", 1)[1].strip() or "news"
            url = (
                "https://news.cctv.com/2019/07/gaiban/cmsdatainterface/page/"
                f"{channel}_1.jsonp"
            )
            text = (await asyncio.to_thread(self._http_get_bytes, url, 20)).decode(
                "utf-8", "replace"
            )
            m = re.search(r"\(\s*(\{.*\})\s*\)\s*$", text, re.S)
            payload = json.loads(m.group(1)) if m else json.loads(text)
            out: list[dict] = []
            for it in payload.get("data", {}).get("list") or []:
                title = _strip_html(str(it.get("title") or it.get("brief") or ""))
                if not title:
                    continue
                link = str(it.get("url") or "").strip()
                pub = _parse_date(str(it.get("focus_date") or "")) or now
                out.append(
                    {
                        "title": title,
                        "link": link,
                        "pub_date": pub,
                        "summary": _strip_html(str(it.get("brief") or ""))[:200],
                    }
                )
            return out
        if token == "tencent:hot":
            url = "https://r.inews.qq.com/gw/event/hot_ranking_list?page_size=30"
            payload = json.loads(
                (await asyncio.to_thread(self._http_get_bytes, url, 20)).decode(
                    "utf-8", "replace"
                )
            )
            out = []
            for it in (payload.get("idlist") or [{}])[0].get("newslist") or []:
                title = _strip_html(str(it.get("title") or ""))
                if not title:
                    continue
                if "腾讯新闻用户最关注" in title:  # 过滤占位头条目
                    continue
                out.append(
                    {
                        "title": title,
                        "link": "",
                        "pub_date": now,
                        "summary": "",
                    }
                )
            return out
        if token == "baidu:hot":
            url = "https://top.baidu.com/api/board?platform=wise&tab=realtime"
            payload = json.loads(
                (await asyncio.to_thread(self._http_get_bytes, url, 20)).decode(
                    "utf-8", "replace"
                )
            )
            out = []
            for it in self._walk_keyed_items(payload):
                word = str(it.get("word") or it.get("query") or "").strip()
                if not word:
                    continue
                link = str(it.get("url") or "").strip() or (
                    "https://www.baidu.com/s?wd=" + urllib.parse.quote(word)
                )
                out.append(
                    {
                        "title": word,
                        "link": link,
                        "pub_date": now,
                        "summary": str(it.get("desc") or "")[:200],
                    }
                )
            return out
        return []

    async def _fetch_gov_source(self, token: str, max_items: int) -> list[dict]:
        """政府官方网站源（政策板块）：
        - gov:zuixin  中国政府网「最新政策」ZUIXINZHENGCE.json（字段 TITLE/URL/DOCRELPUBTIME）
        - gov:yaowen  中国政府网「要闻」pushinfo.json（字段 title/link/pubDate/description）
        - ndrc:xwfb   国家发改委「新闻发布」列表页（HTML 静态 li/a/span）
        政策文件发布频率低（可能数日才有一条），所以这里不做「昨日」硬过滤，
        而是取「最近 gov_policy_recent_days 天内」，交由上层时效过滤统一处理。
        """
        days = max(1, self._cfg_int("gov_policy_recent_days", 30))
        now = datetime.now().astimezone()
        cutoff = now - timedelta(days=days)
        out: list[dict] = []

        if token == "gov:zuixin":
            url = "https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json"
            raw = await asyncio.to_thread(
                self._http_get_bytes, url, 20, GOV_HEADERS
            )
            payload = json.loads(raw.decode("utf-8", "replace"))
            for it in payload if isinstance(payload, list) else []:
                title = _strip_html(str(it.get("TITLE") or ""))
                if not title:
                    continue
                pub = _parse_date(str(it.get("DOCRELPUBTIME") or ""))
                out.append(
                    {
                        "title": title,
                        "link": str(it.get("URL") or "").strip(),
                        "pub_date": pub,
                        "summary": _strip_html(str(it.get("SUB_TITLE") or ""))[:200],
                        "source": "中国政府网",
                    }
                )
        elif token == "gov:yaowen":
            url = "https://www.gov.cn/pushinfo/v150203/pushinfo.json"
            raw = await asyncio.to_thread(
                self._http_get_bytes, url, 20, GOV_HEADERS
            )
            payload = json.loads(raw.decode("utf-8", "replace"))
            items = payload if isinstance(payload, list) else []
            for it in items:
                title = _strip_html(str(it.get("title") or ""))
                if not title:
                    continue
                out.append(
                    {
                        "title": title,
                        "link": str(it.get("link") or "").strip(),
                        "pub_date": _parse_date(str(it.get("pubDate") or "")),
                        "summary": _strip_html(str(it.get("description") or ""))[:200],
                        "source": "中国政府网",
                    }
                )
        elif token == "ndrc:xwfb":
            base = "https://www.ndrc.gov.cn/xwdt/xwfb/"
            raw = await asyncio.to_thread(
                self._http_get_bytes, base, 20, GOV_HEADERS
            )
            html_text = raw.decode("utf-8", "replace")
            for m in re.finditer(
                r'<li[^>]*>\s*<a\s+href="([^"]+)"[^>]*title="([^"]*)"[^>]*>'
                r'[^<]*</a>\s*<span[^>]*>([\d/]+)</span>',
                html_text,
            ):
                href, title, date_s = m.group(1), m.group(2), m.group(3)
                title = _strip_html(title)
                if not title:
                    continue
                if href.startswith("//"):
                    link = "https:" + href
                elif href.startswith("http"):
                    link = href
                else:
                    link = urllib.parse.urljoin(base, href)
                out.append(
                    {
                        "title": title,
                        "link": link,
                        "pub_date": _parse_date(date_s.replace("/", "-")),
                        "summary": "",
                        "source": "国家发改委",
                    }
                )

        # 政策文件更新慢：优先保留窗口内条目；窗口内为空则退回最新几条，
        # 避免「政策前沿」板块因为近期无新政策而整块消失。
        recent = []
        for it in out:
            d = it.get("pub_date")
            if d is not None and d.tzinfo is None:
                d = d.replace(tzinfo=now.tzinfo)
                it["pub_date"] = d
            if d is None or d >= cutoff:
                recent.append(it)
        if not recent:
            recent = out[: max(1, max_items)]
            if recent:
                logger.info(
                    f"[daily_digest] {token} 近 {days} 天无新政策，退回最新 {len(recent)} 条"
                )
        return recent[: max(1, max_items * 3)]

    async def _fetch_cisa_kev(self, max_items: int) -> list[dict]:
        """CISA KEV（Known Exploited Vulnerabilities，已知被利用漏洞目录）。

        - 数据：known_exploited_vulnerabilities.json（约 1.8MB，结构见 CISA_KEV_URL 注释）
        - 窗口：不做「昨日」硬过滤。KEV 收录的是「已被实锤在野利用」的漏洞，
          新增节奏不均匀（常有几天没新增），卡昨日会经常整块空白，
          因此按 dateAdded 取「最近 security_kev_days 天」（默认 7）。
        - 缓存：单次下载后 6 小时内复用（1.8MB 不必每次日报都拉），
          缓存的是「已解析的全部条目」，窗口过滤在每次调用时按最新配置重算。
        - 失败隔离：任何异常都只 warning + 返回空列表，绝不拖垮整份日报。
        """
        days = max(1, self._cfg_int("security_kev_days", 7))
        now = datetime.now().astimezone()

        cache = getattr(self, "_kev_cache", None)
        if cache and time.time() - cache[0] < KEV_CACHE_TTL_SECONDS:
            parsed = list(cache[1])
        else:
            try:
                raw = await asyncio.to_thread(
                    self._http_get_bytes, CISA_KEV_URL, 30, CISA_HEADERS
                )
                payload = json.loads(raw.decode("utf-8", "replace"))
            except Exception as e:
                logger.warning(f"[daily_digest] CISA KEV 抓取失败: {e}")
                return []
            vulns = payload.get("vulnerabilities") if isinstance(payload, dict) else None
            if not isinstance(vulns, list):
                logger.warning("[daily_digest] CISA KEV 返回结构异常，缺少 vulnerabilities 列表")
                return []
            parsed = []
            for it in vulns:
                if not isinstance(it, dict):
                    continue
                cve = str(it.get("cveID") or "").strip()
                if not cve:
                    continue
                pub = _parse_date(str(it.get("dateAdded") or ""))
                if pub is None:
                    continue
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=now.tzinfo)
                vendor = _strip_html(str(it.get("vendorProject") or "")).strip()
                product = _strip_html(str(it.get("product") or "")).strip()
                name = _strip_html(str(it.get("vulnerabilityName") or "")).strip()
                subject = " ".join(p for p in (vendor, product) if p)
                title = f"{cve}：{subject} —— {name}" if subject else f"{cve}：{name}"
                summary = _strip_html(str(it.get("shortDescription") or ""))[:80]
                if str(it.get("knownRansomwareCampaignUse") or "").strip().lower() == "known":
                    summary = (summary + " ⚠️ 已用于勒索软件").strip()
                link = f"https://nvd.nist.gov/vuln/detail/{cve}"
                parsed.append(
                    {
                        "title": title,
                        "link": link,
                        "url": link,
                        "pub_date": pub,
                        "summary": summary,
                        "source": "CISA KEV",
                    }
                )
            self._kev_cache = (time.time(), parsed)

        cutoff = now - timedelta(days=days)
        hit = [
            it
            for it in parsed
            if it.get("pub_date") is not None and cutoff <= it["pub_date"] <= now
        ]
        hit.sort(key=lambda it: it["pub_date"], reverse=True)
        logger.info(
            f"[daily_digest] CISA KEV 近 {days} 天新增 {len(hit)} 条（全库 {len(parsed)} 条）"
        )
        return hit[: max(1, max_items * 3)]

    @staticmethod
    def _walk_keyed_items(node):
        """递归收集含 word/query 键的条目（适配百度热搜等多层嵌套结构）。"""
        out: list[dict] = []
        if isinstance(node, dict):
            if node.get("word") or node.get("query"):
                out.append(node)
            for v in node.values():
                out.extend(DailyDigestCore._walk_keyed_items(v))
        elif isinstance(node, list):
            for v in node:
                out.extend(DailyDigestCore._walk_keyed_items(v))
        return out

    def _feed_urls(self, feed_key: str) -> list[str]:
        val = self.config.get(feed_key)
        if isinstance(val, (list, tuple)):  # 兼容 list 形式的配置值
            val = "\n".join(str(v) for v in val)
        if val:
            urls = [u.strip() for u in str(val).splitlines() if u.strip()]
            if urls:
                # 自动迁移：若某板块配置的源全部是已停更的人民网 RSS（2025-06 停更），
                # 说明是旧版默认配置，自动切换到新的实时默认源
                if all("people.com.cn" in u for u in urls):
                    logger.info(
                        f"[daily_digest] {feed_key} 仍在使用已停更的人民网源，已自动迁移到实时默认源"
                    )
                    return DEFAULT_FEEDS.get(feed_key, [])
                return urls
        return DEFAULT_FEEDS.get(feed_key, [])

    # ------------------------------------------------------------------ #
    # GitHub 日升榜（免费免 Key 多源降级）
    # ------------------------------------------------------------------ #
    async def _fetch_github_trending(self) -> list[dict]:
        """近 N 天 star 增长最快的仓库 TopN（多源降级，全部失败则省略板块并记日志）。"""
        days = max(1, self._cfg_int("github_trending_days", 7))
        limit = max(1, self._cfg_int("github_trending_count", 10))
        min_stars = max(0, self._cfg_int("github_trending_min_stars", 0))
        for source in GITHUB_TRENDING_SOURCES:
            try:
                url = self._github_trending_url(source, days, limit)
                timeout = 25
                data = json.loads(
                    await asyncio.to_thread(self._http_get_bytes, url, timeout)
                )
                items = self._parse_trending_repos(data, days)
                if min_stars > 0:
                    items = [
                        it for it in items if int(it.get("stars") or 0) >= min_stars
                    ]
                if items:
                    logger.info(f"[daily_digest] GitHub 日升榜命中数据源: {source}")
                    return items[:limit]
                logger.warning(f"[daily_digest] GitHub 日升榜源 {source} 返回空结果")
            except Exception as e:
                logger.warning(
                    f"[daily_digest] GitHub 日升榜源 {source} 失败: {e}"
                )
        logger.warning("[daily_digest] GitHub 日升榜全部数据源失败，该板块省略")
        return []

    @staticmethod
    def _github_trending_url(source: str, days: int, limit: int) -> str:
        if source == "ossinsight":
            return (
                "https://api.ossinsight.io/v1/trends/repos/?period="
                f"past_7_days&limit={limit}"
            )
        if source == "gitterapp":
            return "https://api.gitterapp.com/repositories/trending?since=daily"
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        if source.endswith("_created"):
            q = f"created%3A%3E{since}"
        else:  # _alltime：全站热门（stars 排序），国内网络下作为日升榜的降级数据
            q = "stars%3A%3E1000"
        base = (
            "https://api.github.com/search/repositories"
            if source.startswith("github_search")
            else "https://gh-proxy.com/https://api.github.com/search/repositories"
        )
        return f"{base}?q={q}&sort=stars&order=desc&per_page={limit}"

    @staticmethod
    def _parse_trending_repos(data, days: int) -> list[dict]:
        """容错解析三种 API 的仓库列表（dict 的 items/repos/results/data 或顶层 list）。"""
        raw = DailyDigestCore._find_repo_list(data)
        out: list[dict] = []
        for obj in raw:
            if not isinstance(obj, dict):
                continue
            name = (
                obj.get("repo_name")
                or obj.get("full_name")
                or obj.get("name")
                or ""
            )
            name = str(name).strip().lstrip("/")
            if "/" not in name:
                # gitterapp 等源把 author 与 name 分开给
                author = str(obj.get("author") or "").strip().lstrip("@")
                repo = str(obj.get("name") or "").strip()
                if author and repo:
                    name = f"{author}/{repo}"
            if not name or "/" not in name:
                continue
            url = (
                obj.get("html_url")
                or obj.get("url")
                or f"https://github.com/{name}"
            )
            total_raw = (
                obj.get("current_total_stars")
                or obj.get("currentTotalStars")
                or obj.get("stargazers_count")
                or obj.get("total_stars")
            )
            period_raw = (
                obj.get("current_period_stars")
                or obj.get("currentPeriodStars")
                or obj.get("period_stars")
                or obj.get("added_stars")
                or obj.get("stars_today")
            )
            stars = DailyDigestCore._to_int(total_raw)
            period = DailyDigestCore._to_int(period_raw)
            if total_raw is None and obj.get("stars") is not None:
                # 部分源只给 stars（=总数）
                stars = DailyDigestCore._to_int(obj.get("stars"))
            elif period_raw is None and obj.get("stars") is not None:
                # OSS Insight：total_stars 为总数、stars 为周期增量
                period = DailyDigestCore._to_int(obj.get("stars"))
            desc = str(obj.get("description") or "").strip()
            language = str(obj.get("language") or "").strip()
            title = f"{name} ⭐{stars}（近{days}天+{period}）" if period else f"{name} ⭐{stars}"
            out.append(
                {
                    "name": name,
                    "title": title,
                    "link": str(url).strip(),
                    "description": desc[:200],
                    "language": language,
                    "stars": stars,
                    "period_stars": period,
                }
            )
        return out

    @staticmethod
    def _find_repo_list(data) -> list:
        """从 dict 的 items/repos/results/data 或顶层 list 中找数组。"""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "repos", "results"):
                v = data.get(key)
                if isinstance(v, list):
                    return v
            for key in ("data", "payload"):
                v = data.get(key)
                if isinstance(v, dict):
                    for key2 in ("items", "repos", "results"):
                        v2 = v.get(key2)
                        if isinstance(v2, list):
                            return v2
                    if isinstance(v, list):
                        return v
        return []

    @staticmethod
    def _to_int(v) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _http_get_bytes(
        url: str, timeout: int = 15, headers: dict | None = None
    ) -> bytes:
        hdrs = {
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def _parse_feed(self, data: bytes) -> list[dict]:
        """解析 RSS 2.0 / RDF / Atom，返回条目列表。"""
        root = ET.fromstring(data)
        items: list[dict] = []
        for el in root.iter():
            if _local_name(el.tag) not in ("item", "entry"):
                continue
            item: dict = {"title": "", "link": "", "pub_date": None, "summary": ""}
            for child in el:
                name = _local_name(child.tag)
                if name == "title":
                    item["title"] = _strip_html(child.text or "")
                elif name == "link":
                    href = child.get("href")
                    if href:
                        item["link"] = href.strip()
                    elif child.text and child.text.strip():
                        item["link"] = child.text.strip()
                elif name in ("pubDate", "published", "updated", "date"):
                    if item["pub_date"] is None:
                        item["pub_date"] = _parse_date(child.text or "")
                elif name in ("description", "summary", "encoded", "content"):
                    item["summary"] = _strip_html(child.text or "")[:200]
            if item["title"]:
                items.append(item)
        return items

    def _filter_yesterday(
        self, items: list[dict], recent_days: int = 0
    ) -> list[dict]:
        """严格时效过滤：只保留「昨日」条目；不足时回退到近 24 小时。
        recent_days > 0 时（政策/AI/网安等低频板块）放宽为「最近 N 天」，
        因为政策文件不是每天都有、AI 与网安源更新不规律，硬卡「昨日」会让
        整个板块经常空白；此时窗口末端取「现在」，当天新发的条目也算在内。
        注意：不回退到更早的条目——死源（如停更的 RSS）不会把陈旧内容当「昨日」展示。"""
        now = datetime.now().astimezone()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)
        cutoff_24h = now - timedelta(hours=24)
        # 放宽窗口时窗口末端必须是「现在」而不是今天 0 点：AI / 网安这类板块的
        # 新闻当天就发，若末端卡在今天 0 点，早上 8 点跑的日报会把当天所有条目
        # 全部丢掉（只剩昨天以前），板块看起来像「没新闻」。
        window_end = today_start
        if recent_days > 0:
            yesterday_start = now - timedelta(days=recent_days)
            window_end = now + timedelta(minutes=5)

        dated: list[dict] = []
        undated: list[dict] = []
        for it in items:
            d = it.get("pub_date")
            if d is None:
                undated.append(it)
                continue
            if d.tzinfo is None:
                d = d.replace(tzinfo=now.tzinfo)
            it["pub_date"] = d
            dated.append(it)
        dated.sort(key=lambda x: x["pub_date"], reverse=True)

        hit = [it for it in dated if yesterday_start <= it["pub_date"] < window_end]
        if recent_days <= 0 and len(hit) < 2:
            hit24 = [it for it in dated if it["pub_date"] >= cutoff_24h]
            if len(hit24) > len(hit):
                hit = hit24
        return hit + undated

    # ------------------------------------------------------------------ #
    # 天气（Open-Meteo，免费免 Key；多城市 + 防高并发）
    #   - 天气结果 KV 缓存 weather_cache_minutes 分钟
    #   - 地理编码结果 KV 缓存 7 天
    #   - 每城市一把 asyncio 单飞锁（同城市并发去重）
    #   - 全局请求间隔节流 weather_interval_seconds 秒 + 每日上限
    #   - 失败隔离：单城市失败只在该城市输出「获取失败」
    # ------------------------------------------------------------------ #
    def _weather_cities(self) -> list[str]:
        raw = str(self.config.get("weather_city") or "上海").strip()
        parts = re.split(r"[\n,，、;；]+", raw)
        return [p.strip() for p in parts if p.strip()] or ["上海"]

    async def _fetch_weather(self) -> str | None:
        cities = self._weather_cities()
        if not cities:
            return None
        cache_minutes = self._cfg_int("weather_cache_minutes", 30)
        try:
            cache = (await self.get_kv_data("weather_cache", None)) or {}
            if (
                cache.get("cities") == cities
                and time.time() - (cache.get("ts") or 0) < cache_minutes * 60
                and cache.get("text")
            ):
                return cache["text"]
        except Exception:
            pass

        lines = ["🌤️ 天气"]
        ok = False
        for city in cities:
            try:
                block = await self._fetch_city_weather(city)
            except Exception as e:
                logger.warning(f"[daily_digest] 天气获取失败（{city}）: {e}")
                block = None
            if block:
                lines.append(block)
                ok = True
            else:
                lines.append(f"→【{city}】获取失败")
        if not ok:
            return None  # 全部城市失败 -> 板块省略
        text = "\n".join(lines)
        try:
            await self.put_kv_data(
                "weather_cache", {"cities": cities, "ts": time.time(), "text": text}
            )
        except Exception:
            pass
        return text

    async def _fetch_city_weather(self, city: str) -> str | None:
        """单城市天气。失败抛异常或返回 None，由调用方做失败隔离。"""
        lock = self._weather_locks.setdefault(city, asyncio.Lock())
        async with lock:  # 单飞锁：同城市并发只发一个请求
            loc = await self._geocode(city)
            if loc is None:
                return None
            await self._weather_throttle()
            fc_url = (
                "https://api.open-meteo.com/v1/forecast?"
                + urllib.parse.urlencode(
                    {
                        "latitude": loc["lat"],
                        "longitude": loc["lon"],
                        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
                        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
                        "timezone": "auto",
                        "forecast_days": 2,
                    }
                )
            )
            fc = json.loads(await asyncio.to_thread(self._http_get_bytes, fc_url))
            cur = fc.get("current") or {}
            daily = fc.get("daily") or {}
            times = daily.get("time") or []

            parts = [
                f"现在：{self._wmo(cur.get('weather_code'))}，"
                f"{cur.get('temperature_2m')}°C（体感 {cur.get('apparent_temperature')}°C），"
                f"湿度 {cur.get('relative_humidity_2m')}%，"
                f"风速 {cur.get('wind_speed_10m')}km/h"
            ]
            if len(times) >= 1:
                parts.append(
                    f"今日：{self._wmo(daily.get('weather_code', [None])[0])}，"
                    f"{daily.get('temperature_2m_min', [None])[0]}~"
                    f"{daily.get('temperature_2m_max', [None])[0]}°C，"
                    f"降水概率 {daily.get('precipitation_probability_max', [None])[0]}%"
                )
            if len(times) >= 2:
                parts.append(
                    f"明日：{self._wmo(daily.get('weather_code', [None])[1])}，"
                    f"{daily.get('temperature_2m_min', [None])[1]}~"
                    f"{daily.get('temperature_2m_max', [None])[1]}°C"
                )
            return f"→【{loc.get('name', city)}】" + "；".join(parts)

    async def _geocode(self, city: str) -> dict | None:
        """地理编码（KV 缓存 7 天），返回 {"lat","lon","name"} 或 None。"""
        try:
            geo_cache = (await self.get_kv_data("geo_cache", None)) or {}
        except Exception:
            geo_cache = {}
        entry = geo_cache.get(city)
        if entry and time.time() - (entry.get("ts") or 0) < 7 * 86400:
            return entry

        await self._weather_throttle()
        geo_url = (
            "https://geocoding-api.open-meteo.com/v1/search?"
            + urllib.parse.urlencode(
                {"name": city, "count": 1, "language": "zh", "format": "json"}
            )
        )
        geo = json.loads(await asyncio.to_thread(self._http_get_bytes, geo_url))
        results = geo.get("results") or []
        if not results:
            return None
        loc = results[0]
        entry = {
            "lat": loc["latitude"],
            "lon": loc["longitude"],
            "name": loc.get("name", city),
            "ts": time.time(),
        }
        try:
            geo_cache[city] = entry
            await self.put_kv_data("geo_cache", geo_cache)
        except Exception:
            pass
        return entry

    async def _weather_throttle(self) -> None:
        """全局节流：相邻两次 Open-Meteo 调用间隔 >= interval 秒，并计入每日上限。"""
        interval = max(0, self._cfg_int("weather_interval_seconds", 3))
        today = datetime.now().strftime("%Y-%m-%d")
        if self._weather_call_day != today:
            self._weather_call_day = today
            self._weather_call_count = 0
        if self._weather_call_count >= WEATHER_DAILY_CALL_LIMIT:
            raise RuntimeError("今日天气 API 调用次数已达上限")
        wait = self._last_weather_call + interval - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_weather_call = time.time()
        self._weather_call_count += 1

    @staticmethod
    def _wmo(code) -> str:
        try:
            code = int(code)
        except (TypeError, ValueError):
            return "未知"
        return WMO_CODES.get(code, f"天气代码{code}")

    # ------------------------------------------------------------------ #
    # AI 总结（优先新 API，回退旧 API）
    # ------------------------------------------------------------------ #
    async def _ai_summarize(self, weather: str | None, sections: dict) -> str:
        lines: list[str] = []
        if weather:
            lines.append(weather)
            lines.append("")
        for _enabled_key, feed_key, label, _emoji in SECTIONS:
            items = sections.get(feed_key) or []
            if not items:
                continue
            lines.append(f"## {label}")
            for it in items:
                line = f"- {it['title']}"
                detail = str(it.get("description") or it.get("summary") or "").strip()
                if detail:
                    line += f"：{detail[:200]}"
                lang = str(it.get("language") or "").strip()
                if lang:
                    line += f"（语言：{lang}）"
                if it.get("link"):
                    line += f"（{it['link']}）"
                lines.append(line)
        if not lines:
            raise RuntimeError("没有抓到任何内容")

        prompt = (self.config.get("llm_prompt") or DIGEST_DEFAULT_PROMPT).replace(
            "{date}", datetime.now().strftime("%Y-%m-%d")
        ).replace("{data}", "\n".join(lines))
        prompt = self._normalize_prompt(prompt)
        text = await self._llm_chat(prompt)
        if not text:
            raise RuntimeError("LLM 返回为空")
        if self._is_rejection(text):
            # 内容安全拦截：用「仅标题+链接」的精简数据重试一次（去掉描述/摘要，降低触发概率）
            logger.warning("[toolkit] AI 返回被内容安全拦截，改用精简数据重试一次")
            reduced = self._build_reduced_data(weather, sections)
            if reduced:
                prompt2 = (self.config.get("llm_prompt") or DIGEST_DEFAULT_PROMPT).replace(
                    "{date}", datetime.now().strftime("%Y-%m-%d")
                ).replace("{data}", reduced)
                prompt2 = self._normalize_prompt(prompt2)
                text2 = await self._llm_chat(prompt2)
                if text2 and not self._is_rejection(text2):
                    return text2
            raise RuntimeError("LLM 连续被内容安全拦截，降级为模板日报")
        return text

    @staticmethod
    def _build_reduced_data(weather: str | None, sections: dict) -> str:
        """精简数据：只保留标题+链接，每板块最多 3 条（降低被内容安全拦截的概率）。"""
        lines: list[str] = []
        if weather:
            lines.append(weather)
            lines.append("")
        for _enabled_key, feed_key, label, _emoji in SECTIONS:
            items = sections.get(feed_key) or []
            if not items:
                continue
            lines.append(f"## {label}")
            for it in items[:3]:
                line = f"- {it['title']}"
                if it.get("link"):
                    line += f"（{it['link']}）"
                lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _is_rejection(text: str) -> bool:
        """检测 LLM 返回是否为内容安全拦截/拒绝（大小写不敏感）。"""
        low = (text or "").lower()
        markers = (
            "high risk",
            "was rejected",
            "rejected because",
            "content policy",
            "moderation",
            "risk control",
            "safety check",
            "safe check",
            "the request was rejected",
        )
        if any(m in low for m in markers):
            return True
        cn_markers = (
            "安全风险", "内容安全", "被拒绝", "违规", "敏感内容",
            "不予处理", "拒绝回答", "无法满足该请求", "无法处理该请求",
        )
        return any(m in text for m in cn_markers)

    @staticmethod
    def _normalize_prompt(prompt: str) -> str:
        """规范化提示词：兼容旧版配置。
        - 旧版「1200 字内」升级为「1800 字内」；
        - 旧版「（仅输出实际有的板块）」替换为「必须全部输出」；
        - 末尾强制追加一条指令：{data} 里出现的每个板块（含 🚀 GitHub 日升榜 等）都必须输出，
          防止旧提示词板块清单缺失时 AI 以「未列入板块」为由漏掉 GitHub 等板块。
        """
        p = prompt.replace(
            "总长度控制在 1200 字内",
            "总长度控制在 1800 字内",
        )
        p = p.replace(
            "（仅输出实际有的板块）",
            "（有数据的板块必须全部输出，不要遗漏任何板块）",
        )
        p = p.rstrip() + (
            "\n\n【强制要求】下方 {data} 中出现的每一个板块（包括但不限于："
            "🌤️ 天气、🇨🇳 昨日国内、🌍 昨日国际、💻 科技前沿、"
            "🤖 AI 界的新变化、🛡️ 网安漏洞与情报、💊 医药前沿、"
            "📜 政策前沿、🚀 GitHub 日升榜）都必须输出；只有某个板块在 {data} 中完全无数据时才可跳过。"
        )
        return p

    async def _llm_chat(self, prompt: str) -> str:
        ctx = self.context

        # 1) AstrBot 4.x：context.llm_generate(chat_provider_id=...)
        try:
            prov_id = None
            get_using = getattr(ctx, "get_using_provider_async", None)
            if callable(get_using):
                prov = await get_using()
                meta = getattr(prov, "meta", None) if prov else None
                if callable(meta):
                    prov_id = getattr(meta(), "id", None)
            if prov_id and callable(getattr(ctx, "llm_generate", None)):
                resp = await ctx.llm_generate(
                    chat_provider_id=prov_id,
                    prompt=prompt,
                    system_prompt="你是一名严谨、简洁的中文每日简报编辑。",
                )
                text = self._llm_text(resp)
                if text:
                    return text
        except Exception as e:
            logger.debug(f"[daily_digest] llm_generate 调用失败: {e}")

        # 2) 旧版：context.get_using_provider() + provider.text_chat()
        try:
            get_prov = getattr(ctx, "get_using_provider", None)
            if callable(get_prov):
                prov = get_prov()
                if prov is not None and callable(getattr(prov, "text_chat", None)):
                    resp = await prov.text_chat(
                        prompt=prompt, session_id=None, image_urls=[]
                    )
                    text = self._llm_text(resp)
                    if text:
                        return text
        except Exception as e:
            logger.debug(f"[daily_digest] text_chat 调用失败: {e}")

        raise RuntimeError("无法获取可用的 LLM 提供商")

    @staticmethod
    def _llm_text(resp) -> str:
        if resp is None:
            return ""
        for attr in ("completion_text", "result", "text"):
            try:
                v = getattr(resp, attr, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            except Exception:
                continue
        try:
            rc = getattr(resp, "result_chain", None)
            if rc is not None and hasattr(rc, "get_plain_text"):
                t = rc.get_plain_text()
                if t:
                    return t
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------ #
    # 模板简报（AI 不可用时的降级方案）
    # ------------------------------------------------------------------ #
    def _template_digest(self, weather: str | None, sections: dict) -> str:
        lines = [f"📰 每日简报 · {datetime.now().strftime('%Y-%m-%d %A')}"]
        if weather:
            lines.append("")
            lines.append(weather)
        max_items = self._cfg_int("max_items_per_section", 5)
        for _enabled_key, feed_key, label, emoji in SECTIONS:
            items = sections.get(feed_key) or []
            if not items:
                continue
            lines.append("")
            lines.append(f"{emoji} {label}")
            for i, it in enumerate(items[:max_items], 1):
                lines.append(f"{i}. {it['title']}")
                detail = str(it.get("description") or it.get("summary") or "").strip()
                lang = str(it.get("language") or "").strip()
                tail_parts = []
                if detail:
                    tail_parts.append(detail[:120])
                if lang:
                    tail_parts.append(f"语言：{lang}")
                if tail_parts:
                    lines.append("   " + "；".join(tail_parts))
                if it.get("link"):
                    lines.append(f"   🔗 {it['link']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 发送
    # ------------------------------------------------------------------ #
    async def _send_text(self, umo: str, text: str) -> None:
        chunks = self._split_text(text)
        for idx, chunk in enumerate(chunks):
            try:
                chain = MessageChain().message(chunk)
                ok = await self._send_to_session(umo, chain)
                if not ok:
                    logger.warning(
                        f"[daily_digest] 发送到 {umo} 失败：未找到对应平台"
                    )
            except Exception as e:
                logger.error(f"[daily_digest] 发送到 {umo} 失败: {e}")
            if idx < len(chunks) - 1:
                await asyncio.sleep(0.8)

    async def _send_to_session(self, umo: str, chain: MessageChain) -> bool:
        """发送消息。兼容新旧两种 send_message 签名：
        新（AstrBot 4.x）: send_message(session: str|MessageSesion, chain)
        旧（AstrBot 3.x）: send_message(platform_name, chain, target_id, is_group=...)
        """
        ctx = self.context
        try:
            sig = inspect.signature(ctx.send_message)
            positional = [
                p
                for p in sig.parameters.values()
                if p.kind
                in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
            if len(positional) >= 4:
                parts = umo.split(":")
                platform_name = parts[0] if parts else umo
                target = parts[2] if len(parts) > 2 else ""
                is_group = "group" in umo.lower() or len(parts) > 3
                return await self._gated_send(platform_name, chain, target, is_group)
        except Exception as e:
            logger.debug(f"[daily_digest] 旧版 send_message 适配失败，改用新版调用: {e}")
        return await self._gated_send(umo, chain)

    # ------------------------------------------------------------------ #
    # 工具方法
    # ------------------------------------------------------------------ #
    def _cfg_bool(self, key: str, default: bool) -> bool:
        v = self.config.get(key, default)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _split_text(text: str, limit: int = 3800) -> list[str]:
        text = text.strip()
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        cur = ""
        for line in text.splitlines(keepends=True):
            while len(line) > limit:
                # 单行超长：先补满当前块，再按 limit 拆行
                if cur:
                    chunks.append(cur)
                    cur = ""
                chunks.append(line[:limit])
                line = line[limit:]
            if cur and len(cur) + len(line) > limit:
                chunks.append(cur)
                cur = line
            else:
                cur += line
        if cur:
            chunks.append(cur)
        return chunks

# ===========================================================================
# 核心三：主动消息门禁 + 每日定时人格消息
# （原 astrbot-plugin-proactive-guard.ProactiveGuardPlugin）
# 门禁：拦截非本插件的 AI 主动发言；每日预生成随机时间点人格消息并自动发送。
# 合并说明：本插件内部主动发送（guard 人格消息、digest 日报）通过
# owner._bypass_cnt 放行；对外部插件/内置 Agent 的主动发言仍一律拦截。
# ===========================================================================
class ProactiveGuardCore(_PluginCoreBase):
    """主动消息门禁 + 每日定时人格消息"""

    def __init__(self, owner: "ElainaSuitePlugin") -> None:
        super().__init__(owner)
        self._orig_send_message = None
        self._cron_job_ids: list[str] = []
        self._fallback_sched = None
        self._last_user_activity: dict[str, float] = {}
        self._gen_lock = asyncio.Lock()
        # 记忆刷新互斥：定时任务、生成前冷却刷新、/刷新记忆 可能同时触发
        self._memory_lock = asyncio.Lock()
        self._last_gen_attempt = 0.0
        self._migrated = False
        # 阶段一：living 调度（插件内部调度器 + 每会话决策循环）
        self._living_sched = None
        self._living_locks: dict[str, asyncio.Lock] = {}
        self._living_rng = random.Random()

    # ================================================================== #
    # 生命周期
    # ================================================================== #
    async def initialize(self) -> None:
        """插件激活时：安装门禁 -> 暂停内置主动任务 -> 启动调度。

        living_enabled 开启时用插件内部 AsyncIOScheduler（阶段一），
        完全不注册 AstrBot cron_manager —— 避免两套分钟任务各发一遍。
        关闭时保持旧行为（cron_manager + 每日预生成计划）。
        """
        if self._cfg_bool("pg_enabled", True):
            if self._cfg_bool("block_proactive", True):
                self._install_gate()
            await self._pause_active_agent_jobs()
            if self._living_on():
                await self._living_start()
            else:
                await self._setup_scheduler()

    async def terminate(self) -> None:
        """插件禁用/重载时：卸载门禁、停止调度（living 与旧 cron 两套都清）"""
        ctx = self.context
        try:
            await self._living_stop()
        except Exception as e:
            logger.warning(f"[living] 停止调度失败: {e}")
        if getattr(ctx, "_proactive_guard_installed", False):
            try:
                ctx.send_message = self._orig_send_message
                setattr(ctx, "_proactive_guard_installed", False)
                logger.info("[proactive_guard] 已卸载主动消息门禁")
            except Exception as e:
                logger.warning(f"[proactive_guard] 卸载门禁失败: {e}")
        try:
            cm = getattr(self.context, "cron_manager", None)
            if cm is not None and hasattr(cm, "delete_job"):
                for jid in self._cron_job_ids:
                    if jid:
                        try:
                            await cm.delete_job(jid)
                        except Exception:
                            pass
        except Exception:
            pass
        try:
            if self._fallback_sched is not None:
                self._fallback_sched.shutdown(wait=False)
                self._fallback_sched = None
        except Exception:
            pass

    # ================================================================== #
    # 门禁：拦截非本插件的主动发送
    # ================================================================== #
    def _install_gate(self) -> None:
        ctx = self.context
        if getattr(ctx, "_proactive_guard_installed", False):
            return
        orig = ctx.send_message
        self._orig_send_message = orig

        async def wrapped(session, message_chain, *args, **kwargs):
            if self._allow_send(session):
                return await orig(session, message_chain, *args, **kwargs)
            try:
                caller = self._caller_module()
                logger.warning(
                    f"[proactive_guard] 已拦截非白名单主动消息 -> {session}（来源模块: {caller}）"
                )
            except Exception:
                pass
            return False

        ctx.send_message = wrapped
        setattr(ctx, "_proactive_guard_installed", True)
        logger.info("[proactive_guard] 主动消息门禁已安装（仅放行本插件/白名单/活跃会话回复）")

    def _allow_send(self, session) -> bool:
        if self.owner._bypass_cnt > 0:
            return True
        caller = self._caller_module()
        for prefix in self._allowlist():
            if prefix and prefix in caller:
                return True
        if not self._cfg_bool("strict_mode", False):
            try:
                s = str(session) if session else ""
                if s and self._recent_user_activity(s):
                    return True
            except Exception:
                pass
        return False

    def _allowlist(self) -> list[str]:
        val = self.config.get("allow_senders")
        if val and str(val).strip():
            return [s.strip() for s in str(val).splitlines() if s.strip()]
        # 默认放行 daily_digest（日报）——合并插件中日报主动发送通过
        # owner._bypass_cnt 直接放行，此处白名单仍保留以兼容用户自定义配置
        # （如放行其他外部插件模块名）。
        return ["daily_digest"]

    def _caller_module(self) -> str:
        # 合并插件内所有核心代码都在同一模块（main）；跳过本插件模块自身的帧，
        # 返回第一个外部调用者模块名（用于白名单匹配与拦截日志）。
        my_mod = sys.modules.get(self.__class__.__module__, None)
        frame = inspect.currentframe()
        while frame is not None:
            mod = frame.f_globals.get("__name__", "") or ""
            if my_mod is not None and frame.f_globals is getattr(
                my_mod, "__dict__", None
            ):
                frame = frame.f_back
                continue
            if mod:
                return mod
            frame = frame.f_back
        return "unknown"

    # ------------------------------------------------------------------ #
    # 用户活跃度记录（用于区分「回复用户」与「主动发言」）
    # ------------------------------------------------------------------ #
    async def _on_user_message(self, event: AstrMessageEvent):
        """观察所有用户消息，记录会话最近活跃时间（不产生任何回复）。"""
        try:
            umo = getattr(event, "unified_msg_origin", "") or ""
            if umo:
                self._last_user_activity[umo] = time.time()
                if len(self._last_user_activity) > 500:
                    # 简单裁剪：删除最早的一半
                    items = sorted(
                        self._last_user_activity.items(), key=lambda kv: kv[1]
                    )
                    self._last_user_activity = dict(items[len(items) // 2 :])
                # 阶段一：把用户活跃喂给 living 决策循环（取消待触发 + 推后下次决策）
                if self._living_on():
                    await self._living_on_user_message(umo)
        except Exception:
            pass

    def _recent_user_activity(self, session_str: str) -> bool:
        last = self._last_user_activity.get(session_str)
        if not last:
            return False
        window = self._cfg_int("active_window_minutes", 30) * 60
        return time.time() - last <= window

    # ------------------------------------------------------------------ #
    # 暂停 AstrBot 内置「主动型 Agent」定时任务（可逆）
    # ------------------------------------------------------------------ #
    async def _pause_active_agent_jobs(self) -> None:
        if not self._cfg_bool("pause_active_agent_jobs", True):
            return
        try:
            cm = getattr(self.context, "cron_manager", None)
            if cm is None or not hasattr(cm, "list_jobs"):
                return
            jobs = await cm.list_jobs()
            paused = 0
            for job in jobs:
                jt = getattr(job, "job_type", "") or ""
                enabled = getattr(job, "enabled", True)
                if jt == "active_agent" and enabled:
                    try:
                        await cm.update_job(getattr(job, "job_id", ""), enabled=False)
                        paused += 1
                    except Exception as e:
                        logger.warning(f"[proactive_guard] 暂停主动任务失败: {e}")
            if paused:
                logger.info(f"[proactive_guard] 已暂停 {paused} 个主动型 Agent 定时任务（可恢复）")
        except Exception as e:
            logger.warning(f"[proactive_guard] 暂停主动任务出错: {e}")

    # ================================================================== #
    # 定时任务：每日生成 + 每分钟检查
    # ================================================================== #
    async def _setup_scheduler(self) -> None:
        # 默认 03:20：排在 03:00 的记忆刷新之后，保证「生成用的记忆」是刚刷过的
        gen_cron = str(self.config.get("gen_time") or "20 3 * * *").strip()
        tz = str(self.config.get("pg_timezone") or "Asia/Shanghai").strip()
        cm = getattr(self.context, "cron_manager", None)
        if cm is not None and hasattr(cm, "add_basic_job"):
            try:
                try:
                    j1 = await cm.add_basic_job(
                        name="proactive_guard_gen",
                        cron_expression=gen_cron,
                        handler=self._on_gen_time,
                        description="每日生成人格消息计划（后台）",
                        enabled=True,
                        persistent=False,
                        timezone=tz,
                    )
                except TypeError:
                    # 旧版 cron_manager 不支持 timezone 参数
                    j1 = await cm.add_basic_job(
                        name="proactive_guard_gen",
                        cron_expression=gen_cron,
                        handler=self._on_gen_time,
                        description="每日生成人格消息计划（后台）",
                        enabled=True,
                        persistent=False,
                    )
                self._cron_job_ids.append(getattr(j1, "job_id", None))
                try:
                    j2 = await cm.add_basic_job(
                        name="proactive_guard_minute",
                        cron_expression="* * * * *",
                        handler=self._on_minute,
                        description="每分钟检查待发送的人格消息",
                        enabled=True,
                        persistent=False,
                        timezone=tz,
                    )
                except TypeError:
                    j2 = await cm.add_basic_job(
                        name="proactive_guard_minute",
                        cron_expression="* * * * *",
                        handler=self._on_minute,
                        description="每分钟检查待发送的人格消息",
                        enabled=True,
                        persistent=False,
                    )
                self._cron_job_ids.append(getattr(j2, "job_id", None))
                if self._cfg_bool("memory_enabled", True):
                    mem_cron = str(
                        self.config.get("memory_cron") or "0 3 * * *"
                    ).strip()
                    try:
                        j3 = await cm.add_basic_job(
                            name="proactive_guard_memory",
                            cron_expression=mem_cron,
                            handler=self._on_memory_time,
                            description="把近期对话总结进记忆库（后台静默）",
                            enabled=True,
                            persistent=False,
                            timezone=tz,
                        )
                    except TypeError:
                        j3 = await cm.add_basic_job(
                            name="proactive_guard_memory",
                            cron_expression=mem_cron,
                            handler=self._on_memory_time,
                            description="把近期对话总结进记忆库（后台静默）",
                            enabled=True,
                            persistent=False,
                        )
                    self._cron_job_ids.append(getattr(j3, "job_id", None))
                    logger.info(f"[proactive_guard] 记忆总结任务已注册: {mem_cron}（时区 {tz}）")
                logger.info(
                    f"[proactive_guard] 定时任务已注册（cron_manager）: 生成 {gen_cron} "
                    f"/ 分钟检查 * * * * *（时区 {tz}）"
                )
                return
            except Exception as e:
                logger.warning(f"[proactive_guard] cron_manager 注册失败，回退 APScheduler: {e}")
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger

            self._fallback_sched = AsyncIOScheduler(timezone=tz)
            self._fallback_sched.add_job(
                self._on_gen_time,
                CronTrigger.from_crontab(gen_cron, timezone=tz),
                id="pg_gen",
                misfire_grace_time=300,
            )
            self._fallback_sched.add_job(
                self._on_minute,
                CronTrigger.from_crontab("* * * * *", timezone=tz),
                id="pg_min",
                misfire_grace_time=30,
            )
            if self._cfg_bool("memory_enabled", True):
                mem_cron = str(self.config.get("memory_cron") or "0 3 * * *").strip()
                self._fallback_sched.add_job(
                    self._on_memory_time,
                    CronTrigger.from_crontab(mem_cron, timezone=tz),
                    id="pg_mem",
                    misfire_grace_time=600,
                )
            self._fallback_sched.start()
            logger.info(
                f"[proactive_guard] 定时任务已注册（APScheduler）: 生成 {gen_cron} "
                f"/ 分钟检查（时区 {tz}）"
            )
        except Exception as e:
            logger.error(f"[proactive_guard] 定时任务注册失败: {e}")

    async def _on_gen_time(self) -> None:
        """到生成时间：先刷记忆 → 必要时重建今日剩余计划 → 再预生成明日计划。

        顺序很重要：预生成机制下今天的计划是昨天写的，可能比记忆旧十几个小时。
        所以这里先强制刷一次记忆（不靠 30 分钟冷却），再拿新记忆去校正今天还没发的那几条。
        """
        try:
            if self._cfg_bool("memory_enabled", True) and self._cfg_bool(
                "memory_refresh_before_gen", True
            ):
                try:
                    await self.refresh_memory()
                except Exception as e:
                    logger.warning(f"[proactive_guard] 生成前刷新记忆失败: {e}")
            if self._cfg_bool("dispatch_regenerate_on_memory_change", True):
                try:
                    # 顺序：先把昨日预生成的那份提升为今日计划，再检查它是否基于旧记忆。
                    # 反过来会漏：提升发生在检查之后，等于永远检查的是「昨天那份」。
                    await self._ensure_schedule(force=False)
                    await self._regenerate_today_if_stale()
                    return
                except Exception as e:
                    logger.warning(f"[proactive_guard] 重建今日计划失败: {e}")
            await self._ensure_schedule(force=False)
        except Exception as e:
            logger.error(f"[proactive_guard] 生成计划失败: {e}", exc_info=True)

    async def _on_minute(self) -> None:
        """每分钟：发送到点的消息，并兜底补种计划。"""
        try:
            await self._dispatch_due()
            await self._ensure_schedule(force=False)
        except Exception as e:
            logger.error(f"[proactive_guard] 分钟检查任务失败: {e}", exc_info=True)

    # ================================================================== #
    # 每日计划：生成 / 存储 / 发送 / 删除
    # ================================================================== #
    async def _migrate_old_schedule(self) -> None:
        """v1.0.0 -> v1.0.1 迁移：把旧键 daily_schedule 拆进双槽位后删除旧键。"""
        if self._migrated:
            return
        self._migrated = True
        try:
            old = await self.get_kv_data("daily_schedule", None)
            if not old:
                return
            today = datetime.now().strftime("%Y-%m-%d")
            tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            d = old.get("date")
            if d == today and old.get("items"):
                await self.put_kv_data("daily_plan", old)
            elif d == tomorrow and old.get("items"):
                await self.put_kv_data("daily_plan_next", old)
            await self.put_kv_data("daily_schedule", None)  # 清掉旧键
            logger.info("[proactive_guard] 已迁移旧版 daily_schedule 到新双槽位")
        except Exception as e:
            logger.warning(f"[proactive_guard] 旧计划迁移失败: {e}")

    async def _ensure_schedule(self, force: bool) -> None:
        """确保：daily_plan.date == 今天，daily_plan_next.date == 明天。"""
        if not self._cfg_bool("pg_enabled", True):
            return
        await self._migrate_old_schedule()
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        # a) 今天的槽位过期：提升 daily_plan_next 或丢弃
        plan = await self._load_schedule("daily_plan")
        if plan.get("date") != today or not plan.get("items"):
            next_plan = await self._load_schedule("daily_plan_next")
            if next_plan.get("date") == today and next_plan.get("items"):
                await self.put_kv_data("daily_plan", next_plan)
                await self.put_kv_data("daily_plan_next", {})
                logger.info("[proactive_guard] 已将预生成的明日计划提升为今日计划")
            else:
                await self.put_kv_data("daily_plan", {})  # 过期计划静默丢弃

        # b) 明天的槽位缺失：生成明天的计划
        next_plan = await self._load_schedule("daily_plan_next")
        if next_plan.get("date") != tomorrow or not next_plan.get("items"):
            if not force and time.time() - self._last_gen_attempt < 20 * 60:
                return  # 20 分钟节流
            async with self._gen_lock:
                next_plan = await self._load_schedule("daily_plan_next")
                if next_plan.get("date") == tomorrow and next_plan.get("items"):
                    return
                self._last_gen_attempt = time.time()
                await self._generate_and_store(target_date=tomorrow)

    async def _generate_and_store(
        self, target_date: str, times: list[str] | None = None
    ) -> None:
        """后台为指定日期生成计划并写入 KV 存储（静默，不通知用户）。

        目标日期为明天 -> 存 daily_plan_next（预生成）；
        目标日期为今天 -> 存 daily_plan（/重建今日计划 在窗口未结束时补今天的场景）。

        times 显式给出时只用这些时间点（重建「今日剩余计划」时用来保留原有排期）。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        slot = "daily_plan" if target_date == today else "daily_plan_next"
        if times:
            times = [self._normalize_time(str(t)) for t in times if str(t).strip()]
            count = len(times)
        else:
            count = random.randint(
                self._cfg_int("msg_count_min", 5), self._cfg_int("msg_count_max", 10)
            )
            times = self._random_times(count)
        time_list = "、".join(times)
        persona = await self._resolve_persona()
        world_block = self._world_block()
        share_ratio = self._share_ratio()
        share_count, care_count = self._share_counts(count, share_ratio)

        # ---- 记忆：从会话历史里捞近期真实对话 ----
        memory = await self._collect_memory()
        memory_block = ""
        if memory:
            memory_block = MEMORY_BLOCK_TEMPLATE.format(memory=memory)
            logger.debug(
                f"[proactive_guard] 已读取记忆 {len(memory)} 字，用于生成（{target_date}）"
            )

        subagent_on = self._cfg_bool("subagent_enabled", True)
        items: list[dict] = []
        if subagent_on:
            items = await self._generate_with_subagents(
                target_date,
                times,
                time_list,
                count,
                persona,
                memory_block,
                world_block=world_block,
                share_count=share_count,
                care_count=care_count,
            )
        if not items:
            # 兜底：单次调用（v1.0.x 行为）。子代理关闭、或规划代理失败/未产出要点时走这里，
            # 保证「回退单次生成」这句日志名副其实（不再拿到空列表就直接报错）。
            if subagent_on:
                logger.warning(
                    f"[proactive_guard] 子代理流水线未产出结果，回退单次生成（{target_date}）"
                )
            prompt = (
                (self.config.get("message_prompt") or GUARD_DEFAULT_PROMPT)
                # 老模板写的是「你是{persona}」，而 persona 本身以「你是一个…」开头，
                # 直接替换会得到「你是你是一个…」。先把这种写法整体吃掉。
                .replace("你是{persona}", persona)
                .replace("{world_block}", world_block)
                .replace("{persona}", persona)
                .replace("{share_ratio}", str(share_ratio))
                .replace("{date}", target_date)
                .replace("{time_list}", time_list)
                .replace("{count}", str(count))
            )
            if memory_block:
                prompt = f"{prompt}\n{memory_block}"
            try:
                text = await self._llm_chat(prompt)
                items = self._parse_items(text, times)
            except Exception as e:
                # 兜底也失败（例如 LLM 全挂）：只记日志，不把异常抛给调度器
                logger.warning(f"[proactive_guard] 兜底单次生成失败（{target_date}）: {e}")
                items = []
        if not items:
            logger.error(
                f"[proactive_guard] 计划生成失败（{target_date}）：无法解析 LLM 输出，稍后重试"
            )
            return
        # 记录「这份计划是基于哪一版记忆生成的」。派发时会拿它和当前记忆比对，
        # 不一致就说明记忆在生成之后又更新过，需要在发送前复核（见 _dispatch_due）。
        memory_updated = await self._memory_updated()
        await self.put_kv_data(
            slot,
            {
                "date": target_date,
                "items": items,
                "memory_updated": memory_updated,
            },
        )
        logger.info(
            f"[proactive_guard] 计划已生成（{target_date}）：{len(items)} 条消息 "
            f"@ {time_list}（后台静默，记忆 {'有' if memory else '无'}，"
            f"子代理 {'开' if subagent_on else '关'}）"
        )

    async def _resolve_persona(self) -> str:
        """获取 AstrBot 当前人格设定（不单独配置）。"""
        pm = getattr(self.context, "persona_manager", None)
        if pm is not None:
            try:
                # v3 人格（AstrBot 4.x 默认）：按配置的 default_personality 解析
                get_default = getattr(pm, "get_default_persona_v3", None)
                if callable(get_default):
                    p = await get_default()
                    text = self._persona_text(p)
                    if text:
                        return text
                # 兜底：初始化时选中的 v3 人格 / v2 人格
                for attr in ("selected_default_persona_v3", "selected_default_persona"):
                    p = getattr(pm, attr, None)
                    if p is not None:
                        text = self._persona_text(p)
                        if text:
                            return text
            except Exception as e:
                logger.debug(f"[proactive_guard] 读取 AstrBot 人格失败: {e}")
        return FALLBACK_PERSONA

    @staticmethod
    def _persona_text(p) -> str:
        """从 Personality / Persona 对象中提取设定文本。"""
        if p is None:
            return ""
        prompt = ""
        name = ""
        try:
            prompt = getattr(p, "prompt", None) or (
                p.get("prompt") if isinstance(p, dict) else None
            )
            name = getattr(p, "name", None) or (
                p.get("name") if isinstance(p, dict) else None
            )
        except Exception:
            pass
        if not prompt:
            try:
                prompt = getattr(p, "persona", None) or (
                    p.get("persona") if isinstance(p, dict) else None
                )
            except Exception:
                pass
        prompt = str(prompt or "").strip()
        if not prompt:
            return ""
        name = str(name or "").strip()
        if name:
            return f"人格名称：{name}\n人格设定：{prompt}"
        return f"人格设定：{prompt}"

    def _random_times(self, count: int) -> list[str]:
        start = self._hhmm_to_min(str(self.config.get("window_start") or "07:00"))
        end = self._hhmm_to_min(str(self.config.get("window_end") or "23:00"))
        if end <= start:
            end = start + 16 * 60
        pool = list(range(start, end + 1))
        random.shuffle(pool)
        chosen = sorted(pool[:count])
        return [f"{m // 60:02d}:{m % 60:02d}" for m in chosen]

    @staticmethod
    def _hhmm_to_min(s: str) -> int:
        try:
            h, m = s.strip().split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return 7 * 60

    @staticmethod
    def _normalize_time(t: str) -> str:
        """把 7:5 / 07：05 / 7.5 / 07点05 统一成 07:05；认不出就原样返回。"""
        t = str(t or "").strip().strip("`'\"“”[]【】()（）")
        # 全角冒号 / 点号 / 中文「点」
        t = re.sub(r"(\d{1,2})\s*[：﹕．·.点]\s*(\d{1,2})", r"\1:\2", t)
        m = re.match(r"^(\d{1,2})\s*:\s*(\d{1,2})", t)
        if m:
            try:
                h, mi = int(m.group(1)), int(m.group(2))
            except Exception:
                return t
            if 0 <= h <= 23 and 0 <= mi <= 59:
                return f"{h:02d}:{mi:02d}"
        return t

    def _parse_items(self, text: str, expected_times: list[str]) -> list[dict]:
        """解析 [{time, text}]。JSON 优先，失败退化到逐行 HH:MM …，都失败返回 []。"""
        items: list[dict] = []
        if not isinstance(text, str) or not text.strip():
            return items

        def _take(obj: dict) -> None:
            t = self._normalize_time(str(obj.get("time", "")).strip())
            msg = str(
                obj.get("text")
                or obj.get("message")
                or obj.get("content")
                or obj.get("msg")
                or ""
            ).strip()
            if t in expected_times and msg:
                items.append({"time": t, "text": msg})

        # 1) JSON（允许代码围栏/全角标点/尾逗号）
        for obj in _json_objects(_loose_llm_text(text, for_json=True)):
            _take(obj)

        # 2) 行解析：HH:MM|text / HH:MM: text / HH:MM text（去掉行首编号与 markdown 强调）
        if not items:
            for line in _loose_llm_text(text).splitlines():
                line = re.sub(r"^(?:\d+\s*[.、)）\-]\s*)+", "", line.strip())
                line = line.strip("*_`> ").strip()
                m2 = re.match(
                    r"^(\d{1,2}:\d{2})\s*[:|\-—]?\s*(.+)$", line
                )
                if m2:
                    t = self._normalize_time(m2.group(1))
                    msg = m2.group(2).strip().strip("*_` ").strip()
                    if t in expected_times and msg:
                        items.append({"time": t, "text": msg})

        # 3) 去重（同一时间只保留一条）
        seen: set[str] = set()
        dedup: list[dict] = []
        for it in items:
            if it["time"] not in seen:
                seen.add(it["time"])
                dedup.append(it)
        if not dedup:
            logger.warning(
                f"[proactive_guard] 解析不出任何时间点（期望 {expected_times}），"
                f"原始输出前 300 字：{_loose_llm_text(text)[:300]!r}"
            )
        return dedup

    async def _dispatch_due(self) -> None:
        """只从 daily_plan（date==今天）派发：到点发送并删除该时间点；
        错过在补偿窗口内补发，超窗丢弃；date<今天的旧计划静默丢弃（由 _ensure_schedule 清理）。

        发送前会做两道保护：
        1. 记忆水位比对：计划的 memory_updated 与当前记忆不一致 => 记忆更新过，必须复核；
        2. 逐条事实校验：拿最新记忆核对这一条消息，冲突则改写或丢弃。
        这两道是给「预生成计划天然滞后」兜底的——计划可能比记忆旧十几小时。
        """
        plan = await self._load_schedule("daily_plan")
        if not plan or not plan.get("items"):
            return
        today = datetime.now().strftime("%Y-%m-%d")
        if plan.get("date") != today:
            return  # 非今天的旧计划，静默丢弃
        now = datetime.now()
        now_min = now.hour * 60 + now.minute
        grace = self._cfg_int("missed_grace_minutes", 30)
        due: list[dict] = []
        removed: list[str] = []
        for it in plan["items"]:
            it_min = self._hhmm_to_min(str(it.get("time", "")))
            if it_min == now_min:
                due.append(it)
            elif it_min < now_min:
                if now_min - it_min <= grace:
                    due.append(it)  # 补发
                else:
                    removed.append(str(it.get("time", "")))  # 超窗丢弃
        if not due and not removed:
            return
        targets = await self._collect_target_sessions()

        # 记忆比生成计划时新了 => 计划内容可能已经过时
        memory_updated = await self._memory_updated()
        plan_watermark = str(plan.get("memory_updated") or "")
        memory_changed = bool(memory_updated) and plan_watermark != memory_updated
        fact_check = self._cfg_bool("dispatch_fact_check", True)
        memory = ""
        if fact_check or memory_changed:
            memory = await self._collect_memory()
        if memory_changed:
            logger.info(
                f"[proactive_guard] 记忆已更新（计划基于 {plan_watermark or '空'}，"
                f"当前 {memory_updated}），发送前逐条复核"
            )

        for it in due:
            text = str(it.get("text", ""))
            if fact_check and memory:
                checked = await self._fact_check_message(text, memory)
                if not checked:
                    removed.append(str(it.get("time", "")))
                    logger.info(
                        f"[proactive_guard] {it.get('time')} 的消息与最新记忆冲突，"
                        f"已丢弃：{text[:60]!r}"
                    )
                    continue
                text = checked
            await self._send_silent(text, targets)
            removed.append(str(it.get("time", "")))
            logger.info(f"[proactive_guard] 已发送 {it.get('time')} 的消息并移除该时间点")

        if removed or memory_changed:
            plan["items"] = [
                it for it in plan["items"] if str(it.get("time", "")) not in removed
            ]
            if memory_updated:
                plan["memory_updated"] = memory_updated
            await self.put_kv_data("daily_plan", plan)

    async def _memory_updated(self) -> str:
        """当前记忆库的更新时间（水位）。空表示还没有记忆。"""
        store = await self._load_memory_store()
        return str(store.get("updated") or "")

    async def _fact_check_message(self, text: str, memory: str) -> str:
        """发送前拿最新记忆复核一条待发消息。

        返回可直接发送的文本（可能被改写）；返回空字符串表示这条应当丢弃。
        LLM 调用失败时原样返回——宁可发出去，也不要因为校验挂了整天不说话。
        """
        text = str(text or "").strip()
        if not text or not memory:
            return text
        try:
            out = await self._llm_chat(
                FACT_CHECK_PROMPT.format(message=text[:1500], memory=memory[:3000])
            )
        except Exception as e:
            logger.warning(f"[proactive_guard] 发送前事实校验失败，按原文发送: {e}")
            return text
        data = None
        for obj in _json_objects(_loose_llm_text(out, for_json=True)):
            data = obj
            break
        if not isinstance(data, dict):
            return text
        conflict = data.get("conflict")
        if isinstance(conflict, str):
            conflict = conflict.strip().lower() in (
                "true",
                "1",
                "yes",
                "y",
                "是",
                "有",
                "冲突",
            )
        if not conflict:
            return text
        reason = str(data.get("reason") or "").strip()
        rewritten = self._clean_message(
            str(data.get("rewritten") or data.get("rewrite") or "")
        )
        if rewritten:
            logger.info(
                f"[proactive_guard] 事实校验改写消息（{reason or '冲突'}）："
                f"{text[:40]!r} -> {rewritten[:40]!r}"
            )
            return rewritten
        return ""

    async def _regenerate_today_if_stale(self) -> None:
        """今日计划若是用旧记忆生成的，就按最新记忆重写「还没到点」的那些时间点。

        预生成机制下今天的计划是昨天写的（最长滞后近两天），典型症状就是
        记忆里已经写着「卡已领」，今天的消息仍在提醒去领卡。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        plan = await self._load_schedule("daily_plan")
        if plan.get("date") != today or not plan.get("items"):
            return
        memory_updated = await self._memory_updated()
        if not memory_updated:
            return
        if str(plan.get("memory_updated") or "") == memory_updated:
            return
        now_min = datetime.now().hour * 60 + datetime.now().minute
        remaining = [
            str(it.get("time", ""))
            for it in plan["items"]
            if self._hhmm_to_min(str(it.get("time", ""))) >= now_min
        ]
        if not remaining:
            plan["memory_updated"] = memory_updated
            await self.put_kv_data("daily_plan", plan)
            return
        async with self._gen_lock:
            logger.info(
                f"[proactive_guard] 记忆已更新（{memory_updated}），重建今日剩余 "
                f"{len(remaining)} 条计划 @ {'、'.join(remaining)}"
            )
            await self._generate_and_store(target_date=today, times=remaining)

    # ------------------------------------------------------------------ #
    # 发送（绕过门禁，静默）
    # ------------------------------------------------------------------ #
    async def _send_silent(self, text: str, targets: list[str]) -> None:
        if not targets:
            return
        chunks = self._split_text(text)
        for umo in targets:
            ok = False
            for chunk in chunks:
                try:
                    chain = MessageChain().message(chunk)
                    self.owner._bypass_cnt += 1
                    try:
                        ok = bool(await self.context.send_message(umo, chain)) or ok
                    finally:
                        self.owner._bypass_cnt -= 1
                    await asyncio.sleep(0.3)
                except Exception as e:
                    logger.error(f"[proactive_guard] 发送到 {umo} 失败: {e}")
            if ok and self._cfg_bool("record_to_history", True):
                await self._record_to_history(umo, text)

    async def _record_to_history(self, umo: str, text: str) -> None:
        """把主动消息写入该会话的 LLM 对话历史，避免「被动输出与主动输出」记忆断层。"""
        if not self._cfg_bool("record_to_history", True):
            return
        try:
            cm = getattr(self.context, "conversation_manager", None)
            if cm is None or not hasattr(cm, "update_conversation"):
                return
            cid = await cm.get_curr_conversation_id(umo)
            if not cid:
                return  # 会话还没有对话，跳过
            conv = await cm.get_conversation(umo, cid)
            history: list[dict] = []
            if conv is not None and getattr(conv, "history", None):
                try:
                    history = json.loads(conv.history) or []
                except Exception:
                    history = []
            if not isinstance(history, list):
                history = []
            history.append({"role": "assistant", "content": text})
            await cm.update_conversation(umo, cid, history=history)
            logger.debug(f"[proactive_guard] 已把主动消息写入会话历史（{umo}）")
        except Exception as e:
            logger.warning(f"[proactive_guard] 写入会话历史失败（{umo}）: {e}")

    async def _collect_target_sessions(self) -> list[str]:
        sessions: set[str] = set()
        cfg_targets = self.config.get("pg_target_sessions")
        if cfg_targets:
            for line in str(cfg_targets).splitlines():
                line = line.strip()
                if line and ":" in line:
                    sessions.add(line)
            return sorted(sessions)
        try:
            db = self.context.get_db()
            from sqlalchemy import select

            from astrbot.core.db.po import ConversationV2

            async with db.get_db() as sess:
                res = await sess.execute(select(ConversationV2.user_id).distinct())
                for row in res:
                    v = str(row[0]) if row[0] else ""
                    if ":" not in v:
                        continue
                    if self._cfg_bool("only_private", True) and not self._is_private(v):
                        continue
                    sessions.add(v)
        except Exception as e:
            logger.warning(f"[proactive_guard] 数据库枚举会话失败: {e}")
        return sorted(sessions)

    @staticmethod
    def _is_private(umo: str) -> bool:
        low = umo.lower()
        return "friend" in low or "private" in low or "c2c" in low or "privatemsg" in low

    # ------------------------------------------------------------------ #
    # KV 存储
    # ------------------------------------------------------------------ #
    async def _load_schedule(self, key: str) -> dict:
        try:
            return (await self.get_kv_data(key, None)) or {}
        except Exception:
            return {}

    # ================================================================== #
    # 记忆库（凌晨总结对话 -> 生成时注入 -> 发完回写历史）
    # ================================================================== #

    async def refresh_memory(self, umo: str | None = None) -> str:
        """把近期对话交给 AI 总结，写入记忆库（KV）。

        记忆库结构（KV key = MEMORY_KEY）：
        {
          "updated": "2026-10-15 03:00",
          "items": [{"umo": "...", "summary": "...", "updated": "..."}]
        }

        与原始历史不同，这里存的是**AI 提炼过的要点**（称呼、正在做的事、
        情绪状态、提过的偏好与约定），比整段对话更省 token、也更聚焦，
        并且会滚动更新（每次把「旧摘要 + 新对话」一起交给 AI 重写）。
        """
        if not self._cfg_bool("memory_enabled", True):
            return ""
        try:
            cm = getattr(self.context, "conversation_manager", None)
            if cm is None or not hasattr(cm, "get_conversation"):
                return ""
            targets = [umo] if umo else await self._collect_target_sessions()
            if not targets:
                return ""
            store = await self._load_memory_store()
            items = {str(it.get("umo", "")): it for it in store.get("items", []) if it}
            max_chars = max(200, self._cfg_int("memory_max_chars", 1500))
            turns = max(2, self._cfg_int("memory_turns", 20))
            window_hours = max(0, self._cfg_int("memory_window_hours", 48))
            rewrite_stale = self._cfg_bool("memory_rewrite_stale", True)
            notes = await self._collect_user_notes()
            for target in targets:
                try:
                    lines = await self._history_lines(cm, target, turns, window_hours)
                    old = str(items.get(target, {}).get("summary", "")).strip()
                    # 没有新对话的会话原来直接 continue，导致旧摘要里的错误永远不被修正
                    # （比如「待办：万事达卡」即使人早领了也一直留着）。这里让旧记忆
                    # 也按新规则过一次，只整理、不新增。
                    if not lines and not (rewrite_stale and old):
                        continue
                    if lines:
                        raw = "\n".join(lines)
                    else:
                        raw = (
                            "（这个会话近期没有新对话。请只整理上面的旧记忆：删掉已完成、"
                            "已过期、被用户笔记否定的条目，合并重复项，保留仍然成立的事实；"
                            "不要新增任何对话里没出现过的信息。）"
                        )
                    prompt = MEMORY_PROMPT.format(
                        old=old or "（暂无）",
                        notes=notes or "（无）",
                        raw=raw[-max_chars * 2 :],
                        max_chars=max_chars,
                    )
                    summary = (await self._llm_chat(prompt)).strip()
                    summary = self._clean_message(summary) if "\n" not in summary else summary
                    if not summary:
                        continue
                    items[target] = {
                        "umo": target,
                        "summary": summary[:max_chars],
                        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    }
                except Exception as e:
                    logger.warning(f"[proactive_guard] 总结会话记忆失败（{target}）: {e}")
            if not items:
                return ""
            # 多会话 -> 合并成一份去重档案。原来三分段并列，同一件事（熬夜、不吃早饭）
            # 会被写三遍，注入时既费 token 又像复读。
            merged = ""
            if len(items) > 1:
                try:
                    packed = "\n\n".join(
                        f"【会话 {i + 1}】\n{str(it.get('summary', '')).strip()}"
                        for i, it in enumerate(items.values())
                    )
                    merged = await self._llm_chat(
                        MEMORY_MERGE_PROMPT.format(items=packed, max_chars=max_chars)
                    )
                    merged = self._filter_memory_lines(merged)[:max_chars]
                except Exception as e:
                    logger.warning(f"[proactive_guard] 记忆合并失败，沿用分会话记忆: {e}")
                    merged = ""
            store = {
                "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "items": list(items.values()),
                "merged": merged,
            }
            await self.put_kv_data(MEMORY_KEY, store)
            logger.info(
                f"[proactive_guard] 记忆已更新：{len(items)} 个会话"
                f"（{', '.join(str(it.get('umo', '')) for it in items.values())}）"
                f"，合并档案 {'已生成' if merged else '未生成'}"
            )
            return self._memory_text(store)
        except Exception as e:
            logger.warning(f"[proactive_guard] 刷新记忆失败: {e}")
            return ""

    async def _load_memory_store(self) -> dict:
        try:
            data = await self.get_kv_data(MEMORY_KEY, None)
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data
        except Exception:
            pass
        return {"items": []}

    @staticmethod
    def _filter_memory_lines(text: str) -> str:
        """丢掉「在描述助手自己」的条目，并按内容去重。

        兜底层：即使提示词写明不要写 AI 自我介绍，模型仍会加上「助手自称魔女」
        「助手会提醒喝水」这类行。这些由人格设定负责，留在记忆里只会重复并挤占预算。
        """
        out: list[str] = []
        seen: set[str] = set()
        for raw in str(text or "").splitlines():
            line = raw.strip().lstrip("-*•·　").strip()
            if not line:
                continue
            head = line[:10].lower()
            if head.startswith(_MEMORY_SELF_SUBJECTS) or "助手" in head:
                continue
            key = re.sub(r"[\s，。；、,.：:!！?？~～]+", "", line)
            if key in seen:
                continue
            seen.add(key)
            out.append(line)
        return "\n".join(out)

    def _memory_text(self, store: dict) -> str:
        """把记忆库渲染成注入提示词的一段文本。

        优先用合并后的单一档案（merged）；没有（单会话 / 合并失败）才回退分会话拼装。
        两种情况都过 _filter_memory_lines。
        """
        store = store or {}
        merged = self._filter_memory_lines(str(store.get("merged") or ""))
        if merged:
            return merged
        items = [it for it in store.get("items", []) if it and it.get("summary")]
        if not items:
            return ""
        if len(items) == 1:
            return self._filter_memory_lines(str(items[0]["summary"]).strip())
        # 跨会话拼装：同名事实只留第一条（按「去掉标点后的正文」判重，
        # 不能用「【umo】+正文」判重，否则前缀不同会让重复行全部留下）
        lines: list[str] = []
        seen: set[str] = set()
        for it in items:
            body = self._filter_memory_lines(str(it["summary"]).strip())
            if not body:
                continue
            key = re.sub(r"[\s，。；、,.：:!！?？~～]+", "", body)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"【{it.get('umo', '')}】{body}")
        return "\n".join(lines)

    def _workspaces_dir(self) -> Path:
        """AstrBot 数据目录下的 workspaces（插件在 <data>/plugins/<name>/main.py）。"""
        try:
            return Path(__file__).resolve().parent.parent.parent / "workspaces"
        except Exception:
            return Path("")

    async def _collect_user_notes(self) -> str:
        """读取用户自己写在工作区里的 user_profile.md。

        为什么必须读它：记忆摘要由 AI 滚动生成，用户改的事实（例如「卡已领」）
        要等下一轮总结才可能被吸收，而且常常被旧摘要里的待办压住。笔记是用户
        亲手写的**事实源**，直接拼进上下文，改写立刻生效、绕开摘要滞后。
        """
        if not self._cfg_bool("memory_notes_enabled", True):
            return ""
        budget = max(200, self._cfg_int("memory_notes_chars", 1200))
        try:
            root = self._workspaces_dir()
            if not root or not root.is_dir():
                return ""
            files = sorted(
                root.glob("*/user_profile.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except Exception as e:
            logger.debug(f"[proactive_guard] 扫描用户笔记失败: {e}")
            return ""
        chunks: list[str] = []
        seen: set[str] = set()
        used = 0
        for f in files[:5]:
            try:
                body = f.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                continue
            body = re.sub(r"\n{3,}", "\n\n", body)[:budget]
            key = re.sub(r"\s+", "", body)
            if not body or key in seen:
                continue
            seen.add(key)
            chunks.append(body)
            used += len(body)
            if used >= budget:
                break
        if not chunks:
            return ""
        logger.debug(f"[proactive_guard] 已读取用户笔记 {used} 字（{len(chunks)} 份）")
        return MEMORY_NOTES_TEMPLATE.format(notes="\n".join(chunks))

    async def _collect_memory(self) -> str:
        """取记忆文本：AI 总结的记忆库 + 用户笔记；为空则实时总结；再不行退回原始历史。

        三级降级是为了保证「第一次运行、还没到总结时间」时也有记忆可用。
        另外在返回前按 `memory_refresh_cooldown` 冷却做一次就地刷新——只靠每天
        03:00 那一次，白天发生的事最多要滞后近 24 小时才可能进入主动消息。
        """
        if not self._cfg_bool("memory_enabled", True):
            return ""
        notes = await self._collect_user_notes()
        try:
            store = await self._load_memory_store()
            if await self._memory_refresh_due(store):
                async with self._memory_lock:
                    # 双检：等锁期间别人可能刚刷过
                    store = await self._load_memory_store()
                    if await self._memory_refresh_due(store):
                        logger.debug("[proactive_guard] 记忆已过期，生成前就地刷新")
                        await self.refresh_memory()
                        store = await self._load_memory_store()
            text = self._memory_text(store)
            if not text:
                # 记忆库还空着：立刻总结一次（首次生成计划时就地建库）
                text = await self.refresh_memory()
            if text:
                merged = self._truncate_memory(text)
                return (notes + "\n" + merged) if notes else merged
        except Exception as e:
            logger.debug(f"[proactive_guard] 读取记忆库失败，回退原始历史: {e}")
        if not self._cfg_bool("memory_raw_fallback", True):
            return notes
        raw = self._truncate_memory(await self._collect_raw_history())
        if notes and raw:
            return notes + "\n" + raw
        return notes or raw

    async def _memory_refresh_due(self, store: dict) -> bool:
        """记忆是否到了该刷新的时间（按 memory_refresh_cooldown 分钟冷却）。"""
        try:
            cooldown = max(5, self._cfg_int("memory_refresh_cooldown", 30))
            stamp = str((store or {}).get("updated", "") or "").strip()
            if not stamp:
                return True
            last = datetime.strptime(stamp, "%Y-%m-%d %H:%M")
            return (datetime.now() - last).total_seconds() >= cooldown * 60
        except Exception:
            return False

    def _truncate_memory(self, text: str) -> str:
        budget = max(200, self._cfg_int("memory_max_chars", 1500))
        if len(text) <= budget:
            return text
        kept = text[-budget:]
        nl = kept.find("\n")
        if nl != -1:
            kept = kept[nl + 1 :]
        return kept

    async def _collect_raw_history(self) -> str:
        """兜底：未经总结的原始对话历史（短、杂，不如记忆库聚焦）。"""
        turns = max(1, self._cfg_int("memory_turns", 20))
        try:
            cm = getattr(self.context, "conversation_manager", None)
            if cm is None or not hasattr(cm, "get_conversation"):
                return ""
            targets = await self._collect_target_sessions()
            if not targets:
                return ""
            merged: list[str] = []
            for umo in targets[:5]:
                lines = await self._history_lines(cm, umo, turns)
                if len(lines) > len(merged):
                    merged = lines
            return "\n".join(merged)
        except Exception as e:
            logger.debug(f"[proactive_guard] 读取原始历史失败: {e}")
            return ""

    async def _history_lines(
        self, cm, umo: str, turns: int, window_hours: int | None = None
    ) -> list[str]:
        """把单个会话的历史整理成「[MM-DD HH:MM] 用户：…」行列表。

        `window_hours > 0` 时按时间窗过滤，而不是只数条数——只取「最近 N 条」
        会让一句纠正很快滑出窗口，旧摘要里的错误待办就被原样继承下去。
        时间来自 AstrBot 注入在用户消息里的 <system_reminder>Current datetime: …；
        拿不到时间戳的老数据退化为「取最近 turns 条」。
        """
        try:
            cid = await cm.get_curr_conversation_id(umo)
            if not cid:
                return []
            conv = await cm.get_conversation(umo, cid)
            raw = getattr(conv, "history", None) if conv is not None else None
            if not raw:
                return []
            history = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(history, list):
                return []
            cutoff = None
            if window_hours and window_hours > 0:
                cutoff = datetime.now() - timedelta(hours=window_hours)
            records: list[tuple[datetime | None, str]] = []
            last_ts: datetime | None = None
            for msg in history:
                if not isinstance(msg, dict):
                    continue
                role = str(msg.get("role", ""))
                content = msg.get("content", "")
                # content 可能是多模态数组，只取文本部分（think 段不带 text，自动跳过）
                if isinstance(content, list):
                    parts = [
                        str(p.get("text", ""))
                        for p in content
                        if isinstance(p, dict) and p.get("text")
                    ]
                    content = " ".join(parts)
                content = str(content)
                m = _TS_RE.search(content)
                if m:
                    try:
                        last_ts = datetime.strptime(
                            f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M"
                        )
                    except ValueError:
                        pass
                content = _SYS_REMINDER_RE.sub("", content).strip().replace("\n", " ")
                if not content:
                    continue
                if role == "user":
                    records.append((last_ts, f"用户：{content}"))
                elif role == "assistant":
                    records.append((last_ts, f"你：{content}"))
            if cutoff is not None:
                kept = [r for r in records if r[0] is None or r[0] >= cutoff]
                # 全被时间窗滤掉（长时间没聊）时保留尾巴，别让记忆凭空失忆
                if kept:
                    records = kept
            if len(records) > turns:
                records = records[-turns:]
            out: list[str] = []
            for ts, line in records:
                out.append(f"[{ts.strftime('%m-%d %H:%M')}] {line}" if ts else line)
            return out
        except Exception as e:
            logger.debug(f"[proactive_guard] 读取会话历史失败（{umo}）: {e}")
            return []

    # ------------------------------------------------------------------ #
    # 人格 / 世界观 / 分享比例
    # ------------------------------------------------------------------ #

    def _world_block(self) -> str:
        """世界观设定块。没填就留空，提示词里不出现这一段。"""
        world = str(self.config.get("world_setting") or "").strip()
        if not world:
            return ""
        return WORLD_BLOCK_TEMPLATE.format(world=world)

    def _share_ratio(self) -> int:
        """自我分享占比（0~100）。其余部分留给对用户的关心。"""
        try:
            ratio = int(self.config.get("self_share_ratio", 70))
        except (TypeError, ValueError):
            ratio = 70
        return max(0, min(100, ratio))

    @staticmethod
    def _share_counts(count: int, ratio: int) -> tuple[int, int]:
        """把占比换算成条数，并保证「关心用户」那一类至少 1 条。

        用户明确要求两类都要：不能为了自我分享把关心挤成 0 条。
        """
        count = max(1, int(count or 1))
        share = max(0, min(count, round(count * ratio / 100)))
        care = count - share
        if count >= 2 and care == 0:
            care = 1
            share = count - 1
        return share, care

    # ------------------------------------------------------------------ #
    # 子代理流水线：规划 -> 并行写手 -> 审校
    # ------------------------------------------------------------------ #

    async def _generate_with_subagents(
        self,
        target_date: str,
        times: list[str],
        time_list: str,
        count: int,
        persona: str,
        memory_block: str,
        world_block: str = "",
        share_count: int = 0,
        care_count: int = 0,
    ) -> list[dict]:
        """用三个「子代理」协作产出一天的消息。

        子代理在这里就是三次独立、职责单一、上下文各自收敛的 LLM 调用：
        规划代理只出要点（不写正文），写手代理一次只写一条（上下文最小、
        最不容易被别的时段带偏），审校代理统管全局语气与去重。
        比单次「一把梭」更稳：某个时间点写砸了不会拖垮整天，而且每个写手
        只看到自己那一条的记忆片段，不会串味。
        """
        # --- 1) 规划代理：每个时间点一句要点 ---
        planner_prompt = PLANNER_PROMPT.format(
            persona=persona,
            world_block=world_block,
            date=target_date,
            count=count,
            time_list=time_list,
            memory_block=memory_block,
            share_count=share_count,
            care_count=care_count,
        )
        try:
            plan_text = await self._llm_chat(planner_prompt)
            points = self._parse_points(plan_text, times)
        except Exception as e:
            logger.warning(f"[proactive_guard] 规划代理失败，回退单次生成: {e}")
            return []
        if not points:
            logger.warning("[proactive_guard] 规划代理未产出可用要点，回退单次生成")
            return []

        # --- 2) 写手代理：每人只写自己那条，并行 ---
        async def write_one(t: str, point: str) -> dict | None:
            prompt = WRITER_PROMPT.format(
                persona=persona,
                world_block=world_block,
                date=target_date,
                time=t,
                point=point,
                memory_block=memory_block,
            )
            try:
                text_out = await self._llm_chat(prompt)
            except Exception as e:
                logger.warning(f"[proactive_guard] 写手代理失败（{t}）: {e}")
                return None
            text_out = self._clean_message(text_out)
            if not text_out:
                logger.debug(f"[proactive_guard] 写手代理返回空内容（{t}）")
                return None
            return {"time": t, "text": text_out}

        concurrency = max(1, min(self._cfg_int("subagent_concurrency", 3), len(points)))
        if not self._cfg_bool("subagent_parallel", True):
            concurrency = 1
        sem = asyncio.Semaphore(concurrency)

        async def limited(t: str, point: str) -> dict | None:
            async with sem:
                return await write_one(t, point)

        results = await asyncio.gather(
            *(limited(t, p) for t, p in points), return_exceptions=True
        )
        drafts: list[dict] = []
        for r in results:
            if isinstance(r, dict) and r.get("text"):
                drafts.append(r)
        if not drafts:
            logger.warning("[proactive_guard] 写手代理全部失败，回退单次生成")
            return []

        # --- 3) 审校代理：统一语气、去重、修场景错位 ---
        if self._cfg_bool("subagent_review", True) and len(drafts) > 0:
            reviewed = await self._review_drafts(drafts, persona, world_block)
            if reviewed:
                drafts = reviewed

        drafts.sort(key=lambda x: x.get("time", ""))
        return drafts

    async def _review_drafts(
        self, drafts: list[dict], persona: str, world_block: str = ""
    ) -> list[dict]:
        """审校代理：只允许它改文案，条数与时间点以输入为准（防止被 AI 增删）。"""
        if not drafts:
            return []
        times = [d["time"] for d in drafts]
        prompt = REVIEWER_PROMPT.format(
            persona=persona,
            world_block=world_block,
            draft=json.dumps(drafts, ensure_ascii=False),
        )
        try:
            text = await self._llm_chat(prompt)
        except Exception as e:
            logger.warning(f"[proactive_guard] 审校代理失败，使用写手原稿: {e}")
            return []
        if not isinstance(text, str) or not text.strip():
            logger.warning("[proactive_guard] 审校代理返回空内容，使用写手原稿")
            return []
        items = self._parse_items(text, times)
        if len(items) != len(times):
            logger.warning(
                f"[proactive_guard] 审校代理输出条数不符（{len(items)}/{len(times)}），使用写手原稿"
            )
            return []
        return items

    def _parse_points(self, text: str, expected_times: list[str]) -> list[tuple[str, str]]:
        """解析规划代理的 [{time, point}]。字段兼容 point/plan/topic/text。"""
        out: list[tuple[str, str]] = []
        if not isinstance(text, str) or not text.strip():
            return [(t, "自然地聊一句，贴合这个时间的场景") for t in expected_times]

        def _take(obj: dict) -> None:
            t = self._normalize_time(str(obj.get("time", "")).strip())
            point = str(
                obj.get("point")
                or obj.get("plan")
                or obj.get("topic")
                or obj.get("text")
                or obj.get("content")
                or ""
            ).strip()
            if t in expected_times and point:
                out.append((t, point))

        for obj in _json_objects(_loose_llm_text(text, for_json=True)):
            _take(obj)

        if not out:
            # 行解析：HH:MM 要点
            for line in _loose_llm_text(text).splitlines():
                line = re.sub(r"^(?:\d+\s*[.、)）\-]\s*)+", "", line.strip())
                line = line.strip("*_`> ").strip()
                m2 = re.match(r"^(\d{1,2}:\d{2})\s*[:|\-—]?\s*(.+)$", line)
                if m2:
                    t = self._normalize_time(m2.group(1))
                    point = m2.group(2).strip().strip("*_` ").strip()
                    if t in expected_times and point:
                        out.append((t, point))
        if not out:
            logger.warning(
                f"[proactive_guard] 规划代理输出解析不出要点（期望 {expected_times}），"
                f"原始输出前 300 字：{_loose_llm_text(text)[:300]!r}"
            )
        # 去重 + 按输入时间顺序对齐（缺失的时间点用通用要点补齐，避免整天少几条）
        seen: dict[str, str] = {}
        for t, p in out:
            if t not in seen:
                seen[t] = p
        return [(t, seen.get(t, "自然地聊一句，贴合这个时间的场景")) for t in expected_times]

    @staticmethod
    def _clean_message(text: str) -> str:
        """清掉写手代理可能多输出的包装：引号、时间前缀、编号、解释。"""
        if not isinstance(text, str):
            return ""
        t = text.strip()
        if not t:
            return ""
        # 去掉整体包裹的引号
        t = t.strip().strip('"').strip("“”").strip("「」").strip("'").strip()
        # 去掉行首时间前缀："07:35 早上好" / "07:35：早上好"
        t = re.sub(r"^\d{1,2}:\d{2}\s*[:：\-|]?\s*", "", t)
        # 去掉行首编号
        t = re.sub(r"^(?:\d+\s*[.、)）\-]\s*)+", "", t)
        # 只保留第一行有效内容
        for line in t.splitlines():
            line = line.strip()
            if line:
                return line.strip()
        return ""

    async def _on_memory_time(self) -> None:
        """到记忆总结时间：把近期对话交给 AI 总结，写进记忆库（后台静默）。"""
        try:
            await self.refresh_memory()
        except Exception as e:
            logger.error(f"[proactive_guard] 记忆总结失败: {e}", exc_info=True)

    async def memory_command(self, event: AstrMessageEvent):
        """查看当前记忆库内容。

        重点是展示「真正注入给 AI 的那份档案」（merged，已剔除 AI 自述 + 去重），
        分会话明细放后面折叠显示，方便核对来源。
        """
        store = await self._load_memory_store()
        items = [it for it in store.get("items", []) if it]
        lines: list[str] = ["🧠 记忆库"]
        if not items:
            lines.append("（还没有记忆。到 memory_cron 时间会自动总结，或现在用 /刷新记忆 立刻生成）")
        else:
            lines.append(f"最后更新：{store.get('updated', '未知')}　会话数：{len(items)}")
            archive = self._memory_text(store)
            lines.append("")
            lines.append("—— 注入给 AI 的档案 ——")
            lines.append(archive or "（空：记忆被过滤后没有剩下可用内容）")
            if len(items) > 1:
                lines.append("")
                lines.append("—— 分会话明细 ——")
                for it in items:
                    body = str(it.get("summary", "")).strip()
                    lines.append(f"【{it.get('umo', '')}】{it.get('updated', '')}")
                    lines.append(body[:400] + ("…" if len(body) > 400 else ""))
        if not self._cfg_bool("memory_enabled", True):
            lines.append("")
            lines.append("⚠️ 记忆功能当前已关闭（memory_enabled = false）")
        yield event.plain_result("\n".join(lines))

    async def refresh_memory_command(self, event: AstrMessageEvent):
        """立刻让 AI 总结一次对话，写入记忆库"""
        if not self._cfg_bool("memory_enabled", True):
            yield event.plain_result("⚠️ 记忆功能已关闭，请先在配置里打开 memory_enabled。")
            return
        yield event.plain_result("⏳ 正在总结记忆…")
        asyncio.create_task(self.refresh_memory())

    async def reset_memory_command(self, event: AstrMessageEvent):
        """清空记忆库并按当前规则重建（用来洗掉历史遗留的错误条目）。"""
        if not self._cfg_bool("memory_enabled", True):
            yield event.plain_result("⚠️ 记忆功能已关闭，请先在配置里打开 memory_enabled。")
            return
        # 必须走 KV 接口写空，不能直接改数据库：AstrBot 的 preferences 在内存里有副本，
        # 直接改 sqlite 会被下一次写回覆盖掉。
        await self.put_kv_data(MEMORY_KEY, {"updated": "", "items": [], "merged": ""})
        yield event.plain_result(
            "🧹 记忆库已清空，正在按「近期对话 + 用户笔记」重建…（完成后用 /记忆 查看）"
        )
        asyncio.create_task(self.refresh_memory())
    # ================================================================== #
    # 指令（用户主动触发时才回复）
    # ================================================================== #
    async def today_plan_command(self, event: AstrMessageEvent):
        """查看今日待发送的人格消息计划 + 明日预生成摘要"""
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        plan = await self._load_schedule("daily_plan")
        next_plan = await self._load_schedule("daily_plan_next")
        lines: list[str] = []
        if plan.get("date") == today and plan.get("items"):
            lines.append(f"📋 今日计划（{today}）")
            for it in sorted(plan["items"], key=lambda x: x.get("time", "")):
                lines.append(f"- {it.get('time')} {str(it.get('text', ''))[:40]}")
        else:
            lines.append(f"📭 今日（{today}）暂无待发送的消息计划。")
        lines.append("")
        if next_plan.get("date") == tomorrow and next_plan.get("items"):
            lines.append(f"🗓️ 明日计划已预生成（{tomorrow}，共 {len(next_plan['items'])} 条）")
            for it in sorted(next_plan["items"], key=lambda x: x.get("time", ""))[:5]:
                lines.append(f"- {it.get('time')} {str(it.get('text', ''))[:40]}")
            if len(next_plan["items"]) > 5:
                lines.append(f"  … 共 {len(next_plan['items'])} 条")
        else:
            lines.append(f"🗓️ 明日（{tomorrow}）计划尚未生成。")
        yield event.plain_result("\n".join(lines))

    async def rebuild_plan_command(self, event: AstrMessageEvent):
        """立即在后台重新生成明天的计划；若今天的窗口未结束且今天计划缺失，也补一份今天的"""
        yield event.plain_result("⏳ 正在后台重建计划…")
        asyncio.create_task(self._rebuild_plans())

    async def _rebuild_plans(self) -> None:
        """强制重建：明天的计划无条件重新生成；今天的窗口未结束且今天计划缺失时补今天的。"""
        if not self._cfg_bool("pg_enabled", True):
            return
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        plan = await self._load_schedule("daily_plan")
        window_end = self._hhmm_to_min(str(self.config.get("window_end") or "23:00"))
        now_min = datetime.now().hour * 60 + datetime.now().minute
        # 今天的窗口未结束且今天计划缺失 -> 补一份今天的
        if (plan.get("date") != today or not plan.get("items")) and now_min < window_end:
            async with self._gen_lock:
                await self._generate_and_store(target_date=today)
        # 无条件重新生成明天的计划（force 忽略节流）
        async with self._gen_lock:
            await self._generate_and_store(target_date=tomorrow)

    # ================================================================== #
    # LLM 调用（新 API 优先，旧 API 回退）
    # ================================================================== #
    async def _llm_chat(self, prompt: str) -> str:
        ctx = self.context
        try:
            prov_id = None
            get_using = getattr(ctx, "get_using_provider_async", None)
            if callable(get_using):
                prov = await get_using()
                meta = getattr(prov, "meta", None) if prov else None
                if callable(meta):
                    prov_id = getattr(meta(), "id", None)
            if prov_id and callable(getattr(ctx, "llm_generate", None)):
                resp = await ctx.llm_generate(
                    chat_provider_id=prov_id,
                    prompt=prompt,
                    system_prompt="你是一个内容生成助手，严格按照要求输出。",
                )
                text = self._llm_text(resp)
                if text:
                    return text
        except Exception as e:
            logger.debug(f"[proactive_guard] llm_generate 调用失败: {e}")
        try:
            get_prov = getattr(ctx, "get_using_provider", None)
            if callable(get_prov):
                prov = get_prov()
                if prov is not None and callable(getattr(prov, "text_chat", None)):
                    resp = await prov.text_chat(
                        prompt=prompt, session_id=None, image_urls=[]
                    )
                    text = self._llm_text(resp)
                    if text:
                        return text
        except Exception as e:
            logger.debug(f"[proactive_guard] text_chat 调用失败: {e}")
        raise RuntimeError("无法获取可用的 LLM 提供商")

    @staticmethod
    def _llm_text(resp) -> str:
        if resp is None:
            return ""
        for attr in ("completion_text", "result", "text"):
            try:
                v = getattr(resp, attr, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            except Exception:
                continue
        try:
            rc = getattr(resp, "result_chain", None)
            if rc is not None and hasattr(rc, "get_plain_text"):
                t = rc.get_plain_text()
                if t:
                    return t
        except Exception:
            pass
        return ""

    # ================================================================== #
    # 工具方法
    # ================================================================== #
    def _cfg_bool(self, key: str, default: bool) -> bool:
        v = self.config.get(key, default)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _split_text(text: str, limit: int = 3800) -> list[str]:
        text = text.strip()
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        cur = ""
        for line in text.splitlines(keepends=True):
            while len(line) > limit:
                if cur:
                    chunks.append(cur)
                    cur = ""
                chunks.append(line[:limit])
                line = line[limit:]
            if cur and len(cur) + len(line) > limit:
                chunks.append(cur)
                cur = line
            else:
                cur += line
        if cur:
            chunks.append(cur)
        return chunks

    # ================================================================== #
    # 阶段一：插件内部「单会话决策循环」主动消息（living_*）
    # 契约见 docs/PHASE0_MEMO.md §4：
    #   · 只用插件内部 AsyncIOScheduler，不注册 AstrBot cron_manager；
    #   · 每个会话一个决策 job（proactive_decision_<sid>，replace_existing），
    #     决策完自己排下一次 —— 自我延续，无外部节拍；
    #   · 两阶段：决策只产「意图 + 时间偏移」，触发时才读当下记忆生成正文；
    #   · 同一会话同一时刻最多一个待触发（proactive_fire_<sid> 单槽 + token 幂等）；
    #   · 每会话一把 asyncio.Lock 覆盖 决策→生成→发送；
    #   · 状态落 <data>/proactive_state.json（原子写），重启恢复、过期待触发丢弃不补发。
    # ================================================================== #
    def _living_on(self) -> bool:
        return self._cfg_bool("living_enabled", False)

    def _living_state_file(self) -> Path | None:
        data_dir = _plugin_data_dir()
        if not data_dir:
            return None
        return Path(data_dir) / "proactive_state.json"

    @staticmethod
    def _living_sid(umo: str) -> str:
        return re.sub(r"[^0-9A-Za-z_]+", "_", str(umo or "unknown"))[:80]

    def _living_lock(self, umo: str) -> asyncio.Lock:
        lock = self._living_locks.get(umo)
        if lock is None:
            lock = asyncio.Lock()
            self._living_locks[umo] = lock
        return lock

    async def _living_load(self) -> dict:
        path = self._living_state_file()
        empty = {"version": 2, "sessions": {}}
        if path is None:
            return empty
        try:
            if not path.exists():
                return empty
            raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("state 顶层不是对象")
            data.setdefault("version", 2)
            if not isinstance(data.get("sessions"), dict):
                data["sessions"] = {}
            return data
        except Exception as e:
            logger.warning(f"[living] 读取状态失败，按空状态继续: {e}")
            return empty

    async def _living_save(self, state: dict) -> None:
        path = self._living_state_file()
        if path is None:
            return
        try:
            payload = json.dumps(state, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[living] 状态序列化失败: {e}")
            return

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.part")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, path)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            logger.warning(f"[living] 保存状态失败: {e}")

    def _living_sstate(self, state: dict, umo: str) -> dict:
        s = state["sessions"].get(umo)
        if not isinstance(s, dict):
            s = {}
            state["sessions"][umo] = s
        s.setdefault("last_user_at", 0.0)
        s.setdefault("last_proactive_at", 0.0)
        s.setdefault("sent_date", "")
        s.setdefault("sent_today", 0)
        s.setdefault("next_decision_at", 0.0)
        s.setdefault("pending", None)
        if not isinstance(s.get("history"), list):
            s["history"] = []
        return s

    def _living_log(self, s: dict, kind: str, detail: str = "") -> None:
        s.setdefault("history", []).append(
            {"ts": round(time.time(), 1), "kind": kind, "detail": str(detail)[:200]}
        )
        hist = s["history"]
        if len(hist) > 40:
            del hist[: len(hist) - 40]

    # ------------------------------ 时间 ------------------------------ #
    def _living_tz(self):
        tz_name = str(self.config.get("pg_timezone") or "Asia/Shanghai").strip()
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(tz_name)
        except Exception:
            return None

    def _living_now(self) -> datetime:
        tz = self._living_tz()
        return datetime.now(tz) if tz else datetime.now()

    def _living_dt(self, ts: float) -> datetime:
        tz = self._living_tz()
        return datetime.fromtimestamp(ts, tz) if tz else datetime.fromtimestamp(ts)

    def _living_quiet_range(self) -> tuple[int, int] | None:
        raw = str(self.config.get("living_quiet_hours") or "23:30-07:30").strip()
        if not raw or raw.lower() in ("off", "none", "0"):
            return None
        m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$", raw)
        if not m:
            return None
        try:
            start = int(m.group(1)) * 60 + int(m.group(2))
            end = int(m.group(3)) * 60 + int(m.group(4))
        except Exception:
            return None
        if start == end:
            return None
        return start, end

    def _living_in_quiet(self, dt: datetime) -> bool:
        rng = self._living_quiet_range()
        if rng is None:
            return False
        start, end = rng
        cur = dt.hour * 60 + dt.minute
        if start < end:
            return start <= cur < end
        return cur >= start or cur < end

    def _living_quiet_end_after(self, dt: datetime) -> datetime:
        rng = self._living_quiet_range()
        if rng is None:
            return dt
        _start, end = rng
        out = dt.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
        if out <= dt:
            out = out + timedelta(days=1)
        return out

    @staticmethod
    def _living_period(dt: datetime) -> str:
        h = dt.hour
        if h < 5:
            return "深夜"
        if h < 8:
            return "清晨"
        if h < 11:
            return "上午"
        if h < 13:
            return "中午"
        if h < 17:
            return "下午"
        if h < 19:
            return "傍晚"
        if h < 23:
            return "晚上"
        return "夜里"

    @staticmethod
    def _living_human_gap(minutes: float) -> str:
        m = max(1, int(minutes))
        if m < 60:
            return f"{m} 分钟"
        if m < 1440:
            return f"{m // 60} 小时"
        return f"{m // 1440} 天"

    # ---------------------------- 调度原语 ---------------------------- #
    def _living_add_date_job(self, func, run_ts: float, args: list, job_id: str) -> None:
        sched = getattr(self, "_living_sched", None)
        if sched is None or not getattr(sched, "running", False):
            logger.debug("[living] 调度器未运行，跳过注册 job")
            return
        try:
            from apscheduler.triggers.date import DateTrigger

            sched.add_job(
                func,
                DateTrigger(run_date=self._living_dt(max(run_ts, time.time() + 1))),
                args=args,
                id=job_id,
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=120,
            )
        except Exception as e:
            logger.warning(f"[living] 注册 job 失败 {job_id}: {e}")

    async def _living_schedule_decision(self, umo: str, when_ts: float | None = None) -> None:
        state = await self._living_load()
        s = self._living_sstate(state, umo)
        now = time.time()
        if when_ts is None:
            check = max(1, self._cfg_int("living_decision_check_minutes", 10)) * 60
            when_ts = now + check + self._living_rng.randint(0, max(1, check // 3))
        when_ts = max(float(when_ts), now + 15)
        s["next_decision_at"] = when_ts
        await self._living_save(state)
        self._living_add_date_job(
            self._living_decision_tick,
            when_ts,
            [umo],
            f"proactive_decision_{self._living_sid(umo)}",
        )

    async def _living_start(self) -> None:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
        except Exception as e:
            logger.error(f"[living] 无法导入 APScheduler，主动消息 v2 不可用: {e}")
            return
        tz = self._living_tz()
        sched = AsyncIOScheduler(timezone=tz) if tz is not None else AsyncIOScheduler()
        try:
            sched.start()
        except Exception as e:
            logger.error(f"[living] 调度器启动失败: {e}")
            return
        self._living_sched = sched
        now = time.time()
        state = await self._living_load()
        sessions = await self._collect_target_sessions()
        for umo in sessions:
            s = self._living_sstate(state, umo)
            if not float(s.get("last_user_at") or 0):
                # 基线：从现在开始算沉默，避免一上线就对沉默很久的会话开口
                s["last_user_at"] = now
        await self._living_save(state)
        for umo in sessions:
            s = self._living_sstate(state, umo)
            nxt = float(s.get("next_decision_at") or 0)
            if nxt <= now:
                nxt = now + self._living_rng.randint(120, 600)
            s["next_decision_at"] = nxt
            self._living_add_date_job(
                self._living_decision_tick,
                nxt,
                [umo],
                f"proactive_decision_{self._living_sid(umo)}",
            )
        await self._living_save(state)
        if self._cfg_bool("memory_enabled", True):
            await self._living_schedule_memory()
        logger.info(
            f"[living] 主动消息 v2 调度已启动：{len(sessions)} 个会话"
            "（插件内部 AsyncIOScheduler，不依赖 cron_manager）"
        )

    async def _living_schedule_memory(self) -> None:
        sched = getattr(self, "_living_sched", None)
        if sched is None:
            return
        cron = str(self.config.get("memory_cron") or "0 3 * * *").strip()
        tz = self._living_tz()
        try:
            from apscheduler.triggers.cron import CronTrigger

            trigger = (
                CronTrigger.from_crontab(cron, timezone=tz)
                if tz is not None
                else CronTrigger.from_crontab(cron)
            )
            sched.add_job(
                self._on_memory_time,
                trigger,
                id="living_memory",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=600,
            )
        except Exception as e:
            logger.warning(f"[living] 记忆任务注册失败: {e}")

    async def _living_stop(self) -> None:
        sched = getattr(self, "_living_sched", None)
        self._living_sched = None
        if sched is None:
            return
        try:
            sched.remove_all_jobs()
        except Exception:
            pass
        try:
            sched.shutdown(wait=False)
            logger.info("[living] 主动消息 v2 调度已停止")
        except Exception as e:
            logger.warning(f"[living] 调度器关闭失败: {e}")

    # ---------------------------- 决策阶段 ---------------------------- #
    def _living_should_initiate(self, umo: str, s: dict, now_ts: float) -> tuple[bool, str]:
        """决策 tick 的门禁：沉默偏离 / 冷却 / 日上限 / 安静时段 / 概率斜坡。"""
        if not self._living_on():
            return False, "living 未开启"
        last = max(float(s.get("last_user_at") or 0), float(s.get("last_proactive_at") or 0))
        if last <= 0:
            return False, "没有交互基线"
        silence_min = (now_ts - last) / 60.0
        min_sil = max(1, self._cfg_int("living_min_silence_minutes", 90))
        if silence_min < min_sil:
            return False, f"沉默 {silence_min:.0f} 分钟 < 下限 {min_sil}"
        cooldown = max(1, self._cfg_int("living_cooldown_minutes", 180))
        last_pro = float(s.get("last_proactive_at") or 0)
        if last_pro and now_ts - last_pro < cooldown * 60:
            return False, "冷却中"
        now_dt = self._living_dt(now_ts)
        if self._living_in_quiet(now_dt):
            return False, "安静时段"
        today = now_dt.strftime("%Y-%m-%d")
        if s.get("sent_date") != today:
            s["sent_date"] = today
            s["sent_today"] = 0
        daily_max = max(1, self._cfg_int("living_daily_max", 6))
        if int(s.get("sent_today") or 0) >= daily_max:
            return False, f"今日已发满 {daily_max} 条"
        ramp = max(1, self._cfg_int("living_ramp_minutes", 240))
        p = min(1.0, max(0.05, (silence_min - min_sil) / ramp))
        if self._living_rng.random() > p:
            return False, f"概率门未过（p={p:.2f}）"
        return True, f"沉默 {silence_min:.0f} 分钟 / p={p:.2f}"

    def _living_candidates(self, umo: str, s: dict, now_dt: datetime) -> list[dict]:
        """候选意图池（阶段四会换成真正的评分-选择循环，这里先用可解释的启发式）。"""
        ratio = max(0, min(100, self._share_ratio()))
        share_w = max(0.1, ratio / 100.0)
        care_w = max(0.1, 1.0 - ratio / 100.0)
        hour = now_dt.hour
        night = 1.0 if hour >= 21 or hour < 6 else 0.6
        day = 1.0 if 9 <= hour <= 20 else 0.6
        last_gap = max(
            float(s.get("last_user_at") or 0), float(s.get("last_proactive_at") or 0)
        )
        silence_h = max(0.0, (now_dt.timestamp() - last_gap) / 3600.0)
        out: list[dict] = [
            {
                "intent": "share",
                "label": "分享自己正在经历的一件小事（世界观内，有画面）",
                "score": 1.0 * share_w * day,
                "reason": "沉默期间你去过/见过什么",
            },
            {
                "intent": "care",
                "label": "具体地关心对方的近况（要能说出由头）",
                "score": 1.0 * care_w * night,
                "reason": "记起了对方提过的事",
            },
            {
                "intent": "followup",
                "label": "追问之前没说完的话题",
                "score": 0.55 * share_w,
                "reason": "上次聊到一半的事",
            },
            {
                "intent": "curiosity",
                "label": "对对方提过的东西产生新的好奇",
                "score": 0.45 * share_w,
                "reason": "想不通的地方",
            },
        ]
        if silence_h >= 6:
            out.append(
                {
                    "intent": "check_in",
                    "label": "试探性地确认对方还在不在（带点别扭，不直白示弱）",
                    "score": 0.4 + 0.05 * min(6.0, silence_h - 6) + 0.2 * care_w,
                    "reason": f"已经 {self._living_human_gap(silence_h * 60)}没动静",
                }
            )
        return out

    def _living_plan_intent(self, umo: str, s: dict, now_dt: datetime) -> dict:
        cands = self._living_candidates(umo, s, now_dt)
        if not cands:
            return {}
        best = max(cands, key=lambda c: float(c.get("score") or 0) * (0.8 + 0.4 * self._living_rng.random()))
        off_min = max(1, self._cfg_int("living_offset_min", 5))
        off_max = max(off_min + 1, self._cfg_int("living_offset_max", 45))
        offset = self._living_rng.randint(off_min, off_max)
        fire_dt = now_dt + timedelta(minutes=offset)
        if self._living_in_quiet(fire_dt):
            fire_dt = self._living_quiet_end_after(fire_dt) + timedelta(
                minutes=self._living_rng.randint(5, 25)
            )
            offset = max(1, int((fire_dt - now_dt).total_seconds() // 60))
        return {
            "intent": str(best.get("intent") or "share"),
            "label": str(best.get("label") or ""),
            "reason": str(best.get("reason") or ""),
            "offset_min": offset,
            "fire_at": fire_dt.timestamp(),
        }

    async def _living_decide(self, umo: str) -> None:
        if not self._living_on():
            return
        state = await self._living_load()
        s = self._living_sstate(state, umo)
        now_dt = self._living_now()
        now = now_dt.timestamp()
        pending = s.get("pending")
        if isinstance(pending, dict) and float(pending.get("fire_at") or 0) > now - 5:
            self._living_log(s, "skip", "已有待触发，等它执行")
            await self._living_save(state)
            await self._living_schedule_decision(umo, now + 300)
            return
        if isinstance(pending, dict):
            # 过期待触发（进程休眠/停机所致）：直接丢弃，绝不补发
            s["pending"] = None
            self._living_log(s, "drop", "过期待触发已丢弃（不补发）")
        ok, why = self._living_should_initiate(umo, s, now)
        if not ok:
            self._living_log(s, "skip", why)
            await self._living_save(state)
            await self._living_schedule_decision(umo)
            return
        plan = self._living_plan_intent(umo, s, now_dt)
        if not plan:
            self._living_log(s, "skip", "没有候选意图")
            await self._living_save(state)
            await self._living_schedule_decision(umo)
            return
        token = uuid.uuid4().hex
        s["pending"] = {
            "token": token,
            "fire_at": float(plan["fire_at"]),
            "intent": plan["intent"],
            "label": plan["label"],
            "reason": plan["reason"],
            "created": now,
        }
        cooldown = max(1, self._cfg_int("living_cooldown_minutes", 180))
        s["next_decision_at"] = float(plan["fire_at"]) + cooldown * 60
        self._living_log(s, "plan", f"{plan['intent']}@+{plan['offset_min']}min")
        await self._living_save(state)
        self._living_add_date_job(
            self._living_fire_tick,
            float(plan["fire_at"]),
            [umo, token],
            f"proactive_fire_{self._living_sid(umo)}",
        )
        self._living_add_date_job(
            self._living_decision_tick,
            float(s["next_decision_at"]),
            [umo],
            f"proactive_decision_{self._living_sid(umo)}",
        )
        logger.info(
            f"[living] {umo} 决定主动：{plan['label']}（{plan['offset_min']} 分钟后触发）"
        )

    async def _living_decision_tick(self, umo: str) -> None:
        async with self._living_lock(umo):
            try:
                await self._living_decide(umo)
            except Exception as e:
                logger.error(f"[living] 决策失败 {umo}: {e}", exc_info=True)
                try:
                    await self._living_schedule_decision(umo)
                except Exception:
                    pass

    # ---------------------------- 触发阶段 ---------------------------- #
    async def _living_persona_block(self) -> str:
        p = load_persona_asset()
        if not p:
            return ""
        chars = max(400, self._cfg_int("living_persona_chars", 2400))
        lines: list[str] = []
        dn = str(p.get("display_name") or p.get("name") or "")
        title = str(p.get("title") or "")
        if dn:
            lines.append(f"【人格】你是{dn}{('（' + title + '）') if title else ''}。")
        ident = p.get("identity")
        if isinstance(ident, dict):
            bits = [
                str(ident.get(k))
                for k in ("species", "origin", "age", "height_cm", "appearance")
                if ident.get(k) not in (None, "")
            ]
            if bits:
                lines.append("身份：" + "；".join(bits) + "。")
            sref = ident.get("self_reference")
            if isinstance(sref, dict) and sref.get("default"):
                extra = f"；自夸时用「{sref.get('boast')}」" if sref.get("boast") else ""
                lines.append(f"自称：{sref.get('default')}{extra}。")
            forb_ref = ident.get("forbidden_self_reference")
            if isinstance(forb_ref, list) and forb_ref:
                lines.append("不得自称：" + "、".join(str(x) for x in forb_ref[:8]) + "。")
        traits = [t for t in (p.get("traits") or []) if isinstance(t, dict)]
        if traits:
            lines.append(
                "性格：" + "；".join(f"{t.get('label')}——{t.get('summary')}" for t in traits[:6]) + "。"
            )
        tone = p.get("tone")
        if isinstance(tone, dict):
            if tone.get("register"):
                lines.append("语气：" + str(tone["register"]) + "。")
            lr = tone.get("length_rule")
            if isinstance(lr, dict) and lr.get("hard_max_chars"):
                lines.append(
                    f"长度铁律：{lr.get('hard_max_chars')} 字以内（例外场景不超过 "
                    f"{lr.get('exception_max_chars') or 50} 字）。"
                )
        habits = [h for h in (p.get("speech_habits") or []) if isinstance(h, dict)]
        if habits:
            lines.append("表达习惯：" + "；".join(str(h.get("habit") or "") for h in habits[:6]) + "。")
        ts = p.get("tsundere")
        if isinstance(ts, dict):
            lines.append("【傲娇机制】（这是你表达的方式，不是要向对方解释的概念）")
            negs = [
                str(n.get("pattern"))
                for n in (ts.get("negation_patterns") or [])
                if isinstance(n, dict) and n.get("pattern")
            ]
            if negs:
                lines.append("· 嘴硬时可以这样写：" + "；".join(negs[:3]))
            leaks = [
                str(l.get("mechanism"))
                for l in (ts.get("care_leak_patterns") or [])
                if isinstance(l, dict) and l.get("mechanism")
            ]
            if leaks:
                lines.append("· 关心要以泄露的方式出现：" + "；".join(leaks[:3]))
            forb = [
                str(f.get("phrase") or f.get("id"))
                for f in (ts.get("forbidden_meta_phrases") or [])
                if isinstance(f, dict) and (f.get("phrase") or f.get("id"))
            ]
            if forb:
                lines.append("· 绝对禁止（出现即判定失败）：" + "；".join(forb))
        proh = [
            str(x.get("rule"))
            for x in (p.get("prohibitions") or [])
            if isinstance(x, dict) and x.get("rule")
        ]
        if proh:
            lines.append("禁令：" + "；".join(proh[:6]))
        return "\n".join(lines)[:chars]

    async def _living_world_block(self) -> str:
        w = load_worldview_asset()
        if not w:
            return self._world_block()
        chars = max(300, self._cfg_int("living_worldview_chars", 1200))
        lines: list[str] = ["【世界观（你身处其中，不能说出设定外的概念）】"]
        setting = w.get("setting")
        if isinstance(setting, dict):
            for key in ("era_style", "politics"):
                if setting.get(key):
                    lines.append(str(setting[key]))
            tech = setting.get("technology")
            if isinstance(tech, dict):
                absent = tech.get("absent")
                if isinstance(absent, list) and absent:
                    lines.append(
                        "这个世界没有：" + "、".join(str(x) for x in absent) + "（出现即世界观破产）。"
                    )
            magic = setting.get("magic")
            if isinstance(magic, dict):
                for key in ("association", "how_to_become_witch"):
                    if magic.get(key):
                        lines.append("魔法：" + str(magic[key]))
        cons = [
            str(c.get("constraint"))
            for c in (w.get("environment_constraints") or [])
            if isinstance(c, dict) and c.get("constraint") and c.get("type") == "in_world"
        ]
        if cons:
            lines.append("可执行约束：" + "；".join(cons[:5]))
        taboos = [
            f"{t.get('topic')}（{t.get('handling') or '不主动提'}）"
            for t in (w.get("taboo_topics") or [])
            if isinstance(t, dict) and t.get("topic")
        ]
        if taboos:
            lines.append("禁忌：" + "；".join(taboos[:5]))
        return "\n".join(lines)[:chars]

    async def _living_memory_block(self) -> str:
        if not self._cfg_bool("memory_enabled", True):
            return ""
        try:
            memory = await self._collect_memory()
        except Exception as e:
            logger.warning(f"[living] 取记忆失败: {e}")
            return ""
        if not memory:
            return ""
        try:
            return MEMORY_BLOCK_TEMPLATE.format(memory=self._truncate_memory(memory))
        except Exception:
            return f"\n【关于对方，你记得的事】\n{self._truncate_memory(memory)}\n"

    async def _living_compose(
        self, umo: str, s: dict, intent: str, reason: str, now_dt: datetime
    ) -> str:
        persona_block = await self._living_persona_block()
        world_block = await self._living_world_block()
        memory_block = await self._living_memory_block()
        last = max(float(s.get("last_user_at") or 0), float(s.get("last_proactive_at") or 0))
        gap_min = max(1.0, (now_dt.timestamp() - last) / 60.0)
        label = {
            "share": "分享你自己的一件事",
            "care": "具体地关心对方",
            "followup": "追问之前没说完的话题",
            "curiosity": "对对方提过的事产生新的好奇",
            "check_in": "试探性地确认对方还在不在",
        }.get(intent, intent)
        prompt = LIVING_PROMPT_TEMPLATE
        prompt = prompt.replace("{persona_block}", persona_block)
        prompt = prompt.replace("{world_block}", world_block)
        prompt = prompt.replace("{memory_block}", memory_block)
        prompt = prompt.replace("{now}", now_dt.strftime("%H:%M"))
        prompt = prompt.replace("{period}", self._living_period(now_dt))
        prompt = prompt.replace("{silence}", self._living_human_gap(gap_min))
        prompt = prompt.replace("{intent_label}", label)
        prompt = prompt.replace(
            "{reason_block}", f"（由头：{reason}）" if reason else ""
        )
        try:
            raw = await self._llm_chat(prompt)
        except Exception as e:
            logger.warning(f"[living] 生成主动消息失败: {e}")
            return ""
        text = self._clean_message(raw)
        if not text:
            logger.warning("[living] 生成结果为空，本次放弃")
            return ""
        return self._living_check_text(text)

    @staticmethod
    def _living_check_text(text: str) -> str:
        """阶段一的最小硬约束：命中「元话语式否定」就丢弃这条（阶段五升级为大模型审校）。"""
        forbidden = ("才不是特意", "我才没有", "又不是为了你", "别误会")
        if any(f in text for f in forbidden):
            logger.info(f"[living] 命中元话语式否定，丢弃: {text[:40]}")
            return ""
        if "作为AI" in text or "作为一个AI" in text or "语言模型" in text:
            logger.info(f"[living] 命中 AI 自称，丢弃: {text[:40]}")
            return ""
        return text

    async def _living_fire(self, umo: str, token: str) -> None:
        if not self._living_on():
            return
        state = await self._living_load()
        s = self._living_sstate(state, umo)
        pending = s.get("pending")
        if not isinstance(pending, dict) or pending.get("token") != token:
            logger.info(f"[living] 跳过过期触发（token 不匹配）: {umo}")
            return
        s["pending"] = None
        created = float(pending.get("created") or 0)
        if float(s.get("last_user_at") or 0) > created:
            self._living_log(s, "skip", "用户已回话，本次放弃")
            await self._living_save(state)
            await self._living_schedule_decision(umo)
            return
        now_dt = self._living_now()
        if self._living_in_quiet(now_dt):
            self._living_log(s, "skip", "落在安静时段")
            await self._living_save(state)
            await self._living_schedule_decision(umo)
            return
        text = await self._living_compose(
            umo,
            s,
            str(pending.get("intent") or "share"),
            str(pending.get("reason") or ""),
            now_dt,
        )
        if not text:
            self._living_log(s, "skip", "生成失败或未过硬约束")
            await self._living_save(state)
            await self._living_schedule_decision(umo)
            return
        await self._send_silent(text, [umo])
        today = now_dt.strftime("%Y-%m-%d")
        if s.get("sent_date") != today:
            s["sent_date"] = today
            s["sent_today"] = 0
        s["sent_today"] = int(s.get("sent_today") or 0) + 1
        s["last_proactive_at"] = now_dt.timestamp()
        self._living_log(s, "sent", f"{pending.get('intent')}: {text[:60]}")
        await self._living_save(state)
        logger.info(f"[living] 已发送主动消息（{umo}）：{text[:40]}")
        cooldown = max(1, self._cfg_int("living_cooldown_minutes", 180))
        await self._living_schedule_decision(umo, now_dt.timestamp() + cooldown * 60)

    async def _living_fire_tick(self, umo: str, token: str) -> None:
        async with self._living_lock(umo):
            try:
                await self._living_fire(umo, token)
            except Exception as e:
                logger.error(f"[living] 触发失败 {umo}: {e}", exc_info=True)

    # ------------------------- 用户消息介入 ------------------------- #
    async def _living_on_user_message(self, umo: str) -> None:
        """用户回话：更新活跃基线、取消待触发、把下一次决策推到沉默下限之后。"""
        if not self._living_on() or not umo:
            return
        try:
            state = await self._living_load()
            s = self._living_sstate(state, umo)
            now = time.time()
            s["last_user_at"] = now
            if isinstance(s.get("pending"), dict):
                s["pending"] = None
                self._living_log(s, "cancel", "用户回话，取消待触发")
            nxt = now + max(60, self._cfg_int("living_min_silence_minutes", 90) * 60)
            s["next_decision_at"] = nxt
            await self._living_save(state)
            self._living_add_date_job(
                self._living_decision_tick,
                nxt,
                [umo],
                f"proactive_decision_{self._living_sid(umo)}",
            )
        except Exception as e:
            logger.warning(f"[living] 记录用户活跃失败: {e}")

    async def living_status(self) -> dict:
        """给 /主动状态 命令与排障用：当前每个会话的调度状态。"""
        state = await self._living_load()
        return {
            "enabled": self._living_on(),
            "scheduler_running": bool(
                getattr(getattr(self, "_living_sched", None), "running", False)
            ),
            "sessions": state.get("sessions") or {},
        }

    async def living_status_command(self, event: AstrMessageEvent):
        """查看主动消息 v2 的调度状态（/主动状态）。"""
        st = await self.living_status()
        now = time.time()
        lines: list[str] = []
        if not st["enabled"]:
            lines.append("主动消息 v2：未开启（living_enabled=false，走旧版每日计划发送）")
        else:
            run = "运行中" if st["scheduler_running"] else "未运行（异常！）"
            lines.append(f"主动消息 v2：已开启，插件内部调度器{run}")

        def _ago(ts) -> str:
            try:
                d = int(max(0, now - float(ts or 0)))
            except Exception:
                return "—"
            if d < 60:
                return "刚刚" if d >= 0 else "—"
            if d < 3600:
                return f"{d // 60} 分钟前"
            if d < 86400:
                return f"{d // 3600} 小时前"
            return f"{d // 86400} 天前"

        def _hm(ts) -> str:
            try:
                dt = self._living_dt(float(ts or 0))
                return dt.strftime("%m-%d %H:%M") if dt else "—"
            except Exception:
                return "—"

        sessions = st["sessions"] or {}
        if not sessions:
            lines.append("（暂无被追踪的会话：目标会话解析为空）")
        for umo, s in sessions.items():
            if not isinstance(s, dict):
                continue
            lines.append("")
            lines.append(f"· {self._living_sid(umo)}")
            lines.append(f"  最后互动：{_ago(s.get('last_user_at'))}")
            pend = s.get("pending")
            if isinstance(pend, dict) and pend.get("fire_at"):
                lines.append(
                    f"  待触发：{pend.get('label') or pend.get('intent')} @ {_hm(pend.get('fire_at'))}"
                )
            else:
                lines.append("  待触发：无")
            lines.append(f"  下次决策：{_hm(s.get('next_decision_at'))}")
            lines.append(
                f"  今日已发：{int(s.get('sent_today') or 0)} 条"
                f"（上次主动：{_ago(s.get('last_proactive_at'))}）"
            )
            hist = [h for h in (s.get("history") or []) if isinstance(h, dict)][-5:]
            if hist:
                bits = []
                for h in hist:
                    bits.append(f"{h.get('time', '?')} {h.get('kind', '?')}: {str(h.get('detail') or '')[:28]}")
                lines.append("  最近：" + " / ".join(bits))
        yield event.plain_result(chr(10).join(lines))

# ===========================================================================
# 核心四：Elaina 表情包（本地表情库 + AI 选图，对齐原版 astrbot_plugin_Elaina_meme_Bridge）
# LLM 回复后按概率触发：让 AI 根据对话内容从本地表情库里挑一张最合适的发送。
# 只发本地素材，不做联网表情包搜索 —— 在线搜图容易「搜错、发错、被防盗链」。
# ===========================================================================
class ElainaMemeCore(_PluginCoreBase):
    """Elaina 表情包核心：AI 从本地表情库选图，随 AI 回复发送。

    对齐原版 astrbot_plugin_Elaina_meme_Bridge 的行为：
    - 触发点：LLM 回复之后（`on_llm_response`，priority=99999），按 `meme_probability` 掷骰子；
    - 对话上下文：以 **AI 自己的回复文本** 为准（回复为空才回退用户消息）；
    - 选图：把表情包文件名列表 + 对话内容交给 AI，让它只回一个文件名；
    - 兜底：AI 选的文件名不在库里时随机挑一张；
    - 素材目录：配置 `meme_dir` -> 插件目录 `meme/` -> AstrBot `plugins/*/meme`。

    相对原版的两点工程化补强（不改变对外行为）：
    - AI 回答带引号/多余说明时，用「包含匹配」再对齐一次文件名（原版只做全等匹配，
      不命中就随机，容易发得不准）；
    - 发送走 `owner._gated_send`（合并插件的门禁放行通道）并带超时，避免平台媒体
      上传卡住时阻塞事件流水线。
    """

    def __init__(self, owner: "ElainaSuitePlugin") -> None:
        super().__init__(owner)
        # 本地表情库（懒加载，只解析一次）
        self._meme_dir: str = ""
        self._meme_list: list[str] = []
        self._meme_dir_ready = False

    async def initialize(self) -> None:
        """插件激活时预加载本地表情库（原版在 __init__ 里加载，效果相同）。"""
        memes = self._load_memes()
        if memes:
            logger.info(
                f"[elaina_meme] 本地表情库就绪：{self._meme_dir}（{len(memes)} 个表情包）"
            )
        else:
            logger.warning(
                "[elaina_meme] 未找到本地表情库，表情包不会发送；"
                "请把表情图放进插件目录 meme/，或设置 meme_dir 指向素材目录"
            )

    # ------------------------------------------------------------------ 工具
    def _cfg(self, key: str, default):
        try:
            return self.config.get(key, default)
        except Exception:
            return default

    def _cfg_bool(self, key: str, default: bool) -> bool:
        v = self._cfg(key, default)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    def _cfg_float(self, key: str, default: float) -> float:
        try:
            return float(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def probability(self) -> float:
        """表情包发送概率：优先原版键名 meme_probability，兼容旧的 trigger_prob。"""
        try:
            has_new_key = "meme_probability" in self.config
        except Exception:
            has_new_key = False
        raw = (
            self._cfg("meme_probability", DEFAULT_MEME_PROBABILITY)
            if has_new_key
            else self._cfg("trigger_prob", DEFAULT_MEME_PROBABILITY)
        )
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = DEFAULT_MEME_PROBABILITY
        return max(0.0, min(1.0, value))

    # ------------------------------------------------------------ 本地表情库
    def _resolve_meme_dir(self) -> str:
        """确定本地表情包目录：配置 meme_dir > 插件目录/meme > AstrBot plugins 下任意 */meme。

        第三顺位让本插件也能自动复用「单独安装的原版 Elaina 表情包插件」的 meme/ 素材目录。
        """
        if self._meme_dir_ready:
            return self._meme_dir
        self._meme_dir_ready = True
        configured = str(self._cfg("meme_dir", "") or "").strip()
        candidates: list[str] = []
        if configured:
            candidates.append(configured)
        here = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(here, "meme"))
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            plugins_dir = os.path.join(get_astrbot_data_path(), "plugins")
            if os.path.isdir(plugins_dir):
                for name in sorted(os.listdir(plugins_dir)):
                    d = os.path.join(plugins_dir, name, "meme")
                    if os.path.isdir(d):
                        candidates.append(d)
        except Exception as e:
            logger.debug(f"[elaina_meme] 扫描 plugins 目录失败: {e}")
        for path in candidates:
            try:
                if path and os.path.isdir(path) and any(
                    f.lower().endswith(MEME_EXTENSIONS) for f in os.listdir(path)
                ):
                    self._meme_dir = path
                    logger.info(f"[elaina_meme] 本地表情库: {path}")
                    break
            except Exception:
                continue
        return self._meme_dir

    def _load_memes(self) -> list[str]:
        if self._meme_list:
            return self._meme_list
        path = self._resolve_meme_dir()
        if not path:
            return []
        try:
            self._meme_list = sorted(
                f for f in os.listdir(path) if f.lower().endswith(MEME_EXTENSIONS)
            )
            logger.info(f"[elaina_meme] 已加载 {len(self._meme_list)} 个本地表情包")
        except Exception as e:
            logger.warning(f"[elaina_meme] 读取本地表情目录失败: {e}")
            self._meme_list = []
        return self._meme_list

    # ---------------------------------------------------------------- 选图
    async def _ask_ai_choose_meme(
        self, event: AstrMessageEvent, conversation: str
    ) -> str:
        """让 AI 从本地表情库中挑一个文件名（原版提示词）。"""
        names = "\n".join(self._meme_list)
        prompt = (
            "你是一个表情包助手，名叫 Elaina。根据以下对话内容，"
            "从表情包列表中选择一个最合适的表情包。\n\n"
            f"表情包列表：\n{names}\n\n对话内容：\n{conversation}\n\n"
            "请只回复表情包的文件名（包含扩展名），不要回复其他任何内容。"
        )
        try:
            provider_id = await self.context.get_current_chat_provider_id(
                umo=event.unified_msg_origin
            )
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id, prompt=prompt
            )
            text = ""
            for attr in ("completion_text", "result", "text"):
                v = getattr(resp, attr, None)
                if isinstance(v, str) and v.strip():
                    text = v.strip()
                    break
            return text
        except Exception as e:
            logger.error(f"[elaina_meme] AI 选择表情包失败: {e}")
            return ""

    @staticmethod
    def _match_meme_name(answer: str, memes: list[str]) -> str:
        """把 AI 的回答对齐到表情库里的真实文件名。

        ① 全等（原版行为）-> ② 回答里出现了唯一一个文件名 -> ③ 命中多个时取最长的那个。
        都不命中返回空串，由调用方随机兜底。
        """
        text = (answer or "").strip().strip("`'\"“”《》[]【】").strip()
        if not text:
            return ""
        if text in memes:
            return text
        hits = [m for m in memes if m in text]
        if not hits:
            return ""
        if len(hits) == 1:
            return hits[0]
        return max(hits, key=len)

    # ---------------------------------------------------------------- 发送
    @staticmethod
    def _read_local_file(path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    async def _send_local_meme(self, event: AstrMessageEvent, resp=None) -> bool:
        """从本地表情库选一张并发送（格式/大小校验 + 超时）。"""
        memes = self._load_memes()
        if not memes:
            logger.warning("[elaina_meme] 本地表情库为空，跳过")
            return False
        # 原版以 AI 自己的回复文本作为「对话内容」；回复为空才回退用户消息
        conversation = self._llm_text(resp)
        if not conversation:
            try:
                conversation = (event.message_str or "").strip()
            except Exception:
                conversation = ""
        answer = await self._ask_ai_choose_meme(event, conversation)
        chosen = self._match_meme_name(answer, memes)
        if not chosen:
            if answer:
                logger.info(f"[elaina_meme] AI 选择 `{answer}` 不在库中，改用随机一张")
            chosen = random.choice(memes)
        path = os.path.join(self._resolve_meme_dir(), chosen)
        timeout = self._cfg_int("send_timeout", DEFAULT_SEND_TIMEOUT)
        max_bytes = self._cfg_int("max_meme_mb", DEFAULT_MAX_MEME_MB) * 1024 * 1024
        try:
            data = await asyncio.to_thread(self._read_local_file, path)
        except Exception as e:
            logger.error(f"[elaina_meme] 读取表情包失败 {path}: {e}")
            return False
        if not _is_valid_image(data, max_bytes):
            logger.warning(
                f"[elaina_meme] 表情包不是有效图片或超出大小限制，跳过: {chosen}"
            )
            return False
        try:
            from astrbot.api.message_components import Image as AImage

            img = AImage.fromBytes(data)
            await asyncio.wait_for(
                self.owner._gated_send(
                    event.unified_msg_origin, MessageChain(chain=[img])
                ),
                timeout=timeout,
            )
            logger.info(f"[elaina_meme] 已发送本地表情包: {chosen}")
            return True
        except Exception as e:
            logger.error(f"[elaina_meme] 发送本地表情包失败 {chosen}: {e}")
            return False

    # ---------------------------------------------------------------- 事件
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        """LLM 回复后：按概率让 AI 从本地表情库挑一张发送（原版行为）。"""
        if not self._cfg_bool("meme_enabled", True):
            return
        if not self._load_memes():
            return
        prob = self.probability()
        if random.random() > prob:
            return
        logger.debug(f"[elaina_meme] 本次触发表情包（prob={prob}）")
        await self._send_local_meme(event, resp)

    @staticmethod
    def _llm_text(resp) -> str:
        """取 LLM 回复正文（各版本字段名兼容）。"""
        if resp is None:
            return ""
        for attr in ("completion_text", "result", "text"):
            try:
                v = getattr(resp, attr, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            except Exception:
                continue
        try:
            rc = getattr(resp, "result_chain", None)
            if rc is not None and hasattr(rc, "get_plain_text"):
                t = rc.get_plain_text()
                if t:
                    return t
        except Exception:
            pass
        return ""

    async def terminate(self) -> None:
        """插件卸载时清空表情库缓存。"""
        self._meme_list = []
        self._meme_dir = ""
        self._meme_dir_ready = False


# ===========================================================================
# 功能核心 5：LLM 安全拦截兜底（合并自 astrbot-plugin-llm-guard）
#   部分服务商对高风险 prompt 直接返回 400（content_filter），AstrBot 核心会把
#   原始错误文案当成机器人回复发给用户。本核心在「发送前」阶段拦截并按配置
#   替换/丢弃/仅记录，可选换备用 provider 重发，并可转存当次请求用于定位根因。
# ===========================================================================
GUARD_HARD_MARKERS: tuple[str, ...] = (
    "the request was rejected because it was considered high risk",
    "was rejected because it was considered high risk",
    "all chat models failed",
    "all available chat models are unavailable",
    "no messages remain for the llm request",
    "error occurred during ai execution",
    "error occurred while processing agent request",
    "llm 响应错误",
    "llm 请求失败",
    "阿里云百炼请求失败",
    "coze 请求失败",
    "dify 请求失败",
    "content_filter",
    "请求被拒绝",
)
GUARD_SOFT_MARKERS_EN: tuple[str, ...] = (
    "high risk",
    "rejected because",
    "content policy",
    "risk control",
    "safety check",
    "safe check",
)
GUARD_SOFT_MARKERS_CN: tuple[str, ...] = (
    "内容安全",
    "不予处理",
    "拒绝回答",
    "无法满足该请求",
    "无法处理该请求",
    "请求被拒绝",
    "请求已被拒绝",
    "涉及敏感",
    "包含敏感",
    "不符合内容规范",
)
GUARD_DEFAULT_FALLBACK_TEXT = "（刚才那条没能生成出来，再发一次试试？）"


def _guard_as_bool(value: object, default: bool = True) -> bool:
    """容错解析布尔配置（WebUI 可能存成字符串）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "是")
    if value is None:
        return default
    return bool(value)


def _guard_as_int(value: object, default: int = 0) -> int:
    """容错解析整数配置。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _guard_as_markers(value: object) -> tuple[str, ...]:
    """把自定义特征解析成小写元组（支持逗号 / 换行 / 分号分隔）。"""
    if not value:
        return ()
    if isinstance(value, (list, tuple)):
        raw = [str(item) for item in value]
    else:
        raw = re.split(r"[,;，；\n\r]+", str(value))
    return tuple(item.strip().lower() for item in raw if item.strip())


class LlmGuardCore(_PluginCoreBase):
    """LLM 内容安全拦截兜底：挡住被泄漏的 LLM 错误文案，并可用备用 provider 重发。"""

    def __init__(self, owner: "ElainaSuitePlugin") -> None:
        super().__init__(owner)
        # 最近一次 LLM 请求快照（诊断 + 换 provider 重发用）
        self._last_request: dict = {}
        self._recent: deque[dict] = deque(maxlen=20)
        self._blocked_total = 0
        self._retried_ok = 0

    # ------------------------------------------------------------------ 配置
    def _g(self, key: str, default: object) -> object:
        """读 guard_* 配置（每次现读，WebUI 改动即时生效）。"""
        try:
            return self.config.get("guard_" + key, default)
        except Exception:
            return default

    @property
    def enabled(self) -> bool:
        return _guard_as_bool(self._g("enabled", True), True)

    @property
    def mode(self) -> str:
        value = str(self._g("mode", "replace") or "replace").strip().lower()
        return value if value in ("replace", "drop", "log") else "replace"

    @property
    def fallback_text(self) -> str:
        return str(self._g("fallback_text", GUARD_DEFAULT_FALLBACK_TEXT) or "")

    @property
    def retry_provider_id(self) -> str:
        """重新注入用的 provider（留空 = 用当前会话模型）。"""
        return str(self._g("retry_provider_id", "") or "").strip()

    @property
    def retry_timeout(self) -> int:
        return _guard_as_int(self._g("retry_timeout", 60), 60)

    @property
    def retry_attempts(self) -> int:
        """把用户提示词重新注入 LLM 的尝试次数（1~3）。"""
        return max(1, min(3, _guard_as_int(self._g("retry_attempts", 2), 2)))

    @property
    def retry_keep_system_prompt(self) -> bool:
        """首次重新注入是否携带人设/系统提示词。"""
        return _guard_as_bool(self._g("retry_keep_system_prompt", True), True)

    @property
    def soft_scan(self) -> bool:
        return _guard_as_bool(self._g("soft_scan", False), False)

    @property
    def soft_max_len(self) -> int:
        return _guard_as_int(self._g("soft_max_len", 300), 300)

    @property
    def extra_markers(self) -> tuple[str, ...]:
        return _guard_as_markers(self._g("extra_markers", ""))

    @property
    def patch_custom_error_reply(self) -> bool:
        return _guard_as_bool(self._g("patch_custom_error_reply", True), True)

    @property
    def dump_on_reject(self) -> bool:
        return _guard_as_bool(self._g("dump_on_reject", False), False)

    @property
    def dump_dir(self) -> str:
        return str(self._g("dump_dir", "") or "").strip()

    @property
    def log_prompt_preview(self) -> int:
        return _guard_as_int(self._g("log_prompt_preview", 0), 0)

    # -------------------------------------------------------------- 拦截判定
    def match_reason(self, text: str) -> str | None:
        """判定一段文本是否为「被泄漏的 LLM 错误文案」，命中返回原因。"""
        if not text:
            return None
        stripped = text.strip()
        if not stripped:
            return None
        low = stripped.lower()
        for marker in GUARD_HARD_MARKERS:
            if marker in low:
                return f"hard:{marker}"
        for marker in self.extra_markers:
            if marker and marker in low:
                return f"extra:{marker}"
        if not self.soft_scan:
            return None
        # 二级特征：只对「短文本、且不像成段回复」生效，避免误伤正常聊天
        if len(stripped) > self.soft_max_len:
            return None
        if stripped.count("\n") > 3:
            return None
        for marker in GUARD_SOFT_MARKERS_EN:
            if marker in low:
                return f"soft:{marker}"
        for marker in GUARD_SOFT_MARKERS_CN:
            if marker in stripped:
                return f"soft:{marker}"
        return None

    @staticmethod
    def _plain_text(chain) -> str:
        """拼接消息链里的 Plain 分片文本。"""
        parts: list[str] = []
        for comp in chain or []:
            if isinstance(comp, Plain):
                parts.append(comp.text)
        return "".join(parts)

    # ------------------------------------------------------------ LLM 钩子
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """请求前：挂兜底错误文案 + 记录当次请求快照。"""
        self._ensure_custom_error_reply(event)
        if not self.enabled:
            return
        try:
            prompt = getattr(req, "prompt", "") or ""
            system_prompt = getattr(req, "system_prompt", "") or ""
            contexts = (
                getattr(req, "contexts", None) or getattr(req, "messages", None) or []
            )
            extras = getattr(req, "extra_user_content_parts", None) or []
            extra_texts: list[str] = []
            for part in extras:
                text = getattr(part, "text", None)
                if not isinstance(text, str) and isinstance(part, dict):
                    text = part.get("text")
                if isinstance(text, str) and text.strip():
                    extra_texts.append(text.strip())
            self._last_request = {
                "ts": time.time(),
                "umo": str(getattr(event, "unified_msg_origin", "") or ""),
                "user_message": (event.message_str or "").strip(),
                "prompt": prompt,
                "prompt_len": len(prompt),
                "prompt_sha1": hashlib.sha1(
                    prompt.encode("utf-8", "ignore")
                ).hexdigest(),
                "system_prompt": system_prompt,
                "system_prompt_len": len(system_prompt),
                "n_contexts": len(contexts),
                "extra_parts": extra_texts,
                "n_extra_parts": len(extra_texts),
            }
        except Exception as e:  # 快照失败绝不能影响主流程
            logger.debug(f"[llm_guard] 记录请求快照失败: {e}")

    async def on_decorating_result(self, event: AstrMessageEvent):
        """发送前检查：命中拦截特征时改写或丢弃，可选换 provider 重发。"""
        if not self.enabled:
            return
        try:
            result = event.get_result()
            if result is None or not result.chain:
                return
            text = self._plain_text(result.chain)
            reason = self.match_reason(text)
            if reason is None:
                return
        except Exception as e:
            logger.error(f"[llm_guard] 发送前检查异常，已放行: {e}")
            return

        self._blocked_total += 1
        preview = text.strip().replace("\n", " ")[:160]
        logger.warning(f"[llm_guard] 拦截到被泄漏的 LLM 错误文案（{reason}）：{preview!r}")
        record = {
            "ts": time.time(),
            "reason": reason,
            "text": preview,
            "umo": str(getattr(event, "unified_msg_origin", "") or ""),
        }
        if self.dump_on_reject:
            try:
                path = self._dump_request(reason)
                if path:
                    record["dump"] = str(path)
                    logger.warning(f"[llm_guard] 已转存当次请求到：{path}")
            except Exception as e:
                logger.error(f"[llm_guard] 转存请求失败: {e}")
        if self.log_prompt_preview > 0:
            raw = str((self._last_request or {}).get("prompt") or "")
            if raw:
                logger.warning(
                    f"[llm_guard] 当次 prompt 预览（前 {self.log_prompt_preview} 字）："
                    f"{raw[: self.log_prompt_preview]!r}"
                )
        self._recent.append(record)

        if self.mode == "log":
            return
        if self.mode == "drop":
            event.clear_result()
            logger.info("[llm_guard] 已丢弃该条消息（mode=drop）")
            return

        # mode == "replace"：把用户提示词重新注入 LLM 再生成一次（默认用当前会话模型），
        # 全部尝试仍被拦/失败时才退回静态兜底文案。
        replacement = await self._re_inject_prompt(event)
        if replacement:
            self._retried_ok += 1
            record["retried"] = True
        else:
            replacement = self.fallback_text
        kept = [c for c in result.chain if not isinstance(c, Plain)]
        if replacement.strip():
            kept.append(Plain(replacement))
        if kept:
            result.chain = kept
        else:
            event.clear_result()
        logger.info("[llm_guard] 已替换错误文案（mode=replace）")

    async def _re_inject_prompt(self, event: AstrMessageEvent) -> str:
        """把用户提示词重新注入 LLM 再生成一次回复（不终止本轮对话）。

        策略：
        1. provider 取配置的备用 provider；未配置则用**当前会话的模型**（开箱即用）；
        2. 第 1 次尝试携带原始人设/系统提示词；第 2 次起**只重发用户提示词**
           （去掉人设与历史，通常能绕开由人设/历史触发的风控）；
        3. 每次返回都二次校验，避免把错误文案又发出去；
        4. 全部尝试失败返回空串，由调用方回退兜底文案。
        """
        snapshot = self._last_request or {}
        user_message = str(snapshot.get("user_message") or "").strip()
        if not user_message:
            user_message = (event.message_str or "").strip()
        if not user_message:
            logger.warning("[llm_guard] 无可用的问题文本，跳过重新注入")
            return ""
        provider_id = self.retry_provider_id
        if not provider_id:
            try:
                provider_id = await self.context.get_current_chat_provider_id(
                    umo=event.unified_msg_origin
                )
            except Exception as e:
                logger.warning(f"[llm_guard] 获取当前会话 provider 失败: {e}")
        if not provider_id:
            logger.warning("[llm_guard] 无法确定 provider，跳过重新注入")
            return ""
        system_prompt = str(snapshot.get("system_prompt") or "").strip()
        attempts = self.retry_attempts
        for idx in range(1, attempts + 1):
            kwargs: dict = {
                "chat_provider_id": provider_id,
                "prompt": user_message,
            }
            if idx == 1 and self.retry_keep_system_prompt and system_prompt:
                kwargs["system_prompt"] = system_prompt
            try:
                resp = await asyncio.wait_for(
                    self.context.llm_generate(**kwargs), timeout=self.retry_timeout
                )
            except Exception as e:
                logger.warning(f"[llm_guard] 第 {idx} 次重新注入失败: {e}")
                continue
            text = self._llm_text(resp)
            if text and not self.match_reason(text):
                logger.info(
                    f"[llm_guard] 第 {idx} 次重新注入成功"
                    f"（provider={provider_id}，"
                    f"{'携带人设' if kwargs.get('system_prompt') else '仅用户提示词'}）"
                )
                return text
            logger.warning(f"[llm_guard] 第 {idx} 次重新注入仍被拦截或返回为空")
        return ""

    # 兼容旧名称
    async def _retry_with_provider(self, event: AstrMessageEvent) -> str:
        """[兼容保留] 等价于 _re_inject_prompt。"""
        return await self._re_inject_prompt(event)

    @staticmethod
    def _llm_text(resp: object) -> str:
        if resp is None:
            return ""
        for attr in ("completion_text", "result", "text"):
            value = getattr(resp, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        try:
            chain = getattr(resp, "result_chain", None)
            if chain is not None and hasattr(chain, "get_plain_text"):
                text = chain.get_plain_text()
                if text:
                    return str(text).strip()
        except Exception:
            pass
        return ""

    def _ensure_custom_error_reply(self, event: AstrMessageEvent) -> None:
        """把兜底文案写进事件的 persona 自定义错误消息（仅未配置时）。

        AstrBot 有若干条把错误文案直接 event.send() 的路径不经过 result_decorate 钩子，
        但它们都会优先采用 persona 的自定义错误消息，因此在这里从源头堵住。
        """
        if not self.patch_custom_error_reply:
            return
        text = (self.fallback_text or "").strip()
        if not text:
            return
        try:
            from astrbot.core.persona_error_reply import (
                extract_persona_custom_error_message_from_event,
                set_persona_custom_error_message_on_event,
            )
        except ImportError:
            logger.debug("[llm_guard] 当前 AstrBot 版本无 persona_error_reply，跳过该层防护")
            return
        try:
            if extract_persona_custom_error_message_from_event(event):
                return  # 用户已配置，尊重之
            set_persona_custom_error_message_on_event(event, text)
        except Exception as e:
            logger.debug(f"[llm_guard] 设置自定义错误文案失败: {e}")

    def _dump_request(self, reason: str):
        """把当次请求快照写入本地文件，用于定位风控触发源。"""
        snapshot = self._last_request or {}
        if not snapshot:
            return None
        base = Path(self.dump_dir) if self.dump_dir else Path.cwd() / "llm_guard_dumps"
        base.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = base / f"reject-{stamp}-{snapshot.get('prompt_sha1', 'nohash')[:8]}.json"
        payload = dict(snapshot)
        payload["reason"] = reason
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    # ---------------------------------------------------------------- 指令
    async def llmguard_status(self, event: AstrMessageEvent):
        """查看兜底运行状态与最近拦截记录。"""
        lines = [
            "🛡 LLM 安全拦截兜底",
            f"启用：{'是' if self.enabled else '否'}｜模式：{self.mode}",
            f"重新注入 provider：{self.retry_provider_id or '（当前会话模型）'}",
            f"重新注入尝试：{self.retry_attempts} 次"
            f"（首次{'携带' if self.retry_keep_system_prompt else '不携带'}人设）",
            f"二级特征扫描：{'开' if self.soft_scan else '关'}",
            f"累计拦截：{self._blocked_total}｜重新注入成功：{self._retried_ok}",
        ]
        if self._recent:
            lines.append("")
            lines.append("最近拦截：")
            for item in list(self._recent)[-5:]:
                when = time.strftime("%m-%d %H:%M:%S", time.localtime(item["ts"]))
                lines.append(f"· {when} [{item['reason']}] {item['text'][:60]}")
        snapshot = self._last_request or {}
        if snapshot:
            lines.append("")
            lines.append(
                f"最近请求指纹：prompt {snapshot.get('prompt_len', 0)} 字｜"
                f"system {snapshot.get('system_prompt_len', 0)} 字｜"
                f"历史 {snapshot.get('n_contexts', 0)} 条｜"
                f"注入 {snapshot.get('n_extra_parts', 0)} 段"
            )
        yield event.plain_result("\n".join(lines))

    async def llmguard_dump(self, event: AstrMessageEvent):
        """手动转存最近一次 LLM 请求。"""
        try:
            path = self._dump_request("manual")
        except Exception as e:
            yield event.plain_result(f"❌ 转存失败：{e}")
            return
        if not path:
            yield event.plain_result("⚠️ 还没有记录到任何 LLM 请求")
            return
        yield event.plain_result(f"✅ 已转存最近一次请求到：{path}")


# ===========================================================================
# 功能核心 6：系统健康报告（合并自 astrbot-plugin-system-health）
#   CPU/内存/磁盘/运行时长/进程 → 渲染图片定时推送。
#   psutil 为可选依赖：缺失时该核心自动禁用，不影响其它功能。
# ===========================================================================
try:
    import psutil as _psutil  # type: ignore
except Exception:  # pragma: no cover - 环境缺 psutil 时优雅降级
    _psutil = None


def _health_fmt_duration(seconds: float) -> str:
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    m = int((seconds % 3600) // 60)
    parts: list[str] = []
    if d > 0:
        parts.append(f"{d} 天")
    if h > 0:
        parts.append(f"{h} 小时")
    parts.append(f"{m} 分钟")
    return " ".join(parts)


def _health_cpu_model() -> str:
    system = platform.system()
    try:
        if system == "Windows":
            name = platform.processor()
            return name if name else "N/A"
        if system == "Linux":
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":", 1)[1].strip()
        if system == "Darwin":
            import subprocess

            return subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], timeout=5, text=True
            ).strip()
    except Exception:
        pass
    return "N/A"


class SystemHealthCore(_PluginCoreBase):
    """系统健康报告核心：采集指标并渲染图片定时推送。"""

    def __init__(self, owner: "ElainaSuitePlugin") -> None:
        super().__init__(owner)
        self._fallback_sched = None
        self._cron_job_id = None
        self._start_time = time.time()

    # ------------------------------------------------------------------ 配置
    def _h(self, key: str, default: object = None) -> object:
        try:
            return self.config.get("health_" + key, default)
        except Exception:
            return default

    def _h_bool(self, key: str, default: bool) -> bool:
        return _guard_as_bool(self._h(key, default), default)

    @property
    def enabled(self) -> bool:
        return self._h_bool("enabled", True) and _psutil is not None

    async def initialize(self) -> None:
        if _psutil is None:
            logger.warning(
                "[system_health] 未安装 psutil，系统健康报告已禁用（pip install psutil 后重载插件即可启用）"
            )
            return
        if self._h_bool("enabled", True):
            await self._setup_scheduler()

    async def terminate(self) -> None:
        try:
            cm = getattr(self.context, "cron_manager", None)
            if cm is not None and hasattr(cm, "delete_job") and self._cron_job_id:
                await cm.delete_job(self._cron_job_id)
                self._cron_job_id = None
        except Exception as e:
            logger.warning(f"[system_health] cron 清理失败: {e}")
        try:
            if self._fallback_sched:
                self._fallback_sched.shutdown(wait=False)
                self._fallback_sched = None
        except Exception:
            pass

    async def _setup_scheduler(self) -> None:
        cron = str(self._h("cron", "0 6 * * *") or "0 6 * * *").strip()
        tz = str(self._h("timezone", "Asia/Shanghai") or "Asia/Shanghai").strip()
        try:
            cm = getattr(self.context, "cron_manager", None)
            if cm is not None and hasattr(cm, "add_basic_job"):
                try:
                    job = await cm.add_basic_job(
                        name="elaina_health",
                        cron_expression=cron,
                        handler=self._on_schedule,
                        description="系统健康报告",
                        enabled=True,
                        persistent=False,
                        timezone=tz,
                    )
                except TypeError:
                    job = await cm.add_basic_job(
                        name="elaina_health",
                        cron_expression=cron,
                        handler=self._on_schedule,
                        description="系统健康报告",
                        enabled=True,
                        persistent=False,
                    )
                self._cron_job_id = getattr(job, "job_id", None)
                logger.info(f"[system_health] 定时任务已注册: {cron} tz={tz}")
                return
        except Exception as e:
            logger.warning(f"[system_health] cron_manager 注册失败，回退 APScheduler: {e}")
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger

            self._fallback_sched = AsyncIOScheduler(timezone=tz)
            self._fallback_sched.add_job(
                self._on_schedule,
                CronTrigger.from_crontab(cron, timezone=tz),
                id="elaina_health",
                misfire_grace_time=60,
            )
            self._fallback_sched.start()
            logger.info(f"[system_health] 定时任务已注册（APScheduler）: {cron} tz={tz}")
        except Exception as e:
            logger.error(f"[system_health] 定时任务注册失败: {e}")

    async def _on_schedule(self) -> None:
        if not self.enabled:
            return
        try:
            await self.send_health_report()
        except Exception as e:
            logger.error(f"[system_health] 定时发送失败: {e}", exc_info=True)

    async def health_command(self, event: AstrMessageEvent):
        """立即生成一份系统健康报告。"""
        if _psutil is None:
            yield event.plain_result("⚠️ 未安装 psutil，系统健康报告不可用")
            return
        asyncio.create_task(self.send_health_report())
        yield event.plain_result("⏳ 正在生成系统健康报告…")

    async def send_health_report(self) -> None:
        targets = await self._collect_target_sessions()
        if not targets:
            logger.warning("[system_health] 没有可推送的会话")
            return
        text = self._build_report_text()
        try:
            url = await self.text_to_image(text, return_url=True)
            if url:
                chain = _image_chain(url)
                ok = 0
                for umo in targets:
                    try:
                        await asyncio.wait_for(
                            self.owner._gated_send(umo, chain),
                            timeout=90,
                        )
                        ok += 1
                    except Exception as e:
                        logger.error(f"[system_health] 图片发送到 {umo} 失败: {e}")
                    await asyncio.sleep(0.3)
                if ok:
                    logger.info(f"[system_health] 报告已渲染为图片发送（成功 {ok}/{len(targets)}）")
                    return
                logger.warning("[system_health] 图片发送全部失败，回退文本")
        except Exception as e:
            logger.warning(f"[system_health] 图片渲染失败，回退文本: {e}")
        chunks = self._split_text(text)
        for umo in targets:
            for chunk in chunks:
                try:
                    await self.owner._gated_send(umo, MessageChain().message(chunk))
                    await asyncio.sleep(0.8)
                except Exception as e:
                    logger.error(f"[system_health] 文本发送到 {umo} 失败: {e}")

    def _build_report_text(self) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        L: list[str] = []
        L.append("🖥 系统健康报告")
        L.append(f"📅 {now}")
        L.append("")
        # 系统
        L.append("════ 📋 系统信息 ════")
        L.append(f"  系统：{platform.system()} {platform.release()}")
        L.append(f"  版本：{platform.version()}")
        L.append(f"  架构：{platform.machine()}")
        L.append(f"  主机：{socket.gethostname()}")
        L.append(f"  Python：{platform.python_version()}")
        # CPU
        L.append("")
        L.append("════ 🧠 CPU ════")
        try:
            cpu = _health_cpu_model()
            if len(cpu) > 55:
                cpu = cpu[:52] + "..."
            phys = _psutil.cpu_count(logical=False) or "?"
            logic = _psutil.cpu_count(logical=True) or "?"
            pct = _psutil.cpu_percent(interval=1)
            L.append(f"  型号：{cpu}")
            L.append(f"  核心：{phys} 物理 / {logic} 逻辑")
            L.append(f"  使用率：{pct:.1f}%")
            if hasattr(os, "getloadavg"):
                l1, l5, l15 = os.getloadavg()
                L.append(f"  负载：{l1:.2f} / {l5:.2f} / {l15:.2f}")
        except Exception as e:
            L.append(f"  获取 CPU 信息失败：{e}")
        # 内存
        L.append("")
        L.append("════ 💾 内存 ════")
        try:
            mem = _psutil.virtual_memory()
            L.append(f"  总计：{mem.total / (1024 ** 3):.2f} GB")
            L.append(
                f"  已用：{mem.used / (1024 ** 3):.2f} GB ({mem.percent:.1f}%)"
            )
            L.append(f"  可用：{mem.available / (1024 ** 3):.2f} GB")
        except Exception as e:
            L.append(f"  获取内存信息失败：{e}")
        try:
            sw = _psutil.swap_memory()
            if sw.total > 0:
                L.append(f"  交换：{sw.total / (1024 ** 3):.2f} GB (已用 {sw.percent:.1f}%)")
        except Exception:
            pass
        # 磁盘
        if self._h_bool("show_disk", True):
            L.append("")
            L.append("════ 💿 磁盘 ════")
            try:
                seen: set[str] = set()
                for p in _psutil.disk_partitions(all=False):
                    if p.mountpoint in seen:
                        continue
                    seen.add(p.mountpoint)
                    try:
                        u2 = _psutil.disk_usage(p.mountpoint)
                        L.append(
                            f"  {p.mountpoint} {u2.total / (1024 ** 3):.0f}GB "
                            f"已用 {u2.used / (1024 ** 3):.0f}GB ({u2.percent:.1f}%) "
                            f"剩余 {u2.free / (1024 ** 3):.0f}GB"
                        )
                    except Exception:
                        pass
            except Exception as e:
                L.append(f"  获取磁盘信息失败：{e}")
        # 运行时长
        L.append("")
        L.append("════ ⏱ 运行时长 ════")
        try:
            L.append(f"  系统运行：{_health_fmt_duration(time.time() - _psutil.boot_time())}")
        except Exception as e:
            L.append(f"  系统运行时长：获取失败（{e}）")
        L.append(f"  插件运行：{_health_fmt_duration(time.time() - self._start_time)}")
        # 网络
        if self._h_bool("show_network", False):
            L.append("")
            L.append("════ 📡 网络 ════")
            try:
                for iface, addrs in _psutil.net_if_addrs().items():
                    for a in addrs:
                        if a.family.name == "AF_INET":
                            L.append(f"  {iface}：{a.address}")
            except Exception as e:
                L.append(f"  获取网络信息失败：{e}")
        # 进程
        L.append("")
        L.append("════ 📊 当前进程 ════")
        try:
            proc = _psutil.Process(os.getpid())
            mi = proc.memory_info()
            L.append(f"  PID：{proc.pid}")
            L.append(f"  线程数：{proc.num_threads()}")
            L.append(f"  RSS：{mi.rss / (1024 ** 2):.1f} MB")
            L.append(f"  VMS：{mi.vms / (1024 ** 2):.1f} MB")
            try:
                L.append(f"  CPU 占用：{proc.cpu_percent(interval=0.1):.1f}%")
            except Exception:
                pass
        except Exception as e:
            L.append(f"  获取进程信息失败：{e}")
        L.append("")
        L.append("═" * 31)
        L.append(f"📊 报告生成于 {now}")
        return "\n".join(L)

    async def _collect_target_sessions(self) -> list[str]:
        sessions: set[str] = set()
        cfg = self._h("target_sessions")
        if cfg:
            for line in str(cfg).splitlines():
                s = line.strip()
                if s and ":" in s:
                    sessions.add(s)
            if sessions:
                return sorted(sessions)
        try:
            db = self.context.get_db()
            from sqlalchemy import select

            from astrbot.core.db.po import ConversationV2

            async with db.get_db() as sess:
                res = await sess.execute(select(ConversationV2.user_id).distinct())
                for row in res:
                    v = row[0]
                    if v and ":" in str(v):
                        sessions.add(str(v))
        except Exception as e:
            logger.warning(f"[system_health] 数据库枚举会话失败: {e}")
        try:
            tracked = await self.get_kv_data("subscribed_sessions", []) or []
            for s in tracked:
                if ":" in str(s):
                    sessions.add(str(s))
        except Exception:
            pass
        return sorted(sessions)

    @staticmethod
    def _split_text(text: str, limit: int = 3800) -> list[str]:
        text = text.strip()
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        cur = ""
        for line in text.splitlines(keepends=True):
            while len(line) > limit:
                if cur:
                    chunks.append(cur)
                    cur = ""
                chunks.append(line[:limit])
                line = line[limit:]
            if cur and len(cur) + len(line) > limit:
                chunks.append(cur)
                cur = line
            else:
                cur += line
        if cur:
            chunks.append(cur)
        return chunks


# ===========================================================================
# 主插件类：AstrBot 只注册一个 Star 类（ElainaSuitePlugin），
# 持有 6 个功能核心实例并负责事件/指令/LLM 钩子的路由。
# ===========================================================================
class ElainaSuitePlugin(Star):
    """Elaina 工具箱（六合一）：图片问答桥接 + 每日简报 + 主动消息门禁/人格消息 + 表情包 + LLM 拦截兜底 + 系统健康报告。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        # 门禁放行计数：本合并插件内部主动发送（guard 人格消息、digest 日报定时推送）
        # 发送期间 +1，让 proactive_guard 门禁放行，避免被误判为外部主动发言
        self._bypass_cnt = 0
        self.image_bridge = ImageBridgeCore(self)
        self.daily_digest = DailyDigestCore(self)
        self.proactive_guard = ProactiveGuardCore(self)
        self.meme = ElainaMemeCore(self)
        self.llm_guard = LlmGuardCore(self)
        self.system_health = SystemHealthCore(self)
        # 启动就补齐中文字体：图片渲染走本地 PIL 时全靠它
        _ensure_cjk_font()

    # ------------------------------------------------------------------ #
    # 门禁放行（供各核心主动发送使用）
    # ------------------------------------------------------------------ #
    async def _gated_send(self, *args) -> bool:
        """发送消息（带门禁放行标记）。

        合并插件内部的主动发送（日报、人格消息、健康报告、表情包）发送期间把
        _bypass_cnt 加一，让 proactive_guard 门禁放行，避免被误判成「外部插件主动发言」。
        """
        self._bypass_cnt += 1
        try:
            return bool(await self.context.send_message(*args))
        finally:
            self._bypass_cnt -= 1

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def initialize(self) -> None:
        """插件激活时：注册定时任务 + 安装门禁 + 预加载本地表情库。"""
        await self.daily_digest.initialize()
        await self.proactive_guard.initialize()
        await self.system_health.initialize()
        await self.meme.initialize()

    async def terminate(self) -> None:
        """插件禁用/重载时：依次清理 6 个核心。"""
        await self.image_bridge.terminate()
        await self.meme.terminate()
        await self.daily_digest.terminate()
        await self.proactive_guard.terminate()
        await self.system_health.terminate()

    # ------------------------------------------------------------------ #
    # 消息事件路由（2 个观察者互不干扰）
    # ------------------------------------------------------------------ #
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def image_bridge_on_message(self, event: AstrMessageEvent):
        """图片问答桥接：识别图片/表情并挂起；纯图片消息拦截等待提问。"""
        await self.image_bridge.on_message(event)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def guard_on_user_message(self, event: AstrMessageEvent):
        """门禁：记录会话最近活跃时间（不产生任何回复）。"""
        await self.proactive_guard._on_user_message(event)

    # ------------------------------------------------------------------ #
    # LLM 钩子路由
    # ------------------------------------------------------------------ #
    @filter.on_llm_request()
    async def image_bridge_on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """图片问答桥接：把挂起的图片识别内容注入本次 LLM 请求。"""
        await self.image_bridge.on_llm_request(event, req)

    @filter.on_llm_request()
    async def llm_guard_on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 拦截兜底：挂兜底错误文案 + 记录请求快照。"""
        await self.llm_guard.on_llm_request(event, req)

    @filter.on_llm_response(priority=99999)
    async def meme_on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        """表情包：按概率让 AI 从本地表情库挑一张，随回复发送。"""
        await self.meme.on_llm_response(event, resp)

    @filter.on_decorating_result()
    async def llm_guard_on_decorating_result(self, event: AstrMessageEvent):
        """LLM 拦截兜底：发送前拦截被泄漏的错误文案并按配置替换/丢弃。"""
        await self.llm_guard.on_decorating_result(event)

    # ------------------------------------------------------------------ #
    # 指令路由
    # ------------------------------------------------------------------ #
    @filter.command("picreset")
    async def picreset(self, event: AstrMessageEvent):
        """清除本会话挂起的图片识别内容。"""
        async for result in self.image_bridge.picreset(event):
            yield result

    @filter.command("digest", alias={"日报"})
    async def digest_command(self, event: AstrMessageEvent):
        """立即生成并推送一份每日简报。"""
        async for result in self.daily_digest.digest_command(event):
            yield result

    @filter.command("订阅日报")
    async def subscribe_command(self, event: AstrMessageEvent):
        """订阅每日简报（定时推送到当前会话）。"""
        async for result in self.daily_digest.subscribe_command(event):
            yield result

    @filter.command("退订日报")
    async def unsubscribe_command(self, event: AstrMessageEvent):
        """退订每日简报。"""
        async for result in self.daily_digest.unsubscribe_command(event):
            yield result

    @filter.command("今日计划")
    async def today_plan_command(self, event: AstrMessageEvent):
        """查看今日待发送的人格消息计划 + 明日预生成摘要。"""
        async for result in self.proactive_guard.today_plan_command(event):
            yield result

    @filter.command("重建今日计划")
    async def rebuild_plan_command(self, event: AstrMessageEvent):
        """立即在后台重新生成明天的计划。"""
        async for result in self.proactive_guard.rebuild_plan_command(event):
            yield result

    @filter.command("主动状态", alias={"主动消息状态"})
    async def living_status_cmd(self, event: AstrMessageEvent):
        """查看主动消息 v2 的调度状态（会话、下次决策、待触发、今日已发）。"""
        async for result in self.proactive_guard.living_status_command(event):
            yield result

    @filter.command("记忆")
    async def memory_command(self, event: AstrMessageEvent):
        """查看当前记忆库内容。"""
        async for result in self.proactive_guard.memory_command(event):
            yield result

    @filter.command("刷新记忆")
    async def refresh_memory_command(self, event: AstrMessageEvent):
        """立刻让 AI 总结一次对话，写入记忆库。"""
        async for result in self.proactive_guard.refresh_memory_command(event):
            yield result

    @filter.command("记忆重置")
    async def reset_memory_command(self, event: AstrMessageEvent):
        """清空记忆库并按当前规则重建（洗掉历史遗留的错误条目）。"""
        async for result in self.proactive_guard.reset_memory_command(event):
            yield result

    @filter.command("health", alias={"健康报告"})
    async def health_command(self, event: AstrMessageEvent):
        """立即生成一份系统健康报告。"""
        async for result in self.system_health.health_command(event):
            yield result

    @filter.command("llmguard", alias={"拦截状态"})
    async def llmguard_status(self, event: AstrMessageEvent):
        """查看 LLM 拦截兜底的运行状态与最近拦截记录。"""
        async for result in self.llm_guard.llmguard_status(event):
            yield result

    @filter.command("llmguarddump", alias={"转存请求"})
    async def llmguard_dump(self, event: AstrMessageEvent):
        """手动转存最近一次 LLM 请求，便于定位风控触发源。"""
        async for result in self.llm_guard.llmguard_dump(event):
            yield result

