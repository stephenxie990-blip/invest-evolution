# Progress Log

## 2026-03-13

### Session start

- 读取并采用技能：`pi-planning-with-files`、`python-patterns`、`tdd-workflow`、`verification-loop`
- 已完成 Phase 6 RFC 文档：
  - `docs/architecture/PHASE6_STRUCTURAL_REFACTOR_RFC_20260313.md`

### Current work

- 读取关键实现：`app/train.py`、`app/stock_analysis.py`、`app/commander.py`
- 读取目录结构：`app/`、`brain/`、`market_data/`、`tests/`
- 建立本次会话的 planning files

### Next

- 审阅现有测试守卫与 application/training 支点
- 落地 Wave A 结构骨架
- 运行最小验证

### Completed

- 新增 `docs/plans/PHASE6_IMPLEMENTATION_PLAN_20260313.md`
- 新增 `app/application/`、`app/interfaces/`、`invest/services/`、`market_data/services/`
- `app/web_server.py` 已切换到统一接口注册器
- 新增 `tests/test_phase6_wave_a.py`
- 更新 `tests/test_architecture_import_rules.py`，纳入 Phase 6 包结构守卫
- 新增 `app/training/cycle_services.py`
- `app/train.py` 已把 cycle bootstrap / data loading 下沉到 `TrainingCycleDataService`
- `app/train.py` 已开始通过 `SelectionMeetingService` / `ReviewMeetingService` 访问关键会议编排路径
- 新增 `app/training/review_services.py`
- `app/train.py` 已把 `EvalReport` 构造与 review decision 应用下沉到 `TrainingReviewService`
- `app/training/optimization.py` 已开始通过 `evolution_service` 使用进化能力
- 新增 `market_data/services/query.py`
- `app/commander_support/services.py` 已改为通过 `MarketQueryService` 获取数据状态与读侧数据

### Verification

- `.venv/bin/ruff check .` -> pass
- `.venv/bin/pyright .` -> 0 errors
- `.venv/bin/pytest -q` -> pass
- `475 tests collected`
- `.venv/bin/python -m app.freeze_gate --mode quick` -> pass

### Protocol convergence kickoff

- 读取并采用技能：`pi-planning-with-files`、`verification-loop`
- 已复核 `task_plan.md`、`findings.md`、`progress.md`
- 已启动“协议消费方收敛与旧路径退役”5 步主线

### Completed

- 完成第 1 步盘点与分级：
  - 扫描 `invest/ app/ tests/` 中 `SignalPacket.context`、`stock_summaries/raw_summaries`、`ask_stock` payload、`metadata.get(...)` 的消费点
  - 确认主要收口点集中在：
    - `AgentContext.metadata["confidence"]` 的显式化
    - 模型层摘要对象显式化
    - `ask_stock` canonical payload 与兼容顶层字段定界
- 完成第 2 步模型层与研究层收口：
  - `AgentContext` 新增显式 `confidence` 字段
  - 训练选择与会议编排优先读取 `agent_context.confidence`
  - 四个主模型改为主动构造 `StockSummaryView`，不再默认依赖契约层被动归一化
  - 补充 `tests/test_v2_momentum_model.py`、`tests/test_v2_contracts.py` 的新契约断言
- 完成第 3 步会议层与 Agent 层收口：
  - `hunters/specialists/reviewers` 的核心消费签名改为 `Sequence[Mapping[str, Any]]`
  - `StockSummaryView` 在 Agent / meeting 路径上从“兼容对象”提升为默认输入协议
  - focused agent/meeting 回归保持通过
- 完成第 4 步 `ask_stock` payload 定界：
  - 新增 canonical 分区：`request`、`identifiers`、`resolved_entities`
  - `research` 与 `analysis.model_bridge` 现在都显式携带同一组 `identifiers`
  - 顶层 `policy_id / research_case_id / attribution_id / resolved_security` 继续保留兼容镜像
- 完成第 5 步兼容层退役与总验收：
  - 删除 `app/stock_analysis.py` 中 4 个无消费者的 research wrapper
  - 完成全仓 `ruff / pyright / pytest` 验证，全部通过
- 完成“契约彻底化 + schema 守卫化”蓝图：
  - `AgentContext` 新增 `effective_confidence()`，selection/training 默认通过对象方法读取置信度
  - `ask_stock` canonical sections 新增 shape 守卫测试
  - 兼容 stub 继续支持，无需强制所有测试桩升级为完整契约对象

### Verification

- Focused:
  - `.venv/bin/ruff check invest/contracts/agent_context.py invest/models/base.py invest/models/momentum.py invest/models/mean_reversion.py invest/models/defensive_low_vol.py invest/models/value_quality.py invest/meetings/selection.py app/training/selection_services.py tests/test_v2_momentum_model.py tests/test_v2_contracts.py` -> pass
  - `.venv/bin/pyright invest/contracts/agent_context.py invest/models/base.py invest/models/momentum.py invest/models/mean_reversion.py invest/models/defensive_low_vol.py invest/models/value_quality.py invest/meetings/selection.py app/training/selection_services.py tests/test_v2_momentum_model.py tests/test_v2_contracts.py` -> 0 errors
  - `.venv/bin/pytest -q tests/test_v2_momentum_model.py tests/test_v2_contracts.py tests/test_agent_roster.py tests/test_training_controller_services.py -q` -> pass
