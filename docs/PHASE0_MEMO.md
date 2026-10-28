# 阶段零工程备忘录（重构前置结论）

> 目的：在写任何代码前，把「论文依据 / 现状与根因 / 重叠功能判据」钉死，作为后续阶段的验收基线。
> 判据始终是**拟人感可观测提升**，不是「用了多少框架」。
> 配套文档：`docs/PHASE0_RESEARCH.md`（原始文献核实）、`docs/PHASE0_CURRENT_STATE.md`（现状与复现）、`docs/PHASE0_PERSONA.md`（人格固化依据）、`_scratch/repro_burst.py`（爆发复现脚本）。

---

## 1. 论文对齐（本期实现只允许用这些来源，逐条可验收）

### 1.1 Sentipolis（情感状态机）
- 出处已核实：Fu, Chen, Xiao, Xuan, Busso, Diab，《Sentipolis: Emotion-Aware Agents for Social Simulations》，arXiv:2601.18027（v1 2026-01-25 / v2 2026-04-21），正式版 Findings of ACL 2026, pp.7465–7494, DOI 10.18653/v1/2026.findings-acl.368。
- PAD 三维 [−1,1]；衰减 `s(t+Δt)=s(t)·2^(−Δt/120min)`，每步无条件施加 → 必须按**真实经过时间**折算，禁止「每轮固定乘系数」。
- 快通道：每轮对话一次即时更新。慢通道：**不是 N 轮/N 分钟**，原文是 reflection 在「近期事件 poignancy 累计 > 150」时触发 → 实现为可配累积重要度阈值。
- 「不活跃时期的情感行为」：原文只有一句——衰减让强情绪软化、避免群体过度封闭；**没有独立 idle 机制**。→ 阶段三的「沉默沮丧」补丁必须显式标注为**超出 Sentipolis 的新增**，不得伪装成论文机制。

### 1.2 GWA + 内在动机（自主性）
- 注意力选择：Mashour et al. 2020, Neuron（竞争 → 点火 → 广播，排他性 bottleneck）；每轮只允许 1 个候选意图成为 winner，其余降权不删除。常规回复走快路径，仅「多路信息冲突 / 未见话题」才开全局竞争。
- 内在动机：Oudeyer & Kaplan 2007（learning progress，Eq.11 区域误差差）做门控；奖励用「历史平均预测误差 − 当前平均预测误差」，按话题/情境分区统计；太熟（误差已低）与太乱（误差高且不降）都不给好奇加成。
- 情绪参与选择：Shanahan 2006（affect 介导 action selection）——恰好匹配「PAD + 意图竞争」。
- 预测误差只允许离线可算的代理量（perplexity / 语义距离 / 重复率），**不引入训练**，阈值可配。

### 1.3 Mem0（记忆）
- 包名 `mem0ai`，核实版本 **2.2.1**（Python >=3.10,<4.0）。
- `add(messages, *, user_id=None, agent_id, run_id, metadata, timestamp, expiration_date, infer=True, memory_type, prompt)` → `{"results":[{id,memory,event}]}`。
- `search(query, *, top_k=20, filters=None, threshold=0.1, rerank=False, ...)` → `{"results":[{id,memory,score}]}`；`get_all / update(memory_id, text, metadata) / delete(memory_id)`；`AsyncMemory` 全量 async 版。
- 硬坑：`search()/get_all()` **拒绝顶层 user_id**，必须 `filters={"user_id": ...}`；OSS 传 `timestamp` 会 ValueError；遥测默认开，必须 `MEM0_TELEMETRY=False`。
- OSS 不纯本地：vector_store 默认 `qdrant(path=...)` 嵌入式，但 llm/embedder 默认 OpenAI → 要么切本地 provider，要么显式声明依赖外部 API（本机当前无 mem0ai/fastembed/qdrant 安装，需 pip 安装并冻结版本）。
- 写入走 `infer=True` 默认去重推理，**禁止每轮无条件 add 原始消息**。

### 1.4 本期明确不实现
RGWM 多尺度内部模拟、SARSI 完整递归自我改进、ASURA 能力创生循环——不引依赖、不预留占位、不写「未来会接」的注释。

---

## 2. 现状彻查与爆发根因（先复现，后修复）

