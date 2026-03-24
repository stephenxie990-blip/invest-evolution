# Strict Training Readiness Checklist

Date: 2026-03-24
Status: active

## Goal

This checklist turns the current "can run training" state into a concrete path toward
"strict training ready".

Use it when deciding whether the project is ready to enter strict training / strict
shadow-gate validation, rather than only basic real-data training.

## Current Snapshot

Latest evidence checked on 2026-03-24:

- Governance recovery pipeline was restored in `v1.5` on 2026-03-24:
  - change summary: `docs/GOVERNANCE_RECOVERY_CHANGE_SUMMARY_2026-03-24.md`
  - blueprint: `docs/GOVERNANCE_RECOVERY_BLUEPRINT_2026-03-24.md`
  - mainline reconnections now include runtime window open/close, proposal-bundle persistence, suggestion tracking, and candidate build from approved proposals
  - implication:
    - `v1.5` no longer lacks the core governance recovery wiring that existed in `v1.0`
    - remaining strict-readiness blockers should now be read mainly as quality / evidence blockers, not as missing governance-mainline capability
- Offline market database is healthy and present at `data/stock_history.db`
- Latest offline data date is `2026-03-06`
- `ruff` passed
- `pyright` passed
- `scripts/run_verification_smoke.py` passed
- Architecture import failure has been fixed on 2026-03-24
- Full `pytest` passed on 2026-03-24 after the current repo-truth alignment work
- Focused regression tests passed on 2026-03-24 for:
  - `tests/test_research_case_store.py`
  - `tests/test_training_boundary_adapters.py`
  - `tests/test_manager_execution_services.py`
- `freeze gate quick` passed on 2026-03-24
- Fresh-output real shadow/training run completed on 2026-03-24:
  - output dir: `outputs/strict_readiness_20260324_133840`
  - run report: `outputs/strict_readiness_20260324_133840/run_report.json`
  - successful cycles: `5/5`
  - profit cycles: `2`
  - loss cycles: `3`
  - profit rate: `0.4`
  - self assessment:
    - `avg_return = 0.2055`
    - `avg_sharpe = 0.1967`
    - `benchmark_pass_rate = 0.2`
  - research feedback still not actionable for requested bull regime:
    - `sample_count = 0`
    - `reason = requested_regime_unavailable`
    - `overall_sample_count = 200`
    - covered regimes: `bear`, `oscillation`
- Bull-regime research feedback scope mismatch root cause was fixed on 2026-03-24
  - fix: `ResearchCaseStore` now resolves regime scope from `snapshot.market_context.governance_context.regime` before falling back to `snapshot.market_context.regime`
  - why it mattered: persisted training-cycle cases stored `governance_context.regime = bull` while `market_context.regime` was still `oscillation`, so bull evidence existed but was being dropped from feedback grouping
- Fresh-output real shadow/training rerun completed on 2026-03-24 after the regime-scope fix:
  - output dir: `outputs/strict_readiness_20260324_2nd_regime_fix`
  - run report: `outputs/strict_readiness_20260324_2nd_regime_fix/run_report.json`
  - successful cycles: `5/5`
  - target met: `true`
  - requested bull-regime research feedback is now actionable:
    - `scope.effective_scope = regime`
    - `sample_count = 117`
    - `research_feedback_gate.active = true`
    - `research_feedback_gate.passed = false`
    - `recommendation.bias = tighten_risk`
    - covered regimes: `bear`, `bull`, `oscillation`
- Momentum bull-regime feedback contamination was fixed on 2026-03-24
  - fix 1: training research persistence now falls back to `manager_output.signal_packet.{manager_id,manager_config_ref}` when top-level manager identity is blank
  - fix 2: training feedback now excludes cases whose `snapshot.market_context.manager_id` / config does not match the requested manager subject
  - why it mattered: historical `momentum` bull feedback was polluted by `defensive_low_vol` snapshots stored under a `momentum` policy subject
  - evidence before fix:
    - `bull_rows = 160`
    - `snapshot_manager_counts = {'defensive_low_vol': 92, 'momentum': 68}`
  - evidence after fix:
    - recomputed `momentum + bull + as_of<=20180730` feedback now uses `sample_count = 68`
    - the remaining blocker is pure momentum bull quality, not missing or cross-manager polluted evidence
