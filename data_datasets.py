import logging
import random
from datetime import datetime, timedelta
from typing import Any, Dict, Sequence

import pandas as pd

from config import normalize_date
from data_repository import MarketDataRepository

logger = logging.getLogger(__name__)

_NUMERIC_COLUMNS = ("open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover")


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
    return result.sort_values("trade_date").reset_index(drop=True)[ordered]


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
    ) -> Dict[str, pd.DataFrame]:
        codes = self.repository.select_codes_with_history(cutoff_date, min_history_days, stock_count)
        if not codes:
            return {}

        df = self.repository.query_daily_bars(codes=codes, end_date=_query_end_date(cutoff_date, include_future_days))
        stock_data: Dict[str, pd.DataFrame] = {}
        cutoff = normalize_date(cutoff_date)

        for code in codes:
            stock_df = normalize_stock_frame(df[df["code"] == code].copy(), code)
            if stock_df.empty:
                continue
            if int((stock_df["trade_date"] <= cutoff).sum()) >= max(1, int(min_history_days)):
                stock_data[code] = stock_df
        return stock_data

    def get_stock(self, code: str, cutoff_date: str | None = None) -> pd.DataFrame | None:
        df = self.repository.get_stock(code, cutoff_date=cutoff_date)
        if df.empty:
            return None
        return normalize_stock_frame(df, code)

    def get_available_date_range(self) -> tuple[str | None, str | None]:
        return self.repository.get_available_date_range()

    def get_stock_count(self) -> int:
        return self.repository.get_stock_count()


class WebDatasetService:
    """Read-only status/query service for web endpoints."""

    def __init__(self, repository: MarketDataRepository | None = None, db_path: str | None = None):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()

    def get_status_summary(self) -> dict[str, Any]:
        return self.repository.get_status_summary()


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
    ) -> dict[str, Any]:
        pool = self.get_pool_at_date(cutoff_date)
        if max_stocks and len(pool) > max_stocks:
            pool = random.sample(pool, max_stocks)

        if not pool:
            return {"cutoff_date": normalize_date(cutoff_date), "stocks": {}, "survived": {}}

        df = self.repository.query_daily_bars(
            codes=pool,
            start_date=_query_start_date(cutoff_date, history_days),
            end_date=_query_end_date(cutoff_date, future_days),
        )
        stock_data: Dict[str, pd.DataFrame] = {}
        for code in pool:
            stock_df = normalize_stock_frame(df[df["code"] == code].copy(), code)
            if len(stock_df) > 100:
                stock_data[code] = stock_df

        survived = self.get_survived_stocks(cutoff_date, list(stock_data.keys()))
        return {"cutoff_date": normalize_date(cutoff_date), "stocks": stock_data, "survived": survived}
