# 阶段二探针报告：Mem0 在 AstrBot 容器内的可行性（不接插件，只验证底座）

> 探针执行：2026-10-28 19:02–19:26 (Asia/Shanghai)，容器 `astrbot`，AstrBot v4.27.5，插件 1.2.1。
> **本文只写实测到的东西**；没测的一律标「未验证」，不拿推测充数据。
> 配套脚本：`D:\deepseek\_scratch\probe_mem0.py`（容器内路径 `/AstrBot/data/probe_mem0.py`）。

## 0. 结论（TL;DR）

**技术可行**：mem0 2.2.1 + fastembed 0.9.1 + 嵌入式 qdrant 在本容器里跑通了 `add / search / get_all / AsyncMemory`，
中文问题「布丁是谁」能召回猫相关记忆（top1 score 0.538），容器重启后数据仍在，且 **AstrBot 插件 1.2.1 正常加载、零 Traceback/[ERROR]**。

但有 **四个硬前提**，缺一个就翻车：

| # | 前提 | 不满足的后果 |
| --- | --- | --- |
| 1 | `numpy` 必须钉 **1.26.4** | 容器 CPU（AMD G-T40N）无 AVX/AVX2/SSE4.2，numpy 2.x 直接 `SIGILL`，mem0/fastembed/qdrant/onnxruntime 全部 import 失败（rc=132） |
| 2 | `FASTEMBED_CACHE_PATH` 显式指到持久卷 | 默认缓存写 `/tmp/fastembed_cache`，容器重启后重下 91 MB（外网还要走 hf-mirror） |
| 3 | LLM 端点必须实测可达 | AstrBot 现配的 deepseek key **401 已失效**；xiaomi 的 OpenAI 兼容 key 也 401；只有 **anthropic 兼容端点** 200 可用 |
| 4 | 嵌入式 qdrant 单实例 + 懒加载 | 目录独占锁（第二个 Memory 实例直接 `RuntimeError`）；`import mem0` 冷启动 **13.7 s / +153 MB RSS**，不能放主线程 |

## 1. 环境与方法

- NAS `192.168.1.10`，容器 `astrbot`（`docker exec` 以 root 操作）。
- 容器：Python 3.12.14、pip 26.2.1；**CPU `AMD G-T40N Processor`，nproc=2**，`/proc/cpuinfo` 的 flags 里 **没有 avx / avx2 / sse4_2**。
- 访问链路：Windows → `D:\deepseek\_scratch\sshx.py`（paramiko，走 Clash 的 HTTP CONNECT `127.0.0.1:7897`，订阅须是「家里NAS SeaVPN」）→ NAS `sudo` → `docker exec`。
- 计时口径：NAS 宿主是 busybox，`date +%s%3N` 不支持，**秒级用 `date +%s`，毫秒级在容器内用 `time.time()`**。
- 每次改动后都 `docker restart` 并检查日志（§8 硬验收）。

## 2. 安装（实测）

`pip install -i https://pypi.tuna.tsinghua.edu.cn/simple mem0ai fastembed`：

| 包 | 版本 |
| --- | --- |
| mem0ai | 2.2.1 |
| fastembed | 0.9.1 |
| qdrant-client | 1.20.0 |
| onnxruntime | 1.31.1 |
| huggingface_hub | 1.33.0 |
| tokenizers | 0.23.3 |
| grpcio / protobuf | 1.85.0 / 6.33.6 |
| posthog / mmh3 / portalocker / pytz / backoff / fsspec / flatbuffers / hf-xet / py_rust_stemmers / tqdm | 随附 |

- 安装墙钟：**exit 0，69,239 ms**。
- `pip check` → `No broken requirements found.`（装完后复验，仍是这句）。
- 插件自身 `requirements.txt` 只有 `httpx>=0.24.0 / Pillow>=9.0.0 / psutil>=5.9.0` → 与 mem0 依赖无交集、无冲突。

## 3. 阻碍一：numpy 2.5.2 在无 AVX 的 CPU 上 SIGILL（本次最大坑）

镜像自带 numpy **2.5.2**（不是 mem0 拉进来的；mem0ai 2.2.1 本身不直接依赖 numpy，numpy 来自 qdrant-client / fastembed / onnxruntime 这一支）。
在本机 CPU 上：

