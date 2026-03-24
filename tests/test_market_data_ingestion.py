from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from invest_evolution.market_data.manager import (
    AKSHARE_BOUNDARY_EXCEPTIONS,
    DataIngestionService,
    _akshare_call,
    _merge_financial_frames,
    _normalize_loose_date,
)
from invest_evolution.market_data.repository import MarketDataRepository


def test_get_latest_close_map_returns_latest_hfq_rows(tmp_path):
    repo = MarketDataRepository(tmp_path / "latest_close.db")
    repo.initialize_schema()
    repo.upsert_daily_bars(
        [
            {
                "code": "sh.600010",
                "trade_date": "20240101",
                "open": 9.8,
                "high": 10.2,
                "low": 9.7,
                "close": "10.0",
                "volume": 1,
                "amount": 1,
                "pct_chg": 0.0,
                "turnover": 1.0,
                "adj_flag": "hfq",
                "source": "test",
            },
            {
                "code": "sh.600010",
                "trade_date": "20240102",
                "open": 10.0,
                "high": 10.4,
                "low": 9.9,
                "close": "10.5",
                "volume": 1,
                "amount": 1,
                "pct_chg": 0.0,
                "turnover": 1.0,
                "adj_flag": "hfq",
                "source": "test",
            },
            {
                "code": "sh.600010",
                "trade_date": "20240103",
                "open": 10.0,
                "high": 10.4,
                "low": 9.9,
                "close": "99.9",
                "volume": 1,
                "amount": 1,
                "pct_chg": 0.0,
                "turnover": 1.0,
                "adj_flag": "qfq",
                "source": "test",
            },
            {
                "code": "sz.000001",
                "trade_date": "20240102",
                "open": 20.0,
                "high": 20.4,
                "low": 19.9,
                "close": "20.5",
                "volume": 1,
                "amount": 1,
                "pct_chg": 0.0,
                "turnover": 1.0,
                "adj_flag": "hfq",
                "source": "test",
            },
        ]
    )

    service = DataIngestionService(repository=repo)

    assert service._get_latest_close_map(["sh.600010", "sz.000001"]) == {
        "sh.600010": 10.5,
        "sz.000001": 20.5,
    }


def test_merge_financial_frames_and_loose_date_helpers_handle_sparse_frames():
    merged = _merge_financial_frames(
        pd.DataFrame(
            [
                {
                    "end_date": "20231231",
                    "ann_date": "20240331",
                    "total_revenue": 120.0,
                    "n_income": 10.0,
                }
            ]
        ),
        pd.DataFrame([{"end_date": "20231231", "total_assets": 999.0}]),
        pd.DataFrame([{"end_date": "20231231", "roe": 12.5}]),
    )

    assert merged["20231231"]["publish_date"] == "20240331"
    assert merged["20231231"]["revenue"] == 120.0
    assert merged["20231231"]["net_profit"] == 10.0
    assert merged["20231231"]["total_assets"] == 999.0
    assert merged["20231231"]["roe"] == 12.5
    assert _normalize_loose_date("报告期:2024/06/30") == "20240630"
    assert _normalize_loose_date(None) == ""


def test_akshare_call_retries_named_boundary_failures(monkeypatch):
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise ValueError("temporary schema drift")
        return "ok"

    monkeypatch.setattr("invest_evolution.market_data.manager.time.sleep", lambda *_args, **_kwargs: None)

    assert _akshare_call(flaky, retries=3, sleep_seconds=0.0) == "ok"
    assert calls["count"] == 3
    assert ValueError in AKSHARE_BOUNDARY_EXCEPTIONS


