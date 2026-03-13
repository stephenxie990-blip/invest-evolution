from __future__ import annotations

from typing import Dict, List, Optional, cast

import pandas as pd

from config import normalize_date
from .factors import calc_algo_score
from .indicators import (
    calc_bb_position,
    calc_macd_signal,
    calc_pct_change,
    calc_rsi,
    calc_volume_ratio,
    filter_by_cutoff,
)


def _numeric_close_series(frame: pd.DataFrame) -> pd.Series:
    return cast(pd.Series, pd.to_numeric(frame["close"], errors="coerce")).dropna()


def compute_stock_summary(df: pd.DataFrame, code: str, cutoff_norm: str, summary_scoring: Optional[dict] = None) -> Optional[dict]:
    try:
        sub = filter_by_cutoff(df, cutoff_norm)
        if len(sub) < 30:
            return None
        close = _numeric_close_series(sub)
        if len(close) < 30 or close.iloc[-1] <= 0:
            return None
        latest = float(close.iloc[-1])
        change_5d = calc_pct_change(latest, close, 5)
        change_20d = calc_pct_change(latest, close, 20)
        ma5 = float(close.iloc[-5:].mean()) if len(close) >= 5 else latest
        ma20 = float(close.iloc[-20:].mean()) if len(close) >= 20 else latest
        summary_profile = dict(summary_scoring or {})
        logic = dict(summary_profile.get("logic", {}) or {})
        ma_bull_ratio = float(logic.get("ma_bull_ratio", 1.0) or 1.0)
        ma_bear_ratio = float(logic.get("ma_bear_ratio", 1.0) or 1.0)
        if ma5 > ma20 * ma_bull_ratio:
            ma_trend = "多头"
        elif ma5 < ma20 * ma_bear_ratio:
            ma_trend = "空头"
        else:
            ma_trend = "交叉"
        rsi = calc_rsi(close, 14)
        macd_signal = calc_macd_signal(close)
        bb_pos = calc_bb_position(close, 20)
        vol_ratio = calc_volume_ratio(sub)
        returns = close.pct_change().dropna()
        volatility = float(returns.iloc[-20:].std()) if len(returns) >= 20 else 0.0
        algo_score = calc_algo_score(change_5d, change_20d, ma_trend, rsi, macd_signal, bb_pos, profile=summary_profile)
        return {
            "code": code,
            "close": round(latest, 2),
            "change_5d": round(change_5d, 2),
            "change_20d": round(change_20d, 2),
            "ma_trend": ma_trend,
            "rsi": round(rsi, 1),
            "macd": macd_signal,
            "bb_pos": round(bb_pos, 2),
            "vol_ratio": round(vol_ratio, 2),
            "volatility": round(volatility, 4),
            "algo_score": round(algo_score, 3),
        }
    except Exception:
        return None


def summarize_stocks(stock_data: Dict[str, pd.DataFrame], codes: List[str], cutoff_date: str, summary_scoring: Optional[dict] = None) -> List[dict]:
    cutoff_norm = normalize_date(cutoff_date)
    results = []
    for code in codes:
        df = stock_data.get(code)
        if df is None:
            continue
        summary = compute_stock_summary(df, code, cutoff_norm, summary_scoring=summary_scoring)
        if summary:
            results.append(summary)
    results.sort(key=lambda item: item.get("algo_score", 0), reverse=True)
    return results


def compute_market_stats(stock_data: Dict[str, pd.DataFrame], cutoff_date: str, min_valid: Optional[int] = None, regime_policy: Optional[dict] = None) -> dict:
    total = len(stock_data)
    if total == 0:
        return {
            "valid_stocks": 0,
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

    for _, df in stock_data.items():
        sub = filter_by_cutoff(df, cutoff_norm)
        if len(sub) < 30:
            continue
        close = _numeric_close_series(sub)
        if len(close) < 30 or close.iloc[-1] <= 0:
            continue
        latest = float(close.iloc[-1])
        c5 = calc_pct_change(latest, close, 5)
        c20 = calc_pct_change(latest, close, 20)
        ma20 = float(close.iloc[-20:].mean()) if len(close) >= 20 else latest
        vol = float(close.pct_change().dropna().iloc[-20:].std()) if len(close) >= 20 else 0.0
        valid_count += 1
        changes_5d.append(c5)
        changes_20d.append(c20)
        volatilities.append(vol)
        if latest > ma20:
            above_ma20 += 1

    if valid_count < min_valid:
        return {
            "valid_stocks": valid_count,
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

    avg_change_5d = sum(changes_5d) / valid_count
    median_change_5d = sorted(changes_5d)[len(changes_5d) // 2]
    avg_change_20d = sum(changes_20d) / valid_count
    median_change_20d = sorted(changes_20d)[len(changes_20d) // 2]
    avg_volatility = sum(volatilities) / valid_count
    above_ma20_ratio = above_ma20 / valid_count
    market_breadth = sum(1 for item in changes_5d if item > 0) / valid_count

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
