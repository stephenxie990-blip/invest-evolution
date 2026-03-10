from __future__ import annotations

from typing import Any, Dict


def derive_scoring_adjustments(model_name: str, analysis: Any) -> Dict[str, Any]:
    model_kind = str(model_name or "")
    cause = str(getattr(analysis, "cause", "") or "")
    suggestions_text = " ".join(getattr(analysis, "suggestions", []) or [])
    combined = f"{cause} {suggestions_text}"

    if model_kind == "mean_reversion":
        weights = {}
        penalties = {}
        if any(token in combined for token in ["亏损", "过热", "追高", "持续性存疑"]):
            penalties["overheat_rsi"] = 0.18
            penalties["high_volatility"] = 0.10
        if any(token in combined for token in ["减少交易频率", "增加趋势确认", "普跌"]):
            weights["volume_ratio_bonus"] = 0.10
            penalties["insufficient_drop_5d"] = 0.08
        return {k: v for k, v in {"weights": weights, "penalties": penalties}.items() if v}

    if model_kind == "value_quality":
        weights = {}
        if any(token in combined for token in ["质量", "估值", "基本面"]):
            weights["roe"] = 0.35
            weights["pb"] = 0.22
        if any(token in combined for token in ["波动", "风险"]):
            weights["low_volatility"] = 0.08
        return {"weights": weights} if weights else {}

    if model_kind == "defensive_low_vol":
        weights = {}
        penalties = {}
        if any(token in combined for token in ["波动", "回撤", "风险"]):
            weights["low_volatility"] = 0.40
            penalties["bearish_trend"] = 0.10
        if any(token in combined for token in ["追高", "过热"]):
            penalties["bad_rsi"] = 0.12
        return {k: v for k, v in {"weights": weights, "penalties": penalties}.items() if v}

    return {}


__all__ = ["derive_scoring_adjustments"]
