# 阶段十一：智能分段合并 + 说话连贯

> 版本：v1.12.0（2026-11-09）
> 触发问题（用户原话）：「ai 说话前言不搭后语」；「长时间聊后，有的回复会另存在一个地方，
> 当用户说什么的时候，突然间蹦出来一大堆，估计是和我的插件不匹配，你兼容下，合并到我的插件内」。

## 1. 结论先行

| 现象 | 根因 | 处理 |
| --- | --- | --- |
| 回复「另存在一个地方」，用户下次说话时突然冒出一大堆 | 外部插件 `astrbot_plugin_smart_segmentation` 在 `after_message_sent` 之后用 `context.send_message` 补发后续分段；此时 webchat 的 `back_queue` 已被移除，适配器把消息当「主动消息」写进 `platform_message_history` | 分段改由本插件在 `on_decorating_result` 内用 `event.send` 即时发出；外部插件删除 |
| 回复碎成互不相干的短条、像「前言不搭后语」 | ① AstrBot 内置 `platform_settings.segmented_reply`（正则 `.*?[。？！~…]+` 逐句切 + 随机 1.5~3.5s 间隔）② 人格只有长度上限、没有承接要求 | 关闭内置分段；人格新增 `coherence` 规则并在每次请求注入 |
| 两句之间没有承接 | `prohibitions[:8]` 把 p09「必须是伊蕾娜会说的话」截掉；`second_sentence` 只规定「极短」没规定「要承接」 | 放宽到 `[:9]`；`second_sentence` 改为语义承接 |

## 2. 证据链（代码级）

### 2.1 外部插件的行为

`astrbot_plugin_smart_segmentation`（v0.1.0）三个钩子：

1. `on_llm_response`：用 `context.llm_generate` 预分段，结果按
   `(session, hash(normalized_text))` 存 `_prepared_segments`，TTL 60s。
2. `on_decorating_result`：命中预分段就 `result.chain = [Plain(first_segment)]`，
   其余段登记 `_pending_follow_ups[pending_id]`（TTL 60s）。
3. `after_message_sent`：`asyncio.create_task(_run_follow_up_segments(pending))`，
   任务里逐段 `await asyncio.sleep(...)` +
   `await self.context.send_message(pending.session, MessageChain([Plain(segment)]))`。

**关键**：第 3 步发生在首条消息发送完成之后。

### 2.2 为什么「补发」会变成「另存」

`astrbot/core/platform/sources/webchat/webchat_adapter.py:86-134 async def send_by_session`：

```python
target_request_ids = self._queue_mgr.list_back_request_ids(conversation_id)
if not target_request_ids:
    await self._save_proactive_message(conversation_id, message_chain)
    return
```

`_save_proactive_message` 调 `db_helper.insert_platform_message_history(...)`，即写入
`platform_message_history` 表，**不推前端**。而 `webchat_queue_mgr` 在请求结束时
`remove_back_queue()` / `remove_queues()` 会清掉该会话的活跃队列，所以补发时必然命中
`not target_request_ids` 分支。

前端只在**用户下一次发言**时重新拉历史 → 积压的机器人消息一次性渲染出来。

同一原因也解释了为什么主动消息门禁「拦不住」：`ProactiveGuardCore._allow_send()`
在非 strict 模式下只要「用户刚活动过」就放行，补发正好发生在刚回完消息之后。

### 2.3 内置分段是碎片化的另一半

`/opt/astrbot/data/cmd_config.json`：

```json
"platform_settings": {"segmented_reply": {
  "enable": true, "only_llm_result": true, "interval_method": "random",
  "interval": "1.5,3.5", "split_mode": "regex", "regex": ".*?[。？！~…]+|.+$"
}}
```

`astrbot/core/pipeline/respond/stage.py:256-284`：分段开启时对 `result.chain` 里**每个组件**
`sleep(随机 1.5~3.5s)` 再 `event.send(...)`。于是「一句话 → 一个气泡 + 1.5~3.5 秒空档」，
语气词、半句都可能单独成条。它和外部插件的二次分段叠加，观感就是「前后不搭」。

## 2.4 第三层原因：流式 + realtime_segmenting（v1.12.1 补）

webchat 的请求默认带 `enable_streaming=True`（`astrbot/core/platform/sources/webchat/request_flags.py`
的 `WEBCHAT_REQUEST_FLAG_DEFAULTS`）。当模型/上游不支持流式时，AstrBot 用
`unsupported_streaming_strategy = "realtime_segmenting"`：把最终文本按长度硬切成若干块、
逐条 `event.send_streaming`。实测切点落在词中间：

```
plain: '上午整理备' / '忘，下午炖' / '了菜等你' / '。……才不是' / '特意等。'
```

同时该结果的 `result_content_type` 是 `STREAMING_FINISH`，而
`MessageEventResult.is_model_result()` 只认 `LLM_RESULT` / `AGENT_RUNNER_ERROR`，
所以本插件的分段钩子直接 return（日志里看不到 `[segment]`）。

修法（v1.12.1）：在消息处理器里把 extra 置 False。位置很关键——

- `process_stage/method/agent_sub_stages/internal.py:170` 先读
  `event.get_extra("enable_streaming")` 决定流式；
- `StarRequestSubStage`（插件消息处理器）在此之后、LLM 请求之前运行，**能赶在决策前**；
- `OnLLMRequestEvent`（`internal.py:269`）与 `on_decorating_result` 都已太晚；
- `OnWaitingLLMRequestEvent` 在本版本管线中没有触发点（挂上去等于没挂）。

