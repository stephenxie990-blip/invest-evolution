# v1.5 第三阶段正式变更摘要

日期：2026-03-24  
范围：第三阶段 3 个 P0 项完整实现、文档同步与验证收口

## 1. 本轮完成内容

本轮已经完整实现并接线以下 3 个 P0 能力：

1. `regime-aware feedback evidence coverage planner`
2. `manager runtime profile calibration framework`
3. `strict run quality breakdown by manager x regime`

这轮不再是“列候选项”，而是已经落到正式代码、测试和治理文档中的实现。

## 2. 变更摘要

### 2.1 Research Feedback Evidence Coverage Planner

主落点：

- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/investment/research/case_store.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/application/training/research.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/application/training/observability.py`

新增能力：

- `ResearchCaseStore.build_training_feedback()` 新增 `coverage_plan`
- `coverage_plan` 提供：
  - `requested_regime_gap_count`
  - `next_target_regimes`
  - `regime_targets`
  - `current_cycle_contribution`
- `TrainingFeedbackService.research_feedback_summary()` / `research_feedback_brief()` 现在会回显 coverage 关键信号
- training report / freeze report 现在会显式落 `research_feedback_coverage`

治理意义：

- 现在 strict 失败时，不再只能看到“样本不足”，还可以直接看到“缺哪个 regime、还差多少、当前 cycle 有没有补到”
- 这让补样从人工诊断变成了结构化 evidence planning

### 2.2 Runtime Regime Profile Calibration Framework

主落点：

- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/investment/runtimes/base.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/investment/runtimes/styles.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/investment/runtimes/configs/mean_reversion_v1.yaml`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/investment/runtimes/configs/defensive_low_vol_v1.yaml`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/investment/runtimes/ops.py`

新增能力：

- `ManagerRuntime` 新增统一 helper：
  - `regime_profile()`
  - `regime_param()`
  - `regime_risk_param()`
  - `regime_filter()`
- `mean_reversion` / `defensive_low_vol` 改为优先读取统一 `regime_profiles` contract
- 仍保留 legacy prefix fallback：
  - `oscillation_*`
  - `bear_*`
- `SignalPacketContext.debug_metadata` 现在会回显：
  - `runtime_profile`
  - `resolved_profile_source`
  - `applied_profile_params`
  - `applied_profile_risk`
  - `applied_profile_filters`
- runtime config validator 现在会校验 `regime_profiles` 的结构合法性

治理意义：

- runtime 的分 regime 行为不再是“代码里散着一堆特殊判断”
- 现在可以明确回答：
  - 这轮为什么只保留 1 个仓位
  - 为什么 cash reserve 被抬高
  - 这些守卫来自新 contract 还是 legacy fallback

### 2.3 Manager x Regime Strict Quality Breakdown

主落点：

- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/application/lab.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/investment/shared/policy.py`

新增能力：

- training evaluation 新增 `assessment.manager_regime_breakdown`
- promotion 新增 `promotion.manager_regime_validation`
- 新增：
  - `build_manager_regime_breakdown_summary()`
  - `evaluate_manager_regime_validation()`
- 默认 promotion policy 新增 `manager_regime_validation`
  - 默认 `enabled: false`

治理意义：

- 现在不仅能看整体 `regime_validation`
- 还能看到某个 manager 在某个 regime 下是否持续拖累整体 strict readiness
- 同时保持默认 gate 关闭，避免无意改变历史 verdict

## 3. 文档同步

本轮同步更新了以下 active 文档：

- `/Users/zhangsan/Desktop/投资进化系统v1.5/README.md`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/docs/README.md`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/docs/CONFIG_GOVERNANCE.md`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/docs/TRAINING_FLOW.md`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/docs/RELEASE_READINESS.md`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/docs/V1_5_PHASE3_CANDIDATES_2026-03-24.md`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/docs/V1_5_PHASE3_IMPLEMENTATION_BLUEPRINT_2026-03-24.md`

同步目的：

- 把第三阶段从“候选”更新为“已实现的当前事实”
- 让 README / docs index / 治理说明 / 训练说明 / release sign-off 使用同一套术语

## 4. 新增/更新测试

本轮补充或扩展了以下测试：

- `/Users/zhangsan/Desktop/投资进化系统v1.5/tests/test_research_feedback_windowing.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/tests/test_v2_additional_runtimes.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/tests/test_runtime_configuration_suite.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/tests/test_commander.py`

覆盖重点：

- coverage planner 的 gap / next target / current cycle contribution
- runtime `regime_profiles` 生效
- legacy prefix fallback 仍然有效
- `debug_metadata` 审计字段存在
- `manager_regime_breakdown` 与 `manager_regime_validation` 的 summary/gate 接线

## 5. 验证结果

本轮执行并通过：

```bash
python3 -m pytest -q tests/test_runtime_configuration_suite.py tests/test_research_feedback_windowing.py tests/test_v2_additional_runtimes.py tests/test_commander.py
python3 scripts/generate_runtime_contract_derivatives.py --check
uv run python -m invest_evolution.application.freeze_gate --mode quick
```

结果：

- focused pytest 通过
- runtime contract artifacts 无漂移
- freeze gate quick 通过

## 6. 默认行为说明

这轮虽新增了更严格的二维质量门，但默认行为保持保守：

- `manager_regime_validation.enabled = false`
- 因此现有默认 promotion verdict 不会因为本轮而被无意改变

同时：

- `research feedback` 阈值没有被放松
- `coverage_plan` 只增强 evidence planning，不替代 gate 判定

## 7. 当前阶段结论

第三阶段 P0 完成后，`v1.5` 的 strict readiness 审查能力明显前移并下钻：

- 可以看到 requested regime 的 evidence 缺口
- 可以看到 runtime 在各 regime 下到底是如何被校准的
- 可以看到 strict 失败是否集中在单一 `manager x regime` 组合

这意味着后续的优化可以更少围绕“有没有模块”，更多围绕：

- 哪条 regime 证据还没补够
- 哪个 manager 的 regime profile 仍需重构
- 哪个二维单元在拖累整体 strict readiness
