# v1.5 第三阶段优化候选清单

Date: 2026-03-24
Status: P0 completed, P1/P2 pending

## 定位

第二阶段完成后，`v1.5` 的治理主链、主体身份、review/eval 主语边界、isolated experiments、research case store 缓存都已经收口。第三阶段不再以“修丢失模块”为主，而应转向：

- strict 训练质量提升
- regime 证据覆盖提升
- runtime profile 泛化与参数纪律强化
- 训练运行成本和审计效率继续优化

下面这份清单按优先级分为 `P0 / P1 / P2`。

## P0：直接影响 strict readiness 的候选项

### 1. regime-aware feedback evidence coverage planner

#### 目标

把“requested regime 样本不足”从事后发现，推进到训练前或训练中可预警、可规划补样。

#### 原因

当前 bull / rare regime 证据问题已经从“统计错了”收敛成“独立样本确实不足”。下一步应该把补样从人工诊断改成系统能力。

#### 建议 owner

- `src/invest_evolution/application/training/research.py`
- `src/invest_evolution/investment/research/case_store.py`
- `src/invest_evolution/application/training/observability.py`

#### 期望能力

- 在 run 前给出按 regime 的 evidence coverage 诊断
- 在 run 中暴露“当前 cycle 对哪个 regime evidence 有增益”
- 在 run 后给出“还缺哪些 regime / manager / horizon 的独立样本”

### 2. manager runtime profile calibration framework

#### 目标

把 `momentum` 之外的 runtime 也系统化纳入 profile 校准，而不是靠局部参数加守卫。

#### 原因

第二阶段已经给 `mean_reversion` / `defensive_low_vol` 加了 regime guard，但这仍是“手工收紧”。第三阶段更适合建立统一的 runtime calibration 框架。

#### 建议 owner

- `src/invest_evolution/investment/runtimes/styles.py`
- `src/invest_evolution/investment/runtimes/configs/*.yaml`
- `src/invest_evolution/application/training/execution.py`

#### 期望能力

- 每个 manager 的 regime profile 有统一 contract
- risk / execution / scoring 的 regime override 语义统一
- 可以输出“当前 runtime profile 为什么这样收紧/放松”的解释

### 3. strict run quality breakdown by manager x regime

#### 目标

把 strict readiness 评估从 run-level aggregate，进一步拆成 `manager x regime` 的质量矩阵。

#### 原因

现在整体失败，已经不能只看单一 `avg_return`。更关键的是识别：

- 哪个 manager 在哪个 regime 下拖累整体
- 哪些 manager 本来应在该 regime 关闭或降权

#### 建议 owner

- `src/invest_evolution/application/training/observability.py`
- `src/invest_evolution/application/training/review_contracts/__init__.py`
- `runtime/state/training_evals/*` 的生成链

## P1：提高训练纪律与治理可解释性的候选项

### 4. runtime discipline event lineage completion

#### 目标

把 runtime discipline 的延迟调整、proposal effect、candidate build、final applied params 串成一条完整 lineage。

#### 原因

第二阶段已经补了“锁窗期延迟应用”，但现在 still 缺一层统一可追溯的 lineage 视图。

#### 建议 owner

- `src/invest_evolution/application/training/execution.py`
- `src/invest_evolution/application/training/observability.py`
- `src/invest_evolution/application/training/persistence.py`

#### 期望能力

- 看一条 cycle 就能知道参数何时被提出、何时被延迟、何时真正生效
- review / audit / freeze gate 读到的是同一条真相链

### 5. isolated experiment discovery index / replay cache

#### 目标

在第二阶段完成 discovery 策略收口后，继续降低 isolated experiments 的重复 governance preview 成本。

#### 原因

现在发现能力已经变强，但 preview 调用密度也上去了。第三阶段可以把 discovery 结果做成轻量索引或 replay cache。

#### 建议 owner

