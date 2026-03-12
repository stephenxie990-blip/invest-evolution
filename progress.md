# Progress Log

## Session: 2026-03-12

### Phase 1: Requirements & Discovery
- **Status:** complete
- **Started:** 2026-03-12
- Actions taken:
  - 阅读项目级技能与约束
  - 读取 `agentic-engineering` 与 `pi-planning-with-files` 说明
  - 初始化 `task_plan.md`、`findings.md`、`progress.md`
  - 读取 `eval-harness`、`verification-loop`、`python-patterns`、`python-testing`、`search-first` 以支撑执行蓝图
- Files created/modified:
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 2: Current Architecture Analysis
- **Status:** complete
- Actions taken:
  - 定位训练链 `SelfLearningController`
  - 定位问股链 `StockAnalysisService`
  - 梳理训练侧 contracts、meeting 输出、评估路径与 policy 同步逻辑
  - 梳理问股侧 tool plan、derived signals 与 dashboard 逻辑
- Files created/modified:
  - `findings.md` (updated)

### Phase 3: Unified Engine Design
- **Status:** complete
- Actions taken:
  - 输出统一研究引擎方案文档
  - 定义四层闭环对象：`ResearchSnapshot`、`PolicySnapshot`、`ResearchHypothesis`、`OutcomeAttribution`
  - 给出阶段性迁移路径与概率推演方案
- Files created/modified:
  - `docs/RESEARCH_ENGINE_UNIFICATION_PROPOSAL_20260312.md` (created)

### Phase 4: Execution Blueprint
- **Status:** complete
- Actions taken:
  - 结合用户反馈补强 `PolicySnapshot.version_hash` 与 multi-horizon scoring
  - 输出可执行蓝图：阶段规划、验收门、subagent 调度、skills 矩阵、测试与验证路线
- Files created/modified:
  - `docs/RESEARCH_ENGINE_UNIFICATION_EXECUTION_BLUEPRINT_20260312.md` (created)
  - `findings.md` (updated)
  - `progress.md` (updated)


### Phase 5: Phase 0-4 Implementation
- **Status:** complete
- Actions taken:
  - 新增 `invest/research/` 统一研究对象与引擎：snapshot / policy / hypothesis / case / attribution / scenario / renderer
  - 为 `StockAnalysisService.ask_stock()` 增加 `as_of_date`、回放边界、active/routed model bridge 与 fallback 语义
  - 为 `CommanderRuntime.ask_stock()` 与 `InvestAskStockTool` 透传 `as_of_date`
  - 为 `ResearchCaseStore` 增加 case 检索与 calibration report 落盘
  - 新增 `docs/research/phase0_contract_mapping.md` 与 `.Codex/evals/research-unification-phase{0..4}.md`
  - 新增 research tests 并通过 targeted pytest
- Files created/modified:
  - `app/stock_analysis.py` (updated)
  - `app/commander.py` (updated)
  - `brain/tools.py` (updated)
  - `invest/research/case_store.py` (updated)
  - `tests/test_research_contracts.py` (created)
  - `tests/test_research_case_store.py` (created)
  - `tests/test_research_attribution_engine.py` (created)
  - `tests/test_ask_stock_model_bridge.py` (created)
  - `docs/research/phase0_contract_mapping.md` (created)
  - `.Codex/evals/research-unification-phase0.md` (created)
  - `.Codex/evals/research-unification-phase1.md` (created)
  - `.Codex/evals/research-unification-phase2.md` (created)
  - `.Codex/evals/research-unification-phase3.md` (created)
  - `.Codex/evals/research-unification-phase4.md` (created)

