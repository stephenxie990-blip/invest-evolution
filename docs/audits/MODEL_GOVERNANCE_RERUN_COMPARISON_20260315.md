# 模型治理基线复跑对比报告（2026-03-15）

## 1. 文档目标

本文用于对比以下两次完整训练运行：

- 基线运行：`outputs/phase_v11_validation_20260315_final`
- 治理修复后复跑：`outputs/phase_v11_validation_20260315_governance_rerun`

本报告聚焦两类结论：

1. 治理修复是否真正进入了真实训练闭环，而不只是停留在代码层和单元测试层。
2. 在治理门收紧后，模型效果、候选晋级、路由准入、冻结评估分别发生了什么变化。

## 2. 对比范围

### 2.1 代码与治理面

- `OptimizationEvent` 契约
- `lineage / promotion / deployment_stage` 纪律
- routing / leaderboard / allocator 质量门
- `promotion_gate / freeze_gate` 配置层上收
- `quality_gate_matrix` 输出可审计性

### 2.2 运行工件

- `leaderboard.json`
- `optimization_events.jsonl`
- `cycle_*.json`
- 训练末轮治理摘要

### 2.3 效果指标

- `score`
- `avg_return_pct`
- `avg_sharpe_ratio`
- `avg_max_drawdown`
- `benchmark_pass_rate`
- `eligible_for_routing`
- `promotion_status / deployment_stage`
- `governance_metrics`
- `freeze_gate_evaluation`

## 3. 执行结论摘要

### 3.1 治理结论

- 治理修复已经真实进入训练闭环，而不是停留在代码和测试层：
  - 新复跑 `optimization_events.jsonl` 共落盘 `44` 条事件，全部具备 `event_id / contract_version / cycle_id / lineage / evidence / contract_check`，且 `contract_check.passed=true`。
  - 旧基线 `optimization_events.jsonl` 仅有 `11` 条旧格式记录，全部缺少 `contract_version / cycle_id / lineage / event_id / evidence`。
  - 新复跑 `leaderboard.json` 顶层已显式输出 `quality_gate_matrix`，并对 entry 增加 `eligible_for_routing / deployment_stage / promotion_status / quality_gate / objective_eligible_after_governance`。
- 路由准入、候选晋级纪律、review/research 证据链已经落到真实工件：
  - 真实运行中出现了 `candidate_generated -> candidate_pending -> candidate_pruned` 链路。
  - review 事件不再只体现在 YAML mutation，而是以 `review_decision` 事件进入 `optimization_events.jsonl`。
  - allocator/router 已在最终路由决策中明确说明“没有通过质量门的正式候选”，没有再把负质量候选推进正式路由。
- `promotion_gate / freeze_gate` 默认策略参数已经上收到统一治理配置层，并通过运行期注入与测试守卫验证：
  - `promotion_gate` 默认策略继续由 `normalize_promotion_gate_policy(...)` 归一化。
  - `freeze_gate` 新增 `DEFAULT_FREEZE_GATE_POLICY` 与 `normalize_freeze_gate_policy(...)`，运行期会自动补全 `avg_sharpe_gte=0.8`、`benchmark_pass_rate_gte=0.60`、`research_feedback.min_sample_count=8`、`governance.max_override_pending_count=0` 等默认门槛。

### 3.2 效果结论

- 治理修复成功，不等于模型效果提升成功。本次复跑在治理上更严格，但模型表现并未达到生产路由或 freeze 标准。
- 与旧基线最佳模型相比，新复跑最佳条目在效果面明显回落：
  - `score`: `16.940772 -> -15.640334`
  - `avg_return_pct`: `1.512385 -> -1.726543`
  - `avg_sharpe_ratio`: `0.785999 -> -2.412323`
  - `avg_max_drawdown`: `2.404319 -> 2.936115`
  - `profit_rate`: `0.666667 -> 0.210526`
  - `benchmark_pass_rate`: `0.0 -> 0.052632`，虽略有改善，但仍远低于 promotion/freeze 需要的稳定水位