- Independent-observation de-duplication was added to training feedback on 2026-03-24
  - fix 1: `ResearchCaseStore.build_training_feedback()` now de-dupes repeated replays of the same market observation before summarizing evidence
  - de-dupe key: `(snapshot manager identity, snapshot manager config, resolved regime, symbol, as_of_date, stance)`
  - fix 2: requested-regime scope now uses the runtime-configured `research_feedback.min_sample_count` threshold instead of a hard-coded `3`
  - why it mattered: the remaining bull pool was still over-counting replayed `2018-01-31` observations as independent evidence
  - evidence before fix:
    - recomputed `momentum + bull + as_of<=20180730` feedback showed `sample_count = 68`
    - but those `68` rows collapsed to only `4` unique `(symbol, as_of_date)` observations
  - evidence after fix:
    - current runtime-state recompute now shows `sample_count = 4`
    - `overall_sample_count = 12`
    - `scope.effective_scope = regime_insufficient_samples`
    - `scope.actionable = false`
    - `recommendation.bias = insufficient_samples`
    - `research_feedback_gate.active = false`
    - `research_feedback_gate.reason = requested_regime_feedback_unavailable`
  - implication:
    - the bull blocker is now best understood as insufficient independent bull evidence, not a large validated bull sample that is uniformly bad
- Focused regression tests for the regime-scope fix passed on 2026-03-24
- Focused regression tests for the de-duplication and requested-regime scope fixes passed on 2026-03-24
- Focused regression tests for canonical `manager_config_ref` semantics passed on 2026-03-24:
  - `tests/test_training_boundary_adapters.py::test_runtime_manager_config_ref_prefers_runtime_config_path`
  - `tests/test_runtime_config_ref_semantics.py::test_runtime_process_emits_canonical_manager_config_ref_across_output_channels`
- Momentum runtime regression tests passed on 2026-03-24 after aligning their contract with the current threshold semantics:
  - `tests/test_v2_momentum_runtime.py`
- Ask-stock manager bridge regression tests passed on 2026-03-24:
  - `tests/test_ask_stock_manager_bridge.py`
- Research feedback gate regression tests passed on 2026-03-24 after aligning the inactive-policy test with the current `min_sample_count` gate:
  - `tests/test_research_feedback_gate.py`
- Fresh-output real shadow/training rerun completed on 2026-03-24 after report-truth sync fixes:
  - output dir: `outputs/strict_readiness_20260324_truth_sync_fix`
  - run report: `outputs/strict_readiness_20260324_truth_sync_fix/run_report.json`
  - successful cycles: `4/5`
  - profit cycles: `2`
  - loss cycles: `2`
  - profit rate: `0.5`
  - self assessment:
    - `avg_return = -1.0682`
    - `avg_sharpe = -0.5068`
    - `benchmark_pass_rate = 0.0`
  - run-level research feedback is now preserved correctly when the terminal attempt is skipped:
    - `research_feedback.sample_count = 39`
    - `research_feedback.scope.effective_scope = regime`
    - `research_feedback.recommendation.bias = tighten_risk`
    - `freeze_gate_evaluation.research_feedback_gate.active = true`
    - `freeze_gate_evaluation.research_feedback_gate.passed = false`
  - implication:
    - the previous `run_report.research_feedback = {}` / `sample_count = 0` state was a report-truth bug, not the latest business truth
