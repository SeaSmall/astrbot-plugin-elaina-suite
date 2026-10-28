"""阶段五（傲娇重构）专属测试。

不依赖真实 LLM / 真实情感：LLM 与 Affect 全部打桩。
重点验证：零容忍元话语式否定、persona 块只给三条行为路径不再给标签句式、
情绪越负面傲娇越加密（三档提示）、审校 PASS/FAIL/改写/解析失败、
生成重试、配置键与接线。
运行：py tests/test_tsundere.py
"""
import asyncio
import importlib.util
import io
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("suite_tsundere", os.path.join(ROOT, "main.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

TMP = tempfile.mkdtemp(prefix="tsundere_test_")
mod._plugin_data_dir = lambda: TMP
UMO = "test:Friend:2002"


class FakeAffect:
    def __init__(self):
        self.pad = {"p": 0.3, "a": 0.0, "d": 0.0}
        self.silence_sev = 0.0
        self.on = True

    def enabled(self):
        return self.on

    def get_state(self, umo, now=None):
        return {"pad": dict(self.pad), "silence_sev": self.silence_sev, "poignancy": 0.0}

    def describe(self, umo, chars=0):
        return "（情感：还算平稳）"


class FakeOwner:
    def __init__(self, cfg):
        self.config = cfg
        self.kv = {}
        self.affect = FakeAffect()

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.kv[key] = value


BASE = {
    "pg_enabled": True,
    "living_enabled": True,
    "living_persona_chars": 4000,
    "living_worldview_chars": 1200,
    "pg_timezone": "Asia/Shanghai",
    "tsundere_enabled": True,
    "tsundere_review_enabled": True,
    "tsundere_encrypt_negative": True,
    "tsundere_max_retry": 1,
}


def make_core(**overrides):
    cfg = dict(BASE)
    cfg.update(overrides)
    owner = FakeOwner(cfg)
    core = mod.ProactiveGuardCore(owner)
    core.prompts = []
    return core


def fake_persona():
    return {
        "display_name": "伊蕾娜",
        "title": "灰之魔女",
        "tsundere": {
            "forbidden_meta_phrases": [{"phrase": "才不是特意"}, {"phrase": "别误会"}],
            "negation_patterns": [{"pattern": "旧式标签句式"}],
            "care_leak_patterns": [{"mechanism": "旧式机制"}],
        },
    }


def run(coro):
    return asyncio.run(coro)


async def t1_static_bans():
    bad = [
        "才不是特意给你带的。",
        "我才没有担心你呢。",
        "又不是为了你才早起的。",
        "别误会，我只是顺手。",
        "才没有想问你到哪了。",
        "作为AI我不能这么说。",
    ]
    for text in bad:
        assert mod.ProactiveGuardCore._living_check_text(text) == "", ("应被硬约束丢弃", text)
    good = [
        "顺手带的，吃不吃？",
        "你几点了还不睡。",
        "窗台的叶子又黄了一片，跟你上次说的一样。",
    ]
    for text in good:
        assert mod.ProactiveGuardCore._living_check_text(text) == text, ("不该误杀", text)
    print("[T1] 零容忍元话语式否定 + 不误杀合法傲娇 OK")


async def t2_persona_paths_not_labels():
    core = make_core()
    orig = mod.load_persona_asset
    mod.load_persona_asset = fake_persona
    try:
        block = await core._living_persona_block(UMO)
    finally:
        mod.load_persona_asset = orig
    assert "三条可用路径" in block, block[:200]
    for path in mod.TSUNDERE_PATHS:
        assert path in block, ("三条路径都要进人格块", path)
    assert "绝不直接否认自己的动机" in block
    assert "嘴硬时可以这样写" not in block, "旧式标签句式指示必须移除"
    assert "旧式标签句式" not in block
    assert "禁止出现" in block and "才不是特意" in block
    real = mod.load_persona_asset()
    assert isinstance(real.get("tsundere", {}).get("forbidden_meta_phrases"), list)
    print("[T2] persona 块只给行为路径、不含标签句式 OK")


async def t3_encrypt_levels():
    core = make_core()
    a = core.owner.affect
    a.pad, a.silence_sev = {"p": 0.3, "a": 0.0, "d": 0.0}, 0.0
    assert core._tsundere_encrypt_level(UMO) == 0
    assert mod.TSUNDERE_ENCRYPT_HINTS[0] in core._tsundere_encrypt_hint(UMO)
    a.pad = {"p": 0.0, "a": 0.0, "d": 0.0}
    assert core._tsundere_encrypt_level(UMO) == 1
    a.pad = {"p": -0.5, "a": 0.0, "d": 0.0}
    assert core._tsundere_encrypt_level(UMO) == 2
    assert "不许直说难过" in core._tsundere_encrypt_hint(UMO)
    a.pad, a.silence_sev = {"p": 0.5, "a": 0.0, "d": 0.0}, 0.6
    assert core._tsundere_encrypt_level(UMO) == 2, "沉默负面评估也要加密"
    core2 = make_core(tsundere_encrypt_negative=False)
    core2.owner.affect.pad = {"p": -0.9, "a": 0.0, "d": 0.0}
    assert core2._tsundere_encrypt_level(UMO) == 0
    core3 = make_core()
    core3.owner.affect = None
    assert core3._tsundere_encrypt_level(UMO) == 0
    print("[T3] 三档加密提示（含沉默评估、可关闭、无 affect 降级） OK")


async def t4_review_pass():
    core = make_core()
    calls = []

    async def _llm(prompt):
        calls.append(prompt)
        return '{"pass": true, "reason": "合格", "rewrite": ""}'

    core._llm_chat = _llm
    text = "顺手带的，吃不吃？"
    assert await core._living_tsundere_review(text, UMO, "平稳") == text
    assert len(calls) == 1 and "判定标准" in calls[0] and text in calls[0]
    print("[T4] 审校 PASS 原样返回 OK")


async def t5_review_fail_rewrite():
    core = make_core()

    async def _llm_rewrite(prompt):
        return '{"pass": false, "reason": "关心不可追溯", "rewrite": "药放桌上了，记得吃。"}'

    core._llm_chat = _llm_rewrite
    got = await core._living_tsundere_review("我有点担心你。", UMO, "平稳")
    assert got == "药放桌上了，记得吃。", got

    async def _llm_bad_rewrite(prompt):
        return '{"pass": false, "reason": "空泛", "rewrite": "才不是特意关心你。"}'

    core._llm_chat = _llm_bad_rewrite
    assert await core._living_tsundere_review("我有点担心你。", UMO, "平稳") == ""
    print("[T5] 审校 FAIL → 用合规改写；改写仍违规 → 丢弃 OK")


async def t6_review_degrade():
    core = make_core()

    async def _garbage(prompt):
        return "无法判定，直接放行。"

    core._llm_chat = _garbage
    text = "顺手带的，吃不吃？"
    assert await core._living_tsundere_review(text, UMO, "") == text

    async def _boom(prompt):
        raise RuntimeError("provider down")

    core._llm_chat = _boom
    assert await core._living_tsundere_review(text, UMO, "") == text
    print("[T6] 审校解析失败/异常时放行原文（不阻断发送） OK")


async def t7_review_switch_off():
    core = make_core(tsundere_review_enabled=False)
    calls = []

    async def _llm(prompt):
        calls.append(prompt)
        return '{"pass": false, "rewrite": ""}'

    core._llm_chat = _llm
    text = "顺手带的，吃不吃？"
    assert await core._living_tsundere_review(text, UMO, "") == text
    assert not calls, "关闭审校不应调用 LLM"
    print("[T7] 关闭审校时不发额外 LLM 调用 OK")


async def t8_compose_retry():
    core = make_core()
    seq = []

    async def _blocks(*a, **k):
        return ""

    async def _persona(umo=""):
        return "【人格】伊蕾娜"

    core._living_persona_block = _persona
    core._living_world_block = _blocks
    core._living_memory_block = _blocks

    async def _llm(prompt):
        if "判定标准" in prompt:
            seq.append("review")
            return '{"pass": true, "reason": "ok", "rewrite": ""}'
        if "【重写】" in prompt:
            seq.append("retry")
            return "顺手带的，吃不吃？"
        seq.append("first")
        return "才不是特意给你带的。"

    core._llm_chat = _llm
    now = mod.datetime.now()
    s = {"last_user_at": (now.timestamp() - 3600), "last_proactive_at": 0}
    text = await core._living_compose(UMO, s, "care", "他昨天说嗓子疼", now, "嗓子")
    assert text == "顺手带的，吃不吃？", text
    assert seq == ["first", "retry", "review"], seq

    async def _always_bad(prompt):
        if "判定标准" in prompt:
            return '{"pass": true, "rewrite": ""}'
        return "我才没有担心你。"

    core._llm_chat = _always_bad
    assert await core._living_compose(UMO, s, "care", "他昨天说嗓子疼", now) == ""
    print("[T8] 生成命中硬约束 → 带【重写】重生成一次 → 再过审校 OK")


async def t9_config_and_wiring():
    schema = json.loads(io.open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8-sig").read())
    for k in ("tsundere_enabled", "tsundere_review_enabled", "tsundere_encrypt_negative", "tsundere_max_retry"):
        assert k in schema, k
    assert schema["tsundere_enabled"]["default"] is True
    assert schema["tsundere_max_retry"]["default"] == 1
    src = io.open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    for needle in (
        "TSUNDERE_PATHS",
        "TSUNDERE_FORBIDDEN",
        "TSUNDERE_META_RE",
        "TSUNDERE_ENCRYPT_HINTS",
        "TSUNDERE_REVIEW_PROMPT",
        "self._tsundere_encrypt_hint(umo)",
        "self._living_persona_block(umo)",
        "self._living_tsundere_review(text, umo, affect_block)",
    ):
        assert needle in src, needle
    for old in ("嘴硬时可以这样写", "关心要以泄露的方式出现"):
        assert old not in src, ("旧的标签式指示应被替换", old)
    print("[T9] 4 个配置键 + 三路径/审校接线 OK")


async def t10_chat_injection():
    core = make_core()
    block = core.tsundere_chat_block(UMO)
    assert "【傲娇怎么写" in block
    for path in mod.TSUNDERE_PATHS:
        assert path in block
    assert "才不是特意" in block
    core_off = make_core(tsundere_chat_inject=False)
    assert core_off.tsundere_chat_block(UMO) == ""
    src = io.open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert "tsundere_on_llm_request" in src
    assert "guard.tsundere_chat_block(" in src
    assert "【傲娇怎么写" in src
    print("[T10] 正常对话 system prompt 注入（可关闭、幂等标记） OK")



async def main():
    await t1_static_bans()
    await t2_persona_paths_not_labels()
    await t3_encrypt_levels()
    await t4_review_pass()
    await t5_review_fail_rewrite()
    await t6_review_degrade()
    await t7_review_switch_off()
    await t8_compose_retry()
    await t9_config_and_wiring()
    await t10_chat_injection()
    print("阶段五（傲娇重构）测试全部通过")


if __name__ == "__main__":
    run(main())
