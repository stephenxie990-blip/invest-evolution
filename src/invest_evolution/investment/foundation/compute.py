from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import sqrt
from threading import local
from typing import Any, Deque, Dict, Generic, List, Optional, TypeVar, cast

import logging
import pandas as pd

from invest_evolution.config import normalize_date

logger = logging.getLogger(__name__)

# Compute data adapter


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    return cast(pd.Series, pd.to_numeric(frame[column], errors="coerce")).dropna()


def get_date_col(df: pd.DataFrame) -> Optional[str]:
    if "trade_date" in df.columns:
        return "trade_date"
    if "date" in df.columns:
        return "date"
    return None


def filter_by_cutoff(df: pd.DataFrame, cutoff_norm: str) -> pd.DataFrame:
    date_col = get_date_col(df)
    if date_col is None:
        return pd.DataFrame()
    dates_norm = df[date_col].apply(normalize_date)
    return df.loc[dates_norm <= cutoff_norm].copy()


# Compute factor scoring


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


# Compute batch snapshot

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


# Compute features

def compute_stock_summary(df: pd.DataFrame, code: str, cutoff_norm: str, summary_scoring: Optional[dict] = None) -> Optional[dict]:
    try:
        return build_batch_summary(df, code, cutoff_norm, summary_scoring=summary_scoring)
    except Exception:
        return None


def summarize_stock_batches(
    stock_data: Dict[str, pd.DataFrame],
    codes: List[str],
    cutoff_date: str,
    summary_scoring: Optional[dict] = None,
) -> List[StockBatchSummary]:
    cutoff_norm = normalize_date(cutoff_date)
    results: List[StockBatchSummary] = []
    for code in codes:
        df = stock_data.get(code)
        if df is None:
            continue
        try:
            batch = build_batch_indicator_snapshot(df, cutoff_norm, summary_scoring=summary_scoring)
            if batch is None:
                continue
            summary = build_batch_summary(df, code, cutoff_norm, summary_scoring=summary_scoring)
            if summary is None:
                continue
            results.append(StockBatchSummary(code=code, batch=batch, summary=summary))
        except Exception:
            continue
    results.sort(key=lambda item: item.summary.get("algo_score", 0), reverse=True)
    return results


def summarize_stocks(stock_data: Dict[str, pd.DataFrame], codes: List[str], cutoff_date: str, summary_scoring: Optional[dict] = None) -> List[dict]:
    return [
        item.summary
        for item in summarize_stock_batches(stock_data, codes, cutoff_date, summary_scoring=summary_scoring)
    ]
# Compute indicators legacy




def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    return cast(pd.Series, numeric_series(frame, column))


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


# Compute indicators v2

_INDICATOR_REGISTRY_LOCAL = local()

T = TypeVar("T")


class RollingWindow(Generic[T]):
    def __init__(self, size: int):
        self.size = max(1, int(size or 1))
        self._items: Deque[T] = deque(maxlen=self.size)

    def add(self, item: T) -> None:
        self._items.appendleft(item)

    def clear(self) -> None:
        self._items.clear()

    def to_list(self) -> list[T]:
        return list(self._items)

    @property
    def latest(self) -> Optional[T]:
        return self._items[0] if self._items else None

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, index: int) -> T:
        return list(self._items)[index]


@dataclass
class IndicatorSnapshot:
    name: str
    current: Any
    previous: Any
    samples: int
    is_ready: bool