### 2.1 调度现状（`main.py` 行号）
- 生命周期：`initialize()` 2800 → `_install_gate()` 2839 + `_pause_active_agent_jobs()` 2932 + `_setup_scheduler()` 2958；`terminate()` 2808 删 job、`shutdown(wait=False)`。
- 三套 job（2958-3069）：`proactive_guard_gen`（`gen_time`，默认 `20 3 * * *`）、`proactive_guard_minute`（`* * * * *`）、`proactive_guard_memory`（`memory_cron`）；优先 `context.cron_manager.add_basic_job`，失败回退 `AsyncIOScheduler`（`_fallback_sched`）。
- 生成路径：`_ensure_schedule(force)` 3130（20 分钟节流 + `_gen_lock`）→ `_generate_and_store()` 3161（条数 `msg_count_min/max`=5..10，随机时间点，子代理流水线 4043 或兜底单次）→ KV `daily_plan` / `daily_plan_next`。
- 派发路径：`_on_minute()` 3098 → `_dispatch_due()` 3398 → `_send_silent()` 3556（+`_record_to_history` 3576）。

### 2.2 「一下子出来一大串」根因（已复现，非推断）
- **主因 = 派发端 catch-up 批量补发**：`_dispatch_due` 3415 取 `missed_grace_minutes`（线上 30），3418-3426 把 `[now−30min, now]` 内**所有**过期条目一起判 due；3445-3459 在**同一 tick 的 for 循环里逐条** `_send_silent`（条目间只 `sleep(0.3)`），整轮结束才写回 KV（3461-3467）。→ 用户侧就是「一下一大串」。
- **放大因素 = 无锁 + 注册不幂等**：`_dispatch_due` 全程无锁（`_on_minute` 3098-3104 只有 try/except），`cron_manager.add_basic_job` 每次新建 job_id（`manager.py:145-170`）→ 进程内存在两套分钟任务时，同一分钟各发一遍。
- 复现（`_scratch/repro_burst.py`，AST 抽真实源码 + 假 KV/假时钟/假发送）：A 场景 6 条计划、停机 23 分钟 → 首个 tick 一次连发 6 条（07:54:00.300→01.800）；B 场景双分钟任务 → 同一条发 2 次；C 场景 2000 次 `_random_times` 重复时间点 0 次（**时间点撞车假设不成立**）；D 对照组无停机 → 单 tick 最多 1 条。
- 线上证据：40 个 boot 段 / 282 条「已发送」，每段 5~10 条、无同 tick 多发、最大迟到 123 秒 → **潜伏 bug**：只要长停机或重复注册就爆。旁证：`cron_jobs` 表里 `proactive_guard_minute` 在 id=10 与 id=16 两代并存（靠 `persistent=0` 未被 `sync_from_db` 重排才没炸）；容器 3 天前重启；`guard_err` 里 20+ 条「无法获取可用的 LLM 提供商」。
- 结论：修法不能是「缩短 grace」这种调参，必须是**结构性**的——每个会话同一时刻最多一个待触发的主动消息，且触发时才生成。

### 2.3 记忆路径
- 存储：插件 KV 键（`MEMORY_KEY` / `daily_plan` / `daily_plan_next`），多会话分段 + `merged` 合并档案。
- 写入：`refresh_memory()` 3648-3743（定时 03:00 / 生成前刷新 / 命令）；召回注入：3188-3191 + `MEMORY_BLOCK_TEMPLATE` 320；派发时 3432-3456 每条消息一次 LLM 事实校验（成本高）。
- 用户判据（2026-10）：**记忆只记用户**（发生过的事、爱好偏好、正在做的事与进度、人际关系、情绪状态、约定），不记助手自体；一次性待办不写长期记忆。

### 2.4 人格注入
- `_resolve_persona()` 3260-3281 从 AstrBot `persona_manager.get_default_persona_v3()` 动态取，persona 正文 format 进 4 个提示词（261-317）。插件 config 的 `persona`/`world_setting` 两键**都是空**。
- **`main.py` 里没有「傲娇」二字**：傲娇完全靠人格正文自然涌现，代码侧无开关、无强度、无状态 → 阶段五要重构。

### 2.5 情感状态
- 当前为零：无任何 PAD / 情绪变量，没有生成前读取、没有发送后更新（grep 只命中提示词文字）。

