# Router Rollout Runbook

## Default Mode

- `model_routing_enabled: true`
- `model_routing_mode: rule`
- `model_routing_agent_override_enabled: false`

## Recommended Rollout

1. 先用 `GET /api/model-routing/preview` 校验近期窗口决策。
2. 观察 SSE 中的 `routing_decided` 与 `model_switch_blocked`。
3. 验证真实训练结果中的 `routing_decision` 与 `allocation_plan`。
4. 稳定后再评估是否开启 `hybrid`。

## Rollback

将以下配置恢复：

```yaml
model_routing_enabled: false
model_routing_mode: off
```

或保留路由但停用 agent 参与：

```yaml
model_routing_enabled: true
model_routing_mode: rule
model_routing_agent_override_enabled: false
```
