import pandas as pd

import invest_evolution.investment.foundation.risk as risk_module
from invest_evolution.investment.foundation import Position, SimulatedTrader


def _price_frame(close: float, *, trade_date: str = "20240105") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [trade_date],
            "open": [close],
            "high": [close],
            "low": [close],
            "close": [close],
            "volume": [1.0],
            "pct_chg": [0.0],
        }
    )


def test_portfolio_risk_uses_peak_drawdown_even_when_above_initial_capital():
    trader = SimulatedTrader(enable_risk_control=True)
    trader.current_date = "20240105"
    trader.cash = 0.0
    trader.peak_value = 120000.0
    trader.positions = [
        Position(
            ts_code="sh.600001",
            name="A",
            entry_date="20240101",
            entry_price=100.0,
            shares=1000,
            stop_loss=90.0,
            take_profit=120.0,
        )
    ]
    trader.set_stock_data({"sh.600001": _price_frame(105.0)})

    risk = trader.check_portfolio_risk("20240105")

    assert round(float(risk["drawdown"]), 4) == round((120000.0 - 105000.0) / 120000.0, 4)
    assert risk["action"] == "CLOSE_ALL"


def test_sector_exposure_uses_mark_to_market_value(monkeypatch):
    monkeypatch.setattr(
        risk_module.industry_registry,
        "get_industry",
        lambda code: "technology",
    )
    trader = SimulatedTrader(enable_risk_control=True)
    trader.current_date = "20240105"
    trader.cash = 140000.0
    trader.positions = [
        Position(
            ts_code="sh.600001",
            name="Existing",
            entry_date="20240101",
            entry_price=10.0,
            shares=2000,
            stop_loss=8.0,
            take_profit=12.0,
        )
    ]
    trader.set_stock_data(
        {
            "sh.600001": _price_frame(30.0),
            "sh.600002": _price_frame(20.0),
        }
    )

    allowed, reason = trader.check_can_open_position("sh.600002", weight=0.05)

    assert allowed is False
    assert "行业'technology'" in reason
