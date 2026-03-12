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

## 2026-03-12 项目总体审核（架构/文件体系/清理前基线）

### 运行与验证基线
- 仓库为 Python 主后端 + React/Vite 前端混合单仓。
- 真实训练前提已满足：
  - 离线数据仓可用（`offline_available=True`）
  - 训练就绪诊断通过（抽样 cutoff=`20250205`，`eligible_stock_count=5262`，`ready=True`）
  - 运行时数据策略当前禁止在线兜底和资金流运行时同步（`allow_online_fallback=false`，`allow_capital_flow_sync=false`）
  - 默认 LLM 已配置（`model=gpt-5.4`，存在 API key）
- 已完成清理前验证：
  - targeted pytest：训练 / 调度 / Commander / Web API 主链路通过
  - 前端 `npm run build` 通过
  - `python train.py --cycles 1` 非 mock 真跑成功，真实数据 + Live LLM 闭环可执行
  - `python commander.py status --detail fast` 统一入口可正常装配
  - 独立 smoke 验证 `CronService` 与 `HeartbeatService` 能真实触发

### 当前发现的结构问题（初步）
- 根目录存在大量兼容壳与真实实现并存：`train.py` / `commander.py` / `web_server.py` / `llm_gateway.py` / `llm_router.py` 与 `app/*` 重叠。
- 根目录混合了“源码 / 文档 / 运行态产物 / 历史归档 / 外部三方代码 / 前端构建产物 / Python 缓存 / 虚拟环境”，可读性偏差。
- `external/lean` 是大型 vendored 代码树，已进入项目扫描范围，正在影响结构守卫测试。
- 仓库当前存在未提交改动：
  - `tests/test_commander_direct_planner_golden.py`
  - `tests/test_commander_mutating_workflow_golden.py`
  - `tests/test_commander_transcript_golden.py`
  - `tests/test_schema_contracts.py`
  - `brain/transcript_snapshot.py`（untracked）
  清理时必须避免误覆盖。

### 当前发现的历史遗留/边界异味（初步）
- 调度模块接口存在“文档/直觉签名”和真实实现不完全一致的问题：`CronService.__init__` 不接收 `on_job`，而是实例化后赋值，说明 API 易误用。
- 项目内已有多份架构/重构/清理文档，说明多轮升级与治理已发生，但文件层面尚未彻底收口。

### 全量测试异常（清理前基线）
- `./.venv/bin/python -m pytest -q` 失败 1 项：
  - `tests/test_structure_guards.py::test_project_code_does_not_import_src_package_internally`
  - 失败根因：扫描到 `external/lean/Tests/Python/Indicators/IndicatorExtensionsTests.py`，其文件开头包含 BOM（U+FEFF），`ast.parse` 直接解析失败。
- 该失败看起来是“结构守卫未排除 vendored external tree / BOM 文件”的仓库遗留问题，不是本次验证命令引入的新故障。

### 已执行的第一波安全清理
- 修复 `tests/test_structure_guards.py`：
  - 将 `external/` vendored tree 排除在项目源码守卫之外
  - 读取源码时改用 `utf-8-sig`，避免 BOM 文件导致 `ast.parse` 假失败
- 新增 `docs/README.md`：为文档体系建立索引，降低 docs 根目录可读性成本
- 新增 `scripts/clean_local_artifacts.sh`：标准化清理本地缓存、构建物、测试残留
- 已清理本地产物：`__pycache__`、`.pytest_cache`、`frontend/dist`、`frontend/test-results`、`.DS_Store`
- 已恢复真实训练带来的跟踪文件副作用：`data/evolution/generations/momentum_v1_test_candidate.json`

### 清理后验证结果
- `./.venv/bin/python -m pytest -q tests/test_structure_guards.py` → pass
- `./.venv/bin/python -m pytest -q` → pass（清理前唯一失败项已消除）
- `cd frontend && npm run build` → pass

### 架构审计结论（当前实现视角）
- 统一运行时主轴已经比较清晰：`CommanderRuntime` → `InvestmentBodyService` → `SelfLearningController`
- 训练链路主轴清晰：`DataManager` → `InvestmentModel` → `SelectionMeeting` → `SimulatedTrader` → `StrategyEvaluator/BenchmarkEvaluator` → `ReviewMeeting`
- 数据链路主轴清晰：`DataIngestionService` / `MarketDataRepository` 写入 canonical SQLite，`TrainingDatasetBuilder` / `WebDatasetService` / 各 read-side builder 统一读出
- 当前主要混乱点不在“核心业务主链”，而在“仓库表层”：
  - 根目录兼容壳仍多
  - docs 顶层平铺较多
  - 本地运行产物容易与源码视图混杂
  - vendored / archive / runtime / external 与主源码边界需要更强约束

### 后续建议的第二波清理（仍建议分波次做）
1. `docs/` 进一步分层：将 dated audit / remediation / blueprint 文档移入 `docs/audits/`、`docs/plans/`、`docs/blueprints/`
2. 为根目录兼容壳建立显式 `compatibility surface` 文档，并在 README 上收拢入口说明
3. 继续盘点 `app/stock_analysis.py`、legacy dashboard、旧字段兼容层，制定真正的弃用窗口
4. 为 `external/`、`历史归档区/`、运行态目录建立更严格的结构守卫规则