## Test Results
| Promotion gate pytest | `tests/test_commander.py -k 'promotion or evaluation_summary or result_dict_serializes_numpy_bool'` | All pass | Success | ✓ |
| Commander feedback regression | `tests/test_commander.py tests/test_train_ui_semantics.py tests/test_research_training_feedback.py tests/test_review_meeting_v2.py tests/test_research_case_store.py` | All pass | Success | ✓ |
| Feedback gate pytest | `tests/test_research_training_feedback.py tests/test_train_ui_semantics.py tests/test_train_cycle.py tests/test_review_meeting_v2.py tests/test_research_case_store.py` | All pass | Success | ✓ |
| Feedback broader pytest | research + ask bridge + review + train suites | All pass | Success | ✓ |
| Training feedback pytest | `tests/test_research_case_store.py tests/test_review_meeting_v2.py tests/test_train_ui_semantics.py tests/test_train_cycle.py` | All pass | Success | ✓ |
| Research broader pytest | unified research + review + training UI suites | All pass | Success | ✓ |
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Planning bootstrap | Create planning files | Files created successfully | Success | ✓ |
| Research proposal doc | Write proposal markdown | File created successfully | Success | ✓ |
| Execution blueprint doc | Write blueprint markdown | File created successfully | Success | ✓ |
| Research targeted pytest | `tests/test_research_*` + ask bridge compatibility suite | All targeted tests pass | Success | ✓ |

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-03-12 | `python: command not found` | 1 | Switched to shell-only bootstrap |
| 2026-03-12 | large file output truncation | 1 | Switched to method-level chunked reads |
| 2026-03-12 | shell `apply_patch` warning | 1 | Switched to direct file writes for planning docs |

## 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | 已完成架构研究与执行蓝图，可进入 Phase 0 实施 |
| Where am I going? | 下一步可启动 contract 冻结与 ask bridge 设计 |
| What's the goal? | 让训练与问股在同一研究语义、同一因果、同一验证闭环中运行 |
| What have I learned? | 最大矛盾是研究语义层分裂，不是数据层分裂 |
| What have I done? | 输出 proposal/blueprint，并完成 Phase 0-4 最小可运行实现、测试与 eval 工件 |

### Phase 7: Training Feedback Loop
- **Status:** complete
- Actions taken:
  - 在 `SelfLearningController` 中消费 `ResearchCaseStore.build_training_feedback(...)`，并透传到 `EvalReport.metadata`、training report、cycle JSON 与 commander snapshot
  - 在 `ReviewMeeting` 中接入 `research_feedback` 事实编译、LLM prompt 注入与 fallback 风险偏置
  - 在 `ReviewDecisionAgent` prompt 中补充 ask 侧校准摘要，统一训练复盘语义
  - 新增训练侧 feedback 回归测试并通过 targeted / broader pytest
- Files created/modified:
  - `invest/meetings/review.py` (updated)
  - `invest/agents/reviewers.py` (updated)
  - `tests/test_review_meeting_v2.py` (updated)
  - `tests/test_research_case_store.py` (updated)
  - `tests/test_train_ui_semantics.py` (updated)

### Phase 8: Feedback-Driven Optimizer & Freeze Gate
- **Status:** complete
- Actions taken:
  - 在 `app/training/reporting.py` 中新增 research feedback gate / freeze gate evaluation，并把评估结果接入 training report 与 freeze report
  - 在 `SelfLearningController` 中新增 feedback optimization plan、cooldown、freeze gate state 与 snapshot 透传
  - 在 `app/training/optimization.py` 中支持 `consecutive_losses` 与 `research_feedback` 双触发优化
  - 新增 `tests/test_research_training_feedback.py`，覆盖 multi-horizon 优化 plan、cooldown、freeze gate 阻断/放行与 report 透传
- Files created/modified:
  - `app/training/reporting.py` (updated)
  - `app/training/optimization.py` (updated)
  - `app/train.py` (updated)
  - `app/commander.py` (updated)
  - `tests/test_research_training_feedback.py` (created)
  - `tests/test_train_ui_semantics.py` (updated)

### Phase 9: Promotion Gate Calibration
- **Status:** complete
- Actions taken:
  - 在 `app/lab/evaluation.py` 中为 `promotion_gate` 新增 research feedback 校准门，并将 gate 结果落入 `promotion.research_feedback`
  - 在 `InvestmentBodyService._to_result_dict()` 中透传 `research_feedback`，让真实 training run 的 evaluation summary 能看到校准状态
  - 新增 commander 晋升门测试，覆盖 calibration reject / calibration promote 两种情况
- Files created/modified:
  - `app/lab/evaluation.py` (updated)
  - `app/commander.py` (updated)
  - `tests/test_commander.py` (updated)
  - `tests/test_train_ui_semantics.py` (updated)

### Phase 10: Training Plan Default Calibration Gate
- **Status:** complete
- Actions taken:
  - 在 `app/lab/artifacts.py` 新增默认 `research_feedback` gate 模板、深度合并函数与 optimization payload 归一化逻辑
  - 让 `build_training_plan_payload(...)` 在 plan 生成时默认注入 `optimization.promotion_gate.research_feedback`
  - 为 `tests/test_lab_artifacts.py` 增加默认注入与 partial override merge 回归
  - 为 `tests/test_commander.py` 增加 `create_training_plan(...)` 持久化默认 gate 回归，并修正 baseline promote 用例以显式提供通过校准的 `research_feedback`
