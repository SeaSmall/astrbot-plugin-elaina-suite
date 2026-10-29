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

    def delete_all(self, **kw):
        self.calls.append(("delete_all", {}, kw))
        return {"message": "deleted"}

    def delete(self, memory_id, **kw):
        self.calls.append(("delete", memory_id, kw))
        self.all_items = [i for i in self.all_items if i.get("id") != memory_id]
        return {"message": "deleted"}


def install_fake_mem0():
    fake = types.ModuleType("mem0")
    fake.Memory = FakeMemory
    sys.modules["mem0"] = fake


class FakeOwner:
    def __init__(self, cfg):
        self.config = cfg
        self.kv = {}
        self.context = None

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.kv[key] = value


class FakeProvider:
    class Meta:
        id = "deepseek/deepseek-v4-flash"

    def meta(self):
        return self.Meta()


class FakeCtx:
    """假 AstrBot context：llm_generate 返回可配置文本；used to test 事实提炼。"""

    def __init__(self, text="用户喜欢布丁", raise_llm=False, chat_text="用户喜欢牛角包"):
        self.text = text
        self.raise_llm = raise_llm
        self.chat_text = chat_text
        self.prompts = []
        self.chat_prompts = []
        self.return_empty = False

    async def get_using_provider_async(self):
        return FakeProvider()

    async def llm_generate(self, **kw):
        self.prompts.append(str(kw.get("prompt") or ""))
        if self.raise_llm:
            raise RuntimeError("llm boom")
        if self.return_empty:
            return None
        return types.SimpleNamespace(completion_text=self.text)

    def get_using_provider(self):
        outer = self

        class Prov:
            async def text_chat(self, prompt=None, **kw):
                outer.chat_prompts.append(str(prompt or ""))
                return types.SimpleNamespace(completion_text=outer.chat_text)

        return Prov()


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
    owner = FakeOwner(cfg)
    owner.context = FakeCtx()
    return mod.LivingMemoryCore(owner)


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
    assert kw.get("filters") == {"user_id": "u_owner"}, kw
    # 不论从哪个会话发起，永远只查唯一 scope
    await e.search("布丁是谁", "webchat_phone!alice!main")
    assert mem.calls[-1][2]["filters"]["user_id"] == "u_owner"
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
        assert add_calls and add_calls[0][2].get("user_id") == "u_owner"
        assert add_calls[0][2].get("metadata", {}).get("kind") == "worldview"
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
    e = make_engine(mem0_flush_turns=2, mem0_distill=False)
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
    print("[M6] 攒够 2 轮后台批量写入（distill 关：原样批量，infer=False） OK")


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


async def m11_affect_rerank():
    """阶段六：负面情绪下情绪一致/未解决事项被抬到前面；中性时原序不动。"""
    e = make_engine(mem0_top_k=3)
    mem = await e._ensure()
    mem.search_hits = [
        {"memory": "对方喜欢在雨天听爵士乐", "score": 0.62},
        {"memory": "对方说最近老是失眠，压力很大", "score": 0.55},
        {"memory": "对方提过下个月体检，还没定哪天", "score": 0.50},
    ]
    neutral = await e.recall_block("最近怎么样", "u1", affect={"pad": {"p": 0.5}, "silence_sev": 0.0})
    assert neutral.index("爵士乐") < neutral.index("失眠"), "中性时按相似度原序"
    neg = await e.recall_block("最近怎么样", "u1", affect={"pad": {"p": -0.4}, "silence_sev": 0.0})
    assert neg.index("失眠") < neg.index("爵士乐"), ("负面时应把情绪一致记忆抬上来", neg)
    assert "体检" in neg, "未解决事项也要保留"
    # 三重候选只取前 base_k 条
    assert neg.count("- ") <= 3
    print("[M11] 情感调制召回：负面优先情绪一致/未解决，中性原序 OK")


async def m12_affect_recall_switch():
    e = make_engine(mem0_top_k=2, mem0_affect_recall=False)
    mem = await e._ensure()
    mem.search_hits = [
        {"memory": "对方喜欢在雨天听爵士乐", "score": 0.62},
        {"memory": "对方说最近老是失眠，压力很大", "score": 0.55},
    ]
    off = await e.recall_block("最近怎么样", "u1", affect={"pad": {"p": -0.6}, "silence_sev": 0.0})
    assert off.index("爵士乐") < off.index("失眠"), ("关闭调制应按相似度", off)
    assert "top_k" in str(mem.calls[-1][2]), "search 契约不变"
    print("[M12] 关闭情感调制时退回纯相似度 OK")


async def m13_affect_wiring():
    src = io.open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    for needle in ("MEMORY_NEGATIVE_HINTS", "MEMORY_UNRESOLVED_HINTS",
                   "def rerank_affect(", "def _affect_context(",
                   "mem0_affect_recall", "mem0_affect_boost_pct", "mem0_unresolved_boost_pct"):
        assert needle in src, needle
    assert "recall_block(query, umo, affect=affect_state)" in src
    assert "mem0_affect_recall" in src and "top_k=min(24, base_k * 3)" in src
    print("[M13] 阶段六接线：词表/重排/配置键/召回扩大候选 OK")



