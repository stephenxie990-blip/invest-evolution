# Findings & Decisions

## Requirements
- 基于真实仓库分析“训练链”和“问股链”当前割裂点
- 给出统一研究引擎的研究方案，而非泛泛概念图
- 方案必须满足：同一语义、同一因果、同一验证闭环
- 方案需要兼顾短期可落地与长期架构收敛
- 在研究方案基础上，继续输出可指导实施的完整蓝图，包括阶段规划、验收标准、subagent 调度与 skills 使用方案

## Research Findings
- 训练主链核心在 `app/train.py` 的 `SelfLearningController`，负责数据加载、模型处理、选股会议、模拟交易、评估、优化与冻结。
- 问股主链核心在 `app/stock_analysis.py` 的 `StockAnalysisService`，由 `app/commander.py` 的 `ask_stock()` 直接透传调用。
- 当前运行时层已有统一意识，但偏“工件与目录统一”，例如 `docs/RUNTIME_STATE_DESIGN.md` 主要统一了 `runtime/` 输出、锁、工件与状态文件。
- 数据层统一已基本完成，`docs/DATA_LAYER_UNIFICATION_REPORT.md` 明确训练与 Web 已共享同一离线库、同一 `DataManager` / repository 口径。
- 因此当前真正未统一的重点，不在数据源，而在“研究语义层 / 状态层 / 归因闭环”。
- 用户认可“四层闭环对象”升级方向，并额外强调两点需要在执行蓝图中补强：
  - `PolicySnapshot.version_hash` 需要更强的可复现签名
  - `OutcomeAttribution` 需要明确多 horizon 的评分时钟
- 已形成两份关键文档：
  - `docs/RESEARCH_ENGINE_UNIFICATION_PROPOSAL_20260312.md`
  - `docs/RESEARCH_ENGINE_UNIFICATION_EXECUTION_BLUEPRINT_20260312.md`

## Decisions
| Decision | Rationale |
|----------|-----------|
| 先验证现有代码中是否已有近似状态对象 | 可能存在可复用基础，不必从零命名/建模 |
| 重点梳理执行链，而不是只看目录名 | 真正割裂点通常在调用路径和状态读写处 |
| 将已有文档作为旁证，不直接等同于现状事实 | 文档可能超前或滞后于代码 |
| 将执行蓝图单独成文 | 便于从“研究共识”切换到“可执行项目计划” |
| 在蓝图中把 `version_hash` 与 `multi-horizon scoring` 明确成硬约束 | 响应用户补强意见，避免后期归因漂移 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| 环境无 `python` | 使用 shell 原生命令与 `python3` 作为后备 |
| 大文件一次性读取被截断 | 改为按关键方法与行段分块读取 |
| 通过 shell 调用 `apply_patch` 触发警告 | 后续改为 here-doc 直接写文件，避免重复警告 |

## Resources
- `/Users/zhangsan/.agents/skills/agentic-engineering/SKILL.md`
- `/Users/zhangsan/.agents/skills/pi-planning-with-files/SKILL.md`
- `/Users/zhangsan/.agents/skills/eval-harness/SKILL.md`
- `/Users/zhangsan/.agents/skills/verification-loop/SKILL.md`
- `/Users/zhangsan/.agents/skills/python-patterns/SKILL.md`
- `/Users/zhangsan/.agents/skills/python-testing/SKILL.md`
- `/Users/zhangsan/.agents/skills/search-first/SKILL.md`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/app/train.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/app/stock_analysis.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/docs/RESEARCH_ENGINE_UNIFICATION_PROPOSAL_20260312.md`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/docs/RESEARCH_ENGINE_UNIFICATION_EXECUTION_BLUEPRINT_20260312.md`

## Visual/Browser Findings
- 当前尚未使用浏览器/图片工具

## Implementation Findings
- `ask_stock` 已具备 `as_of_date` 一等语义：工具层与 model bridge 都只读取 `effective_as_of_date` 之前数据。
- live 场景下，问股 bridge 默认复用 live controller 的 active model；历史回放场景下自动切换为 `config_default_replay_safe`，避免 runtime params 未来泄漏。
- `dashboard` 已降级为 `ResearchHypothesis` projection；问股主语义迁移到 `research.snapshot/policy/hypothesis/scenario`。
- `ResearchCaseStore` 现已支持：
  - case / attribution 落盘
  - 按 `policy_id / symbol / as_of_date / horizon` 检索
  - calibration report 落盘
- `ResearchScenarioEngine` 已在 ask 集成链路中接通：存在相似已归因 case 时自动从 heuristic 切换到 empirical case similarity。
- 训练主循环尚未显式消费 calibration report；这部分仍属于下一阶段训练侧吸收工作，但 artifact 已稳定可读。

