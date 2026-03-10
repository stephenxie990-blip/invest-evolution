# Runtime 目录说明

`runtime/` 是当前系统的**运行态根目录**。代码本体不在这里，所有会随着运行变化而产生的现场、工件、锁文件和审计记录都集中落在这里。

## 当前目录用途

- `runtime/outputs/commander/`
  - Commander 状态快照，例如 `state.json`
- `runtime/outputs/training/`
  - 单周期训练结果 `cycle_*.json`
  - `optimization_events.jsonl`
  - `model_frozen.json`
- `runtime/outputs/leaderboard.json`
  - 汇总所有训练周期后的模型排行榜
- `runtime/logs/meetings/selection/`
  - 选股会议 JSON / Markdown 记录
- `runtime/logs/meetings/review/`
  - 复盘会议 JSON / Markdown 记录
- `runtime/memory/`
  - Commander 持久记忆与审计文件
- `runtime/sessions/inbox` / `runtime/sessions/outbox`
  - Bridge 文件通道收件箱 / 发件箱
- `runtime/state/`
  - `commander.lock` / `training.lock`
  - `config_changes.jsonl`
  - `config_snapshots/`
  - `training_plans/`
  - `training_runs/`
  - `training_evals/`
  - `runtime_paths.json`
- `runtime/workspace/`
  - BrainRuntime 工作区、`SOUL.md`、`HEARTBEAT.md` 等辅助文件

## 管理原则

- `runtime/` 默认视为**可再生目录**，但训练工件和审计日志通常值得保留。
- 如需清理，请优先确认以下文件是否还需要追溯：
  - `runtime/outputs/training/*`
  - `runtime/outputs/leaderboard.json`
  - `runtime/logs/meetings/*`
  - `runtime/state/training_*/*`
  - `runtime/memory/*`
- 如果系统正在运行，不要手动删除：
  - `runtime/state/commander.lock`
  - `runtime/state/training.lock`
  - `runtime/sessions/inbox` / `runtime/sessions/outbox` 正在使用的文件

## 推荐的排障顺序

1. 先看 `runtime/outputs/commander/state.json`
2. 再看 `runtime/state/commander.lock` 与 `runtime/state/training.lock`
3. 再看最近的 `runtime/outputs/training/cycle_*.json`
4. 再看 `runtime/logs/meetings/` 下对应会议记录
5. 如需追踪 Commander 决策链，再查 `runtime/memory/commander_memory.jsonl`
