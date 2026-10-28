"""阶段二（长期记忆 mem0 封装层）专属测试。

不依赖真实 mem0/fastembed/qdrant：向 sys.modules 注入假 mem0 模组，
只验证封装层的契约（懒加载单例、filters 用法、阈值、召回块、种子幂等、
缓冲写入、失败降级、close）。
运行：py tests/test_memory.py
"""
import asyncio
import importlib.util
import inspect  # noqa: F401
import io
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "stubs"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("suite_memory", os.path.join(ROOT, "main.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeVectorStore:
    def __init__(self):
        self.client = FakeClient()


class FakeMemory:
    instances = []
    fail = False

    def __init__(self):
        self.calls = []
        self.vector_store = FakeVectorStore()
        self.search_hits = []
        self.all_items = []
        FakeMemory.instances.append(self)

    @classmethod
    def from_config(cls, cfg):
        if FakeMemory.fail:
            raise RuntimeError("boom: no llm")
        inst = cls()
        inst.cfg = cfg
        return inst

    def search(self, query, **kw):
        self.calls.append(("search", query, kw))
        return {"results": list(self.search_hits)}

    def add(self, messages, **kw):
        self.calls.append(("add", messages, kw))
        return {"results": []}

    def get_all(self, **kw):
        self.calls.append(("get_all", kw))
        return {"results": list(self.all_items)}


def install_fake_mem0():
    fake = types.ModuleType("mem0")
    fake.Memory = FakeMemory
    sys.modules["mem0"] = fake


class FakeOwner:
    def __init__(self, cfg):
        self.config = cfg
        self.kv = {}

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.kv[key] = value


BASE = {
    "mem0_enabled": True,
    "mem0_llm_model": "mimo-v2.6-pro",
    "mem0_llm_base_url": "https://token-plan-cn.xiaomimimo.com/anthropic",
    "mem0_llm_api_key": "sk-test",
    "mem0_embed_model": "BAAI/bge-small-zh-v1.5",
    "mem0_top_k": 5,
    "mem0_threshold_pct": 45,
    "mem0_seed_facts": True,
    "mem0_flush_turns": 2,
    "mem0_recall_chars": 400,
    "mem0_infer": False,
}


def make_engine(**overrides):
    cfg = dict(BASE)
    cfg.update(overrides)
    FakeMemory.instances = []
    FakeMemory.fail = False
    install_fake_mem0()
    return mod.LivingMemoryCore(FakeOwner(cfg))


def ev(umo="aiocqhttp:Friend:10001", text="我今天买了布丁"):
    return types.SimpleNamespace(unified_msg_origin=umo, message_str=text)


def resp(text="嗯，布丁好吃吗"):
    return types.SimpleNamespace(completion_text=text)


async def flush_tasks(engine):
    for _ in range(12):
        await asyncio.sleep(0.01)
    return len(engine._tasks)


async def m1_disabled():
    e = make_engine(mem0_enabled=False)
    assert await e.search("布丁", "u1") == []
    assert await e.recall_block("布丁", "u1") == ""
    assert "mem0" not in sys.modules or e._mem is None
    assert e.status()["loaded"] is False
    print("[M1] 关闭时不加载、不召回 OK")


async def m2_singleton():
    e = make_engine()
    await e.search("a", "u1")
    await e.search("b", "u1")
    assert len(FakeMemory.instances) == 1, len(FakeMemory.instances)
    assert e.status()["loaded"] is True
    print("[M2] 懒加载且进程内单例（from_config 只一次） OK")


async def m3_search_contract():
    e = make_engine(mem0_top_k=4, mem0_threshold_pct=50)
    mem = await e._ensure()
    mem.search_hits = [{"memory": "对方养了一只橘猫叫布丁", "score": 0.6}, {"memory": "  "}]
    hits = await e.search("布丁是谁", "aiocqhttp:Friend:1")
    assert len(hits) == 1 and "布丁" in hits[0]["memory"]
    kind, query, kw = mem.calls[-1]
    assert kind == "search" and query == "布丁是谁"
    assert kw.get("filters") == {"user_id": "u_aiocqhttp_Friend_1"}, kw
    assert "user_id" not in kw, "顶层 user_id 会被 mem0 拒绝"
    assert kw.get("top_k") == 4
    assert abs(float(kw.get("threshold")) - 0.5) < 1e-9, kw
    print("[M3] search 用 filters/阈值/top_k，且不传顶层 user_id OK")


async def m4_recall_block():
    e = make_engine()
    mem = await e._ensure()
    assert await e.recall_block("x", "u1") == ""
    mem.search_hits = [{"memory": "对方养了一只橘猫叫布丁"}]
    block = await e.recall_block("布丁是谁", "u1")
    assert "【你记得的事】" in block and "布丁" in block
    print("[M4] 召回块：无命中返回空串、有命中带标题 OK")


async def m5_seed_idempotent():
    e = make_engine()
    orig = mod.load_worldview_asset
    mod.load_worldview_asset = lambda: {"facts": [
        {"id": "f1", "fact": "魔法协会总部在旧都", "importance": 0.8},
        {"id": "f2", "fact": "这世界没有互联网", "importance": 0.6},
    ]}
    try:
        mem = await e._ensure()
        mem.all_items = [{"memory": "魔法协会总部在旧都"}]
        added = await e.seed_worldview()
        assert added == 1, added
        add_calls = [c for c in mem.calls if c[0] == "add"]
        assert add_calls and add_calls[0][2].get("user_id") == "elaina_world"
        assert add_calls[0][2].get("infer") is False
        ga = [c for c in mem.calls if c[0] == "get_all"]
        assert ga and int(ga[0][1].get("top_k") or 0) >= 1000, ga
        assert await e.seed_worldview() == 0, "第二次不应重复写"
        print("[M5] 世界观点种子幂等（已存在的跳过、infer=False） OK")
    finally:
        mod.load_worldview_asset = orig


async def m10_seed_sees_all_beyond_default_limit():
    """mem0 v2 get_all 默认 top_k=20；34 条种子必须一次看全，否则重复写。"""
    e = make_engine()
    orig = mod.load_worldview_asset
    facts = [{"id": "f%d" % i, "fact": "事实%d" % i, "importance": 0.5} for i in range(34)]
    mod.load_worldview_asset = lambda: {"facts": facts}
    try:
        mem = await e._ensure()
        mem.all_items = [{"memory": "事实%d" % i} for i in range(34)]
        added = await e.seed_worldview()
        assert added == 0, added
        ga = [c for c in mem.calls if c[0] == "get_all"]
        assert int(ga[0][1].get("top_k") or 0) >= 1000, ga
        assert not [c for c in mem.calls if c[0] == "add"], "全部已存在时不应再写"
        print("[M10] get_all 显式 top_k=1000（34 条种子不被 20 条截断） OK")
    finally:
        mod.load_worldview_asset = orig


async def m6_buffered_write():
    e = make_engine(mem0_flush_turns=2)
    await e.on_user_message(ev(text="第一句"))
    await e.on_llm_response(ev(), resp("第一回"))
    mem = await e._ensure()
    assert not [c for c in mem.calls if c[0] == "add"], "不足轮数不应写入"
    await e.on_user_message(ev(text="第二句"))
    await e.on_llm_response(ev(), resp("第二回"))
    await flush_tasks(e)
    add_calls = [c for c in mem.calls if c[0] == "add"]
    assert len(add_calls) == 1, add_calls
    msgs, kw = add_calls[0][1], add_calls[0][2]
    assert len(msgs) == 4 and [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert str(kw.get("user_id")).startswith("u_")
    assert kw.get("infer") is False
    print("[M6] 攒够 2 轮后台批量写入（infer=False，按会话 user_id） OK")


async def m7_failure_degrade():
    e = make_engine()
    FakeMemory.fail = True
    assert await e.search("a", "u1") == []
    assert await e.recall_block("a", "u1") == ""
    assert e._init_failed is True and e._mem is None
    FakeMemory.fail = False
    assert await e.search("a", "u1") == [], "失败后本次运行不再重试"
    assert e.status()["init_failed"] is True
    print("[M7] 初始化失败降级为空且不反复重试 OK")


async def m8_close():
    e = make_engine()
    mem = await e._ensure()
    await e.close()
    assert mem.vector_store.client.closed is True
    assert e._mem is None and e.status()["loaded"] is False
    print("[M8] close() 关闭 qdrant client 并释放单例 OK")


async def m9_wiring():
    src = io.open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert "recall_block" in src, "主动消息生成链必须接入长期记忆召回"
    assert '_living_memory_block(umo)' in src, "compose 必须把会话带进记忆块"
    assert "recent_text" in src, "召回查询要用最近用户发言"
    plug = src
    assert "LivingMemoryCore(self)" in plug
    assert "self.living_memory.initialize()" in plug
    assert "self.living_memory.close()" in plug
    assert "self.living_memory.on_llm_response" in plug
    assert "记忆状态" in plug
    print("[M9] 插件接线完整（构造/初始化/收尾/写入钩子/状态命令/召回注入） OK")


async def main():
    for fn in [m1_disabled, m2_singleton, m3_search_contract, m4_recall_block,
               m5_seed_idempotent, m6_buffered_write, m7_failure_degrade, m8_close, m9_wiring,
               m10_seed_sees_all_beyond_default_limit]:
        await fn()
    print("阶段二（long-term memory）测试全部通过")


if __name__ == "__main__":
    asyncio.run(main())
