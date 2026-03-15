# Invest V2 执行计划（当前状态版）

## 1. 当前结论

Invest V2 的核心目标已经在当前代码中基本达成：

- 入口收口到 `app/`
- 训练闭环稳定成型
- 数据层统一到 canonical SQLite
- 多模型 + YAML 配置 + leaderboard + allocator 可用
- Commander / Web / 训练共用同一套真实能力

因此本文档现在更适合作为**阶段状态说明**，而不是未执行的计划稿。

## 2. 已完成阶段

### Phase A：入口收口

已完成：

- `app/commander.py`
- `app/train.py`
- `app/web_server.py`
- 根目录兼容壳保留

### Phase B：数据层统一

已完成：

- `MarketDataRepository`
- `DataIngestionService`
- `datasets.py` 读侧 builder
- `DataManager` 兼容 façade

### Phase C：训练闭环稳定化

已完成：

- 模型处理 -> 会议 -> 模拟交易 -> 评估 -> 复盘 -> 优化
- skip / no_data / error 状态语义统一
- freeze gate 落地

### Phase D：实验室与工件化

已完成：

- training plans
- training runs
- training evaluations
- cycle artifacts
- config snapshots
- meeting logs

### Phase E：多模型比较与自动分配

已完成：

- `leaderboard`
- `allocator`
- regime prior + weight cap + cash reserve

## 3. 当前仍可继续推进的阶段

### 3.1 更强的实验协议管理

当前 `experiment_spec` 已支持：

- `protocol`
- `dataset`
- `model_scope`
- `optimization`
- `llm`

后续可进一步补充更严格的协议校验和前端编辑体验。

### 3.2 更强的多模型对比基线

当前 evaluation 里已经支持 baseline 比较与 promotion summary，但仍可继续增强：

- 更细粒度的 holdout / walk-forward 报表
- 更明确的 candidate promotion 流程

### 3.3 更完整的运行可观测性

当前已有：

- SSE
- runtime state
- memory
- artifacts

后续仍可增强：

- 更系统的日志检索
- 运行警报
- UI 对 training lab 工件的完整展示

## 4. 当前不建议再做的事情

- 不建议重新引入多套数据读取逻辑
- 不建议重新把实现拆回根目录脚本
- 不建议为 Web 单独复制训练逻辑
- 不建议让 Agent 绕过结构化契约直接控制交易执行
