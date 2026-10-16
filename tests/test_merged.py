import asyncio
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
    finally:
        mod.httpx = orig_httpx
    print("[15] MiMo 2.6 默认 + 模型名自动降级 OK")

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

    # 18) 记忆库 + 子代理流水线（v1.1.1：与独立版 proactive-guard v1.1.0 对齐）
    pg = pl.proactive_guard
    # 测试桩的 KV 是 no-op，这里换成内存字典，便于验证记忆落库与读取
    _kv = {}

    async def _kv_get(key, default=None):
        return _kv.get(key, default)

    async def _kv_put(key, value):
        _kv[key] = value

    pg.get_kv_data = _kv_get
    pg.put_kv_data = _kv_put
    assert mod.MEMORY_KEY == "memory_store"
    assert "{memory}" in mod.MEMORY_BLOCK_TEMPLATE
    assert "{max_chars}" in mod.MEMORY_PROMPT and "{old}" in mod.MEMORY_PROMPT
    for _c in (mod.PLANNER_PROMPT, mod.WRITER_PROMPT, mod.REVIEWER_PROMPT):
        assert "{persona}" in _c, _c[:40]
    # 18a) 配置键齐全（9 个新键）
    _schema_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_conf_schema.json"
    )
    _schema = json.load(open(_schema_path, encoding="utf-8"))
    for _k in ("memory_enabled", "memory_cron", "memory_turns", "memory_max_chars",
               "memory_raw_fallback", "subagent_enabled", "subagent_parallel",
               "subagent_concurrency", "subagent_review"):
        assert _k in _schema, f"配置缺键 {_k}"
    assert "【记忆】" in _schema["memory_enabled"]["description"]

    class _Conv:
        def __init__(self, h):
            self.history = h

    class _CM:
        async def get_curr_conversation_id(self, umo):
            return "cid1"

        async def get_conversation(self, umo, cid):
            return _Conv(json.dumps([
                {"role": "user", "content": "我叫小鱼，正在做 AstrBot 插件"},
                {"role": "assistant", "content": [
                    {"type": "text", "text": "好的，记下了"},
                    {"type": "image_url", "image_url": {"url": "http://x/y.png"}},
                ]},
            ], ensure_ascii=False))

    _cm = _CM()
    ctx.conversation_manager = _cm
    # 18b) _history_lines：多模态只抽文本
    _lines = await pg._history_lines(_cm, "umo1", 20)
    assert any("小鱼" in x for x in _lines), _lines
    assert any("好的" in x for x in _lines), _lines
    assert not any("y.png" in x for x in _lines), _lines

    async def _targets():
        return ["umo1"]

    pg._collect_target_sessions = _targets
    _seen = {}

    async def _mem_chat(prompt):
        _seen["mem_prompt"] = prompt
        return "用户叫小鱼，正在做 AstrBot 插件。"

    pg._llm_chat = _mem_chat
    _out = await pg.refresh_memory()
    assert "小鱼" in _out, _out
    assert "（暂无）" in _seen["mem_prompt"], "首次总结应显示「暂无」旧摘要"
    _store = await pg.get_kv_data("memory_store", None)
    assert _store and _store["items"][0]["umo"] == "umo1", _store
    # 18c) _collect_memory：生成时能读到
    _mem = await pg._collect_memory()
    assert "小鱼" in _mem, _mem

    # 18d) _generate_and_store：记忆进子代理 + 规划失败时真的回退单次调用
    pg._random_times = lambda n: ["07:35"]

    async def _subagents_fail(date, times, time_list, count, persona, memory_block):
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

    async def _subagents_ok(date, times, time_list, count, persona, memory_block):
        return [{"time": "07:35", "text": "子代理写的"}]

    pg._generate_with_subagents = _subagents_ok
    _seen.pop("gen_prompt", None)
    await pg._generate_and_store("2099-01-02")
    assert "gen_prompt" not in _seen, "子代理成功却又走了单次调用"

    class _Ev:
        def plain_result(self, text):
            return text

    # 18e) /记忆 与 /刷新记忆
    _got = [r async for r in pg.memory_command(_Ev())]
    assert "🧠 记忆库" in _got[0] and "小鱼" in _got[0], _got
    pl.config["memory_enabled"] = False
    _got2 = [r async for r in pg.memory_command(_Ev())]
    assert "memory_enabled = false" in _got2[0], _got2
    _got3 = [r async for r in pg.refresh_memory_command(_Ev())]
    assert "memory_enabled" in _got3[0], _got3
    assert await pg.refresh_memory() == "", "关闭记忆后不应写入"
    pl.config["memory_enabled"] = True
    # 18f) _truncate_memory 上限
    _long = "\n".join(f"第{i}行的记忆内容" for i in range(400))
    _cut = pg._truncate_memory(_long)
    assert len(_cut) < len(_long), (len(_cut), len(_long))
    print("[18] 记忆库 + 子代理流水线（含回退单次生成）OK")

    await pl.terminate()
    assert ctx.send_message is orig, "门禁未卸载"
    print("[16] terminate() 完成，门禁已卸载")

asyncio.run(main())
print("ALL SMOKE TESTS PASSED")