- Focused (step 3):
  - `.venv/bin/ruff check invest/agents/specialists.py invest/agents/hunters.py invest/agents/reviewers.py invest/meetings/selection.py` -> pass
  - `.venv/bin/pyright invest/agents/specialists.py invest/agents/hunters.py invest/agents/reviewers.py invest/meetings/selection.py` -> 0 errors
  - `.venv/bin/pytest -q tests/test_agent_roster.py tests/test_training_controller_services.py tests/test_research_training_feedback.py -q` -> pass
- Focused (step 4):
  - `.venv/bin/ruff check app/stock_analysis.py tests/test_ask_stock_model_bridge.py` -> pass
  - `.venv/bin/pyright app/stock_analysis.py tests/test_ask_stock_model_bridge.py` -> 0 errors
  - `.venv/bin/pytest -q tests/test_ask_stock_model_bridge.py tests/test_stock_analysis_react.py tests/test_commander_unified_entry.py -q` -> pass
- Full (step 5):
  - `.venv/bin/ruff check .` -> pass
  - `.venv/bin/pyright .` -> 0 errors
  - `.venv/bin/pytest -q` -> pass
- Full (contract hardening blueprint):
  - `.venv/bin/ruff check .` -> pass
  - `.venv/bin/pyright .` -> 0 errors
  - `.venv/bin/pytest -q` -> pass

## 2026-03-14

### Session start

- 读取并采用技能：`pi-planning-with-files`
- 复核既有计划与蓝图前置材料：
  - `docs/plans/AGENT_FOUNDATION_PHASED_IMPLEMENTATION_PLAN_20260313.md`
  - `docs/plans/PHASE6_IMPLEMENTATION_PLAN_20260313.md`
  - `docs/TRAINING_FLOW.md`
  - `docs/MAIN_FLOW.md`

### Current work

- 将上一轮战略分析继续下沉为 `v1.1` 实施蓝图
- 对齐 `Phase 6` 与 `Agent Foundation` 的顺序关系
- 明确模块级改动文件、优先测试和按周推进节奏

### Completed

- 新增 `docs/plans/V1_1_IMPLEMENTATION_BLUEPRINT_20260314.md`
- 将 `v1.1` 版本目标收敛为：
  - 训练协议硬化
  - 最小必要结构解耦
  - `Instructor`
  - `Guardrails`
- 为 `v1.1` 明确了模块级文件清单、建议新增文件、首批测试和 6 周推进方案
- 更新 `task_plan.md`、`findings.md` 以纳入本轮蓝图输出

### Verification

- 本轮未运行测试
- 本轮仅新增/更新规划文档，无业务代码改动

### Wave B/C/D closeout kickoff

- 重新读取并采用技能：`pi-planning-with-files`、`verification-loop`、`python-patterns`
- 对照当前仓库状态、`task_plan.md`、`findings.md`、`git diff --stat` 复核 Phase 6 落地现状
- 将本轮目标固定为：按 `Wave B -> Wave C -> Wave D` 顺序完成收口，并在每个 wave 后执行 focused verification

### Current work

- 更新 planning files，明确本轮各 wave 的完成定义、监控项和验证门槛
- 盘点 `SelfLearningController` 剩余包装方法、invest facade 边界和 `market_data` 上层调用迁移范围

### Next

- Wave B：继续削薄 `app/train.py`，清理控制器尾部包装与残余编排
- Wave C：统一 invest / meetings / evolution facade 边界
- Wave D：推进 `market_data/services/` 调用迁移与测试守卫

### Completed

- 更新 `task_plan.md`，补充 `Wave B / C / D` 的完成定义、监控项与验证门槛
- 完成 `Wave B` 收口：
  - `TrainingLifecycleService` 直接通过 persistence / freeze services 驱动周期收尾
  - `TrainingExperimentService` 直接协同 LLM runtime / routing services
  - `FreezeGateService` 增加兼容 rolling hook 解析，避免破坏既有覆写 seam
- 完成 `Wave C` 收口：
  - `SelectionMeetingService` 新增 `set_agent_weights()`，上层不再直接改 meeting 内部属性
  - `TrainingPolicyService` 改为通过 facade 同步 agent 权重
  - `app/training/optimization.py` 统一通过 `EvolutionService` 边界适配进化链，并保留 legacy engine 兼容
  - `app/training/execution_services.py` 优先通过 routing service 重载模型
- 完成 `Wave D` 收口：
  - `app/commander_support/status.py` 改为通过 `MarketQueryService` 获取数据状态
  - 补充 market facade 接入测试，验证 commander 状态流已使用显式 facade