- Files created/modified:
  - `app/lab/artifacts.py` (updated)
  - `tests/test_lab_artifacts.py` (updated)
  - `tests/test_commander.py` (updated)
- Test Results:
  - `./.venv/bin/python -m py_compile app/lab/artifacts.py app/commander.py app/lab/evaluation.py tests/test_lab_artifacts.py tests/test_commander.py` → pass
  - `./.venv/bin/python -m pytest -q tests/test_lab_artifacts.py tests/test_commander.py -k 'create_training_plan or promotion_gate or evaluation_summary or research_feedback_gate'` → pass
  - `./.venv/bin/python -m pytest -q tests/test_commander.py tests/test_train_ui_semantics.py tests/test_research_training_feedback.py tests/test_review_meeting_v2.py tests/test_research_case_store.py` → pass

### Phase 11: Calibration Gate Visibility
- **Status:** complete
- Actions taken:
  - 在 `app/lab/artifacts.py` 为 training plan 增加 `guardrails.promotion_gate.research_feedback` 可见化摘要，包含 `summary`、`reason_codes`、`policy_source` 与 `thresholds`
  - 在 `app/lab/artifacts.py` 的 run artifact 中保留 plan guardrails，确保 run 追溯时也能看到创建时启用的默认校准门
  - 在 `app/commander.py` 为 `training_lab` bundle 增加 `plan.guardrails` 与 `evaluation.promotion.research_feedback` brief summary / reason codes
  - 为 `tests/test_lab_artifacts.py`、`tests/test_commander.py`、`tests/test_web_training_lab_api.py` 增加 CLI/Web 可见性回归测试
- Files created/modified:
  - `app/lab/artifacts.py` (updated)
  - `app/commander.py` (updated)
  - `tests/test_lab_artifacts.py` (updated)
  - `tests/test_commander.py` (updated)
  - `tests/test_web_training_lab_api.py` (updated)
- Test Results:
  - `./.venv/bin/python -m py_compile app/lab/artifacts.py app/commander.py app/web_server.py tests/test_lab_artifacts.py tests/test_commander.py tests/test_web_training_lab_api.py` → pass
  - `./.venv/bin/python -m pytest -q tests/test_lab_artifacts.py tests/test_commander.py tests/test_web_training_lab_api.py -k 'training_lab or create_training_plan or research_feedback_gate or evaluation_summary or api_train'` → pass
  - `./.venv/bin/python -m pytest -q tests/test_commander.py tests/test_train_ui_semantics.py tests/test_research_training_feedback.py tests/test_review_meeting_v2.py tests/test_research_case_store.py tests/test_web_training_lab_api.py` → pass

### Phase 12: Daily Review & Closure
- **Status:** complete
- Actions taken:
  - 基于 `git status` / `git diff --stat` 审计今日变更范围，确认变更主要落在 research contracts、ask bridge、training feedback、promotion gate、plan guardrails、API contracts 与回归测试
  - 对 15 个核心 Python 文件执行 `py_compile`，全部通过
  - 对今日关键测试集合执行完整 targeted regression，稳定通过
  - 额外执行 `git diff --check`，发现仅 planning 文件末尾空行问题，并已在本次记录中一并收敛
- Test Results:
  - `./.venv/bin/python - <<'PY' ... py_compile.compile(...) ... PY`（15 core files） → pass
  - `./.venv/bin/python -m pytest -q tests/test_schema_contracts.py -k 'golden_snapshot or matches_contract_keys or shared_enums'` → pass
  - `./.venv/bin/python -m pytest -q tests/test_research_contracts.py tests/test_research_case_store.py tests/test_research_attribution_engine.py tests/test_ask_stock_model_bridge.py tests/test_stock_analysis_react.py tests/test_schema_contracts.py tests/test_frontend_api_contract.py tests/test_review_meeting_v2.py tests/test_train_ui_semantics.py tests/test_research_training_feedback.py tests/test_commander.py tests/test_lab_artifacts.py tests/test_web_training_lab_api.py tests/test_commander_direct_planner_golden.py tests/test_commander_mutating_workflow_golden.py tests/test_commander_transcript_golden.py tests/test_web_server_contract_headers.py` → pass
