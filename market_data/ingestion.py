import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Protocol, Sequence, cast

import numpy as np
import pandas as pd
import requests

from config import config, normalize_date
from .quality import DataQualityService
from .repository import MarketDataRepository

logger = logging.getLogger(__name__)


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
        for _, row in frame.iterrows():
            report_date = normalize_date(row.get("end_date", ""))
            if not report_date:
                continue
            record = merged.setdefault(report_date, {"report_date": report_date})
            ann_date = normalize_date(row.get("ann_date", ""))
            if ann_date and not record.get("publish_date"):
                record["publish_date"] = ann_date
            if "roe" in row and _safe_float(row.get("roe")) is not None:
                record["roe"] = row.get("roe")
            if "n_income" in row and _safe_float(row.get("n_income")) is not None:
                record["net_profit"] = row.get("n_income")
            if "total_revenue" in row and _safe_float(row.get("total_revenue")) is not None:
                record["revenue"] = row.get("total_revenue")
            if "total_assets" in row and _safe_float(row.get("total_assets")) is not None:
                record["total_assets"] = row.get("total_assets")
    return merged


def _normalize_loose_date(value: Any) -> str:
    if value in (None, "", "None"):
        return ""
    text = str(value).strip()
    try:
        return normalize_date(text)
    except Exception:
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

    for _, row in balance_df.iterrows():
        report_date = _normalize_loose_date(row.get("REPORT_DATE") or row.get("报告日"))
        if not report_date:
            continue
        record = merged.setdefault(report_date, {"report_date": report_date})
        publish_date = _normalize_loose_date(row.get("NOTICE_DATE") or row.get("公告日期"))
        if publish_date and not record.get("publish_date"):
            record["publish_date"] = publish_date
        total_assets = _safe_float(row.get("TOTAL_ASSETS") if "TOTAL_ASSETS" in row else row.get("资产总计"))
        if total_assets is not None:
            record["total_assets"] = total_assets
        share_capital = _safe_float(row.get("SHARE_CAPITAL") if "SHARE_CAPITAL" in row else row.get("总股本"))
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


