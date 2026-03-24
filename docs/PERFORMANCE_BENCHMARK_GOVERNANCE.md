# Performance Benchmark Governance

日期：2026-03-21
状态：active

## 目标

把发布后剩余的性能治理项从“只有绿灯 bundle”推进到“有固定范围、固定工件、固定复核口径”的长期资产。

## 统一基线

- 统一验证 bundle：`uv run python -m invest_evolution.application.release --bundle performance-regression`
- 统一发布入口：`uv run python scripts/run_release_readiness_gate.py --include-commander-brain`
- 统一证据目录：
  - `outputs/release_readiness/`
  - `outputs/release_shadow_gate_*/`

## 必须覆盖的热点

1. `src/invest_evolution/agent_runtime/memory.py`
   - 目标：记忆读取与缓存命中路径不出现回退性放大
   - 回归面：`tests/test_memory.py`

2. `src/invest_evolution/investment/memory.py`
   - 目标：BM25 lazy rebuild 行为可验证、不可静默退化
   - 回归面：`tests/test_brain_extensions.py`

3. `src/invest_evolution/investment/foundation/compute.py`
   - 目标：指标快照热点维持批量化实现，不回退到逐行热点
   - 回归面：`tests/test_factors_and_indicators_suite.py`

4. `src/invest_evolution/market_data/manager.py` + `src/invest_evolution/market_data/repository.py`
   - 目标：批量 ingestion、quality/gateway 编排与 canonical schema 写路径保持受控
   - 回归面：`tests/test_market_data_ingestion.py`

5. 发布聚合与落盘
   - 目标：性能回归 bundle 持续纳入 release readiness 主链
   - 回归面：`tests/test_release_management_suite.py`

## 工件要求

- 每次性能治理变更后，至少保留一轮 `performance-regression` 通过记录
- release readiness 复跑时，性能 bundle 必须并入统一 gate
- 如新增热点，必须同时补充：
  - 对应 focused regression
  - bundle 纳入说明
  - 本文档中的热点目录

## 验收标准

- `performance-regression` bundle 持续存在且可独立执行
- bundle 中覆盖 memory / BM25 / indicators / ingestion / release-management 五类资产
- release readiness wrapper 保持把性能回归纳入主流程
- 文档、bundle、测试三者一致