- 补充并更新回归测试：
  - `tests/test_training_controller_services.py`
  - `tests/test_training_optimization.py`
  - `tests/test_governance_phase_a_f.py`
  - 兼容验证 `tests/test_research_training_feedback.py`

### Verification

- Focused:
  - `.venv/bin/ruff check app/training/... invest/services/... app/commander_support/status.py tests/...` -> pass
  - `.venv/bin/pyright app/training/... invest/services/... app/commander_support/status.py tests/...` -> 0 errors
  - `.venv/bin/pytest -q tests/test_training_controller_services.py tests/test_training_optimization.py tests/test_governance_phase_a_f.py tests/test_research_training_feedback.py` -> pass
- Full:
  - `.venv/bin/ruff check .` -> pass
  - `.venv/bin/pyright .` -> 0 errors
  - `.venv/bin/pytest -q` -> pass
  - `.venv/bin/python -m app.freeze_gate --mode quick` -> pass

### Wave E/F kickoff

- 复核 `task_plan.md`、`docs/plans/V1_1_IMPLEMENTATION_BLUEPRINT_20260314.md`、`brain/runtime.py`、`app/web_server.py`、`app/interfaces/web/`
- 将本轮目标固定为：
  - Wave E：runtime protocol / presentation 解耦、web contract/display 资源化路由下沉
  - Wave F：补齐对应守卫、兼容层薄化与全量回归

### Completed

- 新增 `app/interfaces/web/presentation.py`
- 新增 `app/interfaces/web/contracts.py`
- 新增 `app/interfaces/web/routes/contracts.py`
- `app/interfaces/web/registry.py` 已纳入 contract 路由注册
- `app/web_server.py` 已改为复用 interface-layer 的 contract / display helper，移除本地 contract 路由实现
- 新增 `brain/presentation.py`
- `brain/runtime.py` 已将 human-readable receipt builder 委托给 `BrainHumanReadablePresenter`
- 更新测试：
  - `tests/test_phase6_wave_a.py`
  - `tests/test_architecture_import_rules.py`
- 保持现有 `tests/test_runtime_api_contract.py`、`tests/test_web_server_contract_headers.py`、`tests/test_commander_unified_entry.py`、`tests/test_commander_cli_view.py` 通过，说明行为兼容

### Verification

- Focused:
  - `.venv/bin/ruff check app/web_server.py app/interfaces/web/... brain/runtime.py brain/presentation.py tests/...` -> pass
  - `.venv/bin/pyright app/web_server.py app/interfaces/web/... brain/runtime.py brain/presentation.py tests/...` -> 0 errors
  - `.venv/bin/pytest -q tests/test_architecture_import_rules.py tests/test_phase6_wave_a.py tests/test_runtime_api_contract.py tests/test_web_server_contract_headers.py tests/test_commander_unified_entry.py tests/test_commander_cli_view.py` -> pass
- Full:
  - `.venv/bin/ruff check .` -> pass
  - `.venv/bin/pyright .` -> 0 errors
  - `.venv/bin/pytest -q` -> pass
  - `.venv/bin/python -m app.freeze_gate --mode quick` -> pass

### Pre-v1.1 cleanup gate kickoff

- 将 `v1.1` 主线暂时前置一个仓库级代码清洁阶段，先处理高确定性的静态质量债务
- 首轮扫描使用 `ruff` 规则聚焦 `S110/S112/PLC0415/PLW0603`，并人工复核 `pass`/宽异常语义
- 当前量化结果（仅 `app/ brain/ invest/ market_data/`）：
  - `PLC0415 import-outside-top-level`: 32 处
  - `PLW0603 global-statement`: 10 处
  - `S110 try-except-pass`: 3 处
  - `S112 try-except-continue`: 2 处

### Completed

- 完成第一批“静默失败改可观测”清理：
  - `app/train.py` 的 `emit_event()` 在 callback 异常时记录 warning
  - `app/runtime_artifact_reader.py` 在 JSON/JSONL/text 读取失败或 JSONL 脏行时记录 warning
  - `app/commander_support/observability.py` 在 runtime events 脏行和非法 `ts_ms` 时记录 warning
  - `app/llm_gateway.py` 改为顶层 `logging`，并在 LiteLLM 属性初始化失败时记录 debug
  - `app/commander.py` 在 cycle artifact 路径拼装失败时记录 warning
  - `app/commander_support/services.py` 删除无意义 `finally: pass`
- 补充清洁回归测试：
  - `tests/test_observability_helpers.py`
  - `tests/test_agent_observability_contract.py`
  - `tests/test_llm_gateway.py`

### Verification

