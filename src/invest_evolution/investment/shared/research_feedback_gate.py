from __future__ import annotations

from typing import Any

from .policy import DEFAULT_FREEZE_GATE_POLICY, deep_merge

DEFAULT_RESEARCH_FEEDBACK_GATE = dict(
    DEFAULT_FREEZE_GATE_POLICY.get("research_feedback") or {}
)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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
    config = deep_merge(
        defaults or DEFAULT_RESEARCH_FEEDBACK_GATE,
        dict(policy or {}),
    )
    checks: list[dict[str, Any]] = []
    scope = dict(payload.get("scope") or {})
    requested_regime = str(scope.get("requested_regime") or "").strip()
    effective_scope = str(scope.get("effective_scope") or "").strip()
    scope_actionable = bool(scope.get("actionable", True))

    if requested_regime and (
        not scope_actionable
        or effective_scope in {
            "overall_fallback",
            "requested_regime_unavailable",
            "regime_insufficient_samples",
        }
    ):
        checks.append(
            {
                "name": "requested_regime_scope",
                "passed": False,
                "actual": effective_scope or "unknown",
                "requested_regime": requested_regime,
                "actionable": scope_actionable,
            }
        )
        return {
            "active": False,
            "passed": True,
            "reason": "requested_regime_feedback_unavailable",
            "bias": bias,
            "sample_count": sample_count,
            "checks": checks,
            "failed_checks": [],
            "available_horizons": sorted((payload.get("horizons") or {}).keys()),
            "recommendation": recommendation,
        }

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

    blocked_biases = [
        str(item).strip()
        for item in (config.get("blocked_biases") or [])
        if str(item).strip()
    ]
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
    horizon_policy_catalog = dict(config.get("horizons") or {})
    apply_default_horizon_policy = bool(
        config.get("apply_default_horizon_policy", True)
    )
    horizon_defaults = (
        dict(horizon_policy_catalog.get("default") or {})
        if apply_default_horizon_policy
        else {}
    )
    for horizon_key in sorted(horizons.keys()):
        horizon_metrics = dict(horizons.get(horizon_key) or {})
        horizon_policy = deep_merge(
            horizon_defaults,
            dict(horizon_policy_catalog.get(horizon_key) or {}),
        )
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

    failed_checks = [
        item
        for item in checks
        if item.get("passed") is False and item.get("name") != "min_sample_count"
    ]
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
