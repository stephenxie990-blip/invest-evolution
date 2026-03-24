from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Sequence

import yaml

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

DEFAULT_REGIME_HARD_FAIL_POLICY: dict[str, Any] = {
    "enabled": True,
    "critical_regimes": ["bull", "bear"],
    "min_cycles": 2,
    "min_avg_return_pct": -0.5,
    "max_benchmark_pass_rate": 0.25,
    "max_win_rate": 0.40,
    "per_regime": {},
}

DEFAULT_STRATEGY_FAMILY_REGIME_HARD_FAIL_PROFILES: dict[str, dict[str, Any]] = {
    "momentum": {
        "critical_regimes": ["bull", "bear"],
        "min_cycles": 2,
        "per_regime": {
            "bull": {
                "min_avg_return_pct": -0.10,
                "max_benchmark_pass_rate": 0.25,
                "max_win_rate": 0.45,
            },
            "bear": {
                "min_avg_return_pct": -0.40,
                "max_benchmark_pass_rate": 0.25,
                "max_win_rate": 0.40,
            },
        },
    },
    "mean_reversion": {
        "critical_regimes": ["oscillation", "bear"],
        "min_cycles": 2,
        "per_regime": {
            "oscillation": {
                "min_avg_return_pct": -0.20,
                "max_benchmark_pass_rate": 0.25,
                "max_win_rate": 0.40,
            },
            "bear": {
                "min_avg_return_pct": -0.50,
                "max_benchmark_pass_rate": 0.20,
                "max_win_rate": 0.35,
            },
        },
    },
    "defensive_low_vol": {
        "critical_regimes": ["bear", "oscillation"],
        "min_cycles": 2,
        "per_regime": {
            "bear": {
                "min_avg_return_pct": -0.15,
                "max_benchmark_pass_rate": 0.30,
                "max_win_rate": 0.45,
            },
            "oscillation": {
                "min_avg_return_pct": -0.25,
                "max_benchmark_pass_rate": 0.25,
                "max_win_rate": 0.40,
            },
        },
    },
    "value_quality": {
        "critical_regimes": ["bear", "oscillation"],
        "min_cycles": 2,
        "per_regime": {
            "bear": {
                "min_avg_return_pct": -0.35,
                "max_benchmark_pass_rate": 0.25,
                "max_win_rate": 0.40,
            },
            "oscillation": {
                "min_cycles": 3,
                "min_avg_return_pct": -0.20,
                "max_benchmark_pass_rate": 0.15,
                "max_win_rate": 0.35,
                "max_loss_share": 0.65,
                "min_negative_contribution_pct": -4.0,
                "required_failed_metrics": [
                    "avg_return_pct",
                    "loss_share",
                    "negative_contribution_pct",
                ],
                "confirm_any_failed_metrics": [
                    "benchmark_pass_rate",
                    "win_rate",
                ],
            },
        },
    },
}


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