- 新治理基线下，系统选择“宁缺毋滥”：
  - `eligible_models=0`
  - `eligible_for_routing=false`
  - `deployment_stage=candidate`
  - `promotion_status=candidate_pruned`
  - `ineligible_reason=quality_gate:block_negative_score`

### 3.3 总体判断

- 本轮工作可以判定为“治理修复完成，效果优化尚未完成”。
- 这意味着系统已经具备了可信的治理基线，可以继续做后续 benchmark / Sharpe / 回撤优化；但在当前效果水平下，不应进入正式生产路由，也不应触发 freeze。

## 4. 基线运行摘要

### 4.1 基线 leaderboard 结论

- 旧基线 `outputs/phase_v11_validation_20260315_final/leaderboard.json` 的最佳模型为 `momentum::momentum_v1`：
  - `cycles=9`
  - `profit_rate=0.666667`
  - `avg_return_pct=1.512385`
  - `avg_sharpe_ratio=0.785999`
  - `avg_max_drawdown=2.404319`
  - `benchmark_pass_rate=0.0`
  - `score=16.940772`
- 旧基线还会把不同 regime 的“名义最优模型”直接写入 `regime_leaderboards`：
  - `bull`: `momentum`，`score=16.940772`
  - `bear`: `defensive_low_vol`，`score=0.65043`
  - `oscillation`: `mean_reversion`，`score=-17.881366`

### 4.2 基线治理短板

- 旧 `leaderboard.json` 没有 `quality_gate_matrix`、`eligible_models`、`deployment_stage`、`promotion_status`、`quality_gate` 等治理字段。
- 旧 `optimization_events.jsonl` 为旧契约格式，缺少：
  - `contract_version`
  - `cycle_id`
  - `lineage`
  - `event_id`
  - `evidence`
- 旧 regime leaderboard 会把 `score=-17.881366`、`avg_return_pct=-2.59938`、`benchmark_pass_rate=0.0` 的 `mean_reversion` 直接挂进 `oscillation` 榜单，说明当时的准入门并没有收紧到正式路由层。

## 5. 治理复跑摘要

### 5.1 事件契约与审计

- 新复跑 `outputs/phase_v11_validation_20260315_governance_rerun/optimization_events.jsonl` 共 `44` 条事件，全部 `status=ok`，全部 `contract_check.passed=true`。
- 事件阶段分布：
  - `review_decision`: `20`
  - `yaml_mutation`: `10`
  - `research_feedback`: `6`
  - `llm_analysis`: `4`
  - `evolution_engine`: `4`
- 事件触发分布：
  - `review_meeting`: `20`
  - `research_feedback`: `12`
  - `consecutive_losses`: `12`
- 事件 lineage 分布：
  - `deployment_stage=override`: `20`
  - `deployment_stage=active`: `14`
  - `deployment_stage=candidate`: `10`
  - `promotion_status=override_pending`: `20`
  - `promotion_status=not_evaluated`: `14`
  - `promotion_status=candidate_generated`: `10`
- 关键证据样例：
  - 第 1 轮 review 事件已经带出 `optimization_event.v2`、`cycle_id=1`、`deployment_stage=override`、`promotion_status=override_pending`。
  - 第 3 轮 research feedback mutation 已生成候选配置 `/data/evolution/generations/momentum_v1_cycle_0003.yaml`，并将 lineage 记录为 `deployment_stage=candidate`、`promotion_status=candidate_generated`。

### 5.2 路由准入与候选纪律

- 新复跑 `leaderboard.json` 只保留 `1` 个模型条目，但没有任何模型通过正式路由质量门：
  - `eligible_models=0`
  - `regime_leaderboards={}`
- 最终条目 `momentum::momentum_v1` 的质量门失败项为：
  - `block_negative_score`
  - `min_score`
  - `min_avg_return_pct`
  - `allowed_deployment_stages`
- 路由层的最终行为已与治理约束一致：
  - `routing_decision.hold_reason=no_qualified_routing_candidates`
  - `allocator_quality.qualified_candidate_count=0`
  - `failed_quality_entries` 显式指出 `momentum` 因 `quality_gate:block_negative_score` 且 `deployment_stage=candidate` 被排除
