# Research Unification Eval - Phase 3

## Capability

- `ask_stock` saves `research_case_id`
- historical ask cases auto-produce `OutcomeAttribution`
- calibration report writes to `runtime/state/research_calibration/`

## Regression

Run:

```bash
./.venv/bin/python -m pytest -q tests/test_research_attribution_engine.py tests/test_research_case_store.py tests/test_ask_stock_model_bridge.py
```
