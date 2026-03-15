from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

from app.training.controller_services import TrainingFeedbackService
from app.training.reporting import build_governance_metrics, build_realism_summary
from app.training.reporting import evaluate_research_feedback_gate


def _feedback_sort_key(item: dict[str, Any]) -> tuple[str, int]:
    cutoff = str(item.get("cutoff_date") or "")
    try:
        cycle_id = int(item.get("cycle_id") or 0)
    except (TypeError, ValueError):
        cycle_id = 0
    return cutoff, cycle_id


def _latest_research_feedback(ok_results: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    latest_feedback: dict[str, Any] = {}
    latest_source: dict[str, Any] = {}
    latest_key = ("", 0)
    for item in ok_results:
        feedback = dict(item.get("research_feedback") or {})
        if not feedback:
            continue
        key = _feedback_sort_key(item)
        if key >= latest_key:
            latest_key = key
            latest_feedback = feedback
            latest_source = {
                "cycle_id": item.get("cycle_id"),
                "cutoff_date": item.get("cutoff_date"),
                "model_name": item.get("model_name"),
                "config_name": item.get("config_name"),
            }
    return latest_feedback, latest_source


def _latest_ab_comparison(ok_results: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    latest_comparison: dict[str, Any] = {}
    latest_source: dict[str, Any] = {}
    latest_key = ("", 0)
    for item in ok_results:
        comparison = dict(item.get("ab_comparison") or {})
        if not comparison:
            continue
        key = _feedback_sort_key(item)
        if key >= latest_key:
            latest_key = key
            latest_comparison = comparison
            latest_source = {
                "cycle_id": item.get("cycle_id"),
                "cutoff_date": item.get("cutoff_date"),
                "model_name": item.get("model_name"),
                "config_name": item.get("config_name"),
            }
    return latest_comparison, latest_source


def _research_feedback_brief(feedback: dict[str, Any], *, source: dict[str, Any] | None = None) -> dict[str, Any]:
    return TrainingFeedbackService.research_feedback_summary(feedback, source=source)


def _result_regime(item: dict[str, Any]) -> str:
    routing = dict(item.get("routing_decision") or {})
    audit_tags = dict(item.get("audit_tags") or {})
    self_assessment = dict(item.get("self_assessment") or {})
    return str(
        routing.get("regime")
        or audit_tags.get("routing_regime")
        or self_assessment.get("regime")
        or "unknown"
    ).strip() or "unknown"


def build_return_profile(ok_results: list[dict[str, Any]], *, benchmark_pass_rate: float) -> dict[str, Any]:
    returns = [float(item.get("return_pct") or 0.0) for item in ok_results]
    if not returns:
        return {
            "sample_count": 0,
            "avg_return_pct": None,
            "median_return_pct": None,
            "cumulative_return_pct": None,
            "win_rate": None,
            "benchmark_pass_rate": benchmark_pass_rate,
            "positive_return_count": 0,
            "negative_return_count": 0,
            "flat_return_count": 0,
            "loss_share": None,
            "avg_gain_pct": None,
            "avg_loss_pct": None,
            "gain_loss_ratio": None,
            "max_return_pct": None,
            "min_return_pct": None,
        }

    positives = [value for value in returns if value > 0]
    negatives = [value for value in returns if value < 0]
    flat_count = sum(1 for value in returns if value == 0)
    avg_gain = round(sum(positives) / len(positives), 4) if positives else None
    avg_loss = round(sum(negatives) / len(negatives), 4) if negatives else None
    gain_loss_ratio = None
    if avg_gain is not None and avg_loss is not None and avg_loss != 0:
        gain_loss_ratio = round(avg_gain / abs(avg_loss), 4)
    return {
        "sample_count": len(returns),
        "avg_return_pct": round(sum(returns) / len(returns), 4),
        "median_return_pct": round(median(returns), 4),
        "cumulative_return_pct": round(sum(returns), 4),
        "win_rate": round(len(positives) / len(returns), 4),
        "benchmark_pass_rate": benchmark_pass_rate,
        "positive_return_count": len(positives),
        "negative_return_count": len(negatives),
        "flat_return_count": flat_count,
        "loss_share": round(len(negatives) / len(returns), 4),
        "avg_gain_pct": avg_gain,
        "avg_loss_pct": avg_loss,
        "gain_loss_ratio": gain_loss_ratio,
        "max_return_pct": round(max(returns), 4),
        "min_return_pct": round(min(returns), 4),
    }


def build_regime_validation_summary(ok_results: list[dict[str, Any]]) -> dict[str, Any]:
    if not ok_results:
        return {
            "sample_count": 0,
            "distinct_regime_count": 0,
            "dominant_regime": "",
            "dominant_regime_share": None,
            "regimes": {},
        }

    regime_groups: dict[str, list[dict[str, Any]]] = {}
    for item in ok_results:
        regime = _result_regime(item)
        regime_groups.setdefault(regime, []).append(item)

    total = len(ok_results)
    dominant_regime = max(regime_groups.items(), key=lambda pair: len(pair[1]))[0] if regime_groups else ""
    regimes: dict[str, dict[str, Any]] = {}
    for regime, items in sorted(regime_groups.items()):
        returns = [float(item.get("return_pct") or 0.0) for item in items]
        benchmark_hits = sum(1 for item in items if bool(item.get("benchmark_passed", False)))
        strategy_scores = [
            float((item.get("strategy_scores") or {}).get("overall_score", 0.0) or 0.0)
            for item in items
        ]
        win_count = sum(1 for value in returns if value > 0)
        regimes[regime] = {
            "sample_count": len(items),
            "share": round(len(items) / total, 4),
            "avg_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
            "median_return_pct": round(median(returns), 4) if returns else None,
            "cumulative_return_pct": round(sum(returns), 4) if returns else None,
            "win_rate": round(win_count / len(items), 4) if items else None,
            "benchmark_pass_rate": round(benchmark_hits / len(items), 4) if items else None,
            "avg_strategy_score": round(sum(strategy_scores) / len(strategy_scores), 4) if strategy_scores else None,
            "max_return_pct": round(max(returns), 4) if returns else None,
            "min_return_pct": round(min(returns), 4) if returns else None,
        }
    return {
        "sample_count": total,
        "distinct_regime_count": len(regimes),
        "dominant_regime": dominant_regime,
        "dominant_regime_share": round(len(regime_groups.get(dominant_regime, [])) / total, 4) if total else None,
        "regimes": regimes,
    }


def _extend_gate_checks(
    checks: list[dict[str, Any]],
    prefix: str,
    gate_checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in gate_checks:
        normalized_item = {
            "name": f"{prefix}.{item.get('name')}",
            "passed": bool(item.get("passed", False)),
            "actual": item.get("actual"),
            "threshold": item.get("threshold"),
            "meta": {k: v for k, v in item.items() if k not in {"name", "passed", "actual", "threshold"}},
        }
        checks.append(normalized_item)
        normalized.append(normalized_item)
    return normalized


def evaluate_return_objectives(
    return_profile: dict[str, Any],
    *,
    policy: dict[str, Any] | None,
    baseline_avg_return: float | None = None,
) -> dict[str, Any]:
    config = dict(policy or {})
    if not config:
        return {"enabled": False, "passed": True, "checks": [], "failed_checks": [], "profile": return_profile}

    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, actual: Any, threshold: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), "actual": actual, "threshold": threshold})

    for key in ("min_avg_return_pct", "min_median_return_pct", "min_cumulative_return_pct", "min_win_rate", "min_benchmark_pass_rate"):
        if config.get(key) is None:
            continue
        metric_key = key.removeprefix("min_")
        actual = return_profile.get(metric_key)
        threshold = float(config.get(key) or 0.0)
        add(key, actual is not None and float(actual) >= threshold, actual, threshold)
    if config.get("max_loss_share") is not None:
        actual = return_profile.get("loss_share")
        threshold = float(config.get("max_loss_share") or 0.0)
        add("max_loss_share", actual is not None and float(actual) <= threshold, actual, threshold)
    if config.get("min_gain_loss_ratio") is not None:
        actual = return_profile.get("gain_loss_ratio")
        threshold = float(config.get("min_gain_loss_ratio") or 0.0)
        add("min_gain_loss_ratio", actual is not None and float(actual) >= threshold, actual, threshold)
    if config.get("min_return_advantage_vs_baseline") is not None and baseline_avg_return is not None:
        actual_avg = return_profile.get("avg_return_pct")
        actual = round(float(actual_avg) - float(baseline_avg_return), 4) if actual_avg is not None else None
        threshold = float(config.get("min_return_advantage_vs_baseline") or 0.0)
        add("min_return_advantage_vs_baseline", actual is not None and actual >= threshold, actual, threshold)

    failed_checks = [item for item in checks if not item.get("passed", False)]
    return {
        "enabled": True,
        "passed": not failed_checks,
        "checks": checks,
        "failed_checks": failed_checks,
        "profile": return_profile,
    }


