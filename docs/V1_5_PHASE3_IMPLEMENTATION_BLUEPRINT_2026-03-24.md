# v1.5 第三阶段优化实施蓝图

日期：2026-03-24
适用版本：`投资进化系统 v1.5`
阶段目标：围绕训练治理、runtime 校准与晋升审查，补齐第三阶段 3 个 P0 能力，并保持当前默认 verdict 与已有运行行为的向后兼容。

## 一、实施范围

本阶段包含以下 3 个 P0 项：

1. `regime-aware feedback evidence coverage planner`
2. `manager runtime profile calibration framework`
3. `strict run quality breakdown by manager x regime`

## 二、总体原则

1. 不推翻 v1.5 第二阶段已经落地的 freeze / promotion / research feedback 结构，而是在既有 contract 上补强审计、覆盖率与分层验证能力。
2. 默认策略保持保守：
   - 新能力默认可观测、可审计。
   - 会影响晋升 verdict 的新 gate 默认关闭，除非计划显式开启。
3. runtime contract 统一时必须保留旧前缀参数 fallback，避免现有 YAML 和历史 candidate runtime 失效。
4. 所有新增结构都必须进入测试覆盖，并出现在训练/冻结报告或 evaluation summary 中，避免“逻辑已存在但治理不可见”。

## 三、工作流蓝图

### P0-1：Research Feedback Evidence Coverage Planner

目标：
为 research feedback 增加“证据覆盖规划层”，明确当前 requested regime 是否达到可行动阈值、哪些 regime 仍缺口最大、当前训练周期为 requested regime 补了多少样本。

主落点：

- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/investment/research/case_store.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/application/training/research.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/application/training/observability.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/tests/test_research_feedback_windowing.py`

实施要点：

1. 在 `ResearchCaseStore.build_training_feedback()` 中新增 `coverage_plan`。
2. `coverage_plan` 采用独立 schema，至少包含：
   - `schema_version`
   - `requested_regime`
   - `target_regimes`
   - `min_sample_count`
   - `coverage_ready`
   - `requested_regime_ready`
   - `requested_regime_gap_count`
   - `next_target_regimes`
   - `regime_targets`
   - `current_cycle_contribution`
3. `regime_targets` 需逐 regime 输出：
   - `sample_count`
   - `gap_count`
   - `ready`
4. `current_cycle_contribution` 基于 `as_of_date` 统计：
   - `sample_count`
   - `regime_counts`
   - `requested_regime_sample_count`
5. `TrainingFeedbackService.research_feedback_summary()` 和 `research_feedback_brief()` 扩展 coverage 视图，至少输出：
   - `coverage_ready`
   - `requested_regime_gap_count`
   - `next_target_regimes`
   - `current_cycle_requested_regime_gain`
6. `build_freeze_report()` / `generate_training_report()` 的 research feedback 区域新增 `research_feedback_coverage`，使 freeze 治理可直接审计 coverage 缺口。

验收标准：

1. requested regime 样本不足时，summary 能明确说明 gap，而不是只有“不可用”。
2. 训练报告中能看到当前 regime 还差多少样本、下一步该优先补哪些 regime。
3. 现有 gate 阈值不被放松，只增加可解释性与 evidence planning。

### P0-2：Manager Runtime Profile Calibration Framework

目标：
把 `mean_reversion` / `defensive_low_vol` 已存在但分散的 regime 特异性参数，收敛成统一 `regime_profiles` contract，形成“可校准、可审计、可回退”的 runtime profile 框架。

主落点：

- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/investment/runtimes/base.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/investment/runtimes/styles.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/investment/runtimes/configs/mean_reversion_v1.yaml`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/investment/runtimes/configs/defensive_low_vol_v1.yaml`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/tests/test_v2_additional_runtimes.py`

实施要点：

1. 在 `ManagerRuntime` 增加统一 helper：
   - `regime_profile(regime)`
   - `regime_param(regime, key, default=None)`
   - `regime_risk_param(regime, key, default=None)`
   - `regime_filter(regime, key, default=None)`
2. 统一 YAML contract：
   - `regime_profiles.<regime>.params`
   - `regime_profiles.<regime>.risk`
   - `regime_profiles.<regime>.filters`
3. 保留旧参数名 fallback：
   - `oscillation_*`
   - `bear_*`
