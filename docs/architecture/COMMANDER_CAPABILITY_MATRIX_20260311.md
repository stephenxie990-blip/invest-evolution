# Commander 能力覆盖矩阵（2026-03-11）

## 判定标准
- **已覆盖**：Commander 当前已有明确 Runtime 方法或 Brain Tool 可完成
- **部分覆盖**：Commander 可通过摘要间接获取，但没有完整资源级能力
- **未覆盖**：能力存在于系统/Web，但 Commander 当前没有对应能力入口

| 功能域 | 具体功能 | 系统现状 | Commander 当前 | 备注 |
|---|---|---:|---:|---|
| 运行时 | 系统状态快照 | 有 | 已覆盖 | `status()` |
| 运行时 | 自然语言对话 | 有 | 已覆盖 | `ask()` |
| 运行时 | 常驻 daemon | 有 | 已覆盖 | `serve_forever()` |
| 运行时 | 停止运行时 | 有 | 已覆盖 | `stop()` |
| Brain | ReAct 工具调用循环 | 有 | 已覆盖 | `BrainRuntime._run_loop()` |
| Brain | 插件工具加载 | 有 | 已覆盖 | JSON plugin |
| 训练 | 单轮/多轮训练 | 有 | 已覆盖 | `train_once(rounds=...)` |
| 训练 | 训练计划创建 | 有 | 已覆盖 | `create_training_plan()` |
| 训练 | 训练计划列表 | 有 | 已覆盖 | `list_training_plans()` |
| 训练 | 训练计划详情 | 有 | 已覆盖 | `get_training_plan()` |
| 训练 | 训练计划执行 | 有 | 已覆盖 | `execute_training_plan()` |
| 训练 | 单个 run 查询 | 有 | 已覆盖 | `get_training_run()` |
| 训练 | 单个 evaluation 查询 | 有 | 已覆盖 | `get_training_evaluation()` |
| 训练 | run 列表 | 有 | 未覆盖 | 仅 Web 有列表 API |
| 训练 | evaluation 列表 | 有 | 未覆盖 | 仅 Web 有列表 API |
| 训练 | 训练事件流 | 有 | 未完整覆盖 | Commander 无 recent-events 工具 |
| 策略 | 策略 gene 列表 | 有 | 已覆盖 | `invest_list_strategies` |
| 策略 | 策略 gene 重载 | 有 | 已覆盖 | `invest_reload_strategies` |
| 模型分析 | investment models 列表 | 有 | 未覆盖 | 仅 Web |
| 模型分析 | leaderboard 查询 | 有 | 未覆盖 | 仅 Web |
| 模型分析 | allocator 查询 | 有 | 未覆盖 | 仅 Web |
| 模型分析 | model-routing preview | 有 | 未覆盖 | 仅 Web |
| 调度 | cron 列表/创建/删除 | 有 | 已覆盖 | 已有 3 个 Tool |
| 记忆 | memory 搜索 | 有 | 已覆盖 | `invest_memory_search` |
| 记忆 | memory 列表 | 有 | 未覆盖 | 仅 Web |
| 记忆 | memory 详情 | 有 | 未覆盖 | 仅 Web |
| 配置 | agent prompts 列表 | 有 | 未覆盖 | 仅 Web |
| 配置 | agent prompts 更新 | 有 | 未覆盖 | 仅 Web |
| 配置 | runtime paths 获取/更新 | 有 | 未覆盖 | 仅 Web |
| 配置 | evolution config 获取/更新 | 有 | 未覆盖 | 仅 Web |
| 配置 | control plane 获取/更新 | 有 | 未覆盖 | 仅 Web |
| 数据 | data status | 有 | 部分覆盖 | `status()` 中含摘要 |
| 数据 | capital flow 查询 | 有 | 未覆盖 | 仅 Web |
| 数据 | dragon tiger 查询 | 有 | 未覆盖 | 仅 Web |
| 数据 | intraday 60m 查询 | 有 | 未覆盖 | 仅 Web |
| 数据 | 数据后台下载/同步 | 有 | 未覆盖 | 仅 Web |
| 合同/契约 | contracts 索引/文档 | 有 | 未覆盖 | 仅 Web |
| 可观测 | body snapshot | 有 | 已覆盖 | `status()` |
| 可观测 | brain stats | 有 | 已覆盖 | `status()` |
| 可观测 | memory stats | 有 | 已覆盖 | `status()` |
| 可观测 | bridge status | 有 | 已覆盖 | `status()` |
| 可观测 | runtime/task locks | 有 | 已覆盖 | `status()` |
| 可观测 | recent events 聚合 | 有 | 未覆盖 | 仅 SSE/内部缓存 |
| 可观测 | degraded 诊断摘要 | 有 | 未覆盖 | 需新增 diagnostics 层 |

## 结论
1. Commander 当前覆盖了**核心执行面**，尚未覆盖**完整管理面与观测面**。
2. 要让 Commander 成为唯一入口，优先级应为：
   - 配置域
   - 分析查询域
   - Lab 列表能力
   - 数据域
   - 统一观测面
3. 在 Commander 完成总体验收前，不建议删除 Web API；应先把 Web 降级为内部资源层与可选可视化壳。
