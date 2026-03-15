# Phase 6 Closeout and v1.1 Transition Plan

## Goal

记录 `Phase 6` 结构性重构的完成状态，并将该结果转化为 `v1.1` 的真实起点：在保持现有 CLI / Web / runtime 契约稳定的前提下，不再重复做大规模结构迁移，而是把已形成的 `interface -> application -> domain -> infrastructure` 接缝用于训练协议硬化、协议尾项固化与治理能力落地。

## Phases

| Phase | Status | Scope | Notes |
|---|---|---|---|
| 0 | complete | 建立实施计划、审阅关键模块、确认可落地点 | 已完成 |
| A | complete | 建立结构骨架、兼容导出、最小架构守卫 | 已落地并验证 |
| B | complete | 抽训练编排 service | 已完成控制器主链与尾部 glue 的进一步下沉，service 驱动成为默认实现 |
| C | complete | 抽投资分析与会议编排 service | 已统一 invest facade 调用边界，review / policy / optimization 不再散落依赖底层细节 |
| D | complete | 拆 `market_data/` 子服务 | 已收口上层调用迁移，`market_data/services/` 成为默认 facade 接入面 |
| E | complete | runtime protocol / presentation 解耦，Web 资源化路由 | 已完成 brain receipt presenter 抽离与 web contract/display 接缝下沉 |
| F | complete | 测试分层、架构守卫、兼容层清理 | 已补齐 Wave E/F 守卫与回归，完成全链固化 |

## Current Focus

- `Phase 6` 已视为完成，当前不再把结构性重构当作 `v1.1` 主任务。
- 当前主线切换为：训练协议硬化、promotion/lineage 纪律、协议尾项固化、`Instructor`、`Guardrails`。
- 已完成的 presentation / contract / service seam 直接作为 `v1.1` 起点消费，不重复规划“最小结构解耦”。
- cleanup 继续保留，但仅按 blocker-driven 原则推进，不扩张成新一轮整理工程。
- 每一步结束都更新 planning files，并做 focused verification。

## Protocol Convergence Plan

| Step | Status | Scope | Notes |
|---|---|---|---|
| 1 | complete | 旧协议消费点盘点与分级 | 已确认主要债务集中在 `AgentContext.metadata["confidence"]`、模型层裸 dict 摘要、`ask_stock` payload 重复导出 |
| 2 | complete | 模型层与研究层收口 | 已显式引入 `AgentContext.confidence`，模型层主动产出 `StockSummaryView` |
| 3 | complete | 会议层与 Agent 层收口 | `hunters/specialists/reviewers` 已统一面向 `Sequence[Mapping]` |
| 4 | complete | `ask_stock` payload 定界 | 已补 `request / identifiers / resolved_entities` canonical 分区，并保留顶层兼容字段 |
| 5 | complete | 兼容层退役与全量验证 | 已删 `app/stock_analysis.py` 中 4 个无消费者 wrapper，全仓验证通过 |

## Contract Hardening Blueprint

| Step | Status | Scope | Notes |
|---|---|---|---|
| A | complete | 蓝图细化与剩余隐式契约盘点 | 已确认重点在 `ask_stock` schema 守卫、`confidence` 读取收口、兼容镜像降级 |
| B | complete | `ask_stock` schema 守卫 | 已补 canonical section 稳定性测试 |
| C | complete | review/training/research 隐式字段收口 | `AgentContext.effective_confidence()` 已成为默认入口 |
| D | complete | 兼容路径降级与重复装配清理 | 已继续削薄 `stock_analysis` 兼容 wrapper |
| E | complete | 全仓验证与结项 | `ruff / pyright / pytest` 全部通过 |

## Wave Completion Definition

### Wave B complete when

- `app/train.py` 中训练主链仅保留控制器协调与兼容入口
- 剩余 persistence / report / feedback / freeze 相关包装方法要么进一步下沉，要么明确成为稳定兼容 facade
- 训练 orchestration 的关键路径可通过 service 层单测覆盖

### Wave C complete when

- invest 相关上层入口统一通过 `invest/services/` facade 访问会议与进化能力
- 控制器、优化链、分析链不再散落依赖 meeting / evolution 内部细节
- 兼容导出保持稳定，现有 CLI / runtime / web 契约不变

