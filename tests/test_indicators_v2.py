import pandas as pd

from invest.foundation.compute.indicators_v2 import (
    ExponentialMovingAverageIndicator,
    RelativeStrengthIndexIndicator,
    RollingWindow,
    SimpleMovingAverageIndicator,
    compute_indicator_snapshot,
)


def test_rolling_window_keeps_latest_first():
    window = RollingWindow[int](3)
    window.add(1)
    window.add(2)
    window.add(3)
    window.add(4)

    assert window.to_list() == [4, 3, 2]
    assert len(window) == 3
    assert window.latest == 4


def test_numeric_indicators_reach_ready_state():
    sma = SimpleMovingAverageIndicator(3)
    ema = ExponentialMovingAverageIndicator(3)
    rsi = RelativeStrengthIndexIndicator(3)

    for value in [10.0, 11.0, 12.0, 13.0, 14.0]:
        sma.update(value)
        ema.update(value)
        rsi.update(value)

    assert sma.is_ready is True
    assert ema.is_ready is True
    assert rsi.is_ready is True
    assert round(float(sma.current), 2) == 13.0
    assert float(ema.current) > 0
    assert float(rsi.current) >= 50.0


def test_compute_indicator_snapshot_returns_core_metrics():
    frame = pd.DataFrame([
        {
            "trade_date": f"202401{day:02d}",
            "open": 10 + day * 0.1,
            "high": 10.5 + day * 0.1,
            "low": 9.5 + day * 0.1,
            "close": 10 + day * 0.15,
            "volume": 1000 + day * 20,
        }
        for day in range(1, 91)
    ])

    snapshot = compute_indicator_snapshot(frame)
    indicators = snapshot["indicators"]

    assert snapshot["samples"] == 90
    assert snapshot["ready"] is True
    assert indicators["ma_stack"] == "bullish"
    assert indicators["rsi_14"] is not None
    assert indicators["atr_14"] is not None
    assert indicators["volume_ratio_5_20"] is not None
    assert indicators["macd_12_26_9"]["cross"] in {"golden_cross", "bullish", "neutral"}
