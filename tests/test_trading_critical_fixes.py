import math

import numpy as np
import pandas as pd

from invest_evolution.investment.foundation import Position, SimulatedTrader


def test_missing_price_does_not_use_other_symbol_price():
    stock_a = pd.DataFrame(
        {
            "trade_date": ["20240102"],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [1.0],
            "pct_chg": [0.0],
        }
    )
    # B has no available data at/ before 20240102.
    stock_b = pd.DataFrame(
        {
            "trade_date": ["20240110"],
            "open": [50.0],
            "high": [51.0],
            "low": [49.0],
            "close": [50.0],
            "volume": [1.0],
            "pct_chg": [0.0],
        }
    )

    trader = SimulatedTrader(enable_risk_control=False)
    trader.set_stock_data({"A": stock_a, "B": stock_b})
    trader.positions = [
        Position(
            ts_code="A",
            name="A",
            entry_date="20240101",
            entry_price=95.0,
            shares=100,
            stop_loss=80.0,
            take_profit=200.0,
        ),
        Position(
            ts_code="B",
            name="B",
            entry_date="20240101",
            entry_price=50.0,
            shares=100,
            stop_loss=49.0,
            take_profit=60.0,
        ),
    ]
    trader.hold_days = {"A": 1, "B": 1}

    trader.step("20240102")

    assert any(pos.ts_code == "B" for pos in trader.positions)
    assert not any(
        rec.action.value == "卖出" and rec.ts_code == "B" for rec in trader.trade_history
    )


def test_atr_changes_by_date_not_stuck_on_first_value():
    dates = pd.date_range("2024-01-01", periods=40, freq="B")
    close = np.linspace(10.0, 20.0, 40)
    # Low volatility in first half, high volatility in second half.
    high = close + np.concatenate([np.full(20, 0.2), np.full(20, 3.0)])
    low = close - np.concatenate([np.full(20, 0.2), np.full(20, 3.0)])
    df = pd.DataFrame(
        {
            "trade_date": dates.strftime("%Y%m%d"),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.ones(40),
        }
    )

    trader = SimulatedTrader(enable_risk_control=True)
    trader.set_stock_data({"sh.600000": df})
    atr_early = trader._get_atr("sh.600000", df.iloc[20]["trade_date"])
    atr_late = trader._get_atr("sh.600000", df.iloc[-1]["trade_date"])

    assert atr_early is not None
    assert atr_late is not None
    assert atr_late > atr_early


def test_daily_record_reflects_end_of_day_state():
    data = pd.DataFrame(
        {
            "trade_date": ["20240102"],
            "open": [120.0],
            "high": [121.0],
            "low": [119.0],
            "close": [120.0],
            "volume": [1.0],
            "pct_chg": [0.0],
        }
    )

    trader = SimulatedTrader(enable_risk_control=False)
    trader.set_stock_data({"X": data})
    trader.cash = 0.0
    trader.positions = [
        Position(
            ts_code="X",
            name="X",
            entry_date="20240101",
            entry_price=100.0,
            shares=100,
            stop_loss=90.0,
            take_profit=110.0,
        )
    ]
    trader.hold_days = {"X": 1}

    trader.step("20240102")

    record = trader.daily_records[-1]
    assert record["positions"] == len(trader.positions)
    assert math.isclose(record["cash"], trader.cash, rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(record["total_value"], trader.get_total_value(), rel_tol=1e-9, abs_tol=1e-9)
    assert record["trades_today"] >= 1



def test_trade_history_keeps_entry_and_exit_reasons():
    data = pd.DataFrame(
        {
            "trade_date": ["20240102", "20240103"],
            "open": [10.0, 9.0],
            "high": [10.2, 9.2],
            "low": [9.8, 8.8],
            "close": [10.0, 9.0],
            "volume": [1.0, 1.0],
            "pct_chg": [0.0, -10.0],
        }
    )

    trader = SimulatedTrader(enable_risk_control=False, slippage_rate=0.0)
    trader.set_stock_data({"X": data})
    assert trader.buy("X", "20240102", 10.0, reason="趋势突破", stop_loss_pct=0.05, take_profit_pct=0.10, source="trend_hunter")
    trader.hold_days["X"] = 1
    trader.sell(trader.positions[0], "20240103", 9.0, "止损")

    buy_record = trader.trade_history[0]
    sell_record = trader.trade_history[1]
    assert buy_record.entry_reason == "趋势突破"
    assert buy_record.source == "trend_hunter"
    assert sell_record.entry_reason == "趋势突破"
    assert sell_record.exit_reason == "止损"
    assert sell_record.exit_trigger == "stop_loss"
    assert sell_record.holding_days == 1


def test_get_price_returns_none_for_non_finite_fallback_price():
    data = pd.DataFrame(
        {
            "trade_date": ["20240101", "20240103"],
            "open": [10.0, 11.0],
            "high": [10.2, 11.2],
            "low": [9.8, 10.8],
            "close": [float("nan"), 11.0],
            "volume": [1.0, 1.0],
            "pct_chg": [0.0, 10.0],
        }
    )

    trader = SimulatedTrader(enable_risk_control=False)
    trader.set_stock_data({"X": data})

    assert trader.get_price("X", "20240101") is None
    assert trader.get_price("X", "20240102") is None


def test_buy_rejects_non_finite_price_without_raising():
    trader = SimulatedTrader(enable_risk_control=False)

    assert trader.buy("X", "20240102", float("nan")) is False
    assert trader.buy("X", "20240102", float("inf")) is False
