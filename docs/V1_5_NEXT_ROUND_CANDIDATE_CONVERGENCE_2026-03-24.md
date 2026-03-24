# v1.5 第三阶段后的下一轮候选收敛

Date: 2026-03-24
Baseline commit: `f376f0a`
Previous milestone: `f2c4f5c feat: complete phase3 optimization governance pass`
Status: converged shortlist for next execution round

## 结论

基于当前 `v1.5` 状态，下一轮已经不应继续沿用“大而散”的 `P1 / P2` 长清单。

第三阶段已经完成的事情，改变了问题性质：

- `regime-aware feedback evidence coverage planner` 已落地，`research feedback` 不再是“完全没有证据”
- `manager runtime profile calibration framework` 已落地，runtime profile 不再主要卡在 contract 缺失
- `strict run quality breakdown by manager x regime` 已落地，strict 失败已能拆到更细粒度
- `f376f0a` 进一步补了 oscillation 方向的治理再平衡，说明系统已从“补模块”进入“收口质量主阻塞”阶段

因此，下一轮候选必须只保留那些能够直接推动 `strict readiness` 从“质量未过线”收敛到“可以明确验证”的项目。

## 当前真实剩余阻塞

以 `STRICT_TRAINING_READINESS_CHECKLIST_2026-03-24.md` 为准，当前主阻塞已经很清楚：

1. `Return Objectives` 仍未过线
2. `Regime Validation` 仍卡在 bull 缺口与 oscillation 质量拖累
3. `Research Feedback Gate` 已从“无证据”转为“证据质量未通过 strict 阈值”
4. `strict shadow gate` 还没有在 fresh artifacts 上完成最终通过

这意味着下一轮最该做的事情，不是继续补更多模块，而是补齐以下三类“最后一公里”能力：

- 训练过程真相链是否完整
- regime 探测与实验复用是否足够高效
- research evidence 检索与二次分析是否足够快、足够准

## 收敛原则

下一轮候选只保留满足以下条件的事项：

- 能直接缩短 `strict blocker -> root cause -> targeted fix -> rerun` 的闭环
- 能直接改善 fresh-artifact strict/shadow 验证的证据质量或验证效率
- 能挂在现有 owner 主链上，而不是引入新的横向复杂度

不满足这些条件的项，统一降级为“记录保留，但不进入下一轮主线”。

## 下一轮必须做

### 1. runtime discipline event lineage completion

#### 目标

把 runtime discipline 相关的 proposal、delay、candidate build、effective apply、final review 统一串成一条可追溯 lineage，消除“参数到底什么时候真正生效”的灰区。

#### 现在必须做的原因

当前 strict 失败已经不再是“有没有指标”，而是“为什么失败、失败发生在哪条参数演进链上”。如果不能把参数事件链串起来，`Return Objectives` 和 `Regime Validation` 的 root cause 仍会停留在人工拼接证据。

#### 直接对应的 strict blocker

- `Return Objectives`
- `Regime Validation`
- `strict shadow gate` 的 final sign-off 可解释性

#### 建议 owner 文件

- `src/invest_evolution/application/training/execution.py`
- `src/invest_evolution/application/training/observability.py`
- `src/invest_evolution/application/training/persistence.py`

#### 最小验收标准

- 每个 cycle 都能输出统一的 `discipline_lineage` 或等价结构
- 同一条 lineage 至少覆盖 `proposed -> deferred -> candidate-built -> applied -> reviewed`
- freeze / review / run report 读取的是同一条事件真相链，而不是多个近似副本
- 能明确回答“某个 manager 在某个 regime 下的参数变动何时影响了最终结果”

#### 不做的后果

- strict rerun 仍会继续出现“知道坏了，但不知道是哪次纪律调整导致”的低效排查
- shadow gate 即使接近通过，也难以形成可审计的 sign-off 结论

### 2. isolated experiment discovery index / replay cache

#### 目标

为 isolated experiments 建立 discovery index 与 replay cache，让多 cutoff、多 regime、多 manager 的探测结果优先复用，减少重复 preview 和重复试跑。

#### 现在必须做的原因

当前 `Regime Validation` 的主阻塞之一，是 bull / oscillation 的独立样本与质量仍不够稳。下一轮很可能需要更密集地跑多窗口实验和定向 regime 补样。如果每次都全量重探测，修复闭环会被成本和时间拖慢。

#### 直接对应的 strict blocker

- `Regime Validation`
- `Return Objectives`
- fresh-artifact strict probe 前的低成本定向验证效率

#### 建议 owner 文件

- `src/invest_evolution/application/training/isolated_experiments.py`
- `src/invest_evolution/application/training/observability.py`
- `scripts/run_isolated_regime_manager_experiment.py`

