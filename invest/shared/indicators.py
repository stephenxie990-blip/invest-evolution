from typing import Optional

import numpy as np
import pandas as pd

from config import normalize_date


def _get_date_col(df: pd.DataFrame) -> Optional[str]:
    """获取 DataFrame 的日期列名（适配 trade_date / date）"""
    if "trade_date" in df.columns:
        return "trade_date"
    if "date" in df.columns:
        return "date"
    return None


def _filter_by_cutoff(df: pd.DataFrame, cutoff_norm: str) -> pd.DataFrame:
    """按截断日期过滤 DataFrame（cutoff_norm 格式 YYYYMMDD）"""
    date_col = _get_date_col(df)
    if date_col is None:
        return pd.DataFrame()
    dates_norm = df[date_col].apply(normalize_date)
    return df.loc[dates_norm <= cutoff_norm].copy()


def compute_rsi(close: pd.Series, period: int = 14) -> float:
    """计算 RSI（共享工具，供多处调用）"""
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


def compute_macd_signal(close: pd.Series) -> str:
    """计算 MACD 信号字符串（金叉/死叉/看多/看空/中性）"""
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


def compute_bb_position(close: pd.Series, period: int = 20) -> float:
    """计算布林带位置（0=下轨，1=上轨）"""
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


def compute_volume_ratio(df: pd.DataFrame) -> float:
    """计算量比（5日均量 / 20日均量）"""
    if "volume" not in df.columns:
        return 1.0
    vol = pd.to_numeric(df["volume"], errors="coerce").dropna()
    if len(vol) < 20:
        return 1.0
    avg_5 = vol.iloc[-5:].mean()
    avg_20 = vol.iloc[-20:].mean()
    return float(avg_5 / avg_20) if avg_20 > 0 else 1.0


def compute_pct_change(latest: float, series: pd.Series, n: int) -> float:
    """计算 N 日涨跌幅（%）"""
    if len(series) < n:
        return 0.0
    past = float(series.iloc[-n])
    return (latest / past - 1) * 100 if past > 0 else 0.0


def compute_algo_score(
    change_5d: float,
    change_20d: float,
    ma_trend: str,
    rsi: float,
    macd_signal: str,
    bb_pos: float,
) -> float:
    """综合算法评分（用于兜底排序）"""
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
    macd_scores = {"金叉": 0.2, "看多": 0.1, "中性": 0, "看空": -0.1, "死叉": -0.15}
    score += macd_scores.get(macd_signal, 0)
    if bb_pos < 0.3:
        score += 0.15
    elif bb_pos > 0.8:
        score -= 0.1
    return score


# ============================================================
# Part 4: 股票技术摘要

__all__ = [
    "_get_date_col",
    "_filter_by_cutoff",
    "compute_rsi",
    "compute_macd_signal",
    "compute_bb_position",
    "compute_volume_ratio",
    "compute_pct_change",
    "compute_algo_score",
]
