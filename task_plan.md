# invest/ legacy 清零执行计划（已完成）

## 目标
清空 `invest/` 各子包中的 `legacy.py` / `_optimization_legacy.py`，让新目录结构承载真实实现，而不是继续依赖历史实现缓冲层。

## 已完成阶段
- [x] `shared/`：真实实现落入 `llm.py`、`contracts.py`、`indicators.py`、`summaries.py`、`tracking.py`
- [x] `agents/`：真实实现落入 `base.py`、`regime.py`、`hunters.py`、`reviewers.py`
- [x] `meetings/`：真实实现落入 `selection.py`、`review.py`、`recorder.py`
- [x] `trading/`：真实实现落入 `contracts.py`、`risk.py`、`helpers.py`、`engine.py`
- [x] `evaluation/`：真实实现落入 `cycle.py`、`benchmark.py`、`freeze.py`、`reports.py`
- [x] `selection/ + evolution/`：真实实现落入 `selectors.py`、`factors.py`、`risk_models.py`、`llm_optimizer.py`、`engine.py`、`orchestrator.py`、`optimizers.py`、`analyzers.py`
- [x] 删除全部 `legacy.py` 与 `invest/_optimization_legacy.py`
- [x] 完成全量回归测试

## 结果
- `invest/` 目录已不再存在 `legacy.py`
- 新包结构已承载真实业务代码
- 兼容层仅保留在对外入口 `invest/core.py`、`invest/optimization.py`