```
$ docker exec astrbot python3 -c "import numpy"
Fatal Python error: Illegal instruction
...
File ".../numpy/_core/multiarray.py", line 11
File ".../numpy/__init__.py", line 109
(run 后退出码 132 = SIGILL)
```

Import 隔离结果：`numpy / qdrant_client / onnxruntime / fastembed / mem0` 全部 **rc=132**；
而 `openai / posthog / google.protobuf / grpc / huggingface_hub / tokenizers` 均 rc=0 —— 说明是 numpy 这一条链的 SIMD 指令问题，不是装坏了。

**修复**：`python3 -m pip install numpy==1.26.4`（覆盖镜像里的 2.5.2）。

- 之后 `import numpy` 正常：**851.1 ms**，RSS 36 MB。
- 反向依赖核验：镜像里依赖 numpy 的 `pandas 3.0.5`(>=1.26)、`onnxruntime`、`qdrant-client`、`faiss-cpu 1.15`(>=1.25)、`rank-bm25`、`dashscope` 在 1.26.4 下全部满足，`pip check` 干净。

> ⚠️ 阶段三如果把这几个包写进插件的 `requirements.txt`，AstrBot 的依赖安装器会去装 **numpy 2.x**，于是插件加载即 SIGILL 崩掉。**必须显式钉 `numpy==1.26.4`**（并写注释说明原因）。

## 4. 阻碍二：LLM 端点实测（mem0 只能用于 `infer=True` 的事实抽取）

探针不硬编码密钥，运行时从 `/AstrBot/data/cmd_config.json` 读取（`provider_sources`）。

| 端点 | 类型 | key 长度 | 结果（原始） |
| --- | --- | --- | --- |
| `https://api.deepseek.com/v1`（deepseek，`deepseek-v4-flash`） | OpenAI 兼容 | 35 | **401** `Authentication Fails, Your api key: ****06e3 is invalid` |
| `https://token-plan-cn.xiaomimimo.com/v1`（`mimo-v2.6-flash` / `mimo-v2.6-pro`） | OpenAI 兼容 | 51 | **401** `Invalid API Key`（flash 4251.4 ms、pro 281.7 ms） |
| `https://token-plan-cn.xiaomimimo.com/anthropic`（`mimo-v2.6-pro`） | anthropic 兼容 | 51 | **HTTP 200，1234.3 ms**（`/v1/messages` + `x-api-key` + `anthropic-version: 2023-06-01`） |

结论：mem0 用 `provider="anthropic"` + `anthropic_base_url=.../anthropic` 可跑通事实抽取；
用 `provider="deepseek"` 会卡在 401（**这是 AstrBot 现有配置的 key 失效，不是 mem0 的问题**，值不值得修由你决定）。

启动日志里另有 2 条既有 WARN（与本次安装无关）：
`Configured model 'mimo-v2.6-pro' is not in the known Token Plan model list (...)`。

## 5. 实测可用的配置字典

```python
{
    "llm": {
        "provider": "anthropic",
        "config": {
            "model": "mimo-v2.6-pro",
            "api_key": "<运行时从 cmd_config.json 读，51 字符>",
            "anthropic_base_url": "https://token-plan-cn.xiaomimimo.com/anthropic",
            "temperature": 0.1,
            "max_tokens": 2000,
        },
    },
    "embedder": {
        "provider": "fastembed",
        "config": {"model": "BAAI/bge-small-zh-v1.5", "embedding_dims": 512},
    },
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "collection_name": "mem0_probe",
            "path": "/AstrBot/data/mem0_qdrant",
            "embedding_model_dims": 512,
            "on_disk": True,
        },
    },
    "history_db_path": "/AstrBot/data/mem0_history.db",
}
```

配套环境变量（必须在 `import mem0` **之前**设置）：

```python
os.environ["MEM0_TELEMETRY"] = "False"                         # 默认开启 posthog 上报
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"            # huggingface.co 在本网络不可达
os.environ["FASTEMBED_CACHE_PATH"] = "/AstrBot/data/fastembed_cache"  # 默认 /tmp，重启即丢
```

注意：`QdrantConfig` 拒绝未知字段（`Extra fields not allowed`）；`embedding_dims` 必须与模型输出一致（bge-small-zh-v1.5 = 512，默认是 1536）。

