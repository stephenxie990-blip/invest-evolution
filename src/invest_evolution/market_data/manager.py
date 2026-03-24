"""Canonical market data manager, ingestion, quality, and service facade."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import warnings
from datetime import datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Sequence, cast

import numpy as np
import pandas as pd

from invest_evolution.config import PROJECT_ROOT, config, normalize_date
from invest_evolution.config.control_plane import get_runtime_data_policy
from .datasets import (
    CapitalFlowDatasetService,
    DataProvider,
    DataSourceUnavailableError,
    EventDatasetService,
    EvolutionDataLoader,
    IntradayDatasetBuilder,
    MockDataProvider,
    TrainingDatasetBuilder,
    WebDatasetService,
    _dict_of_objects,
    _float_value,
    _int_value,
    _random_cutoff_date,
    _string_list,
    _training_readiness_contract,
    generate_mock_stock_data,
)
from .repository import (
    BenchmarkDataService,
    DataQualityService,
    MarketDataGateway,
    MarketDataRepository,
    MarketQueryService,
)

logger = logging.getLogger(__name__)

AKSHARE_BOUNDARY_EXCEPTIONS = (
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    IndexError,
    RuntimeError,
)

_QUALITY_AUDIT_SNAPSHOT_KEY = "quality_audit_snapshot"
_QUALITY_AUDIT_UPDATED_AT_KEY = "quality_audit_updated_at"
_QUALITY_AUDIT_MAX_AGE_SECONDS = 300


def _requests_boundary_exceptions(
    *extra: type[BaseException],
) -> tuple[type[BaseException], ...]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            requests_module = import_module("requests")
    except (ImportError, AttributeError):
        return tuple(extra)
    request_exception = cast(type[BaseException], requests_module.RequestException)
    return (request_exception, *extra)


def _is_requests_boundary_exception(exc: BaseException, *extra: type[BaseException]) -> bool:
    return isinstance(exc, _requests_boundary_exceptions(*(extra or AKSHARE_BOUNDARY_EXCEPTIONS)))


def _akshare_boundary_exception_types() -> tuple[type[BaseException], ...]:
    return _requests_boundary_exceptions(*AKSHARE_BOUNDARY_EXCEPTIONS)


def _requests_session() -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        requests_module = import_module("requests")
    return requests_module.Session()


def _load_baostock_module() -> Any:
    return import_module("baostock")


def _load_akshare_module() -> Any:
    return import_module("akshare")


def _load_tushare_module() -> Any:
    return import_module("tushare")


class _BaoStockResult(Protocol):
    fields: list[str]

    def next(self) -> bool:
        ...

    def get_row_data(self) -> list[str]:
        ...


def _format_bs_date(value: str) -> str:
    normalized = normalize_date(value)
    return f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:8]}"


def _is_a_share(code: str) -> bool:
    return code.startswith("sh.6") or code.startswith("sz.00") or code.startswith("sz.30")


def _ts_code_to_local(ts_code: str) -> str:
    symbol, market = ts_code.split(".")
    return f"{market.lower()}.{symbol}"


def _local_code_parts(code: str) -> tuple[str, str]:
    value = str(code or "").strip()
    if not value:
        return "", ""
    if "." in value:
        market, symbol = value.split(".", 1)
        return market.lower(), symbol
    return ("sh" if value.startswith("6") else "sz"), value


def _local_to_ak_em_symbol(code: str) -> str:
    market, symbol = _local_code_parts(code)
    if not symbol:
        return ""
    return f"{market.upper()}{symbol}"


def _local_to_ak_simple_symbol(code: str) -> str:
    _, symbol = _local_code_parts(code)
    return symbol


def _simple_symbol_to_local(symbol: str) -> str:
    value = str(symbol or "").strip().zfill(6)
    if value.startswith("6"):
        return f"sh.{value}"
    if value.startswith(("00", "30")):
        return f"sz.{value}"
    return ""


def _normalize_index_code(code: str) -> str:
    value = str(code or "").strip()
    if not value:
        return ""
    if value.startswith(("sh.", "sz.")):
        return value
    if "." in value:
        symbol, market = value.split(".")
        return f"{market.lower()}.{symbol}"
    if value.startswith("6"):
        return f"sh.{value}"
    return f"sz.{value}"


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest_trade_date(repository: MarketDataRepository) -> str:
    meta = repository.get_meta(["daily_bar_latest_date"])
    latest = meta.get("daily_bar_latest_date", "")
    if latest:
        return normalize_date(latest)
    latest = repository.get_status_summary(use_snapshot=False).get("latest_date", "")
    return normalize_date(latest or datetime.now().strftime("%Y%m%d"))


def _series_from_column(df: pd.DataFrame, column: str, *, dtype: str = "object") -> pd.Series:
    if column in df.columns:
        return cast(pd.Series, df[column])
    return pd.Series(pd.NA, index=df.index, dtype=dtype)


def _merge_financial_frames(*frames) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for frame in frames:
        if frame is None or getattr(frame, "empty", True):
            continue
        columns = {str(name): idx for idx, name in enumerate(frame.columns)}
        end_date_idx = columns.get("end_date")
        if end_date_idx is None:
            continue
        ann_date_idx = columns.get("ann_date")
        roe_idx = columns.get("roe")
        net_income_idx = columns.get("n_income")
        revenue_idx = columns.get("total_revenue")
        total_assets_idx = columns.get("total_assets")
        for row in frame.itertuples(index=False, name=None):
            report_date = normalize_date(row[end_date_idx] or "")
            if not report_date:
                continue
            record = merged.setdefault(report_date, {"report_date": report_date})
            ann_date = normalize_date(row[ann_date_idx] or "") if ann_date_idx is not None else ""
            if ann_date and not record.get("publish_date"):
                record["publish_date"] = ann_date
            if roe_idx is not None and _safe_float(row[roe_idx]) is not None:
                record["roe"] = row[roe_idx]
            if net_income_idx is not None and _safe_float(row[net_income_idx]) is not None:
                record["net_profit"] = row[net_income_idx]
            if revenue_idx is not None and _safe_float(row[revenue_idx]) is not None:
                record["revenue"] = row[revenue_idx]
            if total_assets_idx is not None and _safe_float(row[total_assets_idx]) is not None:
                record["total_assets"] = row[total_assets_idx]
    return merged


def _normalize_loose_date(value: Any) -> str:
    if value in (None, "", "None"):
        return ""
    text = str(value).strip()
    try:
        normalized = normalize_date(text)
        if normalized.isdigit() and len(normalized) == 8:
            return normalized
    except ValueError:
        pass
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _build_akshare_abstract_map(frame: pd.DataFrame | None) -> dict[str, dict[str, Any]]:
    if frame is None or frame.empty or "指标" not in frame.columns:
        return {}

    metric_candidates = {
        "net_profit": ["归母净利润"],
        "revenue": ["营业总收入", "营业收入"],
        "roe": ["净资产收益率(ROE)", "净资产收益率_平均", "净资产收益率ROE"],
    }
    selected_rows: dict[str, Any] = {}
    metric_series = frame["指标"].astype(str)
    for field, candidates in metric_candidates.items():
        for candidate in candidates:
            matched = frame.loc[metric_series == candidate]
            if matched.empty:
                matched = frame.loc[metric_series.str.contains(candidate, regex=False, na=False)]
            if not matched.empty:
                selected_rows[field] = matched.iloc[0]
                break

    date_columns: list[tuple[str, str]] = []
    for column in frame.columns:
        report_date = _normalize_loose_date(column)
        if report_date:
            date_columns.append((str(column), report_date))

    merged: dict[str, dict[str, Any]] = {}
    for original_column, report_date in date_columns:
        record = merged.setdefault(report_date, {"report_date": report_date})
        for field, row in selected_rows.items():
            value = _safe_float(row.get(original_column))
            if value is not None:
                record[field] = value
    return merged


def _merge_akshare_financial_frames(
    *,
    abstract_df: pd.DataFrame | None,
    balance_df: pd.DataFrame | None,
    latest_close: float | None,
) -> dict[str, dict[str, Any]]:
    merged = _build_akshare_abstract_map(abstract_df)
    if balance_df is None or balance_df.empty:
        return merged

    columns = {str(name): idx for idx, name in enumerate(balance_df.columns)}
    report_date_idx = columns.get("REPORT_DATE", columns.get("报告日"))
    publish_date_idx = columns.get("NOTICE_DATE", columns.get("公告日期"))
    total_assets_idx = columns.get("TOTAL_ASSETS", columns.get("资产总计"))
    share_capital_idx = columns.get("SHARE_CAPITAL", columns.get("总股本"))
    if report_date_idx is None:
        return merged

    for row in balance_df.itertuples(index=False, name=None):
        report_date = _normalize_loose_date(row[report_date_idx])
        if not report_date:
            continue
        record = merged.setdefault(report_date, {"report_date": report_date})
        publish_date = _normalize_loose_date(row[publish_date_idx]) if publish_date_idx is not None else ""
        if publish_date and not record.get("publish_date"):
            record["publish_date"] = publish_date
        total_assets = _safe_float(row[total_assets_idx]) if total_assets_idx is not None else None
        if total_assets is not None:
            record["total_assets"] = total_assets
        share_capital = _safe_float(row[share_capital_idx]) if share_capital_idx is not None else None
        if latest_close is not None and share_capital is not None:
            record["market_cap"] = latest_close * share_capital
    return merged


def _financial_report_dates(start_date: str = "20100331", end_date: str | None = None) -> list[str]:
    start = normalize_date(start_date)
    end = normalize_date(end_date or datetime.now().strftime("%Y%m%d"))
    quarter_suffixes = ["0331", "0630", "0930", "1231"]
    dates: list[str] = []
    for year in range(int(start[:4]), int(end[:4]) + 1):
        for suffix in quarter_suffixes:
            report_date = f"{year}{suffix}"
            if start <= report_date <= end:
                dates.append(report_date)
    return dates


def _bar_time_to_trade_date(value: Any) -> str:
    text = str(value or '').strip()
    return normalize_date(text[:8]) if len(text) >= 8 else ''


def _fetch_capital_flow_history_with_session(session: Any, code: str, timeout: float = 10.0) -> pd.DataFrame:
    market, symbol = _local_code_parts(code)
    market_map = {"sh": 1, "sz": 0, "bj": 0}
    if market not in market_map or not symbol:
        return pd.DataFrame()
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "lmt": "0",
        "klt": "101",
        "secid": f"{market_map[market]}.{symbol}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "_": int(time.time() * 1000),
    }
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    content = ((payload or {}).get("data") or {}).get("klines") or []
    if not content:
        return pd.DataFrame()
    frame = pd.DataFrame([item.split(",") for item in content])
    frame.columns = [
        "日期",
        "主力净流入-净额",
        "小单净流入-净额",
        "中单净流入-净额",
        "大单净流入-净额",
        "超大单净流入-净额",
        "主力净流入-净占比",
        "小单净流入-净占比",
        "中单净流入-净占比",
        "大单净流入-净占比",
        "超大单净流入-净占比",
        "收盘价",
        "涨跌幅",
        "_x",
        "_y",
    ]
    return cast(
        pd.DataFrame,
        frame[[
            "日期",
            "收盘价",
            "涨跌幅",
            "主力净流入-净额",
            "主力净流入-净占比",
            "超大单净流入-净额",
            "超大单净流入-净占比",
            "大单净流入-净额",
            "大单净流入-净占比",
            "中单净流入-净额",
            "中单净流入-净占比",
            "小单净流入-净额",
            "小单净流入-净占比",
        ]],
    )


def _akshare_call(func, /, *args, retries: int = 2, sleep_seconds: float = 0.6, **kwargs):
    last_error: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            return func(*args, **kwargs)
        except _akshare_boundary_exception_types() as exc:  # pragma: no cover - 真实网络异常兜底
            last_error = exc
            if attempt >= retries:
                raise
            time.sleep(sleep_seconds * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("AKShare 调用失败")


class DataIngestionService:
    """Write-side application service for the canonical schema."""

    def __init__(
        self,
        repository: MarketDataRepository | None = None,
        db_path: str | None = None,
        tushare_token: str | None = None,
    ):
        self.repository = repository or MarketDataRepository(db_path)
        self.tushare_token = tushare_token or os.environ.get("TUSHARE_TOKEN", "")
        self.repository.initialize_schema()
        self.quality_service = DataQualityService(repository=self.repository)

    def _get_latest_close_map(self, codes: Sequence[str] | None = None) -> dict[str, float | None]:
        clauses = ["d.adj_flag = 'hfq'"]
        params: list[Any] = []
        if codes:
            placeholders = ",".join(["?"] * len(codes))
            clauses.append(f"d.code IN ({placeholders})")
            params.extend(codes)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT d.code, d.close
            FROM daily_bar d
            JOIN (
                SELECT code, MAX(trade_date) AS latest_trade_date
                FROM daily_bar
                WHERE adj_flag = 'hfq'
                GROUP BY code
            ) latest
              ON d.code = latest.code
             AND d.trade_date = latest.latest_trade_date
             AND d.adj_flag = 'hfq'
            {where}
        """
        with self.repository.connect() as conn:
            frame = pd.read_sql_query(query, conn, params=params)
        if frame.empty:
            return {}
        normalized = frame.copy()
        normalized["close"] = normalized["close"].map(_safe_float)
        return dict(zip(normalized["code"].astype(str), normalized["close"], strict=False))

    def sync_security_master(self) -> dict[str, Any]:
        bs = _load_baostock_module()

        login = bs.login()
        if getattr(login, "error_code", "0") != "0":
            raise RuntimeError(f"Baostock 登录失败: {getattr(login, 'error_msg', '')}")

        records: list[dict[str, Any]] = []
        try:
            rs = bs.query_stock_basic()
            while rs.next():
                row = rs.get_row_data()
                code = str(row[0]).strip()
                if not _is_a_share(code):
                    continue
                name = str(row[1]).strip()
                list_date = row[2] if len(row) > 2 else ""
                delist_date = row[3] if len(row) > 3 else ""
                industry = row[4] if len(row) > 4 else ""
                records.append(
                    {
                        "code": code,
                        "name": name,
                        "list_date": list_date,
                        "delist_date": delist_date,
                        "industry": industry,
                        "is_st": "ST" in name,
                        "source": "baostock",
                    }
                )
        finally:
            bs.logout()

        count = self.repository.upsert_security_master(records)
        self.repository.upsert_meta(
            {
                "last_security_sync": datetime.now().isoformat(timespec="seconds"),
                "security_master_source": "baostock",
            }
        )
        quality = self.quality_service.persist_audit()
        return {"stock_count": count, "source": "baostock", "quality": quality}

    def sync_daily_bars(
        self,
        codes: Sequence[str] | None = None,
        start_date: str = "20160101",
        end_date: str | None = None,
    ) -> dict[str, Any]:
        bs = _load_baostock_module()

        start = normalize_date(start_date)
        end = normalize_date(end_date or datetime.now().strftime("%Y%m%d"))
        codes = list(codes) if codes else self.repository.list_security_codes()
        if not codes:
            self.sync_security_master()
            codes = self.repository.list_security_codes()

        login = bs.login()
        if getattr(login, "error_code", "0") != "0":
            raise RuntimeError(f"Baostock 登录失败: {getattr(login, 'error_msg', '')}")

        total_rows = 0
        synced_codes = 0
        try:
            for index, code in enumerate(codes, start=1):
                rs = bs.query_history_k_data_plus(
                    code,
                    "date,code,open,high,low,close,volume,amount,pctChg,turn",
                    start_date=_format_bs_date(start),
                    end_date=_format_bs_date(end),
                    frequency="d",
                    adjustflag="2",
                )
                if getattr(rs, "error_code", "0") != "0":
                    logger.warning("Baostock K线下载失败: %s %s", code, getattr(rs, "error_msg", ""))
                    continue
                if rs is None:
                    continue

                result = cast(_BaoStockResult, rs)
                records: list[dict[str, Any]] = []
                while result.next():
                    row = dict(zip(result.fields, result.get_row_data()))
                    records.append(
                        {
                            "code": row.get("code", code),
                            "trade_date": row.get("date", ""),
                            "open": row.get("open"),
                            "high": row.get("high"),
                            "low": row.get("low"),
                            "close": row.get("close"),
                            "volume": row.get("volume"),
                            "amount": row.get("amount"),
                            "pct_chg": row.get("pctChg"),
                            "turnover": row.get("turn"),
                            "adj_flag": "hfq",
                            "source": "baostock",
                        }
                    )

                inserted = self.repository.upsert_daily_bars(records)
                if inserted:
                    synced_codes += 1
                    total_rows += inserted
                if index % 100 == 0:
                    logger.info("已同步日线 %s/%s", index, len(codes))
        finally:
            bs.logout()

        self.repository.upsert_meta(
            {
                "last_daily_bar_sync": datetime.now().isoformat(timespec="seconds"),
                "daily_bar_latest_date": end,
                "daily_bar_source": "baostock",
            }
        )
        self.sync_trading_calendar(start_date=start, end_date=end)
        quality = self.quality_service.persist_audit()
        return {"stock_count": synced_codes, "row_count": total_rows, "source": "baostock", "latest_date": end, "quality": quality}

    def sync_index_bars(
        self,
        index_codes: Sequence[str] | None = None,
        start_date: str = "20160101",
        end_date: str | None = None,
    ) -> dict[str, Any]:
        bs = _load_baostock_module()

        start = normalize_date(start_date)
        end = normalize_date(end_date or datetime.now().strftime("%Y%m%d"))
        codes = [_normalize_index_code(code) for code in (index_codes or config.index_codes or [])]
        codes = [code for code in codes if code]
        if not codes:
            codes = ["sh.000001", "sz.399001", "sz.399006", "sh.000300"]
        if "sh.000300" not in codes:
            codes.append("sh.000300")

        login = bs.login()
        if getattr(login, "error_code", "0") != "0":
            raise RuntimeError(f"Baostock 登录失败: {getattr(login, 'error_msg', '')}")

        total_rows = 0
        synced_codes = 0
        try:
            for code in codes:
                rs = bs.query_history_k_data_plus(
                    code,
                    "date,code,open,high,low,close,volume,amount,pctChg",
                    start_date=_format_bs_date(start),
                    end_date=_format_bs_date(end),
                    frequency="d",
                    adjustflag="2",
                )
                if getattr(rs, "error_code", "0") != "0":
                    logger.warning("Baostock 指数K线下载失败: %s %s", code, getattr(rs, "error_msg", ""))
                    continue
                if rs is None:
                    continue

                result = cast(_BaoStockResult, rs)
                records: list[dict[str, Any]] = []
                while result.next():
                    row = dict(zip(result.fields, result.get_row_data()))
                    records.append(
                        {
                            "index_code": row.get("code", code),
                            "trade_date": row.get("date", ""),
                            "open": row.get("open"),
                            "high": row.get("high"),
                            "low": row.get("low"),
                            "close": row.get("close"),
                            "volume": row.get("volume"),
                            "amount": row.get("amount"),
                            "pct_chg": row.get("pctChg"),
                            "source": "baostock",
                        }
                    )

                inserted = self.repository.upsert_index_bars(records)
                if inserted:
                    synced_codes += 1
                    total_rows += inserted
        finally:
            bs.logout()

        self.repository.upsert_meta(
            {
                "last_index_bar_sync": datetime.now().isoformat(timespec="seconds"),
                "index_bar_latest_date": end,
                "index_bar_source": "baostock",
            }
        )
        self.sync_trading_calendar(start_date=start, end_date=end)
        quality = self.quality_service.persist_audit()
        return {
            "index_count": synced_codes,
            "row_count": total_rows,
            "source": "baostock",
            "latest_date": end,
            "quality": quality,
        }

    def sync_trading_calendar(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        market: str = "CN_A",
    ) -> dict[str, Any]:
        stock_start, stock_end = self.repository.get_available_date_range()
        index_start, index_end = self.repository.get_index_available_date_range()
        start = normalize_date(start_date or stock_start or index_start or datetime.now().strftime("%Y%m%d"))
        end = normalize_date(end_date or stock_end or index_end or start)
        stock_df = self.repository.query_daily_bars(start_date=start, end_date=end)
        index_df = self.repository.query_index_bars(start_date=start, end_date=end)
        stock_dates = _series_from_column(stock_df, "trade_date", dtype="string").astype(str).tolist()
        index_dates = _series_from_column(index_df, "trade_date", dtype="string").astype(str).tolist()
        dates = sorted(set(stock_dates) | set(index_dates))
        records: list[dict[str, Any]] = []
        for idx, trade_date in enumerate(dates):
            records.append(
                {
                    "market": market,
                    "trade_date": trade_date,
                    "prev_trade_date": dates[idx - 1] if idx > 0 else "",
                    "next_trade_date": dates[idx + 1] if idx + 1 < len(dates) else "",
                    "is_open": 1,
                    "source": "derived",
                }
            )
        inserted = self.repository.upsert_trading_calendar(records)
        self.repository.upsert_meta(
            {
                "last_calendar_sync": datetime.now().isoformat(timespec="seconds"),
                "calendar_source": "derived",
            }
        )
        return {"row_count": inserted, "market": market, "latest_date": end}

    def sync_trading_calendar_from_akshare(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        market: str = "CN_A",
    ) -> dict[str, Any]:
        ak = _load_akshare_module()

        frame = _akshare_call(ak.tool_trade_date_hist_sina)
        if frame is None or frame.empty:
            return {"row_count": 0, "market": market, "source": "akshare"}
        dates = sorted({_normalize_loose_date(value) for value in frame["trade_date"].tolist() if _normalize_loose_date(value)})
        if start_date:
            start = normalize_date(start_date)
            dates = [value for value in dates if value >= start]
        if end_date:
            end = normalize_date(end_date)
            dates = [value for value in dates if value <= end]
        else:
            end = dates[-1] if dates else normalize_date(datetime.now().strftime("%Y%m%d"))
        records: list[dict[str, Any]] = []
        for idx, trade_date in enumerate(dates):
            records.append(
                {
                    "market": market,
                    "trade_date": trade_date,
                    "prev_trade_date": dates[idx - 1] if idx > 0 else "",
                    "next_trade_date": dates[idx + 1] if idx + 1 < len(dates) else "",
                    "is_open": 1,
                    "source": "akshare",
                }
            )
        inserted = self.repository.upsert_trading_calendar(records)
        self.repository.upsert_meta(
            {
                "last_calendar_sync": datetime.now().isoformat(timespec="seconds"),
                "calendar_source": "akshare",
            }
        )
        return {"row_count": inserted, "market": market, "source": "akshare", "latest_date": end}

    def sync_security_status_daily(
        self,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        securities = self.repository.query_securities(codes)
        if not securities:
            return {"row_count": 0, "stock_count": 0, "source": "derived"}
        start = normalize_date(start_date or "19000101")
        end = normalize_date(end_date or datetime.now().strftime("%Y%m%d"))
        price_df = self.repository.query_daily_bars(codes=[row["code"] for row in securities], start_date=start, end_date=end)
        if price_df.empty:
            return {"row_count": 0, "stock_count": 0, "source": "derived"}
        sec_frame = pd.DataFrame(securities).copy()
        sec_frame["code"] = sec_frame["code"].astype(str)
        merged = price_df.copy()
        merged["code"] = merged["code"].astype(str)
        merged["trade_date"] = merged["trade_date"].map(normalize_date)
        merged["pct_chg"] = merged["pct_chg"].map(lambda value: _safe_float(value) or 0.0)
        merged = merged.merge(
            sec_frame[["code", "list_date", "is_st"]],
            on="code",
            how="left",
        )
        merged["list_date"] = merged["list_date"].fillna("").map(normalize_date)
        merged["is_st"] = merged["is_st"].fillna(0).astype(int)
        merged["limit_pct"] = np.where(
            merged["is_st"] == 1,
            4.8,
            np.where(merged["code"].str.startswith(("sz.300", "sh.688")), 19.5, 9.5),
        )
        trade_dates = pd.to_datetime(merged["trade_date"], format="%Y%m%d", errors="coerce")
        list_dates = pd.to_datetime(merged["list_date"], format="%Y%m%d", errors="coerce")
        day_deltas = (trade_dates - list_dates).dt.days
        merged["is_new_stock_window"] = (
            day_deltas.le(90).fillna(False).astype(int)
        )
        merged["is_limit_up"] = merged["pct_chg"].ge(merged["limit_pct"]).astype(int)
        merged["is_limit_down"] = merged["pct_chg"].le(-merged["limit_pct"]).astype(int)
        merged["source"] = "derived"
        records = cast(
            list[dict[str, Any]],
            cast(
                Any,
                merged[
                    [
                        "code",
                        "trade_date",
                        "is_st",
                        "is_new_stock_window",
                        "is_limit_up",
                        "is_limit_down",
                        "source",
                    ]
                ],
            ).to_dict("records"),
        )
        inserted = self.repository.upsert_security_status_daily(records)
        self.repository.upsert_meta(
            {
                "last_status_sync": datetime.now().isoformat(timespec="seconds"),
                "status_source": "derived",
            }
        )
        return {"row_count": inserted, "stock_count": len(securities), "source": "derived", "latest_date": end}

    def sync_factor_snapshots(
        self,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        benchmark_code: str = "sh.000300",
    ) -> dict[str, Any]:
        securities = self.repository.query_securities(codes)
        if not securities:
            return {"row_count": 0, "stock_count": 0, "source": "derived"}
        start = normalize_date(start_date or "19000101")
        end = normalize_date(end_date or datetime.now().strftime("%Y%m%d"))
        price_df = self.repository.query_daily_bars(codes=[row["code"] for row in securities], start_date=start, end_date=end)
        if price_df.empty:
            return {"row_count": 0, "stock_count": 0, "source": "derived"}
        bench_df = self.repository.query_index_bars(index_codes=[benchmark_code], start_date=start, end_date=end)
        benchmark_returns = pd.Series(dtype=float)
        if not bench_df.empty:
            bench = bench_df.sort_values("trade_date").copy()
            bench["close"] = pd.to_numeric(bench["close"], errors="coerce")
            benchmark_returns = bench.set_index("trade_date")["close"].pct_change(20) * 100
        records: list[dict[str, Any]] = []
        for code, frame in price_df.groupby("code"):
            sub = frame.sort_values("trade_date").copy()
            sub["close"] = pd.to_numeric(sub["close"], errors="coerce")
            sub["volume"] = pd.to_numeric(sub.get("volume"), errors="coerce")
            sub["turnover"] = pd.to_numeric(sub.get("turnover"), errors="coerce")
            sub["pct_chg"] = pd.to_numeric(sub.get("pct_chg"), errors="coerce")
            sub["ma5"] = sub["close"].rolling(5).mean()
            sub["ma10"] = sub["close"].rolling(10).mean()
            sub["ma20"] = sub["close"].rolling(20).mean()
            sub["ma60"] = sub["close"].rolling(60).mean()
            sub["momentum20"] = sub["close"].pct_change(20) * 100
            sub["momentum60"] = sub["close"].pct_change(60) * 100
            sub["volatility20"] = sub["pct_chg"].rolling(20).std()
            vol_mean20 = cast(pd.Series, sub["volume"].rolling(20).mean())
            sub["volume_ratio"] = sub["volume"] / cast(pd.Series, vol_mean20.replace(0, np.nan))
            sub["turnover_mean20"] = sub["turnover"].rolling(20).mean()
            roll_max60 = sub["close"].rolling(60).max()
            sub["drawdown60"] = (sub["close"] / roll_max60 - 1.0) * 100
            prior_high20 = sub["close"].rolling(20).max().shift(1)
            sub["breakout20"] = (sub["close"] > prior_high20).fillna(False).astype(int)
            sub["relative_strength_hs300"] = sub["momentum20"].sub(benchmark_returns.reindex(sub["trade_date"]).values)
            export_frame = sub.assign(code=code, source="derived").copy()
            export_frame["breakout20"] = export_frame["breakout20"].fillna(0).astype(int)
            export_records = cast(
                Any,
                export_frame[
                    [
                        "code",
                        "trade_date",
                        "ma5",
                        "ma10",
                        "ma20",
                        "ma60",
                        "momentum20",
                        "momentum60",
                        "volatility20",
                        "volume_ratio",
                        "turnover_mean20",
                        "drawdown60",
                        "relative_strength_hs300",
                        "breakout20",
                        "source",
                    ]
                ],
            ).to_dict("records")
            records.extend(
                cast(list[dict[str, Any]], export_records)
            )
        inserted = self.repository.upsert_factor_snapshots(records)
        self.repository.upsert_meta(
            {
                "last_factor_sync": datetime.now().isoformat(timespec="seconds"),
                "factor_source": "derived",
            }
        )
        return {"row_count": inserted, "stock_count": len(securities), "source": "derived", "latest_date": end}

    def sync_financial_snapshots_from_akshare_bulk(
        self,
        codes: Sequence[str] | None = None,
        stock_limit: int | None = None,
        offset: int = 0,
        start_date: str = "20100331",
        end_date: str | None = None,
    ) -> dict[str, Any]:
        ak = _load_akshare_module()

        securities = self.repository.query_securities(codes)
        if not securities:
            return {"stock_count": 0, "row_count": 0, "source": "akshare_bulk"}
        if offset > 0:
            securities = securities[int(offset):]
        if stock_limit:
            securities = securities[: max(1, int(stock_limit))]
        target_codes = [str(row.get("code", "")).strip() for row in securities if _is_a_share(str(row.get("code", "")).strip())]
        if not target_codes:
            return {"stock_count": 0, "row_count": 0, "source": "akshare_bulk"}
        target_set = set(target_codes)
        industry_updates: dict[str, str] = {}
        report_dates = _financial_report_dates(start_date=start_date, end_date=end_date)
        total_rows = 0
        touched_codes: set[str] = set()
        latest_report_date = ""

        for report_date in report_dates:
            try:
                frame = _akshare_call(ak.stock_yjbb_em, date=report_date)
            except _akshare_boundary_exception_types() as exc:  # pragma: no cover
                logger.warning("AKShare 批量财报下载失败: %s %s", report_date, exc)
                continue
            if frame is None or frame.empty or "股票代码" not in frame.columns:
                continue

            normalized = frame.copy()
            normalized["local_code"] = normalized["股票代码"].map(
                lambda value: _simple_symbol_to_local(str(value or ""))
            )
            normalized = normalized[normalized["local_code"].isin(target_set)].copy()
            if normalized.empty:
                continue

            normalized = normalized.assign(
                code=normalized["local_code"].astype(str),
                report_date=report_date,
                publish_date=_series_from_column(normalized, "最新公告日期").map(_normalize_loose_date),
                roe=_series_from_column(normalized, "净资产收益率"),
                net_profit=_series_from_column(normalized, "净利润-净利润"),
                revenue=_series_from_column(normalized, "营业总收入-营业总收入"),
                total_assets=None,
                market_cap=None,
                source="akshare",
            )
            industries = _series_from_column(normalized, "所处行业").fillna("").astype(str).str.strip()
            industry_updates.update(
                {
                    code: industry
                    for code, industry in zip(normalized["code"].astype(str), industries, strict=False)
                    if industry
                }
            )
            records = cast(
                list[dict[str, Any]],
                cast(
                    Any,
                    normalized[
                        [
                            "code",
                            "report_date",
                            "publish_date",
                            "roe",
                            "net_profit",
                            "revenue",
                            "total_assets",
                            "market_cap",
                            "source",
                        ]
                    ],
                ).to_dict("records"),
            )
            touched_codes.update(cast(list[str], normalized["code"].astype(str).tolist()))
            latest_report_date = report_date
            total_rows += self.repository.upsert_financial_snapshots(records)

        if industry_updates:
            securities_map = {row["code"]: row for row in self.repository.query_securities(list(industry_updates))}
            patched = []
            for code, industry in industry_updates.items():
                base = dict(securities_map.get(code, {"code": code}))
                base["industry"] = industry
                base.setdefault("source", base.get("source") or "akshare")
                patched.append(base)
            self.repository.upsert_security_master(patched)

        self.repository.upsert_meta(
            {
                "last_financial_snapshot_sync": datetime.now().isoformat(timespec="seconds"),
                "financial_snapshot_source": "akshare",
            }
        )
        quality = self.quality_service.persist_audit()
        return {
            "stock_count": len(touched_codes),
            "row_count": total_rows,
            "source": "akshare",
            "latest_date": latest_report_date,
            "quality": quality,
        }

    def sync_financial_snapshots_from_akshare(
        self,
        codes: Sequence[str] | None = None,
        stock_limit: int | None = None,
        offset: int = 0,
        test_mode: bool = False,
    ) -> dict[str, Any]:
        ak = _load_akshare_module()

        securities = self.repository.query_securities(codes)
        if offset > 0:
            securities = securities[int(offset):]
        if stock_limit:
            securities = securities[: max(1, int(stock_limit))]
        if test_mode:
            securities = securities[:3]
        if not securities:
            return {"stock_count": 0, "row_count": 0, "source": "akshare"}

        latest_close_map = self._get_latest_close_map([str(row.get("code", "")) for row in securities])
        total_rows = 0
        processed = 0
        failed = 0
        latest_report_date = ""

        for row in securities:
            code = str(row.get("code", "")).strip()
            if not _is_a_share(code):
                continue
            abstract_df = pd.DataFrame()
            balance_df = pd.DataFrame()
            simple_symbol = _local_to_ak_simple_symbol(code)
            em_symbol = _local_to_ak_em_symbol(code)
            try:
                abstract_df = _akshare_call(ak.stock_financial_abstract, symbol=simple_symbol)
            except _akshare_boundary_exception_types() as exc:  # pragma: no cover - 网络异常兜底
                logger.warning("AKShare 财务摘要下载失败: %s %s", code, exc)
            try:
                balance_df = _akshare_call(ak.stock_balance_sheet_by_report_em, symbol=em_symbol)
            except _akshare_boundary_exception_types() as exc:  # pragma: no cover - 网络异常兜底
                if str(row.get("delist_date") or "").strip() and hasattr(ak, "stock_balance_sheet_by_report_delisted_em"):
                    try:
                        balance_df = _akshare_call(ak.stock_balance_sheet_by_report_delisted_em, symbol=em_symbol)
                    except _akshare_boundary_exception_types() as inner_exc:
                        logger.warning("AKShare 退市资产负债表下载失败: %s %s", code, inner_exc)
                else:
                    logger.warning("AKShare 资产负债表下载失败: %s %s", code, exc)

            merged = _merge_akshare_financial_frames(
                abstract_df=abstract_df,
                balance_df=balance_df,
                latest_close=latest_close_map.get(code),
            )
            records: list[dict[str, Any]] = []
            for report_date, payload in merged.items():
                if not any(payload.get(field) is not None for field in ("roe", "net_profit", "revenue", "total_assets", "market_cap")):
                    continue
                latest_report_date = max(latest_report_date, report_date) if latest_report_date else report_date
                records.append(
                    {
                        "code": code,
                        "report_date": report_date,
                        "publish_date": payload.get("publish_date", ""),
                        "roe": payload.get("roe"),
                        "net_profit": payload.get("net_profit"),
                        "revenue": payload.get("revenue"),
                        "total_assets": payload.get("total_assets"),
                        "market_cap": payload.get("market_cap"),
                        "source": "akshare",
                    }
                )
            inserted = self.repository.upsert_financial_snapshots(records)
            if inserted > 0:
                total_rows += inserted
                processed += 1
            elif abstract_df.empty and balance_df.empty:
                failed += 1
            time.sleep(0.1)

        self.repository.upsert_meta(
            {
                "last_financial_snapshot_sync": datetime.now().isoformat(timespec="seconds"),
                "financial_snapshot_source": "akshare",
            }
        )
        quality = self.quality_service.persist_audit()
        return {
            "stock_count": processed,
            "row_count": total_rows,
            "failed_count": failed,
            "source": "akshare",
            "latest_date": latest_report_date,
            "quality": quality,
        }

    def sync_capital_flow_daily_from_akshare(
        self,
        codes: Sequence[str] | None = None,
        stock_limit: int | None = None,
        offset: int = 0,
        request_sleep_seconds: float = 0.35,
    ) -> dict[str, Any]:
        securities = self.repository.query_securities(codes)
        if offset > 0:
            securities = securities[int(offset):]
        if stock_limit:
            securities = securities[: max(1, int(stock_limit))]
        records: list[dict[str, Any]] = []
        touched = 0
        failed = 0
        session = _requests_session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        })
        for row in securities:
            code = str(row.get("code", "")).strip()
            market, symbol = _local_code_parts(code)
            if market not in {"sh", "sz", "bj"} or not symbol:
                continue
            frame = pd.DataFrame()
            last_error = None
            for attempt in range(4):
                try:
                    frame = _fetch_capital_flow_history_with_session(session, code)
                    last_error = None
                    break
                except _requests_boundary_exceptions(ValueError, KeyError, TypeError) as exc:  # pragma: no cover
                    last_error = exc
                    time.sleep(max(0.8, request_sleep_seconds) * (attempt + 1))
            if last_error is not None:
                logger.warning("AKShare 个股资金流下载失败: %s %s", code, last_error)
                failed += 1
                continue
            if frame is None or frame.empty:
                time.sleep(request_sleep_seconds)
                continue
            touched += 1
            normalized_flow = frame.rename(
                columns={
                    "日期": "trade_date",
                    "收盘价": "close",
                    "涨跌幅": "pct_chg",
                    "主力净流入-净额": "main_net_inflow",
                    "主力净流入-净占比": "main_net_inflow_ratio",
                    "超大单净流入-净额": "super_large_net_inflow",
                    "超大单净流入-净占比": "super_large_net_inflow_ratio",
                    "大单净流入-净额": "large_net_inflow",
                    "大单净流入-净占比": "large_net_inflow_ratio",
                    "中单净流入-净额": "medium_net_inflow",
                    "中单净流入-净占比": "medium_net_inflow_ratio",
                    "小单净流入-净额": "small_net_inflow",
                    "小单净流入-净占比": "small_net_inflow_ratio",
                }
            ).assign(code=code, source="akshare")
            records.extend(
                cast(
                    list[dict[str, Any]],
                    cast(
                        Any,
                        normalized_flow[
                            [
                                "code",
                                "trade_date",
                                "close",
                                "pct_chg",
                                "main_net_inflow",
                                "main_net_inflow_ratio",
                                "super_large_net_inflow",
                                "super_large_net_inflow_ratio",
                                "large_net_inflow",
                                "large_net_inflow_ratio",
                                "medium_net_inflow",
                                "medium_net_inflow_ratio",
                                "small_net_inflow",
                                "small_net_inflow_ratio",
                                "source",
                            ]
                        ],
                    ).to_dict("records"),
                )
            )
            time.sleep(request_sleep_seconds)
        inserted = self.repository.upsert_capital_flow_daily(records)
        self.repository.upsert_meta(
            {
                "last_capital_flow_sync": datetime.now().isoformat(timespec="seconds"),
                "capital_flow_source": "akshare",
            }
        )
        return {"stock_count": touched, "row_count": inserted, "failed_count": failed, "source": "akshare"}

    def sync_dragon_tiger_list_from_akshare(
        self,
        start_date: str,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        ak = _load_akshare_module()

        start = normalize_date(start_date)
        end = normalize_date(end_date or datetime.now().strftime("%Y%m%d"))
        frame = _akshare_call(ak.stock_lhb_detail_em, start_date=start, end_date=end)
        if frame is None or frame.empty:
            return {"row_count": 0, "source": "akshare", "latest_date": end}
        records: list[dict[str, Any]] = []
        dragon_columns = {str(name): idx for idx, name in enumerate(frame.columns)}
        for row in frame.itertuples(index=False, name=None):
            local_code = _simple_symbol_to_local(str(row[dragon_columns["代码"]] or ""))
            if not local_code:
                continue
            records.append(
                {
                    "code": local_code,
                    "trade_date": row[dragon_columns["上榜日"]],
                    "name": row[dragon_columns["名称"]],
                    "interpretation": row[dragon_columns["解读"]],
                    "close": row[dragon_columns["收盘价"]],
                    "pct_chg": row[dragon_columns["涨跌幅"]],
                    "net_buy": row[dragon_columns["龙虎榜净买额"]],
                    "buy_amount": row[dragon_columns["龙虎榜买入额"]],
                    "sell_amount": row[dragon_columns["龙虎榜卖出额"]],
                    "turnover_amount": row[dragon_columns["龙虎榜成交额"]],
                    "market_turnover_amount": row[dragon_columns["市场总成交额"]],
                    "net_buy_ratio": row[dragon_columns["净买额占总成交比"]],
                    "turnover_ratio": row[dragon_columns["成交额占总成交比"]],
                    "turnover_rate": row[dragon_columns["换手率"]],
                    "float_market_cap": row[dragon_columns["流通市值"]],
                    "reason": row[dragon_columns["上榜原因"]],
                    "next_day_return": row[dragon_columns["上榜后1日"]],
                    "next_2day_return": row[dragon_columns["上榜后2日"]],
                    "next_5day_return": row[dragon_columns["上榜后5日"]],
                    "next_10day_return": row[dragon_columns["上榜后10日"]],
                    "source": "akshare",
                }
            )
        inserted = self.repository.upsert_dragon_tiger_list(records)
        self.repository.upsert_meta(
            {
                "last_dragon_tiger_sync": datetime.now().isoformat(timespec="seconds"),
                "dragon_tiger_source": "akshare",
            }
        )
        return {"row_count": inserted, "source": "akshare", "latest_date": end}

    def sync_financial_snapshots_from_tushare(
        self,
        stock_limit: int | None = None,
        test_mode: bool = False,
    ) -> dict[str, Any]:
        if not self.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN")

        ts = _load_tushare_module()

        ts.set_token(self.tushare_token)
        pro = ts.pro_api()

        basic = pro.stock_basic(exchange="", list_status="L,P,D", fields="ts_code,name,list_date,delist_date")
        if basic is None or basic.empty:
            return {"stock_count": 0, "row_count": 0, "source": "tushare"}

        if stock_limit:
            basic = basic.head(max(1, int(stock_limit)))
        if test_mode:
            basic = basic.head(3)

        latest_trade_date = _latest_trade_date(self.repository)
        market_cap_start = (datetime.strptime(latest_trade_date, "%Y%m%d") - timedelta(days=14)).strftime("%Y%m%d")
        market_caps: dict[str, float | None] = {}
        daily_basic = pro.daily_basic(
            trade_date=latest_trade_date,
            fields="ts_code,total_mv",
        )
        if daily_basic is None or daily_basic.empty:
            daily_basic = pro.daily_basic(
                start_date=market_cap_start,
                end_date=latest_trade_date,
                fields="ts_code,trade_date,total_mv",
            )
        if daily_basic is not None and not daily_basic.empty:
            if "trade_date" in daily_basic.columns:
                daily_basic = daily_basic.sort_values(["ts_code", "trade_date"]).drop_duplicates("ts_code", keep="last")
            daily_basic = daily_basic.copy()
            daily_basic["total_mv"] = daily_basic["total_mv"].map(_safe_float)
            market_caps = dict(
                zip(daily_basic["ts_code"].astype(str), daily_basic["total_mv"], strict=False)
            )

        total_rows = 0
        processed = 0
        for row in basic.itertuples(index=False):
            ts_code = str(row.ts_code)
            code = _ts_code_to_local(ts_code)
            if not _is_a_share(code):
                continue

            income = pro.income(ts_code=ts_code, fields="ts_code,ann_date,end_date,total_revenue,n_income")
            balancesheet = pro.balancesheet(ts_code=ts_code, fields="ts_code,ann_date,end_date,total_assets")
            indicator = pro.fina_indicator(ts_code=ts_code, fields="ts_code,ann_date,end_date,roe")
            merged = _merge_financial_frames(income, balancesheet, indicator)
            if not merged:
                time.sleep(0.05)
                continue

            records = []
            market_cap = market_caps.get(ts_code)
            for report_date, payload in merged.items():
                records.append(
                    {
                        "code": code,
                        "report_date": report_date,
                        "publish_date": payload.get("publish_date", ""),
                        "roe": payload.get("roe"),
                        "net_profit": payload.get("net_profit"),
                        "revenue": payload.get("revenue"),
                        "total_assets": payload.get("total_assets"),
                        "market_cap": market_cap,
                        "source": "tushare",
                    }
                )
            inserted = self.repository.upsert_financial_snapshots(records)
            if inserted:
                total_rows += inserted
                processed += 1
            time.sleep(0.05)

        self.repository.upsert_meta(
            {
                "last_financial_snapshot_sync": datetime.now().isoformat(timespec="seconds"),
                "financial_snapshot_source": "tushare",
            }
        )
        quality = self.quality_service.persist_audit()
        return {"stock_count": processed, "row_count": total_rows, "source": "tushare", "latest_date": latest_trade_date, "quality": quality}

    def sync_daily_bars_from_tushare(
        self,
        start_date: str = "20180101",
        end_date: str | None = None,
        stock_limit: int | None = None,
        test_mode: bool = False,
    ) -> dict[str, Any]:
        if not self.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN")

        ts = _load_tushare_module()

        ts.set_token(self.tushare_token)
        pro = ts.pro_api()

        start = normalize_date(start_date)
        end = normalize_date(end_date or datetime.now().strftime("%Y%m%d"))
        basic = pro.stock_basic(exchange="", list_status="L,P,D", fields="ts_code,name,list_date,delist_date")
        if basic is None or basic.empty:
            return {"stock_count": 0, "row_count": 0, "source": "tushare", "latest_date": end}

        if stock_limit:
            basic = basic.head(max(1, int(stock_limit)))
        if test_mode:
            basic = basic.head(3)

        security_records = []
        total_rows = 0
        processed = 0

        for row in basic.itertuples(index=False):
            code = _ts_code_to_local(str(row.ts_code))
            if not _is_a_share(code):
                continue
            security_records.append(
                {
                    "code": code,
                    "name": getattr(row, "name", ""),
                    "list_date": getattr(row, "list_date", ""),
                    "delist_date": getattr(row, "delist_date", ""),
                    "industry": "",
                    "is_st": "ST" in str(getattr(row, "name", "")),
                    "source": "tushare",
                }
            )

            daily = pro.daily(ts_code=row.ts_code, start_date=start, end_date=end)
            if daily is None or daily.empty:
                continue

            export_daily = daily.rename(columns={"vol": "volume"}).copy()
            export_daily["code"] = code
            export_daily["turnover"] = None
            export_daily["adj_flag"] = "hfq"
            export_daily["source"] = "tushare"
            records = export_daily[
                [
                    "code",
                    "trade_date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "amount",
                    "pct_chg",
                    "turnover",
                    "adj_flag",
                    "source",
                ]
            ].to_dict(orient="records")
            total_rows += self.repository.upsert_daily_bars(records)
            processed += 1
            time.sleep(0.05)

        self.repository.upsert_security_master(security_records)
        self.repository.upsert_meta(
            {
                "last_daily_bar_sync": datetime.now().isoformat(timespec="seconds"),
                "daily_bar_latest_date": end,
                "daily_bar_source": "tushare",
            }
        )
        quality = self.quality_service.persist_audit()
        return {"stock_count": processed, "row_count": total_rows, "source": "tushare", "latest_date": end, "quality": quality}

class BenchmarkManagerLike(Protocol):
    def get_benchmark_daily_values(
        self,
        trading_dates: list[str],
        index_code: str = "sh.000300",
    ) -> list[float]:
        ...

    def get_market_index_frame(
        self,
        index_code: str = "sh.000300",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        ...


class BenchmarkRepositoryLike(Protocol):
    def query_index_bars(
        self,
        index_codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        ...

_DEFAULT_QUALITY_CACHE_TTL_SECONDS = 300


def _default_db_path() -> Path:
    return Path(os.environ.get("INVEST_DB_PATH", str(PROJECT_ROOT / "data" / "stock_history.db")))


class DataManager:
    """统一数据入口：优先 canonical 离线库，其次在线兜底；仅显式允许时才可回退 mock。"""

    def __init__(
        self,
        db_path: str | None = None,
        prefer_offline: bool = True,
        data_provider: Optional[DataProvider] = None,
        allow_mock_fallback: bool = False,
    ):
        self._provider = data_provider
        self._offline = TrainingDatasetBuilder(db_path=str(db_path) if db_path else str(_default_db_path()))
        self._quality_service = DataQualityService(repository=self._offline.repository)
        self._web = WebDatasetService(repository=self._offline.repository)
        self._capital_flow = CapitalFlowDatasetService(repository=self._offline.repository)
        self._events = EventDatasetService(repository=self._offline.repository)
        self._intraday = IntradayDatasetBuilder(repository=self._offline.repository)
        self._query = MarketQueryService(dataset_service=self._web)
        self._benchmark = BenchmarkDataService(repository=self._offline.repository)
        self._quality_audit_cache: dict[str, object] = {}
        self._online: Optional[EvolutionDataLoader] = None
        self._prefer_offline = prefer_offline
        self._runtime_data_policy = get_runtime_data_policy()
        self._gateway = MarketDataGateway(
            db_path=str(db_path) if db_path else str(_default_db_path()),
            runtime_policy=self._runtime_data_policy,
            ingestion_factory=DataIngestionService,
            online_loader_factory=EvolutionDataLoader,
        )
        self._allow_online_fallback = self._gateway.allow_online_fallback
        self._allow_capital_flow_sync = self._gateway.allow_capital_flow_sync
        self.allow_mock_fallback = bool(allow_mock_fallback)
        self.last_source: str = "unknown"
        self.last_diagnostics: dict[str, object] = {}
        self.last_resolution: dict[str, object] = {}

        if not self._offline.available:
            if self._allow_online_fallback:
                logger.info("离线数据库不可用，将使用在线数据源；仅显式 mock 时才使用模拟数据")
            else:
                logger.info("离线数据库不可用，且控制面已禁止运行时在线兜底；仅显式 mock 时才使用模拟数据")

    @property
    def requested_mode(self) -> str:
        if isinstance(self._provider, MockDataProvider):
            return "mock"
        if self._provider is not None:
            return "provider"
        if self.allow_mock_fallback:
            return "live_with_mock_fallback"
        return "live"

    def _record_resolution(
        self,
        *,
        source: str,
        offline_diagnostics: dict[str, object] | None = None,
        online_error: str = "",
        degraded: bool = False,
        degrade_reason: str = "",
    ) -> None:
        diagnostics = dict(offline_diagnostics or self.last_diagnostics or {})
        self.last_source = str(source)
        self.last_diagnostics = diagnostics
        self.last_resolution = {
            "requested_data_mode": self.requested_mode,
            "effective_data_mode": str(source),
            "source": str(source),
            "degraded": bool(degraded),
            "degrade_reason": str(degrade_reason or ""),
            "allow_mock_fallback": bool(self.allow_mock_fallback),
            "online_error": str(online_error or ""),
            "offline_diagnostics": diagnostics,
        }

    def _build_data_source_unavailable(
        self,
        *,
        cutoff_date: str,
        stock_count: int,
        min_history_days: int,
        offline_diagnostics: dict[str, object] | None = None,
        online_error: str = "",
    ) -> DataSourceUnavailableError:
        diagnostics = _dict_of_objects(offline_diagnostics or self.last_diagnostics or {})
        suggestions = _string_list(diagnostics.get("suggestions"))
        if not suggestions:
            suggestions = [
                "优先检查本地离线库覆盖范围，必要时先执行数据同步。",
                "如果只是演示或健康检查，请显式启用 mock / smoke 模式。",
            ]
        return DataSourceUnavailableError(
            "训练数据源不可用：离线库与在线兜底均未能返回可训练数据，且当前未显式启用 mock 模式。",
            cutoff_date=cutoff_date,
            stock_count=stock_count,
            min_history_days=min_history_days,
            requested_data_mode=self.requested_mode,
            available_sources={
                "offline": bool(self._offline.available),
                "online": bool(self._online is not None),
                "mock": bool(self.allow_mock_fallback or isinstance(self._provider, MockDataProvider)),
            },
            offline_diagnostics=diagnostics,
            online_error=online_error,
            suggestions=suggestions,
            allow_mock_fallback=self.allow_mock_fallback,
        )

    def _get_cached_quality_audit(self) -> dict[str, object]:
        now_ts = datetime.now().timestamp()
        cached_at = _float_value(self._quality_audit_cache.get("cached_at"), 0.0)
        payload = self._quality_audit_cache.get("payload")
        cached_payload = _dict_of_objects(payload)
        if cached_payload and (now_ts - cached_at) <= _DEFAULT_QUALITY_CACHE_TTL_SECONDS:
            return cached_payload
        fresh_payload = self._quality_service.audit()
        fresh_dict = _dict_of_objects(fresh_payload)
        self._quality_audit_cache = {"cached_at": now_ts, "payload": fresh_dict}
        return fresh_dict

    def random_cutoff_date(self, min_date: str = "20180101", max_date: str | None = None) -> str:
        if self._provider is not None:
            return self._provider.random_cutoff_date(min_date=min_date, max_date=max_date)

        if self._prefer_offline and self._offline.available:
            db_min, db_max = self._offline.get_available_date_range()
            if db_min and db_max:
                min_bound = max(normalize_date(min_date), normalize_date(db_min))
                if max_date:
                    max_bound = min(normalize_date(max_date), normalize_date(db_max))
                else:
                    latest_safe = datetime.strptime(normalize_date(db_max), "%Y%m%d") - timedelta(
                        days=max(getattr(config, "simulation_days", 30) * 2, 60)
                    )
                    max_bound = latest_safe.strftime("%Y%m%d")
                if min_bound < max_bound:
                    return _random_cutoff_date(min_bound, max_bound, config.min_history_days)

        return _random_cutoff_date(min_date, max_date, config.min_history_days)

    def check_training_readiness(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
    ) -> dict[str, object]:
        if self._provider is not None:
            diagnostics = self._provider.diagnose_training_data(cutoff_date, stock_count, min_history_days)
            self.last_diagnostics = diagnostics
            return diagnostics

        cutoff = normalize_date(cutoff_date)
        target_stock_count = max(1, int(stock_count))
        eligible_count = self._offline.repository.count_codes_with_history(
            cutoff_date=cutoff,
            min_history_days=min_history_days,
        )
        date_min, date_max = self._offline.get_available_date_range()
        index_min, index_max = self._offline.repository.get_index_available_date_range()
        stock_count_available = self._offline.get_stock_count()

        issues: list[str] = []
        suggestions: list[str] = []
        readiness = _training_readiness_contract(
            latest_date=date_max,
            cutoff_date=cutoff,
            eligible_count=eligible_count,
            has_kline_data=bool(date_max),
        )

        if stock_count_available <= 0:
            issues.append("security_master 为空")
            suggestions.append("先初始化股票主数据")
        if not date_max:
            issues.append("daily_bar 为空")
            suggestions.append("先下载历史日线")
        elif date_max < cutoff:
            issues.append(f"离线库最新日期 {date_max} 早于训练截断日 {cutoff}")
            suggestions.append("补齐最近日线，避免截断日超出覆盖范围")
        if eligible_count <= 0:
            issues.append(f"截至 {cutoff} 没有股票满足至少 {int(min_history_days)} 个交易日历史")
            suggestions.append("降低 min_history_days，或把 start 日期调早后重新补数")
        elif eligible_count < target_stock_count:
            issues.append(f"满足历史长度要求的股票只有 {eligible_count} 只，低于目标 {target_stock_count} 只")
            suggestions.append("扩大数据覆盖范围，或临时下调 max_stocks")
        if not index_max:
            suggestions.append("建议补齐指数日线，便于 benchmark 评估")

        diagnostics = {
            "cutoff_date": cutoff,
            "target_stock_count": target_stock_count,
            "min_history_days": int(min_history_days),
            "eligible_stock_count": eligible_count,
            "ready": bool(readiness["ready"]),
            "issues": issues,
            "suggestions": suggestions,
            "offline_available": self._offline.available,
            "stale_data": bool(readiness["stale_data"]),
            "requires_explicit_override": bool(readiness["requires_explicit_override"]),
            "severity": str(readiness["severity"]),
            "status": {
                "stock_count": stock_count_available,
                "latest_date": date_max or "",
                "index_latest_date": index_max or "",
            },
            "date_range": {"min": date_min, "max": date_max},
            "index_date_range": {"min": index_min, "max": index_max},
            "quality_checks": {},
            "diagnostic_mode": "training_lightweight",
        }
        self.last_diagnostics = diagnostics
        return diagnostics

    def diagnose_training_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
    ) -> dict[str, object]:
        if self._provider is not None:
            diagnostics = self._provider.diagnose_training_data(cutoff_date, stock_count, min_history_days)
            self.last_diagnostics = diagnostics
            return diagnostics

        cutoff = normalize_date(cutoff_date)
        quality = self._get_cached_quality_audit()
        status = _dict_of_objects(quality.get("status"))
        date_range = _dict_of_objects(quality.get("date_range"))
        eligible_count = self._offline.repository.count_codes_with_history(
            cutoff_date=cutoff,
            min_history_days=min_history_days,
        )
        target_stock_count = max(1, int(stock_count))
        issues: list[str] = []
        suggestions: list[str] = []

        stock_count_value = _int_value(status.get("stock_count"))
        kline_count = _int_value(status.get("kline_count"))
        financial_count = _int_value(status.get("financial_count"))
        index_kline_count = _int_value(status.get("index_kline_count"))
        date_range_max = str(date_range.get("max") or "")

        if stock_count_value <= 0:
            issues.append("security_master 为空")
            suggestions.append("先执行 python3 -m market_data --source baostock --start 20180101 初始化股票主数据")
        if kline_count <= 0:
            issues.append("daily_bar 为空")
            suggestions.append("先执行 python3 -m market_data --source baostock --start 20180101 下载历史日线")
        if kline_count > 0 and eligible_count <= 0:
            issues.append(f"截至 {cutoff} 没有股票满足至少 {int(min_history_days)} 个交易日历史")
            suggestions.append("降低 min_history_days，或把 start 日期调早后重新补数")
        if eligible_count > 0 and eligible_count < target_stock_count:
            issues.append(f"满足历史长度要求的股票只有 {eligible_count} 只，低于目标 {target_stock_count} 只")
            suggestions.append("扩大数据覆盖范围，或临时下调 max_stocks")
        if date_range_max and date_range_max < cutoff:
            issues.append(f"离线库最新日期 {date_range_max} 早于训练截断日 {cutoff}")
            suggestions.append("补齐最近日线，避免训练截断日超出离线库覆盖范围")

        readiness = _training_readiness_contract(
            latest_date=date_range_max,
            cutoff_date=cutoff,
            eligible_count=eligible_count,
            has_kline_data=bool(kline_count > 0),
        )
        if financial_count <= 0:
            suggestions.append("可选：执行 python3 -m market_data --source akshare --financials 补齐财务快照；如已配置 TUSHARE_TOKEN 也可使用 tushare")
        if index_kline_count <= 0:
            suggestions.append("先执行 python3 -m market_data --source baostock 补齐指数日线")

        diagnostics = {
            "cutoff_date": cutoff,
            "target_stock_count": target_stock_count,
            "min_history_days": int(min_history_days),
            "eligible_stock_count": eligible_count,
            "ready": bool(readiness["ready"]),
            "issues": issues,
            "suggestions": suggestions,
            "offline_available": self._offline.available,
            "stale_data": bool(readiness["stale_data"]),
            "requires_explicit_override": bool(readiness["requires_explicit_override"]),
            "severity": str(readiness["severity"]),
            "status": status,
            "date_range": date_range,
            "quality_checks": _dict_of_objects(quality.get("checks")),
        }
        self.last_diagnostics = diagnostics
        return diagnostics

    def _ensure_point_in_time_derivatives(
        self,
        *,
        cutoff_date: str,
        stock_count: int,
        min_history_days: int,
        include_future_days: int,
        include_capital_flow: bool = False,
    ) -> None:
        self._gateway.ensure_runtime_derivatives(
            repository=self._offline.repository,
            cutoff_date=cutoff_date,
            stock_count=stock_count,
            min_history_days=min_history_days,
            include_future_days=include_future_days,
            include_capital_flow=include_capital_flow,
        )

    def get_benchmark_daily_values(self, trading_dates: list[str], index_code: str = "sh.000300") -> list[float]:
        return self._benchmark.get_benchmark_daily_values(
            trading_dates=trading_dates,
            index_code=index_code,
        )

    def get_market_index_frame(self, index_code: str = "sh.000300", start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        if not self._offline.available:
            return pd.DataFrame()
        return self._benchmark.get_market_index_frame(
            index_code=index_code,
            start_date=start_date,
            end_date=end_date,
        )

    def load_stock_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
        include_future_days: int = 0,
        include_capital_flow: bool = False,
    ) -> Dict[str, pd.DataFrame]:
        offline_diagnostics: dict[str, object] = {}
        online_error = ""

        if self._provider is not None:
            source = "mock" if isinstance(self._provider, MockDataProvider) else "provider"
            data = self._provider.load_stock_data(
                cutoff_date=cutoff_date,
                stock_count=stock_count,
                min_history_days=min_history_days,
                include_future_days=include_future_days,
                include_capital_flow=include_capital_flow,
            )
            self._record_resolution(source=source)
            return data

        if self._prefer_offline and self._offline.available:
            offline_diagnostics = self.check_training_readiness(
                cutoff_date=cutoff_date,
                stock_count=stock_count,
                min_history_days=min_history_days,
            )
            if bool(offline_diagnostics.get("ready", False)):
                if include_capital_flow:
                    if not self._allow_capital_flow_sync:
                        logger.info("控制面已禁止运行时资金流外部同步；仅使用本地已有资金流数据")
                    self._ensure_point_in_time_derivatives(
                        cutoff_date=cutoff_date,
                        stock_count=stock_count,
                        min_history_days=min_history_days,
                        include_future_days=include_future_days,
                        include_capital_flow=(include_capital_flow and self._allow_capital_flow_sync),
                    )
                stock_data = self._offline.get_stocks(
                    cutoff_date=cutoff_date,
                    stock_count=stock_count,
                    min_history_days=min_history_days,
                    include_future_days=include_future_days,
                    include_capital_flow=include_capital_flow,
                )
                if stock_data:
                    self._record_resolution(source="offline", offline_diagnostics=offline_diagnostics)
                    return stock_data
                logger.warning(
                    "离线数据未命中: cutoff=%s eligible=%s target=%s issues=%s",
                    offline_diagnostics["cutoff_date"],
                    offline_diagnostics["eligible_stock_count"],
                    offline_diagnostics["target_stock_count"],
                    "; ".join(_string_list(offline_diagnostics.get("issues"))) or "none",
                )
            else:
                logger.warning(
                    "离线训练门禁未通过: cutoff=%s latest=%s ready=%s issues=%s",
                    offline_diagnostics.get("cutoff_date"),
                    _dict_of_objects(offline_diagnostics.get("status")).get("latest_date", ""),
                    offline_diagnostics.get("ready"),
                    "; ".join(_string_list(offline_diagnostics.get("issues"))) or "none",
                )
        else:
            try:
                offline_diagnostics = self.check_training_readiness(
                    cutoff_date=cutoff_date,
                    stock_count=stock_count,
                    min_history_days=min_history_days,
                )
            except Exception as exc:
                logger.warning("离线训练就绪诊断失败: cutoff=%s error=%s", cutoff_date, exc, exc_info=True)
                offline_diagnostics = _dict_of_objects({
                    "cutoff_date": normalize_date(cutoff_date),
                    "target_stock_count": max(1, int(stock_count)),
                    "min_history_days": int(min_history_days),
                    "ready": False,
                    "issues": ["offline_readiness_check_failed"],
                    "suggestions": ["检查离线库可读性与 schema 完整性后重试"],
                })

        if self._allow_online_fallback and self._online is None:
            self._online, online_error = self._gateway.create_online_loader()
        elif not self._allow_online_fallback:
            online_error = "disabled_by_control_plane"

        if self._allow_online_fallback and self._online is not None:
            try:
                effective_cutoff = normalize_date(cutoff_date)
                if include_future_days > 0:
                    cutoff_dt = datetime.strptime(effective_cutoff, "%Y%m%d")
                    effective_cutoff = (cutoff_dt + timedelta(days=include_future_days * 2)).strftime("%Y%m%d")
                data = self._online.load_all_data_before(effective_cutoff)
                stocks = data.get("stocks", {})
                if stocks:
                    self._record_resolution(
                        source="online",
                        offline_diagnostics=offline_diagnostics,
                        degraded=True,
                        degrade_reason="offline_unavailable_or_incomplete",
                    )
                    return dict(list(stocks.items())[:stock_count])
            except Exception as exc:
                online_error = str(exc)
                logger.warning("在线数据加载失败: %s", exc)

        if self.allow_mock_fallback:
            logger.warning("所有真实数据源不可用，显式 allow_mock_fallback 已开启，回退到模拟数据")
            data = generate_mock_stock_data(stock_count)
            self._record_resolution(
                source="mock",
                offline_diagnostics=offline_diagnostics,
                online_error=online_error,
                degraded=True,
                degrade_reason="explicit_mock_fallback",
            )
            return data

        error = self._build_data_source_unavailable(
            cutoff_date=cutoff_date,
            stock_count=stock_count,
            min_history_days=min_history_days,
            offline_diagnostics=offline_diagnostics,
            online_error=online_error,
        )
        self.last_source = "unavailable"
        self.last_diagnostics = dict(offline_diagnostics or {})
        self.last_resolution = {
            "requested_data_mode": self.requested_mode,
            "effective_data_mode": "unavailable",
            "source": "unavailable",
            "degraded": True,
            "degrade_reason": error.payload["error"],
            "allow_mock_fallback": bool(self.allow_mock_fallback),
            "online_error": online_error,
            "offline_diagnostics": dict(offline_diagnostics or {}),
        }
        raise error

    def get_status_summary(self, *, refresh: bool = False) -> dict[str, object]:
        return self._query.get_status_summary(refresh=refresh)

    def get_capital_flow_data(
        self,
        codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return self._query.get_capital_flow(codes=codes, start_date=start_date, end_date=end_date)

    def get_dragon_tiger_events(
        self,
        codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return self._query.get_dragon_tiger_events(codes=codes, start_date=start_date, end_date=end_date)

    def get_intraday_60m_data(
        self,
        codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return self._query.get_intraday_60m_bars(codes=codes, start_date=start_date, end_date=end_date)

    @property
    def offline_available(self) -> bool:
        return self._offline.available


def _cli_main():
    parser = argparse.ArgumentParser(description="投资进化系统 - 统一数据同步器")
    parser.add_argument("--stocks", type=int, default=200, help="股票数量")
    parser.add_argument("--start", type=str, default="20180101", help="开始日期 YYYYMMDD")
    parser.add_argument("--end", type=str, default=None, help="结束日期 YYYYMMDD")
    parser.add_argument("--token", type=str, default=None, help="Tushare Token")
    parser.add_argument("--offset", type=int, default=0, help="分批同步时跳过前 N 只股票")
    parser.add_argument("--test", action="store_true", help="测试模式（只下3只）")
    parser.add_argument("--source", choices=["baostock", "tushare", "akshare"], default="baostock", help="数据源")
    parser.add_argument("--financials", action="store_true", help="同步财务快照（支持 tushare / akshare）")
    parser.add_argument("--calendar", action="store_true", help="同步交易日历")
    parser.add_argument("--capital-flow", action="store_true", help="同步个股资金流")
    parser.add_argument("--dragon-tiger", action="store_true", help="同步龙虎榜")
    parser.add_argument("--intraday-60m", action="store_true", help="同步60分钟线")
    parser.add_argument("--status", action="store_true", help="输出当前离线库审计结果并退出")
    parser.add_argument("--cutoff", type=str, default=None, help="配合 --status 输出训练截断日诊断")
    parser.add_argument("--min-history-days", type=int, default=None, help="配合 --status 指定最小历史天数")
    args = parser.parse_args()

    if args.status:
        manager = DataManager()
        payload = DataQualityService(repository=manager._offline.repository).audit()
        if args.cutoff:
            payload["training_readiness"] = manager.diagnose_training_data(
                cutoff_date=args.cutoff,
                stock_count=args.stocks,
                min_history_days=args.min_history_days or config.min_history_days,
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    gateway = MarketDataGateway(tushare_token=args.token, ingestion_factory=DataIngestionService)
    payload: dict[str, object] = {}

    if args.calendar:
        payload["calendar"] = gateway.sync_calendar(
            source=args.source,
            start_date=args.start,
            end_date=args.end,
        )

    if args.capital_flow:
        payload["capital_flow"] = gateway.sync_capital_flow(
            source=args.source,
            stock_limit=args.stocks,
            offset=args.offset,
        )

    if args.dragon_tiger:
        payload["dragon_tiger"] = gateway.sync_dragon_tiger(
            source=args.source,
            start_date=args.start,
            end_date=args.end,
        )

    if getattr(args, "intraday_60m", False):
        payload["intraday_60m"] = gateway.sync_intraday_60m(
            source=args.source,
            start_date=args.start,
            end_date=args.end,
            stock_limit=args.stocks,
            offset=args.offset,
        )

    if args.financials:
        payload["financial"] = gateway.sync_financials(
            source=args.source,
            start_date=args.start,
            end_date=args.end,
            stock_limit=args.stocks,
            offset=args.offset,
            test_mode=args.test,
        )

    if payload:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(json.dumps(
        gateway.sync_default_source(
            source=args.source,
            start_date=args.start,
            end_date=args.end,
            stock_limit=args.stocks,
            test_mode=args.test,
        ),
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _cli_main()
