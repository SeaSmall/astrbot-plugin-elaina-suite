"""睡眠时段豁免（v1.11.0）专属测试。

为什么需要这一层：情感衰减与「多久没聊了」原本都按真实经过时间算，
于是每天早上一起来，插件就认为「已经 7~8 小时没找我了」，主动消息与
沉默评估直接顶满。人是要睡觉的，睡眠时段必须从沉默里扣掉。

覆盖：
  1. `_parse_sleep_range` / `_sleep_minutes_between` / `_awake_minutes_between`
     三个纯函数（跨午夜、跨多天、夏令时、off）；
  2. AffectCore：衰减 dt 与 silence_severity 都按清醒分钟；
  3. ProactiveGuardCore：`_living_awake_gap_min` 与 `_living_compose` 的
     睡眠说明、由头缺失时拒绝生成；
  4. 配置键齐全且默认开启。
运行：py tests/test_sleep_aware.py
"""
import asyncio
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("suite_sleep", os.path.join(ROOT, "main.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

TMP = tempfile.mkdtemp(prefix="sleep_test_")
mod._plugin_data_dir = lambda: TMP
TZ = ZoneInfo("Asia/Shanghai")
UMO = "test:Friend:1001"
RANGE = (23 * 60 + 30, 7 * 60 + 30)  # 23:30-07:30


def ts(*args) -> float:
    """按 Asia/Shanghai 本地时间构造时间戳。"""
    return datetime(*args, tzinfo=TZ).timestamp()


# --------------------------------------------------------------- 假环境
class FakeGuard:
    def __init__(self):
        self.prompts = []

    def _living_tz(self):
        return TZ

    async def _llm_chat(self, prompt):
        self.prompts.append(prompt)
        return "哼，睡醒了就来看看你还在不在。"


class FakeOwner:
    def __init__(self, cfg):
        self.config = cfg
        self.kv = {}
        self.proactive_guard = FakeGuard()

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.kv[key] = value


BASE_AFFECT = {
    "affect_enabled": True,
    "affect_fast_mode": "heuristic",
    "affect_decay_half_life_minutes": 120,
    "affect_silence_threshold_minutes": 240,
    "affect_silence_check_minutes": 15,
    "affect_silence_cooldown_minutes": 120,
    "affect_silence_target": "-0.45,-0.10,-0.20",
    "affect_reflect_poignancy": 150,
    "affect_slow_delta_cap": 0.6,
    "affect_inject_chars": 600,
    "affect_baseline": "",
    "pg_timezone": "Asia/Shanghai",
}

BASE_LIVING = {
    "pg_enabled": True,
    "living_enabled": True,
    "living_min_silence_minutes": 90,
    "living_cooldown_minutes": 180,
    "living_daily_max": 6,
    "living_quiet_hours": "off",
    "living_sleep_hours": "23:30-07:30",
    "living_offset_min": 5,
    "living_offset_max": 45,
    "living_ramp_minutes": 240,
    "living_persona_chars": 2400,
    "living_worldview_chars": 1200,
    "pg_timezone": "Asia/Shanghai",
    "tsundere_review_enabled": False,
}


def clean_affect():
    for name in ("global_affect.json", "affect_state.json"):
        p = os.path.join(TMP, name)
        if os.path.exists(p):
            os.remove(p)
        if os.path.exists(p + ".part"):
            os.remove(p + ".part")


def make_affect(**overrides):
    clean_affect()
    cfg = dict(BASE_AFFECT)
    cfg.update(overrides)
    return mod.AffectCore(FakeOwner(cfg))


def make_guard(**overrides):
    cfg = dict(BASE_LIVING)
    cfg.update(overrides)
    owner = FakeOwner(cfg)
    core = mod.ProactiveGuardCore(owner)
    core.prompts = owner.proactive_guard.prompts

    async def _persona(_umo):
        return "人格块"

    async def _world():
        return "世界观块"

    async def _memory(_umo):
        return "记忆块"

    core._living_persona_block = _persona
    core._living_world_block = _world
    core._living_memory_block = _memory
    core._llm_chat = owner.proactive_guard._llm_chat
    return core


# ------------------------------------------------------------ 纯函数：解析
def t1_parse_range():
    cases = {
        "23:30-07:30": (1410, 450),
        "23:30~07:30": (1410, 450),
        "23:30—07:30": (1410, 450),
        "23:30至07:30": (1410, 450),
        " 7:00-13:00 ": (420, 780),
        "0:00-8:05": (0, 485),
    }
    for raw, want in cases.items():
        got = mod._parse_sleep_range(raw)
        assert got == want, (raw, got, want)
    for raw in ("", "  ", None, "off", "OFF", "none", "0", "false", "no",
                "abc", "25:00-07:30", "23:70-07:30", "23:30-23:30", "23:30"):
        assert mod._parse_sleep_range(raw) is None, (raw, mod._parse_sleep_range(raw))
    print("[S1] 睡眠时段解析：多种分隔符/跨午夜/非法值全部正确 OK")


# ------------------------------------------------ 纯函数：睡眠分钟数计算
def t2_sleep_minutes():
    # 晚上 21:00 到次日 09:00：12 小时里睡了 23:30-07:30 共 8 小时
    t0, t1 = ts(2026, 3, 2, 21, 0), ts(2026, 3, 3, 9, 0)
    assert abs(mod._sleep_minutes_between(t0, t1, RANGE, TZ) - 480.0) < 1e-6
    assert abs(mod._awake_minutes_between(t0, t1, RANGE, TZ) - 240.0) < 1e-6

    # 完全落在睡眠窗口内：00:00 → 03:00
    a, b = ts(2026, 3, 3, 0, 0), ts(2026, 3, 3, 3, 0)
    assert abs(mod._sleep_minutes_between(a, b, RANGE, TZ) - 180.0) < 1e-6
    assert mod._awake_minutes_between(a, b, RANGE, TZ) == 0.0

    # 完全清醒的白天：10:00 → 12:00
    c, d = ts(2026, 3, 3, 10, 0), ts(2026, 3, 3, 12, 0)
    assert mod._sleep_minutes_between(c, d, RANGE, TZ) == 0.0
    assert abs(mod._awake_minutes_between(c, d, RANGE, TZ) - 120.0) < 1e-6

    # 跨两天（48 小时）→ 睡 16 小时
    e, f = ts(2026, 3, 1, 12, 0), ts(2026, 3, 3, 12, 0)
    assert abs(mod._sleep_minutes_between(e, f, RANGE, TZ) - 960.0) < 1e-6
    assert abs(mod._awake_minutes_between(e, f, RANGE, TZ) - 1920.0) < 1e-6

    # 夏令时：纽约 2026-03-08 春季调时，23:30→次日 07:30 实际只过了 7 小时
    ny = ZoneInfo("America/New_York")
    g = datetime(2026, 3, 7, 23, 30, tzinfo=ny).timestamp()
    h = datetime(2026, 3, 8, 7, 30, tzinfo=ny).timestamp()
    got = mod._sleep_minutes_between(g, h, RANGE, ny)
    assert abs(got - 420.0) < 1e-6, got
    # DST 那天只能扣掉真实经过的时间，不能扣出负数或超过窗口
    assert mod._awake_minutes_between(g, h, RANGE, ny) == 0.0

    # off / 未配置：原样返回真实分钟
    x, y = ts(2026, 3, 2, 21, 0), ts(2026, 3, 3, 9, 0)
    assert mod._sleep_minutes_between(x, y, None, TZ) == 0.0
    assert abs(mod._awake_minutes_between(x, y, None, TZ) - 720.0) < 1e-6
    # 反向区间不产生负数
    assert mod._awake_minutes_between(y, x, RANGE, TZ) == 0.0
    print("[S2] 睡眠分钟数：窗口内/外/跨午夜/多天/DST/off 全部正确 OK")


# ------------------------------------------------------- Affect：衰减与沉默
def t3_affect_decay_uses_awake_time():
    now, later = ts(2026, 3, 2, 21, 0), ts(2026, 3, 3, 9, 0)  # 12h 真实、4h 清醒

    core = make_affect()
    st = core.get_state(UMO, now)
    st["pad"] = {"p": 0.8, "a": 0.4, "d": 0.6}
    st["updated"] = now
    st["silence_sev"] = 0.0
    got = core.get_state(UMO, later)["pad"]

    core2 = make_affect(affect_sleep_aware=False)
    st2 = core2.get_state(UMO, now)
    st2["pad"] = {"p": 0.8, "a": 0.4, "d": 0.6}
    st2["updated"] = now
    st2["silence_sev"] = 0.0
    wall = core2.get_state(UMO, later)["pad"]

    k = float(core._silence_target(0.0)["p"])
    ratio = (float(got["p"]) - k) / (float(wall["p"]) - k)
    # 清醒 240 分钟 vs 真实 720 分钟，各按半衰 120 分钟：相差 2^4 = 16 倍
    assert abs(ratio - 16.0) < 1e-6, (ratio, got, wall)
    print("[S3] 情感衰减按清醒分钟折算（12 小时过夜＝衰减 4 小时）OK")


def t4_affect_silence_uses_awake_time():
    now = ts(2026, 3, 3, 9, 0)
    last = ts(2026, 3, 2, 21, 0)  # 12 小时前，但只醒了 4 小时

    core = make_affect()  # 基线 240 分钟
    st = core.get_state(UMO, now)
    st["last_user_at"] = last
    st["silence_sev"] = 0.0
    sev = core.silence_severity(UMO, now)
    assert sev == 0.0, ("4 小时清醒不该算超基线", sev)

    core2 = make_affect(affect_sleep_aware=False)
    st2 = core2.get_state(UMO, now)
    st2["last_user_at"] = last
    st2["silence_sev"] = 0.0
    sev2 = core2.silence_severity(UMO, now)
    assert abs(sev2 - 1.5) < 1e-6, ("不扣睡眠时 12 小时应顶到上限", sev2)
    print("[S4] 沉默严重度按清醒分钟：过夜不再被判成「很久没回话」 OK")


def t5_affect_explicit_off():
    now, last = ts(2026, 3, 3, 9, 0), ts(2026, 3, 2, 21, 0)
    core = make_affect(affect_sleep_hours="off")
    st = core.get_state(UMO, now)
    st["last_user_at"] = last
    st["silence_sev"] = 0.0
    assert abs(core.silence_severity(UMO, now) - 1.5) < 1e-6
    print("[S5] affect_sleep_hours=off 可整侧关闭（行为回到旧版）OK")


# ------------------------------------------- ProactiveGuard：沉默与生成
def t6_living_gap_uses_awake_time():
    core = make_guard()
    now_dt = datetime(2026, 3, 3, 9, 0, tzinfo=TZ)
    s = {"last_user_at": ts(2026, 3, 2, 21, 0), "last_proactive_at": 0}

    assert abs(core._living_awake_gap_min(s, now_dt) - 240.0) < 1e-6
    assert core._living_human_gap(core._living_awake_gap_min(s, now_dt)) == "4 小时"

    off = make_guard(living_sleep_aware=False)
    assert abs(off._living_awake_gap_min(s, now_dt) - 720.0) < 1e-6
    assert off._living_human_gap(off._living_awake_gap_min(s, now_dt)) == "12 小时"

    # 显式 living_sleep_hours 覆盖 quiet_hours
    custom = make_guard(living_sleep_hours="22:00-06:00")
    assert custom._living_sleep_range() == (22 * 60, 6 * 60)
    # 时段留空 → 沿用安静时段；安静时段 off 且时段也留空 → 不扣睡眠
    fallback = make_guard(living_sleep_hours="", living_quiet_hours="23:30-07:30")
    assert fallback._living_sleep_range() == (23 * 60 + 30, 7 * 60 + 30)
    none_core = make_guard(living_sleep_hours="", living_quiet_hours="off")
    assert none_core._living_sleep_range() is None
    print("[S6] 主动消息的「多久没聊」按清醒分钟；可覆盖/可关闭 OK")


async def t7_compose_prompt_explains_sleep():
    core = make_guard()
    now_dt = datetime(2026, 3, 3, 9, 0, tzinfo=TZ)
    s = {"last_user_at": ts(2026, 3, 2, 21, 0), "last_proactive_at": 0}

    text = await core._living_compose(UMO, s, "share", "想说说窗外的雨", now_dt)
    assert text, "有由头的 share 应当生成"
    assert core.prompts, "应当调用 LLM"
    prompt = core.prompts[-1]
    assert "4 小时" in prompt, prompt
    assert "睡眠时间" in prompt and "已经扣除" in prompt, prompt
    assert "8 小时" in prompt, prompt
    # 硬规则：不许自己编「上次那件事」
    assert "不许" in prompt and "编" in prompt, prompt

    # 睡眠豁免关闭时不应出现睡眠说明
    off = make_guard(living_sleep_aware=False)
    await off._living_compose(UMO, s, "share", "想说说窗外的雨", now_dt)
    assert "睡眠时间" not in off.prompts[-1]
    print("[S7] 生成提示词带「睡眠已扣除、别当成冷落」说明，且只在该出现时出现 OK")


# --------------------------------------------------------------- 配置
def t8_schema_keys():
    raw = json.load(io.open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8-sig"))
    for key in ("living_sleep_aware", "living_sleep_hours", "affect_sleep_aware", "affect_sleep_hours"):
        assert key in raw, "缺少配置键 " + key
    for key in ("living_sleep_aware", "affect_sleep_aware"):
        assert raw[key]["default"] is True, key + " 默认应开启"
    for key in ("living_sleep_hours", "affect_sleep_hours"):
        assert raw[key]["default"] == "", key + " 默认留空（沿用安静时段）"
    print("[S8] 4 个睡眠配置键齐全：两个开关默认开、两个时段默认留空 OK")


async def s9_fresh_session_sleep_note():
    """回归：没有任何互动历史（anchor=0）时不能算出荒唐的睡眠时长。

    旧写法拿 `now - max(...)` 而 max(...)=0，等于 `now - 0`（≈1970 年至今），
    会把「已扣除的睡眠」算成几十万天写进提示词。修法：没有锚点时 slept=0。
    """
    core = make_guard()
    s = {"last_user_at": 0, "last_proactive_at": 0}
    now_dt = core._living_now()
    text = await core._living_compose(UMO, s, "share", "沉默期间你去过哪", now_dt)
    assert text
    prompt = core.prompts[-1]
    assert "各自的睡眠时间" not in prompt, ("空状态不该出现睡眠说明", prompt[:300])
    seg = prompt.split("距离上次互动：", 1)[-1].split(chr(10))[0]
    assert "各自的睡眠" not in seg, ("空状态不该有睡眠说明", seg)
    assert "天" not in seg, ("空状态不该算出几十万天", seg)
    print("[S9] 无历史时会话：不会把 1970 年以来的时长当成睡眠 OK")


async def main():
    fns = [
        t1_parse_range, t2_sleep_minutes, t3_affect_decay_uses_awake_time,
        t4_affect_silence_uses_awake_time, t5_affect_explicit_off,
        t6_living_gap_uses_awake_time, t7_compose_prompt_explains_sleep, t8_schema_keys,
        s9_fresh_session_sleep_note,
    ]
    for fn in fns:
        r = fn()
        if asyncio.iscoroutine(r):
            await r
    print("阶段六（睡眠豁免）测试全部通过")


if __name__ == "__main__":
    asyncio.run(main())
