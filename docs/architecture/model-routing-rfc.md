# Model Routing RFC

## Goal

在选股模型执行前，先完成“市场观察 → 市场状态识别 → 模型路由 → 治理护栏审查”，避免长期固定运行 `momentum`。

## Current Runtime Shape

训练主链路位于 `app/train.py`：

1. 加载训练数据
2. 执行模型路由
3. 执行投资模型
4. 召开选股会议
5. 模拟交易与评估
6. 复盘与优化

## Implemented Components

- `invest/router/engine.py`
  - `MarketObservationService`
  - `RegimeClassifier`
  - `ModelRoutingCoordinator`
- `invest/contracts/model_routing.py`
  - `ModelRoutingDecision`
- `invest/agents/model_selector.py`
  - `ModelSelectorAgent`

## Decision Model

- `rule`：规则路由器主导，默认模式。
- `hybrid`：允许 agent 给出 advisory，并在护栏允许范围内参与选择。
- `agent`：保留为受护栏约束的增强模式。
- `off`：关闭路由，维持当前模型。

## Guardrails

- `model_switch_min_confidence`
- `model_switch_hysteresis_margin`
- `model_switch_cooldown_cycles`
- `model_routing_agent_override_max_gap`

## Audit Trail

每轮训练会记录：

- `routing_decision`
- `allocation_plan`
- `routing_mode`
- `routing_regime`
- `routing_model`

并通过 SSE 发出：

- `routing_started`
- `regime_classified`
- `routing_decided`
- `model_switch_applied`
- `model_switch_blocked`
