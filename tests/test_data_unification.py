from datetime import datetime

import pytest
import pandas as pd

import web_server
from config import IndustryRegistry
from market_data import DataManager, DataSourceUnavailableError
from market_data.datasets import T0DatasetBuilder
from market_data.ingestion import DataIngestionService
from market_data.quality import DataQualityService
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

    def _sync_index(self):
        calls.append("index")
        return {"row_count": 1}

    monkeypatch.setattr(web_server.threading, "Thread", InlineThread)
    monkeypatch.setattr(web_server, "_data_download_running", False)
    monkeypatch.setattr(DataIngestionService, "sync_security_master", _sync_security)
    monkeypatch.setattr(DataIngestionService, "sync_daily_bars", _sync_daily)
    monkeypatch.setattr(DataIngestionService, "sync_index_bars", _sync_index)

    client = web_server.app.test_client()
    res = client.post("/api/data/download")

    assert res.status_code == 200
    assert res.get_json()["status"] == "started"
    assert calls == ["security", "daily", "index"]


def test_web_data_download_deduplicates_running_job(monkeypatch):
    started_threads = []

    class DeferredThread:
        def __init__(self, target, daemon):
            self._target = target
            started_threads.append(self)

        def start(self):
            return None

    monkeypatch.setattr(web_server.threading, "Thread", DeferredThread)
    monkeypatch.setattr(web_server, "_data_download_running", False)

    client = web_server.app.test_client()
    first = client.post("/api/data/download")
    second = client.post("/api/data/download")

    assert first.status_code == 200
    assert first.get_json()["status"] == "started"
    assert second.status_code == 200
    assert second.get_json()["status"] == "running"
    assert len(started_threads) == 1


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


def test_tushare_financial_snapshots_sync_into_canonical_schema(tmp_path, monkeypatch):
    import sys
    import types

    db_path = tmp_path / "financial.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()

    class FakePro:
        def stock_basic(self, **kwargs):
            return pd.DataFrame([
                {"ts_code": "600010.SH", "name": "Foo", "list_date": "20200101", "delist_date": ""},
                {"ts_code": "600011.SH", "name": "Bar", "list_date": "20200101", "delist_date": ""},
            ])

        def daily_basic(self, **kwargs):
            if kwargs.get("trade_date"):
                return pd.DataFrame([
                    {"ts_code": "600010.SH", "total_mv": 1234.5},
                    {"ts_code": "600011.SH", "total_mv": 2345.6},
                ])
            return pd.DataFrame()

        def income(self, ts_code, **kwargs):
            if ts_code == "600010.SH":
                return pd.DataFrame([
                    {"ts_code": ts_code, "ann_date": "20240331", "end_date": "20231231", "total_revenue": 100.0, "n_income": 10.0},
                ])
            return pd.DataFrame([
                {"ts_code": ts_code, "ann_date": "20240331", "end_date": "20231231", "total_revenue": 200.0, "n_income": 20.0},
            ])

        def balancesheet(self, ts_code, **kwargs):
            return pd.DataFrame([
                {"ts_code": ts_code, "ann_date": "20240331", "end_date": "20231231", "total_assets": 999.0},
            ])

        def fina_indicator(self, ts_code, **kwargs):
            return pd.DataFrame([
                {"ts_code": ts_code, "ann_date": "20240331", "end_date": "20231231", "roe": 12.5},
            ])

    fake_tushare = types.SimpleNamespace(set_token=lambda token: None, pro_api=lambda: FakePro())
    monkeypatch.setitem(sys.modules, "tushare", fake_tushare)

    service = DataIngestionService(repository=repo, tushare_token="demo-token")
    result = service.sync_financial_snapshots_from_tushare(stock_limit=2)

    assert result["stock_count"] == 2
    assert result["row_count"] == 2
    status = repo.get_status_summary()
    assert status["financial_count"] == 2
    with repo.connect() as conn:
        payload = conn.execute(
            "select code, report_date, publish_date, roe, net_profit, revenue, total_assets, market_cap from financial_snapshot order by code"
        ).fetchall()
    assert payload[0][0] == "sh.600010"
    assert payload[0][1] == "20231231"
    assert payload[0][2] == "20240331"
    assert payload[0][3] == 12.5
    assert payload[0][4] == 10.0
    assert payload[0][5] == 100.0
    assert payload[0][6] == 999.0
    assert payload[0][7] == 1234.5


