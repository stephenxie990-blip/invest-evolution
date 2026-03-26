from .phase0 import (
    aggregate_cycle_metrics,
    build_bare_trading_plan,
    build_calibration_experiment_spec,
    build_trade_trace_records,
    compare_validation_runs,
    load_controller_run_summary,
    load_cutoff_dates_from_run,
    run_bare_validation,
    run_controller_calibration,
)
from .prephase1 import (
    build_candidate_resolution_summary,
    build_prephase1_validation_spec,
    extract_latest_candidate,
    extract_latest_proposal_gate,
    persist_candidate_resolution_summary,
    persist_validation_summary,
    resolve_validation_cutoff_dates,
    run_candidate_resolution_validation,
    run_prephase1_validation,
)

__all__ = [
    "aggregate_cycle_metrics",
    "build_candidate_resolution_summary",
    "build_phase1_calibration_spec",
    "build_phase1_calibration_summary",
    "build_bare_trading_plan",
    "build_calibration_experiment_spec",
    "build_prephase1_validation_spec",
    "build_trade_trace_records",
    "compare_validation_runs",
    "extract_latest_candidate",
    "extract_latest_proposal_gate",
    "load_controller_run_summary",
    "load_cutoff_dates_from_run",
    "persist_candidate_resolution_summary",
    "persist_validation_summary",
    "resolve_validation_cutoff_dates",
    "run_bare_validation",
    "run_controller_calibration",
    "run_candidate_resolution_validation",
    "run_phase1_threshold_calibration",
    "run_prephase1_validation",
    "summarize_regime_performance",
]

_PHASE1_EXPORTS = {
    "build_phase1_calibration_spec",
    "build_phase1_calibration_summary",
    "run_phase1_threshold_calibration",
    "summarize_regime_performance",
}


def __getattr__(name: str):
    if name in _PHASE1_EXPORTS:
        from . import phase1_calibration

        return getattr(phase1_calibration, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
