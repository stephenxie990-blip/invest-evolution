from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from config import normalize_date

from .batch_snapshot import build_batch_indicator_snapshot


def compute_market_stats(
    stock_data: Dict[str, pd.DataFrame],
    cutoff_date: str,
    min_valid: Optional[int] = None,
    regime_policy: Optional[dict] = None,
) -> dict:
    total = len(stock_data)
    if total == 0:
        return _unknown_market_stats(valid_stocks=0)

    if min_valid is None:
        if total <= 10:
            min_valid = 1
        elif total <= 100:
            min_valid = 3
        else:
            min_valid = max(10, int(total * 0.05))

    cutoff_norm = normalize_date(cutoff_date)
    changes_5d: List[float] = []
    changes_20d: List[float] = []
    volatilities: List[float] = []
    above_ma20 = 0
    valid_count = 0

    for df in stock_data.values():
        batch = build_batch_indicator_snapshot(df, cutoff_norm)
        if batch is None:
            continue
        valid_count += 1
        changes_5d.append(batch.change_5d)
        changes_20d.append(batch.change_20d)
        volatilities.append(batch.volatility)
        if batch.above_ma20:
            above_ma20 += 1

    if valid_count < min_valid:
        return _unknown_market_stats(valid_stocks=valid_count)

    avg_change_5d = sum(changes_5d) / valid_count
    median_change_5d = sorted(changes_5d)[len(changes_5d) // 2]
    avg_change_20d = sum(changes_20d) / valid_count
    median_change_20d = sorted(changes_20d)[len(changes_20d) // 2]
    avg_volatility = sum(volatilities) / valid_count
    above_ma20_ratio = above_ma20 / valid_count
    market_breadth = sum(1 for item in changes_5d if item > 0) / valid_count
    regime_hint = _classify_market_regime(
        avg_change_20d=avg_change_20d,
        above_ma20_ratio=above_ma20_ratio,
        regime_policy=regime_policy,
    )
    return {
        "valid_stocks": valid_count,
        "advance_ratio_5d": round(market_breadth, 4),
        "market_breadth": round(market_breadth, 4),
        "avg_change_5d": round(avg_change_5d, 4),
        "median_change_5d": round(median_change_5d, 4),
        "avg_change_20d": round(avg_change_20d, 4),
        "median_change_20d": round(median_change_20d, 4),
        "avg_volatility": round(avg_volatility, 6),
        "above_ma20_ratio": round(above_ma20_ratio, 4),
        "regime_hint": regime_hint,
    }


def _unknown_market_stats(*, valid_stocks: int) -> dict:
    return {
        "valid_stocks": valid_stocks,
        "advance_ratio_5d": 0.5,
        "market_breadth": 0.5,
        "avg_change_5d": 0.0,
        "median_change_5d": 0.0,
        "avg_change_20d": 0.0,
        "median_change_20d": 0.0,
        "avg_volatility": 0.0,
        "above_ma20_ratio": 0.5,
        "regime_hint": "unknown",
    }


def _classify_market_regime(
    *,
    avg_change_20d: float,
    above_ma20_ratio: float,
    regime_policy: Optional[dict],
) -> str:
    policy = dict(regime_policy or {})
    bull_avg_change_20d = policy.get("bull_avg_change_20d")
    bull_above_ma20_ratio = policy.get("bull_above_ma20_ratio")
    bear_avg_change_20d = policy.get("bear_avg_change_20d")
    bear_above_ma20_ratio = policy.get("bear_above_ma20_ratio")
    default_regime = str(policy.get("default_regime", "unknown") or "unknown")
    regime_hint = default_regime
    if bull_avg_change_20d is not None and bull_above_ma20_ratio is not None:
        if avg_change_20d > float(bull_avg_change_20d) and above_ma20_ratio > float(bull_above_ma20_ratio):
            regime_hint = "bull"
    if regime_hint == default_regime and bear_avg_change_20d is not None and bear_above_ma20_ratio is not None:
        if avg_change_20d < float(bear_avg_change_20d) and above_ma20_ratio < float(bear_above_ma20_ratio):
            regime_hint = "bear"
    return regime_hint


__all__ = ["compute_market_stats"]