- 候选纪律在 20 轮真实运行中已经清晰可见：
  - `candidate_pending_count=10`
  - `override_pending_count=10`
  - `rejected_candidate_count=9`
  - `promotion_applied_count=0`
  - `active_stage_count=0`
- 各周期演进序列显示新纪律不是“纸面规则”：
  - 第 `3` 轮进入 `candidate_pending / awaiting_gate`
  - 第 `5/6/8/10/11/13/14/16/19` 轮进入 `candidate_pruned / rejected`
  - 其余大量周期维持 `override_pending`

### 5.3 freeze / promotion / quality gate 结果

- `promotion_gate` 结果：
  - 最终 leaderboard 条目 `promotion_gate_status=rejected`
  - 第 20 轮 `promotion_record.gate_status=override_pending`
  - 全程没有任何候选被 `applied_to_active`
- `freeze_gate` 默认策略已经统一到治理配置层：
  - 默认 freeze 门槛为 `avg_return_gt=0.0`、`avg_sharpe_gte=0.8`、`avg_max_drawdown_lt=15.0`、`benchmark_pass_rate_gte=0.60`
  - research feedback 默认门槛为 `min_sample_count=8`、禁止 `tighten_risk/recalibrate_probability`
  - governance 默认门槛为 `max_candidate_pending_count=0`、`max_override_pending_count=0`、`max_active_candidate_drift_rate=0.0`
- research feedback gate 在最终运行态明确失败：
  - `bias=tighten_risk`
  - `sample_count=8`
  - `blocked_biases` 失败
  - `T+10 / T+20 / T+5 / T+60` 的 `hit_rate / invalidation_rate / interval_hit_rate` 多项失败
- governance 侧也不满足 freeze 要求：
  - `active_candidate_drift_rate=0.5`
  - `candidate_pending_count=10`
  - `override_pending_count=10`
- 因此可以明确判定：本次 rerun 不具备 freeze 条件。

## 6. 差异对比

### 6.1 事件契约差异

| 维度 | 旧基线 | 新复跑 |
|---|---|---|
| event 条数 | 11 | 44 |
| `contract_version` | 缺失 | `optimization_event.v2` |
| `cycle_id` | 缺失 | 全量具备 |
| `lineage` | 缺失 | 全量具备 |
| `event_id` | 缺失 | 全量具备 |
| `evidence` | 缺失 | 全量具备 |
| `contract_check` | 无 | 44/44 通过 |
| review 事件入 `jsonl` | 否 | 是 |

### 6.2 leaderboard 与 routing 差异

| 维度 | 旧基线 | 新复跑 |
|---|---|---|
| `quality_gate_matrix` | 无 | 有 |
| `eligible_models` | 无 | `0` |
| `eligible_for_routing` | 无 | `false` |
| `deployment_stage` | 无 | `candidate` |
| `promotion_status` | 无 | `candidate_pruned` |
| `regime_leaderboards` | 有，且包含负分模型 | 空对象 |
| router 行为 | 可继续参考 regime 榜单 | 因无合格候选而 hold current |

### 6.3 candidate / override / active 差异

- 旧基线没有足够的候选晋级纪律字段，很难回答“当前是 active、candidate 还是 override”。
- 新复跑中三种状态被彻底分离并留痕：
  - `active`: 仅存在于部分事件 lineage，表示基线配置上下文
  - `candidate`: 真实出现 `candidate_generated / candidate_pending / candidate_pruned`
  - `override`: review 期运行时覆盖会被明确记为 `override_pending`
- 这意味着新的治理面已经能回答两个关键问题：
  - 当前变更是否只是运行期 override，还是已经成为待晋级 candidate
  - candidate 是否被 gate 卡住、被淘汰，还是成功晋级

### 6.4 效果指标差异

| 指标 | 旧基线最佳 | 新复跑最佳 | 变化 |
|---|---:|---:|---:|
| `score` | 16.940772 | -15.640334 | -32.581106 |
| `avg_return_pct` | 1.512385 | -1.726543 | -3.238928 |
| `avg_sharpe_ratio` | 0.785999 | -2.412323 | -3.198322 |
| `avg_max_drawdown` | 2.404319 | 2.936115 | +0.531796 |
| `benchmark_pass_rate` | 0.0 | 0.052632 | +0.052632 |
| `profit_rate` | 0.666667 | 0.210526 | -0.456140 |

