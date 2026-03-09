import numpy as np
import pandas as pd

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