## Validation Findings
- 新增并通过训练侧闭环相关测试：
  - `tests/test_review_meeting_v2.py`
  - `tests/test_research_case_store.py`（新增 training feedback coverage）
  - `tests/test_train_ui_semantics.py`（新增 report / snapshot feedback coverage）
- 使用项目 `.venv` 运行 targeted pytest 可通过：
  - `tests/test_research_contracts.py`
  - `tests/test_research_case_store.py`
  - `tests/test_research_attribution_engine.py`
  - `tests/test_ask_stock_model_bridge.py`
  - `tests/test_stock_analysis_react.py`
  - `tests/test_schema_contracts.py`
- 额外抽样验证 `tests/test_commander_unified_entry.py -k ask_stock_works_via_natural_language_fallback` 通过。
- 仓库中存在与本次改动无关的 commander intent/transcript 失败样例，当前未一并修复，避免越界修改。

## Training Feedback Loop
- `SelfLearningController` 已消费 `ResearchCaseStore.build_training_feedback(...)`，并把 `research_feedback` 写入 cycle dict、`EvalReport.metadata`、training report、JSON 落盘与 commander snapshot。
- `ReviewMeeting._compile_facts()` 现在会优先从 `EvalReport.metadata.research_feedback` 读取 ask 侧校准反馈，并统一透传到 strategist / evo judge / review decision 的事实上下文。
- `ReviewMeeting` fallback 已开始消费 `recommendation.bias`：当 ask 侧给出 `tighten_risk` / `recalibrate_probability` 时，会强制转向保守风险口径，而不是继续沿用纯收益驱动 fallback。
- `ReviewDecisionAgent` prompt 已纳入问股校准摘要，确保 LLM 侧与 fallback 侧看到同一份闭环反馈。

## Feedback-Driven Optimizer & Freeze Gate
- `SelfLearningController` 现在会基于 multi-horizon `research_feedback` 计算 deterministic optimization plan；当 `bias` 为 `tighten_risk` / `recalibrate_probability` 且 horizon 指标越界时，会在 cooldown 约束下自动收紧 `position_size`、`cash_reserve`、`stop_loss_pct`、`trailing_pct` / `take_profit_pct`。
- `app/training/optimization.py` 已从“只认连续亏损”升级为“连续亏损 + research feedback 双触发”；若两者同时出现，会在一次 optimization run 中共同落地到 runtime override 与 YAML mutation。
- `app/training/reporting.py` 新增 `evaluate_research_feedback_gate()` 与 `evaluate_freeze_gate()`，冻结判断不再只看收益/Sharpe/回撤，还会显式检查 calibration bias、Brier-like score 与各 horizon 的 hit/invalidation/interval-hit 指标。
- freeze gate 对 `insufficient_samples` 默认采取 neutral 策略：样本不足不会直接放行也不会永久阻断；只有 gate active 且失败时才阻止冻结。
- `Commander` snapshot 已能看到 `freeze_gate_evaluation` 与 `research_feedback_optimization`，便于观测训练是否被 ask 侧校准结果卡住或触发自动调参。

## Promotion Gate Calibration
- `app/lab/evaluation.py` 的 `build_promotion_summary()` 已升级为“收益门 + 策略分门 + 基准门 + 校准门”联合判定；当 `optimization.promotion_gate.research_feedback` 配置存在时，晋升结论会显式检查最新可用 `research_feedback`。
- promotion gate 当前采用“latest available feedback” 语义：优先使用最新 `cutoff_date/cycle_id` 的校准快照作为候选模型最新校准状态，并把该快照摘要落在 `promotion.research_feedback.latest_feedback`。
- 当 promotion gate 开启 research feedback 检查但训练 run 结果里没有 `research_feedback` 时，会产生 `research_feedback.available=false` 的失败检查并拒绝晋升。
- `InvestmentBodyService._to_result_dict()` 已透传 `TrainingResult.research_feedback`，因此真实训练 run 生成的 evaluation summary 现在能感知 ask/train 校准闭环状态，而不再只是测试载荷里的静态字段。

### Phase 10: Training Plan Default Calibration Gate
- `TrainingLabArtifactStore.build_training_plan_payload(...)` 现在会统一注入默认 `optimization.promotion_gate.research_feedback` 模板，保证新建 training plan 天然带有 calibration gate。
- 默认模板字段当前固定为：`min_sample_count=5`、`blocked_biases=[tighten_risk, recalibrate_probability]`、`max_brier_like_direction_score=0.25`、`horizons.T+20.{min_hit_rate=0.45,max_invalidation_rate=0.30,min_interval_hit_rate=0.40}`。
- `promotion_gate` 采用深度合并：上层如 `min_samples` 保留用户值；`research_feedback` 的局部 override 只覆盖显式字段，未覆盖部分继续回落到默认模板。
- 由于 calibration gate 现在是 plan 默认项，凡是依赖 promote verdict 的旧测试样本如果缺少 `research_feedback`，都应显式补齐校准样本，否则应按 `research_feedback.available` 拒绝晋升。
- 本次为 `tests/test_lab_artifacts.py` 增加默认注入 / merge 回归；为 `tests/test_commander.py` 增加 public API 持久化回归，并同步修正 baseline promote 用例的数据前提。

