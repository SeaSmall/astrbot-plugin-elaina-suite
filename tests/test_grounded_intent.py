"""真实由头（grounded intent，v1.11.0）专属测试。

要修的问题（用户原话）：「动机队列总说『这个事』，而这个『事』是什么完全
是自己认为的——必须结合记忆去做，而不是自己去猜」。

旧行为：`followup` / `curiosity` 候选无论有没有由头都会进候选池（无由头只是
乘 0.35），`care` 的由头还写死成「记起了对方提过的事」，生成时模型手上没有
任何事实，只能现编一件「上次那个事」。

本测试固化三条契约：
  1. 寒暄/语气词不算话题，不会变成将来的「由头」；
  2. 没有真实由头（用户原话话题 / 长期记忆召回）时，追问与好奇类候选根本不存在；
  3. 由头真的存在时，会随候选 → 计划 → 生成一路传下去，并写进提示词。
运行：py tests/test_grounded_intent.py
"""
import asyncio
import importlib.util
import os
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("suite_grounded", os.path.join(ROOT, "main.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

TMP = tempfile.mkdtemp(prefix="grounded_test_")
mod._plugin_data_dir = lambda: TMP
TZ = ZoneInfo("Asia/Shanghai")
UMO = "test:Friend:1001"
NOW = datetime(2026, 3, 3, 15, 0, tzinfo=TZ)

MOT_CFG = {
    "motivation_enabled": True,
    "motivation_affect_gate": True,
    "motivation_learning_gate": True,
    "motivation_topic_max": 12,
    "motivation_topic_chars": 24,
    "motivation_engage_window_minutes": 180,
    "motivation_select_noise": 0.0,
    "motivation_loser_decay": 0.85,
    "affect_sleep_hours": "off",
}


# --------------------------------------------------------------- 假环境
class FakeAffect:
    def __init__(self):
        self.pad = {"p": 0.0, "a": 0.0, "d": 0.0}
        self.silence_sev = 0.0

    def enabled(self):
        return True

    def get_state(self, umo, now=None):
        return {"pad": dict(self.pad), "silence_sev": self.silence_sev, "poignancy": 0.0}

    def describe(self, umo="", chars=0):
        return "此刻心情：平稳"


class FakeOwner:
    def __init__(self, cfg):
        self.config = cfg
        self.kv = {}
        self.affect = FakeAffect()

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.kv[key] = value


def make_mot(**overrides):
    cfg = dict(MOT_CFG)
    cfg.update(overrides)
    for p in os.listdir(TMP):
        if p.startswith("motivation_state"):
            os.remove(os.path.join(TMP, p))
    return mod.MotivationCore(FakeOwner(cfg))


class FakeMemory:
    """假长期记忆：search 返回固定事实。"""

    def __init__(self, memories):
        self.memories = list(memories)

    def enabled(self):
        return True

    def recent_text(self, umo):
        return "最近聊过嗓子"

    async def search(self, query, umo="", top_k=4):
        return [{"memory": m, "score": 0.9} for m in self.memories[:top_k]]


class FakeGuardOwner(FakeOwner):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.proactive_guard = None  # 由 make_guard 回填，避免循环


LIVING_CFG = {
    "pg_enabled": True,
    "living_enabled": True,
    "living_min_silence_minutes": 90,
    "living_cooldown_minutes": 180,
    "living_daily_max": 6,
    "living_quiet_hours": "off",
    "living_sleep_aware": False,
    "living_offset_min": 5,
    "living_offset_max": 45,
    "living_ramp_minutes": 240,
    "pg_timezone": "Asia/Shanghai",
    "tsundere_review_enabled": False,
}


def make_guard(mot=None, memory=None):
    cfg = dict(LIVING_CFG)
    owner = FakeGuardOwner(cfg)
    core = mod.ProactiveGuardCore(owner)
    owner.proactive_guard = core
    if mot is not None:
        owner.motivation = mot
    if memory is not None:
        owner.living_memory = memory
    core.prompts = []

    async def _persona(_umo):
        return "人格块"

    async def _world():
        return "世界观块"

    async def _memory_block(_umo):
        return "记忆块"

    async def _llm(prompt):
        core.prompts.append(prompt)
        return "哼，那就说说你昨天那个事吧。"

    core._living_persona_block = _persona
    core._living_world_block = _world
    core._living_memory_block = _memory_block
    core._llm_chat = _llm
    return core


def cands():
    return [
        {"intent": "share", "label": "分享", "score": 1.0, "reason": "r"},
        {"intent": "care", "label": "关心", "score": 0.6, "reason": "r"},
        {"intent": "followup", "label": "追问", "score": 0.55, "reason": "r"},
        {"intent": "curiosity", "label": "好奇", "score": 0.45, "reason": "r"},
        {"intent": "check_in", "label": "确认", "score": 0.3, "reason": "r"},
    ]


# --------------------------------------------------- 1. 寒暄不算话题
async def g1_meaningless_topics_dropped():
    core = make_mot()
    assert not core.meaningful_topic("嗯")
    assert not core.meaningful_topic("嗯嗯嗯")
    assert not core.meaningful_topic("你好")
    assert not core.meaningful_topic("晚安")
    assert not core.meaningful_topic("好的")
    assert not core.meaningful_topic("哈哈哈")
    assert not core.meaningful_topic("1234")
    assert not core.meaningful_topic("ok")
    assert core.meaningful_topic("我在学吉他")
    assert core.meaningful_topic("下周要去杭州出差")

    await core.observe_user(UMO, "嗯")
    await core.observe_user(UMO, "晚安")
    s = core._sstate(UMO)
    assert not (s.get("topics") or {}), ("寒暄不该成为话题", s.get("topics"))
    assert not (s.get("pending") or []), ("寒暄不该进待接队列", s.get("pending"))

    await core.observe_user(UMO, "我在学吉他，想弹好一首曲子")
    s = core._sstate(UMO)
    assert len(s["topics"]) == 1 and len(s["pending"]) == 1, s
    print("[G1] 寒暄/语气词不进话题池；真话题正常记录 OK")


# --------------------------------------- 2. 没有由头 → 追问/好奇不存在
async def g2_no_evidence_no_followup_candidates():
    core = make_guard(mot=make_mot(), memory=None)
    out = await core._living_candidates(UMO, {"last_user_at": NOW.timestamp() - 600}, NOW)
    intents = [c["intent"] for c in out]
    assert "followup" not in intents and "curiosity" not in intents, intents
    assert "share" in intents and "care" in intents, intents
    care = [c for c in out if c["intent"] == "care"][0]
    assert care["reason"] == "", ("没有由头时 care 不许自称记得", care)
    assert not care.get("evidence"), care
    print("[G2] 无真实由头：不产生追问/好奇候选，关心也不编「记起你提过的事」OK")


# -------------------------------------------- 3. 有由头 → 带证据进候选
async def g3_evidence_from_user_topic():
    mot = make_mot()
    await mot.observe_user(UMO, "我下周要去杭州出差，顺便看看西湖")
    core = make_guard(mot=mot, memory=None)
    out = await core._living_candidates(UMO, {"last_user_at": NOW.timestamp() - 600}, NOW)
    by = {c["intent"]: c for c in out}
    assert "followup" in by and "curiosity" in by, list(by)
    assert by["followup"]["evidence"], by["followup"]
    assert "杭州" in "".join(by["followup"]["evidence"]), by["followup"]
    assert "杭州" in by["followup"]["reason"], by["followup"]
    assert by["care"]["reason"], "有由头时关心应当能说出由头"
    print("[G3] 用户原话话题 → 追问/好奇候选带真实证据（含「杭州」）OK")


async def g4_evidence_from_memory():
    core = make_guard(mot=make_mot(), memory=FakeMemory(["他昨天说嗓子疼，因为熬夜改方案"]))
    out = await core._living_candidates(UMO, {"last_user_at": NOW.timestamp() - 600}, NOW)
    by = {c["intent"]: c for c in out}
    assert "followup" in by, list(by)
    joined = "".join(by["followup"]["evidence"])
    assert "嗓子" in joined, by["followup"]
    print("[G4] 长期记忆召回也能作为由头（不再只靠用户最后一句话）OK")


async def g5_evidence_survives_into_prompt():
    mot = make_mot()
    await mot.observe_user(UMO, "我下周要去杭州出差，顺便看看西湖")
    core = make_guard(mot=mot, memory=None)
    s = {"last_user_at": NOW.timestamp() - 600, "last_proactive_at": 0}
    plan = await core._living_plan_intent(UMO, s, NOW)
    assert plan.get("evidence"), plan
    text = await core._living_compose(
        UMO, s, plan["intent"], plan["reason"], NOW, plan.get("topic") or "", plan.get("evidence")
    )
    assert text, "有由头时应当生成"
    prompt = core.prompts[-1]
    assert "你确实记得的事" in prompt, prompt
    assert "杭州" in prompt, prompt
    print("[G5] 由头随 plan → compose 进入提示词「你确实记得的事」OK")


# ------------------------------------- 4. 兜底：无证据的追问直接不生成
async def g6_compose_refuses_ungrounded():
    core = make_guard(mot=make_mot(), memory=None)
    s = {"last_user_at": NOW.timestamp() - 600, "last_proactive_at": 0}
    for intent in ("followup", "curiosity"):
        text = await core._living_compose(UMO, s, intent, "", NOW)
        assert text == "", (intent, text)
        assert not core.prompts, ("没有由头就不该调用 LLM", core.prompts)
    print("[G6] 没有由头时追问/好奇不调用 LLM、不生成（宁可不说也不编）OK")


# --------------------------------------- 5. 打分闸门 grounded_only
async def g7_grounded_only_gate():
    core = make_mot()
    kept = core.grounded_only(cands(), [])
    intents = [c["intent"] for c in kept]
    assert "followup" not in intents and "curiosity" not in intents, intents
    assert "share" in intents and "care" in intents and "check_in" in intents

    kept2 = core.grounded_only(cands(), ["他提过嗓子疼"])
    intents2 = [c["intent"] for c in kept2]
    assert "followup" in intents2 and "curiosity" in intents2, intents2

    with_topic = [dict(c) for c in cands()]
    for c in with_topic:
        if c["intent"] == "followup":
            c["topic"] = "杭州出差"
    intents3 = [c["intent"] for c in core.grounded_only(with_topic, [])]
    assert "followup" in intents3, ("reweight 已给出真实 topic 时应保留", intents3)
    print("[G7] grounded_only：无由头丢弃追问/好奇，有由头或已绑定 topic 才保留 OK")


async def main():
    for fn in (g1_meaningless_topics_dropped, g2_no_evidence_no_followup_candidates,
               g3_evidence_from_user_topic, g4_evidence_from_memory,
               g5_evidence_survives_into_prompt, g6_compose_refuses_ungrounded,
               g7_grounded_only_gate):
        await fn()
    print("阶段七（真实由头）测试全部通过")


if __name__ == "__main__":
    asyncio.run(main())