class BaseIndicator:
    def __init__(self, name: str, *, warmup_period: int = 1, window_size: int = 2):
        self.name = str(name)
        self.warmup_period = max(1, int(warmup_period or 1))
        self.window = RollingWindow[Any](max(2, int(window_size or 2)))
        self.samples = 0
        self.current: Any = None
        self.previous: Any = None

    @property
    def is_ready(self) -> bool:
        return self.samples >= self.warmup_period

    def reset(self) -> None:
        self.window.clear()
        self.samples = 0
        self.current = None
        self.previous = None
        self._reset_state()

    def _reset_state(self) -> None:
        """Hook for subclasses that keep extra rolling state beyond window/current values."""

    def update(self, input_value: Any) -> Any:
        value = self._compute(input_value)
        self.samples += 1
        self.previous = self.current
        self.current = value
        self.window.add(value)
        return value

    def snapshot(self) -> IndicatorSnapshot:
        return IndicatorSnapshot(
            name=self.name,
            current=self.current,
            previous=self.previous,
            samples=self.samples,
            is_ready=self.is_ready,
        )

    def _compute(self, input_value: Any) -> Any:
        raise NotImplementedError


class SimpleMovingAverageIndicator(BaseIndicator):
    def __init__(self, period: int, *, name: str | None = None):
        self.period = max(1, int(period))
        self._values = RollingWindow[float](self.period)
        super().__init__(name or f"SMA({self.period})", warmup_period=self.period)

    def _reset_state(self) -> None:
        self._values.clear()

    def _compute(self, input_value: Any) -> float | None:
        value = _as_float(input_value)
        if value is None:
            return self.current
        self._values.add(value)
        values = self._values.to_list()
        return float(sum(values) / len(values)) if values else None


class ExponentialMovingAverageIndicator(BaseIndicator):
    def __init__(self, period: int, *, name: str | None = None):
        self.period = max(1, int(period))
        self.multiplier = 2.0 / (self.period + 1)
        self._ema: float | None = None
        super().__init__(name or f"EMA({self.period})", warmup_period=self.period)

    def _reset_state(self) -> None:
        self._ema = None

    def _compute(self, input_value: Any) -> float | None:
        value = _as_float(input_value)
        if value is None:
            return self.current
        if self._ema is None:
            self._ema = value
        else:
            self._ema = (value - self._ema) * self.multiplier + self._ema
        return float(self._ema)


class RateOfChangeIndicator(BaseIndicator):
    def __init__(self, period: int, *, name: str | None = None):
        self.period = max(1, int(period))
        self._values = RollingWindow[float](self.period + 1)
        super().__init__(name or f"ROC({self.period})", warmup_period=self.period + 1)

    def _reset_state(self) -> None:
        self._values.clear()

    def _compute(self, input_value: Any) -> float | None:
        value = _as_float(input_value)
        if value is None:
            return self.current
        self._values.add(value)
        values = self._values.to_list()
        if len(values) <= self.period:
            return 0.0
        current = values[0]
        reference = values[self.period]
        if reference == 0:
            return 0.0
        return float((current / reference - 1.0) * 100.0)


class RelativeStrengthIndexIndicator(BaseIndicator):
    def __init__(self, period: int = 14, *, name: str | None = None):
        self.period = max(1, int(period))
        self._prev_price: float | None = None
        self._gains = RollingWindow[float](self.period)
        self._losses = RollingWindow[float](self.period)
        super().__init__(name or f"RSI({self.period})", warmup_period=self.period + 1)

    def _reset_state(self) -> None:
        self._prev_price = None
        self._gains.clear()
        self._losses.clear()

    def _compute(self, input_value: Any) -> float:
        price = _as_float(input_value)
        if price is None:
            return float(self.current or 50.0)
        if self._prev_price is None:
            self._prev_price = price
            return 50.0
        delta = price - self._prev_price
        self._prev_price = price
        self._gains.add(max(delta, 0.0))
        self._losses.add(max(-delta, 0.0))
        gains = self._gains.to_list()
        losses = self._losses.to_list()
        avg_gain = sum(gains) / len(gains) if gains else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return float(100.0 - (100.0 / (1.0 + rs)))


class AverageTrueRangeIndicator(BaseIndicator):
    def __init__(self, period: int = 14, *, name: str | None = None):
        self.period = max(1, int(period))
        self._prev_close: float | None = None
        self._trs = RollingWindow[float](self.period)
        super().__init__(name or f"ATR({self.period})", warmup_period=self.period)

    def _reset_state(self) -> None:
        self._prev_close = None
        self._trs.clear()

    def _compute(self, input_value: Any) -> float | None:
        bar = _coerce_bar(input_value)
        if bar is None:
            return self.current
        high = bar["high"]
        low = bar["low"]
        close = bar["close"]
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._prev_close = close
        self._trs.add(tr)
        values = self._trs.to_list()
        return float(sum(values) / len(values)) if values else None