def test_sync_financial_snapshots_from_akshare_bulk_filters_targets_and_updates_industry(tmp_path, monkeypatch):
    repo = MarketDataRepository(tmp_path / "akshare_bulk.db")
    repo.initialize_schema()
    repo.upsert_security_master(
        [
            {"code": "sh.600010", "name": "Foo", "list_date": "20200101", "source": "test"},
            {"code": "sz.000001", "name": "Bar", "list_date": "20200101", "source": "test"},
        ]
    )

    def fake_yjbb(date: str):
        assert date == "20240331"
        return pd.DataFrame(
            [
                {
                    "股票代码": "600010",
                    "最新公告日期": "2024-04-30",
                    "净资产收益率": 12.5,
                    "净利润-净利润": 10.0,
                    "营业总收入-营业总收入": 100.0,
                    "所处行业": "银行",
                },
                {
                    "股票代码": "000001",
                    "最新公告日期": "2024-04-29",
                    "净资产收益率": 9.9,
                    "净利润-净利润": 20.0,
                    "营业总收入-营业总收入": 200.0,
                    "所处行业": "保险",
                },
                {
                    "股票代码": "688001",
                    "最新公告日期": "2024-04-28",
                    "净资产收益率": 8.0,
                    "净利润-净利润": 5.0,
                    "营业总收入-营业总收入": 50.0,
                    "所处行业": "半导体",
                },
            ]
        )

    monkeypatch.setitem(sys.modules, "akshare", types.SimpleNamespace(stock_yjbb_em=fake_yjbb))

    service = DataIngestionService(repository=repo)
    result = service.sync_financial_snapshots_from_akshare_bulk(
        start_date="20240331",
        end_date="20240331",
    )

    assert result["stock_count"] == 2
    assert result["row_count"] == 2
    snapshots = repo.query_latest_financial_snapshots(["sh.600010", "sz.000001"], "20240501")
    assert {item["code"] for item in snapshots} == {"sh.600010", "sz.000001"}
    industries = {
        item["code"]: item["industry"]
        for item in repo.query_securities(["sh.600010", "sz.000001"])
    }
    assert industries == {"sh.600010": "银行", "sz.000001": "保险"}


def test_sync_financial_snapshots_from_akshare_bulk_continues_on_boundary_errors(tmp_path, monkeypatch):
    repo = MarketDataRepository(tmp_path / "akshare_bulk_error.db")
    repo.initialize_schema()
    repo.upsert_security_master(
        [{"code": "sh.600010", "name": "Foo", "list_date": "20200101", "source": "test"}]
    )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(stock_yjbb_em=lambda **_kwargs: (_ for _ in ()).throw(ValueError("temporary"))),
    )
    monkeypatch.setattr("invest_evolution.market_data.manager.time.sleep", lambda *_args, **_kwargs: None)

    service = DataIngestionService(repository=repo)
    result = service.sync_financial_snapshots_from_akshare_bulk(
        start_date="20240331",
        end_date="20240331",
    )

    assert result["stock_count"] == 0
    assert result["row_count"] == 0


def test_sync_daily_bars_from_tushare_persists_security_master_and_bars(tmp_path, monkeypatch):
    repo = MarketDataRepository(tmp_path / "daily_bars.db")
    repo.initialize_schema()

    class FakePro:
        def stock_basic(self, **kwargs):
            return pd.DataFrame(
                [
                    {"ts_code": "600010.SH", "name": "Foo", "list_date": "20200101", "delist_date": ""},
                    {"ts_code": "688001.SH", "name": "Sci", "list_date": "20200101", "delist_date": ""},
                ]
            )

        def daily(self, ts_code: str, **kwargs):
            if ts_code != "600010.SH":
                return pd.DataFrame([])
            return pd.DataFrame(
                [
                    {
                        "trade_date": "20240103",
                        "open": 10.0,
                        "high": 10.5,
                        "low": 9.9,
                        "close": 10.2,
                        "vol": 1000,
                        "amount": 5000,
                        "pct_chg": 2.0,
                    },
                    {
                        "trade_date": "20240102",
                        "open": 9.8,
                        "high": 10.1,
                        "low": 9.7,
                        "close": 10.0,
                        "vol": 900,
                        "amount": 4500,
                        "pct_chg": 1.0,
                    },
                ]
            )

    monkeypatch.setitem(
        sys.modules,
        "tushare",
        types.SimpleNamespace(set_token=lambda token: None, pro_api=lambda: FakePro()),
    )

    service = DataIngestionService(repository=repo, tushare_token="demo-token")
    result = service.sync_daily_bars_from_tushare(stock_limit=2)

    assert result["stock_count"] == 1
    assert result["row_count"] == 2
    bars = repo.query_daily_bars(["sh.600010"], start_date="20240101", end_date="20240131")
    assert list(bars["trade_date"]) == ["20240102", "20240103"]
    assert list(bars["volume"]) == [900.0, 1000.0]
    securities = repo.query_securities(["sh.600010"])
    assert securities[0]["name"] == "Foo"
    assert securities[0]["is_st"] == 0