- Focused:
  - `.venv/bin/ruff check app/train.py app/runtime_artifact_reader.py app/commander_support/observability.py app/llm_gateway.py app/commander.py app/commander_support/services.py tests/test_llm_gateway.py tests/test_agent_observability_contract.py tests/test_observability_helpers.py` -> pass
  - `.venv/bin/pytest -q tests/test_llm_gateway.py tests/test_agent_observability_contract.py tests/test_observability_helpers.py tests/test_commander_unified_entry.py tests/test_training_controller_services.py` -> pass

### Cleanup wave 2/3

- 继续推进 `pre-v1.1 cleanup gate`，优先消灭剩余 `S110/S112`，再收低风险 `late import`
- 第二轮清理聚焦：
  - `app/strategy_gene_registry.py`
  - `brain/runtime.py`
  - `brain/plugins.py`
  - `invest/leaderboard/engine.py`
  - `invest/foundation/compute/indicators_v2.py`
  - `app/commander_support/config.py`

- 第三轮清理聚焦：
  - `app/web_server.py`
  - `invest/agents/base.py`
  - `invest/evolution/analyzers.py`
  - `invest/foundation/risk/controller.py`

### Completed

- 完成第二批“剩余静默吞错清零”修复：
  - `app/strategy_gene_registry.py` 的 Python 基因元数据解析失败现在会记录 warning
  - `brain/runtime.py` 的 progress callback 失败现在会记录 warning
  - `brain/plugins.py` 的坏插件 JSON 不再静默跳过
  - `invest/leaderboard/engine.py` 的坏周期文件不再静默跳过
  - `invest/foundation/compute/indicators_v2.py` 的 `pd.isna()` 边界异常收敛为 debug 降级
  - `app/commander_support/config.py` 移除了无副作用 `late import`
- 新增回归：
  - `tests/test_cleanup_regressions.py`
- 完成第三批“低风险 late import 收口”：
  - `app/web_server.py`
  - `invest/agents/base.py`
  - `invest/evolution/analyzers.py`
  - `invest/foundation/risk/controller.py`

### Verification

- Focused wave 2:
  - `.venv/bin/ruff check app/strategy_gene_registry.py brain/runtime.py invest/foundation/compute/indicators_v2.py brain/plugins.py invest/leaderboard/engine.py app/commander_support/config.py tests/test_cleanup_regressions.py` -> pass
  - `.venv/bin/pytest -q tests/test_cleanup_regressions.py tests/test_brain_extensions.py tests/test_brain_runtime.py tests/test_leaderboard.py tests/test_leaderboard_snapshot_exclusion.py tests/test_strategy_gene_validation.py` -> pass
- Focused wave 3:
  - `.venv/bin/ruff check app/web_server.py invest/agents/base.py invest/evolution/analyzers.py invest/foundation/risk/controller.py tests/test_structure_guards.py tests/test_brain_extensions.py tests/test_commander_unified_entry.py tests/test_train_ui_semantics.py` -> pass
  - `.venv/bin/pytest -q tests/test_structure_guards.py tests/test_brain_extensions.py tests/test_commander_unified_entry.py tests/test_train_ui_semantics.py tests/test_strategy_gene_validation.py` -> pass

### Current debt snapshot

- `S110 / S112`: 0
- `PLC0415 import-outside-top-level`: 26
- `PLW0603 global-statement`: 10

### Cleanup wave 4/5

- Wave 4：`app/train.py` 事件回调全局状态改为显式状态容器
- Wave 5：兼容优先地清理 `app/web_server.py` 的 `global statement`，并继续收口一批安全的内部 `late import`

### Completed

- 完成 `app/train.py` 的 event callback 状态容器化：
  - `_event_callback` -> `_event_callback_state.callback`
  - 保持 `set_event_callback()` / `emit_event()` 契约不变
- 更新训练事件流相关测试：
  - `tests/test_agent_observability_contract.py`
  - `tests/test_train_cycle.py`
  - `tests/test_train_event_stream.py`
- 完成 `app/web_server.py` 的 `global statement` 清理，保留外部 monkeypatch 表面不变
- 安全上提一批内部 `late import`：
  - `invest/meetings/selection.py`
  - `invest/meetings/review.py`
  - `invest/foundation/engine/helpers.py`
- 识别并验证：
  - `market_data.manager` 里的 service facade import 机械上提会触发循环依赖，已回退该处改动

### Verification

- Focused wave 4:
  - `.venv/bin/ruff check app/train.py tests/test_agent_observability_contract.py tests/test_train_cycle.py tests/test_train_event_stream.py` -> pass
  - `.venv/bin/pytest -q tests/test_agent_observability_contract.py tests/test_train_cycle.py tests/test_train_event_stream.py tests/test_train_ui_semantics.py tests/test_web_server_security.py` -> pass
