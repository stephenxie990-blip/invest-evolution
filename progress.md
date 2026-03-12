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
- 2026-03-12 真实运行验证补充：
  - 真实训练入口通过：`./.venv/bin/python train.py --cycles 1`
  - 训练完整走通：数据加载 → 选股会议 → 回测模拟 → 评估 → 复盘会议 → 参数回写
  - Commander 真人式问股通过：`./.venv/bin/python commander.py ask -m '请帮我分析一下平安银行，按最近60个交易日视角，给出结论、依据、风险点和操作建议。'`
  - 问股在真实 LLM planner 超时后成功降级到 YAML 计划继续执行，最终正常返回分析结论
  - 结构化问股通过：当前态 `平安银行` 返回 `research.status=ok`
  - 历史时点问股通过：`as_of_date=20260220` 被安全收敛到 `effective_as_of_date=20260213`，且 `parameter_source=config_default_replay_safe`、`attribution_saved=true`
  - Commander 训练入口通过：`./.venv/bin/python commander.py train-once --rounds 1`
  - Commander 训练本轮返回 `insufficient_data/no_data`，原因是 `mean_reversion` 在 `20211123` 截面未产出可交易标的，但训练调度、路由、产物落盘均正常
- 第五段进展：
  - 已补上 `CommanderRuntime` 对 `state.json` 的只读恢复，解决“训练刚跑完但新实例 status 看起来像没跑过”的状态断层
  - 新增 `tests/test_commander.py::test_runtime_restores_persisted_runtime_and_body_state`
  - 已收敛 `app/web_server.py` 中 shell public path 的重复判断，统一为 `_is_shell_public_path()`
  - 回归通过：`./.venv/bin/python -m pytest -q tests/test_commander.py tests/test_commander_unified_entry.py`
  - 回归通过：`./.venv/bin/python -m pytest -q tests/test_web_ui_rollout.py tests/test_web_server_security.py tests/test_web_training_lab_api.py`
  - 回归通过：`./.venv/bin/python -m pytest -q`
- 第六段进展：
  - 新增 `brain/tool_metadata.py`，集中维护 `invest_status` / `invest_quick_status` / `invest_deep_status` 及 runtime observability compat surface
  - `brain/tools.py`、`brain/runtime.py`、`app/commander.py` 已改为复用共享 alias metadata，减少散落字符串
  - `app/web_server.py` 已将 legacy/app shell route 常量化，并统一 legacy shell 响应入口
  - 回归通过：`./.venv/bin/python -m pytest -q tests/test_commander.py tests/test_web_ui_rollout.py tests/test_web_server_security.py tests/test_web_training_lab_api.py`
  - 回归通过：`./.venv/bin/python -m pytest -q`
- 第七段进展：
  - 新增 `app/web_ui_metadata.py`，集中维护 web shell compat 元数据（`/app`、`/legacy`、canary header、query param 默认值等）
  - `app/web_server.py` 已改为复用共享 UI metadata，进一步压缩 shell compat 决策面的散落常量
  - 保持 `docs/contracts/frontend-api-contract.v1.json` 基线不变，未再修改 contract 源文件语义
  - 回归通过：`./.venv/bin/python -m pytest -q tests/test_frontend_contract_generation.py tests/test_frontend_api_contract.py tests/test_web_ui_rollout.py tests/test_web_server_security.py`
  - 回归通过：`./.venv/bin/python -m pytest -q`
- 第八段进展：
  - 新增 `config/web_ui.py`，集中维护 `web_ui_shell_mode` / `frontend_canary_query_param` 的默认值、合法值集合与归一化逻辑
  - `config/__init__.py` 已改为复用共享规范，并补上 `FRONTEND_CANARY_QUERY_PARAM` 的环境变量覆盖链路
  - `config/services.py` 已改为复用共享规范，`frontend_canary_query_param` 进入可编辑配置面，控制面 patch 与掩码输出保持一致
  - `app/web_server.py` 已改为复用共享归一化函数，避免运行时再维护一套独立的 shell mode / query param 清洗逻辑
  - 新增配置层回归：`tests/test_config_layering.py` 覆盖 env 覆盖 query param、非法 shell mode 回落；`tests/test_config_service_security.py` 覆盖控制面 patch 归一化与非法值拒绝
  - 回归通过：`./.venv/bin/python -m py_compile config/__init__.py config/web_ui.py config/services.py app/web_ui_metadata.py app/web_server.py tests/test_config_layering.py tests/test_config_service_security.py`
  - 回归通过：`./.venv/bin/python -m pytest -q tests/test_config_layering.py tests/test_config_service_security.py tests/test_web_ui_rollout.py tests/test_web_server_security.py`
  - 回归通过：`./.venv/bin/python -m pytest -q`
