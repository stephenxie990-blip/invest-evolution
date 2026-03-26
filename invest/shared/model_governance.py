from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any


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
        "regime_hard_fail": deepcopy(DEFAULT_REGIME_HARD_FAIL_POLICY),
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
        "regime_hard_fail": deepcopy(DEFAULT_REGIME_HARD_FAIL_POLICY),
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
    "regime_hard_fail": deepcopy(DEFAULT_REGIME_HARD_FAIL_POLICY),
    "research_feedback": {
        "min_episode_count": 5,
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
        "min_episode_count": 8,
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

_CANDIDATE_BUILD_STAGE_ALIASES: dict[str, str] = {
    "candidate_build": "candidate_build",
    "candidate_build_skipped": "candidate_build_skipped",
    "yaml_mutation": "candidate_build",
    "yaml_mutation_skipped": "candidate_build_skipped",
}

_CANDIDATE_BUILD_SOURCE_ALIASES: dict[str, str] = {
    "runtime_candidate_builder": "runtime_candidate_builder",
    "runtime_yaml_mutation": "runtime_candidate_builder",
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
    for scope_name in ("routing", "promotion"):
        scope_policy = dict(payload.get(scope_name) or {})
        scope_policy["regime_hard_fail"] = deep_merge(
            shared_regime_hard_fail,
            dict(scope_policy.get("regime_hard_fail") or {}),
        )
        payload[scope_name] = scope_policy
    return payload


def resolve_model_governance_matrix(
    *overrides: dict[str, Any] | None,
    strategy_family: Any | None = None,
) -> dict[str, Any]:
    matrix = deepcopy(DEFAULT_MODEL_GOVERNANCE_MATRIX)
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
    resolved_defaults = deepcopy(defaults or DEFAULT_PROMOTION_GATE_POLICY)
    resolved_policy = deepcopy(dict(policy or {}))
    if "research_feedback" in resolved_defaults:
        resolved_defaults["research_feedback"] = normalize_research_feedback_gate_policy(
            resolved_defaults.get("research_feedback")
        )
    if "research_feedback" in resolved_policy:
        resolved_policy["research_feedback"] = normalize_research_feedback_gate_policy(
            resolved_policy.get("research_feedback")
        )
    return deep_merge(resolved_defaults, resolved_policy)


def normalize_freeze_gate_policy(
    policy: dict[str, Any] | None = None,
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_defaults = deepcopy(defaults or DEFAULT_FREEZE_GATE_POLICY)
    resolved_policy = deepcopy(dict(policy or {}))
    if "research_feedback" in resolved_defaults:
        resolved_defaults["research_feedback"] = normalize_research_feedback_gate_policy(
            resolved_defaults.get("research_feedback")
        )
    if "research_feedback" in resolved_policy:
        resolved_policy["research_feedback"] = normalize_research_feedback_gate_policy(
            resolved_policy.get("research_feedback")
        )
    return deep_merge(resolved_defaults, resolved_policy)


def normalize_research_feedback_gate_policy(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = deepcopy(dict(policy or {}))
    legacy_min_sample_count = resolved.pop("min_sample_count", None)
    if legacy_min_sample_count is not None and resolved.get("min_episode_count") is None:
        resolved["min_episode_count"] = legacy_min_sample_count
    return resolved


def normalize_proposal_gate_policy(
    policy: dict[str, Any] | None = None,
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return deep_merge(defaults or DEFAULT_PROPOSAL_GATE_POLICY, dict(policy or {}))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _record_field(item: Any, field: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(field, default)
    return getattr(item, field, default)


def _record_dict(item: Any, field: str) -> dict[str, Any]:
    value = _record_field(item, field, {})
    return dict(value or {})


def _record_regime_name(item: Any) -> str:
    routing_decision = _record_dict(item, "routing_decision")
    audit_tags = _record_dict(item, "audit_tags")
    regime = str(
        routing_decision.get("regime")
        or audit_tags.get("routing_regime")
        or _record_field(item, "regime", "")
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
        return_pct = _safe_float(_record_field(item, "return_pct", 0.0), 0.0)
        is_profit = bool(_record_field(item, "is_profit", return_pct > 0.0))
        benchmark_passed = bool(_record_field(item, "benchmark_passed", False))
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
            "negative_contribution_pct": _safe_float(bucket.get("negative_contribution_pct"), 0.0),
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
        critical_regimes = list(DEFAULT_REGIME_HARD_FAIL_POLICY.get("critical_regimes") or [])

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
                    config.get("max_benchmark_pass_rate"),
                    0.25,
                ),
                "max_win_rate": _safe_float(config.get("max_win_rate"), 0.40),
            },
            dict(dict(config.get("per_regime") or {}).get(regime) or {}),
        )
        min_cycles = max(1, int(threshold.get("min_cycles", 2) or 2))
        min_avg_return_pct = _safe_float(threshold.get("min_avg_return_pct"), -0.5)
        max_benchmark_pass_rate = _safe_float(threshold.get("max_benchmark_pass_rate"), 0.25)
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
                metrics.get("negative_contribution_pct"),
                0.0,
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
                min_negative_contribution_pct,
                0.0,
            )
        failed_metric_status = {
            "avg_return_pct": avg_return_pct <= min_avg_return_pct,
            "benchmark_pass_rate": benchmark_pass_rate <= max_benchmark_pass_rate,
            "win_rate": win_rate <= max_win_rate,
        }
        if max_loss_share is not None:
            failed_metric_status["loss_share"] = loss_share_failed
        if min_negative_contribution_pct is not None:
            failed_metric_status["negative_contribution_pct"] = negative_contribution_failed
        required_failed_metrics = [
            str(item).strip()
            for item in list(threshold.get("required_failed_metrics") or failed_metric_status.keys())
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
            any(bool(failed_metric_status.get(metric_name, False)) for metric_name in confirm_any_failed_metrics)
            if confirm_any_failed_metrics
            else True
        )
        hard_failed = (
            active
            and required_failed
            and auxiliary_failed
        )
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
            failed_regimes.append(
                {
                    "regime": regime,
                    **actual,
                }
            )

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


def canonicalize_candidate_build_stage(stage: Any) -> str:
    return _CANDIDATE_BUILD_STAGE_ALIASES.get(str(stage or "").strip(), str(stage or "").strip())


def is_candidate_build_stage(stage: Any) -> bool:
    return canonicalize_candidate_build_stage(stage) in {
        "candidate_build",
        "candidate_build_skipped",
    }


def latest_candidate_build_event(
    optimization_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    for event in reversed(list(optimization_events or [])):
        if is_candidate_build_stage(event.get("stage")):
            payload = dict(event)
            payload["stage"] = canonicalize_candidate_build_stage(payload.get("stage"))
            decision = dict(payload.get("decision") or {})
            payload["decision"] = decision
            return payload
    return {}


def canonicalize_candidate_build_source(source: Any) -> str:
    return _CANDIDATE_BUILD_SOURCE_ALIASES.get(str(source or "").strip(), str(source or "").strip())


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


def latest_open_candidate_record(cycle_history: list[Any] | None = None) -> dict[str, Any]:
    for item in reversed(list(cycle_history or [])):
        lineage_record = dict(
            item.get("lineage_record", {})
            if isinstance(item, dict)
            else getattr(item, "lineage_record", {})
            or {}
        )
        lineage_status = str(lineage_record.get("lineage_status") or "")
        if lineage_status in {
            "candidate_pruned",
            "candidate_expired",
            "candidate_applied",
            "override_expired",
        }:
            continue
        deployment_stage = str(lineage_record.get("deployment_stage") or "")
        if deployment_stage != "candidate" and lineage_status != "candidate_pending":
            continue
        candidate_config_ref = normalize_config_ref(lineage_record.get("candidate_config_ref") or "")
        if not candidate_config_ref:
            continue
        return {
            "candidate_config_ref": candidate_config_ref,
            "candidate_version_id": str(lineage_record.get("candidate_version_id") or ""),
            "candidate_runtime_fingerprint": str(
                lineage_record.get("candidate_runtime_fingerprint") or ""
            ),
            "candidate_meta_ref": str(lineage_record.get("candidate_meta_ref") or ""),
            "cycle_id": int(
                lineagerecord_cycle_id
                if (lineagerecord_cycle_id := lineage_record.get("cycle_id")) is not None
                else (item.get("cycle_id") if isinstance(item, dict) else getattr(item, "cycle_id", 0))
                or 0
            ),
        }
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
    payload = dict(run_context or {})
    strategy_family = (
        payload.get("strategy_family")
        or payload.get("model_name")
        or payload.get("strategy_kind")
        or ""
    )
    matrix = resolve_model_governance_matrix(
        {"promotion": dict(policy or {})},
        strategy_family=strategy_family,
    )
    config = dict(matrix.get("promotion") or {})
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
    regime_hard_fail = evaluate_regime_hard_fail(
        _build_regime_performance_from_cycle_history(cycle_history),
        policy=dict(config.get("regime_hard_fail") or {}),
    )

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
        feedback_evidence_count = int(research_feedback.get("episode_count") or 0) or int(
            research_feedback.get("sample_count") or 0
        )
        if (
            feedback_bias in blocked_feedback_biases
            and feedback_evidence_count >= int(config.get("min_feedback_samples", 5) or 5)
        ):
            status = "candidate_pruned"
            violations.append("blocked_research_feedback")
            discipline_actions.append("prune_feedback_blocked_candidate")
        if regime_hard_fail.get("failed_regimes"):
            status = "candidate_pruned"
            for regime_name in regime_hard_fail.get("failed_regime_names") or []:
                violation_name = f"regime_hard_fail.{regime_name}"
                if violation_name not in violations:
                    violations.append(violation_name)
            if "prune_regime_hard_fail_candidate" not in discipline_actions:
                discipline_actions.append("prune_regime_hard_fail_candidate")
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
        "regime_hard_fail": regime_hard_fail,
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
    strategy_family = (
        entry.get("strategy_family")
        or entry.get("model_name")
        or entry.get("strategy_kind")
        or ""
    )
    matrix = resolve_model_governance_matrix(
        {"routing": dict(policy or {})},
        strategy_family=strategy_family,
    )
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

    regime_hard_fail = evaluate_regime_hard_fail(
        dict(entry.get("regime_performance") or {}),
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
