"""阶段三（情感状态 Sentipolis PAD）专属测试。

不依赖真实 LLM：规则式快通道 + 假 guard._llm_chat。
重点验证：衰减按真实 dt 折算（T½=120min）、快通道更新与限幅、
慢通道 poignancy>阈值触发 reflection 并归零、沉默评估走正常更新通道、
沉默时衰减目标点偏移、生成注入的分档描述、状态持久化、接线与配置键。
运行：py tests/test_affect.py
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
spec = importlib.util.spec_from_file_location("suite_affect", os.path.join(ROOT, "main.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

TMP = tempfile.mkdtemp(prefix="affect_test_")
mod._plugin_data_dir = lambda: TMP
STATE = os.path.join(TMP, "affect_state.json")


class FakeGuard:
    def __init__(self):
        self.reply = ""
        self.prompts = []

    async def _llm_chat(self, prompt):
        self.prompts.append(prompt)
        return self.reply


class FakeOwner:
    def __init__(self, cfg):
        self.config = cfg
        self.kv = {}
        self.proactive_guard = FakeGuard()

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.kv[key] = value


BASE = {
    "affect_enabled": True,
    "affect_fast_mode": "heuristic",
    "affect_decay_half_life_minutes": 120,
    "affect_silence_threshold_minutes": 60,
    "affect_silence_check_minutes": 15,
    "affect_silence_cooldown_minutes": 120,
    "affect_silence_target": "-0.45,-0.10,-0.20",
    "affect_reflect_poignancy": 150,
    "affect_slow_delta_cap": 0.6,
    "affect_inject_chars": 600,
    "affect_baseline": "0.15,0.25,0.70",
}
UMO = "aiocqhttp:Friend:10001"


def make_core(**overrides):
    cfg = dict(BASE)
    cfg.update(overrides)
    for p in (STATE, STATE + ".part"):
        if os.path.exists(p):
            os.remove(p)
    return mod.AffectCore(FakeOwner(cfg))


def seed(core, pad, sev=0.0, last_user_gap=None, now=None):
    """按「当前真实时间」建一条状态，避免混用假时间戳导致衰减失真。"""
    now = float(now if now is not None else time.time())
    st = core._entry(UMO, now)
    st["pad"] = dict(pad)
    st["updated"] = now
    st["silence_sev"] = sev
    if last_user_gap is not None:
        st["last_user_at"] = now - last_user_gap
    return st, now


async def a1_decay_half_life():
    core = make_core()
    st, now = seed(core, {"p": 0.8, "a": 0.4, "d": 0.6})
    got = core.get_state(UMO, now + 120 * 60)["pad"]
    assert abs(got["p"] - 0.4) < 1e-6, got
    assert abs(got["a"] - 0.2) < 1e-6, got
    assert abs(got["d"] - 0.3) < 1e-6, got
    st2, now2 = seed(core, {"p": 0.8, "a": 0.4, "d": 0.6})
    got2 = core.get_state(UMO, now2 + 240 * 60)["pad"]
    assert abs(got2["p"] - 0.2) < 1e-6, got2
    print("[A1] 衰减按真实 dt 折算：120 分钟减半、240 分钟四分之一 OK")


async def a2_disabled():
    core = make_core(affect_enabled=False)
    await core.fast_update(UMO, "你真好", "哼")
    assert core.describe(UMO) == ""
    assert not (core._load().get("sessions") or {}), "关闭时不应写状态"
    print("[A2] 关闭时不动状态、不注入 OK")


async def a3_fast_channel():
    core = make_core()
    base, now = seed(core, core._persona_baseline())
    base_p = core.get_state(UMO, now)["pad"]["p"]
    await core.fast_update(UMO, "你真好，我喜欢你", "才、才没有很开心")
    p = core.get_state(UMO)["pad"]["p"]
    assert p > base_p + 0.1, (base_p, p)
    await core.fast_update(UMO, "滚，别烦我", "")
    p2 = core.get_state(UMO)["pad"]["p"]
    assert p2 < p, (p, p2)
    for _ in range(40):
        await core.fast_update(UMO, "你真好我喜欢你", "哼")
    pad = core.get_state(UMO)["pad"]
    assert all(-1.0 <= pad[k] <= 1.0 for k in ("p", "a", "d")), pad
    print("[A3] 快通道：夸奖抬 p、斥责压 p、PAD 恒在 [-1,1] OK")


async def a4_slow_reflect():
    guard = FakeGuard()
    guard.reply = json.dumps({"delta": {"p": -0.3, "a": 0.1, "d": -0.2}, "poignancy": 12,
                          "appraisal": "她累了", "insight": "她累了"},
                             ensure_ascii=False)
    core = make_core(affect_fast_mode="llm", affect_reflect_poignancy=1)
    core.owner.proactive_guard = guard
    seed(core, core._persona_baseline())
    await core.fast_update(UMO, "随便吧", "……")
    st = core.get_state(UMO)
    assert float(st.get("poignancy") or 0) == 0.0, "reflection 后 poignancy 必须归零"
    kinds = [e.get("kind") for e in st.get("events") or []]
    assert "reflection" in kinds, kinds
    assert len(guard.prompts) >= 2, "应先后调用 appraisal 与 reflection 两次"
    print("[A4] 慢通道：poignancy 过阈值触发 reflection 并归零 OK")


async def a5_silence_channel():
    core = make_core(affect_silence_threshold_minutes=60)
    seed(core, {"p": 0.3, "a": 0.1, "d": 0.2}, last_user_gap=300 * 60)
    r = await core.silence_check()
    assert r["injected"] == 1, r
    st = core.get_state(UMO)
    assert float(st["pad"]["p"]) < 0.3, st["pad"]
    kinds = [e.get("kind") for e in st.get("events") or []]
    assert "silence" in kinds, kinds
    assert float(st.get("silence_sev") or 0) >= 0.5, st.get("silence_sev")
    r2 = await core.silence_check()
    assert r2["injected"] == 0, "冷却内不得重复注入"
    print("[A5] 沉默评估：走正常更新通道注入负面事件 + 冷却生效 OK")


async def a6_silence_decay_target():
    core = make_core()
    st, now = seed(core, {"p": 0.0, "a": 0.0, "d": 0.0}, sev=1.0)
    got = core.get_state(UMO, now + 120 * 60)["pad"]
    assert got["p"] < -0.15, got
    assert got["d"] < -0.05, got
    core2 = make_core(affect_silence_target="")
    st2, now2 = seed(core2, {"p": 0.0, "a": 0.0, "d": 0.0}, sev=1.0)
    got2 = core2.get_state(UMO, now2 + 120 * 60)["pad"]
    assert abs(got2["p"]) < 1e-6, got2
    print("[A6] 沉默时衰减目标点偏移到负面基线；留空则回中性 OK")


async def a7_describe():
    core = make_core()
    seed(core, {"p": -0.5, "a": 0.1, "d": -0.4})
    low = core.describe(UMO)
    assert "此刻的心情" in low, low
    assert "试探" in low, low
    seed(core, {"p": 0.6, "a": 0.3, "d": 0.5})
    high = core.describe(UMO)
    assert "轻快" in high or "心情很好" in high, high
    assert "试探" not in high, high
    print("[A7] 心情块分档：低 P 低 D 出「试探/留退路」、高 P 出「轻快」 OK")


async def a8_persist():
    core = make_core()
    seed(core, {"p": 0.42, "a": -0.1, "d": 0.3})
    await core._save()
    assert os.path.exists(STATE), "状态文件未落盘"
    with open(STATE, encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["sessions"][core._sid(UMO)]["pad"]["p"] == 0.42
    core2 = mod.AffectCore(FakeOwner(dict(BASE)))  # 不清状态，模拟重启后新实例
    got = core2.get_state(UMO)["pad"]
    assert abs(got["p"] - 0.42) < 1e-6, got
    print("[A8] 状态持久化：落盘后可被新实例读回 OK")


async def a9_wiring():
    src = io.open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert "class AffectCore(" in src
    assert "self.affect = AffectCore(self)" in src
    assert "await self.affect.initialize()" in src
    assert "await self.affect.close()" in src
    assert "affect_on_llm_response" in src
    assert "{affect_block}" in src, "生成提示词必须留注入位"
    assert 'prompt.replace("{affect_block}", affect_block)' in src
    assert "情感状态" in src
    assert "self.affect.note_user(" in src
    body = src.split("class AffectCore(")[1].split("# 核心四")[0]
    assert "def _apply(" in body and "silence_sev" in body and "poignancy" in body
    print("[A9] 接线完整：构造/初始化/收尾/回复钩子/注入位/状态命令 OK")


async def a10_schema():
    raw = json.load(io.open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8-sig"))
    keys = ["affect_enabled", "affect_fast_mode", "affect_decay_half_life_minutes",
            "affect_silence_threshold_minutes", "affect_silence_check_minutes",
            "affect_silence_cooldown_minutes", "affect_silence_target",
            "affect_reflect_poignancy", "affect_slow_delta_cap", "affect_inject_chars",
            "affect_baseline"]
    for k in keys:
        assert k in raw, "缺少配置键 " + k
    assert raw["affect_enabled"]["default"] is False, "默认必须关闭（不改变现有行为）"
    assert int(raw["affect_decay_half_life_minutes"]["default"]) == 120
    assert int(raw["affect_reflect_poignancy"]["default"]) == 150
    print("[A10] 11 个 affect_* 配置键齐全且默认关闭 OK")


async def a11_json_objects():
    got = mod._json_objects(json.dumps({"delta": {"p": -0.3}, "poignancy": 12},
                                       ensure_ascii=False))
    assert got == [{"delta": {"p": -0.3}, "poignancy": 12}], got
    fence = "`" * 3
    arr = mod._json_objects(fence + "json" + chr(10) + 
                              '[{"time":"07:05","point":"a"}]' + chr(10) + fence)
    assert arr == [{"time": "07:05", "point": "a"}], arr
    mid = mod._json_objects('前缀 {"delta":{"p":0.2}} 后缀')
    assert mid == [{"delta": {"p": 0.2}}], mid
    print("[A11] _json_objects 支持单个顶层对象/代码围栏/嵌套对象 OK")


async def main():
    for fn in [a1_decay_half_life, a2_disabled, a3_fast_channel, a4_slow_reflect,
               a5_silence_channel, a6_silence_decay_target, a7_describe, a8_persist,
               a9_wiring, a10_schema, a11_json_objects]:
        await fn()
    print("阶段三（情感状态）测试全部通过")


if __name__ == "__main__":
    asyncio.run(main())