class BollingerBandsIndicator(BaseIndicator):
    def __init__(self, period: int = 20, stddev: float = 2.0, *, name: str | None = None):
        self.period = max(1, int(period))
        self.stddev = float(stddev)
        self._values = RollingWindow[float](self.period)
        super().__init__(name or f"BOLL({self.period},{self.stddev})", warmup_period=self.period)

    def _reset_state(self) -> None:
        self._values.clear()

    def _compute(self, input_value: Any) -> dict[str, float] | None:
        value = _as_float(input_value)
        if value is None:
            return self.current
        self._values.add(value)
        values = self._values.to_list()
        if not values:
            return None
        mean = sum(values) / len(values)
        if len(values) > 1:
            variance = sum((item - mean) ** 2 for item in values) / (len(values) - 1)
            std = sqrt(variance)
        else:
            std = 0.0
        upper = mean + self.stddev * std
        lower = mean - self.stddev * std
        position = 0.5 if upper == lower else (value - lower) / (upper - lower)
        width = 0.0 if mean == 0 else (upper - lower) / mean
        return {
            "middle": float(mean),
            "upper": float(upper),
            "lower": float(lower),
            "position": float(max(0.0, min(1.0, position))),
            "width": float(width),
        }


class MovingAverageConvergenceDivergenceIndicator(BaseIndicator):
    def __init__(self, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9, *, name: str | None = None):
        self.fast = ExponentialMovingAverageIndicator(fast_period, name=f"EMA({fast_period})")
        self.slow = ExponentialMovingAverageIndicator(slow_period, name=f"EMA({slow_period})")
        self.signal_indicator = ExponentialMovingAverageIndicator(signal_period, name=f"EMA({signal_period})")
        self._latest_macd: float | None = None
        self._latest_signal: float | None = None
        warmup = max(fast_period, slow_period) + signal_period
        super().__init__(name or "MACD", warmup_period=warmup)

    def _reset_state(self) -> None:
        self.fast.reset()
        self.slow.reset()
        self.signal_indicator.reset()
        self._latest_macd = None
        self._latest_signal = None

    def _compute(self, input_value: Any) -> dict[str, float | str] | None:
        price = _as_float(input_value)
        if price is None:
            return self.current
        fast = self.fast.update(price)
        slow = self.slow.update(price)
        if fast is None or slow is None:
            return None
        macd_value = float(fast - slow)
        signal_value = self.signal_indicator.update(macd_value)
        if signal_value is None:
            signal_value = 0.0
        histogram = macd_value - float(signal_value)
        cross = _macd_cross(self._latest_macd, self._latest_signal, macd_value, float(signal_value))
        self._latest_macd = macd_value
        self._latest_signal = float(signal_value)
        return {
            "macd": float(macd_value),
            "signal": float(signal_value),
            "histogram": float(histogram),
            "cross": cross,
        }


class VolumeRatioIndicator(BaseIndicator):
    def __init__(self, short_period: int = 5, long_period: int = 20, *, name: str | None = None):
        self.short_period = max(1, int(short_period))
        self.long_period = max(self.short_period, int(long_period))
        self._short_values = RollingWindow[float](self.short_period)
        self._long_values = RollingWindow[float](self.long_period)
        super().__init__(name or f"VOLR({self.short_period},{self.long_period})", warmup_period=self.long_period)

    def _reset_state(self) -> None:
        self._short_values.clear()
        self._long_values.clear()

    def _compute(self, input_value: Any) -> float | None:
        value = _as_float(input_value)
        if value is None:
            return self.current
        self._short_values.add(value)
        self._long_values.add(value)
        short_values = self._short_values.to_list()
        long_values = self._long_values.to_list()
        long_avg = sum(long_values) / len(long_values) if long_values else 0.0
        if long_avg == 0:
            return 1.0
        short_avg = sum(short_values) / len(short_values) if short_values else 0.0
        return float(short_avg / long_avg)


