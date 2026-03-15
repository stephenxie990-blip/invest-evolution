from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_MODEL_GOVERNANCE_MATRIX: dict[str, Any] = {
    "review": {
        "min_strategy_score": 0.45,
        "min_benchmark_pass_rate": 0.05,
        "max_drawdown_pct": 18.0,
    },
    "optimization": {
        "contract_version": "optimization_event.v2",
        "require_cycle_id": True,
        "require_lineage": True,
        "required_fields": [
            "event_id",
            "contract_version",
            "cycle_id",
            "trigger",
            "stage",
            "status",
            "decision",
            "applied_change",
            "lineage",
            "evidence",
            "ts",
        ],
        "required_lineage_fields": [
            "deployment_stage",
            "active_config_ref",
            "candidate_config_ref",
            "promotion_status",
            "review_basis_window",
            "fitness_source_cycles",
            "runtime_override_keys",
        ],
    },
    "routing": {
        "min_score": 0.0,
        "min_avg_return_pct": 0.0,
        "min_avg_strategy_score": 0.0,
        "min_benchmark_pass_rate": 0.0,
        "max_avg_drawdown": 15.0,
        "block_negative_score": True,
        "allowed_deployment_stages": ["active"],
    },
    "promotion": {
        "max_pending_cycles": 3,
        "max_pending_candidates": 1,
        "max_override_cycles": 2,
        "allowed_deployment_stages": ["candidate"],
        "prune_on_failed_candidate_ab": True,
        "max_selection_overlap_for_failed_candidate": 0.85,
        "blocked_feedback_biases": ["tighten_risk", "recalibrate_probability"],
        "min_feedback_samples": 5,
    },
    "freeze": {
        "max_candidate_pending_count": 0,
        "max_override_pending_count": 0,
        "max_active_candidate_drift_rate": 0.0,
    },
    "effect_objectives": {
        "enabled_after_governance": True,
        "required_routing_quality_pass_rate": 1.0,
        "objective_order": [
            "benchmark_pass_rate",
            "avg_sharpe_ratio",
            "avg_return_pct",
            "avg_max_drawdown",
        ],
    },
}

DEFAULT_PROMOTION_GATE_POLICY: dict[str, Any] = {
    "min_samples": 3,
    "research_feedback": {
        "min_sample_count": 5,
        "blocked_biases": ["tighten_risk", "recalibrate_probability"],
        "max_brier_like_direction_score": 0.25,
        "horizons": {
            "T+20": {
                "min_hit_rate": 0.45,
                "max_invalidation_rate": 0.30,
                "min_interval_hit_rate": 0.40,
            }
        },
    },
    "regime_validation": {
        "min_distinct_regimes": 2,
        "min_samples_per_regime": 1,
        "min_avg_return_pct": 0.0,
        "min_win_rate": 0.40,
        "min_benchmark_pass_rate": 0.40,
        "max_dominant_regime_share": 0.75,
    },
    "return_objectives": {
        "min_avg_return_pct": 0.0,
        "min_median_return_pct": 0.0,
        "min_cumulative_return_pct": 0.0,
        "min_win_rate": 0.50,
        "max_loss_share": 0.50,
        "min_benchmark_pass_rate": 0.50,
    },
    "candidate_ab": {
        "required_when_candidate_present": True,
        "require_candidate_outperform_active": True,
        "min_return_lift_pct": 0.0,
        "min_strategy_score_lift": 0.0,
        "min_benchmark_lift": 0.0,
    },
}

DEFAULT_FREEZE_GATE_POLICY: dict[str, Any] = {
    "avg_return_gt": 0.0,
    "avg_sharpe_gte": 0.8,
    "avg_max_drawdown_lt": 15.0,
    "benchmark_pass_rate_gte": 0.60,
    "research_feedback": {
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
    },
    "governance": {
        "max_candidate_pending_count": 0,
        "max_override_pending_count": 0,
        "max_active_candidate_drift_rate": 0.0,
    },
}


