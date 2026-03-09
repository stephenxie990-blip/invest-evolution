import web_server
from market_data.repository import MarketDataRepository


def test_web_data_api_exposes_capital_flow_dragon_tiger_and_intraday(tmp_path, monkeypatch):
    db_path = tmp_path / "web_data_api.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([
        {"code": "sh.600001", "name": "Foo", "list_date": "20200101", "source": "test"}
    ])
    repo.upsert_daily_bars([
        {
            "code": "sh.600001",
            "trade_date": "20240108",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10.5,
            "volume": 1000,
            "amount": 5000,
            "pct_chg": 0.5,
            "turnover": 1.2,
            "source": "test",
        }
    ])
    repo.upsert_capital_flow_daily([
        {
            "code": "sh.600001",
            "trade_date": "20240108",
            "close": 10.5,
            "pct_chg": 0.5,
            "main_net_inflow": 123.0,
            "main_net_inflow_ratio": 1.5,
            "source": "test",
        }
    ])
    repo.upsert_dragon_tiger_list([
        {
            "code": "sh.600001",
            "trade_date": "20240108",
            "name": "Foo",
            "reason": "涨幅偏离",
            "net_buy": 88.0,
            "source": "test",
        }
    ])
    repo.upsert_intraday_bars_60m([
        {
            "code": "sh.600001",
            "trade_date": "20240108",
            "bar_time": "20240108103000000",
            "open": 10.0,
            "high": 10.2,
            "low": 9.9,
            "close": 10.1,
            "volume": 1000,
            "amount": 2000,
            "source": "test",
        }
    ])
    monkeypatch.setenv("INVEST_DB_PATH", str(db_path))

    client = web_server.app.test_client()

    capital_flow_res = client.get("/api/data/capital_flow?codes=sh.600001&start=20240108&end=20240108")
    assert capital_flow_res.status_code == 200
    capital_flow_payload = capital_flow_res.get_json()
    assert capital_flow_payload["count"] == 1
    assert capital_flow_payload["items"][0]["main_net_inflow"] == 123.0

    dragon_tiger_res = client.get("/api/data/dragon_tiger?codes=sh.600001&start=20240108&end=20240108")
    assert dragon_tiger_res.status_code == 200
    dragon_tiger_payload = dragon_tiger_res.get_json()
    assert dragon_tiger_payload["count"] == 1
    assert dragon_tiger_payload["items"][0]["net_buy"] == 88.0

    intraday_res = client.get("/api/data/intraday_60m?codes=sh.600001&start=20240108&end=20240108")
    assert intraday_res.status_code == 200
    intraday_payload = intraday_res.get_json()
    assert intraday_payload["count"] == 1
    assert intraday_payload["items"][0]["bar_time"] == "20240108103000000"