def test_akshare_financial_snapshots_sync_into_canonical_schema(tmp_path, monkeypatch):
    import sys
    import types

    db_path = tmp_path / "financial_ak.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([
        {"code": "sh.600010", "name": "Foo", "list_date": "20200101", "source": "test"},
        {"code": "sh.600011", "name": "Bar", "list_date": "20200101", "source": "test"},
    ])
    repo.upsert_daily_bars([
        {"code": "sh.600010", "trade_date": "20240105", "open": 9.8, "high": 10.2, "low": 9.7, "close": 10.0, "volume": 1, "amount": 1, "pct_chg": 0.0, "turnover": 1.0, "source": "test"},
        {"code": "sh.600011", "trade_date": "20240105", "open": 19.8, "high": 20.2, "low": 19.7, "close": 20.0, "volume": 1, "amount": 1, "pct_chg": 0.0, "turnover": 1.0, "source": "test"},
    ])

    def fake_abstract(symbol: str):
        return pd.DataFrame([
            {"选项": "常用指标", "指标": "归母净利润", "20240630": 12.0, "20231231": 10.0},
            {"选项": "常用指标", "指标": "营业总收入", "20240630": 120.0, "20231231": 100.0},
            {"选项": "常用指标", "指标": "净资产收益率(ROE)", "20240630": 13.5, "20231231": 12.5},
        ])

    def fake_balance(symbol: str):
        return pd.DataFrame([
            {"REPORT_DATE": "2024-06-30", "NOTICE_DATE": "2024-08-30", "TOTAL_ASSETS": 999.0, "SHARE_CAPITAL": 100.0},
            {"REPORT_DATE": "2023-12-31", "NOTICE_DATE": "2024-03-31", "TOTAL_ASSETS": 888.0, "SHARE_CAPITAL": 100.0},
        ])

    fake_akshare = types.SimpleNamespace(
        stock_financial_abstract=fake_abstract,
        stock_balance_sheet_by_report_em=fake_balance,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    service = DataIngestionService(repository=repo)
    result = service.sync_financial_snapshots_from_akshare(stock_limit=2)

    assert result["stock_count"] == 2
    assert result["row_count"] == 4
    status = repo.get_status_summary()
    assert status["financial_count"] == 4
    with repo.connect() as conn:
        payload = conn.execute(
            "select code, report_date, publish_date, roe, net_profit, revenue, total_assets, market_cap from financial_snapshot order by code, report_date"
        ).fetchall()
    assert payload[0][0] == "sh.600010"
    assert payload[0][1] == "20231231"
    assert payload[0][2] == "20240331"
    assert payload[0][3] == 12.5
    assert payload[0][4] == 10.0
    assert payload[0][5] == 100.0
    assert payload[0][6] == 888.0
    assert payload[0][7] == 1000.0


def test_market_data_cli_financials_requires_tushare_source(monkeypatch):
    import market_data.manager as manager

    monkeypatch.setattr(manager, "DataIngestionService", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager.argparse.ArgumentParser, "parse_args", lambda self: type("Args", (), {
        "stocks": 10,
        "start": "20180101",
        "end": None,
        "token": None,
        "offset": 0,
        "test": False,
        "source": "baostock",
        "financials": True,
        "calendar": False,
        "capital_flow": False,
        "dragon_tiger": False,
        "status": False,
        "cutoff": None,
        "min_history_days": None,
    })())

    try:
        manager._cli_main()
    except RuntimeError as exc:
        assert "仅支持 --source tushare 或 --source akshare" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_akshare_bulk_financial_snapshots_sync_into_canonical_schema(tmp_path, monkeypatch):
    import sys
    import types

    db_path = tmp_path / "financial_ak_bulk.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([
        {"code": "sh.600010", "name": "Foo", "list_date": "20200101", "source": "test"},
        {"code": "sz.000001", "name": "Bar", "list_date": "20200101", "source": "test"},
    ])

    def fake_yjbb(date: str):
        if date != "20240331":
            return pd.DataFrame([])
        return pd.DataFrame([
            {"股票代码": "600010", "最新公告日期": "2024-04-30", "净资产收益率": 12.5, "净利润-净利润": 10.0, "营业总收入-营业总收入": 100.0, "所处行业": "银行"},
            {"股票代码": "000001", "最新公告日期": "2024-04-29", "净资产收益率": 9.9, "净利润-净利润": 20.0, "营业总收入-营业总收入": 200.0, "所处行业": "银行"},
        ])

    fake_akshare = types.SimpleNamespace(stock_yjbb_em=fake_yjbb)
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    service = DataIngestionService(repository=repo)
    result = service.sync_financial_snapshots_from_akshare_bulk(start_date="20240331", end_date="20240331")

    assert result["stock_count"] == 2
    assert result["row_count"] == 2
    rows = repo.query_latest_financial_snapshots(["sh.600010", "sz.000001"], "20240501")
    assert len(rows) == 2
    assert rows[0]["report_date"] == "20240331"
    assert rows[0]["publish_date"] in {"20240430", "20240429"}


