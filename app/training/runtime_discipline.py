from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import logging
import math
from typing import Any

from app.training.suggestion_tracking import ensure_proposal_tracking_fields

logger = logging.getLogger(__name__)

BUDGET_RUNTIME_KEYS = frozenset({"position_size", "cash_reserve", "max_positions"})
SAFETY_RUNTIME_KEYS = frozenset(
    {
        "emergency_stop_loss",
        "max_total_exposure_override",
        "max_position_size_override",
        "force_reduce_position",
        "kill_switch",
        "liquidity_guard_threshold",
    }
)
REGIME_NAMES = frozenset({"bull", "bear", "oscillation"})
ENTRY_THRESHOLD_KEYS = (
    "signal_threshold",
    "min_reversion_score",
    "min_value_quality_score",
    "min_defensive_score",
)
DEFAULT_STRATEGY_FAMILY_REGIME_BUDGETS: dict[str, dict[str, dict[str, Any]]] = {
    "momentum": {
        "bull": {"position_size": 0.22, "cash_reserve": 0.15, "max_positions": 4},
        "bear": {"position_size": 0.10, "cash_reserve": 0.45, "max_positions": 2},
        "oscillation": {"position_size": 0.16, "cash_reserve": 0.28, "max_positions": 3},
    },
    "mean_reversion": {
        "bull": {"position_size": 0.14, "cash_reserve": 0.40, "max_positions": 3},
        "bear": {"position_size": 0.15, "cash_reserve": 0.38, "max_positions": 3},
        "oscillation": {"position_size": 0.18, "cash_reserve": 0.30, "max_positions": 4},
    },
    "defensive_low_vol": {
        "bull": {"position_size": 0.16, "cash_reserve": 0.30, "max_positions": 4},
        "bear": {"position_size": 0.15, "cash_reserve": 0.40, "max_positions": 3},
        "oscillation": {"position_size": 0.17, "cash_reserve": 0.32, "max_positions": 4},
    },
    "value_quality": {
        "bull": {"position_size": 0.18, "cash_reserve": 0.22, "max_positions": 4},
        "bear": {"position_size": 0.12, "cash_reserve": 0.40, "max_positions": 2},
        "oscillation": {"position_size": 0.16, "cash_reserve": 0.28, "max_positions": 3},
    },
}
FAMILY_SPECIFIC_BUDGET_CORRECTION_WINDOW = 6
FAMILY_SPECIFIC_BUDGET_CORRECTION_MIN_REPEAT = 2
FAMILY_SPECIFIC_BUDGET_CORRECTIONS: dict[str, dict[str, dict[str, dict[str, Any]]]] = {
    "mean_reversion": {
        "oscillation": {
            "false_rebound_entry": {
                "deltas": {"position_size": -0.02, "cash_reserve": 0.05, "max_positions": -1},
                "reason": "局部反弹被误判时，先降单笔风险、提高现金缓冲，并减少并行回归仓位。",
            },
            "chop_stopout": {
                "deltas": {"position_size": -0.01, "cash_reserve": 0.02, "max_positions": 1},
                "reason": "噪音洗出说明单笔波动承压，适合轻仓但更分散，而不是继续把仓位压死。",
            },
            "overcrowded_reversion_book": {
                "deltas": {"position_size": -0.01, "cash_reserve": 0.04, "max_positions": -1},
                "repeat_scale": {"per_extra_repeat": 0.20, "max_multiplier": 2.0},
                "reason": "重复回归亏损更像组合过满，需要减少同时展开的回归仓位。",
            },
            "slow_reversion_timeout": {
                "deltas": {"position_size": -0.01, "cash_reserve": 0.03, "max_positions": -1},
                "reason": "回归兑现慢时，应降低暴露密度，给组合留出等待空间。",
            },
        },
    },
    "value_quality": {
        "oscillation": {
            "quality_trap_in_range": {
                "deltas": {"position_size": -0.01, "cash_reserve": 0.04, "max_positions": -1},
                "reason": "质量陷阱说明震荡里缺乏价格确认，应先收缩暴露而不是继续摊大组合。",
            },
            "defensive_lag": {
                "deltas": {"position_size": 0.02, "cash_reserve": -0.04, "max_positions": 1},
                "reason": "防守过重时需要温和放开预算，让价值质量因子有机会兑现。",
            },
            "concentration_mismatch": {
                "deltas": {"position_size": -0.02, "cash_reserve": 0.02, "max_positions": 1},
                "reason": "集中度过高时先降单仓、略增持仓数，避免少数持仓拖累整轮。",
            },
            "diluted_edge": {
                "deltas": {"position_size": 0.01, "cash_reserve": -0.02, "max_positions": -1},
                "reason": "边际优势被摊薄时，适合更聚焦地分配预算，而不是继续稀释仓位。",
            },
        },
    },
}


