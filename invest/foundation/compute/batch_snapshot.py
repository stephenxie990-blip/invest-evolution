from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, cast

import pandas as pd

from .data_adapter import filter_by_cutoff, numeric_series
from .factors import calc_algo_score
from .indicators_v2 import compute_indicator_snapshot


def _numeric_close_series(frame: pd.DataFrame) -> pd.Series:
    return cast(pd.Series, numeric_series(frame, "close"))


def _macd_cross_to_legacy_label(cross: str) -> str:
    mapping = {
        "golden_cross": "金叉",
        "dead_cross": "死叉",
        "bullish": "看多",
        "bearish": "看空",
        "neutral": "中性",
    }
    return mapping.get(str(cross or "").strip().lower(), "中性")


def _resolve_ma_trend(
    *,
    latest_close: float,
    sma_5: float,
    sma_20: float,
    ma_bull_ratio: float,
    ma_bear_ratio: float,
) -> str:
    if latest_close > sma_5 > sma_20 * ma_bull_ratio:
        return "多头"
    if latest_close < sma_5 < sma_20 * ma_bear_ratio:
        return "空头"
    if sma_5 > sma_20 * ma_bull_ratio:
        return "多头"
    if sma_5 < sma_20 * ma_bear_ratio:
        return "空头"
    return "交叉"


def _pct_change(series: pd.Series, n: int) -> float:
    if len(series) < n:
        return 0.0
    latest = float(series.iloc[-1])
    past = float(series.iloc[-n])
    return (latest / past - 1.0) * 100.0 if past > 0 else 0.0


def _volatility(close: pd.Series) -> float:
    returns = close.pct_change().dropna()
    if len(returns) < 20:
        return 0.0
    return float(returns.iloc[-20:].std())


@dataclass(frozen=True)
class BatchIndicatorSnapshot:
    samples: int
    latest_trade_date: str | None
    latest_close: float
    change_5d: float
    change_20d: float
    sma_5: float
    sma_20: float
    ma_trend: str
    rsi: float
    macd: str
    bb_pos: float
    vol_ratio: float
    volatility: float
    above_ma20: bool
    streaming_snapshot: dict[str, Any]

    def to_summary_dict(self, code: str, *, algo_score: float) -> dict[str, Any]:
        return {
            "code": code,
            "close": round(self.latest_close, 2),
            "change_5d": round(self.change_5d, 2),
            "change_20d": round(self.change_20d, 2),
            "ma_trend": self.ma_trend,
            "rsi": round(self.rsi, 1),
            "macd": self.macd,
            "bb_pos": round(self.bb_pos, 2),
            "vol_ratio": round(self.vol_ratio, 2),
            "volatility": round(self.volatility, 4),
            "algo_score": round(algo_score, 3),
        }


@dataclass(frozen=True)
class StockBatchSummary:
    code: str
    batch: BatchIndicatorSnapshot
    summary: dict[str, Any]


def build_batch_indicator_snapshot(
    frame: pd.DataFrame,
    cutoff_norm: str,
    *,
    summary_scoring: Optional[dict] = None,
) -> BatchIndicatorSnapshot | None:
    sub = filter_by_cutoff(frame, cutoff_norm)
    if len(sub) < 30:
        return None
    close = _numeric_close_series(sub)
    if len(close) < 30 or float(close.iloc[-1]) <= 0:
        return None

    snapshot = compute_indicator_snapshot(sub)
    indicators = dict(snapshot.get("indicators") or {})
    latest_close = float(snapshot.get("latest_close") or close.iloc[-1])
    sma_5 = float(indicators.get("sma_5") or close.iloc[-5:].mean() or latest_close)
    sma_20 = float(indicators.get("sma_20") or close.iloc[-20:].mean() or latest_close)
    summary_profile = dict(summary_scoring or {})
    logic = dict(summary_profile.get("logic", {}) or {})
    ma_bull_ratio = float(logic.get("ma_bull_ratio", 1.0) or 1.0)
    ma_bear_ratio = float(logic.get("ma_bear_ratio", 1.0) or 1.0)
    ma_trend = _resolve_ma_trend(
        latest_close=latest_close,
        sma_5=sma_5,
        sma_20=sma_20,
        ma_bull_ratio=ma_bull_ratio,
        ma_bear_ratio=ma_bear_ratio,
    )
    bollinger = dict(indicators.get("bollinger_20") or {})
    macd = dict(indicators.get("macd_12_26_9") or {})
    rsi = float(indicators.get("rsi_14") or 50.0)
    bb_pos = float(bollinger.get("position") or 0.5)
    vol_ratio = float(indicators.get("volume_ratio_5_20") or 1.0)
    return BatchIndicatorSnapshot(
        samples=int(snapshot.get("samples") or len(sub)),
        latest_trade_date=cast(Optional[str], snapshot.get("latest_trade_date")),
        latest_close=latest_close,
        change_5d=_pct_change(close, 5),
        change_20d=_pct_change(close, 20),
        sma_5=sma_5,
        sma_20=sma_20,
        ma_trend=ma_trend,
        rsi=rsi,
        macd=_macd_cross_to_legacy_label(str(macd.get("cross") or "neutral")),
        bb_pos=bb_pos,
        vol_ratio=vol_ratio,
        volatility=_volatility(close),
        above_ma20=latest_close > sma_20,
        streaming_snapshot=snapshot,
    )


def build_batch_summary(
    frame: pd.DataFrame,
    code: str,
    cutoff_norm: str,
    *,
    summary_scoring: Optional[dict] = None,
) -> dict[str, Any] | None:
    batch = build_batch_indicator_snapshot(frame, cutoff_norm, summary_scoring=summary_scoring)
    if batch is None:
        return None
    summary_profile = dict(summary_scoring or {})
    algo_score = calc_algo_score(
        batch.change_5d,
        batch.change_20d,
        batch.ma_trend,
        batch.rsi,
        batch.macd,
        batch.bb_pos,
        profile=summary_profile,
    )
    return batch.to_summary_dict(code, algo_score=algo_score)


__all__ = [
    "BatchIndicatorSnapshot",
    "StockBatchSummary",
    "build_batch_indicator_snapshot",
    "build_batch_summary",
]
