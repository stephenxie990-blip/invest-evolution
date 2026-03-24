import numpy as np
import pandas as pd

import invest_evolution.investment.runtimes.styles as momentum_module
from invest_evolution.investment.contracts import StockSummaryView
from invest_evolution.investment.runtimes import MomentumRuntime


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


def test_momentum_runtime_process_outputs_dual_channel():
    runtime = MomentumRuntime(
        runtime_overrides={
            "top_n": 4,
            "max_positions": 3,
            "signal_threshold": 0.0,
        }
    )
    out = runtime.process(_make_stock_data(), "20230601")

    assert out.manager_id == "momentum"
    assert out.signal_packet.manager_id == "momentum"
    assert out.signal_packet.max_positions == 3
    assert len(out.signal_packet.signals) >= 1
    assert out.agent_context.regime in {"bull", "bear", "oscillation"}
    assert out.agent_context.stock_summaries
    assert out.agent_context.candidate_codes
    assert out.agent_context.confidence >= 0.5
    assert isinstance(out.agent_context.stock_summaries[0], StockSummaryView)
    assert isinstance(out.signal_packet.context.raw_summaries[0], StockSummaryView)


def test_momentum_runtime_uses_stock_batch_summary_main_path(monkeypatch):
    runtime = MomentumRuntime(
        runtime_overrides={
            "top_n": 2,
            "max_positions": 2,
            "signal_threshold": 0.0,
        }
    )
    stock_data = _make_stock_data(n=3, days=80)
    calls = {"count": 0}

    original = momentum_module.summarize_stock_batches

    def _wrapped(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(momentum_module, "summarize_stock_batches", _wrapped)

    out = runtime.process(stock_data, "20230601")

    assert calls["count"] == 1
    assert out.signal_packet.signals
