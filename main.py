"""
astrbot_plugin_elaina_suite —— Elaina 工具箱（六合一合并插件）

由 SeaSmall 的 6 个独立插件合并而来（功能不变，互相兼容）：
1. 图片问答桥接（ImageBridgeCore）：图片按「小米 MiMo → 百度图像识别 → OCR.space」
   三级优先链识别，用户随后发送文字提问时把识别内容一并注入 LLM（图片门控问答）。
2. 每日简报（DailyDigestCore）：天气（多城市）+ 昨日国内/国际新闻 + 科技/医药/政策前沿、
   GitHub 日升榜，AI 总结后定时推送（含补发兜底与拦截降级）。
3. 主动消息门禁 + 每日人格消息（ProactiveGuardCore）：拦截非本插件的 AI 主动发言；
   每天后台预生成「明天」的 5-10 条随机时间点人格消息，到点自动发送。
4. Elaina 表情包（ElainaMemeCore）：本地表情库（AI 选图）或免费 API 关键词抓图，
   按概率随回复发送；自动复用已安装的 Elaina 表情包素材目录。
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
  meme_responder : enabled -> meme_enabled
  llm_guard      : 全部加 guard_ 前缀（enabled -> guard_enabled, mode -> guard_mode ...）
  system_health  : timezone -> health_timezone, target_sessions -> health_target_sessions,
                   show_disk -> health_show_disk, show_network -> health_show_network
  Elaina 表情包  : 新增 meme_source / meme_dir（复用原插件同名键）
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
DEFAULT_XIAOMI_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
DEFAULT_XIAOMI_MODEL = "mimo-v2.5"  # 支持图片理解；也可用 mimo-v2.5-pro
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
        "https://www.chinadaily.com.cn/rss/world_rss.xml",
    ],
    "feeds_tech": [
        "https://www.ithome.com/rss/",
        "https://sspai.com/feed",
        "https://www.ifanr.com/feed",
        "https://www.geekpark.net/rss",
    ],
    "feeds_medical": [
        "https://www.who.int/rss-feeds/news-english.xml",
        "https://www.nature.com/nm.rss",
    ],
    "feeds_policy": [
        "cctv:news",
        "baidu:hot",
    ],
}

# 板块顺序：(启用开关配置项, feed 配置项, 板块名, 图标)
SECTIONS = [
    ("news_cn_enabled", "feeds_cn", "昨日国内", "🇨🇳"),
    ("news_intl_enabled", "feeds_intl", "昨日国际", "🌍"),
    ("tech_enabled", "feeds_tech", "科技前沿", "💻"),
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

# 天气：每日 Open-Meteo 调用次数上限（保险，防止误触发限流）
WEATHER_DAILY_CALL_LIMIT = 120

DIGEST_DEFAULT_PROMPT = """你是一名严谨、简洁的中文每日简报编辑。今天是 {date}。
请根据下面的原始资讯生成【每日简报】，要求：
1. 按板块输出：🌤️ 天气、🇨🇳 昨日国内、🌍 昨日国际、💻 科技前沿、💊 医药前沿、📜 政策前沿、🚀 GitHub 日升榜（有数据的板块必须全部输出，不要遗漏任何板块）
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

GUARD_DEFAULT_PROMPT = """你是{persona}
今天是{date}。请在下面给出的 {count} 个时间点，分别给用户写一条简短消息（20~60字），要求：
1. 每条消息要贴合对应时间点的场景（早晨问候、上午加油、中午提醒休息吃饭、下午闲聊、傍晚关心、晚上问候、睡前道晚安等）
2. 每条内容必须不同，风格自然口语化，像真人朋友随手发的一条
3. 只输出一个 JSON 数组，格式：[{"time": "07:35", "text": "消息内容"}, ...]
4. 不要输出 JSON 以外的任何文字
时间点：{time_list}"""