## 6. `probe_mem0.py` 与实测数字

用法（容器内）：

```bash
python3 /AstrBot/data/probe_mem0.py write    # 初始化 + LLM 抽取 + 3 条写入 + search + get_all + async
python3 /AstrBot/data/probe_mem0.py verify   # 容器重启后：数据是否还在
```

脚本行为：读 AstrBot 配置 → LLM 级联（deepseek 先试、失败退 anthropic）→ `Memory.from_config` → `add(infer=True)` 抽取烟测 →
3 条 `add(infer=False)` → `search("布丁是谁", top_k=3, filters={"user_id":"probe"})` → `get_all` → 顶层 `user_id` 拒绝行为 →
`AsyncMemory` → 打印每步 ms 与 `RESULT_JSON`。

**write 轮（实测）**

| 步骤 | 耗时 | 结果 |
| --- | --- | --- |
| 脚本冷启（import mem0 等） | 13,899 ms（首行日志前） | — |
| 读配置 | 2.3 ms | deepseek key_len=35 / xiaomi key_len=51 |
| `Memory.from_config`（deepseek 版，首次含 fastembed 加载） | 10,367.9 ms | OK |
| `add(infer=True)` deepseek | 6,006.0 ms | **401 失败**（§4） |
| `Memory.from_config`（anthropic 版） | 7,677.8 ms | OK |
| `add(infer=True)` anthropic | **13,331.0 ms** | OK，抽出 2 条事实（见下） |
| `Memory.from_config`（最终实例） | 1,617.2 ms | OK |
| `add(infer=False)` × 3 | 3,360.0 / 557.6 / 540.4 ms | OK（首次含冷启 embedding） |
| `search` | **194.0 ms** | top1 = 橘猫那条，`relevance_ok=True` |
| `get_all` | 7.3 ms | 5 条 |
| `AsyncMemory.from_config + search` | 4,612.8 ms | OK |
| 整轮墙钟 | **63 s** | SCRIPT_EXIT=0 |

`add(infer=True)` 抽取结果（原文，注意 **mimo 输出的是英文**）：

```
"User's name is 小林 (Xiao Lin)"
"User has an orange cat (橘猫) named 布丁 (Pudding) who is 3 years old as of 2026"
```

`add(infer=False)` 三条原文（中文原样落库）：

```
我家猫叫布丁，是一只三岁的橘猫，特别爱吃三文鱼。
我每天早上七点起床，习惯先喝一杯不加糖的黑咖啡。
下周三我要去杭州出差，住在西湖附近的酒店。
```

召回相关性（write 轮，查询 `布丁是谁`）：

| rank | score | 记忆 |
| --- | --- | --- |
| 1 | 0.5385 | `User has an orange cat (橘猫) named 布丁 (Pudding) who is 3 years old as of 2026` |
| 2 | 0.4860 | `我家猫叫布丁，是一只三岁的橘猫，特别爱吃三文鱼。` |
| 3 | 0.3560 | `User's name is 小林 (Xiao Lin)` |

## 7. 持久化验证（容器重启后）

`docker restart astrbot` 后跑 `verify`：

| 指标 | 结果 |
| --- | --- |
| `get_all` | **5 条**，3/3 条原始中文事实逐字命中（`facts_present=3/3`） |
| `search("布丁是谁")` | top1 = 0.5385 那条，`relevance_ok=True`，2,894.9 ms（冷启动含模型加载） |
| `get_all` 耗时 | 5.7 ms |
| `AsyncMemory` | OK，4,612.8 ms |
| 磁盘 | `/AstrBot/data/mem0_qdrant` **64 KB**、`fastembed_cache` **91 MB**、`mem0_history.db` **20 KB** |
| 整轮墙钟 | 32 s |

结论：qdrant 落盘 + history sqlite + fastembed 缓存三者都在 `/AstrBot/data`（持久卷）上，**restart 后数据完整**。
（「容器被删除重建后是否还在」取决于 volume 挂载方式，本次未测。）

## 8. 硬性验收：没有弄坏 AstrBot（实测日志）