### Wave D complete when

- `market_data/services/` 成为上层默认接入面
- commander / web / training / 其他读侧调用不再新增对旧聚合实现的直接耦合
- facade 覆盖 query / availability / resolver / benchmark / quality / sync 关键职责，并有测试守卫

## Monitoring Checklist

- 每完成一个 wave，更新 `progress.md` 和 `findings.md`
- 每完成一个 wave，至少执行一次 focused verification
- 全部完成后执行全量验证：`ruff`、`pyright`、`pytest`、`freeze_gate`
- 如出现连续两次同类失败，先记录到 planning files，再切换修复路径

## Wave E Completion Definition

- `brain/runtime.py` 的 human-readable receipt / narration 逻辑已有独立 presenter 接缝
- `app/web_server.py` 不再直接承载 runtime contract 路由实现与 display payload 组装细节
- `app/interfaces/web` 成为 web 资源路由与响应帮助逻辑的默认入口

## Wave F Completion Definition

- 为 `Wave E` 新增的 presentation / contracts 边界补充存在性检查与 import guard
- focused verification 覆盖 runtime contract、web human display、commander unified entry 这些高风险路径
- 全量 `ruff / pyright / pytest / freeze_gate` 全部通过

## Constraints

- 不破坏现有对外契约
- 不回退用户已有未提交修改
- 每个波次结束都做最小验证
- 优先抽职责，不做大规模 rename

## Pre-v1.1 Cleanup Gate

### Goal

- 在进入 `v1.1` 新能力开发前，先收掉高确定性的静态质量债务
- 降低“继续叠功能”时被历史静默失败和兼容胶水反噬的概率

### Cleanup buckets

- Bucket 1：静默异常与无日志降级
- Bucket 2：JSON/JSONL/event 读取链路的损坏输入可观测性
- Bucket 3：无效空块、可删除的 `finally: pass`、兼容壳杂质
- Bucket 4：可以安全迁移的 `late import`
- Bucket 5：更长期的 `global state / bootstrap singleton / optional dependency seam`

### Exit criteria

- 高价值运行链路中不再存在可直接确认的 `except: pass / continue` 静默吞错
- artifact / event / memory / callback 读取失败具备日志
- 新增清洁回归测试通过
- focused verification 与全量验证重新回绿

### Current status

- Bucket 1：已完成，核心目录 `S110 / S112` 清零
- Bucket 2：已完成主要链路，artifact / event / memory / callback 已具备可观测性
- Bucket 3：已完成首批无效空块清理，后续按碰到即收
- Bucket 4：已完成，`PLC0415` 已从 32 降到 0
- Bucket 5：已完成第一阶段，`PLW0603` 已清零；下一步聚焦真正的 bootstrap/provider seam

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| 过早大迁移导致 diff 噪音过大 | review 难度上升 | 先建立新模块并做薄封装 |
| 主链控制器被过度拆分导致行为漂移 | 训练 / Web 回归 | 每次只抽一类职责，保留兼容 facade |
| 路由重组牵连合同测试 | API 回归 | 先做资源层包装，不立即变更路径 |

## Errors Encountered

| Error | Attempt | Resolution |
|---|---|---|
| `python` command unavailable in shell | 1 | 改用 `python3` |

## Completed This Session