- Single-manager subject semantics were realigned on 2026-03-24 for strict runs that intentionally clamp manager architecture:
  - fresh artifact evidence:
    - `outputs/strict_readiness_20260324_truth_sync_fix/cycle_1.json`
    - `selection_mode = single_manager`
    - `execution_snapshot.subject_type = single_manager`
    - `run_context.subject_type = single_manager`
    - `governance_decision.metadata.subject_type = single_manager`
  - implication:
    - the earlier `selection_mode = single_manager` + `run_context.subject_type = manager_portfolio` mismatch was a persistence truth bug, not the current repo truth
- Leaderboard aggregation pollution from non-cycle artifacts was reduced on 2026-03-24:
  - `details/cycle_*_trades.json` and `proposal_store/cycle_*_proposal_bundle_*.json` are now excluded from leaderboard collection
  - implication:
    - strict readiness decisions should no longer be distorted by `unknown::details` / `unknown::proposal_store` pseudo-managers
- `freeze gate quick` passed on 2026-03-24 after the regime-scope fix
- Latest evaluated real training run remains rejected:
  - evaluation artifact: `runtime/state/training_evals/run_20260322_220005_008556.json`
  - latest strict-style blocker values:
    - `avg_return_pct = -0.6142`
    - `median_return_pct = -0.0816`
    - `cumulative_return_pct = -3.071`
    - `win_rate = 0.4`
    - `benchmark_pass_rate = 0.0`
    - `loss_share = 0.6`
    - `research_feedback.sample_count = 0`
    - `regime_validation.dominant_regime_share = 0.8`
    - `regime_validation.bull.avg_return_pct = -1.0155`
    - `regime_validation.bull.win_rate = 0.25`
    - `regime_validation.bull.benchmark_pass_rate = 0.0`

## Phase 0: Engineering Baseline

- [x] Remove the investment-layer dependency on application-layer research feedback gate logic
  - Done on 2026-03-24 by extracting shared gate logic into `src/invest_evolution/investment/shared/research_feedback_gate.py`
- [x] Re-run architecture import rules
  - Command:
    ```bash
    uv run python -m pytest -q tests/test_architecture_import_rules.py
    ```
- [x] Re-run research feedback regression tests
  - Command:
    ```bash
    uv run python -m pytest -q tests/test_research_feedback_gate.py tests/test_research_case_store.py tests/test_research_training_feedback.py
    ```
- [x] Re-run focused lint on touched files
  - Command:
    ```bash
    uv run ruff check src/invest_evolution/investment/research/case_store.py src/invest_evolution/application/training/observability.py src/invest_evolution/investment/shared/research_feedback_gate.py
    ```
- [x] Re-run full project test suite after architecture fix
  - Command:
    ```bash
    uv run python -m pytest -q
    ```
  - Latest rerun on 2026-03-24 passed
- [x] Re-run freeze gate quick after architecture fix
  - Command:
    ```bash
    uv run python -m invest_evolution.application.freeze_gate --mode quick
    ```
  - Latest rerun on 2026-03-24 passed

Exit criteria:

- Full pytest passes
- Freeze gate quick passes
- No new import-layer regression appears

Additional verified checks on 2026-03-24:

- `uv run pyright`
- `uv run python -m pytest -q`
- `uv run python -m pytest -q tests/test_research_case_store.py tests/test_training_boundary_adapters.py tests/test_manager_execution_services.py`
- `uv run python -m pytest -q tests/test_runtime_config_ref_semantics.py`

## Phase 1: Real Training Environment Readiness

- [x] Confirm offline database exists and reports `healthy`
  - Command:
    ```bash
    uv run python -m invest_evolution.interfaces.cli.market_data --status --stocks 50
    ```
- [x] Confirm Commander can read runtime status without boot errors
  - Command:
    ```bash
    uv run python -m invest_evolution.interfaces.cli.commander status --detail fast
    ```
- [ ] Confirm real training environment has required provider credentials
  - Expected env vars:
    - `OPENAI_API_KEY`
    - `MINIMAX_API_KEY`
