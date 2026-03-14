# Commander 流式状态能力评估（2026-03-13）

本文只回答一个问题：

> `commander` 现在是否已经支持把“系统运行时内部状态”流式推给人类对话窗口？

结论先行：

## 1. 结论

### 1.1 已具备的能力

项目**已经具备运行时事件流能力**，通过：

- Web SSE：`/api/events`
- 事件摘要：`/api/events/summary`
- runtime 事件落盘：`runtime/state/commander_events.jsonl`

这些能力已经能把训练、模型路由、模块日志、会议发言、agent 状态等内部事件持续发出来。

### 1.2 还不具备的能力

项目**还不具备“把某次 commander 对话对应的内部状态，直接流式推送回同一个聊天回复窗口”的完整能力**。

也就是说：

- 有**全局事件流**
- 但没有**会话绑定的对话流**

## 2. 当前真实状态

### 2.1 `runtime.ask()` 仍是单次请求 / 单次回复

当前 `CommanderRuntime.ask(...)` 最终返回的是**一次性完整字符串**。

对应链路：

- `app/commander.py`
- `app/commander_support/ask.py`
- `brain/runtime.py`

这条链路里没有逐步 `yield`，也没有 callback / observer 把中间事件直接写回聊天响应。

### 2.2 Web 层已有全局 SSE

Web 已经实现：

- `/api/events`：SSE 持续推送

它能发出：

- cycle start / complete / skipped
- model routing
- agent status / agent progress
- module log
- meeting speech
- runtime ask/task 事件

这说明“运行时流式观测”本身是存在的。

### 2.3 CLI 与 commander 对话入口目前不消费这条流

当前 CLI：

- `commander ask`
- `commander run --interactive`

都还是**等最终回复后一次性打印**。

没有：

- 一边 ask 一边订阅内部事件
- 一边显示“系统当前在干什么”
- 一边把事件翻译成人类可读播报

## 3. 现在为什么还不能直接推到“对话窗口”

核心不是缺事件，而是缺**会话桥接层**。

### 3.1 事件流是全局的，不是会话隔离的

训练与运行时事件现在主要是：

- runtime 全局事件
- training 全局事件

虽然 `ask_started` / `ask_finished` 会带：

- `session_key`
- `chat_id`

但大多数训练内部事件并没有稳定携带 `session_key/chat_id`。

所以当前只能说：

- “系统发生了这些事”

还不能精确说：

- “这些事件就是这一次聊天请求触发的”

### 3.2 `ask` 返回通道与事件推送通道是分开的

当前是两条通道：

1. `ask(...)` -> 最终字符串回复
2. `/api/events` -> 全局 SSE 事件流

缺的是第三层：

3. 把 2 中和本次 ask 相关的事件，实时翻译后回灌到本次聊天窗口

## 4. 如果要做“人类对话窗口里的系统状态流式播报”，需要什么

建议按三步做：

### 第一步：事件关联

给训练与调度事件补稳定关联字段：

- `session_key`
- `chat_id`
- `request_id`

至少要让由某次 ask 触发的内部动作，能在事件总线上被识别出来。

### 第二步：会话态事件订阅

新增一个会话绑定的流式入口，例如：

- `/api/chat/stream`
- 或 `commander ask --stream-events`

它不需要流式输出 LLM token，而是流式输出：

- `status`
- `stage`
- `agent`
- `module`
- `meeting_speech`
- `risk/update/confirmation`

### 第三步：事件翻译层

把内部事件翻成人类播报文案，例如：

- “系统正在加载训练数据”
- “模型路由判断当前是震荡市，准备切换到均值回归模型”
- “复盘会议正在生成建议”
- “当前操作仍需你确认，系统尚未真正执行写入”

这一步本质上就是把现在增强后的 `human_readable` 能力扩展到流式事件上。

## 5. 当前最准确的判断

### 可以说“支持”的部分

- 支持**系统运行时事件流**
- 支持**SSE 推送**
- 支持**状态轮询与事件摘要**

### 不能说“已经支持”的部分

- 不支持“单次 commander 对话内的事件流式回复”
- 不支持“会话隔离的内部状态推送”
- 不支持“把内部事件直接流式送进同一个人类聊天窗口”

## 6. 推荐下一步

如果下一阶段要做这件事，最值得先做的是：

1. 给训练/调度事件补 `request_id/session_key/chat_id`
2. 新增 `chat stream` 或 `ask --stream-events`
3. 复用现有 receipt / event explanation，把事件翻译成人类播报文本

这样做的收益最大，而且与当前事件总线、SSE、监控契约完全兼容。
