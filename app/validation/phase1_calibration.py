from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
import random
from typing import Any

import numpy as np

from app.train import SelfLearningController
from app.training.experiment_protocol import build_standard_training_experiment_spec
from app.training.reporting import build_training_audit_semantics
from config import normalize_date
from invest.foundation.compute.features import compute_market_stats
from invest.models import resolve_model_config_path
from invest.shared.model_governance import evaluate_regime_hard_fail
from app.validation.phase0 import load_cutoff_dates_from_run


def _resolve_output_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _normalize_runtime_train_overrides(
    overrides: dict[str, Any] | None = None,
) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for key, value in dict(overrides or {}).items():
        if value is None or not str(value).strip():
            continue
        normalized[str(key)] = int(value)
    return normalized


def build_phase1_calibration_spec(
    *,
    model_name: str,
    seed: int,
    min_history_days: int,
    simulation_days: int,
    stock_count: int,
    dry_run_llm: bool = True,
    cutoff_dates: list[str] | None = None,
    cutoff_sampling_seeds: list[int | None] | None = None,
    cutoff_policy: dict[str, Any] | None = None,
    universe_policy: dict[str, Any] | None = None,
    runtime_train_overrides: dict[str, Any] | None = None,
    target_regime: str | None = None,
    target_regime_probe_count: int | None = None,
    target_regime_probe_mode: str = "model_cycle",
) -> dict[str, Any]:
    normalized_cutoff_dates = [
        normalize_date(str(item))
        for item in list(cutoff_dates or [])
        if str(item or "").strip()
    ]
    spec = build_standard_training_experiment_spec(
        seed=int(seed),
        min_history_days=int(min_history_days),
        simulation_days=int(simulation_days),
        stock_count=int(stock_count),
        cutoff_policy=cutoff_policy
        or (
            {
                "mode": "sequence",
                "dates": normalized_cutoff_dates,
                "sampling_seeds": [
                    int(item) if item is not None else None
                    for item in list(cutoff_sampling_seeds or [])
                ],
            }
            if normalized_cutoff_dates
            else {
                "mode": "regime_balanced",
                "probe_count": max(3, int(target_regime_probe_count or 9)),
                "probe_mode": str(target_regime_probe_mode or "model_cycle"),
                "target_regimes": [str(target_regime)] if str(target_regime or "").strip() else ["bull", "bear", "oscillation"],
                "fallback_mode": "random",
            }
        ),
        universe_policy=universe_policy or {"mode": "stratified_random", "stratify_by": "board"},
        allowed_models=[str(model_name)],
        dry_run_llm=bool(dry_run_llm),
    )
    spec["model_scope"]["experiment_mode"] = "standard"
    spec["model_scope"]["allocator_enabled"] = False
    spec["model_scope"]["model_routing_enabled"] = False
    normalized_runtime_overrides = _normalize_runtime_train_overrides(runtime_train_overrides)
    if normalized_runtime_overrides:
        spec["optimization"] = {
            "runtime_train_overrides": normalized_runtime_overrides,
        }
    return spec


def _cycle_regime(item: Any) -> str:
    if isinstance(item, dict):
        routing = dict(item.get("routing_decision") or {})
        audit_tags = dict(item.get("audit_tags") or {})
        regime = item.get("regime")
    else:
        routing = dict(getattr(item, "routing_decision", {}) or {})
        audit_tags = dict(getattr(item, "audit_tags", {}) or {})
        regime = getattr(item, "regime", "")
    return str(
        routing.get("regime")
        or audit_tags.get("routing_regime")
        or regime
        or "unknown"
    ).strip() or "unknown"


