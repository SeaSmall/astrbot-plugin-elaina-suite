"""智能分段（v1.12.0，合并 astrbot_plugin_smart_segmentation）专属测试。

要修的问题（用户原话）：「长时间聊后，有的回复会另存在一个地方，当用户说什么的时候，
突然间蹦出来一大堆」。

根因（已彻查）：外部插件把第 2..N 段登记到 `_pending_follow_ups`，等
`after_message_sent` 之后用 `context.send_message` 补发；此时本轮请求的 webchat
back_queue 已被移除（`webchat_queue_mgr.remove_back_queue`），适配器
`send_by_session` 判定为「主动消息」，写进 `platform_message_history` 而不是推给前端
—— 于是消息「另存」在历史里，等用户下次说话、前端重新拉历史时一次性冒出来。

本测试固化五条契约：
  1. 切分保序、不丢字（拼回去 == 原文），段数受上限约束；
  2. 短句不单独成条（并到下一段）、短文本整体不分段；
  3. 非纯文本链（带图片）、命中 LLM 错误文案时不切；
  4. 即时发送：前 N-1 段在钩子里用 event.send 按顺序发出，末段留在 result.chain；
     某段发送失败时剩余内容并回末段，绝不丢字；
  5. 结构约束：不得再出现「后台补发」写法（after_message_sent / context.send_message），
     且配置键齐全（面板 159 → 170）；
  6. 分段开启时关掉流式（enable_streaming=False），否则 AstrBot 会把回复硬切成半句。

运行：py tests/test_segment.py
"""
import asyncio
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("suite_segment", os.path.join(ROOT, "main.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

CFG = {
    "segment_enabled": True,
    "segment_inline_send": True,
    "segment_max_segments": 3,
    "segment_min_chars": 14,
    "segment_max_chars": 60,
    "segment_delay_base": 0.0,
    "segment_delay_per_char": 0.0,
    "segment_delay_max": 0.0,
    "segment_style": "natural",
    "persona_coherence_enabled": True,
    "segment_disable_streaming": True,
}


class FakeOwner:
    def __init__(self, cfg=None):
        self.config = dict(CFG)
        if cfg:
            self.config.update(cfg)
        self.llm_guard = None


class FakeGuard:
    enabled = True

    def __init__(self, hit):
        self.hit = hit

    def match_reason(self, text):
        return "content_filter" if self.hit and text else None


class FakeResult:
    def __init__(self, chain):
        self.chain = chain

    def is_model_result(self):
        return True


class FakeEvent:
    def __init__(self, chain=None, text=""):
        self._result = FakeResult(chain if chain is not None else [mod.Plain(text)])
        self._extra = {}
        self.sent = []
        self.fail_at = None

    def get_result(self):
        return self._result

    def get_extra(self, key=None, default=None):
        return self._extra.get(key, default)

    def set_extra(self, key, value):
        self._extra[key] = value

    async def send(self, chain):
        if self.fail_at is not None and len(self.sent) == self.fail_at:
            raise RuntimeError("send boom")
        self.sent.append("".join(str(getattr(c, "text", "")) for c in chain.chain))


def core(cfg=None):
    return mod.SegmentCore(FakeOwner(cfg))


TEXT3 = "今天把阳台的花搬进来了。外面风太大了，怕吹坏。你那边天气怎么样？"


def s1_order_and_lossless():
    c = core()
    segs = c.split(TEXT3)
    assert len(segs) >= 2, segs
    assert "".join(segs) == TEXT3, segs
    assert all(seg.strip() for seg in segs)
    assert all(len(seg) <= 60 for seg in segs), [len(x) for x in segs]
    print("  s1 ok:", segs)
    return True


def s2_max_segments():
    c = core({"segment_max_segments": 2})
    text = "".join(f"第{i}句大概就是这样的情况了。" for i in range(6))
    segs = c.split(text)
    assert len(segs) == 2, segs
    assert "".join(segs) == text
    c3 = core({"segment_max_segments": 4})
    segs3 = c3.split(text)
    assert 2 <= len(segs3) <= 4, segs3
    assert "".join(segs3) == text
    print("  s2 ok:", [len(x) for x in segs])
    return True


def s3_short_not_split():
    c = core()
    assert c.split("嗯。") == ["嗯。"]
    assert c.split("") == []
    short = "好呀，那就这样。"
    assert c.split(short) == [short]
    print("  s3 ok")
    return True


def s4_style_conservative():
    c = core({"segment_style": "conservative", "segment_min_chars": 4})
    text = "第一句很长很长很长很长很长很长很长很长很长很长很长很长，还没说完。第二句也不短也不短也不短。"
    natural = core({"segment_min_chars": 4}).split(text)
    conservative = c.split(text)
    assert all(seg.endswith(("。", "？", "！", "…")) for seg in conservative), conservative
    assert len(natural) >= len(conservative)
    assert "".join(conservative) == text
    print("  s4 ok: natural", len(natural), "conservative", len(conservative))
    return True


def s5_short_merged():
    c = core({"segment_min_chars": 10})
    text = "嗯。好的。我昨天把阳台的花都搬进屋里了。你说吧。"
    segs = c.split(text)
    assert len(segs) >= 2, segs
    assert all(len(seg) >= 4 for seg in segs), segs
    assert "嗯。" not in segs
    assert "".join(segs) == text
    print("  s5 ok:", segs)
    return True


def s6_inline_send_order():
    c = core()
    ev = FakeEvent(text=TEXT3)
    asyncio.run(c.on_decorating_result(ev))
    expect = c.split(TEXT3)
    assert ev.sent == expect[:-1], (ev.sent, expect)
    chain = ev.get_result().chain
    assert len(chain) == 1 and chain[0].text == expect[-1]
    assert "".join(ev.sent) + chain[0].text == TEXT3
    assert ev.get_extra("_elaina_segment_done") == "1"
    print("  s6 ok: sent", len(ev.sent), "last", chain[0].text)
    return True


def s7_send_failure_keeps_text():
    c = core()
    ev = FakeEvent(text=TEXT3)
    ev.fail_at = 0
    asyncio.run(c.on_decorating_result(ev))
    assert ev.sent == []
    joined = ev.get_result().chain[0].text
    assert joined == TEXT3, joined
    print("  s7 ok: fallback keeps", len(joined), "chars")
    return True


def s8_skip_non_plain_and_error():
    c = core()
    ev_img = FakeEvent(chain=[mod.Plain(TEXT3), mod.Image(url="http://x/1.png")])
    asyncio.run(c.on_decorating_result(ev_img))
    assert ev_img.sent == [] and len(ev_img.get_result().chain) == 2

    ev_err = FakeEvent(text=TEXT3)
    c2 = core()
    c2.owner.llm_guard = FakeGuard(True)
    asyncio.run(c2.on_decorating_result(ev_err))
    assert ev_err.sent == [] and len(ev_err.get_result().chain) == 1

    ev_off = FakeEvent(text=TEXT3)
    c3 = core({"segment_enabled": False})
    asyncio.run(c3.on_decorating_result(ev_off))
    assert ev_off.sent == []
    print("  s8 ok")
    return True


def s9_delay_bounds():
    c = core({"segment_delay_base": 0.4, "segment_delay_per_char": 0.05, "segment_delay_max": 0.9})
    assert abs(c._delay("短") - 0.45) < 1e-9
    assert abs(c._delay("很长" * 20) - 0.9) < 1e-9
    assert c.status()["enabled"] is True
    print("  s9 ok")
    return True


def s10_structure():
    src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert "class SegmentCore" in src
    assert "self.segment = SegmentCore(self)" in src
    assert "segment_on_decorating_result" in src
    assert "self.segment.on_message(event)" in src
    assert "@filter.on_waiting_llm_request" not in src
    # 不再有「后台补发」写法
    assert "@filter.after_message_sent" not in src
    seg_src = src.split("class SegmentCore")[1].split("# 主插件类")[0]
    assert "await self.context.send_message" not in seg_src
    assert "await self.owner.context.send_message" not in seg_src
    assert "event.send(" in seg_src
    assert "create_task" not in seg_src
    # 人格连贯约束接线
    assert "persona_coherence_enabled" in src
    assert "· 连贯：" in src
    assert "接得上：先接住上面提到的那件事" in src
    import json

    schema = json.load(open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8"))
    for key in CFG:
        assert key in schema, key
    assert len(schema) == 170, len(schema)
    persona = json.load(open(os.path.join(ROOT, "assets", "persona.json"), encoding="utf-8"))
    assert len(persona["coherence"]["rules"]) >= 3
    print("  s10 ok: schema", len(schema))
    return True


def s11_disable_streaming():
    c = core()
    ev = FakeEvent(text="")
    asyncio.run(c.on_message(ev))
    assert ev.get_extra("enable_streaming") is False, ev._extra

    ev2 = FakeEvent(text="")
    asyncio.run(core({"segment_disable_streaming": False}).on_message(ev2))
    assert ev2.get_extra("enable_streaming") is None, ev2._extra

    ev3 = FakeEvent(text="")
    asyncio.run(core({"segment_enabled": False}).on_message(ev3))
    assert ev3.get_extra("enable_streaming") is None, ev3._extra
    print("  s11 ok: 只在分段开启时关流式")
    return True


def main():
    tests = [
        ("s1_order_and_lossless", s1_order_and_lossless),
        ("s2_max_segments", s2_max_segments),
        ("s3_short_not_split", s3_short_not_split),
        ("s4_style_conservative", s4_style_conservative),
        ("s5_short_merged", s5_short_merged),
        ("s6_inline_send_order", s6_inline_send_order),
        ("s7_send_failure_keeps_text", s7_send_failure_keeps_text),
        ("s8_skip_non_plain_and_error", s8_skip_non_plain_and_error),
        ("s9_delay_bounds", s9_delay_bounds),
        ("s10_structure", s10_structure),
        ("s11_disable_streaming", s11_disable_streaming),
    ]
    print("== test_segment ==")
    for name, fn in tests:
        print(name)
        fn()
    print("ALL SEGMENT TESTS PASSED")


if __name__ == "__main__":
    main()
