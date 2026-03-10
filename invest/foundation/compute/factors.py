from __future__ import annotations

from typing import Any, Dict


def _score_map(macd_profile: Dict[str, Any]) -> Dict[str, float]:
    return {
        "金叉": float(macd_profile.get("gold_cross", 0.0) or 0.0),
        "看多": float(macd_profile.get("bullish", 0.0) or 0.0),
        "中性": float(macd_profile.get("neutral", 0.0) or 0.0),
        "看空": float(macd_profile.get("bearish", 0.0) or 0.0),
        "死叉": float(macd_profile.get("death_cross", 0.0) or 0.0),
    }


def calc_algo_score(
    change_5d: float,
    change_20d: float,
    ma_trend: str,
    rsi: float,
    macd_signal: str,
    bb_pos: float,
    profile: Dict[str, Any] | None = None,
) -> float:
    profile = dict(profile or {})
    weights = dict(profile.get("weights", {}) or {})
    bands = dict(profile.get("bands", {}) or {})

    change_5d_norm = float(bands.get("change_5d_norm", 1.0) or 1.0)
    change_20d_norm = float(bands.get("change_20d_norm", 1.0) or 1.0)
    rsi_mid_low = float(bands.get("rsi_mid_low", 50.0) or 50.0)
    rsi_mid_high = float(bands.get("rsi_mid_high", 50.0) or 50.0)
    rsi_oversold = float(bands.get("rsi_oversold", 0.0) or 0.0)
    rsi_overbought = float(bands.get("rsi_overbought", 100.0) or 100.0)
    bb_low = float(bands.get("bb_low", 0.0) or 0.0)
    bb_high = float(bands.get("bb_high", 1.0) or 1.0)

    score = 0.0
    score += max(-1, min(1, change_5d / max(change_5d_norm, 1e-6))) * float(weights.get("change_5d", 0.0) or 0.0)
    score += max(-1, min(1, change_20d / max(change_20d_norm, 1e-6))) * float(weights.get("change_20d", 0.0) or 0.0)
    if ma_trend == "多头":
        score += float(weights.get("ma_bull", 0.0) or 0.0)
    elif ma_trend == "空头":
        score += float(weights.get("ma_bear", 0.0) or 0.0)
    if rsi_mid_low <= rsi <= rsi_mid_high:
        score += float(weights.get("rsi_mid", 0.0) or 0.0)
    elif rsi < rsi_oversold:
        score += float(weights.get("rsi_oversold", 0.0) or 0.0)
    elif rsi > rsi_overbought:
        score += float(weights.get("rsi_overbought", 0.0) or 0.0)
    macd_scores = _score_map(dict(weights.get("macd", {}) or {}))
    score += macd_scores.get(macd_signal, 0.0)
    if bb_pos < bb_low:
        score += float(weights.get("bb_low", 0.0) or 0.0)
    elif bb_pos > bb_high:
        score += float(weights.get("bb_high", 0.0) or 0.0)
    return score