- 第九段进展：
  - 新增 `/Users/zhangsan/Desktop/投资进化系统v1.0/app/web_ui_runtime.py`，把 Web 壳层的公开路径判定、灰度判定、根路径壳选择、前端资产路径清洗统一为纯函数 helper
  - `/Users/zhangsan/Desktop/投资进化系统v1.0/app/web_server.py` 已改为复用 `WebUIShellSettings` / `resolve_root_shell_target()` / `is_shell_public_path()`，路由逻辑进一步瘦身
  - `/Users/zhangsan/Desktop/投资进化系统v1.0/tests/test_web_ui_rollout.py` 新增 Header 灰度回归，以及 `frontend/dist` 缺失时根路径自动回退 legacy 壳的回归
  - 全量回归首轮发现并修复 `_parse_bool()` 对 `_TRUE_VALUES` 的隐式依赖；该问题表现为 `tests/test_data_unification.py::test_web_data_status_refresh_query_switches_detail_mode` 返回 500
  - 回归通过：`./.venv/bin/python -m py_compile app/web_ui_runtime.py app/web_server.py tests/test_web_ui_rollout.py`
  - 回归通过：`./.venv/bin/python -m pytest -q tests/test_data_unification.py -k web_data_status_refresh_query_switches_detail_mode tests/test_web_ui_rollout.py tests/test_web_server_security.py`
  - 回归通过：`./.venv/bin/python -m pytest -q tests/test_web_ui_rollout.py tests/test_web_server_security.py tests/test_frontend_api_contract.py tests/test_frontend_contract_generation.py`
  - 回归通过：`./.venv/bin/python -m pytest -q`
- 第十段进展：
  - 新增 `/Users/zhangsan/Desktop/投资进化系统v1.0/app/frontend_contract_catalog.py`，集中维护 frontend contract 文档目录元数据、公开路径集合与文档读盘逻辑
  - `/Users/zhangsan/Desktop/投资进化系统v1.0/app/web_server.py` 已移除本地 contract 路径常量与重复 catalog 拼装，改为直接复用共享 catalog
  - `/Users/zhangsan/Desktop/投资进化系统v1.0/tests/test_frontend_api_contract.py` 已升级为对照共享 catalog 校验 `/api/contracts` 返回值，而不再只做松散的 id 存在性断言
  - 回归通过：`./.venv/bin/python -m py_compile app/frontend_contract_catalog.py app/web_server.py tests/test_frontend_api_contract.py`
  - 回归通过：`./.venv/bin/python -m pytest -q tests/test_frontend_api_contract.py tests/test_frontend_contract_generation.py tests/test_web_server_security.py tests/test_web_ui_rollout.py`
  - 回归通过：`./.venv/bin/python -m pytest -q`
- 第十一段进展：
  - `/Users/zhangsan/Desktop/投资进化系统v1.0/app/web_server.py` 新增统一 `_serve_frontend_contract_document(...)`，收口三条 `/api/contracts/frontend-v1*` 路由的重复读盘与异常映射
  - 三条文档路由现仅保留 document id 声明，404 文案与 500 日志文案继续复用 catalog 元数据
  - `/Users/zhangsan/Desktop/投资进化系统v1.0/tests/test_frontend_api_contract.py` 新增异常路径回归：缺文件返回 404、坏 payload 返回 500
  - 回归通过：`./.venv/bin/python -m py_compile app/web_server.py tests/test_frontend_api_contract.py`
  - 回归通过：`./.venv/bin/python -m pytest -q tests/test_frontend_api_contract.py tests/test_frontend_contract_generation.py tests/test_web_server_security.py`
  - 回归通过：`./.venv/bin/python -m pytest -q`
