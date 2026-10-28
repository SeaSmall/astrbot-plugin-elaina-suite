# 阶段一B / 阶段零-C：人格与世界观资源固化说明

> 产出：`assets/persona.json`、`assets/worldview.json`、`assets/persona_source/`（含 `MANIFEST.json`）。
> 原则：这两个 JSON 是运行时**唯一**的人格与世界观来源；运行时**不读外部文件、不比对哈希、不做热更新**。
> `assets/persona_source/` 只是开发阶段存档，代码不得读取。

## 1. 结论

- **主文档 = AstrBot DB 里 `persona_id=elaina_v2` 的 `system_prompt`**（193 行 / 6588 字），即 `assets/persona_source/astrbot_db/personas/elaina_v2.system_prompt.txt`。
  它自述为「伊蕾娜人格设定 · v2 融合版 = 原始人格设定 + 《魔女之旅》原著世界观，已融合为一份」，与任务要求的「人格设定文档（含世界观）」完全对应。
- 辅助源（只补细节，不改主文档基调）：`persona_source/.../PERSONA_ELAINA.md`（正典核验档案）、`.../SOUL.md`、`.../RUNTIME_GUARD.md`、`persona_source/ex-skill/exes/elaina/persona.md`（早期五层人格）、`personas/elaina_v2.begin_dialogs.txt`（20 条示例对白）。
- 插件自己的 `world_setting` 键是**空字符串**，所以世界观此前只存在于 DB 人格第八章里——这也是必须把它固化进 `worldview.json` 的原因。

## 2. 候选位置与选主文档的判断依据

服务器 `ssh.example.com:22`（用户 `alice`），容器 `astrbot`，AstrBot 数据根 `/opt/astrbot/data`。逐个排查过的候选：

| 候选 | 结论 |
| --- | --- |
| `data_v4.db` → `personas.elaina_v2.system_prompt` | **主文档**（见下 5 条证据） |
| `data_v4.db` → `personas.elaina` | 上一版（4102 字，被 v2 取代），存档备用 |
| `data_v4.db` → `personas.elaina_v2_notools` | prompt 与 v2 **字节完全相同**（sha 一致），只差 tools/skills 配置，合并处理 |
| `config/astrbot_plugin_elaina_suite_config.json` 的 `persona` / `world_setting` | 两键均为空字符串；且该文件含 API key，不存档，只记结论 |
| `skills/elaina-agent/assets/openclaw-workspace/PERSONA_ELAINA.md` | 正典核验档案（77 行），作辅助源 |
| `skills/ex-skill/exes/elaina/{persona,SKILL,memories,meta}` | 早期「前任.skill」五层人格，作风格与雷区辅助源 |
| `workspaces/*/user_profile.md` | 用户个人档案（学校、军训、住址等），**不是**人格设定，且涉隐私，丢弃 |
| `skills/elaina-agent/memory/persona-evidence.md`（440K） | 从逐篇概括机械提取的证据层，不是设定文档，丢弃 |
| 小说/文章概括（296 篇） | 原作语料，用于核验，不作为设定源 |

选 `elaina_v2` 的五条证据：
1. `cmd_config.json` 的 `provider_settings.default_personality` 就是 `elaina_v2`——它是**真正在跑**的人格。
2. `cmd_config['persona']` 是空数组，插件侧没有别的人格配置可覆盖它。
3. 文本自带结构：第零章三条铁律 → 身份锚定 → 性格内核 → 说话风格 → 伴侣关系 → 禁止事项 → 自查 → 风格示例 → **世界观内核** → 语音规则，正是「人格设定含世界观」。
4. v2 的 `begin_dialogs` 有 20 条，v1 只有 12 条，v2 是 v1 的扩写与融合。
5. 其第八章世界观与 `PERSONA_ELAINA.md` 正典档案逐条可对（译名少数不一致，见 §6）。

## 3. 字段 → 原文出处映射（`persona.json`）

