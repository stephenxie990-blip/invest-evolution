import numpy as np
import pandas as pd

from invest.foundation.compute.batch_snapshot import BatchIndicatorSnapshot, StockBatchSummary
from invest.models import DefensiveLowVolModel, MeanReversionModel, ValueQualityModel, create_investment_model, list_models


def _make_stock_data(n=8, days=120):
    dates = pd.date_range("2023-01-01", periods=days, freq="B")
    stock_data = {}
    rng = np.random.default_rng(7)
    for i in range(n):
        code = f"sh.{600100 + i}"
        drift = -0.01 if i % 2 == 0 else 0.03
        close = 12 + np.cumsum(rng.normal(drift, 0.35, len(dates)))
        close = np.maximum(close, 1)
        stock_data[code] = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "trade_date": dates.strftime("%Y%m%d"),
            "open": close * 0.998,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": rng.integers(100000, 10000000, len(dates)).astype(float),
            "pct_chg": pd.Series(close).pct_change().fillna(0) * 100,
            "pe_ttm": np.full(len(dates), 8 + i * 2, dtype=float),
            "pb": np.full(len(dates), 0.8 + i * 0.25, dtype=float),
            "roe": np.full(len(dates), 10 + i, dtype=float),
            "market_cap": np.full(len(dates), 50e8 + i * 5e8, dtype=float),
            "relative_strength_hs300": np.full(len(dates), -1.5 + i * 0.8, dtype=float),
            "breakout20": np.full(len(dates), 1 if i % 3 == 0 else 0, dtype=float),
        })
    return stock_data


def test_mean_reversion_model_outputs_dual_channel():
    model = MeanReversionModel(runtime_overrides={"top_n": 4, "max_positions": 3})
    out = model.process(_make_stock_data(), "20230601")

    assert out.model_name == "mean_reversion"
    assert out.signal_packet.model_name == "mean_reversion"
    assert out.signal_packet.max_positions == 3
    assert len(out.signal_packet.signals) >= 1
    assert out.agent_context.candidate_codes
    assert any("reversion_score" in signal.factor_values for signal in out.signal_packet.signals)


def test_value_quality_model_outputs_dual_channel():
    model = ValueQualityModel(runtime_overrides={"top_n": 4, "max_positions": 3})
    out = model.process(_make_stock_data(), "20230601")

    assert out.model_name == "value_quality"
    assert out.signal_packet.model_name == "value_quality"
    assert out.signal_packet.max_positions == 3
    assert len(out.signal_packet.signals) >= 1
    assert out.agent_context.candidate_codes
    assert any("value_quality_score" in signal.factor_values for signal in out.signal_packet.signals)
    assert any("regime_adjusted_score" in signal.factor_values for signal in out.signal_packet.signals)


def test_value_quality_regime_adjusted_score_penalizes_hot_volatile_oscillation_candidate():
    model = ValueQualityModel()
    item = StockBatchSummary(
        code="sh.688001",
        batch=BatchIndicatorSnapshot(
            samples=120,
            latest_trade_date="20230601",
            latest_close=10.0,
            change_5d=6.0,
            change_20d=18.0,
            sma_5=9.8,
            sma_20=9.3,
            ma_trend="多头",
            rsi=76.0,
            macd="看多",
            bb_pos=0.9,
            vol_ratio=1.8,
            volatility=0.05,
            above_ma20=True,
            streaming_snapshot={},
        ),
        summary={},
    )

    adjusted = model._regime_adjusted_score(
        item,
        base_score=0.8,
        regime="oscillation",
        fundamentals={"relative_strength_hs300": -3.5, "breakout20": 0},
    )

    assert adjusted < 0.8


def test_value_quality_regime_adjusted_score_rewards_stable_main_board_oscillation_candidate():
    model = ValueQualityModel()
    item = StockBatchSummary(
        code="sh.600001",
        batch=BatchIndicatorSnapshot(
            samples=120,
            latest_trade_date="20230601",
            latest_close=10.0,
            change_5d=1.2,
            change_20d=4.5,
            sma_5=9.9,
            sma_20=9.85,
            ma_trend="交叉",
            rsi=52.0,
            macd="中性",
            bb_pos=0.52,
            vol_ratio=1.1,
            volatility=0.022,
            above_ma20=True,
            streaming_snapshot={},
        ),
        summary={},
    )

    adjusted = model._regime_adjusted_score(
        item,
        base_score=0.8,
        regime="oscillation",
        fundamentals={"relative_strength_hs300": 2.2, "breakout20": 1},
    )

    assert adjusted > 0.8


