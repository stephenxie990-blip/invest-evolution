from market_data.repository import MarketDataRepository


def test_has_daily_bars_cache_is_reused_and_invalidated(tmp_path):
    repo = MarketDataRepository(tmp_path / "market_data_cache.db")

    assert repo._has_daily_bars_cache is None
    assert repo.has_daily_bars() is False
    assert repo._has_daily_bars_cache is False

    repo.upsert_daily_bars(
        [
            {
                "code": "sz.000001",
                "trade_date": "20240102",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 1000,
                "amount": 10200,
                "pct_chg": 2.0,
            }
        ]
    )

    assert repo._has_daily_bars_cache is None
    assert repo.has_daily_bars() is True
    assert repo._has_daily_bars_cache is True