- 新增 `app/application/` 与 `app/interfaces/` 骨架目录
- 新增 `invest/services/` 与 `market_data/services/` service facade
- `app/web_server.py` 已切换到 `app.interfaces.web.register_runtime_interface_routes`
- 新增 Phase 6 Wave A 架构守卫与 facade 验证测试
- 从 `SelfLearningController.run_training_cycle()` 抽出 `TrainingCycleDataService`
- 训练主链已开始通过 `SelectionMeetingService` / `ReviewMeetingService` 调用关键编排路径
- 新增 `TrainingReviewService`，将 `EvalReport` 构造与 review decision 应用从主控制器中下沉
- `app/training/optimization.py` 已优先通过 `evolution_service` 调用进化引擎
- `app/commander_support/services.py` 已改为通过 `MarketQueryService` 访问 market data 读侧
- 已将本轮目标收敛为“彻底完成 Wave B / C / D”，并明确完成定义、监控项与验证门槛
- `TrainingLifecycleService` 已优先通过 persistence / freeze services 完成周期收尾，不再以内循环反向依赖 controller 包装方法
- `TrainingExperimentService` 已直接协调 LLM runtime / routing services，`configure_experiment()` 一带的服务边界进一步收紧
- `TrainingPolicyService` 已通过 `SelectionMeetingService.set_agent_weights()` 同步 agent 权重，去除上层对底层 meeting 属性的直接写入
- `app/training/optimization.py` 已统一通过 `EvolutionService` 边界驱动进化链，并保留对 legacy engine 的兼容适配
- `app/commander_support/status.py` 已切换到 `MarketQueryService`，补齐 `Wave D` 的一处剩余直接耦合
- 已完成 `Wave B / C / D` 收口，并补充相应回归测试
- 已进入 `Wave E / F` 并完成：
  - 新增 `app/interfaces/web/presentation.py`，收口 web display / contract 响应帮助逻辑
  - 新增 `app/interfaces/web/contracts.py` 与 `app/interfaces/web/routes/contracts.py`，将 runtime contract 路由下沉到 interface 层
  - 新增 `brain/presentation.py`，将 `brain/runtime.py` 的 human receipt builder 抽成独立 presenter
  - `app/web_server.py` 现仅保留薄适配与兼容 helper，不再直接持有 contract 路由实现
  - `tests/test_architecture_import_rules.py` 已新增 Wave E/F 边界守卫

## Supplementary Planning Outputs

- 已新增 `docs/plans/V1_1_IMPLEMENTATION_BLUEPRINT_20260314.md`
- 2026-03-14 仓库扫描后，蓝图已按真实基线重写
- `v1.1` 现明确收敛为“训练协议硬化 + protocol tail hardening + Instructor + Guardrails”
- `Phase 6` A-F 与 cleanup gate 进展已被视为 `v1.1` 的既有底座，而不是待完成前置任务
- `v1.1` 节奏已改为 `Week 0` 基线冻结 + `Week 1-5` 模块推进
- `PySR / E2B / Temporal` 被明确留在 `v1.2+`，不进入 `v1.1` 主版本范围
- 已启动 `pre-v1.1 cleanup gate`
- 第一批清理已完成：
  - `app/train.py` 的 event callback 失败不再静默吞掉
  - `app/runtime_artifact_reader.py` 的 JSON / JSONL / text 读取失败开始记录告警
  - `app/commander_support/observability.py` 的 runtime event JSONL 损坏行开始记录告警
  - `app/llm_gateway.py` 的 LiteLLM 初始化属性写入失败开始记录 debug 日志
  - `app/commander.py` 的 cycle artifact 路径拼装失败开始记录告警
  - `app/commander_support/services.py` 删除无效 `finally: pass`
  - 新增 `tests/test_observability_helpers.py`

## Verification Snapshot

- `ruff check .` 通过
- `pyright .` 通过，`0 errors`
- `.venv/bin/pytest -q` 通过
- `475 tests collected`
- `python -m app.freeze_gate --mode quick` 通过

## Wave B/C/D Final Verification

- `.venv/bin/ruff check .` 通过
- `.venv/bin/pyright .` 通过，`0 errors`
- `.venv/bin/pytest -q` 通过，`[100%]`
- `.venv/bin/python -m app.freeze_gate --mode quick` 通过

## Post-Scan v1.1 Transition

### Baseline assumptions

- `Phase 6` Wave A-F 已完成，可作为 `v1.1` 直接依赖的结构底座。
- `brain/presentation.py`、`app/interfaces/web/presentation.py`、`app/interfaces/web/contracts.py` 已成为新的 presentation / contract seam。
- `app/stock_analysis_services.py`、`invest/contracts/agent_context.py`、`invest/contracts/stock_summary.py` 已经把 stock-analysis 和 canonical contract surface 往显式协议方向推进。
- `pre-v1.1 cleanup gate` 已完成关键静默失败与低风险 import/global-state 收口，不宜在 `v1.1` 里继续无边界扩张。

### Active v1.1 modules

