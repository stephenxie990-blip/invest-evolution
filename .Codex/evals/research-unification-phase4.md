# Research Unification Eval - Phase 4

## Capability

- scenario engine switches from heuristic bootstrap to empirical case similarity when prior attributed cases exist
- ask output contains `research.scenario`
- probability/interval output does not break Phase 1-3 schema

## Regression

Run:

```bash
./.venv/bin/python -m pytest -q tests/test_ask_stock_model_bridge.py -k empirical
```
