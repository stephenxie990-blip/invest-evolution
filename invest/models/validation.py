from __future__ import annotations

from typing import Any, Dict


class ModelConfigValidationError(ValueError):
    pass


_REQUIRED_TOP_LEVEL = {"name", "kind", "params", "risk", "execution", "benchmark"}
_REQUIRED_SCORING_MODELS = {"mean_reversion", "value_quality", "defensive_low_vol"}
_REQUIRED_SCORING_SHAPE = {
    "mean_reversion": {"weights", "bands", "penalties"},
    "value_quality": {"weights", "bands"},
    "defensive_low_vol": {"weights", "bands", "penalties"},
}


def _ensure_numeric_dict(name: str, payload: Dict[str, Any]) -> None:
    for key, value in payload.items():
        if not isinstance(value, (int, float)):
            raise ModelConfigValidationError(f"{name}.{key} must be numeric")


def _ensure_range_dict(name: str, payload: Dict[str, Any]) -> None:
    for key, value in payload.items():
        if not isinstance(value, dict):
            raise ModelConfigValidationError(f"{name}.{key} must be a dict")
        if "min" not in value or "max" not in value:
            raise ModelConfigValidationError(f"{name}.{key} must define min/max")
        if not isinstance(value["min"], (int, float)) or not isinstance(value["max"], (int, float)):
            raise ModelConfigValidationError(f"{name}.{key}.min/max must be numeric")
        if float(value["min"]) > float(value["max"]):
            raise ModelConfigValidationError(f"{name}.{key}.min must be <= max")


def validate_model_config(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ModelConfigValidationError("model config must be a dict")

    missing = [key for key in _REQUIRED_TOP_LEVEL if key not in data]
    if missing:
        raise ModelConfigValidationError(f"missing required top-level keys: {', '.join(missing)}")

    for section in ["params", "risk", "execution", "benchmark"]:
        if not isinstance(data.get(section), dict):
            raise ModelConfigValidationError(f"{section} must be a dict")

    params = data.get("params", {}) or {}
    if "top_n" in params and int(params["top_n"]) <= 0:
        raise ModelConfigValidationError("params.top_n must be > 0")
    if "max_positions" in params and int(params["max_positions"]) <= 0:
        raise ModelConfigValidationError("params.max_positions must be > 0")
    if "cash_reserve" in params and not (0.0 <= float(params["cash_reserve"]) <= 1.0):
        raise ModelConfigValidationError("params.cash_reserve must be within [0, 1]")

    kind = str(data.get("kind") or "")
    scoring = data.get("scoring")
    if kind in _REQUIRED_SCORING_MODELS:
        if not isinstance(scoring, dict):
            raise ModelConfigValidationError(f"scoring section is required for model kind={kind}")
        missing_scoring = [key for key in _REQUIRED_SCORING_SHAPE[kind] if key not in scoring]
        if missing_scoring:
            raise ModelConfigValidationError(f"scoring missing keys for {kind}: {', '.join(missing_scoring)}")
        for key in _REQUIRED_SCORING_SHAPE[kind]:
            section = scoring.get(key)
            if not isinstance(section, dict):
                raise ModelConfigValidationError(f"scoring.{key} must be a dict")
            _ensure_numeric_dict(f"scoring.{key}", section)

    summary_scoring = data.get("summary_scoring")
    if summary_scoring is not None:
        if not isinstance(summary_scoring, dict):
            raise ModelConfigValidationError("summary_scoring must be a dict")
        for section_name in ["weights", "bands"]:
            section = summary_scoring.get(section_name)
            if section is not None and not isinstance(section, dict):
                raise ModelConfigValidationError(f"summary_scoring.{section_name} must be a dict")
        if "logic" in summary_scoring:
            if not isinstance(summary_scoring["logic"], dict):
                raise ModelConfigValidationError("summary_scoring.logic must be a dict")
            _ensure_numeric_dict("summary_scoring.logic", summary_scoring["logic"])

    market_hints = data.get("market_hints")
    if market_hints is not None:
        if not isinstance(market_hints, dict):
            raise ModelConfigValidationError("market_hints must be a dict")
        _ensure_numeric_dict("market_hints", market_hints)

    review_policy = data.get("review_policy")
    if review_policy is not None and not isinstance(review_policy, dict):
        raise ModelConfigValidationError("review_policy must be a dict")

    market_regime = data.get("market_regime")
    if market_regime is not None:
        if not isinstance(market_regime, dict):
            raise ModelConfigValidationError("market_regime must be a dict")
        numeric_fields = {k: v for k, v in market_regime.items() if k != "default_regime"}
        _ensure_numeric_dict("market_regime", numeric_fields)
        if "default_regime" in market_regime and not isinstance(market_regime["default_regime"], str):
            raise ModelConfigValidationError("market_regime.default_regime must be a string")

    mutation_space = data.get("mutation_space")
    if mutation_space is not None:
        if not isinstance(mutation_space, dict):
            raise ModelConfigValidationError("mutation_space must be a dict")
        if "params" in mutation_space:
            if not isinstance(mutation_space["params"], dict):
                raise ModelConfigValidationError("mutation_space.params must be a dict")
            _ensure_range_dict("mutation_space.params", mutation_space["params"])
        if "scoring" in mutation_space:
            if not isinstance(mutation_space["scoring"], dict):
                raise ModelConfigValidationError("mutation_space.scoring must be a dict")
            for section_name, section_ranges in mutation_space["scoring"].items():
                if not isinstance(section_ranges, dict):
                    raise ModelConfigValidationError(f"mutation_space.scoring.{section_name} must be a dict")
                _ensure_range_dict(f"mutation_space.scoring.{section_name}", section_ranges)

    return data
