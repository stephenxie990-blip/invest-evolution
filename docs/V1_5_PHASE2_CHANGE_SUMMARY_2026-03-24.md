# v1.5 第二阶段优化正式变更摘要

Date: 2026-03-24
Status: ready to commit

## 变更目的

本轮变更是在 `v1.5` 治理恢复主链已经重新接通之后，继续完成第二阶段收口，目标不是再恢复大块缺失模块，而是把当前主链中的 4 个已确认薄弱点补齐，并顺带完成两项质量审查中发现的 runtime hardening。

本轮重点是：

- 让主体身份和配置引用在 training / execution / review / research 之间收口为同一语义
- 让 review/eval boundary 的主语判定与 canonical scope projection 对齐
- 让 isolated experiments 的 preset / discovery 不再双份维护且不再漏掉短暂 regime
- 让 research case store 从“重复全量扫盘”升级到“可失效缓存”
- 补齐 runtime discipline 锁窗语义，以及非 `momentum` runtime 的 regime 守卫

## 本轮实际落地内容

### 1. `manager_config_ref` canonicalization 统一

本轮把共享 canonicalization helper 固定在 manager registry 侧，并让以下链路统一复用：

- `src/invest_evolution/investment/managers/registry.py`
- `src/invest_evolution/application/training/policy.py`
- `src/invest_evolution/application/training/execution.py`
- `src/invest_evolution/application/training/review_contracts/__init__.py`
- `src/invest_evolution/investment/research/case_store.py`

修复结果：

- alias 如 `momentum_v1` 会稳定收口到 registry runtime config ref
- relative path 会归一到统一 canonical path
- bare filename 如 `executed.yaml` 保持兼容，不被强制绝对化
- manager-aware research feedback 读取不再残留“训练侧一套、research 侧一套”的语义裂缝

### 2. review/eval boundary `subject_type` 收口

本轮修正了 `build_review_eval_projection_boundary()` 的主语推导方式，核心变更位于：

- `src/invest_evolution/application/training/observability.py`

修复结果：

- 不再仅凭 `portfolio_plan` 是否非空判定 `subject_type`
- 优先依赖 canonical projection、snapshot、`manager_results`、`dominant_manager_id`
- `manager_portfolio` 不会因为 payload 被裁剪成 `{}` 就被错误降级为 `single_manager`
- `compatibility_fields` 的 `derived/source` 与最终主语语义保持一致

### 3. isolated experiment preset / discovery 收口

本轮收口了 isolated experiments 的双份维护和 discovery 粗采样问题，落点如下：

- `src/invest_evolution/application/training/isolated_experiments.py`
- `scripts/run_isolated_regime_manager_experiment.py`

修复结果：

- CLI `--preset` 改为直接从核心 preset 注册表导出 choices
- 新增 `list_isolated_experiment_preset_names()` 作为单一事实来源
- regime discovery 从固定粗步进升级为 `coarse_plus_dense_scan`
- 在 `step_days=30` 的情况下，7 天级别的短暂 regime 窗口也能被补抓
- discovery 输出现在包含 `discovery_strategy`，便于审计

### 4. research case store 读取与缓存优化

本轮为 `case_store` 加入了低风险缓存和失效机制，落点如下：

- `src/invest_evolution/investment/research/case_store.py`

修复结果：

- `list_cases()` 与 `list_attributions()` 通过文件签名缓存避免重复读 JSON
- `_iter_case_attribution_records()` 增加查询结果缓存
- `save_case()` / `save_attribution()` 会主动触发缓存失效
- 目录文件签名变化也会触发被动失效
- 返回值使用 defensive copy，避免调用方污染内部缓存
- `build_training_feedback()` 入口最后一处 config ref 归一化也已切回 manager-aware canonicalization

## 质量审查中一并落地的额外硬化

### 5. runtime discipline 锁窗期延迟应用调整

在进一步审查 runtime discipline 时，补上了“锁窗期间 runtime adjustment 不应立即生效，而应延迟到 finalize 再一次性提交”的语义，落点如下：

- `src/invest_evolution/application/training/execution.py`
- `tests/test_training_runtime_discipline.py`

修复结果：

- cycle 运行窗口锁定期间，runtime adjustments 不会直接覆盖 `session_state.current_params`
- 调整被收集到 `current_cycle_deferred_runtime_adjustments`
- `finalize_cycle_runtime_window()` 结束时再统一落账
- summary 中会显式记录 `deferred_runtime_adjustment_keys`

### 6. 非 `momentum` runtime 的 regime 守卫强化

在 `mean_reversion` 与 `defensive_low_vol` runtime 上补上更严格的 regime 守卫和参数钳制，落点如下：

- `src/invest_evolution/investment/runtimes/styles.py`
- `src/invest_evolution/investment/runtimes/configs/mean_reversion_v1.yaml`
- `src/invest_evolution/investment/runtimes/configs/defensive_low_vol_v1.yaml`
- `tests/test_v2_additional_runtimes.py`

修复结果：

- `mean_reversion` 在 `oscillation` regime 下增加额外跌幅 / RSI / 空头趋势过滤与更保守的仓位、止盈止损、现金保留约束
- `defensive_low_vol` 在 `bear` regime 下增加波动率、防守评分、趋势过滤和更保守的仓位 / 现金约束
- 非主力 runtime 的 strict-style 运行语义更接近其治理角色，不再过度沿用宽松默认参数

## 主要影响文件

- `scripts/run_isolated_regime_manager_experiment.py`
- `src/invest_evolution/application/training/execution.py`
- `src/invest_evolution/application/training/isolated_experiments.py`
- `src/invest_evolution/application/training/observability.py`
- `src/invest_evolution/application/training/policy.py`
- `src/invest_evolution/application/training/review_contracts/__init__.py`
- `src/invest_evolution/investment/managers/registry.py`
- `src/invest_evolution/investment/research/case_store.py`
- `src/invest_evolution/investment/runtimes/configs/defensive_low_vol_v1.yaml`
- `src/invest_evolution/investment/runtimes/configs/mean_reversion_v1.yaml`
- `src/invest_evolution/investment/runtimes/styles.py`

## 对应测试

- `tests/test_runtime_config_ref_semantics.py`
- `tests/test_training_review_protocol.py`
- `tests/test_isolated_regime_manager_experiments.py`
- `tests/test_research_feedback_windowing.py`
- `tests/test_training_runtime_discipline.py`
- `tests/test_v2_additional_runtimes.py`

## 验证记录

本轮已完成以下验证：

```bash
python3 -m pytest -q \
  tests/test_runtime_config_ref_semantics.py \
  tests/test_training_review_protocol.py \
  tests/test_isolated_regime_manager_experiments.py \
  tests/test_research_feedback_windowing.py

python3 -m pytest -q \
  tests/test_training_runtime_discipline.py \
  tests/test_v2_additional_runtimes.py

python3 scripts/generate_runtime_contract_derivatives.py --check

uv run python -m invest_evolution.application.freeze_gate --mode quick
```

验证结果：

- focused phase2 suites 通过
- runtime discipline / additional runtimes suites 通过
- runtime contract artifacts up to date
- freeze gate quick 通过

## 结果判断

截至 2026-03-24，这轮第二阶段优化已经把 `v1.5` 从“治理主链恢复但存在局部语义裂缝”推进到“主体身份、主语边界、isolated discovery、research I/O、runtime discipline 都已收口”的状态。

当前剩余问题更适合进入第三阶段，不再是“丢失模块未补回”的问题，而是“如何进一步把 strict 训练质量、regime 证据覆盖和 runtime profile 泛化能力继续提高”的问题。