def test_market_data_cli_financials_supports_akshare_source(monkeypatch):
    import market_data.manager as manager

    calls = {}

    class FakeService:
        def __init__(self, *args, **kwargs):
            calls["init"] = True

        def sync_financial_snapshots_from_akshare_bulk(self, stock_limit, offset, start_date, end_date):
            calls["financial"] = {"stock_limit": stock_limit, "offset": offset, "start_date": start_date, "end_date": end_date}
            return {"row_count": 1, "source": "akshare"}

    monkeypatch.setattr(manager, "DataIngestionService", FakeService)
    monkeypatch.setattr(manager.argparse.ArgumentParser, "parse_args", lambda self: type("Args", (), {
        "stocks": 10,
        "start": "20180101",
        "end": None,
        "token": None,
        "offset": 12,
        "test": True,
        "source": "akshare",
        "financials": True,
        "calendar": False,
        "capital_flow": False,
        "dragon_tiger": False,
        "status": False,
        "cutoff": None,
        "min_history_days": None,
    })())

    manager._cli_main()
    assert calls["financial"] == {"stock_limit": 10, "offset": 12, "start_date": "20180101", "end_date": None}


def test_repository_tracks_capital_flow_and_dragon_tiger_in_status(tmp_path):
    repo = MarketDataRepository(tmp_path / "fund_lhb.db")
    repo.initialize_schema()
    repo.upsert_capital_flow_daily([
        {
            "code": "sh.600000",
            "trade_date": "20250303",
            "close": 10.0,
            "pct_chg": 1.0,
            "main_net_inflow": 100.0,
            "main_net_inflow_ratio": 1.5,
            "source": "test",
        }
    ])
    repo.upsert_dragon_tiger_list([
        {
            "code": "sh.600000",
            "trade_date": "20250303",
            "name": "Foo",
            "reason": "涨幅偏离",
            "net_buy": 50.0,
            "source": "test",
        }
    ])
    status = repo.get_status_summary()
    assert status["capital_flow_count"] == 1
    assert status["dragon_tiger_count"] == 1