# ---------------------------------------------------------------------------
# AI 表情包回复：默认配置
# ---------------------------------------------------------------------------
DEFAULT_TRIGGER_PROB = 0.5  # 触发概率
DEFAULT_MEME_KEYWORDS = "无语、开心、难过、加油、厉害了、干得漂亮、生气、尴尬、笑死、委屈、点赞、疑问"  # 备用随机关键词
DEFAULT_API_URL_TANGDOUZ = "https://api.tangdouz.com/a/biaoq.php"
DEFAULT_API_URL_APIHZ_SOGOU = "https://cn.apihz.cn/api/img/apihzbqbsougou.php"
DEFAULT_API_URL_APIHZ_BAIDU = "https://cn.apihz.cn/api/img/apihzbqbbaidu.php"

# 注入给 LLM 的提示词模板（要求 AI 输出关键词）
MEME_PROMPT_TEMPLATE = (
    "\n\n【附加指令（不要告诉用户）】\n"
    "在正常回答用户的同时，请根据当前对话的情绪/语境，在心里想一个最适合此时发送的"
    "表情包搜索关键词（2~6 个字，如：无语、开心、干得漂亮、加油、笑死、尴尬）。\n"
    "请在你的回复末尾另起一行，单独输出一行，格式为：\n"
    "{{MEME_KEYWORD}}关键词{{/MEME_KEYWORD}}\n"
    "不要输出其他多余内容在这一行。若你判断完全不需要表情包，输出 {{MEME_KEYWORD}}无{{/MEME_KEYWORD}}。"
)

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
        """小米 MiMo Token Plan 识图模型（OpenAI 兼容，图片以 base64 data URI 传入）。"""
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
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_completion_tokens": 1024,
        }
        headers = {
            "api-key": api_key,  # 官方文档 curl 示例使用的鉴权头
            "Content-Type": "application/json",
        }
        url = f"{base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"小米接口 HTTP {resp.status_code}: {resp.text[:200]}")
        result = resp.json()
        try:
            content = (
                ((result.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            )
        except Exception:
            raise RuntimeError(f"小米接口返回异常: {str(result)[:200]}") from None
        if not content.strip():
            raise RuntimeError("小米接口未返回识别内容")
        return content.strip()

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
        # 天气防高并发状态：每城市单飞锁 + 全局请求节流 + 每日调用计数
        self._weather_locks: dict[str, asyncio.Lock] = {}
        self._last_weather_call = 0.0
        self._weather_call_day = ""
        self._weather_call_count = 0

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

    async def _maybe_send_digest(self) -> None:
        """每天最多发送一次：到达发送时间且在补发窗口内则生成并发送，成功后记录已发日期。"""
        try:
            now = self._now_local()
            today = now.strftime("%Y-%m-%d")
            try:
                last = (await self.get_kv_data("last_digest_date", "")) or ""
            except Exception:
                last = ""
            if last == today:
                return
            start_h, start_m = self._send_window_start()
            deadline_h, deadline_m = self._send_deadline()
            now_min = now.hour * 60 + now.minute
            if now_min < start_h * 60 + start_m:
                return  # 还没到发送时间
            if now_min > deadline_h * 60 + deadline_m:
                return  # 超过补发截止时间，今天放弃（避免深夜误发）
            targets = await self._collect_target_sessions()
            if not targets:
                logger.warning("[daily_digest] 没有可推送的会话，跳过本次发送")
                return
            await self._generate_and_send(targets)
            try:
                await self.put_kv_data("last_digest_date", today)
            except Exception as e:
                logger.debug(f"[daily_digest] 记录发送日期失败: {e}")
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
        text  -> 纯文本分条发送
        image -> 渲染为图片发送（AstrBot t2i）
        file  -> 保存为 .md 文件发送（需平台支持本地文件）
        auto  -> 短文本直接发文本；超过 digest_long_threshold 时自动转图片，失败回退 md 文件/文本
        """
        mode = str(self.config.get("digest_send_mode") or "auto").strip().lower()
        threshold = self._cfg_int("digest_long_threshold", 2000)
        if mode not in ("text", "image", "file", "auto"):
            mode = "auto"
        long_mode = "text"
        if mode in ("image", "file"):
            long_mode = mode
        elif mode == "auto" and len(text) > threshold:
            long_mode = "image"
        if long_mode != "text":
            ok = await self._send_long(text, targets, long_mode)
            if ok:
                return
        for umo in targets:
            await self._send_text(umo, text)

    async def _send_long(self, text: str, targets: list[str], mode: str) -> bool:
        """长简报：优先渲染为图片；失败则保存为 .md 文件；都不行返回 False 回退文本。
        注意：QQ 官方等平台媒体上传可能长时间重试（约 90s），这里加超时与「全目标失败回退」，
        避免日报因图片上传失败而整体丢失。"""
        # 1) 渲染图片（AstrBot text_to_image，全平台可发）
        try:
            url = await self.text_to_image(text, return_url=True)
            if url:
                ok_cnt = 0
                for umo in targets:
                    try:
                        await asyncio.wait_for(
                            self._gated_send(umo, MessageChain().url_image(url)),
                            timeout=90,
                        )
                        ok_cnt += 1
                    except Exception as e:
                        logger.error(f"[daily_digest] 图片发送到 {umo} 失败: {e}")
                    await asyncio.sleep(0.3)
                if ok_cnt == 0:
                    logger.warning(
                        "[daily_digest] 图片发送全部失败（可能为平台媒体上传问题），回退文本"
                    )
                else:
                    logger.info(
                        f"[daily_digest] 简报已渲染为图片发送（成功 {ok_cnt}/{len(targets)}）"
                    )
                    return True
        except Exception as e:
            logger.warning(f"[daily_digest] 简报渲染图片失败: {e}")
        # 2) md 文件（部分平台支持本地文件）
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
            logger.warning("[daily_digest] md 文件发送全部失败，回退文本")
        except Exception as e:
            logger.warning(f"[daily_digest] 发送 md 文件失败，回退文本: {e}")
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
        for entry in self._feed_urls(feed_key):
            for attempt in (1, 2):  # 抖动网络重试一次
                try:
                    if self._is_json_source(entry):
                        items.extend(await self._fetch_json_source(entry, max_items))
                    else:
                        data = await asyncio.to_thread(self._http_get_bytes, entry)
                        items.extend(self._parse_feed(data))
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
        return self._filter_yesterday(items)[:max_items]

    @staticmethod
    def _is_json_source(entry: str) -> bool:
        return entry.startswith("cctv:") or entry in ("tencent:hot", "baidu:hot")

    async def _fetch_json_source(self, token: str, max_items: int) -> list[dict]:
        """抓取免费中文 JSON 新闻源（央视 / 腾讯热榜 / 百度热搜）。
        热榜类条目按「最新」处理（pub_date=当前时间），通过时效过滤。"""
        now = datetime.now().astimezone()
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
    def _http_get_bytes(url: str, timeout: int = 15) -> bytes:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
        )
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

    def _filter_yesterday(self, items: list[dict]) -> list[dict]:
        """严格时效过滤：只保留「昨日」条目；不足时回退到近 24 小时。
        注意：不回退到更早的条目——死源（如停更的 RSS）不会把陈旧内容当「昨日」展示。"""
        now = datetime.now().astimezone()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)
        cutoff_24h = now - timedelta(hours=24)

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

        hit = [it for it in dated if yesterday_start <= it["pub_date"] < today_start]
        if len(hit) < 2:
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
            "🌤️ 天气、🇨🇳 昨日国内、🌍 昨日国际、💻 科技前沿、💊 医药前沿、"
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
        self._last_gen_attempt = 0.0
        self._migrated = False

    # ================================================================== #
    # 生命周期
    # ================================================================== #
    async def initialize(self) -> None:
        """插件激活时：安装门禁 -> 暂停内置主动任务 -> 注册定时任务"""
        if self._cfg_bool("pg_enabled", True):
            if self._cfg_bool("block_proactive", True):
                self._install_gate()
            await self._pause_active_agent_jobs()
            await self._setup_scheduler()

    async def terminate(self) -> None:
        """插件禁用/重载时：卸载门禁、删除定时任务"""
        ctx = self.context
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
        gen_cron = str(self.config.get("gen_time") or "0 6 * * *").strip()
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
            self._fallback_sched.start()
            logger.info(
                f"[proactive_guard] 定时任务已注册（APScheduler）: 生成 {gen_cron} "
                f"/ 分钟检查（时区 {tz}）"
            )
        except Exception as e:
            logger.error(f"[proactive_guard] 定时任务注册失败: {e}")

    async def _on_gen_time(self) -> None:
        """到生成时间：确保今日计划存在、明日计划已预生成（后台静默）。"""
        try:
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

    async def _generate_and_store(self, target_date: str) -> None:
        """后台为指定日期生成计划并写入 KV 存储（静默，不通知用户）。

        目标日期为明天 -> 存 daily_plan_next（预生成）；
        目标日期为今天 -> 存 daily_plan（/重建今日计划 在窗口未结束时补今天的场景）。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        slot = "daily_plan" if target_date == today else "daily_plan_next"
        count = random.randint(
            self._cfg_int("msg_count_min", 5), self._cfg_int("msg_count_max", 10)
        )
        times = self._random_times(count)
        time_list = "、".join(times)
        persona = await self._resolve_persona()
        prompt = (self.config.get("message_prompt") or GUARD_DEFAULT_PROMPT).replace(
            "{date}", target_date
        ).replace("{persona}", persona).replace(
            "{time_list}", time_list
        ).replace("{count}", str(count))
        text = await self._llm_chat(prompt)
        items = self._parse_items(text, times)
        if not items:
            logger.error(
                f"[proactive_guard] 计划生成失败（{target_date}）：无法解析 LLM 输出，稍后重试"
            )
            return
        await self.put_kv_data(slot, {"date": target_date, "items": items})
        logger.info(
            f"[proactive_guard] 计划已生成（{target_date}）：{len(items)} 条消息 "
            f"@ {time_list}（后台静默）"
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
        t = t.strip()
        try:
            h, m = t.split(":")
            return f"{int(h):02d}:{int(m):02d}"
        except Exception:
            return t

    def _parse_items(self, text: str, expected_times: list[str]) -> list[dict]:
        items: list[dict] = []
        # 1) JSON 数组
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            try:
                arr = json.loads(m.group(0))
                if isinstance(arr, list):
                    for obj in arr:
                        if not isinstance(obj, dict):
                            continue
                        t = self._normalize_time(str(obj.get("time", "")).strip())
                        msg = str(
                            obj.get("text") or obj.get("message") or ""
                        ).strip()
                        if t in expected_times and msg:
                            items.append({"time": t, "text": msg})
            except Exception:
                pass
        # 2) 行解析：HH:MM|text / HH:MM: text / HH:MM text（去掉行首编号后匹配）
        if not items:
            for line in text.splitlines():
                line = re.sub(r"^(?:\d+\s*[.、)）\-]\s*)+", "", line.strip())
                m2 = re.match(
                    r"^(\d{1,2}:\d{2})\s*[:|\-]\s*(.+)$", line
                ) or re.match(r"^(\d{1,2}:\d{2})\s+(.+)$", line)
                if m2:
                    t = self._normalize_time(m2.group(1))
                    msg = m2.group(2).strip()
                    if t in expected_times and msg:
                        items.append({"time": t, "text": msg})
        # 3) 去重（同一时间只保留一条）
        seen: set[str] = set()
        dedup: list[dict] = []
        for it in items:
            if it["time"] not in seen:
                seen.add(it["time"])
                dedup.append(it)
        return dedup

    async def _dispatch_due(self) -> None:
        """只从 daily_plan（date==今天）派发：到点发送并删除该时间点；
        错过在补偿窗口内补发，超窗丢弃；date<今天的旧计划静默丢弃（由 _ensure_schedule 清理）。"""
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
        for it in due:
            await self._send_silent(str(it.get("text", "")), targets)
            removed.append(str(it.get("time", "")))
            logger.info(f"[proactive_guard] 已发送 {it.get('time')} 的消息并移除该时间点")
        if removed:
            plan["items"] = [
                it for it in plan["items"] if str(it.get("time", "")) not in removed
            ]
            await self.put_kv_data("daily_plan", plan)

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

# ===========================================================================
# 核心四：Elaina 表情包（本地表情库 + 关键词在线抓图，合并 memeresponder 与 Elaina_meme_Bridge）
# 用户消息按概率触发，AI 输出表情关键词，调用免费表情包搜索 API 抓图发送。
# ===========================================================================
class ElainaMemeCore(_PluginCoreBase):
    """Elaina 表情包核心：支持「本地表情库（AI 选图）」与「关键词在线抓图」两种模式。

    - library：从本地表情包目录里让 AI 挑一张（原 astrbot_plugin_Elaina_meme_Bridge 行为）；
    - keyword：让 AI 顺带输出表情关键词，从免费 API 抓图（原 meme_responder 行为）；
    - auto（默认）：本地有表情库时用 library，否则回退 keyword。
    """

    def __init__(self, owner: "ElainaSuitePlugin") -> None:
        super().__init__(owner)
        # 本次触发的会话标记：{key: ts}，on_llm_request 注入提示词，on_llm_response 消费
        self._triggers: dict[str, float] = {}
        # 本地表情库（懒加载）
        self._meme_dir: str = ""
        self._meme_list: list[str] = []
        self._meme_dir_ready = False

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

    @staticmethod
    def _http_get_json(url: str, timeout: int = 20) -> object:
        """GET 请求并解析 JSON，失败抛异常。"""
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://api.aa1.cn/",
                "Accept": "application/json, */*",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        return json.loads(data.decode("utf-8", errors="replace"))

    @staticmethod
    def _http_get_bytes(url: str, timeout: int = 20) -> bytes:
        """GET 请求获取原始字节（图片下载）。"""
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://api.aa1.cn/",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    # ------------------------------------------------------------ 表情包 API
    async def _fetch_meme_images(self, keyword: str) -> list[str]:
        """按关键词搜索表情包，多源降级，返回图片 URL 列表。"""
        errors: list[str] = []
        for source in ("tangdouz", "apihz_sogou", "apihz_baidu"):
            try:
                urls = await self._fetch_from_source(source, keyword)
                if urls:
                    logger.info(f"[meme_responder] 数据源 {source} 返回 {len(urls)} 张表情包")
                    return urls
                errors.append(f"{source}: 空结果")
            except Exception as e:
                errors.append(f"{source}: {e}")
                logger.warning(f"[meme_responder] 数据源 {source} 失败: {e}")
        logger.warning(f"[meme_responder] 表情包搜索全部失败: {'; '.join(errors)}")
        return []

    async def _fetch_from_source(self, source: str, keyword: str) -> list[str]:
        kw = urllib.parse.quote(keyword)
        if source == "tangdouz":
            url = f"{self._cfg('api_url_tangdouz', DEFAULT_API_URL_TANGDOUZ)}?return=json&nr={kw}"
            data = await asyncio.to_thread(self._http_get_json, url)
            if not isinstance(data, list):
                return []
            out = []
            for item in data:
                if isinstance(item, dict):
                    src = item.get("thumbSrc") or item.get("thumb") or ""
                    if src:
                        out.append(str(src))
            return out
        if source == "apihz_sogou":
            url = self._cfg("api_url_apihz_sogou", DEFAULT_API_URL_APIHZ_SOGOU)
        else:
            url = self._cfg("api_url_apihz_baidu", DEFAULT_API_URL_APIHZ_BAIDU)
        params = {
            "id": str(self._cfg("apihz_id", "")),
            "key": str(self._cfg("apihz_key", "")),
            "words": keyword,
            "limit": str(self._cfg_int("meme_count", 10)),
            "page": "1",
        }
        qs = urllib.parse.urlencode(params)
        data = await asyncio.to_thread(self._http_get_json, f"{url}?{qs}")
        # apihz 返回 {"code":..., "data": [...]} 或 {"code":..., "list": [...]}
        if isinstance(data, dict):
            if data.get("code") not in (0, 200, 1, "0", "200", "1"):
                raise RuntimeError(f"apihz 返回错误: {data.get('msg') or data.get('code')}")
            arr = data.get("data") or data.get("list") or data.get("result") or []
        else:
            arr = data
        out = []
        if isinstance(arr, list):
            for item in arr:
                if isinstance(item, dict):
                    src = (
                        item.get("thumbSrc")
                        or item.get("thumb")
                        or item.get("url")
                        or item.get("image")
                        or ""
                    )
                    if src:
                        out.append(str(src))
        return out

    @staticmethod
    def _pick_meme_url(urls: list[str]) -> str | None:
        """随机取一张表情包图片 URL。"""
        if not urls:
            return None
        return random.choice(urls)

    async def _download_meme(self, url: str) -> bytes | None:
        """下载表情包图片并校验格式；失败/非法内容返回 None。"""
        try:
            data = await asyncio.to_thread(self._http_get_bytes, url)
        except Exception as e:
            logger.warning(f"[meme_responder] 表情包下载失败 {url}: {e}")
            return None
        if not _is_valid_image(data, self._cfg_int("max_meme_mb", 8) * 1024 * 1024):
            logger.warning(
                f"[meme_responder] 下载内容不是有效图片或超出大小限制（可能被防盗链或返回 HTML），跳过: {url}"
            )
            return None
        return data

    # ---------------------------------------------------------------- 事件
    # ------------------------------------------------------------ 本地表情库
    def _resolve_meme_dir(self) -> str:
        """确定本地表情包目录：配置 meme_dir > 插件目录/meme > AstrBot plugins 下任意 */meme。

        第三顺位让本插件可以自动复用已安装的 Elaina 表情包插件的素材目录，
        无需手动拷贝表情图。
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
                    f.lower().endswith((".gif", ".jpg", ".jpeg", ".png", ".webp"))
                    for f in os.listdir(path)
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
                f
                for f in os.listdir(path)
                if f.lower().endswith((".gif", ".jpg", ".jpeg", ".png", ".webp"))
            )
            logger.info(f"[elaina_meme] 已加载 {len(self._meme_list)} 个本地表情包")
        except Exception as e:
            logger.warning(f"[elaina_meme] 读取本地表情目录失败: {e}")
            self._meme_list = []
        return self._meme_list

    @property
    def source(self) -> str:
        """表情包来源模式：library / keyword / auto（默认）。"""
        value = str(self._cfg("meme_source", "auto") or "auto").strip().lower()
        return value if value in ("library", "keyword", "auto") else "auto"

    def use_library(self) -> bool:
        """当前是否走本地表情库分支。"""
        if self.source == "keyword":
            return False
        if self.source == "library":
            return True
        return bool(self._load_memes())

    async def _ask_ai_choose_meme(
        self, event: AstrMessageEvent, conversation: str
    ) -> str:
        """让 AI 从本地表情库中挑一个文件名。"""
        names = "\n".join(self._meme_list[:200])
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
    def _read_local_file(path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    async def _send_local_meme(self, event: AstrMessageEvent) -> bool:
        """从本地表情库选一张并发送（格式/大小校验 + 超时）。"""
        memes = self._load_memes()
        if not memes:
            logger.warning("[elaina_meme] 本地表情库为空，跳过")
            return False
        try:
            conversation = (event.message_str or "").strip()
        except Exception:
            conversation = ""
        chosen = await self._ask_ai_choose_meme(event, conversation)
        if chosen not in memes:
            if chosen:
                logger.info(f"[elaina_meme] AI 选择 `{chosen}` 不在库中，改用随机一张")
            chosen = random.choice(memes)
        path = os.path.join(self._resolve_meme_dir(), chosen)
        timeout = self._cfg_int("send_timeout", 30)
        max_bytes = self._cfg_int("max_meme_mb", 8) * 1024 * 1024
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

    async def on_message(self, event: AstrMessageEvent):
        """用户消息到达时：按概率决定本次是否触发表情包回复。"""
        if not self._cfg_bool("meme_enabled", True):
            return
        # 只对普通用户消息触发（忽略指令与空消息）
        text = (event.message_str or "").strip()
        if not text or text.startswith("/"):
            return
        prob = max(0.0, min(1.0, self._cfg_float("trigger_prob", DEFAULT_TRIGGER_PROB)))
        if random.random() > prob:
            return
        # 标记本次会话触发（on_llm_request 注入提示词）
        key = self._trigger_key(event)
        self._triggers[key] = time.time()
        logger.debug(f"[meme_responder] 本次触发表情包（prob={prob}）")

    def _trigger_key(self, event: AstrMessageEvent) -> str:
        return f"{event.unified_msg_origin}:::{event.get_sender_id()}"

    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 请求前（关键词模式）：若本次已触发，注入表情关键词提示词。

        本地表情库模式无需注入（在回复阶段直接让 AI 从库里选图）。
        """
        key = self._trigger_key(event)
        if key not in self._triggers:
            return
        if self.use_library():
            return
        # 注入提示词（临时内容块，不写入历史）
        template = self._cfg("meme_prompt", MEME_PROMPT_TEMPLATE)
        try:
            from astrbot.core.agent.message import TextPart

            part = TextPart(text=template).mark_as_temp()
        except Exception:
            part = {"type": "text", "text": template}
        req.extra_user_content_parts.append(part)

    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        """LLM 回复后：剥离关键词标记，解析关键词，抓取表情包并发送。"""
        key = self._trigger_key(event)
        if key not in self._triggers:
            return
        self._triggers.pop(key, None)  # 一次性消费
        text = self._llm_text(resp)
        if not text:
            return
        # 1) 从回复文本中剥离 {{MEME_KEYWORD}} 标记行（避免标记发给用户）
        clean_text = self._strip_meme_mark(text)
        if clean_text != text:
            self._set_llm_text(resp, clean_text)
            logger.debug("[meme_responder] 已从回复中剥离表情关键词标记")
        # 2) 本地表情库模式：让 AI 从库里挑一张直接发送（原 Elaina 表情包行为）
        if self.use_library():
            await self._send_local_meme(event)
            return
        # 3) 关键词模式：解析关键词并在线抓图发送
        keyword = self._parse_keyword(text)
        if not keyword:
            logger.debug("[meme_responder] AI 未给出表情关键词，跳过")
            return
        logger.info(f"[meme_responder] AI 表情关键词: {keyword}")
        urls = await self._fetch_meme_images(keyword)
        if not urls:
            logger.warning("[meme_responder] 未获取到表情包，跳过")
            return
        timeout = self._cfg_int("send_timeout", 30)
        random.shuffle(urls)
        sent = False
        # 最多尝试 3 张：下载校验 -> 发送（带超时，避免 QQ 官方媒体上传重试阻塞过久）
        for url in urls[:3]:
            data = await self._download_meme(url)
            if not data:
                continue
            try:
                from astrbot.api.message_components import Image as AImage

                img = AImage.fromBytes(data)
                chain = MessageChain(chain=[img])
                await asyncio.wait_for(
                    self.owner._gated_send(event.unified_msg_origin, chain),
                    timeout=timeout,
                )
                logger.info(f"[meme_responder] 已发送表情包（关键词: {keyword}）")
                sent = True
                break
            except Exception as e:
                logger.warning(
                    f"[meme_responder] 发送表情包失败（{url}），尝试下一张: {e}"
                )
        if not sent:
            logger.error(
                f"[meme_responder] 表情包发送失败（关键词: {keyword}），已跳过（不影响文字回复）"
            )

    @staticmethod
    def _strip_meme_mark(text: str) -> str:
        """从 LLM 回复中移除 {{MEME_KEYWORD}}...{{/MEME_KEYWORD}} 标记（含所在行）。"""
        if not text:
            return text
        # 标记独立成行（前面只有空白或行首）：连行删除
        cleaned = re.sub(
            r"^[ \t]*\{\{MEME_KEYWORD\}\}.*?\{\{/MEME_KEYWORD\}\}[ \t]*\n?",
            "",
            text,
            flags=re.M | re.S,
        )
        # 标记出现在行中：仅删标记本身
        cleaned = re.sub(
            r"\{\{MEME_KEYWORD\}\}.*?\{\{/MEME_KEYWORD\}\}",
            "",
            cleaned,
            flags=re.S,
        )
        cleaned = re.sub(r"[ \t]*\n[ \t]*", "\n", cleaned)  # 清理空行残留空白
        cleaned = cleaned.strip()
        # 兜底：行首 MEME: 前缀行
        cleaned = re.sub(r"^MEME:[^\n]*\n?", "", cleaned)
        return cleaned.strip()

    @staticmethod
    def _set_llm_text(resp, text: str) -> None:
        """就地修改 LLM 响应的文本内容（各版本字段名兼容）。"""
        if resp is None:
            return
        for attr in ("completion_text", "result", "text"):
            try:
                if hasattr(resp, attr):
                    setattr(resp, attr, text)
                    return
            except Exception:
                continue
        try:
            rc = getattr(resp, "result_chain", None)
            if rc is not None and hasattr(rc, "chain"):
                for comp in rc.chain:
                    if getattr(comp, "type", None) == "Plain" or type(comp).__name__ == "Plain":
                        try:
                            comp.text = text
                            return
                        except Exception:
                            continue
        except Exception:
            pass

    @staticmethod
    def _parse_keyword(text: str) -> str:
        """从 LLM 回复中提取表情关键词。

        只认两种显式格式，避免把正常回复误判为关键词：
        - {{MEME_KEYWORD}}关键词{{/MEME_KEYWORD}}（推荐）
        - 独立行 MEME:关键词（兜底）
        """
        if not text:
            return ""
        m = re.search(r"\{\{MEME_KEYWORD\}\}(.*?)\{\{/MEME_KEYWORD\}\}", text, re.S)
        if m:
            kw = m.group(1).strip()
            return "" if kw in ("无", "none", "None", "-") else kw
        # 兜底：独立行 "MEME:关键词"
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("MEME:"):
                kw = line[5:].strip()
                return "" if kw in ("无", "none") else kw
        return ""

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

    async def terminate(self) -> None:
        """插件卸载时清理触发状态。"""
        self._triggers.clear()


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
                ok = 0
                for umo in targets:
                    try:
                        await asyncio.wait_for(
                            self.owner._gated_send(umo, MessageChain().url_image(url)),
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
        """插件激活时：注册日报/健康报告定时任务 + 安装门禁/注册人格消息定时任务。"""
        await self.daily_digest.initialize()
        await self.proactive_guard.initialize()
        await self.system_health.initialize()

    async def terminate(self) -> None:
        """插件禁用/重载时：依次清理 6 个核心。"""
        await self.image_bridge.terminate()
        await self.meme.terminate()
        await self.daily_digest.terminate()
        await self.proactive_guard.terminate()
        await self.system_health.terminate()

    # ------------------------------------------------------------------ #
    # 消息事件路由（3 个观察者互不干扰）
    # ------------------------------------------------------------------ #
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def image_bridge_on_message(self, event: AstrMessageEvent):
        """图片问答桥接：识别图片/表情并挂起；纯图片消息拦截等待提问。"""
        await self.image_bridge.on_message(event)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def meme_on_message(self, event: AstrMessageEvent):
        """表情包回复：按概率标记本次会话触发。"""
        await self.meme.on_message(event)

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
    async def meme_on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """表情包回复（关键词模式）：若本次已触发，注入表情关键词提示词。"""
        await self.meme.on_llm_request(event, req)

    @filter.on_llm_request()
    async def llm_guard_on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 拦截兜底：挂兜底错误文案 + 记录请求快照。"""
        await self.llm_guard.on_llm_request(event, req)

    @filter.on_llm_response(priority=99999)
    async def meme_on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        """表情包回复：本地表情库（AI 选图）或关键词抓图，然后发送。"""
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