- Closure assessment:
  - Blocking issues: none reproduced
  - Non-blocking issues: 建议后续按逻辑分组提交；Web UI 展示层按用户决策暂不继续推进

### Phase 13: Executable Remediation Action Plan
- **Status:** complete
- Actions taken:
  - 结合总体评审报告与研究统一蓝图，拆解出 P0 / P1 / P2 可执行整改项
  - 设计五类 owner：Research Kernel / Training Runtime / Runtime & Interaction / Data & Lineage / Quality & Governance
  - 输出 work unit 粒度的任务分配方案、subagent 调度建议与 skills 使用矩阵
  - 新增整改清单文档 `docs/PROJECT_REMEDIATION_ACTION_PLAN_20260312.md`
- Files created/modified:
  - `docs/PROJECT_REMEDIATION_ACTION_PLAN_20260312.md` (created)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)


### Phase 14: Runtime Response Envelope Unification
- **Status:** complete
- Actions taken:
  - 在 `brain/task_bus.py` 抽取共享 `build_protocol_response(...)` / `build_response_envelope(...)`，统一 message / reply / feedback / next_action 组装
  - 让 `brain/runtime.py`、`app/commander.py`、`app/stock_analysis.py` 统一消费共享协议封装，而不是各自散落拼装响应
  - 同步扩展 `brain/schema_contract.py` 与 frontend contract / transcript golden，固化 response envelope 键集
- Files created/modified:
  - `brain/task_bus.py` (updated)
  - `brain/runtime.py` (updated)
  - `app/commander.py` (updated)
  - `app/stock_analysis.py` (updated)
  - `brain/schema_contract.py` (updated)
  - `docs/contracts/frontend-api-contract.v1.json` (updated)
  - `docs/contracts/frontend-api-contract.v1.openapi.json` (updated)
  - `docs/contracts/frontend-api-contract.v1.schema.json` (updated)
  - `tests/test_schema_contracts.py` (updated)
  - `tests/test_frontend_api_contract.py` (updated)
  - `tests/test_commander_transcript_golden.py` (updated)

### Phase 15: Training Controller Service Extraction
- **Status:** complete
- Actions taken:
  - 新增 `app/training/controller_services.py`，承接 research feedback、freeze gate、cycle persistence 三类职责
  - 让 `SelfLearningController` 初始化 service 并以委派方式保留原有方法接口，减少控制器直接承载的逻辑重量
  - 新增 `tests/test_training_controller_services.py` 验证 service 暴露、委派链路与 cycle 持久化行为
- Files created/modified:
  - `app/training/controller_services.py` (created)
  - `app/train.py` (updated)
  - `tests/test_training_controller_services.py` (created)

### Phase 16: Research Asset Runtime Exposure
- **Status:** complete
- Actions taken:
  - 新增 `app/research_services.py`，把 research case / attribution / calibration 组织成 runtime 可读 payload
  - 为 `CommanderRuntime` 增加 `list_research_cases(...)`、`list_research_attributions(...)`、`get_research_calibration(...)`
  - 为 `brain/tools.py` 注册 `invest_research_cases`、`invest_research_attributions`、`invest_research_calibration`
  - 新增 `tests/test_research_runtime_assets.py` 验证 research asset 运行时检索协议
- Files created/modified:
  - `app/research_services.py` (created)
  - `app/commander.py` (updated)
  - `brain/tools.py` (updated)
  - `tests/test_research_runtime_assets.py` (created)

### Final Verification
- **Status:** complete
- Commands run:
  - `./.venv/bin/python -m py_compile app/train.py app/training/controller_services.py app/research_services.py app/commander.py app/stock_analysis.py brain/runtime.py brain/schema_contract.py brain/task_bus.py brain/tools.py tests/test_training_controller_services.py tests/test_research_runtime_assets.py tests/test_research_training_feedback.py tests/test_research_case_store.py tests/test_stock_analysis_react.py tests/test_ask_stock_model_bridge.py tests/test_schema_contracts.py tests/test_frontend_api_contract.py tests/test_commander.py tests/test_commander_transcript_golden.py tests/test_web_server_contract_headers.py tests/test_train_ui_semantics.py` → pass
  - `./.venv/bin/python -m pytest -q tests/test_training_controller_services.py tests/test_research_runtime_assets.py tests/test_research_training_feedback.py tests/test_research_case_store.py tests/test_stock_analysis_react.py tests/test_ask_stock_model_bridge.py tests/test_schema_contracts.py tests/test_frontend_api_contract.py tests/test_commander.py tests/test_commander_transcript_golden.py tests/test_web_server_contract_headers.py tests/test_train_ui_semantics.py` → pass