def deep_merge(base: dict[str, Any] | None, patch: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = deepcopy(dict(base or {}))
    for key, value in dict(patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(dict(merged.get(key) or {}), value)
        else:
            merged[key] = deepcopy(value)
    return merged


def resolve_model_governance_matrix(*overrides: dict[str, Any] | None) -> dict[str, Any]:
    matrix = deepcopy(DEFAULT_MODEL_GOVERNANCE_MATRIX)
    for override in overrides:
        matrix = deep_merge(matrix, dict(override or {}))
    return matrix


def normalize_promotion_gate_policy(
    policy: dict[str, Any] | None = None,
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return deep_merge(defaults or DEFAULT_PROMOTION_GATE_POLICY, dict(policy or {}))


def normalize_freeze_gate_policy(
    policy: dict[str, Any] | None = None,
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return deep_merge(defaults or DEFAULT_FREEZE_GATE_POLICY, dict(policy or {}))


def normalize_config_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    looks_like_path = (
        path.is_absolute()
        or path.suffix.lower() in {".yaml", ".yml", ".json"}
        or "/" in text
        or "\\" in text
    )
    if not looks_like_path:
        return text
    try:
        return str(path.resolve(strict=False))
    except Exception:
        return text


def latest_actionable_event(optimization_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    for event in reversed(list(optimization_events or [])):
        applied_change = dict(event.get("applied_change") or {})
        if applied_change:
            return dict(event)
    return {}


def build_optimization_event_lineage(
    *,
    cycle_id: int | None,
    model_name: str = "",
    active_config_ref: str = "",
    candidate_config_ref: str = "",
    promotion_status: str = "not_evaluated",
    deployment_stage: str = "active",
    review_basis_window: dict[str, Any] | None = None,
    fitness_source_cycles: list[int] | None = None,
    runtime_override_keys: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "cycle_id": int(cycle_id) if cycle_id is not None else None,
        "model_name": str(model_name or ""),
        "deployment_stage": str(deployment_stage or "active"),
        "active_config_ref": normalize_config_ref(active_config_ref),
        "candidate_config_ref": normalize_config_ref(candidate_config_ref),
        "promotion_status": str(promotion_status or "not_evaluated"),
        "review_basis_window": deepcopy(dict(review_basis_window or {})),
        "fitness_source_cycles": [int(item) for item in list(fitness_source_cycles or [])],
        "runtime_override_keys": [
            str(item).strip()
            for item in list(runtime_override_keys or [])
            if str(item).strip()
        ],
    }


def infer_deployment_stage(
    *,
    run_context: dict[str, Any] | None = None,
    optimization_events: list[dict[str, Any]] | None = None,
    applied_change: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(run_context or {})
    promotion_decision = dict(payload.get("promotion_decision") or {})
    candidate_config_ref = normalize_config_ref(payload.get("candidate_config_ref") or "")
    action_payload = dict(applied_change or {})
    if not action_payload:
        action_payload = dict(latest_actionable_event(optimization_events).get("applied_change") or {})
    runtime_override_keys: list[str] = []
    for scope in ("params", "agent_weights"):
        values = dict(action_payload.get(scope) or {})
        for key in values.keys():
            normalized = str(key).strip()
            if normalized and normalized not in runtime_override_keys:
                runtime_override_keys.append(normalized)
    if candidate_config_ref and not bool(promotion_decision.get("applied_to_active", False)):
        return {
            "deployment_stage": "candidate",
            "reason": "candidate_pending_promotion",
            "runtime_override_keys": runtime_override_keys,
        }
    if runtime_override_keys:
        return {
            "deployment_stage": "override",
            "reason": "runtime_override_pending_promotion",
            "runtime_override_keys": runtime_override_keys,
        }
    return {
        "deployment_stage": "active",
        "reason": "active_baseline",
        "runtime_override_keys": runtime_override_keys,
    }


def evaluate_promotion_discipline(
    *,
    run_context: dict[str, Any] | None,
    cycle_history: list[Any] | None = None,
    policy: dict[str, Any] | None = None,
    optimization_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    matrix = resolve_model_governance_matrix({"promotion": dict(policy or {})})
    config = dict(matrix.get("promotion") or {})
    payload = dict(run_context or {})
    stage_info = infer_deployment_stage(
        run_context=payload,
        optimization_events=optimization_events,
    )
    deployment_stage = str(stage_info.get("deployment_stage") or "active")
    current_candidate_ref = normalize_config_ref(payload.get("candidate_config_ref") or "")
    pending_refs: list[str] = []
    override_streak = 0
    for item in list(cycle_history or []):
        lineage_record = dict(
            item.get("lineage_record", {}) if isinstance(item, dict) else getattr(item, "lineage_record", {})
            or {}
        )
        historical_stage = str(lineage_record.get("deployment_stage") or "")
        if not historical_stage:
            if str(lineage_record.get("lineage_status") or "") == "candidate_pending":
                historical_stage = "candidate"
            elif str(lineage_record.get("lineage_status") or "") == "override_pending":
                historical_stage = "override"
            else:
                historical_stage = "active"
        if historical_stage == "candidate":
            ref = normalize_config_ref(lineage_record.get("candidate_config_ref") or "")
            if ref:
                pending_refs.append(ref)
        if historical_stage == "override":
            override_streak += 1
        else:
            override_streak = 0

    pending_age = 0
    if deployment_stage == "candidate" and current_candidate_ref:
        for ref in reversed(pending_refs):
            if ref == current_candidate_ref:
                pending_age += 1
            else:
                break
        pending_age += 1
    distinct_pending_refs = {ref for ref in pending_refs if ref}
    if deployment_stage == "candidate" and current_candidate_ref:
        distinct_pending_refs.add(current_candidate_ref)
    pending_candidate_count = len(distinct_pending_refs)
    current_override_streak = override_streak + (1 if deployment_stage == "override" else 0)
    ab_comparison = dict(payload.get("ab_comparison") or {})
    comparison = dict(ab_comparison.get("comparison") or {})
    research_feedback = dict(payload.get("research_feedback") or {})
    recommendation = dict(research_feedback.get("recommendation") or {})

    status = "active_aligned"
    discipline_actions: list[str] = []
    violations: list[str] = []
    if deployment_stage == "candidate":
        status = "candidate_pending"
        if bool(config.get("prune_on_failed_candidate_ab", True)) and comparison:
            selection_overlap_ratio = float(comparison.get("selection_overlap_ratio") or 0.0)
            max_selection_overlap = float(
                config.get("max_selection_overlap_for_failed_candidate", 0.85) or 0.85
            )
            if (
                bool(comparison.get("candidate_present", True))
                and bool(comparison.get("comparable", False))
                and not bool(comparison.get("candidate_outperformed", False))
                and selection_overlap_ratio >= max_selection_overlap
            ):
                status = "candidate_pruned"
                violations.append("failed_candidate_ab")
                discipline_actions.append("prune_failed_candidate")
        blocked_feedback_biases = [
            str(item).strip()
            for item in list(config.get("blocked_feedback_biases") or [])
            if str(item).strip()
        ]
        feedback_bias = str(recommendation.get("bias") or "").strip()
        if (
            feedback_bias in blocked_feedback_biases
            and int(research_feedback.get("sample_count") or 0)
            >= int(config.get("min_feedback_samples", 5) or 5)
        ):
            status = "candidate_pruned"
            violations.append("blocked_research_feedback")
            discipline_actions.append("prune_feedback_blocked_candidate")
        if pending_age > int(config.get("max_pending_cycles", 3) or 3):
            status = "candidate_expired"
            violations.append("max_pending_cycles")
            discipline_actions.append("expire_candidate")
        if pending_candidate_count > int(config.get("max_pending_candidates", 1) or 1):
            if status != "candidate_expired":
                status = "candidate_pruned"
            violations.append("max_pending_candidates")
            discipline_actions.append("prune_old_pending_candidates")
    elif deployment_stage == "override":
        status = "override_pending"
        if current_override_streak > int(config.get("max_override_cycles", 2) or 2):
            status = "override_expired"
            violations.append("max_override_cycles")
            discipline_actions.append("force_candidate_or_revert")

    return {
        "deployment_stage": deployment_stage,
        "status": status,
        "reason": str(stage_info.get("reason") or ""),
        "pending_candidate_count": pending_candidate_count,
        "pending_candidate_age": pending_age,
        "override_streak": current_override_streak,
        "violations": violations,
        "discipline_actions": discipline_actions,
        "runtime_override_keys": list(stage_info.get("runtime_override_keys") or []),
        "candidate_ab": comparison,
        "feedback_bias": str(recommendation.get("bias") or ""),
        "policy": config,
    }


def evaluate_optimization_event_contract(
    payload: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    matrix = resolve_model_governance_matrix({"optimization": dict(policy or {})})
    config = dict(matrix.get("optimization") or {})
    checks: list[dict[str, Any]] = []
    lineage = dict(payload.get("lineage") or {})
    required_fields = [str(item) for item in list(config.get("required_fields") or []) if str(item).strip()]
    for field_name in required_fields:
        value = payload.get(field_name)
        checks.append(
            {
                "name": f"field.{field_name}",
                "passed": value is not None and value != "",
                "actual": 0 if value in (None, "") else 1,
                "threshold": 1,
            }
        )
    if bool(config.get("require_cycle_id", True)):
        cycle_id = payload.get("cycle_id")
        checks.append(
            {
                "name": "cycle_id.present",
                "passed": cycle_id not in (None, ""),
                "actual": 0 if cycle_id in (None, "") else 1,
                "threshold": 1,
            }
        )
    if bool(config.get("require_lineage", True)):
        required_lineage_fields = [
            str(item)
            for item in list(config.get("required_lineage_fields") or [])
            if str(item).strip()
        ]
        for field_name in required_lineage_fields:
            value = lineage.get(field_name)
            passed = value is not None
            if isinstance(value, str):
                passed = bool(value or field_name in {"active_config_ref", "candidate_config_ref"})
            checks.append(
                {
                    "name": f"lineage.{field_name}",
                    "passed": passed,
                    "actual": 0 if not passed else 1,
                    "threshold": 1,
                }
            )
    failed_checks = [item for item in checks if not bool(item.get("passed", False))]
    return {
        "passed": not failed_checks,
        "checks": checks,
        "failed_checks": failed_checks,
        "contract_version": str(config.get("contract_version") or "optimization_event.v2"),
    }


def evaluate_routing_quality_gate(
    entry: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    matrix = resolve_model_governance_matrix({"routing": dict(policy or {})})
    config = dict(matrix.get("routing") or {})
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, actual: Any, threshold: Any) -> None:
        checks.append(
            {
                "name": name,
                "passed": bool(passed),
                "actual": actual,
                "threshold": threshold,
            }
        )

    score = float(entry.get("score", 0.0) or 0.0)
    avg_return = float(entry.get("avg_return_pct", 0.0) or 0.0)
    avg_strategy = float(entry.get("avg_strategy_score", 0.0) or 0.0)
    benchmark_pass_rate = float(entry.get("benchmark_pass_rate", 0.0) or 0.0)
    avg_drawdown = float(entry.get("avg_max_drawdown", 0.0) or 0.0)
    deployment_stage = str(entry.get("deployment_stage") or "active")

    if bool(config.get("block_negative_score", True)):
        add("block_negative_score", score >= 0.0, score, 0.0)
    add("min_score", score >= float(config.get("min_score", 0.0) or 0.0), score, float(config.get("min_score", 0.0) or 0.0))
    add(
        "min_avg_return_pct",
        avg_return >= float(config.get("min_avg_return_pct", 0.0) or 0.0),
        avg_return,
        float(config.get("min_avg_return_pct", 0.0) or 0.0),
    )
    add(
        "min_avg_strategy_score",
        avg_strategy >= float(config.get("min_avg_strategy_score", 0.0) or 0.0),
        avg_strategy,
        float(config.get("min_avg_strategy_score", 0.0) or 0.0),
    )
    add(
        "min_benchmark_pass_rate",
        benchmark_pass_rate >= float(config.get("min_benchmark_pass_rate", 0.0) or 0.0),
        benchmark_pass_rate,
        float(config.get("min_benchmark_pass_rate", 0.0) or 0.0),
    )
    add(
        "max_avg_drawdown",
        avg_drawdown <= float(config.get("max_avg_drawdown", 15.0) or 15.0),
        avg_drawdown,
        float(config.get("max_avg_drawdown", 15.0) or 15.0),
    )
    allowed_deployment_stages = [
        str(item).strip()
        for item in list(config.get("allowed_deployment_stages") or [])
        if str(item).strip()
    ]
    if allowed_deployment_stages:
        add(
            "allowed_deployment_stages",
            deployment_stage in allowed_deployment_stages,
            deployment_stage,
            allowed_deployment_stages,
        )

    failed_checks = [item for item in checks if not bool(item.get("passed", False))]
    return {
        "passed": not failed_checks,
        "checks": checks,
        "failed_checks": failed_checks,
        "deployment_stage": deployment_stage,
    }