- `src/invest_evolution/application/training/isolated_experiments.py`
- `scripts/run_isolated_regime_manager_experiment.py`

#### 期望能力

- 相同参数重复 discovery 时优先复用已有 probe
- 只对失效窗口补探测
- 能快速对比不同 manager / regime 的可用 cutoff coverage

### 6. research case store secondary index

#### 目标

在当前缓存基础上，继续为 `manager_id / regime / as_of_date / hypothesis_id` 建立轻量索引层。

#### 原因

第二阶段解决的是“每次都重读 JSON”，第三阶段可进一步解决“过滤仍需线性遍历”的问题。

#### 建议 owner

- `src/invest_evolution/investment/research/case_store.py`

#### 期望能力

- 高频查询走 index，而不是全量 filter
- training feedback / attribution lookup 的延迟继续下降
- 为以后更大规模 strict/shadow 长跑做准备

## P2：工程整洁性与审计体验优化

### 7. shared config-ref normalization contract extraction

#### 目标

把当前 registry 层的 shared canonicalization，进一步提升为显式 contract / utility module，并补更系统的 cross-layer schema tests。

#### 原因

第二阶段已经把调用点收口了，但规范本身还主要以 helper 形式存在。第三阶段更适合把它明确成“项目级 contract”。

#### 建议 owner

- `src/invest_evolution/investment/managers/registry.py`
- `src/invest_evolution/investment/shared/policy.py`
- `tests/test_runtime_config_ref_semantics.py`

### 8. phase-aware release / readiness dashboard

#### 目标

把 `governance recovery`、`phase2 optimization`、`strict readiness` 的文档和关键信号汇总到一个统一 dashboard。

#### 原因

现在结论文档已经不少，但分散在多个文件里。第三阶段适合把“项目当前到底卡在哪”做成一个高密度总览。

#### 建议 owner

- `docs/`
- `src/invest_evolution/application/training/observability.py`

### 9. manager portfolio vs single manager truth-table regression pack

#### 目标

把 single-manager / manager-portfolio / payload-cropped / snapshot-derived 这些容易回退的边界做成系统回归矩阵。

#### 原因

第二阶段修掉的是已知残留点，但这类语义边界最容易在后续迭代里再次回退。

#### 建议 owner

- `tests/test_training_review_protocol.py`
- `tests/test_training_boundary_adapters.py`
- `tests/test_training_controller_services.py`

## 推荐启动顺序

建议第三阶段按下面顺序推进：

1. regime-aware feedback evidence coverage planner
2. manager runtime profile calibration framework
3. strict run quality breakdown by manager x regime
4. runtime discipline event lineage completion
5. isolated experiment discovery index / replay cache
6. research case store secondary index

这样可以先打 strict readiness 的主阻塞，再做成本和审计优化。

## 总结判断

第三阶段最应该避免的误区是继续把注意力放在“有没有模块缺失”。到当前这个阶段，`v1.5` 的主问题已经不是模块缺失，而是：

- 证据够不够
- 各 manager 的 regime profile 是否真的站得住
- strict 训练失败到底归因到哪里
- 训练过程的参数变化能不能被完全解释

第三阶段如果做对，输出不应只是“代码更完整”，而应是“strict readiness 的失败原因可以被量化、被定位、被针对性修复”。

## 2026-03-24 完成情况回写

本日已完成全部 `P0` 项：

1. `regime-aware feedback evidence coverage planner`
2. `manager runtime profile calibration framework`
3. `strict run quality breakdown by manager x regime`

已落地结果：

- `research feedback` 现在带 `coverage_plan`，并进入 training / freeze report
- runtime YAML 现在支持统一 `regime_profiles` contract，且保留 legacy prefix fallback
- training evaluation 现在输出 `manager_regime_breakdown`
- promotion gate 现在支持可选 `manager_regime_validation`，默认关闭，不改变历史默认 verdict

剩余 `P1 / P2` 项仍作为后续候选，不在本轮提交内。
