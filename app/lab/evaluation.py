from __future__ import annotations

from datetime import datetime
from typing import Any


def build_promotion_summary(*, plan: dict[str, Any], ok_results: list[dict[str, Any]], avg_return_pct: float | None, avg_strategy_score: float | None, benchmark_pass_rate: float, baseline_entries: list[dict[str, Any]]) -> dict[str, Any]:
    gate = dict((plan.get("optimization") or {}).get("promotion_gate") or {})
    model_scope = dict(plan.get("model_scope") or {})
    protocol = dict(plan.get("protocol") or {})
    holdout = protocol.get("holdout") if isinstance(protocol.get("holdout"), dict) else {}
    walk_forward = protocol.get("walk_forward") if isinstance(protocol.get("walk_forward"), dict) else {}
    candidate_model = "unknown"
    candidate_config = ""
    if ok_results:
        first = ok_results[0]
        candidate_model = str(first.get("model_name") or "unknown")
        candidate_config = str(first.get("config_name") or "")
    baseline_models = [str(x) for x in (model_scope.get("baseline_models") or []) if str(x).strip()]
    baseline_avg_return = round(sum(float(entry.get("avg_return_pct", 0.0) or 0.0) for entry in baseline_entries) / len(baseline_entries), 4) if baseline_entries else None
    baseline_avg_score = round(sum(float(entry.get("avg_strategy_score", 0.0) or 0.0) for entry in baseline_entries) / len(baseline_entries), 4) if baseline_entries else None
    checks: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, actual: Any, threshold: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), "actual": actual, "threshold": threshold})

    min_samples = int(gate.get("min_samples", 1) or 1)
    add_check("min_samples", len(ok_results) >= min_samples, len(ok_results), min_samples)
    if gate.get("min_avg_return_pct") is not None:
        threshold = float(gate.get("min_avg_return_pct") or 0.0)
        actual = avg_return_pct if avg_return_pct is not None else None
        add_check("min_avg_return_pct", actual is not None and actual >= threshold, actual, threshold)
    if gate.get("min_avg_strategy_score") is not None:
        threshold = float(gate.get("min_avg_strategy_score") or 0.0)
        actual = avg_strategy_score if avg_strategy_score is not None else None
        add_check("min_avg_strategy_score", actual is not None and actual >= threshold, actual, threshold)
    if gate.get("min_benchmark_pass_rate") is not None:
        threshold = float(gate.get("min_benchmark_pass_rate") or 0.0)
        add_check("min_benchmark_pass_rate", benchmark_pass_rate >= threshold, benchmark_pass_rate, threshold)
    if gate.get("min_return_advantage_vs_baseline") is not None and baseline_avg_return is not None and avg_return_pct is not None:
        threshold = float(gate.get("min_return_advantage_vs_baseline") or 0.0)
        actual = round(avg_return_pct - baseline_avg_return, 4)
        add_check("min_return_advantage_vs_baseline", actual >= threshold, actual, threshold)
    if gate.get("min_strategy_score_advantage_vs_baseline") is not None and baseline_avg_score is not None and avg_strategy_score is not None:
        threshold = float(gate.get("min_strategy_score_advantage_vs_baseline") or 0.0)
        actual = round(avg_strategy_score - baseline_avg_score, 4)
        add_check("min_strategy_score_advantage_vs_baseline", actual >= threshold, actual, threshold)
    passed = all(item.get("passed", False) for item in checks) if checks else False
    verdict = "promoted" if passed else "rejected"
    if not ok_results:
        verdict = "insufficient_data"
    return {
        "candidate": {"model_name": candidate_model, "config_name": candidate_config},
        "baselines": {
            "models": baseline_models,
            "avg_return_pct": baseline_avg_return,
            "avg_strategy_score": baseline_avg_score,
            "sample_count": len(baseline_entries),
        },
        "gate": gate,
        "checks": checks,
        "verdict": verdict,
        "passed": passed,
        "protocol": {"holdout": holdout, "walk_forward": walk_forward},
    }


def build_training_evaluation_summary(*, payload: dict[str, Any], plan: dict[str, Any], run_id: str, error: str, promotion: dict[str, Any], run_path: str, evaluation_path: str) -> dict[str, Any]:
    results = list(payload.get("results") or [])
    ok_results = [item for item in results if item.get("status") == "ok"]
    no_data_results = [item for item in results if item.get("status") == "no_data"]
    error_results = [item for item in results if item.get("status") == "error"]
    returns = [float(item.get("return_pct") or 0.0) for item in ok_results]
    strategy_scores = [float((item.get("strategy_scores") or {}).get("overall_score", 0.0) or 0.0) for item in ok_results]
    benchmark_passes = sum(1 for item in ok_results if bool(item.get("benchmark_passed", False)))
    avg_return_pct = round(sum(returns) / len(returns), 4) if returns else None
    avg_strategy_score = round(sum(strategy_scores) / len(strategy_scores), 4) if strategy_scores else None
    benchmark_pass_rate = round(benchmark_passes / len(ok_results), 4) if ok_results else 0.0
    return {
        "run_id": run_id,
        "plan_id": plan["plan_id"],
        "created_at": datetime.now().isoformat(),
        "status": str(payload.get("status", "ok")),
        "objective": dict(plan.get("objective") or {}),
        "spec": dict(plan.get("spec") or {}),
        "assessment": {
            "total_results": len(results),
            "success_count": len(ok_results),
            "no_data_count": len(no_data_results),
            "error_count": len(error_results),
            "avg_return_pct": avg_return_pct,
            "max_return_pct": round(max(returns), 4) if returns else None,
            "min_return_pct": round(min(returns), 4) if returns else None,
            "avg_strategy_score": avg_strategy_score,
            "benchmark_pass_rate": benchmark_pass_rate,
        },
        "promotion": promotion,
        "error": str(error or ""),
        "artifacts": {"run_path": run_path, "evaluation_path": evaluation_path},
    }


def build_training_memory_summary(*, payload: dict[str, Any], rounds: int, mock: bool, status: str, error: str = "") -> dict[str, Any]:
    results = list(payload.get("results") or [])
    ok_results = [item for item in results if item.get("status") == "ok"]
    skipped_results = [item for item in results if item.get("status") == "no_data"]
    error_results = [item for item in results if item.get("status") == "error"]
    cycle_ids = [item.get("cycle_id") for item in results if item.get("cycle_id") is not None]
    avg_return = round(sum(float(item.get("return_pct") or 0.0) for item in ok_results) / len(ok_results), 2) if ok_results else None
    requested_modes = sorted({str(item.get("requested_data_mode")) for item in results if item.get("requested_data_mode")})
    effective_modes = sorted({str(item.get("effective_data_mode") or item.get("data_mode")) for item in results if (item.get("effective_data_mode") or item.get("data_mode"))})
    llm_modes = sorted({str(item.get("llm_mode")) for item in results if item.get("llm_mode")})
    degraded_count = sum(1 for item in results if bool(item.get("degraded", False)))
    return {
        "status": status,
        "rounds": int(rounds),
        "mock": bool(mock),
        "cycle_ids": cycle_ids,
        "success_count": len(ok_results),
        "skipped_count": len(skipped_results),
        "error_count": len(error_results),
        "avg_return_pct": avg_return,
        "requested_data_modes": requested_modes,
        "effective_data_modes": effective_modes,
        "llm_modes": llm_modes,
        "degraded_count": degraded_count,
        "error": str(error or ""),
    }
