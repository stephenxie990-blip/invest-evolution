import pandas as pd

import web_server
from market_data import DataManager
from market_data.datasets import T0DatasetBuilder
from market_data.ingestion import DataIngestionService
from market_data.repository import MarketDataRepository


def test_data_manager_reads_canonical_schema(tmp_path):
    db_path = tmp_path / "canonical.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master(
        [
            {"code": "sh.600010", "name": "Foo", "list_date": "20200101", "source": "test"},
            {"code": "sh.600011", "name": "Bar", "list_date": "20200101", "source": "test"},
        ]
    )

    bars = []
    dates = pd.date_range("2024-01-02", periods=8, freq="B")
    for code, base in [("sh.600010", 10.0), ("sh.600011", 20.0)]:
        for idx, trade_date in enumerate(dates, start=1):
            close = base + idx * 0.5
            bars.append(
                {
                    "code": code,
                    "trade_date": trade_date.strftime("%Y%m%d"),
                    "open": close - 0.1,
                    "high": close + 0.1,
                    "low": close - 0.2,
                    "close": close,
                    "volume": 1000 + idx,
                    "amount": 5000 + idx,
                    "pct_chg": 1.0,
                    "turnover": 2.0,
                    "source": "test",
                }
            )
    repo.upsert_daily_bars(bars)

    manager = DataManager(db_path=str(db_path))
    stock_data = manager.load_stock_data("20240105", stock_count=2, min_history_days=3, include_future_days=2)

    assert sorted(stock_data) == ["sh.600010", "sh.600011"]
    assert all("date" in frame.columns for frame in stock_data.values())
    assert max(stock_data["sh.600010"]["trade_date"]) >= "20240109"


def test_t0_dataset_builder_uses_canonical_security_master(tmp_path):
    db_path = tmp_path / "t0.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master(
        [
            {"code": "sh.600001", "name": "Alpha", "list_date": "20200101", "delist_date": "", "source": "test"},
            {"code": "sh.600002", "name": "Beta", "list_date": "20200101", "delist_date": "20231231", "source": "test"},
            {"code": "sh.600003", "name": "Gamma", "list_date": "20240110", "delist_date": "", "source": "test"},
        ]
    )
    bars = []
    dates = pd.date_range("2023-08-01", periods=120, freq="B")
    for trade_date in dates:
        bars.append(
            {
                "code": "sh.600001",
                "trade_date": trade_date.strftime("%Y%m%d"),
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
        )
        bars.append(
            {
                "code": "sh.600002",
                "trade_date": trade_date.strftime("%Y%m%d"),
                "open": 20,
                "high": 21,
                "low": 19,
                "close": 20.5,
                "volume": 1000,
                "amount": 5000,
                "pct_chg": 0.5,
                "turnover": 1.2,
                "source": "test",
            }
        )
    repo.upsert_daily_bars(bars)

    builder = T0DatasetBuilder(db_path=str(db_path))
    data = builder.load_data_at_t0("20240105", max_stocks=10)

    assert sorted(data["stocks"]) == ["sh.600001"]
    assert data["survived"] == {"sh.600001": True}


def test_web_data_status_reads_canonical_repository(tmp_path, monkeypatch):
    db_path = tmp_path / "web.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([{"code": "sh.600100", "name": "Web", "list_date": "20200101", "source": "test"}])
    repo.upsert_daily_bars(
        [
            {
                "code": "sh.600100",
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
        ]
    )
    monkeypatch.setenv("INVEST_DB_PATH", str(db_path))

    client = web_server.app.test_client()
    res = client.get("/api/data/status")

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["schema"] == "canonical_v1"
    assert payload["latest_date"] == "20240108"
    assert payload["stock_count"] == 1


def test_web_data_download_uses_unified_ingestion_service(monkeypatch):
    calls = []

    class InlineThread:
        def __init__(self, target, daemon):
            self._target = target

        def start(self):
            self._target()

    def _sync_security(self):
        calls.append("security")
        return {"stock_count": 1}

    def _sync_daily(self):
        calls.append("daily")
        return {"row_count": 1}

    monkeypatch.setattr(web_server.threading, "Thread", InlineThread)
    monkeypatch.setattr(DataIngestionService, "sync_security_master", _sync_security)
    monkeypatch.setattr(DataIngestionService, "sync_daily_bars", _sync_daily)

    client = web_server.app.test_client()
    res = client.post("/api/data/download")

    assert res.status_code == 200
    assert res.get_json()["status"] == "started"
    assert calls == ["security", "daily"]


def test_data_manager_diagnose_training_data_reports_empty_repository(tmp_path):
    manager = DataManager(db_path=str(tmp_path / "empty.db"))

    diagnostics = manager.diagnose_training_data("20240105", stock_count=20, min_history_days=60)

    assert diagnostics["ready"] is False
    assert diagnostics["eligible_stock_count"] == 0
    assert any("daily_bar" in issue for issue in diagnostics["issues"])
    assert any("python3 -m market_data" in item for item in diagnostics["suggestions"])


def test_data_manager_diagnose_training_data_reports_eligible_counts(tmp_path):
    db_path = tmp_path / "diagnostics.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([
        {"code": "sh.600010", "name": "Foo", "list_date": "20200101", "source": "test"},
        {"code": "sh.600011", "name": "Bar", "list_date": "20200101", "source": "test"},
    ])
    bars = []
    dates = pd.date_range("2023-10-09", periods=80, freq="B")
    for code, base in [("sh.600010", 10.0), ("sh.600011", 20.0)]:
        for idx, trade_date in enumerate(dates, start=1):
            close = base + idx * 0.1
            bars.append(
                {
                    "code": code,
                    "trade_date": trade_date.strftime("%Y%m%d"),
                    "open": close - 0.1,
                    "high": close + 0.1,
                    "low": close - 0.2,
                    "close": close,
                    "volume": 1000 + idx,
                    "amount": 5000 + idx,
                    "pct_chg": 0.5,
                    "turnover": 2.0,
                    "source": "test",
                }
            )
    repo.upsert_daily_bars(bars)

    manager = DataManager(db_path=str(db_path))
    diagnostics = manager.diagnose_training_data("20240105", stock_count=5, min_history_days=60)

    assert diagnostics["ready"] is True
    assert diagnostics["eligible_stock_count"] == 2
    assert any("低于目标 5 只" in issue for issue in diagnostics["issues"])