@dataclass
class IndicatorEngineSnapshot:
    samples: int
    latest_trade_date: str | None
    latest_close: float | None
    indicators: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "latest_trade_date": self.latest_trade_date,
            "latest_close": self.latest_close,
            "indicators": self.indicators,
        }


class IndicatorRegistry:
    @staticmethod
    def _build_default_registry() -> dict[str, BaseIndicator]:
        return {
            "sma_5": SimpleMovingAverageIndicator(5),
            "sma_10": SimpleMovingAverageIndicator(10),
            "sma_20": SimpleMovingAverageIndicator(20),
            "sma_60": SimpleMovingAverageIndicator(60),
            "ema_12": ExponentialMovingAverageIndicator(12),
            "ema_26": ExponentialMovingAverageIndicator(26),
            "ema_50": ExponentialMovingAverageIndicator(50),
            "rsi_14": RelativeStrengthIndexIndicator(14),
            "atr_14": AverageTrueRangeIndicator(14),
            "roc_10": RateOfChangeIndicator(10),
            "macd_12_26_9": MovingAverageConvergenceDivergenceIndicator(12, 26, 9),
            "bollinger_20": BollingerBandsIndicator(20, 2.0),
            "volume_ratio_5_20": VolumeRatioIndicator(5, 20),
        }

    @staticmethod
    def default() -> dict[str, BaseIndicator]:
        registry = getattr(_INDICATOR_REGISTRY_LOCAL, "default_registry", None)
        if registry is None:
            registry = IndicatorRegistry._build_default_registry()
            _INDICATOR_REGISTRY_LOCAL.default_registry = registry
            return registry
        for indicator in registry.values():
            indicator.reset()
        return registry