| 检查项 | 实测 |
| --- | --- |
| `docker restart` 墙钟 | **13 s** |
| 容器 `StartedAt` | `2026-10-28T11:19:24Z` |
| AstrBot 版本行 | `[19:20:04.646] AstrBot v4.27.5`（起容器后 +40.6 s） |
| 插件加载 | `[19:20:09.553] Plugin astrbot_plugin_elaina_suite (1.2.1) by SeaSmall`（+45.1 s） |
| 该次启动以来日志 | 67 行；`Traceback`/`[ERROR]` 计数 **0**；插件相关日志 10 行；`[WARN]` 2 条（§4 那个 mimo 模型清单告警，既有配置） |
| WebUI | `AstrBot v4.27.5 WebUI is ready`（启动完成后） |
| `pip check` | `No broken requirements found.` |

即：**mem0ai + fastembed 装上、numpy 降到 1.26.4 之后，AstrBot 连续 3 次重启（19:01 / 19:14 / 19:19）都正常加载插件 1.2.1，无 Traceback/[ERROR]。**
（插件根本没 import mem0，所以这是「不破坏」而不是「已集成」——集成是阶段三的事。）

## 9. API 行为与集成约束（踩过的坑，阶段三会直接遇到）

1. **mem0 v2 的实体过滤走 `filters`**，顶层 `user_id` 会抛异常（原文）：
   ```
   ValueError: Top-level entity parameters frozenset({'user_id'}) are not supported in search().
   Use filters={'user_id': '...'} instead.
   ```
   同理 `get_all(user_id=...)`。
2. **`AsyncMemory.from_config` 是同步 classmethod，不能 `await`**：
   ```
   TypeError: object AsyncMemory can't be used in 'await' expression
   ```
   正确写法 `am = AsyncMemory.from_config(cfg)` 然后 `await am.search(...)`。
3. **嵌入式 qdrant 是目录独占锁**：同进程内再建一个 `Memory` 会炸（原文）：
   ```
   RuntimeError: Storage folder /AstrBot/data/mem0_qdrant is already accessed by another instance of Qdrant client.
   If you require concurrent access, use Qdrant server instead.
   ```
   → 内存里必须**单例**；换实例前先 `mem.vector_store.client.close()`（探针里就是靠这个把级联跑通的）。
4. **导入成本**（容器内实测）：`import numpy` 851.1 ms / 36 MB；`import mem0` **13,694.5 ms** / **+153 MB RSS**（2 核 Bobcat）。
   直接放 AstrBot 主线程/事件循环会卡死十几秒 → 必须 `asyncio.to_thread` 懒加载。
5. **每次 `Memory.from_config` 都会重载 fastembed 模型**（热缓存 1.6–10.4 s 不等）→ 进程内复用单例，别反复建。
6. **spaCy 噪音**（每次 init 都打两行，`pip install mem0ai[nlp]` 可消音，未安装）：
   ```
   Failed to load spaCy lemma model: spaCy is not installed...
   Failed to load spaCy full model: spaCy is not installed...
   ```
7. 首次 `TextEmbedding` 需从 hf-mirror 下 18 个文件（91 MB）：实测下载+加载 **23,946.4 ms**（单跑）；embed 两条 **279.1 ms**，输出 dim=512、L2 范数 1.0。

## 10. 阶段三建议（可直接落地的决策）

1. **依赖钉死**：`requirements.txt` 里加 `numpy==1.26.4`（并注释：无 AVX CPU，2.x 会 SIGILL）、`mem0ai==2.2.1`、`fastembed==0.9.1`。
2. **env 先于 import**：`FASTEMBED_CACHE_PATH` / `HF_ENDPOINT` / `MEM0_TELEMETRY=False` 在插件最早初始化时设好。
3. **单例 + 懒加载**：`Memory` 全局一个，首次用到时 `await asyncio.to_thread(_build)`；AstrBot 热重载/停插件时 `close()` 释放锁。
4. **LLM 选型**：走 `anthropic` provider + `anthropic_base_url`（当前唯一可用的 key 路径）；**抽取一次 13.3 s，太慢**，建议：
   - 默认 `infer=False`（由插件自己决定写什么事实，快 0.5 s 级）；
   - 或 `infer=True` 丢后台队列，不阻塞回复；
   - 若要 `infer=True`，给 `custom_instructions` 强制中文（当前 mimo 把事实抽成了英文，中英混库会稀释中文召回）。
