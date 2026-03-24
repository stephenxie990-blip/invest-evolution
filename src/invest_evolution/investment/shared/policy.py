from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Sequence

from invest_evolution.agent_runtime.runtime import enforce_path_within_root
from invest_evolution.config import PROJECT_ROOT, config

logger = logging.getLogger(__name__)

# Shared assistant helpers

if TYPE_CHECKING:
    from invest_evolution.investment.contracts import ManagerPlan, ManagerRunContext, ManagerSpec


def _normalized_limit(limit: int) -> int:
    value = int(limit)
    return max(0, value)


class MemoryRetrievalService:
    """Lightweight retrieval adapter for manager research and review contexts."""

    def search(
        self,
        query: str,
        records: Iterable[Dict[str, Any]],
        *,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        tokens = {
            token.strip().lower()
            for token in str(query or "").replace("_", " ").split()
            if token.strip()
        }
        ranked: List[tuple[int, Dict[str, Any]]] = []
        for record in list(records or []):
            haystack = json.dumps(record, ensure_ascii=False).lower()
            score = sum(1 for token in tokens if token in haystack)
            if score > 0:
                ranked.append((score, dict(record)))
        ranked.sort(key=lambda item: item[0], reverse=True)
        bounded_limit = _normalized_limit(limit)
        if bounded_limit <= 0:
            return []
        return [record for _, record in ranked[:bounded_limit]]


class CognitiveAssistService:
    """Shared cognitive helpers used across managers and governance layers."""

    def explain_plan(self, manager_plan: "ManagerPlan") -> Dict[str, Any]:
        return {
            "manager_id": manager_plan.manager_id,
            "summary": manager_plan.reasoning or f"{manager_plan.manager_name} produced {len(manager_plan.positions)} picks",
            "top_theses": [position.thesis for position in list(manager_plan.positions or [])[:3] if position.thesis],
            "selected_codes": list(manager_plan.selected_codes),
        }

    def diagnose_empty_plan(
        self,
        manager_spec: "ManagerSpec",
        run_context: "ManagerRunContext",
        *,
        reason: str,
    ) -> Dict[str, Any]:
        findings: List[str] = [str(reason or "empty_plan")]
        if not run_context.market_stats:
            findings.append("market_stats_missing")
        return {
            "manager_id": manager_spec.manager_id,
            "regime": run_context.regime,
            "diagnosis": findings,
            "suggestion": "hold_for_review",
        }


# Shared governance policy


DEFAULT_GOVERNANCE_MATRIX: dict[str, Any] = {
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
            "active_runtime_config_ref",
            "candidate_runtime_config_ref",
            "promotion_status",
            "review_basis_window",
            "fitness_source_cycles",
            "runtime_override_keys",
        ],
    },
    "governance": {
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
        "required_governance_quality_pass_rate": 1.0,
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


def resolve_governance_matrix(*overrides: dict[str, Any] | None) -> dict[str, Any]:
    matrix = deepcopy(DEFAULT_GOVERNANCE_MATRIX)
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
        resolved = enforce_path_within_root(PROJECT_ROOT, path)
        return str(resolved)
    except ValueError:
        logger.warning("Rejected out-of-root config reference: %s", text)
        return ""


def _item_field(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _historical_shadow_mode(item: Any) -> bool:
    lineage_record = dict(_item_field(item, "lineage_record", {}) or {})
    if "shadow_mode" in lineage_record:
        return bool(lineage_record.get("shadow_mode"))

    promotion_record = dict(_item_field(item, "promotion_record", {}) or {})
    if "shadow_mode" in promotion_record:
        return bool(promotion_record.get("shadow_mode"))

    validation_report = dict(_item_field(item, "validation_report", {}) or {})
    if "shadow_mode" in validation_report:
        return bool(validation_report.get("shadow_mode"))

    validation_summary = dict(_item_field(item, "validation_summary", {}) or {})
    if "shadow_mode" in validation_summary:
        return bool(validation_summary.get("shadow_mode"))

    experiment_spec = dict(_item_field(item, "experiment_spec", {}) or {})
    protocol = dict(experiment_spec.get("protocol") or {})
    return bool(protocol.get("shadow_mode", False))


def _int_with_default(value: Any, default: int) -> int:
    if value is None or value == "":
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _float_with_default(value: Any, default: float) -> float:
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def latest_actionable_event(optimization_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    for event in reversed(list(optimization_events or [])):
        if _optimization_action_payload(event):
            return dict(event)
    return {}


def _optimization_action_payload(event: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(event or {})
    review_effects = dict(payload.get("review_applied_effects_payload") or {})
    if review_effects:
        return {
            "params": dict(review_effects.get("param_adjustments") or {}),
            "agent_weights": dict(review_effects.get("agent_weight_adjustments") or {}),
            "manager_budget_weights": dict(
                review_effects.get("manager_budget_adjustments") or {}
            ),
        }
    runtime_payload = dict(payload.get("runtime_config_mutation_payload") or {})
    if runtime_payload:
        return {
            "params": dict(runtime_payload.get("param_adjustments") or {}),
            "scoring": dict(runtime_payload.get("scoring_adjustments") or {}),
        }
    skipped_payload = dict(payload.get("runtime_config_mutation_skipped_payload") or {})
    if skipped_payload:
        return {
            "params": dict(skipped_payload.get("param_adjustments") or {}),
            "scoring": dict(skipped_payload.get("scoring_adjustments") or {}),
        }
    feedback_payload = dict(payload.get("research_feedback_payload") or {})
    if feedback_payload:
        return {
            "params": dict(feedback_payload.get("param_adjustments") or {}),
            "scoring": dict(feedback_payload.get("scoring_adjustments") or {}),
        }
    return dict(payload.get("applied_change") or {})


def build_optimization_event_lineage(
    *,
    cycle_id: int | None,
    manager_id: str = "",
    active_runtime_config_ref: str = "",
    candidate_runtime_config_ref: str = "",
    promotion_status: str = "not_evaluated",
    deployment_stage: str = "active",
    review_basis_window: dict[str, Any] | None = None,
    fitness_source_cycles: list[int] | None = None,
    runtime_override_keys: list[str] | None = None,
) -> dict[str, Any]:
    raw_active_runtime_config_ref = str(active_runtime_config_ref or "").strip()
    raw_candidate_runtime_config_ref = str(candidate_runtime_config_ref or "").strip()
    return {
        "cycle_id": int(cycle_id) if cycle_id is not None else None,
        "manager_id": str(manager_id or ""),
        "deployment_stage": str(deployment_stage or "active"),
        "active_runtime_config_ref": (
            normalize_config_ref(raw_active_runtime_config_ref)
            or raw_active_runtime_config_ref
        ),
        "candidate_runtime_config_ref": (
            normalize_config_ref(raw_candidate_runtime_config_ref)
            or raw_candidate_runtime_config_ref
        ),
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
    raw_candidate_runtime_config_ref = str(
        payload.get("candidate_runtime_config_ref") or ""
    ).strip()
    candidate_runtime_config_ref = (
        normalize_config_ref(raw_candidate_runtime_config_ref)
        or raw_candidate_runtime_config_ref
    )
    action_payload = dict(applied_change or {})
    if not action_payload:
        action_payload = _optimization_action_payload(
            latest_actionable_event(optimization_events)
        )
    runtime_override_keys: list[str] = []
    for scope in ("params", "agent_weights"):
        values = dict(action_payload.get(scope) or {})
        for key in values.keys():
            normalized = str(key).strip()
            if normalized and normalized not in runtime_override_keys:
                runtime_override_keys.append(normalized)
    if candidate_runtime_config_ref and not bool(promotion_decision.get("applied_to_active", False)):
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
    matrix = resolve_governance_matrix({"promotion": dict(policy or {})})
    config = dict(matrix.get("promotion") or {})
    payload = dict(run_context or {})
    stage_info = infer_deployment_stage(
        run_context=payload,
        optimization_events=optimization_events,
    )
    deployment_stage = str(stage_info.get("deployment_stage") or "active")
    current_candidate_ref = normalize_config_ref(payload.get("candidate_runtime_config_ref") or "")
    pending_refs: list[str] = []
    override_streak = 0
    for item in list(cycle_history or []):
        if _historical_shadow_mode(item):
            continue
        lineage_record = dict(
            item.get("lineage_record", {}) if isinstance(item, dict) else getattr(item, "lineage_record", {})
            or {}
        )
        historical_stage = str(lineage_record.get("deployment_stage") or "")
        lineage_status = str(lineage_record.get("lineage_status") or "")
        if not historical_stage:
            if lineage_status == "candidate_pending":
                historical_stage = "candidate"
            elif lineage_status == "override_pending":
                historical_stage = "override"
            else:
                historical_stage = "active"
        if historical_stage == "candidate" and lineage_status not in {
            "candidate_pruned",
            "candidate_expired",
            "candidate_applied",
        }:
            ref = normalize_config_ref(lineage_record.get("candidate_runtime_config_ref") or "")
            if ref:
                pending_refs.append(ref)
        if historical_stage == "override" and lineage_status != "override_expired":
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
        selection_overlap_ratio = 0.0
        max_selection_overlap = float(
            config.get("max_selection_overlap_for_failed_candidate", 0.85) or 0.85
        )
        if bool(config.get("prune_on_failed_candidate_ab", True)) and comparison:
            selection_overlap_ratio = float(comparison.get("selection_overlap_ratio") or 0.0)
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
                >= _int_with_default(config.get("min_feedback_samples", 5), 5)
        ):
            status = "candidate_pruned"
            violations.append("blocked_research_feedback")
            discipline_actions.append("prune_feedback_blocked_candidate")
        if pending_age > _int_with_default(config.get("max_pending_cycles", 3), 3):
            status = "candidate_expired"
            violations.append("max_pending_cycles")
            discipline_actions.append("expire_candidate")
        if pending_candidate_count > _int_with_default(config.get("max_pending_candidates", 1), 1):
            if status != "candidate_expired":
                status = "candidate_pruned"
            violations.append("max_pending_candidates")
            discipline_actions.append("prune_old_pending_candidates")
    elif deployment_stage == "override":
        status = "override_pending"
        if current_override_streak > _int_with_default(config.get("max_override_cycles", 2), 2):
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
    matrix = resolve_governance_matrix({"optimization": dict(policy or {})})
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
                passed = bool(value or field_name in {"active_runtime_config_ref", "candidate_runtime_config_ref"})
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


def evaluate_governance_quality_gate(
    entry: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    matrix = resolve_governance_matrix({"governance": dict(policy or {})})
    config = dict(matrix.get("governance") or {})
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


# Shared indicator re-exports



# Shared manager style



DEFAULT_MANAGER_STYLE_COMPATIBILITY: dict[str, dict[str, float]] = {
    "momentum": {
        "bull": 1.0,
        "oscillation": 0.45,
        "bear": 0.10,
        "unknown": 0.60,
    },
    "mean_reversion": {
        "bull": 0.20,
        "oscillation": 1.0,
        "bear": 0.65,
        "unknown": 0.60,
    },
    "defensive_low_vol": {
        "bull": 0.35,
        "oscillation": 0.78,
        "bear": 1.0,
        "unknown": 0.65,
    },
    "value_quality": {
        "bull": 0.82,
        "oscillation": 0.76,
        "bear": 0.62,
        "unknown": 0.68,
    },
    "unknown": {
        "bull": 0.55,
        "oscillation": 0.55,
        "bear": 0.55,
        "unknown": 0.55,
    },
}


REGIME_ORDER = ("bull", "oscillation", "bear", "unknown")


def normalize_manager_id(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized or "unknown"


def normalize_regime(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"bull", "bear", "oscillation", "unknown"}:
        return normalized
    return "unknown"


def get_manager_style_profile(manager_id: str | None) -> dict[str, float]:
    normalized = normalize_manager_id(manager_id)
    profile = DEFAULT_MANAGER_STYLE_COMPATIBILITY.get(normalized)
    if profile is None:
        profile = DEFAULT_MANAGER_STYLE_COMPATIBILITY["unknown"]
    return deepcopy(profile)


def manager_regime_compatibility(manager_id: str | None, regime: str | None) -> float:
    normalized_regime = normalize_regime(regime)
    profile = get_manager_style_profile(manager_id)
    value = float(profile.get(normalized_regime, profile.get("unknown", 0.55)) or 0.0)
    return round(max(0.0, min(1.0, value)), 4)


# Shared summaries


def format_stock_table(summaries: Sequence[Mapping[str, Any]]) -> str:
    if not summaries:
        return "（无候选股票）"

    lines = [
        "| # | 代码 | 收盘价 | 5日涨跌% | 20日涨跌% | MA趋势 | RSI | MACD信号 | BB位置 | 量比 |",
        "|---|------|--------|----------|-----------|--------|-----|----------|--------|------|",
    ]
    for i, s in enumerate(summaries):
        lines.append(
            f"| {i+1} "
            f"| {s['code']} "
            f"| {s['close']:.1f} "
            f"| {s['change_5d']:+.1f} "
            f"| {s['change_20d']:+.1f} "
            f"| {s['ma_trend']} "
            f"| {s['rsi']:.0f} "
            f"| {s['macd']} "
            f"| {s['bb_pos']:.2f} "
            f"| {s['vol_ratio']:.1f} |"
        )
    return "\n".join(lines)


# Shared tracking


@dataclass
class PredictionRecord:
    """单条 Agent 预测记录"""
    cycle: int
    agent: str           # "trend_hunter" / "contrarian" / ...
    code: str            # 股票代码
    score: float         # Agent 给的评分 (0-1)
    stop_loss_pct: float
    take_profit_pct: float
    reasoning: str = ""

    # 交易结束后填入
    actual_return: Optional[float] = None  # 实际盈亏（绝对值）
    was_selected: bool = False             # 是否被 Commander 选中
    was_profitable: bool = False           # 是否盈利


class AgentTracker:
    """
    Agent 预测追踪器

    记录每个 Agent 的推荐，交易结束后与实际结果对账
    为复盘会议提供事实数据
    """

    def __init__(self):
        self.predictions: List[PredictionRecord] = []
        self._by_cycle: Dict[int, List[PredictionRecord]] = {}

    def record_predictions(self, cycle: int, agent_name: str, picks: List[dict]):
        """记录一个 Agent 的推荐（picks = Agent.analyze() 的 picks 列表）"""
        for p in picks:
            record = PredictionRecord(
                cycle=cycle,
                agent=agent_name,
                code=p.get("code", ""),
                score=p.get("score", 0.5),
                stop_loss_pct=p.get("stop_loss_pct", 0.05),
                take_profit_pct=p.get("take_profit_pct", 0.15),
                reasoning=p.get("reasoning", ""),
            )
            self.predictions.append(record)
            self._by_cycle.setdefault(cycle, []).append(record)

    def mark_selected(self, cycle: int, selected_codes: List[str]):
        """标记哪些推荐被 Commander 选中"""
        selected_set = set(selected_codes)
        for record in self._by_cycle.get(cycle, []):
            record.was_selected = record.code in selected_set

    def record_outcomes(self, cycle: int, per_stock_pnl: Dict[str, float]):
        """记录实际交易结果（per_stock_pnl = {code: 盈亏金额}）"""
        for record in self._by_cycle.get(cycle, []):
            if record.code in per_stock_pnl:
                pnl = per_stock_pnl[record.code]
                record.actual_return = pnl
                record.was_profitable = pnl > 0

    def compute_accuracy(
        self,
        agent_name: str | None = None,
        last_n_cycles: int | None = None,
    ) -> dict:
        """
        计算 Agent 预测准确率

        Returns:
            {agent_name: {total_picks, selected_count, traded_count,
                          profitable_count, accuracy, avg_score}}
        """
        records = self.predictions

        if last_n_cycles is not None and self._by_cycle:
            recent = set(sorted(self._by_cycle.keys())[-last_n_cycles:])
            records = [r for r in records if r.cycle in recent]

        if agent_name:
            records = [r for r in records if r.agent == agent_name]

        agent_records: Dict[str, List[PredictionRecord]] = {}
        for r in records:
            agent_records.setdefault(r.agent, []).append(r)

        stats = {}
        for name, recs in agent_records.items():
            total = len(recs)
            selected = sum(1 for r in recs if r.was_selected)
            traded = sum(1 for r in recs if r.actual_return is not None)
            profitable = sum(1 for r in recs if r.was_profitable)
            avg_score = sum(r.score for r in recs) / total if total > 0 else 0

            stats[name] = {
                "total_picks": total,
                "selected_count": selected,
                "traded_count": traded,
                "profitable_count": profitable,
                "accuracy": profitable / traded if traded > 0 else 0.0,
                "avg_score": round(avg_score, 3),
            }
        return stats

    def get_cycle_summary(self, cycle: int) -> dict:
        """获取单个 cycle 的预测摘要（按 Agent 分组）"""
        by_agent = {}
        for r in self._by_cycle.get(cycle, []):
            by_agent.setdefault(r.agent, []).append({
                "code": r.code,
                "score": r.score,
                "selected": r.was_selected,
                "profitable": r.was_profitable,
                "actual_return": r.actual_return,
            })
        return by_agent

    def get_summary(self) -> dict:
        """获取总体摘要"""
        return {
            "total_predictions": len(self.predictions),
            "total_cycles": len(self._by_cycle),
            "accuracy_by_agent": self.compute_accuracy(),
        }


# ============================================================
# Part 7: 决策追踪日志
# ============================================================

class TraceLog:
    """
    决策追踪日志

    记录每轮决策的全过程，便于复盘和调试
    """

    def __init__(self, log_dir: str | None = None):
        base_logs_dir = config.logs_dir or (config.output_dir / "logs" if config.output_dir is not None else Path("runtime/logs"))
        self.log_dir = log_dir or str(base_logs_dir / "trace")
        self.current_round: int | None = None
        self.round_data: dict = {}

    def start_round(self, round_id: int, t0_date: str):
        """开始一轮"""
        self.current_round = round_id
        self.round_data = {
            "round_id": round_id,
            "t0_date": t0_date,
            "start_time": datetime.now().isoformat(),
            "steps": [],
        }

    def log_step(self, step_name: str, data: Dict):
        """记录步骤"""
        self.round_data["steps"].append({
            "step": step_name,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        })

    def log_decision(self, decision: Dict):
        """记录决策"""
        self.round_data["decision"] = decision

    def log_result(self, result: Dict):
        """记录结果"""
        self.round_data["result"] = result
        self.round_data["end_time"] = datetime.now().isoformat()

    def save(self):
        """保存本轮日志"""
        if not self.current_round:
            return
        os.makedirs(self.log_dir, exist_ok=True)
        filepath = os.path.join(self.log_dir, f"round_{self.current_round:04d}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.round_data, f, ensure_ascii=False, indent=2, default=str)
        self.current_round = None
        self.round_data = {}

    def load_round(self, round_id: int) -> Dict:
        """加载某轮日志"""
        filepath = os.path.join(self.log_dir, f"round_{round_id:04d}.json")
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

__all__ = [name for name in globals() if not name.startswith('_')]
