import asyncio
import datetime
import importlib.util
import inspect
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "stubs"))

spec = importlib.util.spec_from_file_location(
    "astrbot_plugin_elaina_suite",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

from astrbot.api.star import Star
from astrbot.api.event import filter as _f

# 0) 模块级常量与工具
assert mod.USER_AGENT
assert mod.DIGEST_DEFAULT_PROMPT and "每日简报" in mod.DIGEST_DEFAULT_PROMPT
assert mod.GUARD_DEFAULT_PROMPT and "{persona}" in mod.GUARD_DEFAULT_PROMPT
assert mod.DEFAULT_PROMPT_TEMPLATE and "{image_content}" in mod.DEFAULT_PROMPT_TEMPLATE
assert mod.DEFAULT_MEME_PROBABILITY == 0.5
assert mod.MEME_EXTENSIONS == (".gif", ".jpg", ".jpeg", ".png", ".webp")
assert mod.GOV_SOURCES == ("gov:zuixin", "gov:yaowen", "ndrc:xwfb"), mod.GOV_SOURCES
assert "Referer" in mod.GOV_HEADERS
assert mod.DEFAULT_FEEDS["feeds_policy"] == ["gov:zuixin", "ndrc:xwfb", "gov:yaowen"]
# 划线净化：只剥标记、保留文字
assert mod._sanitize_for_image("~~删除线~~内容") == "删除线内容"
assert mod._sanitize_for_image("**粗体**与_斜体_") == "粗体与斜体"
assert mod._sanitize_for_image("") == ""
assert mod.GUARD_HARD_MARKERS and "content_filter" in mod.GUARD_HARD_MARKERS
assert mod._guard_as_bool("true", False) is True and mod._guard_as_bool("0", True) is False
assert mod._guard_as_int("60") == 60 and mod._guard_as_int("x", 5) == 5
assert mod._guard_as_markers("a,b\nc") == ("a", "b", "c")
assert mod._is_valid_image(b"\x89PNG\r\n\x1a\n" + b"x" * 20) is True
assert mod._is_valid_image(b"<html>err</html>" + b"x" * 20) is False
print("[0] 模块常量与工具 OK")

# 1) 唯一 Star 子类 = ElainaSuitePlugin
star_cls = [c for c in vars(mod).values() if inspect.isclass(c) and issubclass(c, Star) and c is not Star]
assert [c.__name__ for c in star_cls] == ["ElainaSuitePlugin"], star_cls
print("[1] 唯一 Star 类:", [c.__name__ for c in star_cls])

# 2) 六个核心类存在且不是 Star 子类
for name in ("ImageBridgeCore", "DailyDigestCore", "ProactiveGuardCore",
             "ElainaMemeCore", "LlmGuardCore", "SystemHealthCore"):
    cls = getattr(mod, name)
    assert not issubclass(cls, Star), name
print("[2] 6 个核心类存在且均为普通类")

# 3) handler 注册：数量与唯一性 + 新增钩子/指令
by_kind = {}
full_names = []
for e in _f._registry:
    by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
    full_names.append(f"{e['module']}_{e['name']}")
assert len(full_names) == len(set(full_names)), "handler 全名重复!"
print("[3] handlers:", by_kind, "| 全部唯一:", len(full_names))
cmd_names = [e["command"] for e in _f._registry if e["kind"] == "command"]
assert len(cmd_names) == len(set(cmd_names)), "指令名重复!"
for need in ("digest", "health", "llmguard", "llmguarddump", "picreset"):
    assert need in cmd_names, f"缺少指令 {need}"
assert by_kind.get("decorating_result", 0) >= 1, "缺少 on_decorating_result 钩子"
assert by_kind.get("llm_response", 0) >= 1 and by_kind.get("llm_request", 0) >= 2
print("    指令:", cmd_names)

# 4) 实例化 + 6 核心挂载
class FakeCtx:
    def __init__(self):
        self.sent = []
        self.cron_manager = None
        self.send_message = self._send

    async def _send(self, *a, **k):
        self.sent.append(a)
        return True


ctx = FakeCtx()
config = {
    "pg_enabled": True,
    "block_proactive": True,
    "strict_mode": False,
    "meme_enabled": True,
    "meme_probability": 0.0,
    "digest_timezone": "Asia/Shanghai",
    "pg_timezone": "Asia/Shanghai",
    "guard_enabled": True,
    "health_enabled": False,
}
pl = mod.ElainaSuitePlugin(context=ctx, config=config)
assert pl._bypass_cnt == 0
assert all(
    isinstance(getattr(pl, a), getattr(mod, b))
    for a, b in [
        ("image_bridge", "ImageBridgeCore"),
        ("daily_digest", "DailyDigestCore"),
        ("proactive_guard", "ProactiveGuardCore"),
        ("meme", "ElainaMemeCore"),
        ("llm_guard", "LlmGuardCore"),
        ("system_health", "SystemHealthCore"),
    ]
)
print("[4] ElainaSuitePlugin 实例化，6 核心挂载 OK")

async def main():
    await pl.initialize()
    assert ctx.send_message.__name__ == "wrapped", "门禁未安装"
    print("[5] initialize() 完成，门禁已安装")

    orig = pl.proactive_guard._orig_send_message

    r = await ctx.send_message("aiocqhttp:Friend:1", "foreign")
    assert r is False, "外部主动发送未被拦截!"
    print("[6] 外部插件主动发送被拦截 ✓")

    ok = await pl.daily_digest._gated_send("aiocqhttp:Friend:1", "digest-body")
    assert ok is True and pl._bypass_cnt == 0
    await pl.proactive_guard._send_silent("persona-msg", ["aiocqhttp:Friend:1"])
    ok2 = await pl._gated_send("aiocqhttp:Friend:1", "health-body")
    assert ok2 is True
    assert len(ctx.sent) >= 3, ctx.sent
    print("[7] 日报/人格消息/健康报告主动发送被门禁放行 ✓")

    class FakeEvent:
        unified_msg_origin = "aiocqhttp:Friend:1"
        message_str = "你好"
        message_obj = None

        def get_sender_id(self):
            return "u1"

        def stop_event(self):
            pass

        def should_call_llm(self, v):
            pass

        def plain_result(self, t):
            return t

        def get_result(self):
            return None

    ev = FakeEvent()
    await pl.image_bridge.on_message(ev)
    await pl.proactive_guard._on_user_message(ev)
    assert "aiocqhttp:Friend:1" in pl.proactive_guard._last_user_activity
    print("[8] 两条消息事件路由执行 OK（纯文字 no-op / 活跃记录）")

    pl.proactive_guard._last_user_activity["aiocqhttp:Friend:1"] = __import__("time").time()
    r2 = pl.proactive_guard._allow_send("aiocqhttp:Friend:1")
    assert r2 is True
    print("[9] 活跃窗口内回复放行 ✓")

    async def fake_build():
        return "mock-digest-body"

    pl.daily_digest._build_digest = fake_build
    res = [x async for x in pl.daily_digest.digest_command(ev)]
    assert res and isinstance(res[0], str) and "简报" in res[0], res
    res2 = [x async for x in pl.proactive_guard.today_plan_command(ev)]
    assert res2 and isinstance(res2[0], str)
    res3 = [x async for x in pl.image_bridge.picreset(ev)]
    assert res3 and "清除" in res3[0]
    res4 = [x async for x in pl.system_health.health_command(ev)]
    assert res4 and isinstance(res4[0], str)
    res5 = [x async for x in pl.llm_guard.llmguard_status(ev)]
    assert res5 and "拦截" in res5[0]
    print("[10] 指令路由（digest/今日计划/picreset/health/llmguard）OK")

    # 11) LLM 拦截兜底判定
    lg = pl.llm_guard
    assert lg.match_reason("The request was rejected because it was considered high risk")
    assert lg.match_reason("content_filter")
    assert lg.match_reason("今天天气不错") is None
    pl.config["guard_soft_scan"] = True
    assert lg.match_reason("blocked: high risk") is not None
    pl.config["guard_extra_markers"] = "blocked by policy"
    assert lg.match_reason("your request was blocked by policy") is not None
    print("[11] LlmGuardCore 拦截判定 OK")

    # 12) Elaina 表情包：本地表情库 + AI 选图（对齐原版 Elaina_meme_Bridge，无联网搜索）
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "你好.gif"), "wb") as f:
        f.write(b"GIF89a" + b"\x00" * 64)
    with open(os.path.join(tmp, "无语.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    with open(os.path.join(tmp, "not_an_image.txt"), "w", encoding="utf-8") as f:
        f.write("ignore me")
    pl.config["meme_dir"] = tmp
    pl.meme._meme_dir_ready = False
    pl.meme._meme_list = []
    assert pl.meme._resolve_meme_dir() == tmp, pl.meme._resolve_meme_dir()
    memes = pl.meme._load_memes()
    assert memes == ["你好.gif", "无语.png"], memes  # 只收图片后缀且排序

    # 概率：原版键名 meme_probability 优先，旧 trigger_prob 兜底，越界钳制
    pl.config["meme_probability"] = 0.25
    assert pl.meme.probability() == 0.25
    pl.config.pop("meme_probability")
    pl.config["trigger_prob"] = 0.75
    assert pl.meme.probability() == 0.75, "旧 trigger_prob 未生效"
    pl.config.pop("trigger_prob")
    pl.config["meme_probability"] = 5
    assert pl.meme.probability() == 1.0
    assert pl.meme._cfg_bool("meme_enabled", True) is True

    # AI 回答 -> 表情库文件名对齐（全等 / 含说明文字 / 完全不命中）
    assert pl.meme._match_meme_name("你好.gif", memes) == "你好.gif"
    assert pl.meme._match_meme_name("我选 `无语.png`", memes) == "无语.png"
    assert pl.meme._match_meme_name("随便发一个吧", memes) == ""

    # on_llm_response 端到端：AI 选图命中 -> 经门禁放行通道发出
    async def _meme_provider(umo=None):
        return "p1"

    async def _meme_generate(**kw):
        assert "表情包列表" in kw["prompt"] and "你好.gif" in kw["prompt"], kw["prompt"]

        class R:
            completion_text = "无语.png"

        return R()

    ctx.get_current_chat_provider_id = _meme_provider
    ctx.llm_generate = _meme_generate

    class MemeResp:
        completion_text = "今天天气不错"

    def _image_sends():
        """已发出的「本地图片链」条数。

        日报/人格消息有后台定时任务会往 ctx.sent 里塞东西（本测试是 >= 而非 == 计数），
        所以这里只数携 base64 图片的链，避免与后台发送互相干扰。
        """
        out = []
        for args in ctx.sent:
            if len(args) < 2 or not hasattr(args[1], "chain"):
                continue
            for comp in args[1].chain:
                if str(getattr(comp, "file", "")).startswith("base64://"):
                    out.append(args)
                    break
        return out

    n0 = len(_image_sends())
    pl.config["meme_probability"] = 1.0
    await pl.meme.on_llm_response(ev, MemeResp())
    sent = _image_sends()
    assert len(sent) == n0 + 1, ctx.sent
    assert sent[-1][0] == "aiocqhttp:Friend:1"
    img = sent[-1][1].chain[0]
    assert getattr(img, "file", "").startswith("base64://"), img

    # 总开关关闭 -> 一张都不发
    pl.config["meme_enabled"] = False
    await pl.meme.on_llm_response(ev, MemeResp())
    assert len(_image_sends()) == n0 + 1, "总开关关闭后仍发送了表情包"

    # 概率为 0 -> 一张都不发
    pl.config["meme_enabled"] = True
    pl.config["meme_probability"] = 0.0
    await pl.meme.on_llm_response(ev, MemeResp())
    assert len(_image_sends()) == n0 + 1, "概率 0 仍发送了表情包"
    pl.config["meme_probability"] = 1.0
    print("[12] ElainaMemeCore 本地表情库 + AI 选图 OK:", memes)

    # 13) 系统健康报告
    if mod._psutil is not None:
        txt = pl.system_health._build_report_text()
        assert "系统健康报告" in txt and "内存" in txt
        print("[13] SystemHealthCore 报告生成 OK")
    else:
        assert pl.system_health.enabled is False
        print("[13] 未安装 psutil -> 健康核心自动禁用 OK")

    # 14) LLM 拦截兜底：把用户提示词重新注入 LLM 再生成
    REJECT = "The request was rejected because it was considered high risk"

    class GuardCtx:
        def __init__(self, script):
            self.script = list(script)
            self.calls = []

        async def get_current_chat_provider_id(self, umo=None):
            return "cur-provider"

        async def llm_generate(self, **kw):
            self.calls.append(kw)
            item = self.script.pop(0) if self.script else ""

            class R:
                completion_text = item

            return R()

    # 14a) 首次注入：用当前会话模型 + 携带人设
    gctx = GuardCtx(["春风又绿江南岸"])
    gpl = mod.ElainaSuitePlugin(context=gctx, config={"guard_enabled": True})
    g = gpl.llm_guard
    g._last_request = {"user_message": "帮我写首诗", "system_prompt": "你是艾拉娜", "prompt": "x"}
    out = await g._re_inject_prompt(ev)
    assert out == "春风又绿江南岸", out
    assert gctx.calls[0]["chat_provider_id"] == "cur-provider"
    assert gctx.calls[0]["prompt"] == "帮我写首诗"
    assert gctx.calls[0].get("system_prompt") == "你是艾拉娜"

    # 14b) 第一次被拦 -> 第二次仅用户提示词
    gctx2 = GuardCtx([REJECT, "好的，这是为你写的诗"])
    gpl2 = mod.ElainaSuitePlugin(context=gctx2, config={"guard_enabled": True})
    g2 = gpl2.llm_guard
    g2._last_request = {"user_message": "帮我写首诗", "system_prompt": "你是艾拉娜", "prompt": "x"}
    out2 = await g2._re_inject_prompt(ev)
    assert out2 == "好的，这是为你写的诗", out2
    assert len(gctx2.calls) == 2
    assert "system_prompt" in gctx2.calls[0] and "system_prompt" not in gctx2.calls[1]

    # 14c) 全部被拦 -> 空串（交给兜底文案）
    gctx3 = GuardCtx([REJECT, REJECT])
    gpl3 = mod.ElainaSuitePlugin(context=gctx3, config={"guard_enabled": True})
    g3 = gpl3.llm_guard
    g3._last_request = {"user_message": "帮我写首诗", "prompt": "x"}
    assert await g3._re_inject_prompt(ev) == ""

    # 14d) 端到端：on_decorating_result 把拦截文案换成重注入结果
    class GResult:
        def __init__(self, text):
            self.chain = [mod.Plain(text)]

    class GEvent:
        unified_msg_origin = "aiocqhttp:Friend:1"
        message_str = "帮我写首诗"

        def __init__(self, text):
            self._r = GResult(text)

        def get_result(self):
            return self._r

        def clear_result(self):
            self._r = None

    gctx4 = GuardCtx(["这是重新生成的正常回复"])
    gpl4 = mod.ElainaSuitePlugin(context=gctx4, config={"guard_enabled": True, "guard_retry_attempts": 1})
    g4 = gpl4.llm_guard
    g4._last_request = {"user_message": "帮我写首诗", "prompt": "x"}
    gev = GEvent(REJECT)
    await g4.on_decorating_result(gev)
    final = "".join(getattr(c, "text", "") for c in gev.get_result().chain)
    assert final == "这是重新生成的正常回复", final
    assert g4._blocked_total == 1 and g4._retried_ok == 1
    print("[14] LlmGuardCore 重新注入提示词 OK（含端到端替换）")

    # 15) 小米 MiMo 模型名自动降级（V2.6 默认 + 模型不可用时换候选）
    import types as _types

    script = []

    class _Resp:
        def __init__(self, status, text="", data=None):
            self.status_code = status
            self.text = text
            self._d = data or {}

        def json(self):
            return self._d

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return script.pop(0)

    orig_httpx = mod.httpx
    mod.httpx = _types.SimpleNamespace(
        Timeout=lambda *a, **k: None, AsyncClient=lambda *a, **k: _Client()
    )
    try:
        assert mod.DEFAULT_XIAOMI_MODEL == "mimo-v2.6-flash", mod.DEFAULT_XIAOMI_MODEL
        ib = pl.image_bridge
        pl.config["xiaomi_api_key"] = "tp-test"
        pl.config["xiaomi_model"] = "mimo-v2.6-flash"
        # 15a) 第一个模型报「模型不可用」-> 自动换下一个候选并成功
        script[:] = [
            _Resp(400, '{"error":{"message":"model not found: mimo-v2.6-flash"}}'),
            _Resp(200, "", {"choices": [{"message": {"content": "图中是一杯咖啡"}}]}),
        ]
        out = await ib._recognize_xiaomi(b"\x89PNG\r\n\x1a\n" + b"x" * 20, "png", "image/png")
        assert out == "图中是一杯咖啡", out
        # 15b) 非模型类错误（401）应立即抛出，不做无意义重试
        script[:] = [_Resp(401, '{"error":"unauthorized"}')]
        try:
            await ib._recognize_xiaomi(b"\x89PNG\r\n\x1a\n" + b"x" * 20, "png", "image/png")
            raise AssertionError("401 应抛异常")
        except RuntimeError as e:
            assert "401" in str(e), e
        # 15c) 已下线的 MiMo 2.5 模型名自动迁到 2.6（2026-10-21 下线）
        assert mod._normalize_xiaomi_model("mimo-v2.5") == "mimo-v2.6-flash"
        assert mod._normalize_xiaomi_model(" mimo-v2.5-pro ") == "mimo-v2.6-pro"
        assert mod._normalize_xiaomi_model("mimo-v2.6-flash") == "mimo-v2.6-flash"
        assert "mimo-v2.5" not in mod.XIAOMI_MODEL_FALLBACKS, mod.XIAOMI_MODEL_FALLBACKS
        # 旧配置里写 2.5 时，实际请求应换成 2.6
        pl.config["xiaomi_model"] = "mimo-v2.5"
        seen = []
        script[:] = [
            _Resp(200, "", {"choices": [{"message": {"content": "图里是一只猫"}}]}),
        ]
        _orig_post = _Client.post

        async def _capture(self, url, json=None, headers=None):
            seen.append(json.get("model"))
            return script.pop(0)

        _Client.post = _capture
        try:
            out = await ib._recognize_xiaomi(b"\x89PNG\r\n\x1a\n" + b"x" * 20, "png", "image/png")
        finally:
            _Client.post = _orig_post
        assert out == "图里是一只猫", out
        assert seen and seen[0] == "mimo-v2.6-flash", seen
        pl.config["xiaomi_model"] = "mimo-v2.6-flash"
    finally:
        mod.httpx = orig_httpx
    print("[15] MiMo 2.6 默认 + 模型名自动降级 + 2.5 旧名迁移 OK")

    # 17) 日报：政府官网政策源 + 图片/文件「两份都给」发送链路
    dd = pl.daily_digest
    # 17a) token 识别与默认源
    assert dd._is_json_source("gov:zuixin") is True
    assert dd._is_json_source("gov:yaowen") is True
    assert dd._is_json_source("ndrc:xwfb") is True
    assert dd._is_json_source("cctv:news") is True
    assert dd._is_json_source("https://example.com/feed.xml") is False
    assert dd._feed_urls("feeds_policy") == ["gov:zuixin", "ndrc:xwfb", "gov:yaowen"]
    # 17b) _fetch_gov_source 解析（走 httpx/urllib 桩，覆盖三种 JSON/HTML 结构）
    import urllib.request as _ureq

    class _FakeResp:
        def __init__(self, data):
            self._d = data

        def read(self):
            return self._d

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    orig_urlopen = _ureq.urlopen
    fed = {}

    def _fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        fed[url] = req.headers
        if "ZUIXINZHENGCE.json" in url:
            body = json.dumps(
                [{"TITLE": "中共中央 国务院关于发展新质生产力的意见",
                  "SUB_TITLE": "附件",
                  "URL": "https://www.gov.cn/zhengce/202610/content_7082820.htm",
                  "DOCRELPUBTIME": "2099-01-01"}],
                ensure_ascii=False,
            )
            return _FakeResp(body.encode("utf-8"))
        if "pushinfo.json" in url:
            body = json.dumps(
                [{"title": "国务院召开常务会议",
                  "link": "https://www.gov.cn/yaowen/liebiao/x.htm",
                  "pubDate": "2099-01-01", "description": "摘要"}],
                ensure_ascii=False,
            )
            return _FakeResp(body.encode("utf-8"))
        html = (
            '<ul><li><a href="./202610/t20261015_1.html" target="_blank" '
            'title="成品油价格不作调整">x</a><span>2099/01/01</span></li></ul>'
        )
        return _FakeResp(html.encode("utf-8"))

    _ureq.urlopen = _fake_urlopen
    try:
        zx = await dd._fetch_gov_source("gov:zuixin", 5)
        assert zx and zx[0]["link"].endswith("content_7082820.htm"), zx
        assert zx[0]["source"] == "中国政府网", zx
        assert "gov.cn" in zx[0]["link"]
        yw = await dd._fetch_gov_source("gov:yaowen", 5)
        assert yw and yw[0]["title"], yw
        nd = await dd._fetch_gov_source("ndrc:xwfb", 5)
        assert nd and nd[0]["link"].startswith("https://www.ndrc.gov.cn/"), nd
        # 请求带上了 gov Referer
        assert any("Referer" in str(v) for v in fed.values()), fed.keys()
    finally:
        _ureq.urlopen = orig_urlopen
    # 17c) 政策板块放宽时效窗口；普通板块仍严格
    from datetime import datetime as _dt, timedelta as _td
    now = _dt.now().astimezone()
    old_item = {"title": "20天前的政策", "link": "x", "pub_date": now - _td(days=20), "summary": ""}
    new_item = {"title": "今天的新闻", "link": "y", "pub_date": now, "summary": ""}
    assert dd._filter_yesterday([old_item], 30) == [old_item]
    assert dd._filter_yesterday([old_item], 0) == []
    assert dd._filter_yesterday([new_item], 0) == [new_item]
    # 17d) 发送模式分流：auto 长文本 -> 同时发图片与 md
    calls = {"image": 0, "md": 0, "text": 0}

    async def _fake_image(text, targets):
        calls["image"] += 1
        return True

    async def _fake_md(text, targets):
        calls["md"] += 1
        return True

    async def _fake_text(umo, text):
        calls["text"] += 1

    dd._send_image = _fake_image
    dd._send_md = _fake_md
    dd._send_text = _fake_text
    pl.config["digest_send_mode"] = "auto"
    pl.config["digest_long_threshold"] = 10
    pl.config["digest_also_send_md"] = True
    await dd._send_digest("x" * 100, ["aiocqhttp:Friend:1"])
    assert calls == {"image": 1, "md": 1, "text": 0}, calls
    # text 模式：都发文本
    calls.update(image=0, md=0, text=0)
    pl.config["digest_send_mode"] = "text"
    await dd._send_digest("x" * 100, ["aiocqhttp:Friend:1"])
    assert calls == {"image": 0, "md": 0, "text": 1}, calls
    # both 模式：图片与 md 都发
    calls.update(image=0, md=0, text=0)
    pl.config["digest_send_mode"] = "both"
    await dd._send_digest("x", ["aiocqhttp:Friend:1"])
    assert calls == {"image": 1, "md": 1, "text": 0}, calls
    # 两者都失败 -> 回退纯文本
    calls.update(image=0, md=0, text=0)

    async def _fail_image(text, targets):
        calls["image"] += 1
        return False

    async def _fail_md(text, targets):
        calls["md"] += 1
        return False

    dd._send_image = _fail_image
    dd._send_md = _fail_md
    await dd._send_digest("x", ["aiocqhttp:Friend:1"])
    assert calls == {"image": 1, "md": 1, "text": 1}, calls
    print("[17] 日报政府官网政策源 + 图片/md 两份都给 OK")

    # 18) 记忆源归一（阶段二）：全插件只有 Mem0 一个记忆源
    pg = pl.proactive_guard
    _kv = {}

    async def _kv_get(key, default=None):
        return _kv.get(key, default)

    async def _kv_put(key, value):
        _kv[key] = value

    pg.get_kv_data = _kv_get
    pg.put_kv_data = _kv_put

    class _FakeMemEngine:
        """冒充 LivingMemoryCore：只暴露归一后允许存在的接口。"""

        def __init__(self):
            self.enabled = True
            self.items = [
                {"memory": "用户叫小鱼，正在做 AstrBot 插件", "metadata": {"kind": "chat"}},
                {"memory": "魔法协会总部在旧都", "metadata": {"kind": "worldview"},
                 "created_at": "2026-10-01 03:00:00"},
            ]
            self.wm = "2026-10-20 03:00"
            self.flushed = 0
            self.resets = 0
            self.seeds = 0

        def scope_id(self):
            return "u_owner"

        async def memory_text(self, chars=0):
            return "\n".join("- " + i["memory"] for i in self.items)

        async def list_memories(self, top_k=200):
            return list(self.items)

        async def flush_now(self):
            self.flushed += 1
            return 3

        async def reset_scope(self):
            self.resets += 1
            n = len(self.items)
            self.items = []
            return n

        async def seed_worldview(self):
            self.seeds += 1
            return 34

        def watermark(self):
            return self.wm

        async def close(self):
            return None

    _mem = _FakeMemEngine()
    pl.living_memory = _mem

    # 18a) 旧 KV 记忆链路的配置键/常量必须已删除，只留 mem0_* 一套
    _schema_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_conf_schema.json"
    )
    _schema = json.load(open(_schema_path, encoding="utf-8"))
    for _k in ("memory_enabled", "memory_cron", "memory_turns", "memory_max_chars",
               "memory_raw_fallback", "memory_notes_enabled", "memory_refresh_cool",
               "memory_refresh_cooldown", "memory_refresh_before_gen"):
        assert _k not in _schema, f"旧记忆配置键仍在：{_k}"
    assert "mem0_migrate_legacy" in _schema, "缺少旧 scope 合并开关"
    for _n in ("MEMORY_KEY", "MEMORY_BLOCK_TEMPLATE", "MEMORY_PROMPT", "MEMORY_MERGE_PROMPT"):
        assert not hasattr(mod, _n), f"旧记忆常量仍在：{_n}"

    # 18b) 记忆文本与水位都只从 Mem0 取
    _mem_text = await pg._collect_memory()
    assert "小鱼" in _mem_text, _mem_text
    assert await pg._memory_updated() == "2026-10-20 03:00"
    _mem.wm = ""
    assert await pg._memory_updated() == ""

    class _Ev:
        def plain_result(self, text):
            return text

    # 18c) /记忆 直查 Mem0（世界观条目标 ◆）；/刷新记忆 刷缓冲；/记忆重置 清唯一 scope
    _got = [r async for r in pg.memory_command(_Ev())]
    assert "🧠 记忆（共 2 条" in _got[0] and "u_owner" in _got[0], _got
    assert "◆ 魔法协会总部在旧都" in _got[0], _got
    _got2 = [r async for r in pg.refresh_memory_command(_Ev())]
    assert "已写入 Mem0 3 条" in _got2[0] and _mem.flushed == 1, _got2
    _got3 = [r async for r in pg.reset_memory_command(_Ev())]
    assert "删除 2 条" in _got3[0] and _mem.resets == 1 and _mem.seeds == 1, _got3
    _mem.enabled = False
    _got4 = [r async for r in pg.memory_command(_Ev())]
    assert "没有初始化成功" in _got4[0], _got4
    _mem.enabled = True
    _mem.items = [
        {"memory": "用户叫小鱼，正在做 AstrBot 插件", "metadata": {"kind": "chat"}},
        {"memory": "魔法协会总部在旧都", "metadata": {"kind": "worldview"},
         "created_at": "2026-10-01 03:00:00"},
    ]

    # 18d) _generate_and_store：记忆进子代理 + 规划失败时真的回退单次调用
    pg._random_times = lambda n: ["07:35"]
    _seen = {}

    async def _subagents_fail(date, times, time_list, count, persona, memory_block, **kw):
        _seen["sub_block"] = memory_block
        return []          # 模拟规划代理失败

    pg._generate_with_subagents = _subagents_fail

    async def _gen_chat(prompt):
        _seen["gen_prompt"] = prompt
        return json.dumps([{"time": "07:35", "text": "早呀"}], ensure_ascii=False)

    pg._llm_chat = _gen_chat
    pl.config["subagent_enabled"] = True
    await pg._generate_and_store("2099-01-01")
    assert "小鱼" in _seen.get("sub_block", ""), "记忆没传给子代理"
    assert "小鱼" in _seen.get("gen_prompt", ""), "规划失败后回退的单次调用没带记忆"
    _plan = await pg.get_kv_data("daily_plan_next", None)
    assert _plan and _plan["date"] == "2099-01-01" and _plan["items"], _plan

    async def _subagents_ok(date, times, time_list, count, persona, memory_block, **kw):
        return [{"time": "07:35", "text": "子代理写的"}]

    pg._generate_with_subagents = _subagents_ok
    _seen.pop("gen_prompt", None)
    await pg._generate_and_store("2099-01-02")
    assert "gen_prompt" not in _seen, "子代理成功却又走了单次调用"
    print("[18] 记忆源归一（Mem0 唯一源 / 命令直查 / 子代理带记忆）OK")



    # [19] 本周真机缺陷回归：渲染结果分流 / 宽松解析 / 国际源
    # 19a) _image_chain：本地路径（AstrBot 网络渲染失败时会返回容器内文件路径）
    _tmpfile = os.path.join(tempfile.mkdtemp(), "io_temp_img_1.jpg")
    with open(_tmpfile, "wb") as f:
        f.write(b"\xff\xd8\xff\xd9")
    def _chain_type(ch):
        first = ch.chain[0]
        return first["type"] if isinstance(first, dict) else first.type

    _ch = mod._image_chain(_tmpfile)
    assert _chain_type(_ch) == "image", _ch
    _ch2 = mod._image_chain("https://t2i.example/x.jpeg")
    assert _chain_type(_ch2) == "image"
    _ch3 = mod._image_chain("file://" + _tmpfile)
    assert _chain_type(_ch3) == "image"
    _ch4 = mod._image_chain("base64://aGVsbG8=")
    assert _chain_type(_ch4) == "image"
    try:
        mod._image_chain("")
        raise AssertionError("空渲染结果应抛错")
    except ValueError:
        pass
    # 19b) 宽松解析：代码围栏 + 全角冒号 + 尾逗号 + 全角引号
    times = ["07:35", "12:10"]
    _fenced = (
        "```json\n[{\"time\":\"07：35\",\"text\":\"早呀\"},\n"
        " {\"time\":\"12:10\",\"text\":\"吃饭没\"},]\n```"
    )
    got = pg._parse_items(_fenced, times)
    assert [g["time"] for g in got] == ["07:35", "12:10"], got
    # 19c) 行解析：带序号 + markdown 强调 + 破折号
    got2 = pg._parse_items("1. **07:35** — 早呀\n2. `12:10` 吃饭没", times)
    assert [g["time"] for g in got2] == ["07:35", "12:10"], got2
    # 19d) _normalize_time 全角/点号/中文点
    assert pg._normalize_time("07：05") == "07:05"
    assert pg._normalize_time("7.5") == "07:05"
    assert pg._normalize_time("07点05") == "07:05"
    assert pg._normalize_time("25:99") == "25:99"
    # 19e) _parse_points 同款宽松
    pts = pg._parse_points(
        '```\n[{"time":"07：35","point":"打个招呼"},\n'
        ' {"time":"12:10","topic":"午饭"},]\n```',
        times,
    )
    assert pts == [("07:35", "打个招呼"), ("12:10", "午饭")], pts
    # 19f) 国际源不再有 404 的 chinadaily
    assert "chinadaily" not in json.dumps(mod.DEFAULT_FEEDS), mod.DEFAULT_FEEDS
    assert any("chinanews" in u for u in mod.DEFAULT_FEEDS["feeds_intl"])
    print("[19] 渲染结果分流 + 宽松解析 + 国际源修复 OK")

    # [20] 日报重复发送防护（2026-10-17 真机投诉回归）
    # 20d) 日报：08:00 定时任务与 5 分钟兜底检查并发触发，只能发一次
    _dd_kv = {}

    async def _dd_get(k, d=None):
        return _dd_kv.get(k, d)

    async def _dd_put(k, v):
        _dd_kv[k] = v

    dd.get_kv_data = _dd_get
    dd.put_kv_data = _dd_put
    dd._now_local = lambda: datetime.datetime(2026, 10, 17, 9, 0)
    async def _two_sessions():
        return ["umo1", "umo2"]

    dd._collect_target_sessions = _two_sessions
    _runs = []

    async def _slow_send(targets):
        _runs.append(list(targets))
        await asyncio.sleep(0.05)

    dd._generate_and_send = _slow_send
    _dd_kv.pop("last_digest_date", None)
    await asyncio.gather(dd._maybe_send_digest(), dd._maybe_send_digest())
    assert len(_runs) == 1, _runs
    assert _dd_kv.get("last_digest_date") == "2026-10-17", _dd_kv

    # 20e) 生成失败要回滚占位，否则当天再也不补发
    async def _boom(targets):
        raise RuntimeError("send failed")

    dd._generate_and_send = _boom
    _dd_kv.pop("last_digest_date", None)
    await dd._maybe_send_digest()
    assert not _dd_kv.get("last_digest_date"), _dd_kv
    dd._generate_and_send = _slow_send
    _runs.clear()
    await dd._maybe_send_digest()
    assert len(_runs) == 1, "失败后应能重新补发"
    print("[20] 日报防重复发送（占位/回滚/补发）OK")

    # [21] 旧记忆链路确认已物理移除（阶段二收尾回归）
    for _dead in ("_history_lines", "_collect_user_notes", "_memory_refresh_due",
                  "_load_memory_store", "_memory_text", "_truncate_memory",
                  "_filter_memory_lines", "_collect_raw_history", "_workspaces_dir",
                  "refresh_memory", "_on_memory_time"):
        assert not hasattr(mod.ProactiveGuardCore, _dead), f"旧记忆方法仍在：{_dead}"
    # /记忆重置 不再走 KV，直接清 Mem0 唯一 scope
    _mem.items = [{"memory": "x", "metadata": {}}]
    _msgs = [r async for r in pg.reset_memory_command(_Ev())]
    assert _msgs and "删除 1 条" in _msgs[0], _msgs
    assert _mem.resets == 2 and _mem.items == [], (_mem.resets, _mem.items)
    print("[21] 旧 KV 记忆链路（历史行/用户笔记/冷却刷新/KV 重置）已物理移除 OK")


    # [22] 计划与记忆的时间错位（2026-10-20）
    # 症状：记忆 03:00 已写「卡已领」，今天的计划是昨天用旧记忆预生成的，
    # 于是白天还在提醒去领卡。三道防线：水位比对 / 发送前事实校验 / 重建今日残留。
    _today = datetime.datetime.now().strftime("%Y-%m-%d")
    _now_hhmm = datetime.datetime.now().strftime("%H:%M")
    _schema = json.load(open(_schema_path, encoding="utf-8"))
    for _k in ("dispatch_fact_check", "dispatch_regenerate_on_memory_change"):
        assert _k in _schema, f"配置缺键 {_k}"
    assert _schema["gen_time"]["default"] == "20 3 * * *", _schema["gen_time"]
    assert "{message}" in mod.FACT_CHECK_PROMPT and "{memory}" in mod.FACT_CHECK_PROMPT

    _saved = {}
    for _n in ("_cfg_bool", "_cfg_int", "_llm_chat", "_collect_memory",
               "_collect_target_sessions", "_send_silent", "_generate_with_subagents",
               "_ensure_schedule", "_regenerate_today_if_stale"):
        _saved[_n] = getattr(pg, _n)
    _flags = {
        "pg_enabled": True,
        "memory_enabled": True,
        "dispatch_fact_check": True,
        "dispatch_regenerate_on_memory_change": True,
        "subagent_enabled": False,
    }
    pg._cfg_bool = lambda k, d=True: _flags.get(k, d)
    pg._cfg_int = lambda k, d=0: d

    _sent = []

    async def _send_stub(text, targets):
        _sent.append(text)

    pg._send_silent = _send_stub

    async def _targets1():
        return ["umo1"]

    pg._collect_target_sessions = _targets1

    async def _mem_empty():
        return ""

    async def _mem_card():
        return "用户笔记：万事达卡已于 2026-10-17 在城里领回。"

    # 22a) 生成时把「基于哪一版记忆」写进计划（水位）
    _mem.wm = "2026-10-20 03:00"
    pg._collect_memory = _mem_empty

    async def _gen_one(prompt):
        return '[{"time":"08:00","text":"早上好"}]'

    pg._llm_chat = _gen_one
    await pg._generate_and_store(_today, times=["08:00"])
    assert _kv["daily_plan"]["memory_updated"] == "2026-10-20 03:00", _kv["daily_plan"]
    assert [i["time"] for i in _kv["daily_plan"]["items"]] == ["08:00"]

    # 22b) 记忆比计划新 => 发送前拿最新记忆校验，冲突则改写
    _mem.wm = "2026-10-20 12:00"
    _kv["daily_plan"] = {
        "date": _today,
        "memory_updated": "2026-10-20 03:00",
        "items": [{"time": _now_hhmm, "text": "记得去领万事达卡"}],
    }
    pg._collect_memory = _mem_card
    _seen = []

    async def _check_rewrite(prompt):
        _seen.append(prompt)
        return '{"conflict": true, "reason": "卡已领", "rewritten": "训练辛苦啦，记得多喝水"}'

    pg._llm_chat = _check_rewrite
    _sent.clear()
    await pg._dispatch_due()
    assert _sent == ["训练辛苦啦，记得多喝水"], _sent
    assert _kv["daily_plan"]["items"] == [], _kv["daily_plan"]
    assert _kv["daily_plan"]["memory_updated"] == "2026-10-20 12:00"
    assert "待发送的消息" in _seen[0] and "万事达卡" in _seen[0], _seen[0][:200]

    # 22c) 冲突且无法改写 => 丢弃，不发送
    _kv["daily_plan"] = {
        "date": _today,
        "memory_updated": "2026-10-20 03:00",
        "items": [{"time": _now_hhmm, "text": "卡领了吗"}],
    }

    async def _check_drop(prompt):
        return '{"conflict": true, "reason": "已领", "rewritten": ""}'

    pg._llm_chat = _check_drop
    _sent.clear()
    await pg._dispatch_due()
    assert _sent == [], _sent
    assert _kv["daily_plan"]["items"] == [], _kv["daily_plan"]

    # 22d) 不冲突 => 原文发送；LLM 挂了 => 也原文发送（不能因为校验失败整天不说话）
    _kv["daily_plan"] = {
        "date": _today,
        "memory_updated": "2026-10-20 03:00",
        "items": [{"time": _now_hhmm, "text": "军训累不累"}],
    }

    async def _check_ok(prompt):
        return '{"conflict": false, "reason": "", "rewritten": ""}'

    pg._llm_chat = _check_ok
    _sent.clear()
    await pg._dispatch_due()
    assert _sent == ["军训累不累"], _sent

    _kv["daily_plan"] = {
        "date": _today,
        "memory_updated": "2026-10-20 03:00",
        "items": [{"time": _now_hhmm, "text": "军训累不累"}],
    }

    async def _check_boom(prompt):
        raise RuntimeError("LLM 挂了")

    pg._llm_chat = _check_boom
    _sent.clear()
    await pg._dispatch_due()
    assert _sent == ["军训累不累"], _sent

    # 22e) 今日计划基于旧记忆 => 重建「还没到点」的条目，时间点保持不变
    _mem.wm = "2026-10-20 12:00"
    _kv["daily_plan"] = {
        "date": _today,
        "memory_updated": "2026-10-20 03:00",
        "items": [{"time": "23:59", "text": "旧内容：提醒去领卡"}],
    }

    async def _gen_new(prompt):
        return '[{"time":"23:59","text":"新内容：问问军训"}]'

    pg._llm_chat = _gen_new
    pg._collect_memory = _mem_empty
    await pg._regenerate_today_if_stale()
    assert _kv["daily_plan"]["items"][0]["text"].startswith("新内容"), _kv["daily_plan"]
    assert _kv["daily_plan"]["items"][0]["time"] == "23:59", _kv["daily_plan"]
    assert _kv["daily_plan"]["memory_updated"] == "2026-10-20 12:00"

    # 22f) 生成时点顺序：先确保槽位 -> 最后重建今日残留（不再有「生成前刷记忆」）
    _order = []

    async def _ens(force=False):
        _order.append("ensure")

    async def _reg():
        _order.append("regen")

    pg._ensure_schedule = _ens
    pg._regenerate_today_if_stale = _reg
    await pg._on_gen_time()
    assert _order == ["ensure", "regen"], _order

    for _n, _v in _saved.items():
        setattr(pg, _n, _v)
    print("[22] 计划/记忆时间错位（水位/发送前校验/重建今日残留）OK")

    # 23) 人格/世界观驱动的自我分享（v1.1.6）
    assert pg._share_counts(10, 70) == (7, 3), pg._share_counts(10, 70)
    assert pg._share_counts(10, 100) == (9, 1), "关心类必须至少保留 1 条"
    assert pg._share_counts(1, 100) == (1, 0)
    assert pg._share_counts(5, 0) == (0, 5)

    pl.config["world_setting"] = ""
    assert pg._world_block() == "", "没填世界观时不应产生空标题块"
    pl.config["world_setting"] = "你是旅行中的魔女，正坐火车去下一个国家。"
    _wb = pg._world_block()
    assert "坐火车" in _wb and "【世界观设定】" in _wb, _wb

    for _p in (
        mod.PLANNER_PROMPT,
        mod.WRITER_PROMPT,
        mod.REVIEWER_PROMPT,
        mod.GUARD_DEFAULT_PROMPT,
    ):
        assert "{world_block}" in _p, "提示词缺少世界观占位"

    _wr = mod.WRITER_PROMPT.format(
        persona="你是一个魔女。",
        world_block=_wb,
        date="2099-01-03",
        time="09:00",
        point="分享路上看到的风景",
        memory_block="",
    )
    assert "坐火车" in _wr and "分享路上看到的风景" in _wr, _wr
    assert "你的分享必须落在这个框架内" not in mod.WRITER_PROMPT.format(
        persona="你是一个魔女。",
        world_block="",
        date="2099-01-03",
        time="09:00",
        point="随手发一条",
        memory_block="",
    ), "世界观留空时不应拼出空标题块"

    _pl = mod.PLANNER_PROMPT.format(
        persona="你是一个魔女。",
        world_block=_wb,
        date="2099-01-03",
        count=6,
        time_list="07:35、12:00",
        memory_block="",
        share_count=4,
        care_count=2,
    )
    assert "4" in _pl and "2" in _pl and "【世界观设定】" in _pl, _pl
    print("[23] 人格/世界观驱动的自我分享（分享比例/世界观块/提示词）OK")

    # 24) 新增板块：AI 界的新变化 + 网安漏洞与情报（v1.1.7）
    _sec_by_feed = {x[1]: x for x in mod.SECTIONS}
    assert "feeds_ai" in _sec_by_feed and "feeds_security" in _sec_by_feed
    assert _sec_by_feed["feeds_ai"][2] == "AI 界的新变化", _sec_by_feed["feeds_ai"]
    assert _sec_by_feed["feeds_security"][2] == "网安漏洞与情报"
    assert _sec_by_feed["feeds_ai"][3] == "🤖"
    assert _sec_by_feed["feeds_security"][3] == "🛡️"
    _feed_order = [x[1] for x in mod.SECTIONS]
    assert (
        _feed_order.index("feeds_tech")
        < _feed_order.index("feeds_ai")
        < _feed_order.index("feeds_security")
        < _feed_order.index("feeds_medical")
    ), _feed_order
    assert mod.DEFAULT_FEEDS["feeds_ai"] == [
        "https://www.qbitai.com/feed",
        "https://www.infoq.cn/feed",
    ], mod.DEFAULT_FEEDS["feeds_ai"]
    assert "cisa:kev" in mod.DEFAULT_FEEDS["feeds_security"]
    assert "freebuf.com" in " ".join(mod.DEFAULT_FEEDS["feeds_security"])
    assert dd._is_json_source("cisa:kev") is True
    assert "cisa:kev" in mod.KEV_SOURCES

    # 24a) cisa:kev 解析：7 天窗口 / 勒索标记 / nvd 链接 / 6 小时缓存 / 失败隔离
    _now = datetime.datetime.now().astimezone()
    _d = lambda n: (_now - datetime.timedelta(days=n)).strftime("%Y-%m-%d")

    def _kev_payload():
        return json.dumps({
            "title": "CISA KEV",
            "vulnerabilities": [
                {"cveID": "CVE-2099-0001", "vendorProject": "Acme", "product": "Router",
                 "vulnerabilityName": "命令注入", "dateAdded": _d(1),
                 "shortDescription": "Acme 路由器存在命令注入，可远程执行任意命令。",
                 "knownRansomwareCampaignUse": "Known"},
                {"cveID": "CVE-2099-0002", "vendorProject": "Beta", "product": "Panel",
                 "vulnerabilityName": "未授权访问", "dateAdded": _d(5),
                 "shortDescription": "Beta 面板缺少鉴权。",
                 "knownRansomwareCampaignUse": "Unknown"},
                {"cveID": "CVE-2099-0003", "vendorProject": "Gamma", "product": "Old",
                 "vulnerabilityName": "越界写", "dateAdded": _d(30),
                 "shortDescription": "太老了，应被窗口过滤掉。",
                 "knownRansomwareCampaignUse": "Unknown"},
            ],
        }).encode("utf-8")

    _orig_hgb = dd._http_get_bytes
    _calls = {"n": 0}

    def _fake_hgb(url, *a, **k):
        _calls["n"] += 1
        assert "known_exploited_vulnerabilities.json" in url, url
        return _kev_payload()

    pl.config["security_kev_days"] = 7
    dd._http_get_bytes = _fake_hgb
    dd._kev_cache = None
    _kev = await dd._fetch_cisa_kev(20)
    assert [it["title"].split("：")[0] for it in _kev] == ["CVE-2099-0001", "CVE-2099-0002"], _kev
    assert _kev[0]["title"] == "CVE-2099-0001：Acme Router —— 命令注入", _kev[0]["title"]
    assert "⚠️ 已用于勒索软件" in _kev[0]["summary"], _kev[0]["summary"]
    assert "⚠️" not in _kev[1]["summary"], _kev[1]["summary"]
    assert _kev[0]["url"] == "https://nvd.nist.gov/vuln/detail/CVE-2099-0001"
    assert _kev[0]["link"] == _kev[0]["url"]
    _kev2 = await dd._fetch_cisa_kev(20)
    assert len(_kev2) == 2 and _calls["n"] == 1, (_calls, len(_kev2))
    pl.config["security_kev_days"] = 2
    _kev3 = await dd._fetch_cisa_kev(20)
    assert len(_kev3) == 1 and _kev3[0]["title"].startswith("CVE-2099-0001"), _kev3
    assert _calls["n"] == 1, "窗口收窄不应重新下载"

    def _boom_hgb(url, *a, **k):
        raise RuntimeError("mock 网络挂了")

    dd._http_get_bytes = _boom_hgb
    dd._kev_cache = None
    assert await dd._fetch_cisa_kev(20) == [], "抓取失败必须返回空列表"
    dd._http_get_bytes = _orig_hgb
    pl.config["security_kev_days"] = 7
    print("[24a] cisa:kev 解析（7 天窗口/勒索标记/nvd/缓存/失败隔离）OK")

    # 24b) feeds_ai 走 ai_recent_days 窗口（默认 2 天，硬卡昨日条目太少）
    import urllib.request as _ureq2
    from email.utils import format_datetime as _fmt_dt

    def _rss(pairs):
        body = "".join(
            "<item><title>%s</title><link>https://qbitai.example/%s</link>"
            "<pubDate>%s</pubDate><description>d</description></item>"
            % (t, t, _fmt_dt(_when))
            for t, _when in pairs
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            "<title>qbitai</title>%s</channel></rss>" % body
        ).encode("utf-8")

    _orig_hgb2 = dd._http_get_bytes

    class _FakeResp2:
        def __init__(self, data):
            self._d = data

        def read(self):
            return self._d

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    _body = _rss([
        ("AI 新模型发布，推理成本再降", _now - datetime.timedelta(hours=6)),
        ("十天后才该出现的旧闻", _now - datetime.timedelta(days=10)),
    ])
    dd._http_get_bytes = lambda url, *a, **k: _body
    try:
        pl.config["ai_recent_days"] = 2
        _ai_items = await dd._fetch_category("feeds_ai", 5)
        _titles = [it["title"] for it in _ai_items]
        assert "AI 新模型发布，推理成本再降" in _titles, _titles
        assert "十天后才该出现的旧闻" not in _titles, _titles
        pl.config["ai_recent_days"] = 1
        _ai_items2 = await dd._fetch_category("feeds_ai", 5)
        # 两个默认源都会喂同一段 RSS，去重后应只剩「当天那条」
        assert {it["title"] for it in _ai_items2} == {"AI 新模型发布，推理成本再降"}, _ai_items2
    finally:
        dd._http_get_bytes = _orig_hgb2
    print("[24b] feeds_ai 时效窗口（ai_recent_days）OK")

    # 24c) 模板渲染出新板块标题
    _sec_items = [{"title": "示例条目", "link": "https://e.x/1", "url": "https://e.x/1",
                   "pub_date": _now, "summary": "摘要"}]
    _md2 = dd._template_digest(None, {"feeds_ai": _sec_items, "feeds_security": _sec_items})
    assert "🤖 AI 界的新变化" in _md2, _md2[:400]
    assert "🛡️ 网安漏洞与情报" in _md2, _md2[:400]
    print("[24c] 模板渲染新板块标题 OK")

    # 24d) 配置 schema 新键
    _schema_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_conf_schema.json"
    )
    _schema = json.load(open(_schema_path, encoding="utf-8"))
    for _k, _t, _dv in (
        ("ai_enabled", "bool", True),
        ("security_enabled", "bool", True),
        ("ai_recent_days", "int", 2),
        ("security_recent_days", "int", 2),
        ("security_kev_days", "int", 7),
    ):
        assert _k in _schema, "schema 缺少 " + _k
        assert _schema[_k]["type"] == _t, (_k, _schema[_k]["type"])
        assert _schema[_k]["default"] == _dv, (_k, _schema[_k]["default"])
    assert "cisa:kev" in str(_schema["feeds_security"]["default"])
    assert "qbitai" in str(_schema["feeds_ai"]["default"])
    assert "AI 界的新变化" in _schema["llm_prompt"]["default"]
    assert "网安漏洞与情报" in _schema["llm_prompt"]["default"]
    assert "AI 界的新变化" in mod.DIGEST_DEFAULT_PROMPT
    assert "网安漏洞与情报" in dd._normalize_prompt("旧的提示词")
    print("[24d] schema 7 个新键 + 提示词默认值 OK")

    # 24e) 中文字体自愈（图片乱码根因：官方镜像无 CJK 字体）
    assert mod.CJK_FONT_ASSET == os.path.join("assets", "fonts", "digest_cjk.ttf"), mod.CJK_FONT_ASSET
    _repo_font = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "fonts", "digest_cjk.ttf",
    )
    assert os.path.getsize(_repo_font) > 500_000, "自带中文字体缺失或过小"
    from PIL import ImageFont as _IF

    _rf = _IF.truetype(_repo_font, 24)
    assert bytes(_rf.getmask("中")) != bytes(_rf.getmask("国")), "自带字体把中文画成方框"
    assert _rf.getbbox("每日简报")[2] > 50, "自带字体量不到中文宽度"

    _tmp_data = tempfile.mkdtemp()
    _tmp_plug = os.path.join(_tmp_data, "plugins", "astrbot_plugin_elaina_suite")
    _tmp_asset_dir = os.path.join(_tmp_plug, "assets", "fonts")
    os.makedirs(_tmp_asset_dir, exist_ok=True)
    open(os.path.join(_tmp_plug, "main.py"), "w").close()
    with open(_repo_font, "rb") as _fs:
        _fb = _fs.read()
    _tmp_asset = os.path.join(_tmp_asset_dir, "digest_cjk.ttf")
    with open(_tmp_asset, "wb") as _fd:
        _fd.write(_fb)
    _orig_mod_file = mod.__file__
    mod.__file__ = os.path.join(_tmp_plug, "main.py")
    try:
        assert mod._plugin_data_dir() == _tmp_data, mod._plugin_data_dir()
        _p1 = mod._ensure_cjk_font()
        assert _p1 == os.path.join(_tmp_data, "font.ttf"), _p1
        assert os.path.getsize(_p1) == len(_fb), "字体未完整写入"
        with open(_p1, "wb") as _fu:
            _fu.write(b"USERFONT")
        assert mod._ensure_cjk_font() == _p1
        with open(_p1, "rb") as _fr:
            assert _fr.read() == b"USERFONT", "不应覆盖用户已有 font.ttf"
        os.remove(_tmp_asset)
        os.remove(_p1)
        assert mod._ensure_cjk_font() is None, "资源缺失应静默返回 None"
    finally:
        mod.__file__ = _orig_mod_file
    assert "_ensure_cjk_font()" in inspect.getsource(mod.DailyDigestCore._send_image), \
        "_send_image 未调用字体自愈"
    assert "_ensure_cjk_font()" in inspect.getsource(mod.ElainaSuitePlugin.__init__), \
        "__init__ 未调用字体自愈"
    print("[24e] 中文字体自愈 OK")

    # 24f) 图片文本去掉 emoji（本地字体无 emoji 字形会画方框）
    clean = mod._sanitize_for_image("## 🤖 AI 界的新变化 ✅ 抓取成功 🛡️ 网安 ⏳ 等待 ⭐ 精选")
    assert not any(ord(c) >= 0x1F000 for c in clean), clean
    assert "️" not in clean and "‍" not in clean, clean
    assert "✅" not in clean and "⏳" not in clean and "⭐" not in clean, clean
    for keep in ("AI 界的新变化", "抓取成功", "网安", "精选"):
        assert keep in clean, (keep, clean)
    print("[24f] 图片文本剔除 emoji（保留正文）OK")
    print("[24] AI 界的新变化 + 网安漏洞与情报板块 OK")

    await pl.terminate()
    assert ctx.send_message is orig, "门禁未卸载"
    print("[16] terminate() 完成，门禁已卸载")

asyncio.run(main())
print("ALL SMOKE TESTS PASSED")