def compute_indicator_snapshot(frame: pd.DataFrame) -> dict[str, Any]:
    if frame is None or frame.empty:
        return {
            "samples": 0,
            "latest_trade_date": None,
            "latest_close": None,
            "indicators": {},
            "ready": False,
        }
    date_col = get_date_col(frame)
    indicators = IndicatorRegistry.default()
    ordered = (
        frame.sort_values(date_col).reset_index(drop=True)
        if date_col and date_col in frame.columns
        else frame.reset_index(drop=True)
    )
    columns = list(ordered.columns)
    column_index = {name: idx for idx, name in enumerate(columns)}
    date_idx = column_index.get(date_col) if date_col and date_col in column_index else None
    close_idx = column_index.get("close")
    volume_idx = column_index.get("volume")
    high_idx = column_index.get("high")
    low_idx = column_index.get("low")
    latest_trade_date = None
    latest_close: float | None = None
    for row in ordered.itertuples(index=False, name=None):
        trade_date = row[date_idx] if date_idx is not None else None
        close_raw = row[close_idx] if close_idx is not None else None
        volume_raw = row[volume_idx] if volume_idx is not None else None
        high_raw = row[high_idx] if high_idx is not None else None
        low_raw = row[low_idx] if low_idx is not None else None
        latest_trade_date = str(trade_date or latest_trade_date or "") or latest_trade_date
        close = _as_float(close_raw)
        volume = _as_float(volume_raw)
        high = _as_float(high_raw)
        low = _as_float(low_raw)
        latest_close = close if close is not None else latest_close
        if close is not None:
            indicators["sma_5"].update(close)
            indicators["sma_10"].update(close)
            indicators["sma_20"].update(close)
            indicators["sma_60"].update(close)
            indicators["ema_12"].update(close)
            indicators["ema_26"].update(close)
            indicators["ema_50"].update(close)
            indicators["rsi_14"].update(close)
            indicators["roc_10"].update(close)
            indicators["macd_12_26_9"].update(close)
            indicators["bollinger_20"].update(close)
        if close is not None and high is not None and low is not None:
            indicators["atr_14"].update({"high": high, "low": low, "close": close})
        if volume is not None:
            indicators["volume_ratio_5_20"].update(volume)

    raw = {name: indicator.current for name, indicator in indicators.items()}
    sma_5 = _as_float(raw.get("sma_5"))
    sma_10 = _as_float(raw.get("sma_10"))
    sma_20 = _as_float(raw.get("sma_20"))
    ma_stack = "mixed"
    if all(value is not None for value in [latest_close, sma_5, sma_10, sma_20]):
        assert latest_close is not None
        assert sma_5 is not None
        assert sma_10 is not None
        assert sma_20 is not None
        close_value = latest_close
        sma_5_value = sma_5
        sma_10_value = sma_10
        sma_20_value = sma_20
        if close_value > sma_5_value > sma_10_value > sma_20_value:
            ma_stack = "bullish"
        elif close_value < sma_5_value < sma_10_value < sma_20_value:
            ma_stack = "bearish"

    output = {
        "sma_5": _round_value(raw.get("sma_5")),
        "sma_10": _round_value(raw.get("sma_10")),
        "sma_20": _round_value(raw.get("sma_20")),
        "sma_60": _round_value(raw.get("sma_60")),
        "ema_12": _round_value(raw.get("ema_12")),
        "ema_26": _round_value(raw.get("ema_26")),
        "ema_50": _round_value(raw.get("ema_50")),
        "rsi_14": _round_value(raw.get("rsi_14")),
        "atr_14": _round_value(raw.get("atr_14")),
        "roc_10": _round_value(raw.get("roc_10")),
        "macd_12_26_9": _round_value(raw.get("macd_12_26_9")),
        "bollinger_20": _round_value(raw.get("bollinger_20")),
        "volume_ratio_5_20": _round_value(raw.get("volume_ratio_5_20")),
        "ma_stack": ma_stack,
    }
    output["is_ready"] = {
        name: indicator.is_ready for name, indicator in indicators.items()
    }
    return {
        "samples": int(len(ordered)),
        "latest_trade_date": latest_trade_date,
        "latest_close": _round_value(latest_close),
        "indicators": output,
        "ready": all(indicator.is_ready for indicator in indicators.values() if indicator.samples > 0),
    }


def _coerce_bar(input_value: Any) -> dict[str, float] | None:
    if input_value is None:
        return None
    if isinstance(input_value, pd.Series):
        high = _as_float(input_value.get("high"))
        low = _as_float(input_value.get("low"))
        close = _as_float(input_value.get("close"))
    elif isinstance(input_value, dict):
        high = _as_float(input_value.get("high"))
        low = _as_float(input_value.get("low"))
        close = _as_float(input_value.get("close"))
    else:
        high = _as_float(getattr(input_value, "high", None))
        low = _as_float(getattr(input_value, "low", None))
        close = _as_float(getattr(input_value, "close", None))
    if high is None or low is None or close is None:
        return None
    return {"high": high, "low": low, "close": close}


def _macd_cross(previous_macd: float | None, previous_signal: float | None, current_macd: float, current_signal: float) -> str:
    if previous_macd is None or previous_signal is None:
        if current_macd > current_signal:
            return "bullish"
        if current_macd < current_signal:
            return "bearish"
        return "neutral"
    if previous_macd <= previous_signal and current_macd > current_signal:
        return "golden_cross"
    if previous_macd >= previous_signal and current_macd < current_signal:
        return "dead_cross"
    if current_macd > current_signal:
        return "bullish"
    if current_macd < current_signal:
        return "bearish"
    return "neutral"


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError) as exc:
        logger.debug("pd.isna check failed for %r: %s", value, exc)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_value(item) for item in value]
    numeric = _as_float(value)
    if numeric is None:
        return value
    return round(float(numeric), 6)


# Compute market stats






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

__all__ = [name for name in globals() if not name.startswith('_')]
