# Research Unification Eval - Phase 1

## Capability

- `ask_stock` accepts `as_of_date`
- `ask_stock` returns `research.snapshot` and `research.policy`
- queried symbol returns `rank / percentile / selected_by_policy / policy_id`

## Regression

Run:

```bash
./.venv/bin/python -m pytest -q tests/test_ask_stock_model_bridge.py tests/test_stock_analysis_react.py tests/test_schema_contracts.py
```