def evaluate_regime_validation(
    regime_validation: dict[str, Any],
    *,
    policy: dict[str, Any] | None,
) -> dict[str, Any]:
    config = dict(policy or {})
    if not config:
        return {
            "enabled": False,
            "passed": True,
            "checks": [],
            "failed_checks": [],
            "summary": regime_validation,
        }

    checks: list[dict[str, Any]] = []
    distinct_regimes = int(regime_validation.get("distinct_regime_count") or 0)
    regimes = dict(regime_validation.get("regimes") or {})
    min_distinct_regimes = int(config.get("min_distinct_regimes") or 0)
    min_samples_per_regime = int(config.get("min_samples_per_regime") or 0)

    if min_distinct_regimes:
        checks.append(
            {
                "name": "min_distinct_regimes",
                "passed": distinct_regimes >= min_distinct_regimes,
                "actual": distinct_regimes,
                "threshold": min_distinct_regimes,
            }
        )
    if config.get("max_dominant_regime_share") is not None:
        actual = regime_validation.get("dominant_regime_share")
        threshold = float(config.get("max_dominant_regime_share") or 0.0)
        checks.append(
            {
                "name": "max_dominant_regime_share",
                "passed": actual is not None and float(actual) <= threshold,
                "actual": actual,
                "threshold": threshold,
            }
        )

    for regime_name, summary in sorted(regimes.items()):
        sample_count = int(summary.get("sample_count") or 0)
        if min_samples_per_regime:
            checks.append(
                {
                    "name": f"{regime_name}.sample_count",
                    "passed": sample_count >= min_samples_per_regime,
                    "actual": sample_count,
                    "threshold": min_samples_per_regime,
                }
            )
        if sample_count < max(1, min_samples_per_regime):
            continue
        for metric_name, config_key in (
            ("avg_return_pct", "min_avg_return_pct"),
            ("win_rate", "min_win_rate"),
            ("benchmark_pass_rate", "min_benchmark_pass_rate"),
        ):
            if config.get(config_key) is None:
                continue
            actual = summary.get(metric_name)
            threshold = float(config.get(config_key) or 0.0)
            checks.append(
                {
                    "name": f"{regime_name}.{metric_name}",
                    "passed": actual is not None and float(actual) >= threshold,
                    "actual": actual,
                    "threshold": threshold,
                }
            )

    failed_checks = [item for item in checks if not item.get("passed", False)]
    return {
        "enabled": True,
        "passed": not failed_checks,
        "checks": checks,
        "failed_checks": failed_checks,
        "summary": regime_validation,
    }


