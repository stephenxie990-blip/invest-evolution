import numpy as np
import pandas as pd

from invest.models import MomentumModel


def _make_stock_data(n=8, days=120):
    dates = pd.date_range("2023-01-01", periods=days, freq="B")
    stock_data = {}
    rng = np.random.default_rng(42)
    for i in range(n):
        code = f"sh.{600000 + i}"
        close = 10 + np.cumsum(rng.normal(0.05, 0.4, len(dates)))
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
        })
    return stock_data


def test_momentum_model_process_outputs_dual_channel():
    model = MomentumModel(runtime_overrides={"top_n": 4, "max_positions": 3})
    out = model.process(_make_stock_data(), "20230601")

    assert out.model_name == "momentum"
    assert out.signal_packet.model_name == "momentum"
    assert out.signal_packet.max_positions == 3
    assert len(out.signal_packet.signals) >= 1
    assert out.agent_context.regime in {"bull", "bear", "oscillation"}
    assert out.agent_context.stock_summaries
    assert out.agent_context.candidate_codes
