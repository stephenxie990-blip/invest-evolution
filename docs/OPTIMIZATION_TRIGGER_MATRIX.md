# 优化触发矩阵

## 目标

把当前训练系统中的“建议、决策、已应用变更”来源讲清楚，降低优化层心智负担。

## 触发矩阵

| 触发源 | 触发条件 | 参与模块 | 输出类型 | 是否直接改参数 |
|---|---|---|---|---|
| ReviewMeeting | 每轮训练后 | `Strategist` / `EvoJudge` / `Commander` | 复盘建议、参数调整、权重调整 | 是 |
| LLMOptimizer | 连续亏损达到阈值 | `LLMOptimizer` | 亏损原因、修复建议 | 是 |
| EvolutionEngine | 连续亏损达到阈值且历史样本足够 | `EvolutionEngine` | 演化后的候选参数 | 是 |
| BenchmarkEvaluator | 每轮评估 | `BenchmarkEvaluator` | 质量门控指标 | 否 |
| FreezeEvaluator | 连续周期汇总 | `FreezeEvaluator` | 是否满足冻结条件 | 否 |

## 统一事件模型

当前优化输出建议统一抽象为：

- `trigger`
- `stage`
- `status`
- `suggestions`
- `decision`
- `applied_change`
- `notes`
- `ts`

## 建议

后续如果继续拆分优化层，应保持：

1. 建议与已应用变更分离
2. 复盘调整与进化调整分离
3. 每次参数变化都有明确来源