- 模块 A：训练协议与 experiment boundary
- 模块 B：protocol tail hardening + cleanup gate blocker-only 推进
- 模块 C：`Instructor` 接入现有 seam
- 模块 D：`Guardrails` 接入稳定后的 protocol/task-bus 边界
- 模块 E：观测、Freeze Gate、文档冻结

### Implemented slice

- 已完成 `Module A / Week 1` 的第一刀：
  - 新增 `app/training/experiment_protocol.py`
  - `configure_experiment()` 现在会生成 canonical `experiment_spec`
  - controller 已显式持有 `experiment_review_window` 与 `experiment_promotion_policy`
  - `TrainingResult` / `cycle_*.json` / commander body result 已显式记录 `experiment_spec` 与 `run_context`
- 当前已落盘的 `run_context` 至少包含：
  - `active_config_ref`
  - `candidate_config_ref`
  - `runtime_overrides`
  - `review_basis_window`
  - `fitness_source_cycles`
  - `promotion_decision`
- 这意味着训练产物已经从“纯结果 JSON”推进到“带协议上下文的结果 JSON”，后续可以继续接 `review window` 强化和 promotion lineage。

### Planning rule

- `v1.1` 期间不再启动新的 `Phase 6` 级结构迁移。
- 如需 cleanup，只处理阻塞训练协议、结构化输出或治理接线的问题。
- 每周推进顺序按蓝图的 `Week 0 + Week 1-5` 执行，不再沿用旧版“结构前置”假设。

## Wave E/F Final Verification

- `.venv/bin/ruff check .` 通过
- `.venv/bin/pyright .` 通过，`0 errors`
- `.venv/bin/pytest -q` 通过，`[100%]`
- `.venv/bin/python -m app.freeze_gate --mode quick` 通过

## v1.1 remaining cuts status

- 已完成：
  - `Module A / cut 2` 滚动复盘事实窗口
  - `Module A / cut 3` promotion / lineage 显式记录
  - `Module B` protocol tail hardening
  - `Module C` runtime structured output adapter
  - `Module D` mutating workflow guardrails
- 当前 `v1.1` 后续工作应切到：
  - 复盘新增记录在 commander / web 侧是否需要补展示
  - `Instructor` / schema-first adapter 是否要扩到更多高价值工具
  - `Guardrails` 是否继续补充 patch 语义级校验

## Instructor and observability status

- 已完成：
  - `Instructor` 风格 structured-output 继续扩到 config update 路径
  - `training_plan_execute` 增加 `result_overview` 与 `latest_result` 归一化
  - commander/web/runtime 三个展示入口都能看到 `promotion_record` 与 `lineage_record`
- 后续如果继续推进：
  - 给更多 `invest_*_get` / `invest_*_update` 路径补专属 normalize
  - 把 promotion / lineage 摘要接进更显式的 dashboard/ops 卡片，而不只停留在 human receipt

## 2026-03-15 Re-Audit After Refactor

### Goal

- 在“大调整和重构已经落地”的前提下，重新建立系统级理解，而不是沿用旧版 `v1.1` 假设。
- 审查重点聚焦：
  - 架构 seam 是否稳定
  - 功能模块边界是否已切换
  - 数据流 / 展示流 / 训练链路是否已经协议化
  - 原升级方案是否需要重排

### Confirmed baseline

- `BrainRuntime` 已内建 `RuntimeGuardrails` 与 `StructuredOutputAdapter`，并将治理指标挂到 runtime payload；这意味着“先接第三方 Guardrails / Instructor”不再是当前版本的起点。
- 训练链路已形成新的协议骨架：`ExperimentSpec`、`review_basis_window`、`cutoff_policy`、`promotion_policy`、`run_context`、`promotion_record`、`lineage_record`。
- `ReviewMeeting` 输入已经从“单轮裸结果”扩展为“滚动事实窗口 + 相似样本 + 因果诊断”。
- `brain/presentation.py` 与 `app/interfaces/web/presentation.py` 已成为稳定展示 seam，后续展示增强不应再回流到 entrypoint。
- `app/stock_analysis_services.py` 与 `invest/contracts/*` 已成为 stock-analysis 与 contract tail 收口的真实落点。
- `freeze_gate` 已把 contract drift、focused protocol regression、critical ruff、critical pyright 纳入 quick 门。

