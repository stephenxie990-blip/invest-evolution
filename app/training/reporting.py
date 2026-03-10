from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import numpy as np


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


def should_freeze(cycle_history: list[Any], freeze_total_cycles: int, freeze_profit_required: int, freeze_gate_policy: dict[str, Any], rolling: dict[str, Any]) -> bool:
    if len(cycle_history) < freeze_total_cycles or not rolling:
        return False

    required_win_rate = freeze_profit_required / max(freeze_total_cycles, 1)
    min_avg_return = float(freeze_gate_policy.get("avg_return_gt", 0.0) or 0.0)
    min_avg_sharpe = float(freeze_gate_policy.get("avg_sharpe_gte", 0.8) or 0.8)
    max_avg_drawdown = float(freeze_gate_policy.get("avg_max_drawdown_lt", 15.0) or 15.0)
    min_benchmark_pass_rate = float(freeze_gate_policy.get("benchmark_pass_rate_gte", 0.60) or 0.60)
    return (
        rolling["win_rate"] >= required_win_rate
        and rolling["avg_return"] > min_avg_return
        and rolling["avg_sharpe"] >= min_avg_sharpe
        and rolling["avg_max_drawdown"] < max_avg_drawdown
        and rolling["benchmark_pass_rate"] >= min_benchmark_pass_rate
    )


def build_freeze_report(cycle_history: list[Any], current_params: dict[str, Any], freeze_total_cycles: int, freeze_profit_required: int, freeze_gate_policy: dict[str, Any], rolling: dict[str, Any]) -> dict[str, Any]:
    total = len(cycle_history)
    profits = sum(1 for r in cycle_history if r.is_profit)
    return {
        "frozen": True,
        "total_cycles": total,
        "total_profit_count": profits,
        "profit_rate": profits / total if total > 0 else 0,
        "recent_10_profit_count": sum(1 for r in cycle_history[-10:] if r.is_profit),
        "final_params": current_params,
        "frozen_time": datetime.now().isoformat(),
        "self_assessment": rolling,
        "freeze_gate": {
            "window": freeze_total_cycles,
            "required_win_rate": freeze_profit_required / max(freeze_total_cycles, 1),
            "required_avg_return": float(freeze_gate_policy.get("avg_return_gt", 0.0) or 0.0),
            "required_avg_sharpe": float(freeze_gate_policy.get("avg_sharpe_gte", 0.8) or 0.8),
            "required_avg_max_drawdown": float(freeze_gate_policy.get("avg_max_drawdown_lt", 15.0) or 15.0),
            "required_benchmark_pass_rate": float(freeze_gate_policy.get("benchmark_pass_rate_gte", 0.60) or 0.60),
        },
    }


def generate_training_report(total_cycle_attempts: int, skipped_cycle_count: int, cycle_history: list[Any], current_params: dict[str, Any], is_frozen: bool, self_assessment: dict[str, Any]) -> dict[str, Any]:
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
    }