def test_sync_financial_snapshots_from_akshare_uses_delisted_fallback_for_boundary_errors(tmp_path, monkeypatch):
    repo = MarketDataRepository(tmp_path / "akshare_fallback.db")
    repo.initialize_schema()
    repo.upsert_security_master(
        [
            {
                "code": "sh.600010",
                "name": "Foo",
                "list_date": "20200101",
                "delist_date": "20250101",
                "source": "test",
            }
        ]
    )
    repo.upsert_daily_bars(
        [
            {
                "code": "sh.600010",
                "trade_date": "20240102",
                "open": 10.0,
                "high": 10.1,
                "low": 9.8,
                "close": 10.5,
                "volume": 1,
                "amount": 1,
                "pct_chg": 0.0,
                "turnover": 1.0,
                "adj_flag": "hfq",
                "source": "test",
            }
        ]
    )

    fake_akshare = types.SimpleNamespace(
        stock_financial_abstract=lambda **_kwargs: (_ for _ in ()).throw(ValueError("summary unavailable")),
        stock_balance_sheet_by_report_em=lambda **_kwargs: (_ for _ in ()).throw(KeyError("delisted")),
        stock_balance_sheet_by_report_delisted_em=lambda **_kwargs: pd.DataFrame(
            [{"REPORT_DATE": "2024-03-31", "TOTAL_ASSETS": 999.0}]
        ),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)
    monkeypatch.setattr("invest_evolution.market_data.manager.time.sleep", lambda *_args, **_kwargs: None)

    service = DataIngestionService(repository=repo)
    result = service.sync_financial_snapshots_from_akshare(test_mode=True)

    assert result["stock_count"] == 1
    assert result["row_count"] == 1
    snapshots = repo.query_latest_financial_snapshots(["sh.600010"], "20250101")
    assert snapshots[0]["total_assets"] == 999.0


def test_sync_factor_snapshots_persists_breakout_and_relative_strength(tmp_path):
    repo = MarketDataRepository(tmp_path / "factor.db")
    repo.initialize_schema()
    repo.upsert_security_master(
        [{"code": "sh.600010", "name": "Foo", "list_date": "20200101", "source": "test"}]
    )
    repo.upsert_index_bars(
        [
            {
                "index_code": "sh.000300",
                "trade_date": f"202401{day:02d}",
                "open": 100 + day,
                "high": 101 + day,
                "low": 99 + day,
                "close": 100 + day,
                "volume": 1,
                "amount": 1,
                "pct_chg": 0.1,
                "source": "test",
            }
            for day in range(1, 26)
        ]
    )
    repo.upsert_daily_bars(
        [
            {
                "code": "sh.600010",
                "trade_date": f"202401{day:02d}",
                "open": 10 + day,
                "high": 10.5 + day,
                "low": 9.5 + day,
                "close": 10 + day,
                "volume": 1000 + day,
                "amount": 5000 + day,
                "pct_chg": 0.5,
                "turnover": 1.0 + day / 100,
                "source": "test",
            }
            for day in range(1, 26)
        ]
    )

    service = DataIngestionService(repository=repo)
    result = service.sync_factor_snapshots(start_date="20240101", end_date="20240125")

    assert result["stock_count"] == 1
    assert result["row_count"] == 25
    factors = repo.query_factor_snapshots(["sh.600010"], start_date="20240101", end_date="20240125")
    assert len(factors) == 25
    assert set(factors["breakout20"].dropna().astype(int).unique()).issubset({0, 1})
    assert "relative_strength_hs300" in factors.columns


def test_sync_security_status_daily_handles_invalid_list_date_and_market_limits(tmp_path):
    repo = MarketDataRepository(tmp_path / "status.db")
    repo.initialize_schema()
    repo.upsert_security_master(
        [
            {"code": "sh.600010", "name": "Foo", "list_date": "invalid", "is_st": 0, "source": "test"},
            {"code": "sz.300001", "name": "ST Bar", "list_date": "20240101", "is_st": 1, "source": "test"},
        ]
    )
    repo.upsert_daily_bars(
        [
            {
                "code": "sh.600010",
                "trade_date": "20240105",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.4,
                "volume": 1000,
                "amount": 5000,
                "pct_chg": 9.6,
                "turnover": 1.2,
                "source": "test",
            },
            {
                "code": "sz.300001",
                "trade_date": "20240105",
                "open": 20.0,
                "high": 21.0,
                "low": 19.5,
                "close": 20.9,
                "volume": 1000,
                "amount": 5000,
                "pct_chg": 5.0,
                "turnover": 1.2,
                "source": "test",
            },
        ]
    )

    service = DataIngestionService(repository=repo)
    result = service.sync_security_status_daily(start_date="20240101", end_date="20240131")

    assert result["row_count"] == 2
    rows = repo.query_security_status_daily(["sh.600010", "sz.300001"], "20240101", "20240131")
    foo = rows[rows["code"] == "sh.600010"].iloc[0]
    growth = rows[rows["code"] == "sz.300001"].iloc[0]
    assert int(foo["is_new_stock_window"]) == 0
    assert int(foo["is_limit_up"]) == 1
    assert int(growth["is_st"]) == 1
    assert int(growth["is_limit_up"]) == 1