- Focused wave 5:
  - `.venv/bin/ruff check app/web_server.py --select PLW0603,PLC0415` -> pass
  - `.venv/bin/pytest -q tests/test_train_event_stream.py tests/test_web_server_security.py tests/test_web_server_contract_headers.py tests/test_web_server_runtime_and_bool.py` -> pass
  - `.venv/bin/ruff check invest/meetings/selection.py invest/meetings/review.py invest/foundation/engine/helpers.py market_data/manager.py` -> pass
  - `.venv/bin/pytest -q tests/test_train_ui_semantics.py tests/test_structure_guards.py tests/test_data_unification.py tests/test_market_data_gateway.py tests/test_brain_extensions.py` -> pass

### Updated debt snapshot

- `S110 / S112`: 0
- `PLW0603 global-statement`: 0
- `PLC0415 import-outside-top-level`: 23

### Cleanup wave 6/7

- Wave 6：继续压 `web_*` 与 training seam 剩余 `PLC0415`
- Wave 7：将 `market_data` 中的延迟导入改成显式可选依赖 / provider loader，收口剩余 `PLC0415`

### Completed

- 完成 `app/web_ops_routes.py` 与 `app/web_data_routes.py` 的剩余 `PLC0415` 清理
- 新增 `app/training/runtime_hooks.py`，将：
  - `SelfAssessmentSnapshot`
  - 训练事件回调状态
  - `emit_event()` / `set_event_callback()`
  从 `app.train` 抽成独立 runtime hook 模块
- `app/train.py` 保持原有导出表面不变，改为 re-export runtime hooks
- `app/training/lifecycle_services.py` 不再反向依赖 `app.train`
- 将 `cycle_complete` 事件发射显式提升为控制器 seam：`_emit_runtime_event()`
- `market_data` 剩余延迟导入已统一收口为显式 loader/helper：
  - `market_data/ingestion.py`
  - `market_data/manager.py`
  - `market_data/services/benchmark.py`
- `market_data` 中 `baostock / akshare / tushare / DataManager / service class` 的按需加载不再依赖局部 `import` 语句

### Verification

- Focused wave 6:
  - `.venv/bin/ruff check app/web_ops_routes.py app/web_data_routes.py` -> pass
  - `.venv/bin/pytest -q tests/test_web_server_contract_headers.py tests/test_web_server_security.py tests/test_control_plane_api.py tests/test_web_server_runtime_and_bool.py` -> pass
  - `.venv/bin/ruff check app/train.py app/training/lifecycle_services.py app/training/runtime_hooks.py tests/test_train_cycle.py tests/test_train_event_stream.py tests/test_agent_observability_contract.py tests/test_training_controller_services.py` -> pass
  - `.venv/bin/pytest -q tests/test_train_cycle.py tests/test_train_event_stream.py tests/test_agent_observability_contract.py tests/test_training_controller_services.py tests/test_train_ui_semantics.py` -> pass
- Focused wave 7:
  - `.venv/bin/ruff check market_data/ingestion.py market_data/manager.py market_data/services/benchmark.py` -> pass
  - `.venv/bin/pytest -q tests/test_market_data_gateway.py tests/test_data_unification.py tests/test_phase6_wave_a.py tests/test_brain_extensions.py tests/test_train_ui_semantics.py` -> pass

### Current debt snapshot

- `S110 / S112`: 0
- `PLW0603 global-statement`: 0
- `PLC0415 import-outside-top-level`: 0

### Blueprint recalibration after repo scan

- 重新读取并采用技能：`pi-planning-with-files`
- 复核最新提交、当前 planning files 与 `docs/plans/V1_1_IMPLEMENTATION_BLUEPRINT_20260314.md`
- 核对到的关键基线变化：
  - `Phase 6` Wave A-F 已完成
  - `brain/presentation.py`、`app/interfaces/web/presentation.py`、`app/interfaces/web/contracts.py` 已成为稳定 seam
  - `app/stock_analysis_services.py` 与显式 contract 对象已开始成为默认接入面
  - cleanup gate 已进入 blocker-only 更合理

### Completed

- 已根据仓库真实状态重写 `docs/plans/V1_1_IMPLEMENTATION_BLUEPRINT_20260314.md`
- 已将 `v1.1` 主线从“训练协议硬化 + 最小结构解耦 + Instructor + Guardrails”修正为“训练协议硬化 + protocol tail hardening + Instructor + Guardrails”
- 已把时间线修正为：
  - `Week 0` 基线冻结
  - `Week 1-5` 模块推进
- 已更新 `task_plan.md`、`findings.md`、`progress.md`，使 planning files 与新版蓝图一致

### Verification

- 本轮未运行测试
- 本轮仅更新规划与蓝图文档，没有修改业务代码

### Module A slice 1 implementation

- 开始按 `v1.1` 蓝图顺序进入直接实现阶段，优先落 `Module A / Week 1` 的最小切片
- 新增 `app/training/experiment_protocol.py`
- 更新：
  - `app/training/controller_services.py`
  - `app/training/outcome_services.py`
  - `app/train.py`
  - `app/investment_body_service.py`
  - `app/training/__init__.py`
- 更新测试：
  - `tests/test_training_experiment_protocol.py`
  - `tests/test_training_controller_services.py`
  - `tests/test_train_ui_semantics.py`

