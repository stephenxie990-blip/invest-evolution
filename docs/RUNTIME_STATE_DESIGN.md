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
│  └─ artifacts/
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
  - 策略评分摘要
  - benchmark 结果
  - review 是否生效
  - `execution_snapshot` / `run_context`
  - `review_decision` / `similarity_summary`
  - review digest / validation 摘要
  - config snapshot 路径
  - contract stage snapshots 摘要
  - optimization events 日志引用

### 3.2 优化事件

- 路径：`runtime/outputs/training/optimization_events.jsonl`
- 当前 canonical 形态是“稳定 envelope + stage-specific payload”：
  - 稳定 envelope：`event_id`、`contract_version`、`cycle_id`、`trigger`、`stage`、`status`、`ts`
  - 通用上下文：`suggestions`、`lineage`、`evidence`、`notes`
  - 阶段化 payload：`review_decision_payload`、`research_feedback_payload`、`llm_analysis_payload`、`evolution_engine_payload`、`runtime_config_mutation_payload`、`runtime_config_mutation_skipped_payload`、`optimization_error_payload`
- `decision` / `applied_change` 仍可能出现在 envelope 中作为兼容字段，但不再是新读侧与新文档优先依赖的 explainability 主体。
- Training Lab、Commander 摘要与后续 persistence 投影应优先消费规范化后的 stage payload / digest / summary contract，而不是重新拼装宽松子 dict。

### 3.3 冻结报告

- 路径：`runtime/outputs/training/runtime_frozen.json`
- 触发：达到 freeze gate 且允许提前停止训练

### 3.4 最小核心工件集

当前建议把下面这些文件视为 release / 排障 / explainability 的**最小核心工件集**：

- `runtime/outputs/commander/state.json`
- `runtime/outputs/training/cycle_<id>.json`
- `runtime/logs/artifacts/selection/artifact_<cycle>.json`
- `runtime/logs/artifacts/manager_review/artifact_<cycle>.json`
- `runtime/logs/artifacts/allocation_review/artifact_<cycle>.json`
- `runtime/state/config_snapshots/cycle_<id>.json`
- `runtime/outputs/training/optimization_events.jsonl`

如果缺少这组工件中的任意关键成员，通常就无法完整回答：

- 这轮训练实际选了什么
- 为什么会形成当前组合
- simulation / review / validation / outcome 各阶段是怎么判断的，以及 `review_decision` / `similarity_summary` / `run_context` 如何串起来
- 配置当时是什么

## 4. Training Artifacts

### 4.1 Selection Artifact

- JSON：`runtime/logs/artifacts/selection/artifact_<cycle>.json`
- Markdown：`runtime/logs/artifacts/selection/artifact_<cycle>.md`

### 4.2 Manager Review Artifact

- JSON：`runtime/logs/artifacts/manager_review/artifact_<cycle>.json`
- Markdown：`runtime/logs/artifacts/manager_review/artifact_<cycle>.md`

### 4.3 Allocation Review Artifact

- JSON：`runtime/logs/artifacts/allocation_review/artifact_<cycle>.json`
- Markdown：`runtime/logs/artifacts/allocation_review/artifact_<cycle>.md`

### 4.4 最小 explainability 工件集

Training Lab、Commander 摘要与 Web 读侧应优先暴露这组最小 explainability 工件：

- `cycle_result_path`
- `selection_artifact_json_path`
- `manager_review_artifact_json_path`
- `allocation_review_artifact_json_path`

这些文件是训练链最关键的 artifact / explainability 工件，适合排查：

- 为什么选了这些股票
- 为什么进行了参数调整
- 为什么某个 Agent 被降权或升权

与这些 explainability 工件并列的摘要面还包括：

- `manager_review_report` / `allocation_review_report`：持久化 review digest，而不是完整原始中间报告
- `contract_stage_snapshots`：面向 outcome / persistence / lab summary 的阶段摘要投影

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
5. 对应 `runtime/logs/artifacts/` 文件
6. `runtime/outputs/training/optimization_events.jsonl`
7. `runtime/memory/commander_memory.jsonl`

## 11. 拆分式拓扑运维约定

### 11.1 服务边界

- `invest-evolution.service`：无状态 Web/API，只读 `runtime/state`、事件目录与工件目录。
- `invest-evolution-runtime.service`：单实例 Commander/runtime，唯一写入 `runtime/state`、状态快照、事件日志与训练工件。
- `/healthz`：只回答 Web/API 是否存活、反向代理是否可达。
- `/api/status`：查看 runtime 是否在线、`commander.lock` / `training.lock` 是否存在、最近状态快照与训练实验室摘要。

### 11.2 推荐启动顺序

1. 确认 `runtime/` 目录归属运行用户，且 `runtime/state/runtime_paths.json`、`runtime/outputs/`、`runtime/logs/` 可写。
2. 启动 `invest-evolution-runtime.service`，等待其写出 `runtime/outputs/commander/state.json`。
3. 检查 `runtime/state/commander.lock` 已由 runtime 持有。
4. 再启动 `invest-evolution.service` 与 Nginx。
5. 用 `/healthz` 验证 Web 可用，再用 `/api/status` 验证 runtime live 状态。

### 11.3 Restart / Clean Boot

- 重启 Web：`systemctl restart invest-evolution.service`，不会触发 runtime 生命周期变化。
- 重启 runtime：`systemctl restart invest-evolution-runtime.service`，应释放旧 `commander.lock`、重新持锁、保留历史工件。
- clean boot 建议：
  1. `systemctl stop invest-evolution-runtime invest-evolution`
  2. 确认无残留 `invest-runtime` 进程
  3. 检查 `runtime/state/commander.lock` 与 `runtime/state/training.lock`
  4. 必要时清理 stale lock
  5. 先起 runtime，再起 web

### 11.4 Stale Lock 处理

- `runtime/state/commander.lock`：若文件可读取且其中 PID 已失活，runtime service 可在启动时自愈并接管；若文件损坏、不可解析或所有权异常，启动会失败，需要人工清理。
- `runtime/state/training.lock`：当前仅表达“训练互斥正在占用”，没有 stale-lock 自愈。运维必须先确认没有活跃训练或恢复流程，再手动移除。
- `runtime/outputs/commander/state.json` 是状态快照，不是强控制面真相；排障时需要与锁文件、systemd 状态、事件日志一起看。
