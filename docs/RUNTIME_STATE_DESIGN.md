# 运行态文件设计

当前系统所有运行态信息都围绕 `runtime/` 组织，目标是做到：

- 单实例可观测
- 训练互斥可观测
- 工件可追溯
- 配置变更可审计
- Commander 对话与训练实验可以复盘

## 1. 目录分层

```text
runtime/
├─ outputs/
│  ├─ commander/
│  └─ training/
├─ logs/
│  └─ meetings/
├─ memory/
├─ sessions/
│  ├─ inbox/
│  └─ outbox/
├─ state/
└─ workspace/
```

## 2. 核心状态文件

### 2.1 Commander 状态快照

- 文件：`runtime/outputs/commander/state.json`
- 生成方：`CommanderRuntime._persist_state()`
- 作用：提供统一状态快照给 CLI / Web / 排障

### 2.2 运行时锁

- 文件：`runtime/state/commander.lock`
- 生成方：`CommanderRuntime._acquire_runtime_lock()`
- 作用：保证 Commander 默认单实例

### 2.3 训练锁

- 文件：`runtime/state/training.lock`
- 生成方：`InvestmentBodyService._write_training_lock()`
- 作用：表示当前有训练在执行，避免并发训练

## 3. 训练结果工件

### 3.1 单周期结果

- 路径：`runtime/outputs/training/cycle_<id>.json`
- 内容：
  - 收益率、胜负、交易数量
  - 选中股票
  - 策略评分
  - benchmark 结果
  - review 是否生效
  - config snapshot 路径
  - optimization events

### 3.2 优化事件

- 路径：`runtime/outputs/training/optimization_events.jsonl`
- 内容：
  - trigger
  - stage
  - decision
  - applied_change
  - notes

### 3.3 冻结报告

- 路径：`runtime/outputs/training/model_frozen.json`
- 触发：达到 freeze gate 且允许提前停止训练

## 4. 会议工件

### 4.1 选股会议

- JSON：`runtime/logs/meetings/selection/meeting_<cycle>.json`
- Markdown：`runtime/logs/meetings/selection/meeting_<cycle>.md`

### 4.2 复盘会议

- JSON：`runtime/logs/meetings/review/review_<cycle>.json`
- Markdown：`runtime/logs/meetings/review/review_<cycle>.md`

这些文件是训练链最关键的“解释层”工件，适合排查：

- 为什么选了这些股票
- 为什么进行了参数调整
- 为什么某个 Agent 被降权或升权

## 5. Training Lab 工件

### 5.1 计划

- 目录：`runtime/state/training_plans/`
- 文件名：`plan_<timestamp>.json`
- 作用：持久化实验意图和协议

### 5.2 运行

- 目录：`runtime/state/training_runs/`
- 文件名：`run_<timestamp>.json`
- 作用：记录一次实验执行的原始结果

### 5.3 评估

- 目录：`runtime/state/training_evals/`
- 文件名：`run_<timestamp>.json`
- 作用：记录聚合指标、promotion 判断、与 baseline 的比较

## 6. 配置审计工件

### 6.1 配置变更审计

- 文件：`runtime/state/config_changes.jsonl`
- 生成方：`EvolutionConfigService`
- 记录：
  - 时间
  - 来源（如 `web_api`）
  - 变更字段

### 6.2 配置快照

- 目录：`runtime/state/config_snapshots/`
- 两类内容：
  - 常规配置快照 `config_<ts>.json`
  - 周期级运行快照 `cycle_<id>.json`

## 7. Memory 工件

### 7.1 主记忆文件

- 文件：`runtime/memory/commander_memory.jsonl`
- 内容：
  - 对话摘要
  - 训练摘要
  - runtime 检索结果

### 7.2 审计文件

- 文件：`runtime/memory/commander_memory_audit.jsonl`
- 作用：记录 memory append / train_requested 等审计事件

## 8. Bridge 工件

- `runtime/sessions/inbox/`：外部写入消息
- `runtime/sessions/outbox/`：Commander 回复消息

这是轻量级文件消息桥，不是消息队列系统。

## 9. Workspace 辅助文件

- `runtime/workspace/SOUL.md`
- `runtime/workspace/HEARTBEAT.md`

由 `CommanderRuntime` 生成，用于把当前策略基因摘要写入 Brain 工作区。

## 10. 推荐排障顺序

1. `runtime/outputs/commander/state.json`
2. `runtime/state/commander.lock`
3. `runtime/state/training.lock`
4. 最近的 `runtime/outputs/training/cycle_*.json`
5. 对应 `runtime/logs/meetings/` 文件
6. `runtime/outputs/training/optimization_events.jsonl`
7. `runtime/memory/commander_memory.jsonl`