def _fetch_capital_flow_history_with_session(session: requests.Session, code: str, timeout: float = 10.0) -> pd.DataFrame:
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
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - 真实网络异常兜底
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
        return {str(row["code"]): _safe_float(row.get("close")) for _, row in frame.iterrows()}

    def sync_security_master(self) -> dict[str, Any]:
        import baostock as bs

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
        import baostock as bs

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
        import baostock as bs

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
        import akshare as ak

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
        sec_map = {row["code"]: row for row in securities}
        records: list[dict[str, Any]] = []
        for _, row in price_df.iterrows():
            code = str(row["code"])
            trade_date = normalize_date(row["trade_date"])
            meta = sec_map.get(code, {})
            pct_chg = _safe_float(row.get("pct_chg")) or 0.0
            list_date = normalize_date(meta.get("list_date", ""))
            is_st = int(bool(meta.get("is_st", 0)))
            limit_pct = 4.8 if is_st else (19.5 if code.startswith(("sz.300", "sh.688")) else 9.5)
            is_new_stock_window = 0
            if list_date:
                try:
                    is_new_stock_window = int((datetime.strptime(trade_date, "%Y%m%d") - datetime.strptime(list_date, "%Y%m%d")).days <= 90)
                except Exception:
                    is_new_stock_window = 0
            records.append(
                {
                    "code": code,
                    "trade_date": trade_date,
                    "is_st": is_st,
                    "is_new_stock_window": is_new_stock_window,
                    "is_limit_up": int(pct_chg >= limit_pct),
                    "is_limit_down": int(pct_chg <= -limit_pct),
                    "source": "derived",
                }
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
            for _, row in sub.iterrows():
                records.append(
                    {
                        "code": code,
                        "trade_date": row["trade_date"],
                        "ma5": row.get("ma5"),
                        "ma10": row.get("ma10"),
                        "ma20": row.get("ma20"),
                        "ma60": row.get("ma60"),
                        "momentum20": row.get("momentum20"),
                        "momentum60": row.get("momentum60"),
                        "volatility20": row.get("volatility20"),
                        "volume_ratio": row.get("volume_ratio"),
                        "turnover_mean20": row.get("turnover_mean20"),
                        "drawdown60": row.get("drawdown60"),
                        "relative_strength_hs300": row.get("relative_strength_hs300"),
                        "breakout20": int(row.get("breakout20", 0) or 0),
                        "source": "derived",
                    }
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
        import akshare as ak

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
            except Exception as exc:  # pragma: no cover
                logger.warning("AKShare 批量财报下载失败: %s %s", report_date, exc)
                continue
            if frame is None or frame.empty or "股票代码" not in frame.columns:
                continue

            records: list[dict[str, Any]] = []
            for _, row in frame.iterrows():
                local_code = _simple_symbol_to_local(str(row.get("股票代码") or ""))
                if not local_code or local_code not in target_set:
                    continue
                publish_date = _normalize_loose_date(row.get("最新公告日期"))
                industry = str(row.get("所处行业") or "").strip()
                if industry:
                    industry_updates[local_code] = industry
                records.append(
                    {
                        "code": local_code,
                        "report_date": report_date,
                        "publish_date": publish_date,
                        "roe": row.get("净资产收益率"),
                        "net_profit": row.get("净利润-净利润"),
                        "revenue": row.get("营业总收入-营业总收入"),
                        "total_assets": None,
                        "market_cap": None,
                        "source": "akshare",
                    }
                )
                touched_codes.add(local_code)
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
        import akshare as ak

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
            except Exception as exc:  # pragma: no cover - 网络异常兜底
                logger.warning("AKShare 财务摘要下载失败: %s %s", code, exc)
            try:
                balance_df = _akshare_call(ak.stock_balance_sheet_by_report_em, symbol=em_symbol)
            except Exception as exc:  # pragma: no cover - 网络异常兜底
                if str(row.get("delist_date") or "").strip() and hasattr(ak, "stock_balance_sheet_by_report_delisted_em"):
                    try:
                        balance_df = _akshare_call(ak.stock_balance_sheet_by_report_delisted_em, symbol=em_symbol)
                    except Exception as inner_exc:
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
        session = requests.Session()
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
                except Exception as exc:  # pragma: no cover
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
            for _, flow_row in frame.iterrows():
                records.append(
                    {
                        "code": code,
                        "trade_date": flow_row.get("日期"),
                        "close": flow_row.get("收盘价"),
                        "pct_chg": flow_row.get("涨跌幅"),
                        "main_net_inflow": flow_row.get("主力净流入-净额"),
                        "main_net_inflow_ratio": flow_row.get("主力净流入-净占比"),
                        "super_large_net_inflow": flow_row.get("超大单净流入-净额"),
                        "super_large_net_inflow_ratio": flow_row.get("超大单净流入-净占比"),
                        "large_net_inflow": flow_row.get("大单净流入-净额"),
                        "large_net_inflow_ratio": flow_row.get("大单净流入-净占比"),
                        "medium_net_inflow": flow_row.get("中单净流入-净额"),
                        "medium_net_inflow_ratio": flow_row.get("中单净流入-净占比"),
                        "small_net_inflow": flow_row.get("小单净流入-净额"),
                        "small_net_inflow_ratio": flow_row.get("小单净流入-净占比"),
                        "source": "akshare",
                    }
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
        import akshare as ak

        start = normalize_date(start_date)
        end = normalize_date(end_date or datetime.now().strftime("%Y%m%d"))
        frame = _akshare_call(ak.stock_lhb_detail_em, start_date=start, end_date=end)
        if frame is None or frame.empty:
            return {"row_count": 0, "source": "akshare", "latest_date": end}
        records: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            local_code = _simple_symbol_to_local(str(row.get("代码") or ""))
            if not local_code:
                continue
            records.append(
                {
                    "code": local_code,
                    "trade_date": row.get("上榜日"),
                    "name": row.get("名称"),
                    "interpretation": row.get("解读"),
                    "close": row.get("收盘价"),
                    "pct_chg": row.get("涨跌幅"),
                    "net_buy": row.get("龙虎榜净买额"),
                    "buy_amount": row.get("龙虎榜买入额"),
                    "sell_amount": row.get("龙虎榜卖出额"),
                    "turnover_amount": row.get("龙虎榜成交额"),
                    "market_turnover_amount": row.get("市场总成交额"),
                    "net_buy_ratio": row.get("净买额占总成交比"),
                    "turnover_ratio": row.get("成交额占总成交比"),
                    "turnover_rate": row.get("换手率"),
                    "float_market_cap": row.get("流通市值"),
                    "reason": row.get("上榜原因"),
                    "next_day_return": row.get("上榜后1日"),
                    "next_2day_return": row.get("上榜后2日"),
                    "next_5day_return": row.get("上榜后5日"),
                    "next_10day_return": row.get("上榜后10日"),
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

        import tushare as ts

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
            market_caps = {str(row["ts_code"]): _safe_float(row.get("total_mv")) for _, row in daily_basic.iterrows()}

        total_rows = 0
        processed = 0
        for _, row in basic.iterrows():
            ts_code = str(row["ts_code"])
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

        import tushare as ts

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

        for _, row in basic.iterrows():
            code = _ts_code_to_local(str(row["ts_code"]))
            if not _is_a_share(code):
                continue
            security_records.append(
                {
                    "code": code,
                    "name": row.get("name", ""),
                    "list_date": row.get("list_date", ""),
                    "delist_date": row.get("delist_date", ""),
                    "industry": "",
                    "is_st": "ST" in str(row.get("name", "")),
                    "source": "tushare",
                }
            )

            daily = pro.daily(ts_code=row["ts_code"], start_date=start, end_date=end)
            if daily is None or daily.empty:
                continue

            records = []
            for _, daily_row in daily.iterrows():
                records.append(
                    {
                        "code": code,
                        "trade_date": daily_row.get("trade_date", ""),
                        "open": daily_row.get("open"),
                        "high": daily_row.get("high"),
                        "low": daily_row.get("low"),
                        "close": daily_row.get("close"),
                        "volume": daily_row.get("vol"),
                        "amount": daily_row.get("amount"),
                        "pct_chg": daily_row.get("pct_chg"),
                        "turnover": None,
                        "adj_flag": "hfq",
                        "source": "tushare",
                    }
                )
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
