# 阶段二交付报告：长期记忆（Mem0 接入）

> 对应《拟人聊天插件重构工程提示词（最终版）》阶段二：按官方文档接入 Mem0，
> 不自己实现向量库/事实提取；写入、召回、种子记忆三件事跑通即止，
> **本阶段不做情感-记忆耦合**（留给阶段四）。

## 1. 结论

- 记忆后端 = `mem0ai 2.2.1` + 嵌入式 qdrant + `fastembed`（`BAAI/bge-small-zh-v1.5`，512 维），
  全部数据落 AstrBot 持久卷，容器重启不丢（探针已实测）。
- 代码形态 = 新增 `LivingMemoryCore`（`main.py` 核心七），对 mem0 的**薄封装**，
  **不重写**任何向量检索、事实抽取逻辑，全部交给 mem0 官方实现。
- 默认 `mem0_enabled=false`：关闭时插件行为与 v1.2.1 完全一致（阶段零硬约束：
  所有新行为必须有开关，默认关闭时与当前版本行为一致）。
- 失败一律降级：mem0 不可用/初始化失败时，只 warning，主动消息照常生成（只是没有召回块）。

## 2. 三个功能点与实现位置

| 需求 | 实现 | 位置 |
| --- | --- | --- |
| 写入（事实/偏好/事件） | 用户+助手配成一轮进缓冲，攒够 `mem0_flush_turns`（默认 4）轮后后台线程 `mem.add` | `LivingMemoryCore.on_user_message` / `on_llm_response` / `_write` |
| 召回（生成前注入） | 主动消息生成时，用「最近几条用户发言」做查询，召回结果作为【你记得的事】块拼进提示词 | `LivingMemoryCore.recall_block` → `ProactiveGuardCore._living_memory_block` |
| 种子记忆 | 首次启动把 `worldview.json` 的 `facts`（34 条）写入 `user_id="elaina_world"`，幂等 | `LivingMemoryCore.seed_worldview` |

## 3. 关键设计决策（含理由）

1. **懒加载 + 进程内单例 + `asyncio.to_thread`**：`import mem0` 冷启 13.7s / 建实例 1.6~10.4s，
   放主线程会卡死 AstrBot 启动；qdrant 嵌入式是**目录独占锁**，第二个实例必炸
   （`Storage folder ... already accessed by another instance`），所以只能单例。
2. **启动不阻塞**：`initialize()` 只 `create_task(_warmup())`，插件加载时间不受影响。
3. **`infer=False` 作默认**：`add(infer=True)` 实测 13.3s 且事实抽取质量取决于模型（探针里
   中文事实被抽成英文）；默认直接存原文，需要抽取的用户可自己打开 `mem0_infer`。
4. **阈值用百分比整数**：`mem0_threshold_pct`（默认 45 → 0.45）。mem0 默认 0.1 太宽，
   会把 0.356 的无关记忆也召回。用整数是为了配置面板的 slider 好操作。
5. **召回与 KV 摘要并存**：`_living_memory_block` 现在先拼 mem0 召回块，再拼原有 KV 记忆摘要，
   两者都可缺省；这样即使 mem0 出问题，旧路径仍然工作。
6. **API Key 不落插件配置**：`mem0_llm_api_key` 默认空，运行时从 AstrBot 的
   `cmd_config.json` 里 `type=anthropic` 的 provider 自动读取（探针实测：deepseek/xiaomi-openai
   的 key 都 401，只有 anthropic 兼容端点可用）。
7. **数据路径全部落在 `<data>/`**：`mem0_qdrant/`、`mem0_history.db`、`fastembed_cache/`（91MB 模型），
   避免容器重建时重下模型。

## 4. 配置项（`_conf_schema.json` 121 → 132 键，新增 11 个）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `mem0_enabled` | false | 总开关；关时行为与 v1.2.1 一致 |
| `mem0_llm_model` | mimo-v2.6-pro | 抽取用模型（anthropic 兼容端点） |
| `mem0_llm_base_url` | https://token-plan-cn.xiaomimimo.com/anthropic | 同上 |
| `mem0_llm_api_key` | "" | 留空自动从 AstrBot 的 anthropic provider 读取 |
| `mem0_embed_model` | BAAI/bge-small-zh-v1.5 | 换模型要清空 `mem0_qdrant/` |
| `mem0_top_k` | 6 | 每次召回条数上限 |
| `mem0_threshold_pct` | 45 | 相似度阈值百分比 |
| `mem0_seed_facts` | true | 启动时把世界观点事实写入记忆（幂等） |
| `mem0_flush_turns` | 4 | 攒够几轮对话批量写入 |
| `mem0_recall_chars` | 1200 | 召回块最大字符数 |
| `mem0_infer` | false | 是否让 LLM 抽取事实（会有 13s 级延迟） |