DEFAULT_PROPOSAL_GATE_POLICY: dict[str, Any] = {
    "enabled": True,
    "protected_params": [
        "position_size",
        "cash_reserve",
        "signal_threshold",
        "take_profit_pct",
        "stop_loss_pct",
        "trailing_pct",
        "max_hold_days",
    ],
    "behavior_params": [
        "position_size",
        "cash_reserve",
        "signal_threshold",
        "take_profit_pct",
        "max_hold_days",
        "top_n",
        "max_positions",
    ],
    "profitable_cycle": {
        "freeze_behavior_params": True,
        "block_scoring_adjustments": True,
        "block_agent_weight_adjustments": True,
        "allowed_safety_tightening_params": ["stop_loss_pct"],
    },
    "identity_protection": {
        "max_single_step_ratio_vs_baseline": 0.30,
        "scoring": {
            "max_single_step_ratio_vs_baseline": 0.30,
        },
        "agent_weights": {
            "max_single_step_ratio_vs_baseline": 0.30,
        },
    },
    "cumulative_drift": {
        "max_param_ratio_vs_baseline": 0.50,
        "scoring": {
            "max_ratio_vs_baseline": 0.50,
        },
        "agent_weights": {
            "max_ratio_vs_baseline": 0.50,
        },
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


def normalize_strategy_family_name(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    if normalized in DEFAULT_STRATEGY_FAMILY_REGIME_HARD_FAIL_PROFILES:
        return normalized
    for family in DEFAULT_STRATEGY_FAMILY_REGIME_HARD_FAIL_PROFILES:
        if normalized.startswith(f"{family}_") or normalized.startswith(f"{family}-"):
            return family
    return normalized


def resolve_strategy_family_regime_hard_fail_profile(
    strategy_family: Any | None = None,
) -> dict[str, Any]:
    normalized = normalize_strategy_family_name(strategy_family)
    return deepcopy(
        dict(DEFAULT_STRATEGY_FAMILY_REGIME_HARD_FAIL_PROFILES.get(normalized) or {})
    )


def _apply_shared_regime_hard_fail_profile(override: dict[str, Any] | None) -> dict[str, Any]:
    payload = deepcopy(dict(override or {}))
    shared_regime_hard_fail = dict(payload.pop("shared_regime_hard_fail", {}) or {})
    if not shared_regime_hard_fail:
        return payload
    for scope_name in ("governance", "promotion"):
        scope_policy = dict(payload.get(scope_name) or {})
        scope_policy["regime_hard_fail"] = deep_merge(
            shared_regime_hard_fail,
            dict(scope_policy.get("regime_hard_fail") or {}),
        )
        payload[scope_name] = scope_policy
    return payload


def resolve_governance_matrix(
    *overrides: dict[str, Any] | None,
    strategy_family: Any | None = None,
) -> dict[str, Any]:
    matrix = deepcopy(DEFAULT_GOVERNANCE_MATRIX)
    family_profile = resolve_strategy_family_regime_hard_fail_profile(strategy_family)
    if family_profile:
        matrix = deep_merge(
            matrix,
            _apply_shared_regime_hard_fail_profile(
                {"shared_regime_hard_fail": family_profile}
            ),
        )
    for override in overrides:
        matrix = deep_merge(matrix, _apply_shared_regime_hard_fail_profile(override))
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


def normalize_proposal_gate_policy(
    policy: dict[str, Any] | None = None,
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return deep_merge(defaults or DEFAULT_PROPOSAL_GATE_POLICY, dict(policy or {}))


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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _record_dict(item: Any, field: str) -> dict[str, Any]:
    value = _item_field(item, field, {})
    return dict(value or {})


def _record_regime_name(item: Any) -> str:
    governance_decision = _record_dict(item, "governance_decision")
    routing_decision = _record_dict(item, "routing_decision")
    audit_tags = _record_dict(item, "audit_tags")
    regime = str(
        governance_decision.get("regime")
        or routing_decision.get("regime")
        or audit_tags.get("governance_regime")
        or audit_tags.get("routing_regime")
        or _item_field(item, "regime", "")
        or "unknown"
    ).strip()
    return regime or "unknown"


def _build_regime_performance_from_cycle_history(
    cycle_history: list[Any] | None = None,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in list(cycle_history or []):
        regime = _record_regime_name(item)
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
        return_pct = _safe_float(_item_field(item, "return_pct", 0.0), 0.0)
        is_profit = bool(_item_field(item, "is_profit", return_pct > 0.0))
        benchmark_passed = bool(_item_field(item, "benchmark_passed", False))
        bucket["cycles"] += 1
        bucket["return_sum"] += return_pct
        bucket["negative_contribution_pct"] += min(return_pct, 0.0)
        if is_profit:
            bucket["profit_cycles"] += 1
        if benchmark_passed:
            bucket["benchmark_pass_cycles"] += 1

    performance: dict[str, dict[str, Any]] = {}
    for regime, bucket in grouped.items():
        cycles = int(bucket.get("cycles", 0) or 0)
        if cycles <= 0:
            continue
        profit_cycles = int(bucket.get("profit_cycles", 0) or 0)
        benchmark_pass_cycles = int(bucket.get("benchmark_pass_cycles", 0) or 0)
        performance[regime] = {
            "cycles": cycles,
            "profit_cycles": profit_cycles,
            "loss_cycles": cycles - profit_cycles,
            "avg_return_pct": _safe_float(bucket.get("return_sum"), 0.0) / cycles,
            "win_rate": profit_cycles / cycles,
            "benchmark_pass_rate": benchmark_pass_cycles / cycles,
            "negative_contribution_pct": _safe_float(
                bucket.get("negative_contribution_pct"), 0.0
            ),
        }
    return performance


def evaluate_regime_hard_fail(
    regime_performance: dict[str, Any] | None,
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = deep_merge(DEFAULT_REGIME_HARD_FAIL_POLICY, dict(policy or {}))
    enabled = bool(config.get("enabled", True))
    critical_regimes = [
        str(item).strip()
        for item in list(config.get("critical_regimes") or [])
        if str(item).strip()
    ]
    if not critical_regimes:
        critical_regimes = list(
            DEFAULT_REGIME_HARD_FAIL_POLICY.get("critical_regimes") or []
        )

    checks: list[dict[str, Any]] = []
    failed_regimes: list[dict[str, Any]] = []
    performance = dict(regime_performance or {})
    for regime in critical_regimes:
        metrics = dict(performance.get(regime) or {})
        threshold = deep_merge(
            {
                "min_cycles": int(config.get("min_cycles", 2) or 2),
                "min_avg_return_pct": _safe_float(config.get("min_avg_return_pct"), -0.5),
                "max_benchmark_pass_rate": _safe_float(
                    config.get("max_benchmark_pass_rate"), 0.25
                ),
                "max_win_rate": _safe_float(config.get("max_win_rate"), 0.40),
            },
            dict(dict(config.get("per_regime") or {}).get(regime) or {}),
        )
        min_cycles = max(1, int(threshold.get("min_cycles", 2) or 2))
        min_avg_return_pct = _safe_float(threshold.get("min_avg_return_pct"), -0.5)
        max_benchmark_pass_rate = _safe_float(
            threshold.get("max_benchmark_pass_rate"), 0.25
        )
        max_win_rate = _safe_float(threshold.get("max_win_rate"), 0.40)
        cycles = int(metrics.get("cycles", 0) or 0)
        avg_return_pct = _safe_float(metrics.get("avg_return_pct"), 0.0)
        benchmark_pass_rate = _safe_float(metrics.get("benchmark_pass_rate"), 0.0)
        win_rate = _safe_float(metrics.get("win_rate"), 0.0)
        active = enabled and cycles >= min_cycles
        loss_cycles = int(metrics.get("loss_cycles", 0) or 0)
        actual = {
            "cycles": cycles,
            "avg_return_pct": avg_return_pct,
            "benchmark_pass_rate": benchmark_pass_rate,
            "win_rate": win_rate,
            "loss_cycles": loss_cycles,
            "loss_share": (loss_cycles / cycles) if cycles > 0 else 0.0,
            "negative_contribution_pct": _safe_float(
                metrics.get("negative_contribution_pct"), 0.0
            ),
        }
        max_loss_share = threshold.get("max_loss_share")
        min_negative_contribution_pct = threshold.get("min_negative_contribution_pct")
        loss_share_failed = True
        if max_loss_share is not None:
            loss_share_failed = actual["loss_share"] >= _safe_float(max_loss_share, 1.0)
        negative_contribution_failed = True
        if min_negative_contribution_pct is not None:
            negative_contribution_failed = actual["negative_contribution_pct"] <= _safe_float(
                min_negative_contribution_pct, 0.0
            )
        failed_metric_status = {
            "avg_return_pct": avg_return_pct <= min_avg_return_pct,
            "benchmark_pass_rate": benchmark_pass_rate <= max_benchmark_pass_rate,
            "win_rate": win_rate <= max_win_rate,
        }
        if max_loss_share is not None:
            failed_metric_status["loss_share"] = loss_share_failed
        if min_negative_contribution_pct is not None:
            failed_metric_status["negative_contribution_pct"] = (
                negative_contribution_failed
            )
        required_failed_metrics = [
            str(item).strip()
            for item in list(
                threshold.get("required_failed_metrics") or failed_metric_status.keys()
            )
            if str(item).strip() in failed_metric_status
        ]
        confirm_any_failed_metrics = [
            str(item).strip()
            for item in list(threshold.get("confirm_any_failed_metrics") or [])
            if str(item).strip() in failed_metric_status
        ]
        required_failed = all(
            bool(failed_metric_status.get(metric_name, False))
            for metric_name in required_failed_metrics
        )
        auxiliary_failed = (
            any(
                bool(failed_metric_status.get(metric_name, False))
                for metric_name in confirm_any_failed_metrics
            )
            if confirm_any_failed_metrics
            else True
        )
        hard_failed = active and required_failed and auxiliary_failed
        checks.append(
            {
                "name": f"regime_hard_fail.{regime}",
                "passed": not hard_failed,
                "active": active,
                "actual": actual,
                "threshold": threshold,
                "failed_metric_status": failed_metric_status,
                "required_failed_metrics": required_failed_metrics,
                "confirm_any_failed_metrics": confirm_any_failed_metrics,
            }
        )
        if hard_failed:
            failed_regimes.append({"regime": regime, **actual})

    failed_checks = [item for item in checks if not bool(item.get("passed", False))]
    return {
        "enabled": enabled,
        "passed": not failed_checks,
        "policy": config,
        "checks": checks,
        "failed_checks": failed_checks,
        "failed_regimes": failed_regimes,
        "failed_regime_names": [str(item.get("regime") or "") for item in failed_regimes],
        "regime_performance": performance,
    }


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
    strategy_family = (
        entry.get("strategy_family")
        or entry.get("manager_id")
        or entry.get("model_name")
        or entry.get("strategy_kind")
        or ""
    )
    matrix = resolve_governance_matrix(
        {"governance": dict(policy or {})},
        strategy_family=strategy_family,
    )
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

    regime_hard_fail = evaluate_regime_hard_fail(
        dict(entry.get("regime_performance") or {})
        or _build_regime_performance_from_cycle_history(
            list(entry.get("cycle_history") or [])
        ),
        policy=dict(config.get("regime_hard_fail") or {}),
    )
    checks.extend(list(regime_hard_fail.get("checks") or []))

    failed_checks = [item for item in checks if not bool(item.get("passed", False))]
    return {
        "passed": not failed_checks,
        "checks": checks,
        "failed_checks": failed_checks,
        "deployment_stage": deployment_stage,
        "regime_hard_fail": regime_hard_fail,
    }


# Shared indicator re-exports


_TIGHTENING_DIRECTION = {
    "stop_loss_pct": "lower",
    "trailing_pct": "lower",
}


def _proposal_copy_dict(value: Any) -> dict[str, Any]:
    return deepcopy(dict(value or {}))


def _load_config_payload(controller: Any, config_ref: str | Path) -> tuple[Path, dict[str, Any]]:
    normalized_ref = normalize_config_ref(config_ref) or str(config_ref or "").strip()
    model_mutator = getattr(controller, "model_mutator", None)
    if model_mutator is not None and hasattr(model_mutator, "load"):
        path, payload = model_mutator.load(normalized_ref)
        return Path(path), dict(payload or {})
    path = Path(normalized_ref)
    if path.exists():
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return path, dict(payload or {})
    return path, {}


def _load_generation_meta(path: Path) -> dict[str, Any]:
    meta_path = path.with_suffix(".json")
    if not meta_path.exists():
        return {}
    try:
        return dict(json.loads(meta_path.read_text(encoding="utf-8")) or {})
    except Exception:
        return {}


def _resolve_baseline_config(controller: Any, config_ref: str | Path) -> tuple[Path, dict[str, Any]]:
    current_path, current_payload = _load_config_payload(controller, config_ref)
    visited = {str(current_path)}
    while True:
        meta = _load_generation_meta(current_path)
        parent_meta = dict(meta.get("parent_meta") or {})
        next_ref = normalize_config_ref(
            parent_meta.get("baseline_config_ref")
            or meta.get("parent_config")
            or ""
        )
        if not next_ref:
            return current_path, current_payload
        next_path, next_payload = _load_config_payload(controller, next_ref)
        if str(next_path) in visited:
            return current_path, current_payload
        visited.add(str(next_path))
        current_path, current_payload = next_path, next_payload


def _resolve_current_runtime_params(
    controller: Any,
    proposal_bundle: dict[str, Any],
) -> dict[str, Any]:
    snapshot = _proposal_copy_dict(proposal_bundle.get("execution_snapshot") or {})
    runtime_params = _proposal_copy_dict(snapshot.get("runtime_overrides") or {})
    if runtime_params:
        return runtime_params
    proposals = list(proposal_bundle.get("proposals") or [])
    for proposal in proposals:
        active_snapshot = _proposal_copy_dict(
            dict(proposal or {}).get("active_params_snapshot") or {}
        )
        if active_snapshot:
            return active_snapshot
    return _proposal_copy_dict(getattr(controller, "current_params", {}) or {})


def _baseline_param_lookup(config_payload: dict[str, Any], key: str) -> Any:
    params = dict(config_payload.get("params") or {})
    if key in params:
        return params.get(key)
    risk = dict(config_payload.get("risk") or {})
    if key in risk:
        return risk.get(key)
    return None


def _change_ratio(current: Any, candidate: Any, baseline: Any) -> float | None:
    try:
        current_float = float(current)
        candidate_float = float(candidate)
        baseline_float = float(baseline)
    except (TypeError, ValueError):
        return None
    if abs(baseline_float) < 1e-9:
        return None if abs(candidate_float - current_float) < 1e-9 else float("inf")
    return abs(candidate_float - current_float) / abs(baseline_float)


def _drift_ratio(candidate: Any, baseline: Any) -> float | None:
    try:
        candidate_float = float(candidate)
        baseline_float = float(baseline)
    except (TypeError, ValueError):
        return None
    if abs(baseline_float) < 1e-9:
        return None if abs(candidate_float) < 1e-9 else float("inf")
    return abs(candidate_float - baseline_float) / abs(baseline_float)


def _config_section(config_payload: dict[str, Any], *section_names: str) -> dict[str, Any]:
    for section_name in section_names:
        section = config_payload.get(section_name)
        if isinstance(section, dict):
            return _proposal_copy_dict(section)
    return {}


def _flatten_patch_leaves(patch: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    leaves: dict[str, Any] = {}
    for key, value in dict(patch or {}).items():
        key_name = str(key)
        path = f"{prefix}.{key_name}" if prefix else key_name
        if isinstance(value, dict):
            nested = _flatten_patch_leaves(value, path)
            if nested:
                leaves.update(nested)
            else:
                leaves[path] = value
        else:
            leaves[path] = value
    return leaves


def _nested_lookup(payload: dict[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for key in str(dotted_path or "").split("."):
        if not isinstance(current, dict):
            return None
        if key not in current:
            return None
        current = current.get(key)
    return current


def _nested_assign(payload: dict[str, Any], dotted_path: str, value: Any) -> None:
    current = payload
    keys = [segment for segment in str(dotted_path or "").split(".") if segment]
    if not keys:
        return
    for key in keys[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[keys[-1]] = deepcopy(value)


def _proposal_patch_keys(proposal_kind: str, patch: dict[str, Any]) -> list[str]:
    if proposal_kind == "scoring_adjustment":
        return sorted(_flatten_patch_leaves(patch).keys())
    return sorted(str(key) for key in dict(patch or {}).keys())


def _resolve_scope_threshold(
    section: dict[str, Any],
    *,
    nested_key: str,
    flat_keys: list[str],
    default: float,
) -> float:
    nested = dict(section.get(nested_key) or {})
    candidates = [
        nested.get("max_single_step_ratio_vs_baseline"),
        nested.get("max_ratio_vs_baseline"),
    ]
    for key in flat_keys:
        candidates.append(section.get(key))
    for value in candidates:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return float(default)


def _scope_drift_thresholds(policy: dict[str, Any], scope_name: str) -> tuple[float, float]:
    identity_policy = dict(policy.get("identity_protection") or {})
    cumulative_policy = dict(policy.get("cumulative_drift") or {})
    if scope_name == "params":
        max_single_step_ratio = float(
            identity_policy.get("max_single_step_ratio_vs_baseline", 0.30) or 0.30
        )
        max_cumulative_ratio = float(
            cumulative_policy.get("max_param_ratio_vs_baseline", 0.50) or 0.50
        )
        return max_single_step_ratio, max_cumulative_ratio
    if scope_name == "scoring":
        return (
            _resolve_scope_threshold(
                identity_policy,
                nested_key="scoring",
                flat_keys=["max_scoring_single_step_ratio_vs_baseline"],
                default=0.30,
            ),
            _resolve_scope_threshold(
                cumulative_policy,
                nested_key="scoring",
                flat_keys=["max_scoring_ratio_vs_baseline"],
                default=0.50,
            ),
        )
    if scope_name == "agent_weights":
        return (
            _resolve_scope_threshold(
                identity_policy,
                nested_key="agent_weights",
                flat_keys=["max_agent_weight_single_step_ratio_vs_baseline"],
                default=0.30,
            ),
            _resolve_scope_threshold(
                cumulative_policy,
                nested_key="agent_weights",
                flat_keys=["max_agent_weight_ratio_vs_baseline"],
                default=0.50,
            ),
        )
    return 0.30, 0.50


def _scope_drift_reason(scope_name: str, reason: str) -> str:
    if scope_name == "params":
        return reason
    if reason == "single_step_identity_drift_exceeded":
        return f"single_step_{scope_name}_identity_drift_exceeded"
    if reason == "cumulative_identity_drift_exceeded":
        return f"cumulative_{scope_name}_identity_drift_exceeded"
    if reason == "cumulative_identity_drift_worsened":
        return f"cumulative_{scope_name}_identity_drift_worsened"
    return f"{scope_name}_{reason}"


def _evaluate_identity_drift(
    *,
    scope_name: str,
    current_value: Any,
    candidate_value: Any,
    baseline_value: Any,
    max_single_step_ratio: float,
    max_cumulative_ratio: float,
) -> tuple[dict[str, Any], str]:
    effective_current_value = baseline_value if current_value is None else current_value
    metric = {
        "baseline_value": baseline_value,
        "current_value": effective_current_value,
        "candidate_value": candidate_value,
    }
    if baseline_value is None or effective_current_value is None:
        return metric, ""
    single_step_ratio = _change_ratio(
        effective_current_value, candidate_value, baseline_value
    )
    current_drift_ratio = _drift_ratio(effective_current_value, baseline_value)
    candidate_drift_ratio = _drift_ratio(candidate_value, baseline_value)
    metric.update(
        {
            "single_step_ratio_vs_baseline": single_step_ratio,
            "current_drift_ratio_vs_baseline": current_drift_ratio,
            "candidate_drift_ratio_vs_baseline": candidate_drift_ratio,
        }
    )
    if single_step_ratio is not None and single_step_ratio > max_single_step_ratio:
        return metric, _scope_drift_reason(
            scope_name, "single_step_identity_drift_exceeded"
        )
    if candidate_drift_ratio is not None and current_drift_ratio is not None:
        if current_drift_ratio <= max_cumulative_ratio < candidate_drift_ratio:
            return metric, _scope_drift_reason(
                scope_name, "cumulative_identity_drift_exceeded"
            )
        if (
            current_drift_ratio > max_cumulative_ratio
            and candidate_drift_ratio > current_drift_ratio
        ):
            return metric, _scope_drift_reason(
                scope_name, "cumulative_identity_drift_worsened"
            )
    return metric, ""


def _is_tightening_param_change(key: str, current: Any, candidate: Any) -> bool:
    direction = _TIGHTENING_DIRECTION.get(str(key))
    try:
        current_float = float(current)
        candidate_float = float(candidate)
    except (TypeError, ValueError):
        return False
    if direction == "lower":
        return candidate_float < current_float
    return False


def _proposal_deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = _proposal_copy_dict(base)
    for key, value in dict(patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _proposal_deep_merge(dict(merged.get(key) or {}), value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _proposal_kind(proposal: dict[str, Any]) -> str:
    metadata = dict(proposal.get("metadata") or {})
    return str(metadata.get("proposal_kind") or "")


def _append_reason_count(counter: dict[str, int], reason: str) -> None:
    label = str(reason or "unknown").strip() or "unknown"
    counter[label] = counter.get(label, 0) + 1


def evaluate_candidate_proposal_gate(
    controller: Any,
    *,
    cycle_id: int,
    proposal_bundle: dict[str, Any],
) -> dict[str, Any]:
    policy = normalize_proposal_gate_policy(
        dict(getattr(controller, "proposal_gate_policy", {}) or {})
    )
    proposals = [
        dict(item or {})
        for item in list(proposal_bundle.get("proposals") or [])
        if str(dict(item or {}).get("target_scope") or "candidate") == "candidate"
    ]
    if not bool(policy.get("enabled", True)):
        raw_active_runtime_config_ref = str(
            proposal_bundle.get("active_runtime_config_ref")
            or proposal_bundle.get("active_config_ref")
            or getattr(controller, "manager_runtime_config_ref", "")
            or getattr(controller, "model_config_path", "")
            or ""
        ).strip()
        active_runtime_config_ref = (
            normalize_config_ref(raw_active_runtime_config_ref)
            or raw_active_runtime_config_ref
        )
        requested_source_summary: dict[str, int] = {}
        requested_refs: list[str] = []
        filtered_params: dict[str, Any] = {}
        filtered_scoring: dict[str, Any] = {}
        filtered_agent_weights: dict[str, Any] = {}
        for proposal in proposals:
            proposal_id = str(proposal.get("proposal_id") or "")
            if proposal_id:
                requested_refs.append(proposal_id)
            source = str(proposal.get("source") or "unknown")
            requested_source_summary[source] = (
                requested_source_summary.get(source, 0) + 1
            )
            patch = dict(proposal.get("patch") or {})
            kind = _proposal_kind(proposal)
            if kind in {"runtime_param_adjustment", "param_adjustment"}:
                filtered_params.update(patch)
            elif kind == "scoring_adjustment":
                filtered_scoring = _proposal_deep_merge(filtered_scoring, patch)
            elif kind == "agent_weight_adjustment":
                filtered_agent_weights.update(patch)
        return {
            "approved": bool(filtered_params or filtered_scoring or filtered_agent_weights),
            "cycle_id": int(cycle_id),
            "policy": policy,
            "profit_context": {
                "is_profit": False,
                "return_pct": None,
                "benchmark_passed": False,
            },
            "baseline": {
                "config_ref": active_runtime_config_ref,
                "model_kind": "",
                "active_config_ref": active_runtime_config_ref,
            },
            "filtered_adjustments": {
                "params": filtered_params,
                "scoring": filtered_scoring,
                "agent_weights": filtered_agent_weights,
                "proposal_refs": requested_refs,
                "proposal_source_summary": requested_source_summary,
            },
            "blocked_adjustments": {
                "params": {},
                "scoring": {},
                "agent_weights": {},
            },
            "violations": [],
            "drift_summary": {
                "approved_params": {},
                "blocked_params": {},
                "approved_scoring": {},
                "blocked_scoring": {},
                "approved_agent_weights": {},
                "blocked_agent_weights": {},
                "max_single_step_ratio_vs_baseline": None,
                "max_param_drift_ratio_vs_baseline": None,
                "max_scoring_single_step_ratio_vs_baseline": None,
                "max_scoring_drift_ratio_vs_baseline": None,
                "max_agent_weight_single_step_ratio_vs_baseline": None,
                "max_agent_weight_drift_ratio_vs_baseline": None,
            },
            "approved_proposals": [
                {
                    "proposal_id": str(item.get("proposal_id") or ""),
                    "source": str(item.get("source") or "unknown"),
                    "proposal_kind": _proposal_kind(item),
                    "requested_keys": _proposal_patch_keys(
                        _proposal_kind(item),
                        dict(item.get("patch") or {}),
                    ),
                    "approved_keys": _proposal_patch_keys(
                        _proposal_kind(item),
                        dict(item.get("patch") or {}),
                    ),
                    "blocked_keys": [],
                    "status": "approved",
                    "block_reasons": [],
                }
                for item in proposals
            ],
            "blocked_proposals": [],
            "proposal_summary": {
                "requested_proposal_count": len(proposals),
                "approved_proposal_count": len(proposals),
                "blocked_proposal_count": 0,
                "partially_blocked_proposal_count": 0,
                "requested_proposal_refs": requested_refs,
                "approved_proposal_refs": requested_refs,
                "blocked_proposal_refs": [],
                "requested_source_summary": requested_source_summary,
                "approved_source_summary": requested_source_summary,
                "blocked_source_summary": {},
                "block_reason_counts": {},
                "top_block_reasons": [],
            },
        }

    filtered_params: dict[str, Any] = {}
    filtered_scoring: dict[str, Any] = {}
    filtered_agent_weights: dict[str, Any] = {}
    blocked_params: dict[str, Any] = {}
    blocked_scoring: dict[str, Any] = {}
    blocked_agent_weights: dict[str, Any] = {}
    violations: list[dict[str, Any]] = []

    raw_active_runtime_config_ref = str(
        proposal_bundle.get("active_runtime_config_ref")
        or proposal_bundle.get("active_config_ref")
        or getattr(controller, "manager_runtime_config_ref", "")
        or getattr(controller, "model_config_path", "")
        or ""
    ).strip()
    active_runtime_config_ref = (
        normalize_config_ref(raw_active_runtime_config_ref)
        or raw_active_runtime_config_ref
    )
    current_path, current_payload = _load_config_payload(
        controller, active_runtime_config_ref
    )
    baseline_path, baseline_payload = _resolve_baseline_config(
        controller, active_runtime_config_ref
    )
    current_params = _resolve_current_runtime_params(controller, proposal_bundle)
    effective_params = _proposal_copy_dict(current_params)
    current_scoring = _config_section(current_payload, "summary_scoring", "scoring")
    baseline_scoring = _config_section(baseline_payload, "summary_scoring", "scoring")
    effective_scoring = _proposal_copy_dict(current_scoring)
    current_agent_weights = _config_section(current_payload, "agent_weights")
    baseline_agent_weights = _config_section(baseline_payload, "agent_weights")
    effective_agent_weights = _proposal_copy_dict(current_agent_weights)
    protected_params = {
        str(item)
        for item in list(policy.get("protected_params") or [])
        if str(item).strip()
    }
    profitable_cycle_policy = dict(policy.get("profitable_cycle") or {})
    allowed_safety_tightening_params = {
        str(item)
        for item in list(
            profitable_cycle_policy.get("allowed_safety_tightening_params") or []
        )
        if str(item).strip()
    }
    execution_snapshot = _proposal_copy_dict(proposal_bundle.get("execution_snapshot") or {})
    return_pct = execution_snapshot.get("return_pct")
    is_profit = bool(execution_snapshot.get("is_profit", False))
    if not is_profit:
        try:
            is_profit = float(return_pct or 0.0) > 0.0
        except (TypeError, ValueError):
            is_profit = False

    approved_param_metrics: dict[str, Any] = {}
    blocked_param_metrics: dict[str, Any] = {}
    approved_scoring_metrics: dict[str, Any] = {}
    blocked_scoring_metrics: dict[str, Any] = {}
    approved_agent_weight_metrics: dict[str, Any] = {}
    blocked_agent_weight_metrics: dict[str, Any] = {}
    max_single_step_ratio, max_cumulative_ratio = _scope_drift_thresholds(policy, "params")
    scoring_max_single_step_ratio, scoring_max_cumulative_ratio = _scope_drift_thresholds(
        policy,
        "scoring",
    )
    agent_weight_max_single_step_ratio, agent_weight_max_cumulative_ratio = (
        _scope_drift_thresholds(policy, "agent_weights")
    )
    approved_proposals: list[dict[str, Any]] = []
    blocked_proposals: list[dict[str, Any]] = []
    requested_source_summary: dict[str, int] = {}
    approved_source_summary: dict[str, int] = {}
    blocked_source_summary: dict[str, int] = {}
    block_reason_counts: dict[str, int] = {}
    approved_proposal_refs: list[str] = []
    blocked_proposal_refs: list[str] = []
    partial_blocked_count = 0

    for proposal in proposals:
        proposal_id = str(proposal.get("proposal_id") or "")
        source = str(proposal.get("source") or "unknown")
        proposal_kind = _proposal_kind(proposal)
        patch = dict(proposal.get("patch") or {})
        if not patch:
            continue
        requested_source_summary[source] = requested_source_summary.get(source, 0) + 1
        requested_keys = _proposal_patch_keys(proposal_kind, patch)
        approved_patch: dict[str, Any] = {}
        blocked_patch: dict[str, Any] = {}
        proposal_block_reasons: list[str] = []

        if proposal_kind in {"runtime_param_adjustment", "param_adjustment"}:
            for key, candidate_value in patch.items():
                current_value = effective_params.get(key)
                baseline_value = _baseline_param_lookup(baseline_payload, key)
                metric = {
                    "baseline_value": baseline_value,
                    "current_value": current_value,
                    "candidate_value": candidate_value,
                }
                block_reason = ""
                if is_profit:
                    if key in allowed_safety_tightening_params:
                        if not _is_tightening_param_change(
                            key, current_value, candidate_value
                        ):
                            block_reason = (
                                "profitable_cycle_requires_safety_tightening"
                            )
                    elif bool(
                        profitable_cycle_policy.get("freeze_behavior_params", True)
                    ):
                        block_reason = "profitable_cycle_behavior_frozen"
                if (
                    not block_reason
                    and key in protected_params
                    and baseline_value is not None
                    and current_value is not None
                ):
                    single_step_ratio = _change_ratio(
                        current_value, candidate_value, baseline_value
                    )
                    current_drift_ratio = _drift_ratio(current_value, baseline_value)
                    candidate_drift_ratio = _drift_ratio(candidate_value, baseline_value)
                    metric.update(
                        {
                            "single_step_ratio_vs_baseline": single_step_ratio,
                            "current_drift_ratio_vs_baseline": current_drift_ratio,
                            "candidate_drift_ratio_vs_baseline": candidate_drift_ratio,
                        }
                    )
                    if (
                        single_step_ratio is not None
                        and single_step_ratio > max_single_step_ratio
                    ):
                        block_reason = "single_step_identity_drift_exceeded"
                    elif (
                        candidate_drift_ratio is not None
                        and current_drift_ratio is not None
                    ):
                        if current_drift_ratio <= max_cumulative_ratio < candidate_drift_ratio:
                            block_reason = "cumulative_identity_drift_exceeded"
                        elif (
                            current_drift_ratio > max_cumulative_ratio
                            and candidate_drift_ratio > current_drift_ratio
                        ):
                            block_reason = "cumulative_identity_drift_worsened"
                if block_reason:
                    blocked_patch[key] = candidate_value
                    blocked_params[key] = candidate_value
                    blocked_param_metrics[key] = dict(metric, block_reason=block_reason)
                    proposal_block_reasons.append(block_reason)
                    _append_reason_count(block_reason_counts, block_reason)
                    violations.append(
                        {
                            "type": block_reason,
                            "proposal_id": proposal_id,
                            "source": source,
                            "param": key,
                            "current_value": current_value,
                            "candidate_value": candidate_value,
                        }
                    )
                    continue
                approved_patch[key] = candidate_value
                filtered_params[key] = candidate_value
                effective_params[key] = candidate_value
                approved_param_metrics[key] = metric
        elif proposal_kind == "scoring_adjustment":
            if (
                bool(profitable_cycle_policy.get("block_scoring_adjustments", True))
                and is_profit
            ):
                blocked_patch = _proposal_deep_merge(blocked_patch, patch)
                blocked_scoring = _proposal_deep_merge(blocked_scoring, patch)
                proposal_block_reasons.append("profitable_cycle_scoring_frozen")
                _append_reason_count(
                    block_reason_counts, "profitable_cycle_scoring_frozen"
                )
                violations.append(
                    {
                        "type": "profitable_cycle_scoring_frozen",
                        "proposal_id": proposal_id,
                        "source": source,
                        "keys": requested_keys,
                    }
                )
            else:
                for key_path, candidate_value in _flatten_patch_leaves(patch).items():
                    metric, block_reason = _evaluate_identity_drift(
                        scope_name="scoring",
                        current_value=_nested_lookup(effective_scoring, key_path),
                        candidate_value=candidate_value,
                        baseline_value=_nested_lookup(baseline_scoring, key_path),
                        max_single_step_ratio=scoring_max_single_step_ratio,
                        max_cumulative_ratio=scoring_max_cumulative_ratio,
                    )
                    if block_reason:
                        _nested_assign(blocked_patch, key_path, candidate_value)
                        _nested_assign(blocked_scoring, key_path, candidate_value)
                        blocked_scoring_metrics[key_path] = dict(
                            metric, block_reason=block_reason
                        )
                        proposal_block_reasons.append(block_reason)
                        _append_reason_count(block_reason_counts, block_reason)
                        violations.append(
                            {
                                "type": block_reason,
                                "proposal_id": proposal_id,
                                "source": source,
                                "scoring_key": key_path,
                                "current_value": metric.get("current_value"),
                                "candidate_value": candidate_value,
                            }
                        )
                        continue
                    _nested_assign(approved_patch, key_path, candidate_value)
                    _nested_assign(filtered_scoring, key_path, candidate_value)
                    _nested_assign(effective_scoring, key_path, candidate_value)
                    approved_scoring_metrics[key_path] = metric
        elif proposal_kind == "agent_weight_adjustment":
            if (
                bool(
                    profitable_cycle_policy.get("block_agent_weight_adjustments", True)
                )
                and is_profit
            ):
                blocked_patch.update(patch)
                blocked_agent_weights.update(patch)
                proposal_block_reasons.append("profitable_cycle_agent_weights_frozen")
                _append_reason_count(
                    block_reason_counts, "profitable_cycle_agent_weights_frozen"
                )
                violations.append(
                    {
                        "type": "profitable_cycle_agent_weights_frozen",
                        "proposal_id": proposal_id,
                        "source": source,
                        "keys": requested_keys,
                    }
                )
            else:
                for agent_name, candidate_value in patch.items():
                    metric, block_reason = _evaluate_identity_drift(
                        scope_name="agent_weights",
                        current_value=effective_agent_weights.get(agent_name),
                        candidate_value=candidate_value,
                        baseline_value=baseline_agent_weights.get(agent_name),
                        max_single_step_ratio=agent_weight_max_single_step_ratio,
                        max_cumulative_ratio=agent_weight_max_cumulative_ratio,
                    )
                    if block_reason:
                        blocked_patch[agent_name] = candidate_value
                        blocked_agent_weights[agent_name] = candidate_value
                        blocked_agent_weight_metrics[agent_name] = dict(
                            metric, block_reason=block_reason
                        )
                        proposal_block_reasons.append(block_reason)
                        _append_reason_count(block_reason_counts, block_reason)
                        violations.append(
                            {
                                "type": block_reason,
                                "proposal_id": proposal_id,
                                "source": source,
                                "agent": agent_name,
                                "current_value": metric.get("current_value"),
                                "candidate_value": candidate_value,
                            }
                        )
                        continue
                    approved_patch[agent_name] = candidate_value
                    filtered_agent_weights[agent_name] = candidate_value
                    effective_agent_weights[agent_name] = candidate_value
                    approved_agent_weight_metrics[agent_name] = metric
        else:
            blocked_patch.update(patch)
            proposal_block_reasons.append("unsupported_proposal_kind")
            _append_reason_count(block_reason_counts, "unsupported_proposal_kind")
            violations.append(
                {
                    "type": "unsupported_proposal_kind",
                    "proposal_id": proposal_id,
                    "source": source,
                    "proposal_kind": proposal_kind,
                }
            )

        proposal_record = {
            "proposal_id": proposal_id,
            "source": source,
            "proposal_kind": proposal_kind,
            "requested_keys": requested_keys,
            "approved_keys": _proposal_patch_keys(proposal_kind, approved_patch),
            "blocked_keys": _proposal_patch_keys(proposal_kind, blocked_patch),
            "status": "approved",
            "block_reasons": sorted(set(proposal_block_reasons)),
        }
        if blocked_patch and approved_patch:
            proposal_record["status"] = "partially_blocked"
            partial_blocked_count += 1
        elif blocked_patch:
            proposal_record["status"] = "blocked"
        if approved_patch:
            approved_proposals.append(deepcopy(proposal_record))
            approved_source_summary[source] = approved_source_summary.get(source, 0) + 1
            if proposal_id:
                approved_proposal_refs.append(proposal_id)
        if blocked_patch:
            blocked_proposals.append(deepcopy(proposal_record))
            blocked_source_summary[source] = blocked_source_summary.get(source, 0) + 1
            if proposal_id:
                blocked_proposal_refs.append(proposal_id)

    approved = bool(filtered_params or filtered_scoring or filtered_agent_weights)
    top_block_reasons = [
        reason
        for reason, _count in sorted(
            block_reason_counts.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )[:3]
    ]
    return {
        "approved": approved,
        "cycle_id": int(cycle_id),
        "policy": policy,
        "profit_context": {
            "is_profit": is_profit,
            "return_pct": return_pct,
            "benchmark_passed": bool(execution_snapshot.get("benchmark_passed", False)),
        },
        "baseline": {
            "config_ref": str(baseline_path),
            "model_kind": str(baseline_payload.get("kind") or ""),
            "active_config_ref": str(current_path),
        },
        "filtered_adjustments": {
            "params": filtered_params,
            "scoring": filtered_scoring,
            "agent_weights": filtered_agent_weights,
            "proposal_refs": approved_proposal_refs,
            "proposal_source_summary": approved_source_summary,
        },
        "blocked_adjustments": {
            "params": blocked_params,
            "scoring": blocked_scoring,
            "agent_weights": blocked_agent_weights,
        },
        "violations": violations,
        "drift_summary": {
            "approved_params": approved_param_metrics,
            "blocked_params": blocked_param_metrics,
            "approved_scoring": approved_scoring_metrics,
            "blocked_scoring": blocked_scoring_metrics,
            "approved_agent_weights": approved_agent_weight_metrics,
            "blocked_agent_weights": blocked_agent_weight_metrics,
            "max_single_step_ratio_vs_baseline": max_single_step_ratio,
            "max_param_drift_ratio_vs_baseline": max_cumulative_ratio,
            "max_scoring_single_step_ratio_vs_baseline": scoring_max_single_step_ratio,
            "max_scoring_drift_ratio_vs_baseline": scoring_max_cumulative_ratio,
            "max_agent_weight_single_step_ratio_vs_baseline": agent_weight_max_single_step_ratio,
            "max_agent_weight_drift_ratio_vs_baseline": agent_weight_max_cumulative_ratio,
        },
        "approved_proposals": approved_proposals,
        "blocked_proposals": blocked_proposals,
        "proposal_summary": {
            "requested_proposal_count": len(proposals),
            "approved_proposal_count": len(approved_proposals),
            "blocked_proposal_count": len(blocked_proposals),
            "partially_blocked_proposal_count": partial_blocked_count,
            "requested_proposal_refs": [
                str(item.get("proposal_id") or "")
                for item in proposals
                if str(item.get("proposal_id") or "")
            ],
            "approved_proposal_refs": approved_proposal_refs,
            "blocked_proposal_refs": blocked_proposal_refs,
            "requested_source_summary": requested_source_summary,
            "approved_source_summary": approved_source_summary,
            "blocked_source_summary": blocked_source_summary,
            "block_reason_counts": block_reason_counts,
            "top_block_reasons": top_block_reasons,
        },
    }



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