async def m14_single_scope():
    """记忆源归一：不管会话、不管共享开关，全插件只有一个 mem0 scope。"""
    qq = "default_1000000001:FriendMessage:0123456789ABCDEF0123456789ABCDEF"
    phone = "webchat_phone!alice!main"
    e = make_engine(shared_scope_enabled=True, shared_scope_id="alice")
    assert e._uid(qq) == e._uid(phone) == e._uid("") == "u_alice", e._uid("")
    assert e.scope_id() == "u_alice"
    await e.search("布丁", qq)
    await e.search("布丁", phone)
    calls = [c for c in FakeMemory.instances[0].calls if c[0] == "search"]
    assert {c[2]["filters"]["user_id"] for c in calls} == {"u_alice"}
    # 关闭共享开关也仍然只有一个 scope（回退 owner），不再按会话隔离
    off = make_engine()
    assert off._uid(qq) == off._uid(phone) == "u_owner"
    blank = make_engine(shared_scope_enabled=True, shared_scope_id="  ")
    assert blank._uid(qq) == "u_shared"
    bad = make_engine(shared_scope_enabled=True, shared_scope_id="家/里 NAS")
    assert bad._uid(qq) == "u_NAS", bad._uid(qq)
    print("[M14] 记忆源归一：任何会话/开关组合下都只有一个 scope OK")


async def m15_watermark_and_migration():
    """水位来自 Mem0 写入、旧 scope 合并进唯一 scope、重置只清唯一 scope。"""
    e = make_engine()
    e._meta_save({})
    mem = await e._ensure()
    assert e.watermark() == "", e.watermark()
    await e.on_user_message(ev(text="第一句"))
    await e.on_llm_response(ev(), resp("第一回"))
    await e.on_user_message(ev(text="第二句"))
    await e.on_llm_response(ev(), resp("第二回"))
    await flush_tasks(e)
    wm = e.watermark()
    assert wm.startswith("20"), ("写入后必须有水位", wm)
    assert e.status()["watermark"] == wm
    # 旧 scope 合并
    mem.all_items = [
        {"memory": "旧QQ记忆", "user_id": "u_default_x"},
        {"memory": "旧语音记忆", "user_id": "u_webchat_phone"},
        {"memory": "世界观", "user_id": "elaina_world"},
        {"memory": "已在本 scope", "user_id": "u_owner"},
    ]
    mem.calls = []
    merged = await e.migrate_scopes()
    assert merged == 3, merged
    adds = [c for c in mem.calls if c[0] == "add"]
    assert all(c[2]["user_id"] == "u_owner" for c in adds), adds
    assert all(c[2]["infer"] is False for c in adds)
    deleted = [c[2].get("user_id") for c in mem.calls if c[0] == "delete_all"]
    assert set(deleted) == {"u_default_x", "u_webchat_phone", "elaina_world"}, deleted
    assert "u_owner" not in deleted
    # 重置：只清唯一 scope
    mem.calls = []
    n = await e.reset_scope()
    assert n == 4, n
    da = [c for c in mem.calls if c[0] == "delete_all"]
    assert da and da[-1][2]["user_id"] == "u_owner"
    print("[M15] 水位=Mem0 写入时间；旧 scope 合并；重置只清唯一 scope OK")


async def m16_memory_command_reads_mem0():
    """`/记忆` 直查 Mem0；旧 KV / 用户笔记 / 裸历史三条链路必须已删除。"""
    src = io.open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    for dead in ("memory_store", "_load_memory_store", "async def refresh_memory(",
                 "MEMORY_KEY", "_collect_user_notes", "_collect_raw_history",
                 "memory_cron", "MEMORY_MERGE_PROMPT", "_on_memory_time",
                 "MEMORY_BLOCK_TEMPLATE"):
        assert dead not in src, "旧记忆链路仍残留：" + dead
    assert "await engine.list_memories(" in src or "engine.list_memories(" in src
    assert "engine.flush_now()" in src and "engine.reset_scope()" in src
    assert "def memory_text(" in src and "def watermark(" in src
    # 命令层判的是 engine.enabled，真实引擎必须带这个属性（曾漏掉 → /记忆 直接报错）
    on = make_engine()
    off = make_engine(mem0_enabled=False)
    assert on.enabled is True, "真实引擎缺少 enabled 属性，/记忆 会 AttributeError"
    assert off.enabled is False
    print("[M16] /记忆 直查 Mem0，旧 KV/笔记/裸历史链路已彻底移除 OK")


