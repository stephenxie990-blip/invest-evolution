# Phase 0 Contract Mapping

## 目标

把旧问股对象与统一研究引擎对象建立一一映射，确保后续 Phase 1-4 都围绕同一 contract 演进。

## 旧对象 → 新对象映射

| 旧语义/对象 | 新对象 | 映射说明 |
|---|---|---|
| `ask_stock` 的 point-in-time 分析上下文 | `ResearchSnapshot` | 由 `ModelOutput + queried symbol + tool-derived evidence` 共同生成 |
| 训练侧 active/routed model + runtime params | `PolicySnapshot` | 由 `InvestmentModel + routing_context + data_window` 解析，`version_hash` 稳定签名 |
| 问股最终结论 / old dashboard | `ResearchHypothesis` | dashboard 退化为 projection，不再作为主语义 |
| 问股事后表现判断 | `OutcomeAttribution` | 使用 `T+5/T+10/T+20/T+60` 多时钟协议统一评分 |

## `ask_stock(as_of_date=...)` 语义

- `requested_as_of_date`：用户传入的时间边界。
- `effective_as_of_date`：数据库中 **小于等于** `requested_as_of_date` 的最新可见交易日；若未传入则为该标的最新可见交易日。
- YAML tool plan 与 routed model bridge **都只能读取 `effective_as_of_date` 及之前的数据**。
- 若用户显式回放历史日期，则 bridge 进入 `config_default_replay_safe` 模式：
  - 不复用 live controller 的 runtime overrides，避免参数层未来泄漏。
  - 仍复用相同模型注册表、相同路由规则与相同 contract。

## `PolicySnapshot.version_hash` 字段约束

签名由以下字段 canonical JSON 后进行 SHA-256：

1. `model_name`
2. `config_name`
3. `params`
4. `risk_policy`
5. `execution_policy`
6. `evaluation_policy`
7. `review_policy`
8. `agent_weights`
9. `routing_context`
10. `data_window`
11. `feature_version`
12. `code_contract_version`

## `OutcomeAttribution.horizon_results` 固定 schema

每个 horizon（`T+5/T+10/T+20/T+60`）至少包含：

- `label`
- `return_pct`
- `excess_return_pct`
- `max_favorable_excursion`
- `max_adverse_excursion`
- `entry_triggered`
- `invalidation_triggered`
- `de_risk_triggered`
- `end_trade_date`

## Runtime 工件布局

- `runtime/state/research_cases/*.json`
- `runtime/state/research_attributions/*.json`
- `runtime/state/research_calibration/*.json`

## 当前实现边界

- 已完成 ask→train research bridge、case store、multi-horizon attribution、scenario engine、calibration report。
- 训练主循环尚未主动消费 calibration report，但 artifact 已稳定落盘，可被训练侧读取。