所以放在 `guard_on_user_message`（`@filter.event_message_type(ALL)`）里调用
`SegmentCore.on_message()`，配置 `segment_disable_streaming`（默认开）。

## 3. 设计：SegmentCore

位置：`main.py`「核心十：智能分段」（`class SegmentCore(_PluginCoreBase)`）。

### 3.1 发送时序

```
on_decorating_result
  ├─ 取 result.chain（必须恰好 1 个 Plain）
  ├─ split(text) → [seg1, seg2, ..., segN]
  ├─ result.chain = [Plain(segN)]        # 末段交给管线
  └─ for seg in seg1..segN-1:
         await asyncio.sleep(delay(seg))  # 0.45 + 0.02*len，封顶 1.5
         await event.send(MessageChain([Plain(seg)]))
```

- 全程在本轮请求内完成 → `back_queue` 还在 → 走正常回包通道，前端立即看到 N 个气泡；
- 不落库、不起后台任务、不跨轮补发 → 不会出现「另存 / 突然爆发」，也不会撞主动消息门禁；
- 任一段发送失败：把未发出的段与末段拼回去塞进 `result.chain`，**绝不丢字**。

### 3.2 切分算法

1. 按 `[。！？!?…~～\n]` 切句；
2. 段数 `n = clamp(ceil(len / ideal), 1, segment_max_segments)`，`ideal = min_chars * 1.8`；
   短文本（`len < min_chars*2`）整体不分段；
3. 按 `target = len / n` 把句子分组，保证句子完整；
4. `natural` 风格：单段超过 `segment_max_chars` 时在逗号/顿号处再切一刀（切口不早于段长的 35%）；
   `conservative` 风格：只按句末标点切；
5. 首/尾段短于 4 字时并入邻段（避免「嗯。」单独成条）。

### 3.3 跳过条件

- `segment_enabled=false` 或 `segment_inline_send=false`；
- 结果不是模型结果（`is_model_result()` 为假，例如指令输出）；
- `result.chain` 不是「恰好一个 Plain」（带图片/文件时不动，交给表情包与管线）；
- 文本命中 `LlmGuardCore.match_reason()`（错误文案不该被切碎）；
- 同一 event 已处理过（`_elaina_segment_done` extra 幂等）。

## 4. 人格连贯约束

`assets/persona.json` 新增：

```json
"coherence": {
  "rules": ["每条回复先接住对方这一句…", "连续几条之间要有承接…", "短句也要能独立读懂…", "要换话题先给一句过渡…"],
  "forbidden": ["答非所问", "复读对方原话", "同一件事两套说法", "半句话单独成条", "上一句没接就换话题"],
  "segment_rule": "分段时按句读拆，保证每段能独立读懂、按时间或因果顺序排列"
}
```

接线：

- `persona_chat_block()` 在 `· 说话` 之后追加 `· 连贯` / `· 不要`（`persona_coherence_enabled`）；
- 铁律注入 `prohibitions[:8]` → `[:9]`（补回 p09）；
- `LIVING_PROMPT_TEMPLATE` 增加「接得上」要求；
- `tone.length_rule.second_sentence` 改为「两句时必须语义承接」。

## 5. 测试

`tests/test_segment.py`（S1–S10）：

| 用例 | 断言 |
| --- | --- |
| S1 | 保序、拼回等于原文、每段 ≤ max_chars |
| S2 | 段数不超过 `segment_max_segments`，且不丢字 |
| S3 | 短文本 / 空串不分段 |
| S4 | conservative 只在句末标点切 |
| S5 | 极短句不单独成条（「嗯。」不出现） |
| S6 | 前 N-1 段按顺序 `event.send`，末段留在 chain，拼回完整 |
| S7 | 首段发送失败 → 全部并回末段，字符数不丢 |
| S8 | 带图片的链 / 命中错误文案 / 开关关闭 → 完全不动 |
| S9 | 停顿计算与上下限 |
| S10 | 结构约束：无 `@filter.after_message_sent`、`SegmentCore` 内无 `context.send_message` 补发、无 `create_task`、配置 169 键、persona 有 coherence |

原有 9 个测试文件全部通过。

## 6. 部署清单（服务器）

1. 备份插件目录与配置；
2. 上传 `main.py` / `_conf_schema.json` / `assets/persona.json` / `tests/test_segment.py`；
3. 配置补 10 个键：`persona_coherence_enabled`、`segment_*`（159 → 169）；
4. **关闭内置分段**：`platform_settings.segmented_reply.enable = false`；
5. **删除** `plugins/astrbot_plugin_smart_segmentation`（先备份）；
6. 重启容器，验证日志 `(1.12.0)`、无 Traceback。

## 7. 已知限制

- 关流式后 WebUI 不再有打字机效果（换来的是一条完整结果 + 有意为之的分段）；
  想要打字机就把 `segment_disable_streaming` 关掉，但碎片问题会回来。

- 停顿是「字数启发式」，不是真实的打字节奏；
- 分段只处理纯文本链；带图片/表情的回复合并不切（避免把图片和文字拆到两条）；
- `event.send` 在部分平台（如官方 QQ 机器人）可能受频控，失败时会并回末段（少一个气泡但不丢字）；
- 内置分段若被重新打开，会在本插件切好的每段上再按句切一次（建议保持关闭）。
