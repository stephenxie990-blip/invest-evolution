# Governance Recovery Blueprint (v1.5 <- v1.0)

## Goal

Restore the governance and runtime-discipline capabilities that were present in
`v1.0` but were removed or narrowed in `v1.5`, while preserving the current
`governance-first` architecture and owner boundaries in `v1.5`.

This blueprint treats the work as a recoverable "lake":

- restore missing governance contracts
- reconnect training-time proposal lifecycle
- recover runtime discipline and safety boundaries
- preserve existing `v1.5` aggregation and naming (`governance`, not `routing`)

## Recovery Scope

### Must restore

1. `regime_hard_fail` and strategy-family governance profiles
2. proposal governance gate
3. proposal bundle persistence
4. candidate-from-proposals build path
5. suggestion tracking and effect tracking
6. runtime discipline window
7. safety override whitelist
8. regime runtime profile

### Recommended restore

1. proposal ingress normalization from review / research / optimization outputs
2. proposal and suggestion summaries in persisted training artifacts
3. governance reports that surface regime hard fail and proposal outcomes

### Optional restore

1. legacy naming / artifact compatibility
2. expanded internal governance preview
3. public read-only governance surface

## Owner Map

### `src/invest_evolution/investment/shared/policy.py`

Owns shared governance contracts and policy normalization.

Planned additions:

- `normalize_strategy_family_name`
- `resolve_strategy_family_regime_hard_fail_profile`
- `evaluate_regime_hard_fail`
- `normalize_proposal_gate_policy`
- `evaluate_candidate_proposal_gate`
- governance matrix merge support for strategy-family regime overrides

### `src/invest_evolution/investment/governance/engine.py`

Owns governance ranking / leaderboard / eligibility.

Planned additions:

- include `regime_hard_fail` in governance quality gate application
- expose regime fail detail on entries / leaderboard payloads

### `src/invest_evolution/application/training/persistence.py`

Owns training artifacts and durable bundle storage.

Planned additions:

- `persist_cycle_proposal_bundle`
- `load_cycle_proposal_bundle`
- `list_cycle_proposal_bundles`
- `update_cycle_proposal_bundle`
- persisted proposal / suggestion summary projections

### `src/invest_evolution/application/training/observability.py`

Owns audit payloads, tracking summaries, and cross-cycle effect evaluation.

Planned additions:

- `ensure_proposal_tracking_fields`
- `apply_proposal_outcome`
- `evaluate_proposal_effect`
- `refresh_cycle_history_suggestion_effects`
- `build_suggestion_tracking_summary`
- reporting hooks for regime hard fail / proposal outcomes

### `src/invest_evolution/application/training/execution.py`

Owns cycle orchestration, runtime mutation flow, and candidate production.

Planned additions:

- `resolve_active_runtime_params`
- `resolve_effective_runtime_params`
- `begin_cycle_runtime_window`
- `finalize_cycle_runtime_window`
- `apply_safety_override`
- `resolve_entry_threshold_spec`
- `build_regime_runtime_profile`
- `apply_regime_runtime_profile`
- `build_cycle_candidate_from_proposals`

### `src/invest_evolution/application/training/review.py`
### `src/invest_evolution/application/training/research.py`

Own proposal ingress from review / research outputs.

Planned additions:

- normalize review / research adjustments into proposal records
- route proposal creation through a shared proposal recording boundary

## Integration Rules

1. Keep `v1.5` naming and public surfaces intact.
2. Restore capability through current owners rather than reviving retired
   fragment packages.
3. Preserve compatibility with existing promotion / lineage / freeze records.
4. Add focused regression tests for every restored contract.
5. Do not revert unrelated dirty worktree changes.

## Parallel Work Breakdown

1. Shared governance contract recovery
2. Governance engine integration
3. Proposal bundle persistence
4. Suggestion tracking and observability
5. Runtime discipline and candidate build orchestration
6. Proposal ingress from review / research

## Verification Strategy

Targeted suites to add or expand:

- `tests/test_governance_policy.py`
- `tests/test_governance_engine.py`
- `tests/test_leaderboard.py`
- `tests/test_training_promotion_lineage.py`
- new proposal-store / suggestion-tracking / runtime-discipline tests
- focused execution integration tests for candidate build from proposals

## Quality Gate

The work is complete only when:

1. restored contracts exist in the expected owners
2. proposal lifecycle is durable and queryable
3. runtime discipline rejects illegal in-cycle mutation
4. regime hard fail can block governance eligibility
5. promotion / lineage still reflect the restored states
6. targeted regression tests pass