def evaluate_candidate_ab(
    ab_comparison: dict[str, Any],
    *,
    policy: dict[str, Any] | None,
) -> dict[str, Any]:
    config = dict(policy or {})
    if not config:
        return {
            "enabled": False,
            "passed": True,
            "checks": [],
            "failed_checks": [],
            "summary": ab_comparison,
        }

    if not ab_comparison:
        return {
            "enabled": True,
            "passed": True,
            "checks": [],
            "failed_checks": [],
            "summary": {},
            "skipped": True,
        }

    checks: list[dict[str, Any]] = []
    comparison = dict(ab_comparison.get("comparison") or {})
    candidate_present = bool(comparison.get("candidate_present", True))
    comparable = bool(comparison.get("comparable", False))
    required_when_candidate_present = bool(config.get("required_when_candidate_present", True))
    if candidate_present and required_when_candidate_present:
        checks.append(
            {
                "name": "available",
                "passed": comparable,
                "actual": int(comparable),
                "threshold": 1,
            }
        )
    if comparable:
        for check_name, metric_name in (
            ("min_return_lift_pct", "return_lift_pct"),
            ("min_strategy_score_lift", "strategy_score_lift"),
            ("min_benchmark_lift", "benchmark_lift"),
            ("min_win_rate_lift", "win_rate_lift"),
        ):
            if config.get(check_name) is None:
                continue
            actual = comparison.get(metric_name)
            threshold = float(config.get(check_name) or 0.0)
            checks.append(
                {
                    "name": check_name,
                    "passed": actual is not None and float(actual) >= threshold,
                    "actual": actual,
                    "threshold": threshold,
                }
            )
        if config.get("require_candidate_outperform_active", True):
            actual = bool(comparison.get("candidate_outperformed", False))
            checks.append(
                {
                    "name": "require_candidate_outperform_active",
                    "passed": actual,
                    "actual": int(actual),
                    "threshold": 1,
                }
            )

    failed_checks = [item for item in checks if not item.get("passed", False)]
    return {
        "enabled": True,
        "passed": not failed_checks,
        "checks": checks,
        "failed_checks": failed_checks,
        "summary": ab_comparison,
        "skipped": False,
    }


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
    return_profile = build_return_profile(ok_results, benchmark_pass_rate=benchmark_pass_rate)
    regime_validation = build_regime_validation_summary(ok_results)
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

    return_objectives = evaluate_return_objectives(
        return_profile,
        policy=dict(gate.get("return_objectives") or {}),
        baseline_avg_return=baseline_avg_return,
    )
    normalized_return_checks = _extend_gate_checks(
        checks,
        "return_objectives",
        list(return_objectives.get("checks") or []),
    )
    return_objectives = {
        **return_objectives,
        "checks": normalized_return_checks,
        "failed_checks": [item for item in normalized_return_checks if not item.get("passed", False)],
    }

    regime_validation_gate = evaluate_regime_validation(
        regime_validation,
        policy=dict(gate.get("regime_validation") or {}),
    )
    normalized_regime_checks = _extend_gate_checks(
        checks,
        "regime_validation",
        list(regime_validation_gate.get("checks") or []),
    )
    regime_validation_gate = {
        **regime_validation_gate,
        "checks": normalized_regime_checks,
        "failed_checks": [item for item in normalized_regime_checks if not item.get("passed", False)],
    }

    latest_ab_comparison, ab_source = _latest_ab_comparison(ok_results)
    candidate_ab_gate = evaluate_candidate_ab(
        latest_ab_comparison,
        policy=dict(gate.get("candidate_ab") or {}),
    )
    normalized_ab_checks = _extend_gate_checks(
        checks,
        "candidate_ab",
        list(candidate_ab_gate.get("checks") or []),
    )
    candidate_ab_gate = {
        **candidate_ab_gate,
        "source": ab_source,
        "checks": normalized_ab_checks,
        "failed_checks": [item for item in normalized_ab_checks if not item.get("passed", False)],
    }

    latest_feedback, feedback_source = _latest_research_feedback(ok_results)
    research_gate_policy = dict(gate.get("research_feedback") or {})
    research_feedback_summary = _research_feedback_brief(latest_feedback, source=feedback_source)
    promotion_feedback_gate: dict[str, Any] = {
        "enabled": bool(research_gate_policy),
        "passed": True,
        "checks": [],
        "latest_feedback": research_feedback_summary,
    }
    if research_gate_policy:
        if not latest_feedback:
            availability_check = {
                "name": "research_feedback.available",
                "passed": False,
                "actual": 0,
                "threshold": 1,
            }
            checks.append(availability_check)
            promotion_feedback_gate = {
                **promotion_feedback_gate,
                "passed": False,
                "checks": [availability_check],
                "failed_checks": [availability_check],
            }
        else:
            evaluation = evaluate_research_feedback_gate(
                latest_feedback,
                policy=research_gate_policy,
                defaults=research_gate_policy,
            )
            gate_checks: list[dict[str, Any]] = []
            for item in list(evaluation.get("checks") or []):
                gate_check = {
                    "name": f"research_feedback.{item.get('name')}",
                    "passed": bool(item.get("passed", False)),
                    "actual": item.get("actual"),
                    "threshold": item.get("required_gte", item.get("required_lte", item.get("blocked"))),
                    "meta": {k: v for k, v in item.items() if k not in {"name", "passed", "actual", "required_gte", "required_lte", "blocked"}},
                }
                gate_checks.append(gate_check)
                checks.append(gate_check)
            promotion_feedback_gate = {
                **evaluation,
                "enabled": True,
                "latest_feedback": research_feedback_summary,
                "checks": gate_checks,
                "failed_checks": [item for item in gate_checks if not item.get("passed", False)],
                "passed": all(item.get("passed", False) for item in gate_checks) if gate_checks else False,
            }

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
        "return_profile": return_profile,
        "return_objectives": return_objectives,
        "regime_validation": regime_validation_gate,
        "candidate_ab": candidate_ab_gate,
        "research_feedback": promotion_feedback_gate,
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
    return_profile = build_return_profile(ok_results, benchmark_pass_rate=benchmark_pass_rate)
    regime_validation = build_regime_validation_summary(ok_results)
    latest_ab_comparison, _ = _latest_ab_comparison(ok_results)
    latest_result = dict(results[-1]) if results else {}
    latest_result_summary = {
        "cycle_id": latest_result.get("cycle_id"),
        "status": str(latest_result.get("status") or ""),
        "return_pct": latest_result.get("return_pct"),
        "benchmark_passed": bool(latest_result.get("benchmark_passed", False)),
        "promotion_record": dict(latest_result.get("promotion_record") or {}),
        "lineage_record": dict(latest_result.get("lineage_record") or {}),
    }
    governance_metrics = build_governance_metrics(results)
    realism_summary = build_realism_summary(results)
    return {
        "run_id": run_id,
        "plan_id": plan["plan_id"],
        "created_at": datetime.now().isoformat(),
        "status": str(payload.get("status", "ok")),
        "objective": dict(plan.get("objective") or {}),
        "spec": dict(plan.get("spec") or {}),
        "protocol": dict(plan.get("protocol") or {}),
        "dataset": dict(plan.get("dataset") or {}),
        "model_scope": dict(plan.get("model_scope") or {}),
        "optimization": dict(plan.get("optimization") or {}),
        "guardrails": dict(plan.get("guardrails") or {}),
        "llm": dict(plan.get("llm") or {}),
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
            "return_profile": return_profile,
            "regime_validation": regime_validation,
            "latest_ab_comparison": latest_ab_comparison,
            "latest_result": latest_result_summary,
        },
        "promotion": promotion,
        "governance_metrics": governance_metrics,
        "realism_summary": realism_summary,
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
