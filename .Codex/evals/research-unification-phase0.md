# Research Unification Eval - Phase 0

## Capability

- `PolicySnapshot.version_hash` same input => same output
- `ResearchSnapshot/PolicySnapshot/ResearchHypothesis/OutcomeAttribution` can serialize
- `ask_stock(as_of_date=...)` semantics documented in `docs/research/phase0_contract_mapping.md`

## Regression

Run:

```bash
./.venv/bin/python -m pytest -q tests/test_research_contracts.py tests/test_research_case_store.py
```
