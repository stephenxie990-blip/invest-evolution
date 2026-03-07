import sqlite3
from pathlib import Path

import pandas as pd

import web_server
from data import DataManager, T0DataLoader
from data_ingestion import DataIngestionService
from data_repository import MarketDataRepository


def _make_legacy_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE stock_info (
            code TEXT PRIMARY KEY,
            name TEXT,
            list_date TEXT,
            delist_date TEXT,
            industry TEXT,
            is_st INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE stock_daily (
            code TEXT,
            trade_date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            pct_chg REAL,
            turnover REAL
        )
        """
    )
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.executemany(
        "INSERT INTO stock_info VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("sh.600001", "Alpha", "2020-01-01", "", "银行", 0),
            ("sh.600002", "Beta", "2020-01-01", "2023-12-31", "消费", 0),
            ("sh.600003", "Gamma", "2024-01-10", "", "科技", 0),
        ],
    )
    rows = []
    for trade_date, close_a, close_b in [
        ("20240102", 10.0, 20.0),
        ("20240103", 10.2, 20.5),
        ("20240104", 10.5, 20.2),
    ]:
        rows.append(("sh.600001", trade_date, close_a - 0.1, close_a + 0.1, close_a - 0.2, close_a, 1000.0, 5000.0, 1.0, 2.0))
        rows.append(("sh.600002", trade_date, close_b - 0.1, close_b + 0.1, close_b - 0.2, close_b, 1500.0, 8000.0, 1.0, 3.0))
    for trade_date, close_c in [("20240110", 30.0), ("20240111", 30.5), ("20240112", 30.8)]:
        rows.append(("sh.600003", trade_date, close_c - 0.1, close_c + 0.1, close_c - 0.2, close_c, 2000.0, 9000.0, 1.0, 4.0))
    conn.executemany("INSERT INTO stock_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.executemany(
        "INSERT INTO metadata VALUES (?, ?)",
        [("latest_date", "20240112"), ("source", "legacy")],
    )
    conn.commit()
    conn.close()


def test_repository_migrates_legacy_tables_into_canonical(tmp_path):
    db_path = tmp_path / "stock_history.db"
    _make_legacy_db(db_path)

    repo = MarketDataRepository(db_path)
    result = repo.migrate_legacy_tables()

    assert set(result["migrated_tables"]) == {"stock_info", "stock_daily", "metadata"}
    status = repo.get_status_summary()
    assert status["schema"] == "canonical_v1"
    assert status["stock_count"] == 3
    assert status["kline_count"] == 9
    assert status["latest_date"] == "20240112"

    dropped = repo.cleanup_legacy_tables()
    assert set(dropped) == {"stock_info", "stock_daily", "metadata"}
    assert repo.list_legacy_tables() == []


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


def test_t0_loader_uses_canonical_security_master(tmp_path):
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

    loader = T0DataLoader(db_path=str(db_path))
    data = loader.load_data_at_t0("20240105", max_stocks=10)

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