5. **阈值**：默认 `threshold=0.1` 太宽（这次 0.356 的「小林」也进来了），建议 0.45–0.5 起调。
6. **数据兼容**：v1 写法（顶层 `user_id`）在 2.2.1 直接 ValueError，插件里要按 `filters=` 写。
7. **磁盘/内存预算**：cache 91 MB（一次性）+ qdrant 64 KB / 5 条；长期增长速率**未验证**。

## 11. 回滚方法（如果决定不进阶段三）

```bash
docker exec astrbot pip uninstall -y mem0ai fastembed
docker restart astrbot
```

**不要**把 numpy 升回 2.5.2（那会 SIGILL）；保留 1.26.4 对 AstrBot 现有插件无副作用（已实测 3 次重启正常）。
插件代码未改动，回滚只涉及容器内包与 `/AstrBot/data/mem0_*` 数据目录。

## 12. 未验证 / 边界

- 长期稳定性、并发（多线程/多 worker 同时用 Memory）——未验证。
- reranker、graph memory、`enable_graph` 等高级特性——未验证。
- 真实插件集成（本次刻意没动 `main.py` / `_conf_schema.json`）——未做。
- 抽取延迟在 mimo 限流/抖动下的表现——只测了单次 13.3 s。
- 容器 **删除重建**（非 restart）后数据是否保留——未验证。
- `probe_mem0.py` 里 `mimo-v2.6-pro` 是配置里读到的模型名；`flash` 也 200，但未做抽取对比。

## 附录 A：原始错误文本（逐字）

```
# A1 numpy SIGILL（numpy 2.5.2，rc=132）
Fatal Python error: Illegal instruction
  File ".../numpy/_core/multiarray.py", line 11
  File ".../numpy/__init__.py", line 109

# A2 deepseek 失败（mem0 内抛，末尾被包成 LLMError）
LLM extraction failed: Error code: 401 - {'error': {'message': 'Authentication Fails, Your api key: ****06e3 is invalid (request_id: ...)', 'type': 'authentication_error', 'param': None, 'code': 'invalid_request_error'}}
mem0.exceptions.LLMError: LLM extraction failed: Error code: 401 - {...}

# A3 xiaomi OpenAI 兼容端点
openai.AuthenticationError: Error code: 401 - {'error': {'message': 'Invalid API Key', 'param': 'Please provide valid API Key', 'code': '401', 'type': 'invalid_key'}}

# A4 qdrant 目录锁
RuntimeError: Storage folder /AstrBot/data/mem0_qdrant is already accessed by another instance of Qdrant client. If you require concurrent access, use Qdrant server instead.

# A5 AsyncMemory
TypeError: object AsyncMemory can't be used in 'await' expression

# A6 顶层 user_id
ValueError: Top-level entity parameters frozenset({'user_id'}) are not supported in search(). Use filters={'user_id': '...'} instead.
```

## 附录 B：路径清单

| 用途 | 路径 |
| --- | --- |
| 探针脚本（宿主机） | `D:\deepseek\_scratch\probe_mem0.py` |
| 探针脚本（容器内） | `/AstrBot/data/probe_mem0.py` |
| AstrBot 配置（读） | `/AstrBot/data/cmd_config.json` |
| qdrant 数据 | `/AstrBot/data/mem0_qdrant/` |
| history sqlite | `/AstrBot/data/mem0_history.db` |
| fastembed 缓存 | `/AstrBot/data/fastembed_cache/`（91 MB） |
| 插件目录（未改动） | `/AstrBot/data/plugins/astrbot_plugin_elaina_suite/` |

## 附录 C：复现命令（从 Windows 宿主）

```bash
# 1) 上传脚本（必须禁掉 git-bash 的路径转换，否则 /tmp 被打成 F:/Temp）
MSYS_NO_PATHCONV=1 python m0_put.py "D:\deepseek\_scratch\probe_mem0.py" /tmp/probe_mem0.py
# 2) 送进容器并跑 write 轮（命令文件 c30.txt 内容）
sudo docker cp /tmp/probe_mem0.py astrbot:/AstrBot/data/probe_mem0.py
sudo docker exec -i astrbot python3 /AstrBot/data/probe_mem0.py write
# 3) 重启后验证持久化
sudo docker restart astrbot && sleep 30
sudo docker exec -i astrbot python3 /AstrBot/data/probe_mem0.py verify
```
