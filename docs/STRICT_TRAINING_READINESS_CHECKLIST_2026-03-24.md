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

- Offline market database is healthy and present at `data/stock_history.db`
- Latest offline data date is `2026-03-06`
- `ruff` passed
- `pyright` passed
- `scripts/run_verification_smoke.py` passed
- Architecture import failure has been fixed on 2026-03-24
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
- [ ] Re-run full project test suite after architecture fix
  - Command:
    ```bash
    uv run python -m pytest -q
    ```
- [ ] Re-run freeze gate quick after architecture fix
  - Command:
    ```bash
    uv run python -m invest_evolution.application.freeze_gate --mode quick
    ```

Exit criteria:

- Full pytest passes
- Freeze gate quick passes
- No new import-layer regression appears

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
- [ ] Confirm training run uses a fresh output directory when evaluating strict readiness
  - Avoid relying on historical in-place artifacts for sign-off

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

Suggested workflow:

- Run a larger multi-cutoff training window instead of a narrow 5-cycle sample
- Review whether cutoff sampling is overly concentrated in bull windows
- Verify governance allocation is not suppressing candidate diversity too early

### 2.3 Research Feedback Gate

- [ ] Raise `research_feedback.sample_count` from `0` to `>= 5`
- [ ] Make `research_feedback_gate.active = true`
- [ ] Make `research_feedback_gate.passed = true`
- [ ] Eliminate `insufficient_samples` as the gate reason

Suggested workflow:

- Confirm research cases and attributions keep accumulating after each training cycle
- Check whether the requested regime scope is too narrow and dropping usable evidence
- Verify horizon-level feedback is produced for at least `T+20`

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
