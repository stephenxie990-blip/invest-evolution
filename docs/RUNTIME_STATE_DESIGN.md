# Runtime 状态设计说明

## 目标

说明 `runtime/` 下核心状态文件、锁文件与运行时状态字段的职责，避免后续继续把运行态逻辑散落到入口层。

## 目录

- `runtime/state/commander.lock`：运行时单实例锁
- `runtime/state/training.lock`：训练执行互斥锁
- `runtime/state/config_changes.jsonl`：配置变更审计日志
- `runtime/state/config_snapshots/`：配置快照目录
- `runtime/outputs/commander/state.json`：统一运行时状态摘要
- `runtime/memory/commander_memory.jsonl`：对话记忆
- `runtime/memory/commander_memory_audit.jsonl`：运行记忆审计日志
- `runtime/sessions/inbox`：Bridge 输入
- `runtime/sessions/outbox`：Bridge 输出

## 状态机

### Runtime 状态

- `initialized`
- `starting`
- `idle`
- `training`
- `reloading_strategies`
- `stopping`
- `stopped`
- `error`
- `busy`

### 任务状态

每个任务至少记录：

- `type`
- `source`
- `started_at`
- `finished_at`
- `status`
- 任务特定元数据

## 设计原则

1. 同一时刻只允许一个 commander runtime 持有 `commander.lock`
2. 同一时刻只允许一个训练任务持有 `training.lock`
3. 所有入口共享同一份状态摘要，不再维护多份运行态视图
4. 运行态写入优先结构化 JSON，便于 Web、CLI、脚本统一读取
