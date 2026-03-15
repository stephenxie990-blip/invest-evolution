from __future__ import annotations

from copy import deepcopy


DEFAULT_MODEL_REGIME_COMPATIBILITY: dict[str, dict[str, float]] = {
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


def normalize_model_name(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized or "unknown"


def normalize_regime(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"bull", "bear", "oscillation", "unknown"}:
        return normalized
    return "unknown"


def get_model_regime_profile(model_name: str | None) -> dict[str, float]:
    normalized = normalize_model_name(model_name)
    profile = DEFAULT_MODEL_REGIME_COMPATIBILITY.get(normalized)
    if profile is None:
        profile = DEFAULT_MODEL_REGIME_COMPATIBILITY["unknown"]
    return deepcopy(profile)


def regime_compatibility(model_name: str | None, regime: str | None) -> float:
    normalized_regime = normalize_regime(regime)
    profile = get_model_regime_profile(model_name)
    value = float(profile.get(normalized_regime, profile.get("unknown", 0.55)) or 0.0)
    return round(max(0.0, min(1.0, value)), 4)