def test_market_data_cli_supports_akshare_capital_flow_and_dragon_tiger(monkeypatch):
    import market_data.manager as manager

    calls = {}

    class FakeService:
        def __init__(self, *args, **kwargs):
            pass

        def sync_capital_flow_daily_from_akshare(self, stock_limit, offset):
            calls["capital_flow"] = {"stock_limit": stock_limit, "offset": offset}
            return {"row_count": 1}

        def sync_dragon_tiger_list_from_akshare(self, start_date, end_date):
            calls["dragon_tiger"] = {"start_date": start_date, "end_date": end_date}
            return {"row_count": 2}

    monkeypatch.setattr(manager, "DataIngestionService", FakeService)
    monkeypatch.setattr(manager.argparse.ArgumentParser, "parse_args", lambda self: type("Args", (), {
        "stocks": 10,
        "start": "20260301",
        "end": "20260306",
        "token": None,
        "offset": 5,
        "test": False,
        "source": "akshare",
        "financials": False,
        "calendar": False,
        "capital_flow": True,
        "dragon_tiger": True,
        "status": False,
        "cutoff": None,
        "min_history_days": None,
    })())
    manager._cli_main()
    assert calls["capital_flow"] == {"stock_limit": 10, "offset": 5}
    assert calls["dragon_tiger"] == {"start_date": "20260301", "end_date": "20260306"}



def test_repository_tracks_intraday_60m_in_status(tmp_path):
    repo = MarketDataRepository(tmp_path / "intraday_60m.db")
    repo.initialize_schema()
    repo.upsert_intraday_bars_60m([
        {
            "code": "sh.600000",
            "trade_date": "20250303",
            "bar_time": "20250303103000000",
            "open": 10.0,
            "high": 10.2,
            "low": 9.9,
            "close": 10.1,
            "volume": 1000,
            "amount": 2000,
            "source": "test",
        }
    ])
    status = repo.get_status_summary()
    frame = repo.query_intraday_bars_60m(codes=["sh.600000"], start_date="20250303", end_date="20250303")
    assert status["intraday_60m_count"] == 1
    assert len(frame) == 1
    assert frame.iloc[0]["bar_time"] == "20250303103000000"


def test_market_data_cli_supports_baostock_intraday_60m(monkeypatch):
    import market_data.manager as manager

    calls = {}

    class FakeService:
        def __init__(self, *args, **kwargs):
            pass

        def sync_intraday_bars_60m(self, start_date, end_date, stock_limit, offset):
            calls["intraday_60m"] = {
                "start_date": start_date,
                "end_date": end_date,
                "stock_limit": stock_limit,
                "offset": offset,
            }
            return {"row_count": 3}

    monkeypatch.setattr(manager, "DataIngestionService", FakeService)
    monkeypatch.setattr(manager.argparse.ArgumentParser, "parse_args", lambda self: type("Args", (), {
        "stocks": 12,
        "start": "20230310",
        "end": "20260309",
        "token": None,
        "offset": 7,
        "test": False,
        "source": "baostock",
        "financials": False,
        "calendar": False,
        "capital_flow": False,
        "dragon_tiger": False,
        "intraday_60m": True,
        "status": False,
        "cutoff": None,
        "min_history_days": None,
    })())
    manager._cli_main()
    assert calls["intraday_60m"] == {
        "start_date": "20230310",
        "end_date": "20260309",
        "stock_limit": 12,
        "offset": 7,
    }


def test_repository_tracks_index_bars_in_status(tmp_path):
    db_path = tmp_path / "index.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    inserted = repo.upsert_index_bars([
        {
            "index_code": "sh.000300",
            "trade_date": "20240108",
            "open": 3500,
            "high": 3510,
            "low": 3490,
            "close": 3505,
            "volume": 1000000,
            "amount": 2000000,
            "pct_chg": 0.5,
            "source": "test",
        }
    ])

    status = repo.get_status_summary()
    quality = DataQualityService(repository=repo).audit()

    assert inserted == 1
    assert status["index_count"] == 1
    assert status["index_kline_count"] == 1
    assert status["index_latest_date"] == "20240108"
    assert quality["checks"]["has_index_bars"] is True
    assert quality["index_date_range"]["min"] == "20240108"