## 2026-03-12 清理前验证与总体审核启动
- 状态：in_progress
- 已完成：
  - 扫描仓库顶层结构、入口文件、README、pyproject 与现有文档索引
  - 确认项目主入口：`app/train.py`、`app/commander.py`、`app/web_server.py`、`market_data/__main__.py`
  - 确认真实训练环境可用：离线 DB 可用、训练 readiness=ready、Live LLM 已配置
  - 跑通 targeted pytest：`tests/test_train_cycle.py` `tests/test_training_controller_services.py` `tests/test_brain_scheduler.py` `tests/test_commander_unified_entry.py` `tests/test_web_training_lab_api.py` `tests/test_web_server_data_api.py`
  - 跑通前端构建：`frontend/npm run build`
  - 跑通真实训练：`./.venv/bin/python train.py --cycles 1`
  - 跑通 Commander 实机入口：`./.venv/bin/python commander.py status --detail fast`
  - 跑通调度 smoke：`CronService` / `HeartbeatService`
- 待完成：
  - 梳理当前架构、模块边界、数据链路
  - 盘点冗余文件/兼容壳/历史残留
  - 提出并执行清理方案
  - 清理后回归验证
- 风险记录：
  - 全量 pytest 当前有 1 个基线失败，来自 `external/lean` vendored 树被结构守卫扫描并触发 BOM 解析异常
  - 工作区已有用户未提交改动，后续不得覆盖
- 新进展：
  - 修复全量 pytest 基线失败（结构守卫误扫 vendored external tree）
  - 新增 docs 索引页与本地清理脚本
  - 再次回归：全量 pytest 通过、前端 build 通过
  - 恢复真实训练引入的跟踪文件副作用
- 第二波进展：
  - 完成 `docs/` 分层重组（audits / plans / blueprints）
  - 更新 README 与 docs 内部引用到新路径
  - 新增 `docs/COMPATIBILITY_SURFACE.md`
  - 为根目录 Python surface 增加结构守卫测试
  - 全量 pytest 再次通过
- 第三波进展：
  - 已将 hypothesis 对 `legacy_signals` 的主依赖降级为兜底
  - 已把部分 legacy-derived 结构提升到 canonical snapshot metadata/factor_values
  - 新增 hypothesis-focused 回归测试
  - 全量 pytest 再次通过
- 第二段进展：
  - 已将 `StockAnalysisService.ask_stock()` 中 research 成功/降级分支抽取为专用 helper，主流程只保留编排职责
  - 新增 `_build_research_payload_bases`、`_persist_research_case_artifacts`、`_resolve_ask_stock_research_outputs`
  - 新增 fallback 回归测试，确保 research bridge 不可用时仍走 `legacy_yaml_dashboard` 合约
  - 回归通过：`./.venv/bin/python -m pytest -q tests/test_ask_stock_model_bridge.py tests/test_stock_analysis_react.py`
  - 回归通过：`./.venv/bin/python -m pytest -q`
- 第三段进展：
  - 已将 fallback dashboard 的最终输出统一收口到 `build_dashboard_projection()`，兼容逻辑与展示协议分离
  - 新增 `_build_dashboard_fallback_projection`，legacy 打分逻辑仅保留为 fallback 输入源
  - 已将 `ask_stock()` 末尾 task_bus / bounded_context / payload 组装提取为 helper，主流程继续瘦身
  - 新增 fallback canonical renderer 回归测试
  - 回归通过：`./.venv/bin/python -m pytest -q tests/test_ask_stock_model_bridge.py tests/test_stock_analysis_react.py tests/test_commander_unified_entry.py`
  - 回归通过：`./.venv/bin/python -m pytest -q`
- 第四段进展：
  - 已将 research snapshot 中 `legacy_signals` 压缩为兼容最小子集（flags / matched_signals / latest_close / ma20 / rsi）
  - 新增 snapshot builder 回归测试，防止整包 `derived` 再次泄入 research contracts
  - 回归通过：`./.venv/bin/python -m pytest -q tests/test_research_hypothesis_engine.py tests/test_ask_stock_model_bridge.py tests/test_research_runtime_assets.py`
  - 回归通过：`./.venv/bin/python -m pytest -q`
