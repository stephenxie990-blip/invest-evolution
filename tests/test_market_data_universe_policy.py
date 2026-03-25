from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from market_data import MockDataProvider
from market_data.repository import MarketDataRepository
from market_data.universe_policy import (
    DEFAULT_MAX_STALENESS_DAYS,
    select_universe_codes,
)


def _frame_with_dates(code: str, dates: pd.DatetimeIndex) -> pd.DataFrame:
    values = pd.Series(range(len(dates)), dtype="float64")
    return pd.DataFrame(
        {
            "code": code,
            "trade_date": dates.strftime("%Y%m%d"),
            "open": 10.0 + values,
            "high": 10.2 + values,
            "low": 9.8 + values,
            "close": 10.1 + values,
            "volume": 1000.0 + values,
            "amount": 5000.0 + values,
            "pct_chg": 0.5,
            "turnover": 2.0,
        }
    )


def test_select_universe_codes_applies_history_and_freshness_policy():
    selected = select_universe_codes(
        candidates=[
            {"code": "sh.600010", "history_days": 300, "last_trade_date": "20240322"},
            {"code": "sh.600011", "history_days": 320, "last_trade_date": "20240220"},
            {"code": "sh.600012", "history_days": 310, "last_trade_date": "20240321"},
            {"code": "sh.600013", "history_days": 80, "last_trade_date": "20240322"},
        ],
        cutoff_date="20240325",
        stock_count=3,
        min_history_days=200,
        max_staleness_days=10,
    )

    assert selected == ["sh.600012", "sh.600010"]


def test_repository_select_codes_with_history_filters_stale_series(tmp_path):
    repo = MarketDataRepository(tmp_path / "universe.db")
    repo.initialize_schema()
    repo.upsert_daily_bars(
        [
            {
                "code": "sh.600001",
                "trade_date": day,
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "close": 10.0,
                "volume": 1000.0,
                "amount": 5000.0,
                "pct_chg": 0.1,
                "turnover": 1.0,
                "source": "test",
            }
            for day in ["20240320", "20240321", "20240322"]
        ]
        + [
            {
                "code": "sh.600002",
                "trade_date": day,
                "open": 20.0,
                "high": 20.1,
                "low": 19.9,
                "close": 20.0,
                "volume": 1000.0,
                "amount": 5000.0,
                "pct_chg": 0.1,
                "turnover": 1.0,
                "source": "test",
            }
            for day in ["20240201", "20240202", "20240205"]
        ]
    )

    selected = repo.select_codes_with_history(
        cutoff_date="20240325",
        min_history_days=3,
        stock_count=5,
        max_staleness_days=10,
    )

    assert selected == ["sh.600001"]


def test_mock_data_provider_load_stock_data_uses_universe_policy_not_dict_order():
    provider = MockDataProvider(stock_count=2, days=10, start_date="20240101")

    cutoff_dt = datetime(2024, 2, 20)
    stale_last = cutoff_dt - timedelta(days=DEFAULT_MAX_STALENESS_DAYS + 5)
    fresh_last = cutoff_dt - timedelta(days=1)

    stale_dates = pd.bdate_range(end=stale_last, periods=10)
    fresh_dates = pd.bdate_range(end=fresh_last, periods=10)

    provider.data = {
        "sh.600001": _frame_with_dates("sh.600001", stale_dates),
        "sh.600002": _frame_with_dates("sh.600002", fresh_dates),
    }

    selected = provider.load_stock_data(
        cutoff_date=cutoff_dt.strftime("%Y%m%d"),
        stock_count=1,
        min_history_days=5,
    )

    assert list(selected.keys()) == ["sh.600002"]
