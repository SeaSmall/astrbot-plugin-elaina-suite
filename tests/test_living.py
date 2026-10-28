"""阶段一（living 主动消息 v2）专属测试。

不依赖 astrbot / apscheduler 真实环境：apscheduler 用假模块注入，
发送器、LLM、目标会话、记忆全部打桩，状态文件指向临时目录。
运行：py tests/test_living.py
"""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "stubs"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("suite_living", os.path.join(ROOT, "main.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeTrigger:
    def __init__(self, run_date=None):
        self.run_date = run_date

    @classmethod
    def from_crontab(cls, expr, timezone=None):
        return cls(run_date=expr)


class FakeScheduler:
    def __init__(self, timezone=None):
        self.timezone = timezone
        self.running = False
        self.jobs = {}
        self.history = []

    def start(self):
        self.running = True

    def add_job(self, func, trigger, args=None, id=None, replace_existing=False,
                coalesce=True, max_instances=1, misfire_grace_time=None, **kw):
        rec = {
            "func": func, "trigger": trigger, "args": list(args or []), "id": id,
            "replace_existing": replace_existing, "coalesce": coalesce,
            "max_instances": max_instances, "misfire_grace_time": misfire_grace_time,
        }
        self.history.append(rec)
        self.jobs[id] = rec
        return rec

    def remove_all_jobs(self):
        self.jobs.clear()

    def shutdown(self, wait=False):
        self.running = False


def install_fake_apscheduler():
    for name in ("apscheduler", "apscheduler.schedulers", "apscheduler.triggers"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sched_mod = types.ModuleType("apscheduler.schedulers.asyncio")
    sched_mod.AsyncIOScheduler = FakeScheduler
    trig_date = types.ModuleType("apscheduler.triggers.date")
    trig_date.DateTrigger = FakeTrigger
    trig_cron = types.ModuleType("apscheduler.triggers.cron")
    trig_cron.CronTrigger = FakeTrigger
    sys.modules["apscheduler.schedulers.asyncio"] = sched_mod
    sys.modules["apscheduler.triggers.date"] = trig_date
    sys.modules["apscheduler.triggers.cron"] = trig_cron


install_fake_apscheduler()


class FakeCronManager:
    def __init__(self):
        self.jobs = {}
        self.added = []

    async def add_basic_job(self, name=None, cron_expression=None, handler=None,
                            persistent=False, **kw):
        jid = f"job-{len(self.jobs) + 1}"
        self.jobs[jid] = {"name": name, "cron": cron_expression, "handler": handler}
        self.added.append(self.jobs[jid])
        return jid

    async def list_jobs(self):
        return []

    async def delete_job(self, jid):
        self.jobs.pop(jid, None)


class FakeContext:
    def __init__(self):
        self.cron_manager = FakeCronManager()
        self.send_message = self._send
        self.sent = []

    async def _send(self, *a, **k):
        self.sent.append((a, k))
        return True


class FakeOwner:
    def __init__(self, cfg):
        self.config = cfg
        self.context = FakeContext()
        self.kv = {}

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.kv[key] = value


BASE_CFG = {
    "pg_enabled": True,
    "block_proactive": False,
    "pause_active_agent_jobs": False,
    "living_enabled": True,
    "living_min_silence_minutes": 90,
    "living_cooldown_minutes": 180,
    "living_daily_max": 6,
    "living_quiet_hours": "off",
    "living_offset_min": 5,
    "living_offset_max": 45,
    "living_ramp_minutes": 240,
    "living_decision_check_minutes": 10,
    "living_persona_chars": 2400,
    "living_worldview_chars": 1200,
    "pg_timezone": "Asia/Shanghai",
    "memory_enabled": False,
    "gen_time": "20 3 * * *",
    "memory_cron": "0 3 * * *",
    "msg_count_min": 5,
    "msg_count_max": 10,
    "missed_grace_minutes": 30,
    "only_private": True,
}

UMO = "test:Friend:1001"


def make_core(**overrides):
    cfg = dict(BASE_CFG)
    cfg.update(overrides)
    owner = FakeOwner(cfg)
    core = mod.ProactiveGuardCore(owner)
    tmp = Path(tempfile.mkdtemp(prefix="living_test_"))
    core._living_state_file = (lambda: tmp / "proactive_state.json")
    core.sent = []
    core.prompts = []

    async def _sessions():
        return [UMO]

    async def _memory():
        return ""

    async def _send_silent(text, targets=None):
        core.sent.append((text, list(targets or [])))

    async def _llm(prompt):
        core.prompts.append(prompt)
        if "判定标准" in prompt:
            return '{"pass": true, "reason": "ok", "rewrite": ""}'
        return "今天的风把帽子吹跑了，捡回来的时候上面沾了片叶子。"

    core._collect_target_sessions = _sessions
    core._collect_memory = _memory
    core._send_silent = _send_silent
    core._llm_chat = _llm
    return core


def run(fn):
    asyncio.run(fn())


# ---------------------------------------------------------------- 结构约束
async def t1_living_never_uses_cron_manager():
    """living 开启：绝不注册 AstrBot cron_manager（不得依赖内置任务系统）。"""
    core = make_core()

    async def boom():
        raise AssertionError("living 开启却调用了旧 _setup_scheduler")

    core._setup_scheduler = boom
    await core.initialize()
    assert isinstance(core._living_sched, FakeScheduler)
    assert core._living_sched.running is True
    assert core.context.cron_manager.added == [], core.context.cron_manager.added
    ids = [j["id"] for j in core._living_sched.history]
    assert ids and all(i.startswith("proactive_decision_") for i in ids), ids
    await core.terminate()
    assert core._living_sched is None
    print("[L1] living 开启：零 cron_manager 注册 + 内部 scheduler 启动 OK")


async def t2_disabled_keeps_old_path():
    core = make_core(living_enabled=False)

    async def boom():
        raise AssertionError("living 关闭却启动了内部调度器")

    core._living_start = boom
    await core.initialize()
    assert core.context.cron_manager.added, "旧路径应注册 cron_manager job"
    assert core._living_sched is None
    await core.terminate()
    print("[L2] living 关闭：走旧 cron_manager 路径，内部调度器未启动 OK")


async def t3_job_flags_and_single_slot():
    core = make_core()
    await core.initialize()
    hist = core._living_sched.history
    assert hist
    for j in hist:
        assert j["replace_existing"] is True, j
        assert j["coalesce"] is True, j
        assert j["max_instances"] == 1, j
        assert j["misfire_grace_time"] == 120, j
        assert j["id"].startswith(("proactive_decision_", "proactive_fire_", "living_memory")), j
    dec = [j for j in hist if j["id"].startswith("proactive_decision_")]
    assert len({j["id"] for j in dec}) == 1, dec
    await core.terminate()
    print("[L3] job 幂等参数（replace_existing/coalesce/max_instances/misfire）+ 单槽 OK")


# ------------------------------------------------------------ 两阶段/幂等
async def _seed_pending(core, token, fire_at, created=None, last_user_at=None):
    state = await core._living_load()
    s = core._living_sstate(state, UMO)
    now = time.time()
    s["last_user_at"] = now - 7200 if last_user_at is None else last_user_at
    s["pending"] = {
        "token": token, "fire_at": fire_at, "intent": "share",
        "label": "分享小事", "reason": "测试", "created": now if created is None else created,
    }
    await core._living_save(state)
    return state


async def t4_token_idempotent():
    core = make_core()
    await core.initialize()
    await _seed_pending(core, "tok-1", time.time() + 60)
    await core._living_fire_tick(UMO, "tok-1")
    await core._living_fire_tick(UMO, "tok-1")
    await core._living_fire_tick(UMO, "tok-1")
    await core._living_fire_tick(UMO, "tok-other")
    assert len(core.sent) == 1, core.sent
    await core.terminate()
    print("[L4] 同一 token 重复触发只发 1 条（跨重启幂等）OK")


async def t5_stale_pending_no_catchup():
    """过期待触发只丢弃、绝不补发（这是旧实现「一大串」的根因）。"""
    core = make_core()
    await core.initialize()
    now = time.time()
    await _seed_pending(core, "old", now - 3600, created=now - 7200)
    await core._living_decision_tick(UMO)
    assert not core.sent, core.sent
    state = await core._living_load()
    hist = state["sessions"][UMO]["history"]
    assert any("丢弃" in h["detail"] for h in hist), hist
    await core.terminate()
    print("[L5] 过期待触发丢弃、不补发 OK")


async def t6_hundred_duplicate_fires():
    core = make_core()
    await core.initialize()
    await _seed_pending(core, "dup", time.time() + 60)
    for _ in range(100):
        await core._living_fire_tick(UMO, "dup")
    assert len(core.sent) == 1, len(core.sent)
    await core.terminate()
    print("[L6] 连续 100 次重复触发 → 消息数严格 = 1 OK")


async def t7_hundred_cycles_exact_count():
    """100 个决策→触发周期：每个周期正好 1 条，不多不少。"""
    core = make_core(
        living_min_silence_minutes=1,
        living_cooldown_minutes=1,
        living_daily_max=10000,
        living_ramp_minutes=1,
        living_offset_min=1,
        living_offset_max=1,
        living_quiet_hours="off",
    )
    await core.initialize()
    base = time.time()
    for i in range(100):
        state = await core._living_load()
        s = core._living_sstate(state, UMO)
        s["last_user_at"] = base - 7200
        s["last_proactive_at"] = 0
        s["sent_date"] = core._living_now().strftime("%Y-%m-%d")
        s["sent_today"] = 0
        s["pending"] = None
        await core._living_save(state)
        await core._living_decision_tick(UMO)
        state = await core._living_load()
        pend = state["sessions"][UMO].get("pending")
        assert isinstance(pend, dict), f"第 {i} 轮没有产生待触发"
        await core._living_fire_tick(UMO, pend["token"])
        assert len(core.sent) == i + 1, f"第 {i} 轮后发送数 {len(core.sent)} != {i + 1}"
    assert len(core.sent) == 100
    await core.terminate()
    print("[L7] 100 个周期 → 发送数严格 = 100 OK")


# ------------------------------------------------------- 时间逻辑与安静时段
async def t8_user_message_cancels_pending():
    core = make_core()
    await core.initialize()
    await _seed_pending(core, "t", time.time() + 300)
    await core._living_on_user_message(UMO)
    state = await core._living_load()
    s = state["sessions"][UMO]
    assert s["pending"] is None
    assert s["next_decision_at"] >= time.time() + 3600
    assert any(h["kind"] == "cancel" for h in s["history"])
    await core.terminate()
    print("[L8] 用户回话：取消待触发 + 下次决策推到沉默下限之后 OK")


async def t9_quiet_hours_skip_and_defer():
    core = make_core(living_quiet_hours="23:30-07:30")
    await core.initialize()
    now = core._living_now()
    night = now.replace(hour=2, minute=0, second=0, microsecond=0)
    noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
    assert core._living_in_quiet(night) is True
    assert core._living_in_quiet(noon) is False
    end = core._living_quiet_end_after(night)
    assert (end.hour, end.minute) == (7, 30), end
    assert end > night

    core._living_now = lambda: night
    await _seed_pending(core, "q", night.timestamp() + 60, created=night.timestamp() - 600)
    await core._living_fire_tick(UMO, "q")
    assert not core.sent, core.sent

    core2 = make_core(living_quiet_hours="23:30-07:30")
    night2 = core2._living_now().replace(hour=23, minute=40)
    state = await core2._living_load()
    s = core2._living_sstate(state, UMO)
    s["last_user_at"] = night2.timestamp() - 7200
    plan = await core2._living_plan_intent(UMO, s, night2)
    assert plan, plan
    fire_dt = core2._living_dt(plan["fire_at"])
    assert not core2._living_in_quiet(fire_dt), fire_dt
    print("[L9] 安静时段：不触发 + 计划自动顺延出时段 OK")


async def t10_state_atomic_and_corrupt_recovery():
    core = make_core()
    path = core._living_state_file()
    await core._living_save({"version": 2, "sessions": {UMO: {"last_user_at": 1.0}}})
    assert path.exists()
    assert not Path(str(path) + ".part").exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["sessions"][UMO]["last_user_at"] == 1.0
    path.write_text("{ 这是坏掉的 json", encoding="utf-8")
    state = await core._living_load()
    assert state["sessions"] == {}
    print("[L10] 状态原子写（无 .part 残留）+ 损坏文件降级为空状态 OK")


# --------------------------------------------------------- 人格/世界观注入
async def t11_prompt_blocks_and_hard_filters():
    core = make_core()
    await core.initialize()
    state = await core._living_load()
    s = core._living_sstate(state, UMO)
    s["last_user_at"] = time.time() - 5400
    text = await core._living_compose(UMO, s, "share", "测试由头", core._living_now())
    assert text, "compose 返回空"
    gen_prompts = [p for p in core.prompts if "判定标准" not in p]
    prompt = gen_prompts[0]
    assert "【人格】" in prompt and "伊蕾娜" in prompt
    assert "【世界观" in prompt
    assert "【傲娇怎么表达】" in prompt and "三条可用路径" in prompt
    assert "测试由头" in prompt
    assert any("判定标准" in p for p in core.prompts), "阶段五审校应被调用"
    assert "【记忆】" not in prompt or "关于对方" in prompt
    for bad in ("才不是特意", "我才没有", "又不是为了你", "别误会"):
        assert core._living_check_text(f"{bad}关心你") == ""
    assert core._living_check_text("作为AI我不能这样做") == ""
    assert core._living_check_text("语言模型没有身体") == ""
    assert core._living_check_text("今天的风有点大。") == "今天的风有点大。"
    await core.terminate()
    print("[L11] 人格/世界观/傲娇机制注入 + 元话语式否定与 AI 自称硬过滤 OK")


async def t12_timing_words():
    core = make_core()
    now = core._living_now()
    assert core._living_period(now.replace(hour=2)) == "深夜"
    assert core._living_period(now.replace(hour=6)) == "清晨"
    assert core._living_period(now.replace(hour=12)) == "中午"
    assert core._living_period(now.replace(hour=15)) == "下午"
    assert core._living_period(now.replace(hour=20)) == "晚上"
    assert core._living_human_gap(30) == "30 分钟"
    assert core._living_human_gap(150) == "2 小时"
    assert core._living_human_gap(3000) == "2 天"
    assert core._living_quiet_range() is None
    print("[L12] 时段词 / 人话时长 / quiet=off 解析 OK")



async def t13_skip_non_target_session():
    """非目标会话（如语音通道）不排主动消息：避免往没人收的会话白发。"""
    core = make_core()
    await core.initialize()
    other = "webchat_phone!alice!main"
    await core._living_decide(other)
    state = await core._living_load()
    assert other not in (state.get("sessions") or {}), state.get("sessions")
    assert not core.sent, core.sent
    await core.terminate()
    print("[L13] 非目标会话不排主动消息、不写状态 OK")

def main():
    run(t1_living_never_uses_cron_manager)
    run(t2_disabled_keeps_old_path)
    run(t3_job_flags_and_single_slot)
    run(t4_token_idempotent)
    run(t5_stale_pending_no_catchup)
    run(t6_hundred_duplicate_fires)
    run(t7_hundred_cycles_exact_count)
    run(t8_user_message_cancels_pending)
    run(t9_quiet_hours_skip_and_defer)
    run(t10_state_atomic_and_corrupt_recovery)
    run(t11_prompt_blocks_and_hard_filters)
    run(t12_timing_words)
    run(t13_skip_non_target_session)
    print("阶段一（living）测试全部通过")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("FAILED:", e)
        raise