### 2.6 阶段一必须处理点（P0/P1）
- P0：补发量上限（结构性单飞）、`_dispatch_due` 加锁 + 逐条写回、任务注册幂等（同一 job 名不重复注册）。
- P1：`_pause_active_agent_jobs` 每次 boot 都暂停（应只在未暂停时操作并记录状态）；生成与派发共享版本号（避免「用旧记忆预生成」再次出现）；单 tick 内 LLM 耗时（不要在一次 tick 里做多条 LLM 调用）。
- P3：情感（阶段三接入）。

---

## 3. 重叠功能判据（四维度，只保留综合占优者）

| 重叠项 | 拟人感贡献（可观测） | 复杂度 | 与现有代码耦合 | 可调试性 | 裁决 |
| --- | --- | --- | --- | --- | --- |
| 旧 `daily_plan` 每日预生成 | 低：内容与「此刻」脱钩（记忆滞后最长 ~24h，已两次出事故：万事达卡、台风） | 低 | 高（KV+子代理+事实校验全绑在它上面） | 差：要读 plan JSON 才知道「将要发什么」 | **局部保留**：作为「无 v2 调度时的降级路径」，v2 默认接管后不再新增功能 |
| 新「单会话决策循环 + 一次性触发」 | 高：触发时刻的情感/记忆是真实的 | 中 | 中（复用门禁/记忆/发送） | 好：每会话状态可 dump，job 名可查 | **保留（阶段一核心）** |
| `cron_manager` 三套 job | 低：只提供节拍，不提供状态 | 低 | 高（外部系统 + 不幂等） | 差 | **移除**：一律插件内部 `AsyncIOScheduler`，job_id 自管 |
| 每分钟兜底 tick 内批量派发 | **负**（爆发根因） | 低 | 高 | 差 | **删除** |
| 派发时逐条 LLM 事实校验 | 中高（防「提醒已办的事」） | 中（每条一次 LLM、串行） | 中 | 中 | **保留但降频**：只对「涉及用户待办/事实」的意图做校验，且移入 fire 生成阶段（触发时刻本来就是最新记忆） |
| KV 记忆（多会话分段 + merged） | 中高（人格外的「用户档案」） | 低 | 低 | 好 | 保留，阶段二被 Mem0 叠加（Mem0 做语义召回，KV 做长期档案摘要） |
| 子代理规划/写手/审校流水线 | 低（离线预生成场景才有意义） | 高（3 个 LLM 调用 + 并发） | 高 | 差 | **降级**：仅保留「写手 + 审校」用于单条生成，规划交给阶段四的候选意图机制 |
| `world_setting` 配置字符串 | 低（自由文本、易漂移） | 低 | 低 | 差 | 被 `assets/worldview.json` 取代（阶段一B） |
| AstrBot `persona_manager` 取人格 | 中（但运行时依赖外部状态，DB 改了行为就变） | 低 | 高 | 差 | 被 `assets/persona.json` 取代（阶段一B），保留只读兜底 |

四条否决理由（写进验收）：① 不为「完整性」留僵尸组件；② 任何新组件必须能说清「它如何改变最终一条消息」；③ 同一能力不允许两套实现并存（并存必须有一方默认关闭）；④ 修 bug 先结构性、后调参。

---

## 4. 阶段一实施契约（本次代码即按此落地）

- 调度：插件内部 `AsyncIOScheduler`（`initialize()` 启动，`terminate()` `shutdown(wait=False)` + 清 job），**不再注册 `cron_manager`**。
- 每活跃会话一个决策 job：`job_id=f"proactive_decision_{sid}"`，`replace_existing=True`，执行完自己排下一次（自我延续，无外部节拍）。
- 两阶段：决策 tick 只产「意图 + 时间偏移」，注册一次性 job（`proactive_fire_{sid}` 单槽 `replace_existing=True`，**同一会话永远最多一个待触发**）；触发时刻才读当前记忆（阶段三接 PAD）生成正文，**不预生成**。
- 状态持久化：插件数据目录 `proactive_state.json`（原子写），重启恢复；过期待触发一律丢弃，不做 catch-up。
- 并发：每会话一把 `asyncio.Lock` 覆盖「决策→生成→发送」全程；fire 用 token 幂等（重复触发/过期 token 直接跳过）。
- 安全开关：`living_enabled` 默认 **False**（关时完全走旧路径，行为不变）；开启后旧 `daily_plan` 派发自动停用。
- 时间逻辑：安静时段（配置，默认 23:30–07:30）不触发；触发偏移落在安静时段则顺延到时段结束；沉默越久触发概率越高（ramp），但有日上限与冷却，避免机械定时。