- [x] Confirm fresh-output real training run can complete with current environment
  - Command used:
    ```bash
    uv run python scripts/run_release_gate_stage1.py --output outputs/strict_readiness_20260324_133840 --cycles 5 --successful-cycles-target 5 --force-full-cycles
    ```
  - Observed result:
    - run completed
    - successful cycles = 5
    - current environment tolerated missing `MINIMAX_API_KEY` for this path
- [x] Confirm training run uses a fresh output directory when evaluating strict readiness
  - Avoid relying on historical in-place artifacts for sign-off
  - Verified with fresh rerun:
    - `outputs/strict_readiness_20260324_2nd_regime_fix`

Exit criteria:

- Real-data training can start without falling back to mock mode
- Strict validation is run on fresh artifacts, not reused historical directories

## Phase 2: Strategy Quality Gaps from Latest Evaluated Run

The latest real training evaluation on 2026-03-22 is still rejected. These are the
current strict blockers to fix before claiming strict training readiness.

### 2.1 Return Objectives

- [ ] Raise `avg_return_pct` from `-0.6142` to `>= 0.0`
- [ ] Raise `median_return_pct` from `-0.0816` to `>= 0.0`
- [ ] Raise `cumulative_return_pct` from `-3.071` to `>= 0.0`
- [ ] Raise `win_rate` from `0.4` to `>= 0.5`
- [ ] Raise `benchmark_pass_rate` from `0.0` to `>= 0.5`
- [ ] Lower `loss_share` from `0.6` to `<= 0.5`

Suggested workflow:

```bash
uv run python -m invest_evolution.interfaces.cli.commander train-once --rounds 5
```

Then inspect:

- `runtime/state/training_runs/*.json`
- `runtime/state/training_evals/*.json`
- `runtime/outputs/training/cycle_*.json`

### 2.2 Regime Validation

- [ ] Lower `dominant_regime_share` from `0.8` to `<= 0.75`
- [ ] Raise bull-regime `avg_return_pct` from `-1.0155` to `>= 0.0`
- [ ] Raise bull-regime `win_rate` from `0.25` to `>= 0.4`
- [ ] Raise bull-regime `benchmark_pass_rate` from `0.0` to `>= 0.4`
- [ ] Keep multi-regime coverage at `>= 2` distinct regimes

Latest fresh-artifact truth on 2026-03-24 after the report-truth sync fixes:

- `outputs/strict_readiness_20260324_truth_sync_fix/run_report.json`
- regime mix across successful cycles is still narrow:
  - `bear = 3`
  - `oscillation = 1`
  - `bull = 0`
- the oscillation manager cycle remains the largest quality drag:
  - `cycle_3.return_pct = -5.751`
  - `cycle_3.manager_id = mean_reversion`
- implication:
  - the regime blocker is now best understood as genuine strategy/regime coverage weakness, not a subject-identity reporting artifact

Suggested workflow:

- Run a larger multi-cutoff training window instead of a narrow 5-cycle sample
- Review whether cutoff sampling is overly concentrated in bull windows
- Verify governance allocation is not suppressing candidate diversity too early

### 2.3 Research Feedback Gate

- [x] Raise actionable `research_feedback.sample_count` from `0` to `>= 5` for the requested regime
- [x] Make `research_feedback_gate.active = true`
- [ ] Make `research_feedback_gate.passed = true`
- [x] Eliminate `requested_regime_unavailable` as the gate reason for bull-regime validation

Suggested workflow:

- Confirm research cases and attributions keep accumulating after each training cycle
- Check whether the requested regime scope is too narrow and dropping usable evidence
  - Root cause fixed on 2026-03-24: training feedback grouping now prefers governance regime over raw snapshot regime
- Check whether manager subject and snapshot manager identity are being mixed in the same feedback pool
  - Root cause fixed on 2026-03-24: training feedback now excludes cross-manager polluted samples, and persistence falls back to `signal_packet` identity when needed
