"""阶段一（情感全局化）专属测试：全局单例 / 迁移 / 主要对象沉默基准。

运行：py tests/test_global.py
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
spec = importlib.util.spec_from_file_location("suite_global", os.path.join(ROOT, "main.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

TMP = tempfile.mkdtemp(prefix="global_affect_test_")
mod._plugin_data_dir = lambda: TMP
GLOBAL = os.path.join(TMP, "global_affect.json")
LEGACY = os.path.join(TMP, "affect_state.json")

QQ = "default_10001:FriendMessage:openid_test"
PHONE = "webchat_phone!testuser!main"


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


BASE_P = 0.15
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
    "affect_primary_user_id": "",
}


def clean():
    for p in (GLOBAL, GLOBAL + ".part", LEGACY, LEGACY + ".migrated"):
        if os.path.exists(p):
            os.remove(p)


def make(**overrides):
    clean()
    cfg = dict(BASE)
    cfg.update(overrides)
    return mod.AffectCore(FakeOwner(cfg))


async def g1_global_file():
    core = make()
    await core.fast_update(QQ, "你真是太厉害了，好棒，太强了", "哼")
    assert os.path.exists(GLOBAL), "全局状态文件未落盘"
    raw = json.load(io.open(GLOBAL, encoding="utf-8"))
    assert "sessions" not in raw, "文件里不能再有 sessions 分区"
    for k in ("pad", "poignancy", "events", "silence_sev", "last_user_at", "version"):
        assert k in raw, "缺字段 " + k
    assert int(raw["version"]) == mod.AFFECT_STATE_VERSION
    print("[G1] 状态文件 = global_affect.json，结构无 sessions 分区 OK")


async def g2_cross_session_and_restart():
    core = make()
    await core.fast_update(QQ, "你真是太厉害了，好棒，太强了", "哼")
    a = dict(core.get_state(QQ)["pad"])
    b = dict(core.get_state(PHONE)["pad"])
    # 每次读都会按真实 dt 补算衰减，两次读之间有几微秒差异属正常
    for k in ("p", "a", "d"):
        assert abs(a[k] - b[k]) < 1e-3, (a, b)
    assert a["p"] > BASE_P + 0.1, a
    core2 = mod.AffectCore(FakeOwner(dict(BASE)))
    got = core2.get_state(PHONE)["pad"]
    assert abs(got["p"] - a["p"]) < 1e-3, (got, a)
    print("[G2] QQ↔语音同一份 PAD；重启后新实例读回同一状态 OK")


async def g3_migrate_preferred_shared():
    now = time.time()
    slow = {"pad": {"p": 0.1, "a": 0.1, "d": 0.1}, "updated": now, "poignancy": 5.0,
            "events": [{"note": "旧A"}], "silence_sev": 0.0, "last_user_at": now, "umo": QQ}
    fast = {"pad": {"p": -0.4, "a": 0.3, "d": 0.2}, "updated": now - 60, "poignancy": 30.0,
            "events": [{"note": "旧B"}], "silence_sev": 0.0, "last_user_at": now - 60,
            "umo": PHONE}
    clean()
    io.open(LEGACY, "w", encoding="utf-8").write(json.dumps(
        {"version": 1, "sessions": {"shared_testuser": slow, "other": fast}},
        ensure_ascii=False))
    cfg = dict(BASE)
    cfg.update({"shared_scope_enabled": True, "shared_scope_id": "testuser"})
    core = mod.AffectCore(FakeOwner(cfg))
    st = core.get_state("")
    assert abs(st["pad"]["p"] - 0.1) < 1e-6, "优先取 shared_<id> 那条（而非时间最新的）"
    assert os.path.exists(LEGACY + ".migrated"), "旧文件必须改名留档"
    assert not os.path.exists(LEGACY), "旧文件不应原地保留"
    await core._save()
    assert os.path.exists(GLOBAL), "迁移后应落盘新文件"
    print("[G3] 旧分区状态迁移：优先 shared_<id>，旧文件改名 .migrated OK")


async def g4_migrate_newest_fallback():
    now = time.time()
    old = {"pad": {"p": 0.2, "a": 0.2, "d": 0.2}, "updated": now - 120, "events": [],
           "last_user_at": now - 120}
    new = {"pad": {"p": -0.6, "a": 0.1, "d": 0.1}, "updated": now, "events": [],
           "last_user_at": now}
    clean()
    io.open(LEGACY, "w", encoding="utf-8").write(json.dumps(
        {"version": 1, "sessions": {"a": old, "b": new}}, ensure_ascii=False))
    core = mod.AffectCore(FakeOwner(dict(BASE)))
    st = core.get_state("")
    assert abs(st["pad"]["p"] + 0.6) < 1e-6, "没有 shared_<id> 时应取 updated 最大的那条"
    print("[G4] 无共享作用域时迁移取「最近更新值」 OK")


async def g5_primary_user_silence_basis():
    core = make(affect_primary_user_id="10001")
    st = core._load()
    now = time.time()
    st["last_user_at"] = now - 3600
    st["last_any_at"] = now - 3600
    st["silence_sev"] = 0.5
    st["updated"] = now
    core.note_user(PHONE, user_id="20002")
    st = core._load()
    assert abs(float(st["last_user_at"]) - (now - 3600)) < 5, "非主要对象不得重置沉默计时"
    assert float(st["last_any_at"]) > now - 5, "但应更新「有人来过」时间"
    assert abs(float(st["silence_sev"]) - 0.5) < 1e-6, "沉默严重度不得被非主要对象清零"
    core.note_user(PHONE, user_id="10001")
    st = core._load()
    assert float(st["last_user_at"]) > now - 5, "主要对象出现必须重置沉默计时"
    assert float(st["silence_sev"]) == 0.0
    core2 = make()  # 未配置主要对象 = 方案二
    st2 = core2._load()
    st2["last_user_at"] = now - 3600
    core2.note_user(QQ)
    assert float(core2._load()["last_user_at"]) > now - 5, "方案二下任意互动都算在场"
    # 多标识：同一个人跨渠道（QQ openid + webchat 用户名）都要能算主要对象
    core3 = make(affect_primary_user_id="openid_test, testuser")
    assert core3._is_primary(QQ) is True, "QQ umo 里的 openid 应命中"
    assert core3._is_primary(PHONE, user_id="testuser") is True, "webchat 用户名应命中"
    assert core3._is_primary("webchat_phone!other!main", user_id="other") is False
    st3 = core3._load()
    st3["last_user_at"] = now - 3600
    st3["silence_sev"] = 0.7
    core3.note_user("webchat_phone!stranger!main", user_id="stranger")
    st3 = core3._load()
    assert abs(float(st3["last_user_at"]) - (now - 3600)) < 5, "多标识下陌生人不得重置计时"
    assert abs(float(st3["silence_sev"]) - 0.7) < 1e-6
    core3.note_user(PHONE, user_id="testuser")
    assert float(core3._load()["last_user_at"]) > now - 5, "多标识下主要对象出现必须重置计时"
    print("[G5] 沉默基准方案一：只有主要对象能重置计时（支持多标识跨渠道）；未配置时退化为方案二 OK")


async def g6_silence_check_global():
    core = make(affect_silence_threshold_minutes=60)
    st = core._load()
    now = time.time()
    st["pad"] = {"p": 0.3, "a": 0.1, "d": 0.2}
    st["updated"] = now
    st["last_user_at"] = now - 300 * 60
    st["silence_sev"] = 0.0
    r = await core.silence_check()
    assert r == {"checked": 1, "injected": 1}, r
    got = core.get_state("")["pad"]
    assert got["p"] < 0.3, got
    r2 = await core.silence_check()
    assert r2["injected"] == 0, "冷却内不得重复注入"
    kinds = [e.get("kind") for e in core._load().get("events") or []]
    assert "silence" in kinds, kinds
    print("[G6] 沉默评估是全局单次评估（checked=1），注入负面事件且冷却生效 OK")


async def g7_status_and_callable_without_umo():
    core = make(affect_primary_user_id="10001")
    st = core.status()
    assert "sessions" not in st, "status 不能再按会话返回"
    for k in ("pad", "poignancy", "silence_sev", "events", "primary_user_id", "file"):
        assert k in st, "status 缺字段 " + k
    assert st["primary_user_id"] == "10001"
    assert st["file"].endswith("global_affect.json"), st["file"]
    assert isinstance(core.get_state(), dict) and core.describe() != ""
    assert core.silence_severity() >= 0.0
    print("[G7] status 返回全局单对象；get_state/describe/silence_severity 可无参调用 OK")


async def g8_no_residue():
    src = io.open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    body = src.split("class AffectCore(")[1].split("# 核心九")[0]
    assert "def _sid(" not in body, "AffectCore 不应再有 _sid 分区函数"
    assert "_locks" not in body, "不应再有按会话的锁字典"
    assert '"global_affect.json"' in body, "状态文件必须是 global_affect.json"
    assert src.count('"affect_state.json"') == 1, "affect_state.json 只应出现在旧文件迁移路径"
    assert "user_id=sender_id" in src, "消息钩子必须把发送者 id 传给 note_user"
    raw = json.load(io.open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8-sig"))
    assert "affect_primary_user_id" in raw, "缺 affect_primary_user_id 配置"
    mig = body.split("def _migrate_legacy")[1].split("def _load")[0]
    # 迁移逻辑之外只允许 _load 里那句「新路径文件但内容是旧结构」的检测
    assert body.count('"sessions"') - mig.count('"sessions"') == 1, "sessions 只允许出现在迁移/检测处"
    print("[G8] 代码无分区残留；affect_primary_user_id 已在 schema；钩子传 sender_id OK")


async def main():
    for fn in [g1_global_file, g2_cross_session_and_restart, g3_migrate_preferred_shared,
               g4_migrate_newest_fallback, g5_primary_user_silence_basis,
               g6_silence_check_global, g7_status_and_callable_without_umo, g8_no_residue]:
        await fn()
    print("阶段一（情感全局化）测试全部通过")


if __name__ == "__main__":
    asyncio.run(main())