def test_value_quality_regime_adjusted_score_penalizes_quality_trap_in_range_candidate():
    model = ValueQualityModel()
    item = StockBatchSummary(
        code="sh.600002",
        batch=BatchIndicatorSnapshot(
            samples=120,
            latest_trade_date="20230601",
            latest_close=10.0,
            change_5d=0.6,
            change_20d=2.0,
            sma_5=9.92,
            sma_20=9.88,
            ma_trend="交叉",
            rsi=51.0,
            macd="中性",
            bb_pos=0.50,
            vol_ratio=1.0,
            volatility=0.023,
            above_ma20=True,
            streaming_snapshot={},
        ),
        summary={},
    )

    weak = model._regime_adjusted_score(
        item,
        base_score=0.8,
        regime="oscillation",
        fundamentals={"relative_strength_hs300": -2.0, "breakout20": 0},
    )
    confirmed = model._regime_adjusted_score(
        item,
        base_score=0.8,
        regime="oscillation",
        fundamentals={"relative_strength_hs300": 2.0, "breakout20": 1},
    )

    assert weak < confirmed


def test_value_quality_main_board_bonus_is_conditional_not_static():
    model = ValueQualityModel()
    item = StockBatchSummary(
        code="sh.600003",
        batch=BatchIndicatorSnapshot(
            samples=120,
            latest_trade_date="20230601",
            latest_close=10.0,
            change_5d=1.0,
            change_20d=3.5,
            sma_5=9.95,
            sma_20=9.90,
            ma_trend="交叉",
            rsi=52.0,
            macd="中性",
            bb_pos=0.48,
            vol_ratio=1.0,
            volatility=0.024,
            above_ma20=True,
            streaming_snapshot={},
        ),
        summary={},
    )

    weak_main_board = model._regime_adjusted_score(
        item,
        base_score=0.8,
        regime="oscillation",
        fundamentals={"relative_strength_hs300": -0.8, "breakout20": 0},
    )
    strong_main_board = model._regime_adjusted_score(
        item,
        base_score=0.8,
        regime="oscillation",
        fundamentals={"relative_strength_hs300": 1.2, "breakout20": 1},
    )

    assert strong_main_board > weak_main_board


def test_value_quality_diversifies_oscillation_selection_by_code_bucket():
    model = ValueQualityModel()
    ranked = [
        {"code": "sh.688001", "regime_adjusted_score": 0.95},
        {"code": "sh.688002", "regime_adjusted_score": 0.94},
        {"code": "sz.300001", "regime_adjusted_score": 0.93},
        {"code": "sz.300002", "regime_adjusted_score": 0.92},
        {"code": "sh.600001", "regime_adjusted_score": 0.91},
        {"code": "sh.600002", "regime_adjusted_score": 0.90},
    ]

    selected = model._select_diversified_candidates(ranked, regime="oscillation", top_n=4)
    selected_codes = [item["code"] for item in selected]

    assert len(selected_codes) == 4
    assert sum(1 for code in selected_codes if code.startswith("sh.688")) <= 1
    assert sum(1 for code in selected_codes if code.startswith("sz.300")) <= 1


def test_model_registry_includes_new_models():
    models = list_models()
    assert "momentum" in models
    assert "mean_reversion" in models
    assert "value_quality" in models
    assert "defensive_low_vol" in models
    assert create_investment_model("mean_reversion").model_name == "mean_reversion"
    assert create_investment_model("value_quality").model_name == "value_quality"
    assert create_investment_model("defensive_low_vol").model_name == "defensive_low_vol"



def test_defensive_low_vol_model_outputs_dual_channel():
    model = DefensiveLowVolModel(runtime_overrides={"top_n": 4, "max_positions": 3})
    out = model.process(_make_stock_data(), "20230601")

    assert out.model_name == "defensive_low_vol"
    assert out.signal_packet.model_name == "defensive_low_vol"
    assert out.signal_packet.max_positions == 3
    assert len(out.signal_packet.signals) >= 1
    assert out.agent_context.candidate_codes
    assert any("defensive_score" in signal.factor_values for signal in out.signal_packet.signals)