### Revised priority

- `P0`：继续硬化训练协议与 promotion/lineage 治理，一致化 Training Lab、runtime、commander、web 的记录与展示。
- `P0`：继续推进 canonical contract tail hardening，优先清理隐式 metadata fallback 和兼容镜像误用。
- `P1`：把内建 `StructuredOutputAdapter` 从规则 normalize 扩到更强 schema layer，但先沿现有 seam 做，不急于引第三方框架。
- `P1`：把内建 `RuntimeGuardrails` 从静态阻断扩成策略化治理层，优先覆盖高风险 mutating workflow。
- `P2`：在上述两层稳定后，再决定是否值得引入 `Instructor` / `Guardrails AI` 替换或包裹现有实现。
- `P3`：`PySR` / `E2B` / `Temporal` 后移到 `v1.2+`，仅在当前协议面和治理面足够稳定后再接。

### Planning rule

- 不再把 `v1.1` 描述为“从零引入新框架”。
- 现阶段的主任务是“把已经进入主链的内建治理与训练协议做深、做稳、做一致”。
- 如后续继续规划，优先围绕：
  - training protocol completion
  - promotion/lineage discipline
  - schema/contract tail hardening
  - structured output / guardrails coverage expansion

## 2026-03-15 Phase 0-5 execution lock

### Frozen scope

- `Phase 0`：冻结当前 `v1.1` 执行基线与 quick/full gate
- `Phase 1`：训练协议与治理摘要继续硬化
- `Phase 2`：`confidence` 尾项彻底收口到 contract helper
- `Phase 3`：内建 structured-output 深化到 training read side 与 config read side
- `Phase 4`：内建 runtime guardrails 升级到 cutoff/runtime path/agent prompt 语义校验
- `Phase 5`：CLI / web / runtime receipt 的治理与可观测性补齐

### Completed in this lock

- 已新增 `docs/plans/V1_1_EXECUTION_FREEZE_20260315.md`，把 frozen seams、默认 gates、Phase 0-5 验收标准落盘
- 已将 `freeze_gate` quick 门升级为覆盖：
  - `brain/structured_output.py`
  - `brain/guardrails.py`
  - `app/commander_support/status.py`
  - `app/interfaces/web/presentation.py`
  - `invest/contracts/agent_context.py`
  - `tests/test_v2_contracts.py`
  - `tests/test_lab_artifacts.py`
  - `tests/test_web_training_lab_api.py`
  - `tests/test_governance_phase_a_f.py`
- 已为本轮 Phase 0-5 新增 focused regression，覆盖：
  - `confidence` clamp 与 legacy helper
  - structured-output 的 training list/summary/config/prompt read side
  - guardrail 的 fixed/sequence/regime cutoff、runtime path、agent prompt 规则
  - training lab artifact brief 与 commander/web status governance 摘要

### Remaining execution order

- 先做全量验证：`ruff .`、`pyright .`、`pytest -q`、`freeze_gate quick/full`
- 再做系统级复审：架构、模块边界、数据流、训练链路、展示链、治理链一致性
- 复审通过后执行 `20` 轮训练，并以实际产物复盘优化效果

### Final closure

- 已完成全量验证：
  - `ruff check .`
  - `pyright .`
  - `pytest -q`
  - `python -m app.freeze_gate --mode quick`
  - `python -m app.freeze_gate --mode full`
- 已完成系统级二次复审，并确认：
  - 训练协议、contract tail、structured output、guardrails、presentation/ops seams 已形成稳定闭环
  - Phase 0-5 的验收目标均已在代码与测试面落地
- 已在修复后的代码上完成新的 `20` 轮训练验证：
  - 输出目录：`outputs/phase_v11_validation_20260315_final`
  - `leaderboard.json` 已不再把 `config_snapshots` 识别成模型
  - 新输出目录中已无 `NaN` 序列化污染
- 当前剩余不是系统实现缺口，而是策略性能门槛未过：
  - 最终 run 的 `freeze_gate_evaluation.ready=True`
  - 但 `passed=False`，主因是 `win_rate / avg_sharpe / benchmark_pass_rate / research_feedback_gate` 未达标