4. `MeanReversionRuntime` 与 `DefensiveLowVolRuntime` 改为优先读取 `regime_profiles`，旧前缀参数仅作向后兼容。
5. `SignalPacketContext.debug_metadata` 新增 runtime calibration 回显，至少记录：
   - `runtime_profile`
   - `resolved_profile_source`
   - `applied_profile_params`
   - `applied_profile_risk`
   - `applied_profile_filters`

验收标准：

1. 新 YAML contract 生效，但旧 prefix 参数仍可驱动行为。
2. mean reversion 与 defensive low vol 的 regime-specific max_positions / cash_reserve / guard 行为可从 context 中审计。
3. 当前工作区已经存在的 mean reversion 守卫收紧改动需被吸收进统一 contract，而不是另起一套平行逻辑。

### P0-3：Strict Run Quality Breakdown by Manager x Regime

目标：
把训练结果从“总体 regime validation”进一步细化到“manager x regime”二维质量审查，让候选 runtime 在不同 manager / config / regime 组合上的质量差异可以被严格看见。

主落点：

- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/application/lab.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/src/invest_evolution/investment/shared/policy.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.5/tests/test_commander.py`

实施要点：

1. 新增 `build_manager_regime_breakdown_summary(ok_results)`。
2. summary 输出至少包含：
   - `sample_count`
   - `manager_count`
   - `managers`
3. 每个 manager 下至少包含：
   - `manager_id`
   - `runtime_config_refs`
   - `sample_count`
   - `avg_return_pct`
   - `benchmark_pass_rate`
   - `avg_strategy_score`
   - `regime_validation`
4. 新增 `evaluate_manager_regime_validation(summary, policy=...)`。
5. 在 `build_promotion_summary()` 中增加：
   - `promotion["manager_regime_validation"]`
6. 在 `build_training_evaluation_summary()` 中增加：
   - `assessment["manager_regime_breakdown"]`
7. `DEFAULT_PROMOTION_GATE_POLICY` 新增：
   - `manager_regime_validation`
   - 默认 `enabled: false`
   - 避免第三阶段默认改变历史 promotion verdict

验收标准：

1. evaluation summary 中能看到 manager x regime 质量剖面。
2. promotion gate 可选择开启严格二维验证，但默认不影响现有流程。
3. 当多个 runtime config 并存时，summary 能分辨 manager 与 config 引用，不再只给总体平均值。

## 四、测试蓝图

### 覆盖 planner

- 扩展 `/Users/zhangsan/Desktop/投资进化系统v1.5/tests/test_research_feedback_windowing.py`
- 核心校验：
  - requested regime 样本不足时会产生 `requested_regime_gap_count`
  - `next_target_regimes` 能反映优先补样方向
  - `current_cycle_contribution` 能正确统计 `as_of_date` 当期增量

### Runtime calibration

- 扩展 `/Users/zhangsan/Desktop/投资进化系统v1.5/tests/test_v2_additional_runtimes.py`
- 核心校验：
  - 新 `regime_profiles` contract 生效
  - 旧 prefix 参数 fallback 生效
  - `SignalPacketContext.debug_metadata` 含 calibration 回显

### Manager x regime breakdown

- 扩展 `/Users/zhangsan/Desktop/投资进化系统v1.5/tests/test_commander.py`
- 核心校验：
  - evaluation summary 暴露 `manager_regime_breakdown`
  - promotion summary 暴露 `manager_regime_validation`
  - 默认 policy 下该 gate 不改变既有 promotion 结论

## 五、实施顺序

1. 先固化文档与数据契约。
2. 落 research feedback coverage planner。
3. 落 runtime `regime_profiles` framework，并吸收当前 mean reversion 在途 guard 改动。
4. 落 manager x regime breakdown 与 promotion 接线。
5. 补 focused tests。
6. 跑 targeted pytest、runtime contract 校验与 freeze quick check。

## 六、风险与控制

1. 风险：runtime contract 改造误伤现有 YAML。
   控制：保留旧 prefix fallback，并在测试中同时验证新旧两套入口。

2. 风险：新增 gate 影响默认 promotion verdict。
   控制：`manager_regime_validation.enabled` 默认关闭。

3. 风险：coverage planner 让用户误以为 gate 被放松。
   控制：文档、summary、测试均明确“只增强 evidence planning，不放松 gate 阈值”。

4. 风险：freeze/report 层只存原 feedback，coverage 丢失。
   控制：training / freeze report 显式回填 `research_feedback_coverage`。