- Check whether repeated replays of the same market observation are being over-counted as independent bull evidence
  - Root cause fixed on 2026-03-24: training feedback now de-dupes repeated `(manager identity, manager config, regime, symbol, as_of_date, stance)` observations before summarization
- Verify horizon-level feedback is produced for at least `T+20`
- Focus next on expanding independent bull evidence before reading too much into the current bull quality recommendation:
  - after de-duplication, the current runtime-state bull feedback now shows:
    - `sample_count = 4`
    - `scope.effective_scope = regime_insufficient_samples`
    - `recommendation.bias = insufficient_samples`
    - `T+20 hit_rate = 0.0`
    - `T+20 invalidation_rate = 1.0`
    - `T+20 interval_hit_rate = 0.5`
  - because the requested bull scope is now below the runtime-configured minimum sample threshold, Phase 2 should first increase independent bull coverage rather than treating the old `68` rows as decisive evidence of broad bull failure

Latest fresh-artifact truth on 2026-03-24 after the report-truth sync fixes:

- `outputs/strict_readiness_20260324_truth_sync_fix/run_report.json`
- run-level research feedback is no longer empty:
  - `sample_count = 39`
  - `scope.effective_scope = regime`
  - `freeze_gate_evaluation.research_feedback_gate.active = true`
- the remaining blocker is now a strict quality failure, not a missing-evidence failure:
  - `freeze_gate_evaluation.research_feedback_gate.passed = false`
  - `bias = tighten_risk`
  - failed checks include:
    - `blocked_biases = tighten_risk`
    - `T+5.hit_rate = 0.3077 < 0.34`
    - `T+5.interval_hit_rate = 0.5385 < 0.55`
- implication:
  - the current research gate blocker is now best understood as low-quality regime evidence under strict thresholds, not a final-report aggregation bug

Key files to inspect:

- `runtime/state/research_cases/`
- `runtime/state/research_attributions/`
- `runtime/state/training_evals/*.json`

## Phase 3: Strict Shadow-Gate Validation

After the engineering baseline and strategy blockers above are improved, run the
strict validation path defined by release readiness.

- [ ] Run strict probe on a fresh output directory
  - Command:
    ```bash
    uv run python scripts/run_release_readiness_gate.py \
      --include-shadow-gate \
      --shadow-profile strict \
      --shadow-cycles 8 \
      --shadow-successful-cycles-target 5 \
      --shadow-verify-successful-cycles-min 5 \
      --shadow-verify-validation-pass-count-min 1 \
      --shadow-verify-promote-count-min 0
    ```
- [ ] Run full strict shadow gate on a fresh output directory
  - Commands:
    ```bash
    uv run python scripts/run_release_gate_stage1.py --output <fresh-output-dir>
    uv run python -m invest_evolution.application.release shadow-gate --run-dir <fresh-output-dir> --profile strict
    ```

Strict pass criteria include:

- [ ] `successful_cycles >= 30`
- [ ] `unexpected_reject_count = 0`
- [ ] `governance_blocked_count = 0`
- [ ] `validation_pass_count >= 2`
- [ ] `promote_count >= 1`
- [ ] `candidate_missing_rate <= 0.50`
- [ ] `needs_more_optimization_rate <= 0.70`
- [ ] `artifact_completeness = 1.0`
- [ ] `run_report.freeze_gate_evaluation.research_feedback_gate.active = true`
- [ ] `run_report.freeze_gate_evaluation.research_feedback_gate.passed = true`

## Final Decision Rule

You may say the project is "strict training ready" only when all of the following are
true at the same time:

- Engineering baseline is green
- Real-data training runs are reproducible on fresh artifacts
- Latest training evaluation is no longer rejected on return / regime / research gates
- Strict shadow-gate thresholds pass on a fresh output directory

Until then, the correct label is:

"Training platform is operational and already in real-data experimentation, but not yet
strict-training ready."
