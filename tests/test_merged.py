import asyncio
import importlib.util
import inspect
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
assert mod.MEME_PROMPT_TEMPLATE and "MEME_KEYWORD" in mod.MEME_PROMPT_TEMPLATE
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
    "trigger_prob": 0.0,
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
    await pl.meme.on_message(ev)
    await pl.proactive_guard._on_user_message(ev)
    assert "aiocqhttp:Friend:1" in pl.proactive_guard._last_user_activity
    print("[8] 三条消息事件路由执行 OK（纯文字 no-op / 概率 0 不触发 / 活跃记录）")

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

    # 12) Elaina 表情包本地库
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "你好.gif"), "wb") as f:
        f.write(b"GIF89a" + b"\x00" * 64)
    with open(os.path.join(tmp, "无语.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    pl.config["meme_dir"] = tmp
    pl.config["meme_source"] = "library"
    pl.meme._meme_dir_ready = False
    pl.meme._meme_list = []
    assert pl.meme.use_library() is True
    memes = pl.meme._load_memes()
    assert "你好.gif" in memes and "无语.png" in memes, memes
    assert pl.meme.source == "library"
    pl.config["meme_source"] = "keyword"
    assert pl.meme.use_library() is False
    print("[12] ElainaMemeCore 本地表情库 OK:", memes)

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

    await pl.terminate()
    assert ctx.send_message is orig, "门禁未卸载"
    print("[15] terminate() 完成，门禁已卸载")

asyncio.run(main())
print("ALL SMOKE TESTS PASSED")