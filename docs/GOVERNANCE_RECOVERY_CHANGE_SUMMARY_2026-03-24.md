# Governance Recovery Change Summary (2026-03-24)

Commit: `fc1219f4d6efb36994bb25a30e36d56ede548c59`

## What Shipped

This change restored the missing governance recovery pipeline in `v1.5` by
reconnecting the proposal lifecycle, runtime discipline window, and
candidate-build path that had regressed relative to `v1.0`.

The recovery intentionally keeps the current `v1.5` architecture intact:

- preserve current owner boundaries instead of reviving retired `v1.0` packages
- preserve `v1.5` public naming and governance-first semantics
- restore missing capability through current owners and current artifacts

## Restored Capabilities

### 1. Governance contract recovery

- shared governance contract support was restored in
  `src/invest_evolution/investment/shared/policy.py`
- governance engine integration was restored in
  `src/invest_evolution/investment/governance/engine.py`
- `regime_hard_fail`, proposal-gate policy, and strategy-family governance
  semantics are now available again in the active `v1.5` owner set

### 2. Proposal lifecycle recovery

- proposal ingress from review / research outputs was restored through
  `src/invest_evolution/application/training/review.py` and
  `src/invest_evolution/application/training/research.py`
- proposal bundle persistence and query/update support were restored in
  `src/invest_evolution/application/training/persistence.py`
- suggestion tracking and proposal effect evaluation were restored in
  `src/invest_evolution/application/training/observability.py`

### 3. Runtime discipline recovery

- runtime discipline window handling was restored in
  `src/invest_evolution/application/training/execution.py`
- the cycle mainline now explicitly opens a runtime mutation window at cycle
  start and finalizes it at cycle close
- in-cycle candidate construction from approved proposals was reattached to the
  review-to-selection handoff

### 4. Candidate build + contract projection recovery

- `build_cycle_candidate_from_proposals` is now wired back into the live cycle
  flow
- `review_contracts` and `run_context` can now recognize `candidate_build` and
  `candidate_build_skipped` events as part of the restored governance path,
  rather than only recognizing `runtime_config_mutation`

## Mainline Wiring Restored

The most important mainline reconnections in `execution.py` are:

- `begin_cycle_runtime_window`
- `build_cycle_candidate_from_proposals`
- `finalize_cycle_runtime_window`

These were the critical missing wires that made `v1.5` look structurally close
to `v1.0` on paper while still lacking the actual governance recovery behavior
at runtime.

## Owner Map

- `src/invest_evolution/investment/shared/policy.py`
- `src/invest_evolution/investment/governance/engine.py`
- `src/invest_evolution/application/training/persistence.py`
- `src/invest_evolution/application/training/observability.py`
- `src/invest_evolution/application/training/execution.py`
- `src/invest_evolution/application/training/review.py`
- `src/invest_evolution/application/training/research.py`
- `src/invest_evolution/application/training/review_contracts/__init__.py`

## Verification

Validated with focused governance and runtime suites:

- `python3 -m pytest tests/test_training_runtime_discipline.py tests/test_training_candidate_build.py tests/test_training_experiment_protocol.py tests/test_training_end_to_end_validation_flow.py -q`
- `python3 -m pytest tests/test_training_proposal_ingress.py tests/test_training_proposal_store.py tests/test_training_suggestion_tracking.py tests/test_governance_policy_recovery.py tests/test_governance_engine.py tests/test_leaderboard.py tests/test_training_persistence_boundary.py tests/test_research_feedback_gate.py tests/test_training_boundary_effects.py -q`
- `uv run ruff check src/invest_evolution/application/training/execution.py src/invest_evolution/application/training/review_contracts/__init__.py src/invest_evolution/application/training/observability.py tests/test_training_experiment_protocol.py tests/test_training_runtime_discipline.py tests/test_training_candidate_build.py`

Observed result:

- focused pytest suites passed: `101/101`
- focused `ruff` checks passed

## Relationship To Strict Readiness

This recovery removes a governance-integrity blocker in `v1.5`, but it does not
by itself mean the project is now strict-training ready.

As of 2026-03-24, the remaining strict-readiness blockers are still primarily
quality blockers:

- weak strategy quality under the latest evaluated strict-style run
- limited multi-regime evidence quality
- research feedback quality gates still failing under strict thresholds

See:

- `docs/GOVERNANCE_RECOVERY_BLUEPRINT_2026-03-24.md`
- `docs/STRICT_TRAINING_READINESS_CHECKLIST_2026-03-24.md`
