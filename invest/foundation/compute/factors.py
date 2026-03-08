from __future__ import annotations

from typing import Dict


def calc_algo_score(
    change_5d: float,
    change_20d: float,
    ma_trend: str,
    rsi: float,
    macd_signal: str,
    bb_pos: float,
) -> float:
    score = 0.0
    score += max(-1, min(1, change_5d / 10)) * 0.15
    score += max(-1, min(1, change_20d / 20)) * 0.15
    if ma_trend == "多头":
        score += 0.2
    elif ma_trend == "空头":
        score -= 0.1
    if 40 <= rsi <= 60:
        score += 0.15
    elif rsi < 30:
        score += 0.05
    elif rsi > 70:
        score -= 0.1
    macd_scores: Dict[str, float] = {"金叉": 0.2, "看多": 0.1, "中性": 0.0, "看空": -0.1, "死叉": -0.15}
    score += macd_scores.get(macd_signal, 0.0)
    if bb_pos < 0.3:
        score += 0.15
    elif bb_pos > 0.8:
        score -= 0.1
    return score