| 字段 | 出处 |
| --- | --- |
| `name` / `display_name` / `title` / `aliases` | v2:L19-23；`PERSONA_ELAINA.md` L16-22 |
| `identity.*`（年龄、生日、身高、外貌、坐骑、自称、禁称） | v2:L19-23、L81、L99；`IDENTITY.md` |
| `traits`（11 条，覆盖 高傲自恋 / 本质温柔 / 毒舌 / 傲娇 / 腹黑 / 爱财 / 旅人自由 / 癖好逆鳞 / 自尊 / 分寸 / 多面性） | v2:L32-39、L72-76；`ex_persona.md` L7-14、L109-110；`PERSONA_ELAINA.md` L61-67 |
| `tone.length_rule`（25 字硬上限 / 例外 50 字） | v2:L11、L45-49 |
| `tone.particles` / `punctuation` / `sentence_patterns` | v2:L53、L54-62、L63-67 |
| `tone.address`（你 / 本魔女；禁妾身本小姐奴家；对旅伴不用「您」） | v2:L23、L27、L78-82 |
| `tone.emoji_policy` / `japanese_policy` | `SOUL.md`、`RUNTIME_GUARD.md`、`ex_persona.md` L41（两源冲突，见 §6） |
| `tone.calibration_examples`（含反面教材） | v2:L124-143 |
| `speech_habits`（13 条） | v2:L69-76；例证取自 `begin_dialogs` 1-20 |
| `tsundere.negation_patterns` | v2:L131；`dialogs#3-4`、`dialogs#7-8` |
| `tsundere.care_leak_patterns` | v2:L76、L93、L132；`dialogs#11-12` |
| `tsundere.forbidden_meta_phrases`（9 条） | 设计决定，见 §6 第 6 项 |
| `emotional_tendencies`（PAD 基线 + 14 条情境增量） | 由 v2:L32-39、L69-76 与 `begin_dialogs` 归纳；`ex_persona.md` L62-83 补充情绪逻辑 |
| `silence_reaction`（沉默五级阶梯 + 沉默当回复 + 被忽视） | v2:L46、L62、L130；`ex_persona.md` L78-79、L83；`dialogs#11-12`、`dialogs#20` |
| `relationship` | v2:L25-27、L87-94 |
| `prohibitions`（14 条） | v2:L99-108（10 条）+ 存档文档与运行时约束合并 4 条 |
| `source_map` / `source_index` | 每个字段逐条给出 `v2:L…` / `persona_elaina:L…` / `dialogs#N` 引用 |

## 4. 字段 → 原文出处映射（`worldview.json`）

| 字段 | 出处 |
| --- | --- |
| `setting.era_style` / `politics` / `geography` / `technology` | v2:L148-151；`PERSONA_ELAINA.md` L17、L51 |
| `setting.magic`（阶级、授名、星形胸饰、魔法统合协会、全能型、魔法是工具） | v2:L152-169；`ex_persona.md` L12；`PERSONA_ELAINA.md` L36、L55 |
| `setting.travel`（单元剧、多半路过、不是救世主、没有终点） | v2:L150、L183-186 |
| `setting.world_separation` | v2:L94 |
| `relationships`（9 条：用户、母亲、芙兰、沙耶、席拉、扫帚、艾姆妮西亚、艾维莉亚、艾丝黛儿） | v2:L22、L160-173；`PERSONA_ELAINA.md` L20、L45-52 |
| `environment_constraints`（12 条：无现代科技、异世界、非救世主、母亲三约、保留自由、不主动谈母亲、不用敬语、不臆造、不破幕后、语言、25 字上限…） | v2:L11、L27、L94、L105、L151、L172-186；`soul`、`guard` |
| `taboo_topics`（11 条：母亲与妮可、永远式承诺、肉麻直球、现代科技、现实资料、被小看、依赖魔法、平行世界恶役、限制自由、无聊对话、叫错名字） | v2:L91、L105、L108、L172-173；`ex_persona.md` L71-74、L109-119；`PERSONA_ELAINA.md` L74-77 |
| `facts`（34 条，作 Mem0 种子记忆） | 见 §5 |

## 5. facts 设计（Mem0 种子）

- 共 **34 条**，每条严格 `{id, fact, tags, importance}`；`tags` 供后续检索过滤，`importance` 0.5–0.95 供记忆衰减/优先级。
- 只收**世界观内的客观事实**（世界结构、技术上限、魔法体系、人物关系、她自己可被第三方陈述的经历），不收「你很傲娇」这类人格描述——人格描述在 `persona.json` 的 `traits`。
- 覆盖：世界结构 5 条、魔法体系 8 条、她本人 7 条、人物关系 11 条、与用户的关系 2 条、证伪护栏 1 条。
- 低重要度条目（w30 母亲孤儿院往事 0.5、w31 父亲细节 0.55、w10 胸饰丢失 0.6）都是**单一来源**，只在 `PERSONA_ELAINA.md` 或只用 v2 记载，标低以免被当成高频复述的事实。
- 刻意保留一条「证伪护栏」w34：原作没有「最年轻成为魔女」只有「最年轻通过魔术测验」；母亲名不可写作「维多利亚」。这是给记忆层的负面约束，防止后续把误传当事实写进 Mem0。

## 6. JSON 与原文的差异清单（有意偏离）

