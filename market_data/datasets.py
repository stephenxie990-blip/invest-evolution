import logging
import random
from datetime import datetime, timedelta
from typing import Any, Dict, Sequence

import pandas as pd

from config import normalize_date
from .quality import DataQualityService
from .repository import MarketDataRepository

logger = logging.getLogger(__name__)

_NUMERIC_COLUMNS = ("open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover")
_CAPITAL_FLOW_COLUMNS = (
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
)


def _query_end_date(cutoff_date: str, include_future_days: int) -> str:
    cutoff = normalize_date(cutoff_date)
    if include_future_days <= 0:
        return cutoff
    cutoff_dt = datetime.strptime(cutoff, "%Y%m%d")
    return (cutoff_dt + timedelta(days=include_future_days * 2)).strftime("%Y%m%d")


def _query_start_date(cutoff_date: str, history_days: int) -> str:
    cutoff = normalize_date(cutoff_date)
    cutoff_dt = datetime.strptime(cutoff, "%Y%m%d")
    return (cutoff_dt - timedelta(days=history_days)).strftime("%Y%m%d")


def normalize_stock_frame(df: pd.DataFrame, code: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    result = df.copy()
    result["code"] = code
    result["trade_date"] = result["trade_date"].astype(str).map(normalize_date)
    for column in _NUMERIC_COLUMNS:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
        else:
            result[column] = pd.NA

    computed_pct = result["close"].pct_change().fillna(0) * 100
    if result["pct_chg"].isna().all():
        result["pct_chg"] = computed_pct
    else:
        result["pct_chg"] = result["pct_chg"].fillna(computed_pct)

    dt = pd.to_datetime(result["trade_date"], format="%Y%m%d", errors="coerce")
    result["date"] = dt.dt.strftime("%Y-%m-%d").fillna(result["trade_date"])
    ordered = ["date", "trade_date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover", "code"]
    extra = [column for column in result.columns if column not in ordered]
    return result.sort_values("trade_date").reset_index(drop=True)[ordered + extra]


def _attach_point_in_time_context(
    repository: MarketDataRepository,
    stock_frames: Dict[str, pd.DataFrame],
    cutoff_date: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    include_capital_flow: bool = False,
) -> Dict[str, pd.DataFrame]:
    if not stock_frames:
        return stock_frames

    codes = list(stock_frames.keys())
    securities = {row["code"]: row for row in repository.query_securities(codes)}
    financials = {row["code"]: row for row in repository.query_latest_financial_snapshots(codes, cutoff_date)}
    factor_df = repository.query_factor_snapshots(codes=codes, start_date=start_date, end_date=end_date)
    status_df = repository.query_security_status_daily(codes=codes, start_date=start_date, end_date=end_date)
    capital_flow_df = repository.query_capital_flow_daily(codes=codes, start_date=start_date, end_date=end_date) if include_capital_flow else pd.DataFrame()

    factor_map = {
        code: frame.drop(columns=["code"]).copy()
        for code, frame in factor_df.groupby("code")
    } if not factor_df.empty else {}
    status_map = {
        code: frame.drop(columns=["code"]).copy()
        for code, frame in status_df.groupby("code")
    } if not status_df.empty else {}
    capital_flow_map = {
        code: frame.drop(columns=["code"]).copy()
        for code, frame in capital_flow_df.groupby("code")
    } if not capital_flow_df.empty else {}

    enriched: Dict[str, pd.DataFrame] = {}
    for code, frame in stock_frames.items():
        result = frame.copy()
        meta = securities.get(code, {})
        for key in ("name", "industry", "list_date", "delist_date"):
            result[key] = meta.get(key)
        result["is_st_master"] = int(bool(meta.get("is_st", 0)))

        finance = financials.get(code, {})
        result["financial_report_date"] = finance.get("report_date")
        result["financial_publish_date"] = finance.get("publish_date")
        result["roe"] = finance.get("roe")
        result["net_profit"] = finance.get("net_profit")
        result["revenue"] = finance.get("revenue")
        result["total_assets"] = finance.get("total_assets")
        result["market_cap"] = finance.get("market_cap")

        if code in factor_map:
            result = result.merge(factor_map[code], on="trade_date", how="left")
        if code in status_map:
            result = result.merge(status_map[code], on="trade_date", how="left")
        if code in capital_flow_map:
            result = result.merge(capital_flow_map[code], on="trade_date", how="left", suffixes=("", "_capital_flow"))
        enriched[code] = result
    return enriched


class TrainingDatasetBuilder:
    """Read-side dataset builder for training and backtesting."""

    def __init__(self, repository: MarketDataRepository | None = None, db_path: str | None = None):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()

    @property
    def available(self) -> bool:
        return self.repository.has_daily_bars()

    def get_stocks(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
        include_future_days: int = 0,
        include_capital_flow: bool = False,
    ) -> Dict[str, pd.DataFrame]:
        codes = self.repository.select_codes_with_history(cutoff_date, min_history_days, stock_count)
        if not codes:
            return {}

        end_date = _query_end_date(cutoff_date, include_future_days)
        df = self.repository.query_daily_bars(codes=codes, end_date=end_date)
        stock_data: Dict[str, pd.DataFrame] = {}
        cutoff = normalize_date(cutoff_date)

        for code in codes:
            stock_df = normalize_stock_frame(df[df["code"] == code].copy(), code)
            if stock_df.empty:
                continue
            if int((stock_df["trade_date"] <= cutoff).sum()) >= max(1, int(min_history_days)):
                stock_data[code] = stock_df
        return _attach_point_in_time_context(
            self.repository,
            stock_data,
            cutoff_date,
            end_date=end_date,
            include_capital_flow=include_capital_flow,
        )

    def get_stock(
        self,
        code: str,
        cutoff_date: str | None = None,
        *,
        include_capital_flow: bool = False,
    ) -> pd.DataFrame | None:
        df = self.repository.get_stock(code, cutoff_date=cutoff_date)
        if df.empty:
            return None
        normalized_cutoff = cutoff_date or normalize_date(df["trade_date"].max())
        result = {code: normalize_stock_frame(df, code)}
        enriched = _attach_point_in_time_context(
            self.repository,
            result,
            normalized_cutoff,
            end_date=normalized_cutoff,
            include_capital_flow=include_capital_flow,
        )
        return enriched.get(code)

    def get_available_date_range(self) -> tuple[str | None, str | None]:
        return self.repository.get_available_date_range()

    def get_stock_count(self) -> int:
        return self.repository.get_stock_count()


class CapitalFlowDatasetService:
    """Read-only daily capital-flow service for optional factor enhancement."""

    def __init__(self, repository: MarketDataRepository | None = None, db_path: str | None = None):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()

    def get_capital_flow(self, codes: Sequence[str] | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        frame = self.repository.query_capital_flow_daily(codes=codes, start_date=start_date, end_date=end_date)
        if frame.empty:
            return frame
        for column in _CAPITAL_FLOW_COLUMNS:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame

    def get_capital_flow_by_code(self, code: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        return self.get_capital_flow(codes=[code], start_date=start_date, end_date=end_date)

    def attach_to_daily_frames(self, stock_frames: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        if not stock_frames:
            return stock_frames
        codes = list(stock_frames.keys())
        all_dates = [frame["trade_date"].astype(str) for frame in stock_frames.values() if not frame.empty]
        if not all_dates:
            return stock_frames
        start_date = min(series.min() for series in all_dates)
        end_date = max(series.max() for series in all_dates)
        return _attach_point_in_time_context(
            self.repository,
            stock_frames,
            cutoff_date=end_date,
            start_date=start_date,
            end_date=end_date,
            include_capital_flow=True,
        )


class EventDatasetService:
    """Read-only event dataset service for sparse event tables such as 龙虎榜."""

    def __init__(self, repository: MarketDataRepository | None = None, db_path: str | None = None):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()

    def get_dragon_tiger_events(self, codes: Sequence[str] | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        frame = self.repository.query_dragon_tiger_list(codes=codes, start_date=start_date, end_date=end_date)
        if frame.empty:
            return frame
        numeric_columns = [
            "close",
            "pct_chg",
            "net_buy",
            "buy_amount",
            "sell_amount",
            "turnover_amount",
            "market_turnover_amount",
            "net_buy_ratio",
            "turnover_ratio",
            "turnover_rate",
            "float_market_cap",
            "next_day_return",
            "next_2day_return",
            "next_5day_return",
            "next_10day_return",
        ]
        for column in numeric_columns:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame

    def get_event_summary(self, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        frame = self.get_dragon_tiger_events(start_date=start_date, end_date=end_date)
        return {
            "row_count": int(len(frame)),
            "stock_count": int(frame["code"].nunique()) if not frame.empty else 0,
            "latest_date": str(frame["trade_date"].max()) if not frame.empty else "",
        }


class IntradayDatasetBuilder:
    """Read-only intraday dataset builder for 60-minute bars."""

    def __init__(self, repository: MarketDataRepository | None = None, db_path: str | None = None):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()

    def get_bars(self, codes: Sequence[str] | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        frame = self.repository.query_intraday_bars_60m(codes=codes, start_date=start_date, end_date=end_date)
        if frame.empty:
            return frame
        for column in ("open", "high", "low", "close", "volume", "amount"):
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame

    def get_stock_bars(self, code: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        return self.get_bars(codes=[code], start_date=start_date, end_date=end_date)

    def get_bar_summary(self, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        frame = self.get_bars(start_date=start_date, end_date=end_date)
        return {
            "row_count": int(len(frame)),
            "stock_count": int(frame["code"].nunique()) if not frame.empty else 0,
            "latest_date": str(frame["trade_date"].max()) if not frame.empty else "",
        }


class WebDatasetService:
    """Read-only status/query service for web endpoints."""

    def __init__(self, repository: MarketDataRepository | None = None, db_path: str | None = None):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()
        self.capital_flow = CapitalFlowDatasetService(repository=self.repository)
        self.events = EventDatasetService(repository=self.repository)
        self.intraday = IntradayDatasetBuilder(repository=self.repository)

    def get_status_summary(self, *, refresh: bool = False) -> dict[str, Any]:
        summary = self.repository.get_status_summary(use_snapshot=not refresh)
        quality = DataQualityService(repository=self.repository).audit(use_snapshot=not refresh, force_refresh=refresh)
        summary["quality"] = {
            "healthy": quality["healthy"],
            "health_status": quality["health_status"],
            "issues": quality["issues"],
            "date_range": quality["date_range"],
            "meta": quality["meta"],
            "detail_mode": "slow" if refresh else "fast",
        }
        summary["detail_mode"] = "slow" if refresh else "fast"
        return summary

    def get_capital_flow(self, codes: Sequence[str] | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        return self.capital_flow.get_capital_flow(codes=codes, start_date=start_date, end_date=end_date)

    def get_dragon_tiger_events(self, codes: Sequence[str] | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        return self.events.get_dragon_tiger_events(codes=codes, start_date=start_date, end_date=end_date)

    def get_intraday_60m_bars(self, codes: Sequence[str] | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        return self.intraday.get_bars(codes=codes, start_date=start_date, end_date=end_date)


class T0DatasetBuilder:
    """T0-aware dataset builder backed by the canonical repository."""

    def __init__(self, repository: MarketDataRepository | None = None, db_path: str | None = None):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()

    def get_pool_at_date(self, cutoff_date: str) -> list[str]:
        return self.repository.get_security_pool_at_date(cutoff_date)

    def get_survived_stocks(self, cutoff_date: str, stocks: Sequence[str]) -> dict[str, bool]:
        return self.repository.get_survival_flags(cutoff_date, stocks)

    def load_data_at_t0(
        self,
        cutoff_date: str,
        max_stocks: int = 500,
        history_days: int = 800,
        future_days: int = 90,
        include_capital_flow: bool = False,
    ) -> dict[str, Any]:
        pool = self.get_pool_at_date(cutoff_date)
        if max_stocks and len(pool) > max_stocks:
            pool = random.sample(pool, max_stocks)

        if not pool:
            return {"cutoff_date": normalize_date(cutoff_date), "stocks": {}, "survived": {}}

        start_date = _query_start_date(cutoff_date, history_days)
        end_date = _query_end_date(cutoff_date, future_days)
        df = self.repository.query_daily_bars(codes=pool, start_date=start_date, end_date=end_date)
        stock_data: Dict[str, pd.DataFrame] = {}
        for code in pool:
            stock_df = normalize_stock_frame(df[df["code"] == code].copy(), code)
            if len(stock_df) > 100:
                stock_data[code] = stock_df

        stock_data = _attach_point_in_time_context(
            self.repository,
            stock_data,
            cutoff_date,
            start_date=start_date,
            end_date=end_date,
            include_capital_flow=include_capital_flow,
        )
        survived = self.get_survived_stocks(cutoff_date, list(stock_data.keys()))
        return {"cutoff_date": normalize_date(cutoff_date), "stocks": stock_data, "survived": survived}