## 2026-03-12 第二波整理

### 本轮目标
- 继续降低仓库表层复杂度，而不触碰训练 / 调度 / Web / 数据主链逻辑。
- 把 `docs/` 从“根目录平铺”调整成“可导航的分层结构”。
- 明确根目录兼容入口边界，避免未来继续把业务实现堆回仓库根部。

### 已完成调整
- `docs/` 分层完成：
  - `docs/audits/`：审计 / 评审 / 结果报告
  - `docs/plans/`：执行计划 / 看板 / 迁移步骤
  - `docs/blueprints/`：架构蓝图 / 提案 / 专题设计
- `README.md` 相关文档区已更新到新路径，并补充 `docs/README.md` 作为总索引入口。
- 新增 `docs/COMPATIBILITY_SURFACE.md`：明确正式实现入口、根目录兼容壳、独立工具脚本与迁移建议。
- `tests/test_structure_guards.py` 新增 `test_root_python_surface_is_intentional()`，把根目录 Python 文件集合收敛成受控边界。
- 已同步修复 moved docs 的内部引用，避免重组后出现坏链。

### 本轮收益
- 新人理解项目时，不必先在 `docs/` 根目录手动筛选 dated report / plan / blueprint。
- 根目录“正式实现 vs 兼容壳”的边界变得明确，后续更容易继续清理 legacy 表层结构。
- 仓库结构守卫从“只防 import 漂移”升级到“同时防根目录脚本面继续膨胀”。

### 仍待后续治理的点
- `app/stock_analysis.py` 与 research payload / legacy dashboard 双轨仍在，属于第三波更适合处理的代码级兼容收口。
- 当前工作区还存在与本轮无关的其他未提交改动（如 contract freeze gate 相关文件），本轮未触碰。

## 2026-03-12 第三波收口

### 本轮目标
- 收缩 `app/stock_analysis.py` → `invest/research/*` 之间对 `legacy_signals` / `legacy_dashboard` 的主依赖。
- 保持对外 payload 与既有测试兼容，但让 canonical snapshot 成为研究假设的优先输入。

### 已完成调整
- `invest/research/snapshot_builder.py`
  - 继续保留 `feature_snapshot.legacy_signals` 作为兼容镜像
  - 但把 `flags`、`matched_signals`、`latest_close`、部分技术值（如 `rsi` / `ma20`）提升到 canonical `metadata` / `factor_values`
- `invest/research/hypothesis_engine.py`
  - `latest_close` 优先读取 canonical `summary.close` / `metadata.latest_close` / `factor_values.latest_close`
  - `supporting_factors` / `contradicting_factors` 优先消费 canonical `metadata.flags` / `metadata.matched_signals`
  - `legacy_signals` 从“主输入”降级为最后兜底
- 新增 `tests/test_research_hypothesis_engine.py`
  - 验证 hypothesis 在没有 `legacy_signals` 的情况下仍可依赖 canonical snapshot 正常工作
  - 验证 snapshot_builder 会把 legacy-derived 字段提升到 canonical metadata/factor_values

### 本轮收益
- research hypothesis 不再把 `legacy_signals` 当成第一数据源
- `stock_analysis -> research snapshot -> hypothesis` 这条链更贴近统一研究引擎设计
- 后续若要进一步压缩 ask legacy dashboard，可在不改 hypothesis 核心逻辑的前提下继续推进

### 回归结果
- `tests/test_research_hypothesis_engine.py` + `tests/test_ask_stock_model_bridge.py` → pass
- `tests/test_research_runtime_assets.py` + `tests/test_schema_contracts.py` + 上述研究桥接测试 → pass
- `./.venv/bin/python -m pytest -q` → pass
## 2026-03-12 第二段清理补充发现
- `app/stock_analysis.py` 的主要可读性问题不在算法，而在 `ask_stock()` 同时承担了 orchestration、research payload 组装、case/attribution 持久化、fallback 分支四类职责。
- 将 research 结果归并逻辑下沉为 helper 后，主入口更接近“编排层”，后续继续替换 legacy fallback 时风险面更小。
- 当前 legacy dashboard 仍是 research bridge unavailable 时的唯一兼容兜底，但已被压缩到单一 helper 返回点，后续可以继续替换为 canonical adapter。
## 2026-03-12 第三段清理补充发现
- `legacy_yaml_dashboard` 现在只剩“fallback 来源标记”语义，不再承担独立的最终 dashboard 展示协议责任。
- `ask_stock()` 的主干已基本收缩为：参数解析 → 执行分析 → 解析 research resolution → 组装协议响应。
- 后续更值得清理的方向，已经从 `app/stock_analysis.py` 内部结构，转向仓库级 legacy/compat 残留面扫描与逐模块收口。
## 2026-03-12 第四段清理补充发现
- `legacy_signals` 当前在 research contract 中的剩余职责已经收缩为兼容桥，不再适合承载完整 derived payload。
- 继续清理时，优先级更高的对象已经不是 `ask_stock()` 主流程，而是仓库范围内的 compat route / legacy shell / backward alias 面。