async def m17_warmup_order_and_dedupe():
    """启动顺序（先合并再播种）与重复条目清理。"""
    src = io.open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    body = src[src.index("async def _warmup(self)"):]
    body = body[:body.index("async def ", 10)]
    assert body.index("await self.migrate_scopes()") < body.index("await self.seed_worldview()"), \
        "必须先合并旧 scope 再补种子，否则世界观事实会写两遍"
    assert body.index("await self.dedupe_scope()") > body.index("await self.seed_worldview()")
    e = make_engine()
    mem = await e._ensure()
    mem.all_items = [
        {"id": "m1", "memory": "魔法协会总部在旧都", "user_id": "u_owner"},
        {"id": "m2", "memory": "魔法协会总部在旧都", "user_id": "u_owner"},
        {"id": "m3", "memory": "布丁很好吃", "user_id": "u_owner"},
    ]
    mem.calls = []
    removed = await e.dedupe_scope()
    assert removed == 1, removed
    dels = [c for c in mem.calls if c[0] == "delete"]
    assert [c[1] for c in dels] == ["m2"], dels
    assert [i["id"] for i in mem.all_items] == ["m1", "m3"]
    # 没有重复时不删任何东西，也不报错
    mem.calls = []
    assert await e.dedupe_scope() == 0
    assert not [c for c in mem.calls if c[0] == "delete"]
    print("[M17] 启动顺序先迁后种；重复条目按文本去重，无重复时空转 OK")


async def m18_distill_facts():
    """写入前把对话提炼成事实：逐条 infer=False 写入；调用失败回退 text_chat；NONE 不写。"""
    # 1) 正常提炼：两条事实 → 两次 add，每次一条字符串，metadata.distilled=True
    e = make_engine(mem0_flush_turns=2, mem0_distill=True)
    e.owner.context = FakeCtx(
        text="- 用户计划 11 月 15 日去京都，住四条河原町\n- 用户对香菜过敏\nNONE"
    )
    await e.on_user_message(ev(text="我 11 月 15 号去京都，住四条河原町"))
    await e.on_llm_response(ev(), resp("记下了"))
    await e.on_user_message(ev(text="另外我对香菜过敏"))
    await e.on_llm_response(ev(), resp("知道了"))
    await flush_tasks(e)
    mem = await e._ensure()
    adds = [c for c in mem.calls if c[0] == "add"]
    assert len(adds) == 2, adds
    facts = [c[1] for c in adds]
    assert all(isinstance(f, str) for f in facts), facts
    assert "京都" in facts[0] and "香菜" in facts[1], facts
    assert adds[0][2].get("infer") is False
    assert adds[0][2].get("metadata", {}).get("distilled") is True
    assert adds[0][2].get("user_id") == "u_owner"
    assert "对话" in e.owner.context.prompts[-1] or "提取" in e.owner.context.prompts[-1]

    # 2) llm_generate 抛错 → 回退 text_chat，仍写入提炼结果
    e2 = make_engine(mem0_flush_turns=2, mem0_distill=True)
    e2.owner.context = FakeCtx(raise_llm=True, chat_text="用户喜欢牛角包")
    await e2.on_user_message(ev(text="我爱吃牛角包"))
    await e2.on_llm_response(ev(), resp("嗯"))
    await e2.on_user_message(ev(text="真的"))
    await e2.on_llm_response(ev(), resp("好"))
    await flush_tasks(e2)
    mem2 = await e2._ensure()
    adds2 = [c for c in mem2.calls if c[0] == "add"]
    assert len(adds2) == 1 and "牛角包" in adds2[0][1], adds2
    assert e2.owner.context.chat_prompts, "应回退到 text_chat"

    # 3) 提炼结果为 NONE / 空 → 不写入任何东西（不退回存原文）
    e3 = make_engine(mem0_flush_turns=2, mem0_distill=True)
    e3.owner.context = FakeCtx(text="NONE")
    await e3.on_user_message(ev(text="你好"))
    await e3.on_llm_response(ev(), resp("你好呀"))
    await e3.on_user_message(ev(text="蛙趣"))
    await e3.on_llm_response(ev(), resp("怎么啦"))
    await flush_tasks(e3)
    mem3 = await e3._ensure()
    assert not [c for c in mem3.calls if c[0] == "add"], "NONE 时不应写入原句"
    print("[M18] 写入前提炼事实→逐条 infer=False；失败回退 text_chat；NONE 不写原句 OK")


async def main():
    for fn in [m1_disabled, m2_singleton, m3_search_contract, m4_recall_block,
               m5_seed_idempotent, m6_buffered_write, m7_failure_degrade, m8_close, m9_wiring,
               m10_seed_sees_all_beyond_default_limit, m11_affect_rerank,
               m12_affect_recall_switch, m13_affect_wiring, m14_single_scope,
               m15_watermark_and_migration, m16_memory_command_reads_mem0,
               m17_warmup_order_and_dedupe, m18_distill_facts]:
        await fn()
    print("阶段二（long-term memory）测试全部通过")


if __name__ == "__main__":
    asyncio.run(main())