新增 `/记忆状态` 命令（别名 `/长期记忆状态`）：查看是否启用、是否就绪、种子是否已写、缓冲与后台任务数。

## 5. 测试

新增 `tests/test_memory.py`（9 组，注入假 mem0 模组，不依赖真实依赖）：

- M1 关闭时不加载、不召回；
- M2 懒加载 + 进程内单例（`from_config` 只调一次）；
- M3 `search` 用 `filters={"user_id":...}`、不传顶层 `user_id`（mem0 2.2.1 会 ValueError）、阈值与 top_k 生效；
- M4 召回块：无命中返回空串，有命中带【你记得的事】标题；
- M5 世界观点种子幂等（已存在的不重复写，`infer=False`）；
- M6 攒够 2 轮后台批量写入（role 顺序、按会话 `u_<sid>`、`infer=False`）；
- M7 初始化失败降级为空且本次运行不反复重试；
- M8 `close()` 关闭 qdrant client 并释放单例；
- M9 接线完整性（构造/初始化/收尾/写入钩子/`/记忆状态`/召回注入四处）。

回归：`py tests/test_living.py`（12 组）、`py tests/test_merged.py`（24 组）全过。

## 6. 明确未做（不在本阶段范围）

- **情感-记忆耦合**（负面情绪下优先召回情绪一致或未解决事项）→ 阶段四；
- reranker、graph memory、多用户隔离策略调优；
- 容器**删除重建**后的持久化（探针只验证了 `docker restart`）；
- 长期稳定性/并发压力（mem0 嵌入式单写者，暂按单会话低频写入设计）。

## 7. 使用注意

- 首次开启会加载向量模型（约 10~20s，日志 `[memory] mem0 就绪（Xs）`），期间聊天不受影响；
- **不要把 numpy 升到 2.x**：目标机 CPU 无 AVX，2.x 直接 SIGILL；
- 若 qdrant 目录损坏：停插件 → 删 `<data>/mem0_qdrant/` → 重启（记忆清空，种子会自动重写）；
- `fastembed_cache/` 不要删，删了会重下 91MB 模型（走 hf-mirror）。

## 真机召回验证（2026-10-28）

在插件持有 qdrant 目录锁的情况下，把 `/AstrBot/data/mem0_qdrant` 复制到容器内 `/tmp/qtest_qdrant`，
用同款 config（同 embedder、同 collection 名、同 512 维）打开副本查询：

| 查询 | top1 分数 | top1 内容 |
| --- | --- | --- |
| 魔法协会在哪里 | 0.694 | 存在世界性的「魔法统合协会」，成员包括沙耶与席拉。 |
| 这个世界有互联网吗 | 0.5716 | 世界已有火药、钟表、机械人偶、飞龙；但没有手机、网络、电脑与人工智能。 |
| 我是谁 | 0.4661 | 她自称固定为「我」，自夸时说「本魔女」，自我介绍固定为「灰之魔女」。 |

三个查询的 top1 都命中世界观事实，且与问题语义相关 → 种子写入的向量与 payload 可被正常召回。

**踩到的坑（已修，v1.3.1）**：`mem0` v2 `get_all()` 默认 `top_k=20`，而世界观有 34 条事实，
判重集合只能看到 20 条 → 每次重启会重复补写 14 条。已在 `seed_worldview()` 显式传 `top_k=1000`。

**另一个坑（验证方法本身）**：qdrant local 模式的 `storage.sqlite` 是懒落盘的，进程内尚未 flush 时
复制出来的目录可能 0 条（`points` 表 `count=0`），会被误判成「写入失败」。判断写入是否成功要看
插件进程所在容器内的原目录，或等 flush 后再复制。