def test_repository_exposes_latest_financial_snapshot_by_cutoff(tmp_path):
    repo = MarketDataRepository(tmp_path / "financial_read.db")
    repo.initialize_schema()
    repo.upsert_financial_snapshots([
        {
            "code": "sh.600010",
            "report_date": "20231231",
            "publish_date": "20240331",
            "roe": 11.0,
            "market_cap": 100.0,
            "source": "test",
        },
        {
            "code": "sh.600010",
            "report_date": "20240930",
            "publish_date": "20241031",
            "roe": 15.0,
            "market_cap": 120.0,
            "source": "test",
        },
    ])

    rows = repo.query_latest_financial_snapshots(["sh.600010"], "20240601")

    assert len(rows) == 1
    assert rows[0]["report_date"] == "20231231"
    assert rows[0]["roe"] == 11.0


def test_training_dataset_builder_attaches_financial_factor_and_status(tmp_path):
    db_path = tmp_path / "context.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([
        {"code": "sh.600010", "name": "Foo", "list_date": "20200101", "industry": "银行", "source": "test"},
    ])
    repo.upsert_daily_bars([
        {
            "code": "sh.600010",
            "trade_date": day,
            "open": 10.0 + idx,
            "high": 10.2 + idx,
            "low": 9.8 + idx,
            "close": 10.1 + idx,
            "volume": 1000 + idx,
            "amount": 5000 + idx,
            "pct_chg": 1.0,
            "turnover": 2.0,
            "source": "test",
        }
        for idx, day in enumerate(["20240102", "20240103", "20240104", "20240105"], start=1)
    ])
    repo.upsert_financial_snapshots([
        {
            "code": "sh.600010",
            "report_date": "20231231",
            "publish_date": "20240331",
            "roe": 12.5,
            "net_profit": 10.0,
            "revenue": 100.0,
            "total_assets": 999.0,
            "market_cap": 1234.5,
            "source": "test",
        }
    ])
    repo.upsert_security_status_daily([
        {
            "code": "sh.600010",
            "trade_date": "20240105",
            "is_st": 0,
            "is_new_stock_window": 0,
            "is_limit_up": 0,
            "is_limit_down": 0,
            "source": "test",
        }
    ])
    repo.upsert_factor_snapshots([
        {
            "code": "sh.600010",
            "trade_date": "20240105",
            "ma5": 10.0,
            "ma10": 9.8,
            "ma20": 9.5,
            "ma60": 9.0,
            "momentum20": 5.0,
            "momentum60": 8.0,
            "volatility20": 2.0,
            "volume_ratio": 1.5,
            "turnover_mean20": 2.1,
            "drawdown60": -1.0,
            "relative_strength_hs300": 3.0,
            "breakout20": 1,
            "source": "test",
        }
    ])

    builder = DataManager(db_path=str(db_path))._offline
    stocks = builder.get_stocks("20240405", stock_count=1, min_history_days=3, include_future_days=0)
    frame = stocks["sh.600010"]

    assert "industry" in frame.columns
    assert "roe" in frame.columns
    assert "market_cap" in frame.columns
    assert "ma5" in frame.columns
    assert "is_limit_up" in frame.columns
    assert frame["industry"].iloc[-1] == "银行"
    assert frame["market_cap"].iloc[-1] == 1234.5
    assert frame["ma5"].iloc[-1] == 10.0


def test_data_manager_builds_benchmark_series_from_index_bar(tmp_path):
    db_path = tmp_path / "bench.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_index_bars([
        {"index_code": "sh.000300", "trade_date": "20240102", "close": 100.0, "source": "test"},
        {"index_code": "sh.000300", "trade_date": "20240103", "close": 101.0, "source": "test"},
        {"index_code": "sh.000300", "trade_date": "20240105", "close": 102.0, "source": "test"},
    ])

    manager = DataManager(db_path=str(db_path))
    values = manager.get_benchmark_daily_values(["20240102", "20240103", "20240104", "20240105"])

    assert values == [100.0, 101.0, 101.0, 102.0]