1. **长度规则结构化**：原文用三段散文讲「≤25 字 / 例外 50 字 / 禁列表」，JSON 拆成 `hard_max_chars`、`exception_scenes`、`forbidden`、`self_check`；语义等价。
2. **`forbidden_meta_phrases` 是新增的设计禁止项**，其中 fmp01「才不是特意…」按本阶段任务要求明令禁止。注意它在早期人格 `ex_persona.md` L7 里确实是原文傲娇原型句（「我才不是特意来帮你的」）——**这是有意偏离旧文档**：主文档 v2 强调「伊蕾娜从不解释」，把傲娇机制说出来等于向用户解释自己在演什么，且极易模板化。允许的否定方式改由 `negation_patterns`（才没有 / 顺路而已 / 毒舌+行动 / 冷淡短句）承担。
3. **emoji 政策取折中**：`ex_persona.md` L41 明令「不要用 emoji」；`SOUL.md` 说「🧹 偶尔可用不是签名」；v2 只禁「颜文字堆砌」。JSON 取「默认不用，仅允许偶尔 🧹，且不连续两条重复」。
4. **世界关系取「不同世界但不假装」**：v2 同时写了「用户是你的旅伴 / 平稳期伴侣」与「你与用户身处不同世界」。JSON 不改写这两句，而是把前者放进 `relationship`，后者放进 `identity.world_separation` 与 `worldview.setting.world_separation`，并以此约束「不把现实世界新事实当成她亲历」。
5. **PAD 是推导出来的**：原文没有数值情绪模型。基线 `pleasure 0.15 / arousal 0.25 / dominance 0.70` 与 14 条情境增量，是从「略带自恋 + 平稳期伴侣 + 魔女自尊」与示例对白反推的**可校准初值**，阶段三可直接替换数值而不动措辞。
6. **`begin_dialogs` 未整段搬运**：20 条对白只作为 `examples` 分散引用（`dialogs#N`），原文完整保存在存档目录。
7. **`IDENTITY.md` 的身份叙事被丢弃**：它说「因魔法事故困在聊天软件里当 AI 助手」，与主文档第零章「禁止自称 AI」直接冲突；只取其功能约束（静默检索、不提 Skill）。
8. **人物关系的取舍**：`ex_persona.md` 有「你们一直在一起」「恋爱关系」的强绑定叙事，与 v2「旅人保留随时上路的自由」并不完全一致；JSON 以 v2 为准，情侣感归入 `relationship` 的平稳期描写，不写「永远在一起」。

## 7. 丢弃的内容与原因

| 丢弃 | 原因 |
| --- | --- |
| `config/astrbot_plugin_elaina_suite_config.json` | 含 provider API key；只记录证据（`persona`、`world_setting` 均为空字符串） |
| `workspaces/*/user_profile.md` | 用户隐私档案，非人格设定 |
| `skills/elaina-agent/memory/persona-evidence.md`（440K） | 机械提取的证据层，非设定文档 |
| 296 篇小说/概括语料 | 原作语料，只用于核验，不作运行时资源 |
| `AGENTS.md` / `SKILL.md` / heartbeat / memory 协议 | 宿主运行规范，与「拟人聊天」运行时无关 |
| `ex_persona.md` 的口头禅「我去举报一下哦」「我觉得我还挺行的」 | 主文档 v2 中不存在，属旧版本残留，丢弃以免人格漂移 |
| v1 人格文本 | 已被 v2 覆盖，仅存档 |

## 8. 发现的可疑点（原文自相矛盾 / 与现有配置冲突）

1. **地名译名**：主文档 v2「和平国**洛**贝塔」；`PERSONA_ELAINA.md`「和平国**罗**贝塔」。同一地，译名不同，JSON 两说并存并注明。
2. **母亲名与称号**：v2「**维多**利加（称号：**谜之魔女**）」；`PERSONA_ELAINA.md`「**薇多**利加（上一任**灰之魔女**）」。名与称号两处冲突。JSON 采用 v2 正文，保留档案译名作 alias，并把「上一任灰之魔女」这条降级不进 facts（正典档案自身也提醒称号须回查授名段落）。
3. **同世界 / 跨世界张力**：v2 第四章前半说用户是旅伴与伴侣，同章末却说「你与用户身处不同世界」，加上插件 `world_setting` 为空——三处并置。阶段一B 的折中见 §6 第 4 项；若后续要改成「同居一个世界」，只需改 `world_separation` 与 facts `w32`。
4. **emoji 禁令冲突**：`ex_persona.md` 禁 emoji，`SOUL.md` 允许偶尔 🧹，v2 只禁颜文字堆砌。
5. **性格侧重差异**：`ex_persona.md` 强调「腹黑、爱捉弄」，v2 强调「优雅从容、略带自恋」。两者都保留为 `trait`（t01 / t05），但权重不同（0.75 / 0.5）。
6. **「最年轻」表述已被自身证伪**：`PERSONA_ELAINA.md` 的证伪清单明确否定「最年轻成为魔女」，只承认「最年轻通过魔术测验」；同时该文档内部年龄线有混用表述。JSON 采用 14 岁见习、15 岁正式魔女。
7. **架构本身的旧假设**：插件现有 config 里 `persona` 与 `world_setting` 都是空字符串，说明此前人格完全依赖 DB 注入——本阶段把两者固化进 `assets/`，需要后续阶段把注入逻辑改为读 JSON（不在本任务范围，未改 `main.py`）。

## 9. 校验与复现

- `json.load` 通过；UTF-8 无 BOM；缩进 2；`persona.json` 46016 字节（20 个顶层键，traits 11 / speech_habits 13 / PAD 规则 14 / 禁用短语 9）；`worldview.json` 30363 字节（facts 34 / relationships 9 / constraints 12 / taboo 11）。
- 存档 18 个文件，`MANIFEST.json` 记录每个文件的远程来源、字节数、sha256、编码与 BOM 标记；`elaina_v2` 与 `elaina_v2_notools` 的 prompt 逐字节一致（sha 相同），可佐证「只有一份实质人格文本」。
- 运行时约束：`assets/persona_source/` 仅为存档，读取它即违反本阶段设计。