#### 最小验收标准

- 相同 `(manager, regime, cutoff window, config signature)` 的 discovery 结果可以命中复用
- replay cache 失效条件清晰，配置变更或数据窗口漂移时自动失效
- 运行报告能区分“新探测”与“缓存复用”
- 多 cutoff regime 补样实验的准备成本明显下降，且不牺牲审计可追溯性

#### 不做的后果

- bull / oscillation 相关验证会持续被重复 preview 成本拖慢
- 下一轮很难高频迭代到足以支撑 strict gate 复验的样本覆盖

### 3. research case store secondary index

#### 目标

在已有 case store 缓存和去重基础上，为 `manager_id / regime / as_of_date / hypothesis_id / horizon` 提供轻量 secondary index 与稳定查询契约，支撑更快的 feedback quality 诊断。

#### 现在必须做的原因

`Research Feedback Gate` 现在已经不是“样本为 0”，而是“strict 质量不过关”。下一轮最需要的是快速回答：到底是哪些 manager、哪些 regime、哪些 horizon、哪些 observation 在拉低 gate，而不是继续做高成本线性筛查。

#### 直接对应的 strict blocker

- `Research Feedback Gate`
- bull regime 独立证据扩样后的质量复盘
- strict shadow gate 前的 feedback truth inspection

#### 建议 owner 文件

- `src/invest_evolution/investment/research/case_store.py`
- `src/invest_evolution/application/training/research.py`
- `src/invest_evolution/application/training/observability.py`

#### 最小验收标准

- 高频 research feedback 查询不再依赖全量线性遍历
- query 结果能稳定按 `manager x regime x horizon` 回答“谁贡献了通过/失败证据”
- 能直接产出支持 strict gate 复核的 evidence slices
- 与现有去重口径一致，不引入新的 cross-manager 污染

#### 不做的后果

- research gate 仍会停留在“知道没过，但难以迅速定位具体坏样本”的状态
- 每次 strict probe 失败后的复盘成本仍然偏高

## 建议恢复为旁线，不进入下一轮主线

下面这些项仍有价值，但不应继续占用下一轮主线带宽：

### phase-aware release / readiness dashboard

保留原因：

- 对管理层和文档阅读体验有价值

当前不进主线的原因：

- 当前真正缺的不是“看板缺失”，而是底层 strict 收敛动作本身
- 如果底层三项没完成，看板只会更完整地展示“仍未通过”

建议 owner 文件：

- `docs/`
- `src/invest_evolution/application/training/observability.py`

### manager portfolio vs single manager truth-table regression pack

保留原因：

- 对防止主体语义回退有长期价值

当前不进主线的原因：

- 当前主阻塞已从“主体身份混乱”转到“质量与验证收口”
- 该项更适合在下一轮主线完成后补成防回退回归包

建议 owner 文件：

- `tests/test_training_review_protocol.py`
- `tests/test_training_boundary_adapters.py`
- `tests/test_training_controller_services.py`

## 建议下沉为记录项

下面这些项本轮不再作为独立候选推进，只保留在历史清单中备查：

- `shared config-ref normalization contract extraction`
  - 原因：当前不构成 strict readiness 主阻塞，且已有 helper/registry 收口基础
- 通用性的文档整编 / 体验型审计增强
  - 原因：优先级低于 strict blocker 收敛
- 泛化的“再补一个治理模块”
  - 原因：问题已不再是模块缺失，而是现有主链的质量闭环

## 推荐执行顺序

1. `runtime discipline event lineage completion`
2. `research case store secondary index`
3. `isolated experiment discovery index / replay cache`

这个顺序的原因是：

- 先把训练事件真相链补齐，避免后面所有修复继续建立在半解释状态上
- 再把 research evidence 查询做快做准，缩短 research gate 的复盘时间
- 最后把 isolated experiments 的 discovery/replay 成本打下来，加速 bull / oscillation 的多窗口补样与复验

## 下一轮完成后的期望状态

如果下一轮按上述三项收敛完成，`v1.5` 应该出现以下变化：

- strict 失败可以被定位到明确的参数事件链，而不是只看到 aggregate 坏结果
- bull / oscillation 的定向实验可以更快复跑、更便宜复跑
- research feedback gate 失败可以更快拆到具体 manager / regime / horizon 证据切片
- strict probe 与 shadow gate 将从“知道没过但排查慢”，进入“可以快速定向修复并复验”的状态

## 一句话判断

第三阶段之后，`v1.5` 的下一轮不该继续“扩候选”，而应该“收口最后三项直接影响 strict readiness 收敛速度的能力”。