### Completed

- `TrainingExperimentService.configure_experiment()` 现在会生成 canonical `experiment_spec`
- controller 已显式持有：
  - `experiment_protocol`
  - `experiment_review_window`
  - `experiment_promotion_policy`
- `TrainingResult` 已新增：
  - `experiment_spec`
  - `run_context`
- `TrainingOutcomeService` 已在生成周期结果时附加 protocol/run-context 信息
- `TrainingPersistenceService.save_cycle_result()` 已把 `experiment_spec` 与 `run_context` 写入 `cycle_*.json`
- `InvestmentBodyService._to_result_dict()` 已把这两块信息暴露给 commander/runtime 结果面

### Verification

- Focused:
  - `.venv/bin/pytest -q tests/test_training_experiment_protocol.py tests/test_training_controller_services.py tests/test_train_ui_semantics.py` -> pass
  - `.venv/bin/ruff check app/training/experiment_protocol.py app/training/controller_services.py app/training/outcome_services.py app/train.py app/investment_body_service.py app/training/__init__.py tests/test_training_experiment_protocol.py tests/test_training_controller_services.py tests/test_train_ui_semantics.py` -> pass
  - `.venv/bin/pyright app/training/experiment_protocol.py app/training/controller_services.py app/training/outcome_services.py app/train.py app/investment_body_service.py app/training/__init__.py tests/test_training_experiment_protocol.py tests/test_training_controller_services.py tests/test_train_ui_semantics.py` -> 0 errors

## v1.1 remaining 5 cuts completed

### Completed

- `Module A / cut 2`:
  - 新增 `app/training/review_protocol.py`
  - 复盘阶段现在会基于 `experiment_review_window` 生成 `recent_results` 与 `review_basis_window`
  - `ReviewMeetingService` / `ReviewMeeting.run_with_eval_report()` 已接受滚动窗口事实
- `Module A / cut 3`:
  - 新增 `app/training/promotion_services.py`
  - 新增 `app/training/lineage_services.py`
  - `TrainingResult` / `cycle_*.json` / commander result 现在显式记录 `promotion_record` 与 `lineage_record`
  - `build_cycle_run_context()` 已在 candidate auto-apply 时把 active ref 正确指向候选配置
- `Module B`:
  - `invest/meetings/selection.py` 已把 hunter / meeting `confidence` 统一收敛到 0-1 范围
  - `AgentContext.effective_confidence()` 已成为 `run_with_context()` 的默认读取入口
- `Module C`:
  - 新增 `brain/structured_output.py`
  - `BrainRuntime` 在 protocol wrapping 前会对 `invest_ask_stock`、`invest_training_plan_create`、`invest_training_plan_execute` 做 schema-first normalization
- `Module D`:
  - 新增 `brain/guardrails.py`
  - mutating tool 在执行前新增轻量 guardrail：
    - 阻断 placeholder 参数
    - 阻断高风险 update 的空 `patch`
    - 阻断缺失 `plan_id` 的 `invest_training_plan_execute`

### Verification

- Focused slices:
  - `.venv/bin/pytest -q tests/test_training_review_protocol.py tests/test_training_controller_services.py tests/test_review_meeting_v2.py tests/test_phase6_wave_a.py` -> pass
  - `.venv/bin/pytest -q tests/test_training_experiment_protocol.py tests/test_training_promotion_lineage.py tests/test_training_controller_services.py tests/test_train_ui_semantics.py` -> pass
  - `.venv/bin/pytest -q tests/test_meeting_refinement.py tests/test_ask_stock_model_bridge.py` -> pass
  - `.venv/bin/pytest -q tests/test_brain_runtime.py tests/test_commander_mutating_workflow_golden.py tests/test_schema_contracts.py` -> pass
- Consolidated:
  - `.venv/bin/pytest -q tests/test_training_experiment_protocol.py tests/test_training_review_protocol.py tests/test_training_promotion_lineage.py tests/test_training_controller_services.py tests/test_train_ui_semantics.py tests/test_review_meeting_v2.py tests/test_phase6_wave_a.py tests/test_meeting_refinement.py tests/test_ask_stock_model_bridge.py tests/test_brain_runtime.py tests/test_commander_mutating_workflow_golden.py tests/test_schema_contracts.py` -> pass
  - `ruff check ...`（本轮涉及文件）-> pass
  - `pyright ...`（本轮涉及文件）-> `0 errors`

## Instructor expansion and promotion/lineage observability

### Completed

- `Instructor` 风格 structured-output 适配继续扩面：
  - `brain/structured_output.py` 现在除了 `invest_ask_stock`、`invest_training_plan_create`、`invest_training_plan_execute` 外，还覆盖：
    - `invest_control_plane_update`
    - `invest_runtime_paths_update`
    - `invest_evolution_config_update`
  - `invest_training_plan_execute` 现在会归一化：
    - `result_overview`
    - `latest_result`
    - `latest_result.promotion_record`
    - `latest_result.lineage_record`
