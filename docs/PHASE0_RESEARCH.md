# 阶段零 · 论文对齐备忘录

> 核实人：researcher（团队资料核实员）｜核实日期：2026-10-28｜适用：AstrBot 拟人聊天插件重构 · 阶段零
> 本文只做「出处核实 + 原文摘录 + 硬约束」，不含代码。
> 标记约定：**「原文」**= 论文/文档里明确写的句子（引号内为原文，英文照抄）；**[推断]**= 我基于原文做的工程推论，不是原文结论。
> 工具限制：`web_fetch` 工具本次全部失败（TypeError: fetch failed），改用 `curl` + arXiv API + Europe PMC/Crossref API 取全文与元数据；`docs.mem0.ai` 三种方式均不可达（见 §3.5，已如实标注）。

---

## 1. Sentipolis（用户口中的「PAD 情感框架」）

### 1.1 结论：**真实存在，已核实到确切出处**（不是查不到的杜撰名）

| 项 | 内容 |
|---|---|
| 标题 | Sentipolis: Emotion-Aware Agents for Social Simulations |
| 作者 | Chiyuan Fu, Lyuhao Chen, Yunze Xiao, Weihao Xuan, Carlos Busso, Mona T. Diab |
| 预印本 | arXiv:2601.18027（v1 2026-01-25；v2 2026-04-21），DOI 10.48550/arXiv.2601.18027 |
| 正式发表 | Findings of ACL 2026（Findings 卷），pp. 7465–7494，San Diego，2026-07 |
| 项目页 | 论文未给出独立项目主页/GitHub（我未检索到） |
| 链接 | [arXiv](https://arxiv.org/abs/2601.18027) ｜ [ACL Anthology](https://aclanthology.org/2026.findings-acl.368/) ｜ [DOI](https://doi.org/10.18653/v1/2026.findings-acl.368) |

摘要原文（arXiv）：*"Existing LLM-based agents for social simulation often treat emotion as a transient cue ... which leads to 'emotional amnesia' and weak long-horizon continuity ... Sentipolis integrates a continuous PAD representation, dual-speed emotion dynamics, and emotion–memory coupling."* 论文在「Thousands of interactions、多 base model / 多评估者」的大规模实验上做评估，结论是增益具有**模型依赖性**（高容量模型情感可信度上升，小模型可能下降），且情感意识会**轻微降低**社会规范遵循度。

> 名称辨析 [推断]：用户称其为「PAD 情感框架」。PAD 只是它三层设计（连续 PAD 表示 / 双速动力学 / 情感-记忆耦合）中的第一层，把它当「PAD 论文」会漏掉后两层——那两层才是它区别于普通 PAD 实现的地方。

### 1.2 PAD 向量维度与取值范围（原文）

- **原文**：*"Each agent maintains a continuous emotion state in the Pleasure–Arousal–Dominance (PAD) space ... where each dimension lies in [−1, 1]."*
- 即 3 维 `[P, A, D]`，每维 ∈ [−1, 1]，中性点为 `[0, 0, 0]`。
- **原文**：PAD 三维依据是 Mehrabian (1996)；离散情绪标签通过 **KNN（k=3, minkowski p=2）** 在真人 PAD 数据（MSP-Podcast，原始量纲 0–7 归一化到 [−1,1]，264,705 个数据点）上反查 Plutchik 标签，且**不做多数投票**，把检索到的全部标签都送入下游 prompt。
- [推断] 实现上 PAD 是「连续状态 + 每轮 LLM 输出增量」的结构，而不是让 LLM 直接吐情绪标签——这正是它能维持长程连续性的原因。

### 1.3 双速（fast / slow）动力学与时间尺度（原文）

- **慢通道的触发条件不是「多少轮/多少分钟」，而是事件累积量**：
  - **原文**：*"For slow inference, we incorporate emotional updates into the reflection mechanism from Generative Agents (Park et al., 2023) ... Reflections are generated periodically; in our implementation, a reflection is triggered when the cumulative poignancy scores of recent events exceed a threshold (set to 150)."*
  - 即：近期事件 poignancy 累加 > **150** 触发一次 reflection，reflection 内完成慢通道情感更新。
- **快通道**：
  - **原文**：*"For fast inference, after each round of conversation, an immediate emotional update is triggered for both participants."*
  - 即：**每轮对话一次**即时情感更新（双人对话场景）。
- **仿真时间尺度（可用于换算）**：*"Simulations start at 2025-02-13 08:00, run for T = 36 steps, and advance time by a stride of Δ = 20 minutes per step, yielding a total simulated duration of 720 minutes (12 hours)."*，25 个 agent。
- 慢通道内部流程（原文 §3.2）：检索近期重要记忆 → 生成 focus questions → 每个 focus 做记忆检索（约 30 条/区）→ 用 insight prompt 合成 high-level observations → 每条 insight 按 1–10 的 poignancy 打分并作为新 thought 存入 → 聚合 insights + 全量人格 profile + 当前 PAD baseline 交给 LLM，输出各维 **delta**。
- [推断] 对聊天插件的直接含义：**快通道 = 每轮**；慢通道不要硬编码成「N 轮」，而应实现成**可配置的累积重要度阈值**（默认 150 可作起点，需按消息密度标定）。

### 1.4 衰减函数与半衰期（原文）

- **原文**：*"Let the PAD state be a vector s(t) = [P(t), A(t), D(t)]. We apply a half-life T½ decay at every time step."*
- **公式**：`s(t + Δt) = s(t) · 2^(−Δt / T½)`
- **原文**：*"We apply exponential decay of emotion states toward neutrality at every time step. Emotion decays with a half-life of 120 minutes."*
- 即 **T½ = 120 分钟**，且衰减在**每个 time step 无条件施加**（不做「有没有交互」的分支）。
- [推断] 这是指数衰减而非线性衰减；实现时必须按「真实经过时间 Δt 折算」，而不是「每轮固定衰减一个系数」，否则长停摆后情绪会衰减不足。

### 1.5 原文对「不活跃时期（无交互）情感行为」的定义（原文）

- **原文（§6.2 / §7 讨论）**：*"Moderate modularity suggests groups form but emotional decay keeps them from becoming overly insular by softening strong feelings during inactive periods."*
- 另有：*"while emotional decay prevents the network from becoming fixed"*。
- **重要边界**：论文**没有**为「无交互期」单独设计机制（没有 idle 状态机、没有离线补算）；它对不活跃期的全部处理就是上面那条「每个 step 无条件向中性衰减」。[推断] 也就是说，Sentipolis 对「长时间没人说话」的答案是**衰减本身**，而不是另一套规则——实现时若想做「静默超时后重置/回调」属于超出原文的新增，需显式标注。
- 消融佐证（Table 3 方向）：去掉衰减（No-decay）主要损伤 **Continuity(CON)**，去掉情感-记忆耦合（No-coupling）损伤记忆相关指标 → 说明「衰减 + 耦合」共同构成连续性来源。

### 1.6 备选原始文献（仅在「不采用 Sentipolis 三层设计」时才用，**非原文替代**）

- 若项目最终退化成「纯 PAD 情绪状态机」，理论底座应引：**Mehrabian, A. (1996). Pleasure-arousal-dominance: A general framework for describing and measuring individual differences in temperament.** *Current Psychology*, 14(4), 261–292. — [DOI](https://doi.org/10.1007/BF02686918)
- 其记忆/反思机制出处是 **Park et al. (2023), Generative Agents**（Sentipolis 明确引用），若要引「反思触发」原始设计应引它而不是转引 Sentipolis。
- ⚠️ 标注要求：以上两条是**备选/上游底座**，不是 Sentipolis 原文；交付物引用时必须写明「本例未采用 Sentipolis，改用 …」。

---

## 2. 全局工作空间（GWA）+ 内在动机（学习进度门控）

### 2.1 推荐组合与一句话理由

- **推荐**：GWA 用 **Mashour, Roelfsema, Changeux & Dehaene (2020, Neuron)** 的机制描述做「竞争-点火-广播」三段依据；内在动机用 **Oudeyer & Kaplan (2007, Front. Neurorobot.)** 的 **learning progress（学习进步）** 做门控；计算蓝本用 **Shanahan (2006)**——它把「全局工作空间 + 由 affect 介导的动作选择」拼成一个可落地的认知架构，与本次「候选意图评分-选择」形态几乎同构。
- 一句话理由：三者分别回答**「谁能进焦点」（竞争+点火+bottleneck）、「什么时候才值得学」（学习进步而非纯预测误差）、「选谁去做」（affect 介导的 action selection）**，刚好覆盖轻量实现需要的三个决策点，且都无需训练神经网络。

### 2.2 GWA：候选来源与摘录（原文）

1. **Dehaene, Kerszberg & Changeux (1998)**, *A neuronal model of a global workspace in effortful cognitive tasks*, PNAS 95(24):14529–14534 — [DOI](https://doi.org/10.1073/pnas.95.24.14529)｜[PMC24407](https://pmc.ncbi.nlm.nih.gov/articles/PMC24407/)
   - **原文**：*"It distinguishes two main computational spaces: a unique global workspace composed of distributed and heavily interconnected neurons with long-range axons, and a set of specialized and modular perceptual, motor, memory, evaluative, and attentional processors. Workspace neurons are mobilized in effortful tasks for which the specialized processors do not suffice. They selectively mobilize or suppress, through descending connections, the contribution of specific processor neurons."*
   - 价值：给出「**工作空间只在专用处理器搞不定时才被动员**」——轻量实现里可理解为「常规寒暄走快路径，只有需要整合多路信息时才开竞争」。
2. **Mashour, Roelfsema, Changeux & Dehaene (2020)**, *Conscious Processing and the Global Neuronal Workspace Hypothesis*, Neuron 105(5):776–798 — [DOI](https://doi.org/10.1016/j.neuron.2020.01.026)｜[PMC8770991](https://pmc.ncbi.nlm.nih.gov/articles/PMC8770991/)
   - **原文**：*"At any given moment, this architecture can select a piece of information within one or several processors, amplify it, and broadcast it to all other processors, thus rendering it consciously accessible and available for verbal report."*
   - 价值：**「select → amplify → broadcast」三步**就是本次意图选择的骨架。
3. **Baars (2005)**, *Global workspace theory of consciousness: toward a cognitive neuroscience of human experience*, Prog. Brain Res. 150:45–53 — [DOI](https://doi.org/10.1016/S0079-6123(05)50004-9)
   - **原文**：*"Newell and co-workers were the first to show the utility of a GW or 'blackboard' architecture in a distributed set of knowledge sources, which could cooperatively solve problems that no single constituent could solve alone."*
   - 价值：GWT 的「黑板书」隐喻，是「候选意图写黑板 → 竞争取用」的最朴素形式。
4. **Shanahan (2006)**, *A cognitive architecture that combines internal simulation with a global workspace*, Consciousness and Cognition 15(2):433–449 — [DOI](https://doi.org/10.1016/j.concog.2005.11.005)
   - **原文**：*"the architecture adopts a model of information flow from global workspace theory. Cognitive functions such as anticipation and planning are realised through internal simulation of interaction with the environment. Action selection, in both actual and internally simulated interaction with the environment, is mediated by affect."*
   - 价值：**同时把「工作空间」和「affect 参与选择」写清楚的计算架构**——正是「PAD 情绪 + 意图竞争」想要的东西。

### 2.3 注意力选择规则（原文可摘的竞争机制）

- **原文（Mashour 2020）**：*"Attentional signals select a particular piece of information by amplifying its activity and reducing that of other competing stimuli, whereas persistent neuronal activity keeps information online in working memory."*
- **原文（Mashour 2020，点火）**：*"Ignition is characterized by the sudden, coherent, and exclusive activation of a subset of workspace neurons coding for the current conscious content, with the remainder of the workspace neurons being inhibited."*
- **原文（Mashour 2020，瓶颈）**：*"a structural bottleneck capable of routing information between other cortical processors"*；同段还写 *"This distributed population of neurons is postulated to possess the ability to receive bottom-up information from and transmit top-down information to the rest of the brain"* 与 *"selectively mobilize or suppress, through descending connections, the contribution of specific processor neurons."*
- 合起来的选择规则（原文归纳，非我发明）：**① 注意力增益 = 放大被选者、压低竞争者；② 点火 = 排他性（子集全激活、其余被抑制）；③ 瓶颈 = 全局广播前只有一个赢家**。[推断] 落成代码就是「每个候选意图打分 → argmax/softmax → 只有一个 winner 进入全局上下文 → 同时压低其余候选优先级」，天然反对「多意图并行输出」。

### 2.4 内在动机：候选来源与「学习进度门控」摘录（原文）

1. **Oudeyer & Kaplan (2007)**, *What is intrinsic motivation? A typology of computational approaches*, Front. Neurorobot. 1:6 — [DOI](https://doi.org/10.3389/neuro.12.006.2007)｜[PMC2533589](https://pmc.ncbi.nlm.nih.gov/articles/PMC2533589/)
   - **原文**：*"it consists in modeling intrinsic motivation with a system that generates rewards when predictions improve over time. Thus, the system will try to maximize prediction progress, i.e., the decrease of prediction errors. Prediction progress has also been referred as 'learning progress'."*
   - **学习进步的可落地公式（原文 Eq.11）**：`r(SM(→t)) = ⟨Er_Rn(t−θ)⟩ − ⟨Er_Rn(t)⟩`
     - 含义：在**同一情境区域 Rn 内**，用「τ 次预测的历史平均误差」减去「当前平均误差」作为内在奖励；区域数量与边界要**自适应更新**。
   - **原文（重要反例警告）**：naive 实现（直接比较 t 与 t−θ 两个窗口）*"is in fact nonsense"*——会把「leaf 乱飘的不可预测」误判成高进步，所以必须按区域（region）比较。
2. **Kaplan & Oudeyer (2007)**, *In search of the neural circuits of intrinsic motivation*, Front. Neurosci. 1(1):225–236 — [DOI](https://doi.org/10.3389/neuro.01.1.1.017.2007)｜[PMC2518057](https://pmc.ncbi.nlm.nih.gov/articles/PMC2518057/)
   - **原文**：*"The central hypothesis of this paper is that intrinsically motivating activities correspond to expected decrease in prediction error. This motivation system pushes the infant to avoid both predictable and unpredictable situations in order to focus on the ones that are expected to maximize progress in learning."*
   - 价值：**「既避开太可预测、也避开不可预测，只挑学习进步最快的那一档」**——正是「带学习进度门控的好奇心」的直接表述；另提出 tonic dopamine 充当 learning progress signal 的假设。
3. **Oudeyer, Kaplan & Hafner (2007)**, *Intrinsic Motivation Systems for Autonomous Mental Development*, IEEE TEC 11(2):265–286 — [DOI](https://doi.org/10.1109/TEVC.2006.890271)
   - IAC（Intelligent Adaptive Curiosity）的出处；本文的「区域划分 + 学习进步」正是在 IAC 基础上讨论的。
4. **Schmidhuber (2010)**, *Formal Theory of Creativity, Fun, and Intrinsic Motivation (1990–2010)*, IEEE TAMD 2(3):230–247 — [DOI](https://doi.org/10.1109/TAMD.2010.2056368)；开放预印本 [arXiv:0812.4360](https://arxiv.org/abs/0812.4360)
   - **原文（摘要）**：*"data becomes temporarily interesting by itself to some self-improving, but computationally limited, subjective observer once he learns to predict or compress the data in a better way ... Curiosity is the desire to create or discover more non-random, non-arbitrary, regular data ... in the sense that it allows for compression progress ... This drive maximizes interestingness, the first derivative of subjective beauty or compressibility, that is, the steepness of the learning curve."*
   - 价值：与 Oudeyer 的 learning progress 同构（**压缩进步 = 学习曲线斜率**），可作为「进步用压缩率/困惑度下降近似」的理论依据。
5. **Pathak, Agrawal, Efros & Darrell (2017)**, *Curiosity-driven Exploration by Self-supervised Prediction*, arXiv:1705.05363（ICML 2017）— [arXiv](https://arxiv.org/abs/1705.05363)
   - **原文**：*"We formulate curiosity as the error in an agent's ability to predict the consequence of its own actions in a visual feature space learned by a self-supervised inverse dynamics model."*
   - 定位：**纯预测误差型 curiosity（无学习进步门控）**，正好是需要被门控修正的反例（易被 noisy TV / 不可学习的随机噪声长期吸引）。

### 2.5 为什么推荐这一组 + 到轻量实现的映射

| 决策点 | 采用来源 | 落成什么（[推断] 的工程映射） |
|---|---|---|
| 谁能进焦点 | Mashour 2020（放大/排他/bottleneck） | 候选意图各自打分 → 只放 **1 个** winner 进全局上下文，其余降权 |
| 何时该开竞争 | Dehaene 1998（处理器搞不定才动员） | 常规回复走快路径；检测到多路冲突/新信息才开竞争 |
| 何时才「好奇」 | Oudeyer & Kaplan 2007（Eq.11） | 维护「话题/情境区域」的误差历史，只在**进步为正**的区域给好奇加成 |
| 情绪如何参与选择 | Shanahan 2006（affect 介导 action selection） | PAD 状态作为打分项之一进入候选意图评分 |
| 选谁去做 | 上表四者合成 | 评分-选择候选意图，不做多意图并行 |

- 选型理由一句话：**GWA 给「排他性选择」的合法性，learning progress 给「好奇心只在学得动的地方开启」的刹车，Shanahan 给「情绪进选择」的接口**——三者在同一套打分-选择循环里能共存，且都不需要训练。

---

## 3. Mem0（当前版本 / API / OSS 最小配置）

### 3.1 版本与包名（已核实 PyPI 元数据）

| 项 | 值 |
|---|---|
| PyPI 包名 | **`mem0ai`**（不是 `mem0`；import 名是 `mem0`） |
| 最新版本 | **2.2.1**（上传 2026-09-25） |
| Python 要求 | `>=3.10, <4.0` |
| 链接 | [PyPI](https://pypi.org/project/mem0ai/) ｜ [GitHub](https://github.com/mem0ai/mem0) ｜ [官方文档](https://docs.mem0.ai/) |

- 核心安装即带的依赖（非 extras）：`httpx>=0.28.0`、`openai>=1.90.0`、`posthog>=7.14.0`、`protobuf>=5.29.6,<7.0.0`、`pydantic>=2.7.3`、`pytz>=2024.1`、`qdrant-client>=1.12.0`、`sqlalchemy>=2.0.31`。
- 本机状态（旁证，非结论依据）：`pip show mem0ai` 未安装、`import mem0` → `ModuleNotFoundError`；§3.2–3.4 的签名与默认值均来自 **2.2.1 源码**（`mem0/memory/main.py`、`mem0/configs/base.py`、`mem0/__init__.py`）。
- 导出（`mem0/__init__.py`）：`Memory`、`AsyncMemory`（OSS），`MemoryClient`、`AsyncMemoryClient`（托管平台 API）。

### 3.2 OSS 默认配置：**不是纯本地**（本次最大的预期偏差）

- `MemoryConfig` 默认值（`mem0/configs/base.py`）：`vector_store.provider = "qdrant"`、`llm.provider = "openai"`、`embedder.provider = "openai"`、`history_db_path = ~/.mem0/history.db`（可用 `MEM0_DIR` 覆盖）、`version = "v1.1"`。
- Qdrant 默认 `path = "/tmp/qdrant"`（**嵌入式本地模式，不需要起 server**）、`embedding_model_dims = 1536`、`collection_name = "mem0"`；校验器要求 `path` 或 `host+port` 或 `url+api_key` 三选一。
- **默认能跑通、但会调用 OpenAI 的 LLM 与 embedding**；`history.db` 是 **sqlite**，但只存变更历史（SQLAlchemy），**不是向量检索后端**。
- 纯本地可行的组合 [推断，依据源码 provider 注册表]：`vector_store = {"provider":"qdrant","config":{"path":"./data/qdrant"}}` 或 `faiss`（需 `pip install "mem0ai[vector-stores]"`）+ `llm = {"provider":"ollama", ...}` + `embedder = {"provider":"ollama"|"huggingface"|"fastembed", ...}`。
- **遥测默认开启**：`mem0/memory/telemetry.py` 中 `MEM0_TELEMETRY = os.environ.get("MEM0_TELEMETRY", "True")`，默认发往 `https://us.i.posthog.com`。要干净必须 `MEM0_TELEMETRY=False`。

### 3.3 API 签名（2.2.1 同步版；`AsyncMemory` 一一对应，全部有 `async` 版本）

```python
Memory.from_config(config)                                    # classmethod
add(messages, *, user_id=None, agent_id=None, run_id=None, metadata=None,
    timestamp=None, expiration_date=None, infer=True, memory_type=None, prompt=None)
search(query, *, top_k=20, filters=None, threshold=0.1, rerank=False,
       explain=False, reference_date=None, show_expired=False, **kwargs)
get_all(*, filters=None, top_k=20, show_expired=False, **kwargs)
update(memory_id, text=None, metadata=None, expiration_date=_UNSET, data=None)
delete(memory_id)
delete_all(user_id=None, agent_id=None, run_id=None)
get(memory_id)  /  history(memory_id)  /  reset()  /  chat(query)
```

返回结构（源码）：

| 方法 | 返回 |
|---|---|
| `add` | `{"results": [{"id", "memory", "event": "ADD"|"UPDATE"|"DELETE"|"NONE"}]}` |
| `search` | `{"results": [{"id", "memory", "score", ...}]}` |
| `get_all` | `{"results": [{"id","memory","hash","created_at","updated_at","user_id"/"agent_id"/"run_id"/"actor_id"/"role"/"attributed_to"/"expiration_date","metadata"}]}` |
| `update` | `{"message": "Memory updated successfully!"}` |
| `delete` | `{"message": "Memory deleted successfully!"}` |
| `delete_all` | `{"message": "Memories deleted successfully!"}` |

### 3.4 三个必踩的坑（源码级，务必写进实现）

1. **`search()` / `get_all()` 不再接受顶层 `user_id`/`agent_id`/`run_id`，传了直接 `raise ValueError`**；必须写 `filters={"user_id": "u1"}`。docstring 原文：*"`search()` and `get_all()` scope queries via `filters={...}` — they reject top-level `user_id`/`agent_id`/`run_id` arguments."*（`add()` 仍收顶层，二者不对称。）
2. `filters` 支持 `eq/ne/in/nin/gt/gte/lt/lte/contains/icontains/*` 与 `AND/OR/NOT` 组合；`add()` 的 `timestamp` 是**平台专属**参数，OSS 传会 `ValueError`。
3. `delete_all()` 在 `user_id/agent_id/run_id` 全空时抛错，要求改用 `reset()`；`update()` 至少给 `text`/`metadata`/`expiration_date` 之一，否则 `ValueError`；`update(data=...)` 是 `text` 的**弃用别名**。

### 3.5 官方文档 URL 与访问限制（不隐瞒）

- 文档入口：[https://docs.mem0.ai/](https://docs.mem0.ai/)，OSS Python quickstart 路径 `https://docs.mem0.ai/open-source/python-quickstart`。
- **状态：本次无法访问**。直连、本机代理（127.0.0.1:7897）、无代理三种方式 `curl` 均返回 `000`（无响应，DNS/TLS 失败），因此**上面所有 API 细节均取自 2.2.1 源码，而非文档页面正文**。可访问的权威镜像：[GitHub 仓库](https://github.com/mem0ai/mem0)、[PyPI 页](https://pypi.org/project/mem0ai/)（均 HTTP 200）。
- 若要引官方文档作为交付物引用，请先用能打开 docs.mem0.ai 的网络复核一遍 §3.3 签名（版本 2.2.1 与文档站可能不同步，尤其 filters 这处新变更）。

---

## 4. 本期**不实现**清单（明确写死，防止范围蔓延）

| 项 | 本期处置 | 理由 |
|---|---|---|
| **RGWM 多尺度内部模拟** | 不实现 | 需多层世界模型 + 内部 rollout，无训练预算；Shanahan 2006 的「内部模拟」只取「选择时参考 affect」的思想，不做模拟器 |
| **SARSI 完整递归自我改进循环** | 不实现 | 递归自改需可写策略与自评闭环，风险与成本超阶段零；本期只保留「人在环」的离线复盘 |
| **ASURA 能力创生循环** | 不实现 | 涉及自动生成新能力/工具并自举，无安全边界与评测集，一律不做 |

> 三项均**不引入依赖、不预留接口占位**；若未来要做，需单独立项并补安全评审。

---

## 5. 给实现的硬约束（每条一行、可执行）

1. PAD 状态用 3 维浮点 `[-1,1]`，落库为 REAL×3，**不要**存成离散情绪字符串。
2. 衰减必须按**真实经过时间**算：`s *= 2 ** (-Δt_minutes / 120)`，禁止「每轮固定乘系数」。
3. 快通道：**每轮对话**结束即做一次 PAD 增量更新（双人对话双方都更新）。
4. 慢通道：以**累积重要度阈值**触发（poignancy 累计 > 150 为默认，配置项可调），**不要**写死成「每 N 轮」。
5. 无交互期**不引入额外状态机**——按原文只用连续衰减；若要加「静默回调」必须显式标注为超出 Sentipolis 的新增。
6. PAD → 离散情绪的映射若做，用 KNN 近邻（k=3）在 PAD 语料上检索，**不做多数投票**（保留多标签给下游）。
7. 每轮只允许 **1 个**候选意图成为 winner（排他性点火），其余候选降权而非删除。
8. 常规回复走快路径；仅当「多路信息冲突 / 出现未见过的话题」才开全局竞争（Dehaene 的「搞不定才动员」）。
9. 好奇心奖励用**学习进步**：`progress = 历史平均预测误差 − 当前平均预测误差`，按话题/情境区域分别统计。
10. 太熟（误差已很低）与太乱（误差高且不降）的区域**都不给**好奇加成——两个方向都要压。
11. 预测误差用可离线计算的代理量（如 perplexity / 语义距离 / 重复率），**不引入任何训练**；阈值必须可配。
12. Mem0 一律 `filters={"user_id": ...}` 查询；禁止给 `search()/get_all()` 传顶层 `user_id`。
13. Mem0 必须设 `MEM0_TELEMETRY=False`；开发环境用嵌入式 `qdrant path=./data/qdrant`，不新起容器。
14. 若坚持纯本地：`llm`/`embedder` 全部切本地 provider，否则默认会打 OpenAI。
15. 记忆写入走 `infer=True` 的默认推理去重，禁止每轮无条件 `add` 原始消息（会产生重复记忆）。
16. 所有新行为（衰减、门控、选择）都必须有**开关**，默认关闭时行为与当前版本一致。

---

## 附录 A：核实方法与检索证据（可复核）

- **Sentipolis**：`web_search` 关键词 `Sentipolis`、`Sentipolis PAD emotion agent` → 命中 arXiv 2601.18027 与 ACL 2026 findings 页；`curl https://arxiv.org/abs/2601.18027` 取 HTML；`curl https://arxiv.org/pdf/2601.18027` 下载 PDF，`pdftotext` 转纯文本后逐条摘引（§1.3–1.5 引号均出自该 PDF 正文，非二手摘要）。
- **Mem0**：PyPI JSON API（`https://pypi.org/pypi/mem0ai/json`）取版本/依赖；GitHub raw 取 `mem0/memory/main.py`、`mem0/configs/base.py`、`mem0/memory/telemetry.py` 源码；本机 `pip show mem0ai`、`import mem0` 佐证未安装。
- **GWA / 内在动机**：Crossref API（`api.crossref.org`）+ Europe PMC（`ebi.ac.uk/europepmc/webservices/rest/...`）核验 DOI 与卷期页；Mashour 2020 与 Kaplan 2007 取到 PMC 全文 XML 后抽取原句；arXiv API 核对 1705.05363 / 0812.4360 的标题、作者、摘要。
- **URL 可达性**：上表所有 `doi.org` 链接均 `curl` 验证（200/202）；`10.1073/pnas.95.24.14529` 直连返回 000（PNAS 侧拦截），已改用 PMC24407 作为可访问佐证。

## 附录 B：未能核实的项（如实列出）

1. `docs.mem0.ai` 正文未取到（三次尝试均 000）→ §3.3 的「官方文档是否与 2.2.1 源码一致」**未核实**。
2. Sentipolis **无公开项目页/GitHub**（未检索到），代码级细节（如 reflection 的具体 prompt）只能依据论文附录。
3. Mem0 的 `faiss` provider 是否在 2.2.1 的 extras 中且无需额外系统依赖：**未在本机安装验证**（本机无 mem0ai），仅为源码注册表 + 文档常识推断，标 [推断]。