### Phase 11: Calibration Gate Visibility
- 新建 training plan 现在除了 `optimization.promotion_gate.research_feedback` 原始阈值，还会额外给出 `guardrails.promotion_gate.research_feedback` 人类可读摘要，包含 `summary`、`reason_codes`、`policy_source` 与 `thresholds`，便于 CLI/Web 直接展示默认校准门为何启用、启用了哪些约束。
- `policy_source.mode` 当前区分 `default_injected` 与 `default_plus_override`，可以一眼看出当前 plan 是纯默认模板还是“默认模板 + 用户覆盖”。
- `execute_training_plan()` / `train_once()` 返回的 `training_lab` 现在不再只给 path/id，还会回传 `plan.guardrails` 与 `evaluation.promotion.research_feedback` 摘要，让前台在不额外打开 artifact 的情况下直接看到校准门是否通过，以及失败原因是否是 `research_feedback.available`。
- 对于缺少 calibration 样本的执行结果，前台现在能直接拿到 `summary=未通过 research_feedback 校准门：缺少可用研究反馈样本。`，避免用户只看到 rejected verdict 却不知道是“收益问题”还是“校准样本缺失”。

### Phase 12: Daily Review & Closure
- 今日核心闭环已经贯通到“ask 研究 → research_feedback → training/report/review → optimizer/freeze gate → promotion gate → training plan default gate → API/CLI 可见摘要”。
- 复核结果表明：当前最值得保留的工程决策是“同一研究语义对象沿链路透传”，而不是为 ask/train 各自继续增补局部解释层。
- `app/stock_analysis.py`、`invest/research/case_store.py`、`app/train.py`、`app/lab/evaluation.py`、`app/lab/artifacts.py`、`app/commander.py` 之间的字段口径目前已基本收敛到 `research_feedback` / `promotion.research_feedback` / `guardrails.promotion_gate.research_feedback` 这三层。
- 今日收口审查中，完整 targeted regression 二次复跑通过；首次批量运行时出现过一次 `tests/test_schema_contracts.py` 的瞬时失败，但单独复跑与整组复跑均稳定通过，当前未复现为持续性问题。
- 非阻塞收口事项：当前工作树仍混合了实现代码、契约文档、planning 文档与 golden tests，适合下一步按“研究闭环核心实现 / 契约文档更新 / planning 记录”三组进行提交或归档，而不是一次性无差别打包。

### Phase 13: Executable Remediation Action Plan
- 项目下一阶段的最佳推进方式不是继续横向加功能，而是围绕“研究闭环已打通”的事实，做结构减重、typed contract 收敛与状态/工件治理清晰化。
- 推荐 owner 切法不是按技术栈，而是按闭环职责：`Research Kernel`、`Training Runtime`、`Runtime & Interaction`、`Data & Lineage`、`Quality & Governance` 五类 owner。
- P0 的核心不是修 bug，而是做结构性收敛：拆轻 `SelfLearningController`、拆轻 `CommanderRuntime`、固化 calibration schema、减少裸 `dict`、明确生命周期边界。
- subagent 调度应按“闭环 work unit”而不是按文件切分，避免多个 agent 长时间交叉改同一超级类。
- 已形成正式整改文档：`docs/PROJECT_REMEDIATION_ACTION_PLAN_20260312.md`。


## Runtime Response Envelope & Research Asset Findings
- `brain/task_bus.py` 的共享 response envelope 已成为 runtime / commander / ask_stock 的统一响应拼装入口，减少了 `message/reply/feedback/next_action` 各自散落拼装导致的漂移风险。
- `SelfLearningController` 已完成第一轮 service 化：`TrainingFeedbackService`、`FreezeGateService`、`TrainingPersistenceService` 把 calibration feedback、freeze gate、cycle artifact 持久化从控制器本体抽离出来。
- `CommanderRuntime` 现可直接查询 `research cases / attributions / calibration`，意味着 research asset 不再只是 ask/train 内部文件，而是可审计、可检索、可重放的一等运行时资产。
- 新增 research asset tool 后，P1 的“research 中间层升格”开始具备真实运维入口，而不仅仅是 ask_stock 返回体里的附属字段。