- commander/web 侧的 promotion/lineage 展示已补齐：
  - `app/commander_support/training.py` 新增 `summarize_latest_training_result()`
  - `attach_training_lab_paths()` 现在会在 `training_lab.run.latest_result` 中暴露 promotion / lineage 摘要
  - `app/commander_support/presentation.py` fallback display 现在会把最新训练周期、收益、晋升状态、lineage 状态合成到 human view
  - `brain/presentation.py` 的 `training_execution` receipt 也会把 promotion / lineage 作为 facts 暴露

### Verification

- Focused:
  - `.venv/bin/pytest -q tests/test_structured_output_adapter.py tests/test_brain_runtime.py tests/test_commander_cli_view.py tests/test_web_training_lab_api.py` -> pass
  - `ruff check brain/structured_output.py brain/presentation.py app/commander_support/training.py app/commander_support/presentation.py tests/test_structured_output_adapter.py tests/test_brain_runtime.py tests/test_commander_cli_view.py tests/test_web_training_lab_api.py` -> pass
  - `pyright brain/structured_output.py brain/presentation.py app/commander_support/training.py app/commander_support/presentation.py tests/test_structured_output_adapter.py tests/test_brain_runtime.py tests/test_commander_cli_view.py tests/test_web_training_lab_api.py` -> `0 errors`
- Expanded:
  - `.venv/bin/pytest -q tests/test_structured_output_adapter.py tests/test_brain_runtime.py tests/test_commander_cli_view.py tests/test_web_training_lab_api.py tests/test_web_server_contract_headers.py tests/test_commander_unified_entry.py tests/test_commander.py` -> pass

## 2026-03-15

### Session start

- 读取并沿用 `pi-planning-with-files` planning context：
  - `task_plan.md`
  - `findings.md`
  - `progress.md`
- 基于大规模重构后的真实代码，重新审查：
  - 架构与入口
  - 训练协议与 review/promotion/lineage 链路
  - runtime structured output / guardrails
  - presentation / web contract seam
  - freeze gate 当前门槛

### Completed

- 复核主链文档：
  - `docs/MAIN_FLOW.md`
  - `docs/TRAINING_FLOW.md`
- 复核关键实现：
  - `brain/runtime.py`
  - `brain/structured_output.py`
  - `brain/guardrails.py`
  - `app/training/experiment_protocol.py`
  - `app/training/review_stage_services.py`
  - `app/training/outcome_services.py`
  - `app/lab/evaluation.py`
  - `app/interfaces/web/presentation.py`
  - `app/freeze_gate.py`
  - `invest/contracts/agent_context.py`
  - `invest/contracts/stock_summary.py`
- 明确了升级方案需要重排：
  - `Instructor` / `Guardrails` 已有内建雏形，不再适合作为 `v1.1` 的“从零接入”叙事
  - 当前应优先继续做训练协议、promotion/lineage、contract tail、structured output / guardrails coverage 的深化
  - `PySR / E2B / Temporal` 后移到 `v1.2+`
- 已更新 planning files，记录本轮系统再审查后的新判断

### Verification

- `source .venv/bin/activate && pytest -q tests/test_structured_output_adapter.py tests/test_brain_runtime.py tests/test_lab_artifacts.py tests/test_freeze_gate.py` -> pass
- `source .venv/bin/activate && python -m app.freeze_gate --mode quick` -> pass

### Phase 0-5 execution hardening

- 新增 `docs/plans/V1_1_EXECUTION_FREEZE_20260315.md`
- 更新：
  - `app/freeze_gate.py`
  - `tests/test_freeze_gate.py`
  - `tests/test_v2_contracts.py`
  - `tests/test_structured_output_adapter.py`
  - `tests/test_brain_runtime.py`
  - `tests/test_lab_artifacts.py`
  - `tests/test_web_training_lab_api.py`
  - `tests/test_governance_phase_a_f.py`
- 更新 planning files：
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

### Completed

- `Phase 0`：
  - 冻结 `v1.1` 当前执行基线
  - 明确 frozen seam、默认 gate、Phase 0-5 验收标准
  - quick freeze gate 已纳入本轮核心 seam 与 focused suites
- `Phase 1`：
  - training governance / realism 摘要已进入 artifact brief、status summary 与 web status view 的测试面
- `Phase 2`：
  - `confidence` clamp 与 legacy helper 已补 focused tests
- `Phase 3`：
  - structured-output 的 training read side、config read side、agent prompt read side 已补 focused tests
- `Phase 4`：
  - 新增 guardrail 规则已补 focused tests：
    - `fixed_cutoff_missing_date`
    - `sequence_cutoff_missing_dates`
    - `regime_balanced_missing_targets`
    - `invalid_llm_mode`
    - `blank_runtime_path`
    - `relative_runtime_path`
    - `missing_agent_name`
    - `empty_system_prompt`