def test_industry_registry_prefers_database_and_json_as_override(tmp_path):
    db_path = tmp_path / "industry.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([
        {"code": "sh.600010", "name": "Foo", "list_date": "20200101", "industry": "银行", "source": "test"},
        {"code": "sh.600011", "name": "Bar", "list_date": "20200101", "industry": "券商", "source": "test"},
    ])
    override = tmp_path / "industry_map.json"
    override.write_text('{"sh.600011": "非银金融"}', encoding='utf-8')

    registry = IndustryRegistry(json_path=override, db_path=db_path)

    assert registry.get_industry("sh.600010") == "银行"
    assert registry.get_industry("sh.600011") == "非银金融"


def test_repository_initializes_adj_code_date_index(tmp_path):
    db_path = tmp_path / "index_check.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()

    with repo.connect() as conn:
        rows = conn.execute("PRAGMA index_list('daily_bar')").fetchall()

    index_names = {row[1] for row in rows}
    assert "idx_daily_bar_adj_code_date" in index_names


def test_repository_query_training_bars_limits_pre_cutoff_history_per_code(tmp_path):
    db_path = tmp_path / "training_slice.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([
        {"code": "sh.600001", "name": "Foo", "list_date": "20200101", "source": "test"},
        {"code": "sh.600002", "name": "Bar", "list_date": "20200101", "source": "test"},
    ])
    bars = []
    dates = pd.date_range("2024-01-02", periods=10, freq="B")
    for code, base in [("sh.600001", 10.0), ("sh.600002", 20.0)]:
        for idx, trade_date in enumerate(dates, start=1):
            bars.append(
                {
                    "code": code,
                    "trade_date": trade_date.strftime("%Y%m%d"),
                    "open": base + idx - 0.2,
                    "high": base + idx + 0.2,
                    "low": base + idx - 0.4,
                    "close": base + idx,
                    "volume": 1000 + idx,
                    "amount": 5000 + idx,
                    "pct_chg": 0.5,
                    "turnover": 1.2,
                    "source": "test",
                }
            )
    repo.upsert_daily_bars(bars)

    frame = repo.query_training_bars(
        codes=["sh.600001", "sh.600002"],
        cutoff_date="20240109",
        history_limit=3,
        end_date="20240111",
    )

    counts = frame.groupby("code").size().to_dict()
    assert counts == {"sh.600001": 5, "sh.600002": 5}
    assert frame["trade_date"].min() == "20240105"
    assert frame["trade_date"].max() == "20240111"


def test_data_manager_caches_quality_audit(tmp_path, monkeypatch):
    db_path = tmp_path / "quality_cache.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([{"code": "sh.600001", "name": "Foo", "list_date": "20200101", "source": "test"}])
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

    manager = DataManager(db_path=str(db_path))
    calls = {"count": 0}

    def _fake_audit():
        calls["count"] += 1
        return {
            "status": {"stock_count": 1, "kline_count": 1, "financial_count": 0, "index_kline_count": 0, "calendar_count": 0, "status_count": 0, "factor_count": 0, "latest_date": "20240108"},
            "date_range": {"min": "20240108", "max": "20240108"},
            "checks": {},
            "issues": [],
            "healthy": True,
        }

    monkeypatch.setattr(manager._quality_service, "audit", _fake_audit)

    first = manager.diagnose_training_data("20240108", stock_count=1, min_history_days=1)
    second = manager.diagnose_training_data("20240108", stock_count=1, min_history_days=1)

    assert first["ready"] is True
    assert second["ready"] is True
    assert calls["count"] == 1


def test_check_training_readiness_skips_full_quality_audit(tmp_path, monkeypatch):
    db_path = tmp_path / "readiness.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([{"code": "sh.600001", "name": "Foo", "list_date": "20200101", "source": "test"}])
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

    manager = DataManager(db_path=str(db_path))

    def _boom():
        raise AssertionError("full quality audit should not run on training hot path")

    monkeypatch.setattr(manager._quality_service, "audit", _boom)
    diagnostics = manager.check_training_readiness("20240108", stock_count=1, min_history_days=1)

    assert diagnostics["ready"] is True
    assert diagnostics["diagnostic_mode"] == "training_lightweight"


