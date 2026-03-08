import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Sequence

from config import config, normalize_date
from .quality import DataQualityService
from .repository import MarketDataRepository

logger = logging.getLogger(__name__)


def _format_bs_date(value: str) -> str:
    normalized = normalize_date(value)
    return f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:8]}"


def _is_a_share(code: str) -> bool:
    return code.startswith("sh.6") or code.startswith("sz.00") or code.startswith("sz.30")


def _ts_code_to_local(ts_code: str) -> str:
    symbol, market = ts_code.split(".")
    return f"{market.lower()}.{symbol}"


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
    latest = repository.get_status_summary().get("latest_date", "")
    return normalize_date(latest or datetime.now().strftime("%Y%m%d"))


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

                records: list[dict[str, Any]] = []
                while rs.next():
                    row = dict(zip(rs.fields, rs.get_row_data()))
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

                records: list[dict[str, Any]] = []
                while rs.next():
                    row = dict(zip(rs.fields, rs.get_row_data()))
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
        quality = self.quality_service.persist_audit()
        return {
            "index_count": synced_codes,
            "row_count": total_rows,
            "source": "baostock",
            "latest_date": end,
            "quality": quality,
        }

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
