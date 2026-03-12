# Research Unification Eval - Phase 2

## Capability

- `research.hypothesis` is the main ask semantic object
- `dashboard` is rendered from `ResearchHypothesis` projection
- YAML remains evidence DSL only

## Regression

Verify:

- `payload["dashboard"]["signal"] == payload["research"]["hypothesis"]["stance"]`
- legacy ask payload fields remain readable by old callers
