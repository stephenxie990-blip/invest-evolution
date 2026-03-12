from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import numpy as np


_DEFAULT_OPTIMIZATION_FEEDBACK_GATE = {
    "min_sample_count": 5,
    "blocked_biases": ["tighten_risk", "recalibrate_probability"],
    "max_brier_like_direction_score": 0.28,
    "horizons": {
        "default": {
            "min_hit_rate": 0.45,
            "max_invalidation_rate": 0.35,
            "min_interval_hit_rate": 0.40,
        }
    },
}

_DEFAULT_FREEZE_FEEDBACK_GATE = {
    "min_sample_count": 8,
    "blocked_biases": ["tighten_risk", "recalibrate_probability"],
    "max_brier_like_direction_score": 0.22,
    "horizons": {
        "default": {
            "min_hit_rate": 0.50,
            "max_invalidation_rate": 0.25,
            "min_interval_hit_rate": 0.45,
        }
    },
}


def _merge_policy(defaults: dict[str, Any], override: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(defaults or {})
    patch = dict(override or {})
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_policy(dict(merged.get(key) or {}), value)
        else:
            merged[key] = value
    return merged


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def build_self_assessment_snapshot(snapshot_factory: Callable[..., Any], cycle_result: Any, cycle_dict: dict[str, Any]) -> Any:
    return snapshot_factory(
        cycle_id=cycle_result.cycle_id,
        cutoff_date=cycle_result.cutoff_date,
        regime=cycle_dict.get("regime", "unknown"),
        plan_source=cycle_dict.get("plan_source", "unknown"),
        return_pct=cycle_result.return_pct,
        is_profit=cycle_result.is_profit,
        sharpe_ratio=float(cycle_dict.get("sharpe_ratio", 0.0) or 0.0),
        max_drawdown=float(cycle_dict.get("max_drawdown", 0.0) or 0.0),
        excess_return=float(cycle_dict.get("excess_return", 0.0) or 0.0),
        benchmark_passed=bool(cycle_dict.get("benchmark_passed", False)),
    )


def rolling_self_assessment(assessment_history: list[Any], freeze_total_cycles: int, window: int | None = None) -> dict[str, Any]:
    if not assessment_history:
        return {}

    w = max(1, window or freeze_total_cycles)
    recent = assessment_history[-w:]
    n = len(recent)
    profit_count = sum(1 for s in recent if s.is_profit)

    return {
        "window": n,
        "profit_count": profit_count,
        "win_rate": profit_count / n if n > 0 else 0.0,
        "avg_return": float(np.mean([s.return_pct for s in recent])) if recent else 0.0,
        "avg_sharpe": float(np.mean([s.sharpe_ratio for s in recent])) if recent else 0.0,
        "avg_max_drawdown": float(np.mean([s.max_drawdown for s in recent])) if recent else 0.0,
        "avg_excess_return": float(np.mean([s.excess_return for s in recent])) if recent else 0.0,
        "benchmark_pass_rate": (sum(1 for s in recent if s.benchmark_passed) / n if n > 0 else 0.0),
    }


def evaluate_research_feedback_gate(
    research_feedback: dict[str, Any] | None,
    policy: dict[str, Any] | None = None,
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(research_feedback or {})
    recommendation = dict(payload.get("recommendation") or {})
    bias = str(recommendation.get("bias") or "maintain")
    sample_count = int(payload.get("sample_count") or 0)
    config = _merge_policy(defaults or _DEFAULT_FREEZE_FEEDBACK_GATE, policy or {})
    checks: list[dict[str, Any]] = []

    min_sample_count = int(config.get("min_sample_count") or 0)
    sample_check = {
        "name": "min_sample_count",
        "passed": sample_count >= min_sample_count,
        "actual": sample_count,
        "required_gte": min_sample_count,
    }
    checks.append(sample_check)
    if sample_count < min_sample_count:
        return {
            "active": False,
            "passed": True,
            "reason": "insufficient_samples",
            "bias": bias,
            "sample_count": sample_count,
            "checks": checks,
            "failed_checks": [],
            "available_horizons": sorted((payload.get("horizons") or {}).keys()),
        }

    blocked_biases = [str(item).strip() for item in (config.get("blocked_biases") or []) if str(item).strip()]
    if blocked_biases:
        checks.append(
            {
                "name": "blocked_biases",
                "passed": bias not in blocked_biases,
                "actual": bias,
                "blocked": blocked_biases,
            }
        )

    max_brier = _safe_float(config.get("max_brier_like_direction_score"))
    brier = _safe_float(payload.get("brier_like_direction_score"))
    if max_brier is not None and brier is not None:
        checks.append(
            {
                "name": "max_brier_like_direction_score",
                "passed": brier <= max_brier,
                "actual": brier,
                "required_lte": max_brier,
            }
        )

    horizons = dict(payload.get("horizons") or {})
    horizon_defaults = dict((config.get("horizons") or {}).get("default") or {})
    for horizon_key in sorted(horizons.keys()):
        horizon_metrics = dict(horizons.get(horizon_key) or {})
        horizon_policy = _merge_policy(horizon_defaults, dict((config.get("horizons") or {}).get(horizon_key) or {}))
        for metric_name, threshold_key, comparator in (
            ("hit_rate", "min_hit_rate", "gte"),
            ("invalidation_rate", "max_invalidation_rate", "lte"),
            ("interval_hit_rate", "min_interval_hit_rate", "gte"),
        ):
            actual = _safe_float(horizon_metrics.get(metric_name))
            threshold = _safe_float(horizon_policy.get(threshold_key))
            if actual is None or threshold is None:
                continue
            passed = actual >= threshold if comparator == "gte" else actual <= threshold
            checks.append(
                {
                    "name": f"{horizon_key}.{metric_name}",
                    "horizon": horizon_key,
                    "metric": metric_name,
                    "passed": passed,
                    "actual": actual,
                    "required_gte" if comparator == "gte" else "required_lte": threshold,
                }
            )

    failed_checks = [item for item in checks if item.get("passed") is False and item.get("name") != "min_sample_count"]
    return {
        "active": True,
        "passed": not failed_checks,
        "bias": bias,
        "sample_count": sample_count,
        "checks": checks,
        "failed_checks": failed_checks,
        "available_horizons": sorted(horizons.keys()),
        "recommendation": recommendation,
    }


def evaluate_freeze_gate(
    cycle_history: list[Any],
    freeze_total_cycles: int,
    freeze_profit_required: int,
    freeze_gate_policy: dict[str, Any],
    rolling: dict[str, Any],
    research_feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if len(cycle_history) < freeze_total_cycles or not rolling:
        return {
            "ready": False,
            "passed": False,
            "checks": [],
            "research_feedback_gate": evaluate_research_feedback_gate(
                research_feedback,
                policy=dict((freeze_gate_policy or {}).get("research_feedback") or {}),
                defaults=_DEFAULT_FREEZE_FEEDBACK_GATE,
            ),
        }

    required_win_rate = freeze_profit_required / max(freeze_total_cycles, 1)
    min_avg_return = float(freeze_gate_policy.get("avg_return_gt", 0.0) or 0.0)
    min_avg_sharpe = float(freeze_gate_policy.get("avg_sharpe_gte", 0.8) or 0.8)
    max_avg_drawdown = float(freeze_gate_policy.get("avg_max_drawdown_lt", 15.0) or 15.0)
    min_benchmark_pass_rate = float(freeze_gate_policy.get("benchmark_pass_rate_gte", 0.60) or 0.60)

    checks = [
        {"name": "win_rate", "passed": rolling.get("win_rate", 0.0) >= required_win_rate, "actual": rolling.get("win_rate", 0.0), "required_gte": required_win_rate},
        {"name": "avg_return", "passed": rolling.get("avg_return", 0.0) > min_avg_return, "actual": rolling.get("avg_return", 0.0), "required_gt": min_avg_return},
        {"name": "avg_sharpe", "passed": rolling.get("avg_sharpe", 0.0) >= min_avg_sharpe, "actual": rolling.get("avg_sharpe", 0.0), "required_gte": min_avg_sharpe},
        {"name": "avg_max_drawdown", "passed": rolling.get("avg_max_drawdown", 0.0) < max_avg_drawdown, "actual": rolling.get("avg_max_drawdown", 0.0), "required_lt": max_avg_drawdown},
        {"name": "benchmark_pass_rate", "passed": rolling.get("benchmark_pass_rate", 0.0) >= min_benchmark_pass_rate, "actual": rolling.get("benchmark_pass_rate", 0.0), "required_gte": min_benchmark_pass_rate},
    ]
    research_gate = evaluate_research_feedback_gate(
        research_feedback,
        policy=dict((freeze_gate_policy or {}).get("research_feedback") or {}),
        defaults=_DEFAULT_FREEZE_FEEDBACK_GATE,
    )
    base_passed = all(check.get("passed") for check in checks)
    return {
        "ready": True,
        "passed": base_passed and bool(research_gate.get("passed", True)),
        "checks": checks,
        "research_feedback_gate": research_gate,
    }


def should_freeze(
    cycle_history: list[Any],
    freeze_total_cycles: int,
    freeze_profit_required: int,
    freeze_gate_policy: dict[str, Any],
    rolling: dict[str, Any],
    research_feedback: dict[str, Any] | None = None,
) -> bool:
    evaluation = evaluate_freeze_gate(
        cycle_history,
        freeze_total_cycles,
        freeze_profit_required,
        freeze_gate_policy,
        rolling,
        research_feedback=research_feedback,
    )
    return bool(evaluation.get("passed"))


def build_freeze_report(
    cycle_history: list[Any],
    current_params: dict[str, Any],
    freeze_total_cycles: int,
    freeze_profit_required: int,
    freeze_gate_policy: dict[str, Any],
    rolling: dict[str, Any],
    research_feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    total = len(cycle_history)
    profits = sum(1 for r in cycle_history if r.is_profit)
    evaluation = evaluate_freeze_gate(
        cycle_history,
        freeze_total_cycles,
        freeze_profit_required,
        freeze_gate_policy,
        rolling,
        research_feedback=research_feedback,
    )
    return {
        "frozen": True,
        "total_cycles": total,
        "total_profit_count": profits,
        "profit_rate": profits / total if total > 0 else 0,
        "recent_10_profit_count": sum(1 for r in cycle_history[-10:] if r.is_profit),
        "final_params": current_params,
        "frozen_time": datetime.now().isoformat(),
        "self_assessment": rolling,
        "research_feedback": dict(research_feedback or {}),
        "freeze_gate": {
            "window": freeze_total_cycles,
            "required_win_rate": freeze_profit_required / max(freeze_total_cycles, 1),
            "required_avg_return": float(freeze_gate_policy.get("avg_return_gt", 0.0) or 0.0),
            "required_avg_sharpe": float(freeze_gate_policy.get("avg_sharpe_gte", 0.8) or 0.8),
            "required_avg_max_drawdown": float(freeze_gate_policy.get("avg_max_drawdown_lt", 15.0) or 15.0),
            "required_benchmark_pass_rate": float(freeze_gate_policy.get("benchmark_pass_rate_gte", 0.60) or 0.60),
            "research_feedback": dict((freeze_gate_policy or {}).get("research_feedback") or {}),
        },
        "freeze_gate_evaluation": evaluation,
    }


def generate_training_report(
    total_cycle_attempts: int,
    skipped_cycle_count: int,
    cycle_history: list[Any],
    current_params: dict[str, Any],
    is_frozen: bool,
    self_assessment: dict[str, Any],
    research_feedback: dict[str, Any] | None = None,
    freeze_gate_evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attempted = max(total_cycle_attempts, len(cycle_history) + skipped_cycle_count)
    successful = len(cycle_history)
    skipped = max(skipped_cycle_count, attempted - successful)

    if not cycle_history:
        return {
            "status": "no_data",
            "total_cycles": attempted,
            "attempted_cycles": attempted,
            "successful_cycles": 0,
            "skipped_cycles": skipped,
            "profit_cycles": 0,
            "loss_cycles": 0,
            "profit_rate": 0,
            "current_params": current_params,
            "is_frozen": False,
            "self_assessment": self_assessment,
            "research_feedback": dict(research_feedback or {}),
            "freeze_gate_evaluation": dict(freeze_gate_evaluation or {}),
        }

    profits = sum(1 for r in cycle_history if r.is_profit)
    status = "completed_with_skips" if skipped else "completed"
    return {
        "status": status,
        "total_cycles": attempted,
        "attempted_cycles": attempted,
        "successful_cycles": successful,
        "skipped_cycles": skipped,
        "profit_cycles": profits,
        "loss_cycles": successful - profits,
        "profit_rate": profits / successful if successful > 0 else 0,
        "current_params": current_params,
        "is_frozen": is_frozen,
        "self_assessment": self_assessment,
        "research_feedback": dict(research_feedback or {}),
        "freeze_gate_evaluation": dict(freeze_gate_evaluation or {}),
    }