- `Phase 5`：
  - commander status / web status 的治理汇总与卡片已补 focused tests

### Verification

- Focused lint:
  - `source .venv/bin/activate && ruff check app/freeze_gate.py tests/test_freeze_gate.py tests/test_v2_contracts.py tests/test_structured_output_adapter.py tests/test_brain_runtime.py tests/test_lab_artifacts.py tests/test_web_training_lab_api.py tests/test_governance_phase_a_f.py` -> pass
- Focused type:
  - `source .venv/bin/activate && pyright app/freeze_gate.py tests/test_freeze_gate.py tests/test_v2_contracts.py tests/test_structured_output_adapter.py tests/test_brain_runtime.py tests/test_lab_artifacts.py tests/test_web_training_lab_api.py tests/test_governance_phase_a_f.py` -> `0 errors`
- Focused tests:
  - `source .venv/bin/activate && pytest -q tests/test_freeze_gate.py tests/test_v2_contracts.py tests/test_structured_output_adapter.py tests/test_brain_runtime.py tests/test_lab_artifacts.py tests/test_web_training_lab_api.py tests/test_governance_phase_a_f.py` -> pass

## Final closure

### Additional implementation

- 为修复最终复审里发现的观测尾巴，继续更新：
  - `app/training/simulation_services.py`
  - `app/training/outcome_services.py`
  - `app/training/reporting.py`
  - `invest/leaderboard/engine.py`
- 新增/更新回归测试：
  - `tests/test_training_controller_services.py`
  - `tests/test_train_ui_semantics.py`
  - `tests/test_leaderboard_snapshot_exclusion.py`

### Completed

- 训练 trade payload 现在会在持久化前清洗非有限数值，避免 `NaN/Inf` 落盘污染 JSON
- realism metrics / realism summary 现在会忽略非有限数值，避免聚合结果被单个异常 trade 污染
- leaderboard 收集现在会排除 `config_snapshots/` 目录中的伪 cycle 文件，避免出现 `unknown::config_snapshots`
- 已在修复后的代码上完成新的完整验证与新的 `20` 轮训练复核

### Verification

- Repair-focused:
  - `source .venv/bin/activate && pytest -q tests/test_training_controller_services.py tests/test_leaderboard_snapshot_exclusion.py tests/test_train_ui_semantics.py` -> pass
  - `source .venv/bin/activate && ruff check app/training/simulation_services.py app/training/outcome_services.py app/training/reporting.py invest/leaderboard/engine.py tests/test_training_controller_services.py tests/test_leaderboard_snapshot_exclusion.py tests/test_train_ui_semantics.py` -> pass
  - `source .venv/bin/activate && pyright app/training/simulation_services.py app/training/outcome_services.py app/training/reporting.py invest/leaderboard/engine.py tests/test_training_controller_services.py tests/test_leaderboard_snapshot_exclusion.py tests/test_train_ui_semantics.py` -> `0 errors`
- Full:
  - `source .venv/bin/activate && ruff check .` -> pass
  - `source .venv/bin/activate && pyright .` -> `0 errors`
  - `source .venv/bin/activate && pytest -q` -> pass
  - `source .venv/bin/activate && python -m app.freeze_gate --mode quick` -> pass
  - `source .venv/bin/activate && python -m app.freeze_gate --mode full` -> pass

### 20-cycle rerun

- 执行命令：
  - `source .venv/bin/activate && python train.py --cycles 20 --force-full-cycles --output outputs/phase_v11_validation_20260315_final --meeting-log-dir outputs/phase_v11_validation_20260315_final/meetings --config-audit-log-path outputs/phase_v11_validation_20260315_final/config_changes.jsonl --config-snapshot-dir outputs/phase_v11_validation_20260315_final/config_snapshots`
- 运行事实：
  - 使用真实数据
  - `LLM API key is empty`，因此持续走 `fallback to algorithm path`
- 产物核验：
  - `outputs/phase_v11_validation_20260315_final/leaderboard.json` 仅包含 `momentum / defensive_low_vol / mean_reversion`
  - `rg -n "NaN" outputs/phase_v11_validation_20260315_final` -> 无结果
- 汇总结果：
  - `20` 轮，盈利 `8`，亏损 `12`，盈利率 `0.4`
  - `governance_metrics.total_cycles=20`
  - `governance_metrics.promotion_attempt_count=5`
  - `governance_metrics.promotion_applied_count=0`
  - `governance_metrics.candidate_pending_count=5`
  - `governance_metrics.active_candidate_drift_rate=0.25`
  - `realism_summary.avg_trade_amount=668815085.8005`
  - `realism_summary.avg_holding_days=9.3625`
  - `realism_summary.high_turnover_trade_count=0`
  - `freeze_gate_evaluation.ready=True`
  - `freeze_gate_evaluation.passed=False`
  - 主要阻塞项：`win_rate`、`avg_sharpe`、`benchmark_pass_rate`、`research_feedback_gate`