结论不是“新治理让模型变差”，而是“新治理不再掩盖真实质量”。旧基线下的正向排序结果没有被正式路由门、promotion gate 和 freeze gate 共同审视；新基线则把这些问题完整暴露出来。

## 7. 关键证据

### 7.1 新治理基线已进入真实工件的证据

- `cycle_20.json` 的 `run_context.quality_gate_matrix` 已完整落盘，覆盖 `review / optimization / routing / promotion / freeze / effect_objectives`。
- `optimization_events.jsonl` 中的 review 事件已包含：
  - `event_id`
  - `contract_version`
  - `cycle_id`
  - `lineage`
  - `evidence`
  - `notes`
- `routing_decision.reasoning` 已明确改为：
  - 由于没有通过质量门的正式候选，继续持有当前 active，不启用 provisional 候选
- 第 3 轮 event 已形成 `research_feedback -> yaml_mutation -> candidate_generated` 证据链，说明 candidate 生成不是文档设计，而是真实运行结果。

### 7.2 新治理基线对效果面的真实影响

- 治理门收紧后，系统不再把“短时收益回正”误判为“可正式路由”：
  - 即便个别周期收益转正，只要 `score` 仍为负、`avg_return_pct` 为负、或 `deployment_stage` 不是 `active`，`eligible_for_routing` 仍保持 `false`。
- 最终路由决策与 leaderboard 的结构化证据保持一致，没有出现“文字说阻断，实际仍路由”的错位。
- research feedback 仍持续给出 `tighten_risk`，且多 horizon 命中率/失效率显著不达标，说明问题已经从“治理/契约缺失”转向“模型质量与 agent 证据质量不足”。

## 8. 问题与风险

- `leaderboard.json` 停留在 `latest_cycle_id=19`，而真实训练已经完成到 `cycle_20.json`。这说明“最终一轮训练完成后是否再次刷新 run-specific leaderboard”仍存在工件同步风险。
- 运行期虽然已经注入了默认 `promotion_gate / freeze_gate`，但 `leaderboard.json` 和 `config_snapshots/cycle_0020.json` 的 `policy.train.promotion_gate / freeze_gate` 仍为 `null`。这意味着“配置层可调”已经在代码和测试层成立，但运行工件对“归一化后默认策略”的显式审计仍不完整。
- 模型质量风险依然明显：
  - 最佳模型为负分、负收益、负 Sharpe
  - research feedback 连续给出 `tighten_risk`
  - `candidate_pending / override_pending` 大量积压
- Agent 效果风险仍待后续专项修复，尤其是 bull regime 下的信号有效性、TrendHunter/QualityAgent 的长期命中率、相似失败样本过滤能力。

## 9. 后续建议

1. 先补“最终工件一致性”：
   - 训练结束后强制刷新一次 run-specific `leaderboard.json`
   - 将归一化后的 `promotion_gate / freeze_gate` 以 resolved policy 形式显式写入最终工件，补齐审计闭环
2. 在当前治理基线上继续做效果优化，而不是回退治理门：
   - 优先围绕 `benchmark_pass_rate`、`avg_sharpe_ratio`、`avg_return_pct`、`avg_max_drawdown` 做目标优化
   - 不允许通过放松 routing/promotion/freeze 门槛来“制造合格结果”
3. 将 bull regime 作为首个专项优化面：
   - 提高 bull 场景的入场门槛
   - 建立相似失败样本过滤或降权
   - 重新校正 TrendHunter 与 QualityAgent 的权重和证据一致性
4. 增强 freeze 侧工件：
   - 在最终训练报告中固定输出 `freeze_gate_evaluation`
   - 将 `candidate_pending_count / override_pending_count / active_candidate_drift_rate` 作为默认审计摘要
5. 在治理修复完成后，才继续做新一轮效果提升 rerun：
   - 以“质量门不过就不晋级”为硬约束
   - 以 benchmark / Sharpe / 回撤三类目标为主，不再混淆治理修复和收益优化
