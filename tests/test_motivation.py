"""阶段四（动机与注意力：GWA 竞争 + 内在动机 + 学习进度门控）测试。

不依赖真实 LLM / 真实情感状态：Affect 用桩，只验证机制契约。
运行：py tests/test_motivation.py
"""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("suite_motivation", os.path.join(ROOT, "main.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

TMP = tempfile.mkdtemp(prefix="motivation_test_")
mod._plugin_data_dir = lambda: TMP
STATE = os.path.join(TMP, "motivation_state.json")


class FakeAffect:
    def __init__(self):
        self.pad = {"p": 0.0, "a": 0.0, "d": 0.0}
        self.silence_sev = 0.0
        self.on = True

    def enabled(self):
        return self.on

    def get_state(self, umo, now=None):
        return {"pad": dict(self.pad), "silence_sev": self.silence_sev, "poignancy": 0.0}


class FakeOwner:
    def __init__(self, cfg):
        self.config = cfg
        self.kv = {}
        self.affect = FakeAffect()


BASE = {
    "motivation_enabled": True,
    "motivation_affect_gate": True,
    "motivation_learning_gate": True,
    "motivation_topic_max": 12,
    "motivation_topic_chars": 24,
    "motivation_engage_window_minutes": 180,
    "motivation_select_noise": 0.0,
    "motivation_loser_decay": 0.85,
}
UMO = "aiocqhttp:Friend:10001"


def make_core(**overrides):
    cfg = dict(BASE)
    cfg.update(overrides)
    for p in (STATE, STATE + ".part"):
        if os.path.exists(p):
            os.remove(p)
    core = mod.MotivationCore(FakeOwner(cfg))
    core._state = None
    return core


def cands(share=1.0, care=0.6, followup=0.55, curiosity=0.45, check_in=0.3):
    return [
        {"intent": "share", "label": "分享", "score": share, "reason": "r"},
        {"intent": "care", "label": "关心", "score": care, "reason": "r"},
        {"intent": "followup", "label": "追问", "score": followup, "reason": "r"},
        {"intent": "curiosity", "label": "好奇", "score": curiosity, "reason": "r"},
        {"intent": "check_in", "label": "确认", "score": check_in, "reason": "r"},
    ]


def now_dt():
    return mod.datetime.now()


async def n1_disabled():
    core = make_core(motivation_enabled=False)
    base = cands()
    got = core.reweight(UMO, base, now_dt())
    assert got is base, "关闭时应原样返回候选"
    assert core.select(UMO, base, now_dt()) == {}
    await core.observe_user(UMO, "今天好累")
    assert not (core._load().get("sessions") or {}), "关闭时不应写状态"
    print("[N1] 关闭时不动候选、不写状态 OK")


async def n2_observe_topics():
    core = make_core()
    await core.observe_user(UMO, "我下周要去杭州出差，顺便看看西湖")
    s = core._sstate(UMO)
    assert len(s["topics"]) == 1, s["topics"]
    key = list(s["topics"])[0]
    assert "杭州" in key and len(key) <= 24, key
    assert len(s["pending"]) == 1, s["pending"]
    await core.observe_user(UMO, "杭州出差的事还没定")
    s = core._sstate(UMO)
    assert len(s["topics"]) == 1, "相似话题应合并而不是新增"
    print("[N2] 话题抽取/相似合并/待接话题去重 OK")


async def n3_learning_progress():
    core = make_core()
    await core.observe_user(UMO, "我在学吉他")
    topic = list(core._sstate(UMO)["topics"])[0]
    entry = core._sstate(UMO)["topics"][topic]
    gate0 = core.learning_gate(entry)
    assert abs(gate0 - 1.0) < 1e-6, ("新话题应为中性 1.0", gate0)
    for _ in range(4):
        await core.note_attempt(UMO, "followup", topic)
    entry = core._sstate(UMO)["topics"][topic]
    gate_low = core.learning_gate(entry)
    assert gate_low < 0.75, ("反复发起无回应应降权", gate_low)
    assert gate_low >= 0.2, gate_low
    await core.observe_user(UMO, "吉他练了半小时")
    entry = core._sstate(UMO)["topics"][topic]
    gate_up = core.learning_gate(entry)
    assert gate_up > gate_low, (gate_low, gate_up)
    prog = core.learning_progress(entry)
    assert 0.0 <= prog <= 1.0
    print("[N3] 学习进度：反复无回应 → 门控下降；得到回应 → 回升 OK")


async def n4_affect_gate():
    core = make_core()
    core.owner.affect.pad = {"p": -0.3, "a": -0.1, "d": -0.2}
    m_share, why = core.affect_multiplier("share", core._affect_ctx(UMO))
    m_check, _ = core.affect_multiplier("check_in", core._affect_ctx(UMO))
    assert m_share <= 0.5, m_share
    assert m_check >= 1.3, m_check
    assert "低落" in why or "提不起" in why, why
    core.owner.affect.pad = {"p": 0.4, "a": 0.35, "d": 0.3}
    m_share2, _ = core.affect_multiplier("share", core._affect_ctx(UMO))
    m_check2, _ = core.affect_multiplier("check_in", core._affect_ctx(UMO))
    assert m_share2 > 1.1, m_share2
    assert m_check2 < 1.0, m_check2
    core.owner.affect.on = False
    assert core.affect_multiplier("share", core._affect_ctx(UMO))[0] == 1.0
    print("[N4] 情感门控：低落时分享降权/试探升权，愉快时相反 OK")


async def n5_reweight():
    core = make_core()
    await core.observe_user(UMO, "我在学吉他，想弹好一首曲子")
    topic = list(core._sstate(UMO)["topics"])[0]
    out = core.reweight(UMO, cands(), now_dt())
    by = {c["intent"]: c for c in out}
    assert by["followup"]["topic"] == topic, by["followup"]
    assert by["followup"]["score"] > 0.4, ("新话题按中性门控，不该被压分", by["followup"]["score"])
    assert "学习进度" in by["followup"]["why"], by["followup"]["why"]
    assert by["care"]["topic"] in ("", topic)
    core2 = make_core()
    out2 = core2.reweight(UMO, cands(), now_dt())
    by2 = {c["intent"]: c for c in out2}
    assert by2["followup"]["score"] < 0.55 * 0.5, "没有由头时应被压分"
    assert "由头" in by2["followup"]["why"], by2["followup"]["why"]
    print("[N5] 重加权：有由头的话题门控生效、无由头被压到 0.35 OK")


async def n6_select():
    core = make_core()
    win = core.select(UMO, cands(share=1.0, care=0.4), now_dt())
    assert win["intent"] == "share", win
    comp = core._sstate(UMO)["last_competition"]
    assert comp["winner"] == "share" and len(comp["losers"]) == 4, comp
    assert all(x["score"] < comp["winner_score"] for x in comp["losers"]), comp
    base_by = {"share": 1.0, "care": 0.4, "followup": 0.55, "curiosity": 0.45, "check_in": 0.3}
    for item in comp["losers"]:
        want = base_by[item["intent"]] * 0.85
        assert abs(item["score"] - want) < 1e-6, (item, want)
    print("[N6] 排他性瓶颈：单个 winner + 落选降权不删除 OK")


async def n7_persist():
    core = make_core()
    await core.observe_user(UMO, "周末想去爬山")
    await core.note_attempt(UMO, "curiosity", list(core._sstate(UMO)["topics"])[0])
    await core._save()
    assert os.path.exists(STATE)
    cfg = dict(BASE)
    core2 = mod.MotivationCore(FakeOwner(cfg))
    s2 = core2._sstate(UMO)
    assert len(s2["topics"]) == 1, s2
    assert int(list(s2["topics"].values())[0]["attempts"]) == 1
    print("[N7] 状态持久化：话题与 attempts 可被新实例读回 OK")


async def n8_status_and_cap():
    core = make_core(motivation_topic_max=3)
    for i in range(6):
        await core.observe_user(UMO, "话题内容{}".format(i))
    s = core._sstate(UMO)
    assert len(s["pending"]) <= 3, s["pending"]
    st = core.status()
    assert st["enabled"] is True
    sid = core._sid(UMO)
    assert sid in st["sessions"], st
    assert isinstance(st["sessions"][sid]["topics"], list)
    print("[N8] 待接话题上限 + /动机状态 数据面 OK")


async def n9_wiring():
    main_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert "class MotivationCore(_PluginCoreBase)" in main_src
    assert "self.motivation = MotivationCore(self)" in main_src
    assert "await self.motivation.initialize()" in main_src
    assert "await self.motivation.close()" in main_src
    assert "await self._living_on_user_message(" in main_src
    assert "mot.observe_user(umo, text)" in main_src
    assert "mot.note_attempt(" in main_src
    assert "mot.reweight(umo, out, now_dt)" in main_src
    assert "mot.select(umo, cands, now_dt)" in main_src
    assert "async def _living_plan_intent" in main_src
    assert "动机状态" in main_src and "注意力状态" in main_src
    assert "具体到：{topic}" in main_src
    print("[N9] 插件接线完整（构造/初始化/收尾/观察/尝试/加权/竞争/命令/由头注入） OK")


async def n10_schema():
    schema = json.load(open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8-sig"))
    for k in ("motivation_enabled", "motivation_affect_gate", "motivation_learning_gate",
              "motivation_topic_max", "motivation_topic_chars",
              "motivation_engage_window_minutes", "motivation_select_noise",
              "motivation_loser_decay"):
        assert k in schema, k
    assert schema["motivation_enabled"]["default"] is False, "默认必须关闭（与 v1.4.0 行为一致）"
    assert len(schema) >= 151, len(schema)
    print("[N10] 配置面板 8 个 motivation_* 键齐备，默认关闭 OK")


async def n11_shared_scope():
    """跨会话共享作用域：QQ 与语音通道共用同一份话题/学习进度（默认关闭时各一份）。"""
    QQ = "default_1000000001:FriendMessage:0123456789ABCDEF0123456789ABCDEF"
    PHONE = "webchat_phone!alice!main"
    core = make_core(shared_scope_enabled=True, shared_scope_id="alice")
    assert core._sid(QQ) == core._sid(PHONE) == "shared_alice"
    await core.observe_user(QQ, "我下个月要去日本看枫叶，顺便想买台相机")
    await core.observe_user(PHONE, "相机的事我还没定下来")
    st = core._load()
    assert list(st["sessions"].keys()) == ["shared_alice"], st["sessions"].keys()
    assert len(st["sessions"]["shared_alice"]["topics"]) >= 1
    off = make_core()
    assert off._sid(QQ) != off._sid(PHONE)
    print("[N11] 共享作用域：语音与 QQ 同一份话题；关闭时按会话隔离 OK")


async def main():
    for fn in [n1_disabled, n2_observe_topics, n3_learning_progress, n4_affect_gate,
               n5_reweight, n6_select, n7_persist, n8_status_and_cap, n9_wiring, n10_schema, n11_shared_scope]:
        await fn()
    print("阶段四（动机与注意力）测试全部通过")


if __name__ == "__main__":
    asyncio.run(main())