def test_load_stock_data_uses_lightweight_training_readiness(tmp_path, monkeypatch):
    db_path = tmp_path / "load_readiness.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([{"code": "sh.600001", "name": "Foo", "list_date": "20200101", "source": "test"}])
    bars = []
    for trade_date in pd.date_range("2023-09-01", periods=80, freq="B"):
        bars.append({
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
        })
    repo.upsert_daily_bars(bars)

    manager = DataManager(db_path=str(db_path))

    def _boom(*args, **kwargs):
        raise AssertionError("full diagnostics should not run inside load_stock_data hot path")

    monkeypatch.setattr(manager, "diagnose_training_data", _boom)
    stock_data = manager.load_stock_data("20240108", stock_count=1, min_history_days=20, include_future_days=0)

    assert list(stock_data.keys()) == ["sh.600001"]


def test_load_stock_data_skips_derivative_sync_on_default_hot_path(tmp_path, monkeypatch):
    db_path = tmp_path / "load_skip_derivatives.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([{"code": "sh.600001", "name": "Foo", "list_date": "20200101", "source": "test"}])
    bars = []
    for trade_date in pd.date_range("2023-09-01", periods=80, freq="B"):
        bars.append({
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
        })
    repo.upsert_daily_bars(bars)

    manager = DataManager(db_path=str(db_path))

    def _boom(**kwargs):
        raise AssertionError("default load path should not trigger derivative sync")

    monkeypatch.setattr(manager, "_ensure_point_in_time_derivatives", _boom)
    stock_data = manager.load_stock_data("20240108", stock_count=1, min_history_days=20, include_future_days=0)

    assert list(stock_data.keys()) == ["sh.600001"]


def test_status_summary_uses_snapshot_cache(tmp_path, monkeypatch):
    db_path = tmp_path / "status_snapshot.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([{"code": "sh.600001", "name": "Foo", "list_date": "20200101", "source": "test"}])
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

    first = repo.get_status_summary(use_snapshot=True, max_age_seconds=30)
    assert first["kline_count"] == 1

    def _boom():
        raise AssertionError("snapshot path should avoid recomputing status summary")

    monkeypatch.setattr(repo, "_compute_status_summary", _boom)
    second = repo.get_status_summary(use_snapshot=True, max_age_seconds=30)

    assert second["kline_count"] == 1
    assert second["latest_date"] == "20240108"


def test_web_data_status_refresh_query_switches_detail_mode(tmp_path, monkeypatch):
    db_path = tmp_path / "web_refresh.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([{"code": "sh.600100", "name": "Web", "list_date": "20200101", "source": "test"}])
    repo.upsert_daily_bars([
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
    ])
    monkeypatch.setenv("INVEST_DB_PATH", str(db_path))

    client = web_server.app.test_client()
    fast = client.get("/api/data/status")
    slow = client.get("/api/data/status?refresh=true")

    assert fast.status_code == 200
    assert slow.status_code == 200
    assert fast.get_json()["detail_mode"] == "fast"
    assert slow.get_json()["detail_mode"] == "slow"


