from __future__ import annotations

from typing import cast

import pandas as pd

from . import data_adapter as _data_adapter


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    return cast(pd.Series, _data_adapter.numeric_series(frame, column))


filter_by_cutoff = _data_adapter.filter_by_cutoff
get_date_col = _data_adapter.get_date_col


def calc_rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = close.diff().iloc[-(period + 1):]
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    last_gain = gain.iloc[-1]
    last_loss = loss.iloc[-1]
    if last_loss == 0:
        return 100.0 if last_gain > 0 else 50.0
    return float(100 - (100 / (1 + last_gain / last_loss)))


def calc_macd_signal(close: pd.Series) -> str:
    if len(close) < 26:
        return "中性"
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    curr_m, curr_s = macd.iloc[-1], signal.iloc[-1]
    prev_m, prev_s = macd.iloc[-2], signal.iloc[-2]
    if prev_m <= prev_s and curr_m > curr_s:
        return "金叉"
    if prev_m >= prev_s and curr_m < curr_s:
        return "死叉"
    if curr_m > curr_s and curr_m > 0:
        return "看多"
    if curr_m < curr_s and curr_m < 0:
        return "看空"
    return "中性"


def calc_bb_position(close: pd.Series, period: int = 20) -> float:
    if len(close) < period:
        return 0.5
    recent = close.iloc[-period:]
    sma = recent.mean()
    std = recent.std()
    if std == 0:
        return 0.5
    upper = sma + 2 * std
    lower = sma - 2 * std
    pos = (float(close.iloc[-1]) - lower) / (upper - lower) if upper != lower else 0.5
    return max(0.0, min(1.0, pos))


def calc_volume_ratio(df: pd.DataFrame) -> float:
    if "volume" not in df.columns:
        return 1.0
    vol = _numeric_series(df, "volume")
    if len(vol) < 20:
        return 1.0
    avg_5 = vol.iloc[-5:].mean()
    avg_20 = vol.iloc[-20:].mean()
    return float(avg_5 / avg_20) if avg_20 > 0 else 1.0


def calc_pct_change(latest: float, series: pd.Series, n: int) -> float:
    if len(series) < n:
        return 0.0
    past = float(series.iloc[-n])
    return (latest / past - 1) * 100 if past > 0 else 0.0
