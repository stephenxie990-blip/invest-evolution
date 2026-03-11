from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import sqrt
from typing import Any, Deque, Generic, Iterable, Optional, TypeVar

import pandas as pd

from .indicators import get_date_col

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
        pass

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
        variance = sum((item - mean) ** 2 for item in values) / len(values)
        std = sqrt(variance)
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
    def default() -> dict[str, BaseIndicator]:
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


def compute_indicator_snapshot(frame: pd.DataFrame) -> dict[str, Any]:
    if frame is None or frame.empty:
        return {
            "samples": 0,
            "latest_trade_date": None,
            "latest_close": None,
            "indicators": {},
            "ready": False,
        }
    date_col = get_date_col(frame) or "trade_date"
    indicators = IndicatorRegistry.default()
    ordered = frame.sort_values(date_col).reset_index(drop=True)
    latest_trade_date = None
    latest_close: float | None = None
    for _, row in ordered.iterrows():
        latest_trade_date = str(row.get(date_col) or latest_trade_date or "") or latest_trade_date
        close = _as_float(row.get("close"))
        volume = _as_float(row.get("volume"))
        bar = _coerce_bar(row)
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
        if bar is not None:
            indicators["atr_14"].update(bar)
        if volume is not None:
            indicators["volume_ratio_5_20"].update(volume)

    raw = {name: indicator.current for name, indicator in indicators.items()}
    sma_5 = _as_float(raw.get("sma_5"))
    sma_10 = _as_float(raw.get("sma_10"))
    sma_20 = _as_float(raw.get("sma_20"))
    ma_stack = "mixed"
    if all(value is not None for value in [latest_close, sma_5, sma_10, sma_20]):
        if latest_close > sma_5 > sma_10 > sma_20:
            ma_stack = "bullish"
        elif latest_close < sma_5 < sma_10 < sma_20:
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
    except Exception:
        pass
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


__all__ = [
    "AverageTrueRangeIndicator",
    "BaseIndicator",
    "BollingerBandsIndicator",
    "ExponentialMovingAverageIndicator",
    "IndicatorRegistry",
    "IndicatorSnapshot",
    "MovingAverageConvergenceDivergenceIndicator",
    "RateOfChangeIndicator",
    "RelativeStrengthIndexIndicator",
    "RollingWindow",
    "SimpleMovingAverageIndicator",
    "VolumeRatioIndicator",
    "compute_indicator_snapshot",
]
