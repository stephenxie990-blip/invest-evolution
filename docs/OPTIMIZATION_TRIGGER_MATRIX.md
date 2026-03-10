# 优化触发矩阵

当前训练链路里，优化、复盘、固化与跳过都已经有比较明确的触发条件。

## 1. 触发矩阵

| 场景 | 触发条件 | 代码入口 | 当前动作 | 输出工件 |
| --- | --- | --- | --- | --- |
| 数据不足跳过 | 没有加载到可用训练数据 | `run_training_cycle()` | 标记 skip，返回 `None` | 周期结果由外层记为 `no_data` |
| 选股为空跳过 | 模型与会议未产出可交易标的 | `run_training_cycle()` | 标记 skip，返回 `None` | `no_data` |
| 数据不匹配跳过 | 选股结果在数据集中不可用 | `run_training_cycle()` | 标记 skip，返回 `None` | `no_data` |
| 未来交易日不足跳过 | `dates_after < simulation_days` | `run_training_cycle()` | 标记 skip，返回 `None` | `no_data` |
| 复盘触发 | 每个成功周期结束后 | `ReviewMeeting.run_with_eval_report()` | 输出建议、参数调整、权重调整 | review meeting JSON/MD |
| 连续亏损优化 | `consecutive_losses >= max_losses_before_optimize` | `_trigger_optimization()` | LLM 诊断、进化引擎、YAML mutation | `optimization_events.jsonl` |
| 固化触发 | 近窗 win rate / avg return / sharpe / drawdown / benchmark 全满足 | `should_freeze()` | 生成 freeze report，可提前停止 | `model_frozen.json` |
| allocator 切换模型 | `allocator_enabled = true` | `_maybe_apply_allocator()` | 根据 regime + leaderboard 切换主模型 | 当前周期结果 + leaderboard |

## 2. 连续亏损优化细节

当连续亏损达到阈值时，当前实现会串行尝试以下动作：

1. `LLMOptimizer.analyze_loss()`
2. 根据分析结果调整 runtime params
3. `EvolutionEngine.evolve()` 更新种群并尝试拿最佳参数
4. `YamlConfigMutator` 写出候选模型配置
5. 记录 optimization event 并重置 `consecutive_losses`

注意：

- mutation 生成的 YAML 不一定自动接管 active config
- 是否自动接管，取决于 `auto_apply_mutation`

## 3. 固化门控细节

freeze gate 当前要求最近窗口同时满足：

- 胜率 >= `freeze_profit_required / freeze_total_cycles`
- 平均收益率 > `avg_return_gt`
- 平均 Sharpe >= `avg_sharpe_gte`
- 平均最大回撤 < `avg_max_drawdown_lt`
- benchmark pass rate >= `benchmark_pass_rate_gte`

这些阈值优先由当前模型 YAML 的 `train.freeze_gate` 控制。

## 4. review 与 optimization 的关系

- `review_meeting` 是每个成功周期都会执行的标准动作
- `optimization` 是连续亏损达到阈值时才触发的强化调整动作
- 两者都会以 event 形式写入周期结果和相关工件

## 5. 当前建议

如果要新增触发器，优先明确 4 个问题：

1. 触发条件由哪个指标负责
2. 触发动作是“建议”还是“自动应用”
3. 触发结果写到哪个工件
4. 是否会改变 freeze / leaderboard / allocator 口径