def test_status_summary_invalid_snapshot_logs_warning_and_recomputes(tmp_path, caplog):
    repo = MarketDataRepository(tmp_path / "status_invalid.db")
    repo.initialize_schema()
    repo.upsert_security_master([{"code": "sh.600001", "name": "Foo", "list_date": "20200101", "source": "test"}])
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
    repo.upsert_meta(
        {
            "status_summary_snapshot": '{"broken":',
            "status_summary_updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )

    with caplog.at_level("WARNING"):
        status = repo.get_status_summary(use_snapshot=True, max_age_seconds=30)

    assert status["kline_count"] == 1
    assert "Ignoring invalid status summary snapshot" in caplog.text


def test_quality_audit_invalid_snapshot_logs_warning_and_recomputes(tmp_path, caplog):
    repo = MarketDataRepository(tmp_path / "quality_invalid.db")
    repo.initialize_schema()
    repo.upsert_security_master([{"code": "sh.600001", "name": "Foo", "list_date": "20200101", "source": "test"}])
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
    repo.upsert_meta(
        {
            "quality_audit_snapshot": '{"broken":',
            "quality_audit_updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )

    with caplog.at_level("WARNING"):
        payload = DataQualityService(repository=repo).audit(use_snapshot=True, max_age_seconds=30)

    assert payload["healthy"] is True
    assert "Ignoring invalid quality audit snapshot" in caplog.text


def test_data_manager_exposes_extended_dataset_services(tmp_path):
    db_path = tmp_path / "extended_services.db"
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

    manager = DataManager(db_path=str(db_path))
    capital_flow = manager.get_capital_flow_data(codes=["sh.600001"], start_date="20240108", end_date="20240108")
    events = manager.get_dragon_tiger_events(codes=["sh.600001"], start_date="20240108", end_date="20240108")
    intraday = manager.get_intraday_60m_data(codes=["sh.600001"], start_date="20240108", end_date="20240108")
    summary = manager.get_status_summary(refresh=False)

    assert len(capital_flow) == 1
    assert len(events) == 1
    assert len(intraday) == 1
    assert summary["intraday_60m_count"] >= 1


def test_load_stock_data_can_attach_capital_flow(tmp_path):
    db_path = tmp_path / "capital_flow_attach.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([
        {"code": "sh.600001", "name": "Foo", "list_date": "20200101", "source": "test"}
    ])
    bars = []
    for trade_date in pd.date_range("2024-01-02", periods=8, freq="B"):
        bars.append({
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
        })
    repo.upsert_daily_bars(bars)
    repo.upsert_capital_flow_daily([
        {
            "code": "sh.600001",
            "trade_date": "20240108",
            "close": 10.5,
            "pct_chg": 0.5,
            "main_net_inflow": 123.0,
            "main_net_inflow_ratio": 1.5,
            "small_net_inflow": -50.0,
            "small_net_inflow_ratio": -0.8,
            "source": "test",
        }
    ])

    manager = DataManager(db_path=str(db_path))
    stock_data = manager.load_stock_data("20240108", stock_count=1, min_history_days=3, include_capital_flow=True)
    frame = stock_data["sh.600001"]

    assert "main_net_inflow" in frame.columns
    assert float(frame.loc[frame["trade_date"] == "20240108", "main_net_inflow"].iloc[0]) == 123.0



def test_load_stock_data_raises_when_live_sources_unavailable_and_mock_not_explicit(tmp_path):
    manager = DataManager(db_path=str(tmp_path / 'empty_live.db'))

    class FakeOnlineLoader:
        def load_all_data_before(self, cutoff_date):
            raise RuntimeError('network down')

    manager._online = FakeOnlineLoader()

    with pytest.raises(DataSourceUnavailableError) as exc_info:
        manager.load_stock_data('20240108', stock_count=2, min_history_days=20, include_future_days=0)

    payload = exc_info.value.to_dict()
    assert payload['error_code'] == 'data_source_unavailable'
    assert payload['requested_data_mode'] == 'live'
    assert payload['allow_mock_fallback'] is False
    assert manager.last_source == 'unavailable'
    assert manager.last_resolution['effective_data_mode'] == 'unavailable'



def test_load_stock_data_uses_mock_only_when_allow_mock_fallback_enabled(tmp_path):
    manager = DataManager(db_path=str(tmp_path / 'empty_fallback.db'), allow_mock_fallback=True)

    class FakeOnlineLoader:
        def load_all_data_before(self, cutoff_date):
            raise RuntimeError('network down')

    manager._online = FakeOnlineLoader()
    stock_data = manager.load_stock_data('20240108', stock_count=2, min_history_days=20, include_future_days=0)

    assert stock_data
    assert manager.last_source == 'mock'
    assert manager.last_resolution['effective_data_mode'] == 'mock'
    assert manager.last_resolution['degrade_reason'] == 'explicit_mock_fallback'