def summarize_regime_performance(cycle_history: list[Any] | None = None) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in list(cycle_history or []):
        regime = _cycle_regime(item)
        bucket = grouped.setdefault(
            regime,
            {
                "cycles": 0,
                "profit_cycles": 0,
                "benchmark_pass_cycles": 0,
                "return_sum": 0.0,
                "negative_contribution_pct": 0.0,
            },
        )
        return_pct = float(
            (item.get("return_pct") if isinstance(item, dict) else getattr(item, "return_pct", 0.0))
            or 0.0
        )
        is_profit = bool(
            item.get("is_profit") if isinstance(item, dict) else getattr(item, "is_profit", False)
        )
        benchmark_passed = bool(
            item.get("benchmark_passed")
            if isinstance(item, dict)
            else getattr(item, "benchmark_passed", False)
        )
        bucket["cycles"] += 1
        bucket["return_sum"] += return_pct
        bucket["negative_contribution_pct"] += min(return_pct, 0.0)
        if is_profit:
            bucket["profit_cycles"] += 1
        if benchmark_passed:
            bucket["benchmark_pass_cycles"] += 1

    performance: dict[str, dict[str, Any]] = {}
    for regime, bucket in grouped.items():
        cycles = int(bucket.get("cycles") or 0)
        if cycles <= 0:
            continue
        profit_cycles = int(bucket.get("profit_cycles") or 0)
        benchmark_pass_cycles = int(bucket.get("benchmark_pass_cycles") or 0)
        performance[regime] = {
            "cycles": cycles,
            "profit_cycles": profit_cycles,
            "loss_cycles": cycles - profit_cycles,
            "avg_return_pct": float(bucket.get("return_sum") or 0.0) / cycles,
            "win_rate": profit_cycles / cycles,
            "benchmark_pass_rate": benchmark_pass_cycles / cycles,
            "negative_contribution_pct": float(bucket.get("negative_contribution_pct") or 0.0),
        }
    return performance