def test_sync_capital_flow_daily_from_akshare_retries_and_persists_rows(tmp_path, monkeypatch):
    repo = MarketDataRepository(tmp_path / "capital_flow.db")
    repo.initialize_schema()
    repo.upsert_security_master(
        [{"code": "sh.600010", "name": "Foo", "list_date": "20200101", "source": "test"}]
    )
    calls = {"count": 0}

    class FakeRequestException(Exception):
        pass

    def fake_fetch(session, code: str, timeout: float = 10.0):
        calls["count"] += 1
        if calls["count"] < 3:
            raise FakeRequestException("temporary")
        return pd.DataFrame(
            [
                {
                    "日期": "20240105",
                    "收盘价": 10.2,
                    "涨跌幅": 1.5,
                    "主力净流入-净额": 100.0,
                    "主力净流入-净占比": 2.0,
                    "超大单净流入-净额": 50.0,
                    "超大单净流入-净占比": 1.0,
                    "大单净流入-净额": 25.0,
                    "大单净流入-净占比": 0.5,
                    "中单净流入-净额": -10.0,
                    "中单净流入-净占比": -0.2,
                    "小单净流入-净额": -65.0,
                    "小单净流入-净占比": -1.3,
                }
            ]
        )

    monkeypatch.setattr("invest_evolution.market_data.manager._fetch_capital_flow_history_with_session", fake_fetch)
    monkeypatch.setattr(
        "invest_evolution.market_data.manager._requests_boundary_exceptions",
        lambda *extra: (FakeRequestException, *extra),
    )
    monkeypatch.setattr("invest_evolution.market_data.manager.time.sleep", lambda *_args, **_kwargs: None)

    service = DataIngestionService(repository=repo)
    result = service.sync_capital_flow_daily_from_akshare(request_sleep_seconds=0.0)

    assert calls["count"] == 3
    assert result["stock_count"] == 1
    assert result["row_count"] == 1
    assert result["failed_count"] == 0


def test_sync_financial_snapshots_from_akshare_bulk_reraises_non_boundary_errors(tmp_path, monkeypatch):
    repo = MarketDataRepository(tmp_path / "akshare_bulk_raise.db")
    repo.initialize_schema()
    repo.upsert_security_master(
        [{"code": "sh.600010", "name": "Foo", "list_date": "20200101", "source": "test"}]
    )

    class UnexpectedAkshareFailure(Exception):
        pass

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(stock_yjbb_em=lambda **_kwargs: (_ for _ in ()).throw(UnexpectedAkshareFailure("boom"))),
    )
    monkeypatch.setattr(
        "invest_evolution.market_data.manager._requests_boundary_exceptions",
        lambda *extra: tuple(extra),
    )

    service = DataIngestionService(repository=repo)

    with pytest.raises(UnexpectedAkshareFailure):
        service.sync_financial_snapshots_from_akshare_bulk(
            start_date="20240331",
            end_date="20240331",
        )


def test_sync_dragon_tiger_list_from_akshare_persists_filtered_records(tmp_path, monkeypatch):
    repo = MarketDataRepository(tmp_path / "dragon.db")
    repo.initialize_schema()
    fake_akshare = types.SimpleNamespace(
        stock_lhb_detail_em=lambda **kwargs: pd.DataFrame(
            [
                {
                    "代码": "600010",
                    "上榜日": "20240105",
                    "名称": "Foo",
                    "解读": "test",
                    "收盘价": 10.2,
                    "涨跌幅": 1.5,
                    "龙虎榜净买额": 100.0,
                    "龙虎榜买入额": 150.0,
                    "龙虎榜卖出额": 50.0,
                    "龙虎榜成交额": 200.0,
                    "市场总成交额": 5000.0,
                    "净买额占总成交比": 2.0,
                    "成交额占总成交比": 4.0,
                    "换手率": 6.0,
                    "流通市值": 8000.0,
                    "上榜原因": "涨幅偏离",
                    "上榜后1日": 1.0,
                    "上榜后2日": 1.2,
                    "上榜后5日": 2.0,
                    "上榜后10日": 3.0,
                },
                {"代码": "123456", "上榜日": "20240105"},
            ]
        )
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    service = DataIngestionService(repository=repo)
    result = service.sync_dragon_tiger_list_from_akshare("20240101", "20240131")

    assert result["row_count"] == 1
    rows = repo.query_dragon_tiger_list(["sh.600010"], "20240101", "20240131")
    assert len(rows) == 1
    assert rows.iloc[0]["reason"] == "涨幅偏离"
