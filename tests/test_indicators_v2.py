import pandas as pd
from typing import cast

from invest.foundation.compute.batch_snapshot import build_batch_indicator_snapshot
from invest.foundation.compute.features import summarize_stock_batches
from invest.foundation.compute import (
    calc_bb_position,
    calc_macd_signal,
    calc_rsi,
    calc_volume_ratio,
)
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


def test_compute_package_keeps_v2_indicators_out_of_legacy_public_surface():
    import invest.foundation.compute as compute_module

    assert not hasattr(compute_module, "compute_indicator_snapshot")
    assert not hasattr(compute_module, "RollingWindow")
    assert not hasattr(compute_module, "IndicatorRegistry")


def test_legacy_indicator_functions_align_with_v2_snapshot_core_values():
    frame = pd.DataFrame(
        [
            {
                "trade_date": f"202402{day:02d}",
                "open": 20 + day * 0.2,
                "high": 20.4 + day * 0.2,
                "low": 19.7 + day * 0.2,
                "close": 20 + day * 0.25 + (0.3 if day % 7 == 0 else 0.0),
                "volume": 5000 + day * 60 + (120 if day % 5 == 0 else 0),
            }
            for day in range(1, 91)
        ]
    )

    close = cast(pd.Series, frame["close"])
    snapshot = compute_indicator_snapshot(frame)
    indicators = snapshot["indicators"]

    legacy_rsi = calc_rsi(close, 14)
    legacy_bb_position = calc_bb_position(close, 20)
    legacy_volume_ratio = calc_volume_ratio(frame)
    legacy_macd_signal = calc_macd_signal(close)

    assert indicators["rsi_14"] == round(legacy_rsi, 6)
    assert indicators["bollinger_20"]["position"] == round(legacy_bb_position, 6)
    assert indicators["volume_ratio_5_20"] == round(legacy_volume_ratio, 6)

    macd_cross = indicators["macd_12_26_9"]["cross"]
    if legacy_macd_signal == "金叉":
        assert macd_cross == "golden_cross"
    elif legacy_macd_signal == "死叉":
        assert macd_cross == "dead_cross"
    elif legacy_macd_signal == "看多":
        assert macd_cross in {"bullish", "golden_cross"}
    elif legacy_macd_signal == "看空":
        assert macd_cross in {"bearish", "dead_cross"}
    else:
        assert macd_cross in {"neutral", "bullish", "bearish"}


def test_batch_snapshot_adapter_projects_v2_snapshot_into_legacy_summary_fields():
    frame = pd.DataFrame(
        [
            {
                "trade_date": f"202403{day:02d}",
                "open": 30 + day * 0.15,
                "high": 30.5 + day * 0.15,
                "low": 29.6 + day * 0.15,
                "close": 30 + day * 0.22 + (0.25 if day % 6 == 0 else 0.0),
                "volume": 8000 + day * 90 + (150 if day % 4 == 0 else 0),
            }
            for day in range(1, 91)
        ]
    )

    batch = build_batch_indicator_snapshot(frame, "20240331")

    assert batch is not None
    assert batch.samples == 31
    assert batch.macd in {"金叉", "死叉", "看多", "看空", "中性"}
    assert 0.0 <= batch.bb_pos <= 1.0
    assert batch.vol_ratio > 0.0
    assert batch.ma_trend in {"多头", "空头", "交叉"}
    assert batch.streaming_snapshot["indicators"]["rsi_14"] == round(batch.rsi, 6)


def test_features_module_no_longer_reexports_legacy_indicator_helpers():
    import invest.foundation.compute.features as features_module

    assert not hasattr(features_module, "calc_rsi")
    assert not hasattr(features_module, "calc_macd_signal")
    assert not hasattr(features_module, "calc_bb_position")
    assert not hasattr(features_module, "calc_volume_ratio")


def test_summarize_stock_batches_keeps_batch_and_summary_in_one_ranked_pass():
    frame = pd.DataFrame(
        [
            {
                "trade_date": f"202404{day:02d}",
                "open": 12 + day * 0.1,
                "high": 12.4 + day * 0.1,
                "low": 11.7 + day * 0.1,
                "close": 12 + day * 0.14,
                "volume": 3000 + day * 40,
            }
            for day in range(1, 61)
        ]
    )
    items = summarize_stock_batches({"AAA": frame}, ["AAA"], "20240430")

    assert len(items) == 1
    assert items[0].code == "AAA"
    assert items[0].summary["code"] == "AAA"
    assert items[0].summary["close"] == round(items[0].batch.latest_close, 2)
    assert items[0].batch.streaming_snapshot["latest_close"] == round(items[0].batch.latest_close, 6)