def build_phase1_calibration_summary(
    *,
    model_name: str,
    output_dir: str | Path,
    experiment_spec: dict[str, Any],
    report: dict[str, Any] | None,
    cycle_history: list[Any] | None,
    llm_mode: str,
    regime_hard_fail_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cycles = list(cycle_history or [])
    regime_performance = summarize_regime_performance(cycles)
    resolved_policy = dict(regime_hard_fail_policy or {})
    resolved_report = dict(report or {})
    regime_discipline_dashboard = dict(
        resolved_report.get("regime_discipline_dashboard") or {}
    )
    suggestion_adoption_summary = dict(
        resolved_report.get("suggestion_adoption_summary") or {}
    )
    regime_hard_fail_evaluation = evaluate_regime_hard_fail(
        regime_performance,
        policy=resolved_policy,
    )
    cycle_briefs = []
    for item in cycles:
        cycle_id = int(item.get("cycle_id") if isinstance(item, dict) else getattr(item, "cycle_id", 0) or 0)
        cutoff_date = str(
            item.get("cutoff_date") if isinstance(item, dict) else getattr(item, "cutoff_date", "")
        )
        cycle_briefs.append(
            {
                "cycle_id": cycle_id,
                "cutoff_date": normalize_date(cutoff_date) if cutoff_date else "",
                "regime": _cycle_regime(item),
                "return_pct": float(
                    (item.get("return_pct") if isinstance(item, dict) else getattr(item, "return_pct", 0.0))
                    or 0.0
                ),
                "benchmark_passed": bool(
                    item.get("benchmark_passed")
                    if isinstance(item, dict)
                    else getattr(item, "benchmark_passed", False)
                ),
            }
        )

    return {
        "schema_version": "phase1.threshold_calibration_summary.v1",
        "terminology_version": str(
            build_training_audit_semantics().get("terminology_version") or ""
        ),
        "generated_at": datetime.now().isoformat(),
        "model_name": str(model_name),
        "llm_mode": str(llm_mode or "dry_run"),
        "output_dir": str(_resolve_output_path(output_dir)),
        "experiment_spec": dict(experiment_spec or {}),
        "report": resolved_report,
        "completed_cycle_count": len(cycles),
        "overlay_applied_cycles": int(
            regime_discipline_dashboard.get("overlay_applied_cycles") or 0
        ),
        "hard_filter_cycles": int(
            regime_discipline_dashboard.get("hard_filter_cycles") or 0
        ),
        "budget_correction_applied_cycles": int(
            regime_discipline_dashboard.get("budget_correction_applied_cycles") or 0
        ),
        "strategy_families": list(
            regime_discipline_dashboard.get("strategy_families") or []
        ),
        "top_budget_correction_signatures": list(
            regime_discipline_dashboard.get("top_repeated_budget_correction_signatures") or []
        ),
        "suggestion_count": int(
            suggestion_adoption_summary.get("suggestion_count") or 0
        ),
        "adopted_suggestion_count": int(
            suggestion_adoption_summary.get("adopted_suggestion_count") or 0
        ),
        "pending_effect_count": int(
            suggestion_adoption_summary.get("pending_effect_count") or 0
        ),
        "completed_effect_count": int(
            suggestion_adoption_summary.get("completed_evaluation_count") or 0
        ),
        "improved_suggestion_count": int(
            suggestion_adoption_summary.get("improved_suggestion_count") or 0
        ),
        "worsened_suggestion_count": int(
            suggestion_adoption_summary.get("worsened_suggestion_count") or 0
        ),
        "neutral_suggestion_count": int(
            suggestion_adoption_summary.get("neutral_suggestion_count") or 0
        ),
        "inconclusive_suggestion_count": int(
            suggestion_adoption_summary.get("inconclusive_suggestion_count") or 0
        ),
        "pending_effect_suggestions": list(
            suggestion_adoption_summary.get("pending_effect_suggestions") or []
        ),
        "evaluated_effect_suggestions": list(
            suggestion_adoption_summary.get("evaluated_effect_suggestions") or []
        ),
        "regime_performance": regime_performance,
        "regime_hard_fail_policy": resolved_policy,
        "regime_hard_fail_evaluation": regime_hard_fail_evaluation,
        "cycles": cycle_briefs,
    }


def run_phase1_threshold_calibration(
    *,
    model_name: str,
    output_dir: str | Path,
    cycles: int = 10,
    seed: int = 7,
    min_history_days: int = 200,
    simulation_days: int = 30,
    stock_count: int = 50,
    dry_run_llm: bool = True,
    cutoff_dates: list[str] | None = None,
    runtime_train_overrides: dict[str, Any] | None = None,
    target_regime: str | None = None,
    target_regime_probe_count: int = 36,
    target_regime_probe_mode: str = "model_cycle",
) -> dict[str, Any]:
    run_dir = _resolve_output_path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    controller = SelfLearningController(
        output_dir=str(run_dir),
        meeting_log_dir=str(run_dir / "meetings"),
        config_audit_log_path=str(run_dir / "config_audit.jsonl"),
        config_snapshot_dir=str(run_dir / "snapshots"),
    )
    controller.stop_on_freeze = False
    controller.model_name = str(model_name)
    controller.model_config_path = str(resolve_model_config_path(model_name))
    controller.current_params = {}
    controller.training_routing_service.reload_investment_model(
        controller,
        controller.model_config_path,
    )
    if dry_run_llm:
        controller.set_llm_dry_run(True)
    regime_hard_fail_policy = dict(
        dict(getattr(controller, "quality_gate_matrix", {}) or {})
        .get("routing", {})
        .get("regime_hard_fail", {})
        or {}
    )
    target_cutoff_dates: list[str] = []
    target_sampling_seeds: list[int | None] = []
    if not list(cutoff_dates or []) and str(target_regime or "").strip():
        target_samples = _probe_target_regime_samples(
            controller,
            target_regime=str(target_regime),
            cycles=int(cycles),
            seed=int(seed),
            min_history_days=int(min_history_days),
            stock_count=int(stock_count),
            probe_count=max(int(target_regime_probe_count or 0), int(cycles) * 8),
            allowed_models=[str(model_name)],
            probe_mode=str(target_regime_probe_mode or "model_cycle"),
            universe_policy={"mode": "stratified_random", "stratify_by": "board"},
        )
        target_cutoff_dates = [str(item.get("cutoff_date") or "") for item in target_samples]
        target_sampling_seeds = [
            int(item.get("sampling_seed")) if item.get("sampling_seed") is not None else None
            for item in target_samples
        ]
    spec = build_phase1_calibration_spec(
        model_name=model_name,
        seed=int(seed),
        min_history_days=int(min_history_days),
        simulation_days=int(simulation_days),
        stock_count=int(stock_count),
        dry_run_llm=bool(dry_run_llm),
        cutoff_dates=target_cutoff_dates or cutoff_dates,
        cutoff_sampling_seeds=target_sampling_seeds or None,
        runtime_train_overrides=runtime_train_overrides,
        target_regime=target_regime,
        target_regime_probe_count=target_regime_probe_count,
        target_regime_probe_mode=target_regime_probe_mode,
    )
    controller.configure_experiment(spec)
    report = controller.run_continuous(max_cycles=int(cycles))
    summary = build_phase1_calibration_summary(
        model_name=model_name,
        output_dir=run_dir,
        experiment_spec=spec,
        report=report,
        cycle_history=list(controller.cycle_history or []),
        llm_mode=str(getattr(controller, "llm_mode", "dry_run") or "dry_run"),
        regime_hard_fail_policy=regime_hard_fail_policy,
    )
    path = run_dir / "phase1_threshold_calibration_summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["phase1_threshold_calibration_summary_path"] = str(path)
    return summary


def _parse_runtime_override_args(items: list[str] | None = None) -> dict[str, int]:
    overrides: dict[str, int] = {}
    for item in list(items or []):
        text = str(item or "").strip()
        if not text:
            continue
        if "=" not in text:
            raise ValueError(f"Invalid runtime override: {text}")
        key, raw_value = text.split("=", 1)
        key = str(key).strip()
        raw_value = str(raw_value).strip()
        if not key or not raw_value:
            raise ValueError(f"Invalid runtime override: {text}")
        overrides[key] = int(raw_value)
    return overrides


def _probe_target_regime_cutoff_dates(
    controller: SelfLearningController,
    *,
    target_regime: str,
    cycles: int,
    seed: int,
    min_history_days: int,
    stock_count: int,
    probe_count: int,
    allowed_models: list[str] | None = None,
    probe_mode: str = "model_cycle",
    universe_policy: dict[str, Any] | None = None,
) -> list[str]:
    return [
        str(item.get("cutoff_date") or "")
        for item in _probe_target_regime_samples(
            controller,
            target_regime=target_regime,
            cycles=cycles,
            seed=seed,
            min_history_days=min_history_days,
            stock_count=stock_count,
            probe_count=probe_count,
            allowed_models=allowed_models,
            probe_mode=probe_mode,
            universe_policy=universe_policy,
        )
    ]


def _probe_target_regime_samples(
    controller: SelfLearningController,
    *,
    target_regime: str,
    cycles: int,
    seed: int,
    min_history_days: int,
    stock_count: int,
    probe_count: int,
    allowed_models: list[str] | None = None,
    probe_mode: str = "model_cycle",
    universe_policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    normalized_target = str(target_regime or "").strip().lower()
    if normalized_target not in {"bull", "bear", "oscillation"}:
        return []
    resolved_probe_mode = str(probe_mode or "model_cycle").strip().lower() or "model_cycle"
    min_probe_evaluations = min(
        max(int(probe_count or 0), int(cycles) * 4),
        max(int(cycles) * 2, 12),
    )

    matched: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for index in range(max(int(probe_count or 0), int(cycles) * 4)):
        sample_seed = int(seed) + index + 1
        random.seed(sample_seed)
        np.random.seed(sample_seed % (2**32 - 1))
        sample_date = normalize_date(controller.data_manager.random_cutoff_date())
        if sample_date in seen_dates:
            continue
        seen_dates.add(sample_date)
        if resolved_probe_mode == "routing_regime":
            preview = controller.training_routing_service.preview_routing(
                controller,
                cutoff_date=sample_date,
                stock_count=int(stock_count),
                min_history_days=int(min_history_days),
                allowed_models=list(allowed_models or []),
                sampling_policy=dict(universe_policy or {}),
                sampling_seed=sample_seed,
                regime_only=True,
            )
        else:
            preview = _preview_model_cycle_regime(
                controller,
                cutoff_date=sample_date,
                stock_count=int(stock_count),
                min_history_days=int(min_history_days),
                sampling_policy=dict(universe_policy or {}),
                sampling_seed=sample_seed,
            )
        if str(preview.get("regime") or "").strip().lower() != normalized_target:
            continue
        matched.append(
            {
                "cutoff_date": sample_date,
                "confidence": float(preview.get("regime_confidence") or preview.get("confidence") or 0.0),
                "probe_mode": resolved_probe_mode,
                "sampling_seed": sample_seed,
            }
        )
        if len(matched) >= int(cycles) and (index + 1) >= int(min_probe_evaluations):
            break

    ranked = sorted(
        matched,
        key=lambda item: (-float(item.get("confidence") or 0.0), str(item.get("cutoff_date") or "")),
    )
    resolved_samples = [dict(item) for item in ranked[: int(cycles)]]
    if len(resolved_samples) < int(cycles):
        raise RuntimeError(
            f"Unable to pre-probe enough cutoff dates for target regime '{normalized_target}': "
            f"required={int(cycles)}, found={len(resolved_samples)}, probe_count={int(probe_count)}, "
            f"probe_mode={resolved_probe_mode}"
        )
    return resolved_samples


def _preview_model_cycle_regime(
    controller: SelfLearningController,
    *,
    cutoff_date: str,
    stock_count: int,
    min_history_days: int,
    sampling_policy: dict[str, Any] | None = None,
    sampling_seed: int | None = None,
) -> dict[str, Any]:
    stock_data = controller.data_manager.load_stock_data(
        cutoff_date=cutoff_date,
        stock_count=max(1, int(stock_count)),
        min_history_days=max(30, int(min_history_days)),
        sampling_policy=dict(sampling_policy or {}) or None,
        sampling_seed=sampling_seed,
    )
    model = getattr(controller, "investment_model", None)
    regime_policy = {}
    config_section = getattr(model, "config_section", None)
    if callable(config_section):
        try:
            regime_policy = dict(config_section("market_regime", {}) or {})
        except Exception:
            regime_policy = {}
    market_stats = compute_market_stats(
        stock_data,
        cutoff_date,
        regime_policy=regime_policy or None,
    )
    resolver = getattr(model, "_resolve_regime", None)
    if callable(resolver):
        resolved_regime = str(resolver(dict(market_stats or {})) or "unknown").strip() or "unknown"
    else:
        resolved_regime = str(market_stats.get("regime_hint") or "unknown").strip() or "unknown"
    confidence = 0.65 if resolved_regime == "oscillation" else 0.75
    return {
        "cutoff_date": cutoff_date,
        "regime": resolved_regime,
        "regime_confidence": confidence,
        "confidence": confidence,
        "probe_source": "model_cycle_regime",
        "market_stats": market_stats,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a standard-mode pinned rerun for Phase 1 threshold calibration",
    )
    parser.add_argument("--model", required=True, help="Pinned model name")
    parser.add_argument("--output-dir", required=True, help="Calibration output directory")
    parser.add_argument("--cycles", type=int, default=10, help="Calibration cycle count")
    parser.add_argument("--seed", type=int, default=7, help="Reproducible standard-path seed")
    parser.add_argument("--min-history-days", type=int, default=200, help="Minimum history days")
    parser.add_argument("--simulation-days", type=int, default=30, help="Simulation days")
    parser.add_argument("--stock-count", type=int, default=50, help="Universe sample size")
    parser.add_argument("--target-regime", choices=("bull", "bear", "oscillation"), default=None, help="Pre-probe and pin cutoffs for a specific regime")
    parser.add_argument("--target-regime-probe-count", type=int, default=36, help="Probe count used to pre-select cutoff dates for target regime calibration")
    parser.add_argument("--target-regime-probe-mode", choices=("model_cycle", "routing_regime"), default="model_cycle", help="Which regime semantics to use during target regime probing")
    parser.add_argument("--cutoff-source-run", default=None, help="Load sequence cutoff dates from an existing run")
    parser.add_argument("--cutoff-date", action="append", default=None, help="Explicit cutoff date (repeatable)")
    parser.add_argument(
        "--runtime-train-override",
        action="append",
        default=None,
        help="Runtime train override in key=value form, e.g. max_losses_before_optimize=1",
    )
    parser.add_argument(
        "--llm-mode",
        choices=("dry_run", "live"),
        default="dry_run",
        help="LLM mode for calibration run",
    )
    args = parser.parse_args()
    cutoff_dates = [
        normalize_date(str(item))
        for item in list(args.cutoff_date or [])
        if str(item or "").strip()
    ]
    if not cutoff_dates and args.cutoff_source_run:
        cutoff_dates = load_cutoff_dates_from_run(args.cutoff_source_run)
    summary = run_phase1_threshold_calibration(
        model_name=str(args.model),
        output_dir=args.output_dir,
        cycles=int(args.cycles),
        seed=int(args.seed),
        min_history_days=int(args.min_history_days),
        simulation_days=int(args.simulation_days),
        stock_count=int(args.stock_count),
        dry_run_llm=str(args.llm_mode) == "dry_run",
        cutoff_dates=cutoff_dates,
        runtime_train_overrides=_parse_runtime_override_args(args.runtime_train_override),
        target_regime=args.target_regime,
        target_regime_probe_count=int(args.target_regime_probe_count),
        target_regime_probe_mode=str(args.target_regime_probe_mode or "model_cycle"),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