def _copy_dict(value: Any) -> dict[str, Any]:
    return deepcopy(dict(value or {}))


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return number


def _policy_lookup(policy: dict[str, Any] | None, path: str, default: Any) -> Any:
    current: Any = dict(policy or {})
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _normalize_regime(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in REGIME_NAMES else "unknown"


def _resolve_controller_regime_controls(controller: Any) -> dict[str, dict[str, Any]]:
    configured = _copy_dict(getattr(controller, "regime_controls", {}) or {})
    if not configured:
        model = getattr(controller, "investment_model", None)
        config_section = getattr(model, "config_section", None)
        if callable(config_section):
            try:
                configured = _copy_dict(config_section("regime_controls", {}) or {})
            except Exception:
                configured = {}
    normalized: dict[str, dict[str, Any]] = {}
    for regime, params in configured.items():
        regime_name = _normalize_regime(regime)
        if regime_name == "unknown":
            continue
        normalized[regime_name] = _copy_dict(params or {})
    return normalized


def _resolve_strategy_family(controller: Any) -> str:
    explicit = str(getattr(controller, "strategy_family", "") or "").strip().lower()
    if explicit:
        return explicit
    model = getattr(controller, "investment_model", None)
    config = getattr(model, "config", None)
    config_data = dict(getattr(config, "data", {}) or {})
    kind = str(config_data.get("kind") or "").strip().lower()
    if kind:
        return kind
    model_name = str(getattr(controller, "model_name", "") or getattr(model, "model_name", "") or "").strip().lower()
    return model_name or "unknown"


def _resolve_strategy_family_regime_budgets(controller: Any) -> dict[str, dict[str, Any]]:
    family = _resolve_strategy_family(controller)
    configured = _copy_dict(getattr(controller, "strategy_family_risk_budgets", {}) or {})
    if not configured:
        model = getattr(controller, "investment_model", None)
        config_section = getattr(model, "config_section", None)
        if callable(config_section):
            try:
                configured = _copy_dict(config_section("strategy_family_risk_budgets", {}) or {})
            except Exception:
                configured = {}

    family_budget = _copy_dict(DEFAULT_STRATEGY_FAMILY_REGIME_BUDGETS.get(family, {}) or {})
    for regime, params in configured.items():
        regime_name = _normalize_regime(regime)
        if regime_name == "unknown":
            continue
        baseline = dict(family_budget.get(regime_name) or {})
        baseline.update(
            {
                str(key): value
                for key, value in dict(params or {}).items()
                if str(key) in BUDGET_RUNTIME_KEYS
            }
        )
        family_budget[regime_name] = baseline
    return family_budget


def _history_field(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _history_dict(item: Any, key: str) -> dict[str, Any]:
    value = _history_field(item, key, {})
    return dict(value or {}) if isinstance(value, dict) else dict(value or {})


def _history_bool(item: Any, key: str, default: bool = False) -> bool:
    value = _history_field(item, key, default)
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _history_regime(item: Any) -> str:
    routing = _history_dict(item, "routing_decision")
    audit_tags = _history_dict(item, "audit_tags")
    return _normalize_regime(
        routing.get("regime")
        or audit_tags.get("routing_regime")
        or _history_field(item, "regime", "")
    )


def _build_history_failure_signature(item: Any) -> dict[str, Any]:
    from app.training.review_protocol import build_failure_signature

    model_name = str(_history_field(item, "model_name", "") or "")
    strategy_family = str(_history_field(item, "strategy_family", "") or "")
    return build_failure_signature(
        {
            "cycle_id": int(_history_field(item, "cycle_id", 0) or 0),
            "return_pct": _safe_float(_history_field(item, "return_pct", 0.0)) or 0.0,
            "is_profit": _history_bool(item, "is_profit", False),
            "benchmark_passed": _history_bool(item, "benchmark_passed", False),
            "selection_mode": str(_history_field(item, "selection_mode", "unknown") or "unknown"),
            "plan_source": str(_history_field(item, "plan_source", "unknown") or "unknown"),
            "review_applied": _history_bool(item, "review_applied", False),
            "regime": _history_regime(item),
            "strategy_family": strategy_family,
            "metadata": {
                "strategy_family": strategy_family,
                "model_name": model_name,
                "config_name": str(_history_field(item, "config_name", "") or ""),
            },
            "research_feedback": _history_dict(item, "research_feedback"),
            "causal_diagnosis": _history_dict(item, "causal_diagnosis"),
            "similarity_summary": _history_dict(item, "similarity_summary"),
        }
    )


def _apply_budget_deltas(
    base_budget: dict[str, Any] | None,
    deltas: dict[str, Any] | None = None,
) -> dict[str, Any]:
    budget = _copy_dict(base_budget or {})
    patch = dict(deltas or {})
    if "position_size" in patch:
        current = _safe_float(budget.get("position_size"))
        delta = _safe_float(patch.get("position_size"))
        if current is not None and delta is not None:
            budget["position_size"] = round(current + delta, 4)
    if "cash_reserve" in patch:
        current = _safe_float(budget.get("cash_reserve"))
        delta = _safe_float(patch.get("cash_reserve"))
        if current is not None and delta is not None:
            budget["cash_reserve"] = round(current + delta, 4)
    if "max_positions" in patch:
        current = _safe_int(budget.get("max_positions"))
        delta = _safe_int(patch.get("max_positions"))
        if current is not None and delta is not None:
            budget["max_positions"] = max(1, current + delta)
    return budget


def _build_family_budget_correction(
    controller: Any,
    *,
    strategy_family: str,
    regime: str,
    family_budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = {
        "applied": False,
        "window_size": FAMILY_SPECIFIC_BUDGET_CORRECTION_WINDOW,
        "min_repeat_failures": FAMILY_SPECIFIC_BUDGET_CORRECTION_MIN_REPEAT,
        "observed_loss_cycles": 0,
        "matched_cycle_ids": [],
        "failure_sub_signature_counts": {},
        "dominant_failure_sub_signature": "",
        "dominant_failure_count": 0,
        "adjustment_deltas": {},
        "base_adjustment_deltas": {},
        "repeat_multiplier": 1.0,
        "reason": "",
        "adjusted_budget": _copy_dict(family_budget or {}),
    }
    normalized_family = str(strategy_family or "").strip().lower()
    normalized_regime = _normalize_regime(regime)
    correction_specs = dict(
        FAMILY_SPECIFIC_BUDGET_CORRECTIONS.get(normalized_family, {}).get(normalized_regime, {})
        or {}
    )
    if not correction_specs:
        return summary

    history = list(getattr(controller, "cycle_history", []) or [])
    if not history:
        return summary

    relevant = history[-FAMILY_SPECIFIC_BUDGET_CORRECTION_WINDOW:]
    counts: dict[str, int] = {}
    matched_cycle_ids: list[int] = []
    for item in relevant:
        signature = _build_history_failure_signature(item)
        if str(signature.get("return_direction") or "") == "profit":
            continue
        if str(signature.get("strategy_family") or "") != normalized_family:
            continue
        if str(signature.get("regime") or "") != normalized_regime:
            continue
        sub_label = str(signature.get("sub_label") or "")
        if not sub_label:
            continue
        counts[sub_label] = int(counts.get(sub_label, 0) or 0) + 1
        matched_cycle_ids.append(int(_history_field(item, "cycle_id", 0) or 0))

    summary["observed_loss_cycles"] = sum(counts.values())
    summary["matched_cycle_ids"] = matched_cycle_ids
    summary["failure_sub_signature_counts"] = counts
    if not counts:
        return summary

    dominant_sub_label, dominant_count = sorted(
        counts.items(),
        key=lambda item: (-int(item[1]), str(item[0])),
    )[0]
    summary["dominant_failure_sub_signature"] = dominant_sub_label
    summary["dominant_failure_count"] = int(dominant_count)
    if int(dominant_count) < FAMILY_SPECIFIC_BUDGET_CORRECTION_MIN_REPEAT:
        return summary

    correction = dict(correction_specs.get(dominant_sub_label) or {})
    base_deltas = dict(correction.get("deltas") or {})
    deltas = dict(base_deltas)
    repeat_scale = dict(correction.get("repeat_scale") or {})
    if repeat_scale:
        extra_repeat_count = max(
            0,
            int(dominant_count) - FAMILY_SPECIFIC_BUDGET_CORRECTION_MIN_REPEAT,
        )
        per_extra_repeat = max(
            0.0,
            _safe_float(repeat_scale.get("per_extra_repeat")) or 0.0,
        )
        max_multiplier = max(
            1.0,
            _safe_float(repeat_scale.get("max_multiplier")) or 1.0,
        )
        repeat_multiplier = min(
            max_multiplier,
            1.0 + extra_repeat_count * per_extra_repeat,
        )
        deltas = _scale_budget_deltas(deltas, repeat_multiplier)
        summary["repeat_multiplier"] = round(repeat_multiplier, 4)
    if not deltas:
        return summary

    summary["applied"] = True
    summary["base_adjustment_deltas"] = base_deltas
    summary["adjustment_deltas"] = deltas
    summary["reason"] = str(correction.get("reason") or "")
    summary["adjusted_budget"] = _apply_budget_deltas(family_budget, deltas)
    return summary


def _scale_budget_deltas(
    deltas: dict[str, Any] | None,
    multiplier: float,
) -> dict[str, Any]:
    payload = dict(deltas or {})
    if multiplier <= 1.0:
        return payload
    scaled: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "max_positions":
            parsed = _safe_int(value)
            if parsed is None:
                continue
            scaled[key] = int(round(parsed * multiplier))
            continue
        parsed = _safe_float(value)
        if parsed is None:
            continue
        scaled[key] = round(parsed * multiplier, 4)
    return scaled


def _clamp_between(value: Any, minimum: float, maximum: float, *, digits: int = 4) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    return round(max(minimum, min(maximum, number)), digits)


def _sanitize_regime_overlay(
    controller: Any,
    *,
    base_params: dict[str, Any],
    overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = _copy_dict(overlay or {})
    clean: dict[str, Any] = {}
    risk_clamps = dict(_policy_lookup(getattr(controller, "risk_policy", {}), "clamps", {}) or {})
    review_clamps = dict(
        _policy_lookup(getattr(controller, "review_policy", {}), "param_clamps", {}) or {}
    )
    for key, value in raw.items():
        if value is None:
            continue
        if key == "position_size":
            bounds = dict(risk_clamps.get("position_size") or {"min": 0.0, "max": 1.0})
            clamped = _clamp_between(
                value,
                float(bounds.get("min", 0.0)),
                float(bounds.get("max", 1.0)),
            )
            if clamped is not None:
                clean[key] = clamped
            continue
        if key == "cash_reserve":
            bounds = dict(review_clamps.get("cash_reserve") or {"min": 0.0, "max": 0.80})
            clamped = _clamp_between(
                value,
                float(bounds.get("min", 0.0)),
                float(bounds.get("max", 0.80)),
            )
            if clamped is not None:
                clean[key] = clamped
            continue
        if key == "signal_threshold":
            bounds = dict(
                review_clamps.get("signal_threshold") or {"min": 0.30, "max": 0.95}
            )
            clamped = _clamp_between(
                value,
                float(bounds.get("min", 0.30)),
                float(bounds.get("max", 0.95)),
            )
            if clamped is not None:
                clean[key] = clamped
            continue
        if key == "max_positions":
            parsed = _safe_int(value)
            if parsed is not None:
                clean[key] = max(1, parsed)
            continue
        if key == "max_hold_days":
            bounds = dict(review_clamps.get("max_hold_days") or {"min": 5, "max": 60})
            parsed = _safe_int(value)
            if parsed is not None:
                clean[key] = max(
                    int(bounds.get("min", 5)),
                    min(int(bounds.get("max", 60)), parsed),
                )
            continue
        parsed_float = _safe_float(value)
        if parsed_float is not None:
            baseline_value = base_params.get(key)
            if isinstance(baseline_value, int) and not isinstance(baseline_value, bool):
                clean[key] = int(round(parsed_float))
            else:
                clean[key] = round(parsed_float, 6)
            continue
        clean[key] = value
    return clean


def resolve_entry_threshold_spec(params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = _copy_dict(params or {})
    for key in ENTRY_THRESHOLD_KEYS:
        value = _safe_float(payload.get(key))
        if value is not None:
            return {"key": key, "value": value}
    return {"key": "", "value": None}


def build_regime_runtime_profile(
    controller: Any,
    *,
    regime: str | None = None,
    base_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_params = _copy_dict(base_params or resolve_active_runtime_params(controller))
    requested_regime = _normalize_regime(
        regime or dict(getattr(controller, "last_routing_decision", {}) or {}).get("regime")
    )
    strategy_family = _resolve_strategy_family(controller)
    strategy_family_budgets = _resolve_strategy_family_regime_budgets(controller)
    regime_controls = _resolve_controller_regime_controls(controller)
    raw_family_budget = _copy_dict(strategy_family_budgets.get(requested_regime, {}) or {})
    raw_model_overlay = _copy_dict(regime_controls.get(requested_regime, {}) or {})
    raw_model_budget_override = {
        key: value
        for key, value in raw_model_overlay.items()
        if str(key) in BUDGET_RUNTIME_KEYS
    }
    raw_behavior_overlay = {
        key: value
        for key, value in raw_model_overlay.items()
        if str(key) not in BUDGET_RUNTIME_KEYS
    }
    merged_budget_before_correction = _copy_dict(raw_family_budget)
    merged_budget_before_correction.update(raw_model_budget_override)
    family_budget_correction = _build_family_budget_correction(
        controller,
        strategy_family=strategy_family,
        regime=requested_regime,
        family_budget=merged_budget_before_correction,
    )
    corrected_family_budget = _copy_dict(
        family_budget_correction.get("adjusted_budget") or merged_budget_before_correction
    )
    raw_overlay = _copy_dict(corrected_family_budget)
    raw_overlay.update(raw_behavior_overlay)
    overlay = _sanitize_regime_overlay(
        controller,
        base_params=active_params,
        overlay=raw_overlay,
    )
    family_budget = _sanitize_regime_overlay(
        controller,
        base_params=active_params,
        overlay=raw_family_budget,
    )
    corrected_family_budget_overlay = _sanitize_regime_overlay(
        controller,
        base_params=active_params,
        overlay=corrected_family_budget,
    )
    model_budget_override = _sanitize_regime_overlay(
        controller,
        base_params=active_params,
        overlay=raw_model_budget_override,
    )
    behavior_overlay = _sanitize_regime_overlay(
        controller,
        base_params=active_params,
        overlay=raw_behavior_overlay,
    )
    effective_params = _copy_dict(active_params)
    effective_params.update(overlay)
    resolved_budget = {
        key: effective_params.get(key)
        for key in BUDGET_RUNTIME_KEYS
        if effective_params.get(key) is not None
    }
    source_parts = []
    if family_budget:
        source_parts.append("strategy_family_risk_budget")
    if bool(family_budget_correction.get("applied", False)):
        source_parts.append("family_specific_budget_correction")
    if raw_model_overlay:
        source_parts.append("model_regime_controls")
    profile_source = "+".join(source_parts) if source_parts else "base_runtime"
    return {
        "schema_version": "training.regime_runtime_profile.v1",
        "regime": requested_regime,
        "strategy_family": strategy_family,
        "source": profile_source if overlay else "base_runtime",
        "controls_configured": bool(regime_controls or strategy_family_budgets),
        "applied": bool(overlay),
        "control_keys": sorted(raw_overlay.keys()),
        "budget_control_keys": sorted(
            {str(key) for key in list(raw_family_budget.keys()) + list(raw_model_budget_override.keys())}
        ),
        "behavior_control_keys": sorted(raw_behavior_overlay.keys()),
        "base_params": active_params,
        "overlay": overlay,
        "effective_params": effective_params,
        "entry_threshold": resolve_entry_threshold_spec(behavior_overlay),
        "budget_layering": {
            "schema_version": "training.regime_budget_layering.v1",
            "strategy_family": strategy_family,
            "family_budget": family_budget,
            "family_budget_correction": {
                **family_budget_correction,
                "adjusted_budget": corrected_family_budget_overlay,
            },
            "corrected_family_budget": corrected_family_budget_overlay,
            "model_budget_override": model_budget_override,
            "behavior_overlay": behavior_overlay,
            "resolved_budget": resolved_budget,
            "budget_keys": sorted(BUDGET_RUNTIME_KEYS),
            "source": profile_source,
        },
    }


def apply_regime_runtime_profile(controller: Any, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    effective_params = _copy_dict(dict(profile or {}).get("effective_params") or {})
    if not effective_params:
        effective_params = _copy_dict(resolve_active_runtime_params(controller))
    setattr(controller, "current_cycle_effective_runtime_params", _copy_dict(effective_params))
    setattr(controller, "current_cycle_regime_profile", deepcopy(dict(profile or {})))
    _sync_model_runtime(controller, effective_params)
    return _copy_dict(effective_params)


def resolve_effective_runtime_params(controller: Any) -> dict[str, Any]:
    if bool(getattr(controller, "current_cycle_runtime_locked", False)):
        effective = _copy_dict(
            getattr(controller, "current_cycle_effective_runtime_params", {}) or {}
        )
        if effective:
            return effective
    return resolve_active_runtime_params(controller)


def _sync_model_runtime(controller: Any, params: dict[str, Any]) -> None:
    model = getattr(controller, "investment_model", None)
    if model is None:
        return
    if hasattr(model, "runtime_overrides"):
        model.runtime_overrides = _copy_dict(params)
        return
    update_runtime_overrides = getattr(model, "update_runtime_overrides", None)
    if callable(update_runtime_overrides):
        update_runtime_overrides(params)


def resolve_active_runtime_params(controller: Any) -> dict[str, Any]:
    if bool(getattr(controller, "current_cycle_runtime_locked", False)):
        return _copy_dict(getattr(controller, "current_cycle_frozen_params", {}) or {})
    return _copy_dict(getattr(controller, "current_params", {}) or {})


def begin_cycle_runtime_window(controller: Any, *, cycle_id: int) -> dict[str, Any]:
    active_params = _copy_dict(getattr(controller, "current_params", {}) or {})
    setattr(controller, "current_cycle_frozen_params", _copy_dict(active_params))
    setattr(controller, "current_cycle_start_params", _copy_dict(active_params))
    setattr(controller, "current_cycle_effective_runtime_params", _copy_dict(active_params))
    setattr(controller, "current_cycle_learning_proposals", [])
    setattr(controller, "current_cycle_runtime_violations", [])
    setattr(controller, "current_cycle_safety_overrides", {})
    setattr(
        controller,
        "current_cycle_regime_profile",
        {
            "schema_version": "training.regime_runtime_profile.v1",
            "regime": "unknown",
            "source": "base_runtime",
            "controls_configured": bool(_resolve_controller_regime_controls(controller)),
            "applied": False,
            "control_keys": [],
            "base_params": _copy_dict(active_params),
            "overlay": {},
            "effective_params": _copy_dict(active_params),
            "entry_threshold": resolve_entry_threshold_spec(active_params),
        },
    )
    setattr(controller, "current_cycle_selection_intercepts", {})
    setattr(controller, "current_cycle_runtime_locked", True)
    setattr(controller, "current_cycle_runtime_started_at", datetime.now().isoformat())
    setattr(controller, "current_cycle_runtime_started_for", int(cycle_id))
    _sync_model_runtime(controller, active_params)
    return _copy_dict(active_params)


def record_learning_proposal(
    controller: Any,
    *,
    source: str,
    patch: dict[str, Any] | None,
    target_scope: str = "candidate",
    rationale: str = "",
    evidence: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    cycle_id: int | None = None,
) -> dict[str, Any]:
    proposals = getattr(controller, "current_cycle_learning_proposals", None)
    if not isinstance(proposals, list):
        proposals = []
        setattr(controller, "current_cycle_learning_proposals", proposals)
    proposal_cycle_id = (
        cycle_id
        if cycle_id is not None
        else getattr(controller, "current_cycle_runtime_started_for", None)
        or getattr(controller, "current_cycle_id", 0)
    )
    sequence = len(proposals) + 1
    proposal = ensure_proposal_tracking_fields(
        {
        "proposal_id": f"proposal_{int(proposal_cycle_id or 0):04d}_{len(proposals) + 1:03d}",
        "cycle_id": int(proposal_cycle_id or 0),
        "source": str(source or "unknown"),
        "target_scope": str(target_scope or "candidate"),
        "patch": _copy_dict(patch or {}),
        "rationale": str(rationale or ""),
        "evidence": _copy_dict(evidence or {}),
        "metadata": _copy_dict(metadata or {}),
        "active_params_snapshot": resolve_active_runtime_params(controller),
        "created_at": datetime.now().isoformat(),
        "suggestion_id": f"suggestion_{int(proposal_cycle_id or 0):04d}_{sequence:03d}",
        },
        default_cycle_id=int(proposal_cycle_id or 0),
    )
    proposals.append(proposal)
    return deepcopy(proposal)


def apply_safety_override(
    controller: Any,
    adjustments: dict[str, Any] | None,
    *,
    source: str,
) -> dict[str, Any]:
    clean = _copy_dict(adjustments or {})
    for key in clean:
        if key not in SAFETY_RUNTIME_KEYS:
            raise ValueError(f"Non-safety param {key} cannot override frozen runtime")
    if not clean:
        return {}

    current_params = _copy_dict(getattr(controller, "current_params", {}) or {})
    current_params.update(clean)
    setattr(controller, "current_params", current_params)

    if bool(getattr(controller, "current_cycle_runtime_locked", False)):
        frozen = _copy_dict(getattr(controller, "current_cycle_frozen_params", {}) or {})
        frozen.update(clean)
        setattr(controller, "current_cycle_frozen_params", frozen)
        effective = _copy_dict(
            getattr(controller, "current_cycle_effective_runtime_params", {}) or {}
        )
        effective.update(clean)
        setattr(controller, "current_cycle_effective_runtime_params", effective)
        safety_overrides = _copy_dict(getattr(controller, "current_cycle_safety_overrides", {}) or {})
        safety_overrides.update(clean)
        setattr(controller, "current_cycle_safety_overrides", safety_overrides)

    _sync_model_runtime(controller, resolve_effective_runtime_params(controller))
    record_learning_proposal(
        controller,
        source=str(source or "safety_override"),
        patch=clean,
        target_scope="safety",
        rationale="approved safety override",
        metadata={"proposal_kind": "safety_override"},
    )
    return clean


def finalize_cycle_runtime_window(controller: Any) -> dict[str, Any]:
    proposals = deepcopy(list(getattr(controller, "current_cycle_learning_proposals", []) or []))
    safety_overrides = _copy_dict(getattr(controller, "current_cycle_safety_overrides", {}) or {})
    start_params = _copy_dict(getattr(controller, "current_cycle_start_params", {}) or {})
    current_params = _copy_dict(getattr(controller, "current_params", {}) or {})
    violations: list[dict[str, Any]] = []

    if bool(getattr(controller, "current_cycle_runtime_locked", False)):
        for key in sorted(set(start_params) | set(current_params)):
            if key in safety_overrides:
                continue
            before = start_params.get(key)
            after = current_params.get(key)
            if before == after:
                continue
            violations.append(
                {
                    "key": key,
                    "before": before,
                    "after": after,
                    "violation_type": "illegal_cycle_runtime_mutation",
                }
            )

    if violations:
        logger.warning(
            "Detected illegal runtime mutation during cycle: %s",
            [item["key"] for item in violations],
        )
        setattr(controller, "current_params", _copy_dict(start_params))
        _sync_model_runtime(controller, start_params)

    summary = {
        "cycle_id": int(
            getattr(controller, "current_cycle_runtime_started_for", 0)
            or getattr(controller, "current_cycle_id", 0)
            or 0
        ),
        "proposal_count": len(proposals),
        "violation_count": len(violations),
        "violations": deepcopy(violations),
        "safety_override_keys": sorted(safety_overrides.keys()),
        "frozen_params": resolve_active_runtime_params(controller),
        "effective_runtime_params": resolve_effective_runtime_params(controller),
        "regime_runtime_profile": deepcopy(
            dict(getattr(controller, "current_cycle_regime_profile", {}) or {})
        ),
        "selection_intercepts": deepcopy(
            dict(getattr(controller, "current_cycle_selection_intercepts", {}) or {})
        ),
    }

    setattr(controller, "last_cycle_learning_proposals", proposals)
    setattr(controller, "last_cycle_runtime_summary", deepcopy(summary))
    setattr(controller, "current_cycle_runtime_violations", deepcopy(violations))
    setattr(controller, "current_cycle_learning_proposals", [])
    setattr(controller, "current_cycle_frozen_params", {})
    setattr(controller, "current_cycle_start_params", {})
    setattr(controller, "current_cycle_effective_runtime_params", {})
    setattr(controller, "current_cycle_safety_overrides", {})
    setattr(controller, "current_cycle_regime_profile", {})
    setattr(controller, "current_cycle_selection_intercepts", {})
    setattr(controller, "current_cycle_runtime_locked", False)
    setattr(controller, "current_cycle_runtime_started_at", "")
    setattr(controller, "current_cycle_runtime_started_for", 0)
    return summary
