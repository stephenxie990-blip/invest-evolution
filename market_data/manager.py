"""投资进化系统 - 干净的数据主入口。"""

import argparse
import logging
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Protocol

import numpy as np
import pandas as pd

from config import PROJECT_ROOT, config, normalize_date
from .datasets import T0DatasetBuilder, TrainingDatasetBuilder
from .ingestion import DataIngestionService

logger = logging.getLogger(__name__)

DEFAULT_STOCK_POOL = [
    "sh.600519",
    "sh.000858",
    "sh.601318",
    "sh.600036",
    "sh.600900",
    "sz.000333",
    "sz.002594",
    "sh.600276",
    "sz.000651",
    "sh.601888",
    "sz.300750",
    "sz.002475",
    "sh.600030",
    "sh.601012",
    "sz.000002",
    "sh.600016",
    "sh.601166",
    "sh.601398",
    "sh.601857",
    "sz.000001",
]


def _default_db_path() -> Path:
    return Path(os.environ.get("INVEST_DB_PATH", str(PROJECT_ROOT / "data" / "stock_history.db")))


def _random_cutoff_date(
    min_date: str = "20180101",
    max_date: str | None = None,
    min_history_days: int = 730,
) -> str:
    if max_date is None:
        max_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    min_dt = datetime.strptime(normalize_date(min_date), "%Y%m%d")
    max_dt = datetime.strptime(normalize_date(max_date), "%Y%m%d")
    earliest_valid = min_dt + timedelta(days=min_history_days)

    if earliest_valid >= max_dt:
        logger.warning("历史数据不足，使用最小日期: %s", min_date)
        return normalize_date(min_date)

    random_days = random.randint(0, (max_dt - earliest_valid).days)
    return (earliest_valid + timedelta(days=random_days)).strftime("%Y%m%d")


def generate_mock_stock_data(
    stock_count: int = 50,
    days: int = 300,
    start_date: str = "20230101",
) -> Dict[str, pd.DataFrame]:
    dates = pd.date_range(start=start_date, periods=days, freq="B")
    trade_dates = dates.strftime("%Y%m%d").tolist()
    stock_data = {}

    for i in range(stock_count):
        code = f"sh.{600000 + i}"
        np.random.seed(42 + i)
        trend = np.random.choice([-0.001, 0, 0.001])
        close = 10 + np.cumsum(np.random.randn(days) * 0.3 + trend)
        close = np.maximum(close, 1)

        df = pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "trade_date": trade_dates,
                "open": close * (1 + np.random.randn(days) * 0.003),
                "high": close * (1 + np.abs(np.random.randn(days)) * 0.01),
                "low": close * (1 - np.abs(np.random.randn(days)) * 0.01),
                "close": close,
                "volume": np.random.randint(100_000, 50_000_000, days).astype(float),
                "amount": np.random.randint(100_000_000, 5_000_000_000, days).astype(float),
                "pct_chg": pd.Series(close).pct_change().fillna(0) * 100,
                "turnover": np.random.uniform(1, 10, days),
                "code": code,
            }
        )
        stock_data[code] = df

    logger.info("生成了 %s 只模拟股票数据", len(stock_data))
    return stock_data


class DataProvider(Protocol):
    def random_cutoff_date(self, min_date: str = "20180101", max_date: str | None = None) -> str:
        ...

    def load_stock_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
        include_future_days: int = 0,
    ) -> Dict[str, pd.DataFrame]:
        ...


class MockDataProvider:
    def __init__(
        self,
        stock_count: int = 30,
        days: int = 1500,
        start_date: str = "20200101",
        seed_cutoff_min: int = 250,
        seed_cutoff_tail: int = 60,
    ):
        self.data = generate_mock_stock_data(stock_count=stock_count, days=days, start_date=start_date)
        dates = []
        for df in self.data.values():
            dates.extend(df["trade_date"].tolist())
        self._dates = sorted(set(dates))
        self._seed_cutoff_min = max(10, int(seed_cutoff_min))
        self._seed_cutoff_tail = max(10, int(seed_cutoff_tail))

    def random_cutoff_date(self, min_date: str = "20180101", max_date: str | None = None) -> str:
        del min_date, max_date
        if len(self._dates) < (self._seed_cutoff_min + self._seed_cutoff_tail + 1):
            return self._dates[-1] if self._dates else "20231201"
        return random.choice(self._dates[self._seed_cutoff_min : -self._seed_cutoff_tail])

    def load_stock_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
        include_future_days: int = 0,
    ) -> Dict[str, pd.DataFrame]:
        del include_future_days
        selected: Dict[str, pd.DataFrame] = {}
        cutoff = normalize_date(cutoff_date)
        for code in list(self.data.keys())[: max(1, int(stock_count))]:
            df = self.data[code]
            if int((df["trade_date"] <= cutoff).sum()) >= max(1, int(min_history_days)):
                selected[code] = df.copy()
        return selected


class EvolutionDataLoader:
    """在线数据兜底，仅用于离线库不可用时的临时抓取。"""

    def __init__(self, data_source: str | None = None, stock_pool: list[str] | None = None):
        self.data_source = data_source or config.data_source
        self.stock_pool = stock_pool or DEFAULT_STOCK_POOL

    def random_cutoff_date(
        self,
        min_date: str = "20180101",
        max_date: str | None = None,
        min_history_days: int = 730,
    ) -> str:
        return _random_cutoff_date(min_date, max_date, min_history_days)

    def load_all_data_before(self, cutoff_date: str) -> dict:
        if self.data_source != "baostock":
            raise RuntimeError(f"在线数据源 {self.data_source} 暂未统一接入")

        import baostock as bs

        cutoff = normalize_date(cutoff_date)
        start_date = (datetime.strptime(cutoff, "%Y%m%d") - timedelta(days=900)).strftime("%Y-%m-%d")
        end_date = f"{cutoff[:4]}-{cutoff[4:6]}-{cutoff[6:8]}"

        login = bs.login()
        if getattr(login, "error_code", "0") != "0":
            raise RuntimeError(f"Baostock 登录失败: {getattr(login, 'error_msg', '')}")

        stock_data: dict[str, pd.DataFrame] = {}
        try:
            for code in self.stock_pool:
                rs = bs.query_history_k_data_plus(
                    code,
                    "date,code,open,high,low,close,volume,amount,pctChg,turn",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="2",
                )
                if getattr(rs, "error_code", "0") != "0":
                    continue

                rows = []
                while rs.next():
                    rows.append(dict(zip(rs.fields, rs.get_row_data())))
                if not rows:
                    continue

                df = pd.DataFrame(rows)
                df["trade_date"] = df["date"].map(normalize_date)
                df["pct_chg"] = pd.to_numeric(df.get("pctChg"), errors="coerce")
                df["turnover"] = pd.to_numeric(df.get("turn"), errors="coerce")
                for column in ("open", "high", "low", "close", "volume", "amount"):
                    df[column] = pd.to_numeric(df[column], errors="coerce")
                stock_data[code] = df[["date", "trade_date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover", "code"]]
        finally:
            bs.logout()

        return {"cutoff_date": cutoff, "stocks": stock_data}


class DataManager:
    """统一数据入口：优先 canonical 离线库，其次在线兜底，最后 mock。"""

    def __init__(
        self,
        db_path: str | None = None,
        prefer_offline: bool = True,
        data_provider: Optional[DataProvider] = None,
    ):
        self._provider = data_provider
        self._offline = TrainingDatasetBuilder(db_path=str(db_path) if db_path else str(_default_db_path()))
        self._online: Optional[EvolutionDataLoader] = None
        self._prefer_offline = prefer_offline

        if not self._offline.available:
            logger.info("离线数据库不可用，将使用在线数据源或模拟数据")

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

    def load_stock_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
        include_future_days: int = 0,
    ) -> Dict[str, pd.DataFrame]:
        if self._provider is not None:
            return self._provider.load_stock_data(
                cutoff_date=cutoff_date,
                stock_count=stock_count,
                min_history_days=min_history_days,
                include_future_days=include_future_days,
            )

        if self._prefer_offline and self._offline.available:
            stock_data = self._offline.get_stocks(
                cutoff_date=cutoff_date,
                stock_count=stock_count,
                min_history_days=min_history_days,
                include_future_days=include_future_days,
            )
            if stock_data:
                return stock_data

        if self._online is None:
            try:
                self._online = EvolutionDataLoader()
            except Exception as exc:
                logger.warning("在线加载器初始化失败: %s", exc)
                return generate_mock_stock_data(stock_count)

        try:
            effective_cutoff = normalize_date(cutoff_date)
            if include_future_days > 0:
                cutoff_dt = datetime.strptime(effective_cutoff, "%Y%m%d")
                effective_cutoff = (cutoff_dt + timedelta(days=include_future_days * 2)).strftime("%Y%m%d")
            data = self._online.load_all_data_before(effective_cutoff)
            stocks = data.get("stocks", {})
            if stocks:
                return dict(list(stocks.items())[:stock_count])
        except Exception as exc:
            logger.warning("在线数据加载失败: %s", exc)

        logger.warning("所有数据源不可用，使用模拟数据")
        return generate_mock_stock_data(stock_count)

    @property
    def offline_available(self) -> bool:
        return self._offline.available


def _cli_main():
    parser = argparse.ArgumentParser(description="投资进化系统 - 统一数据同步器")
    parser.add_argument("--stocks", type=int, default=200, help="股票数量")
    parser.add_argument("--start", type=str, default="20180101", help="开始日期 YYYYMMDD")
    parser.add_argument("--end", type=str, default=None, help="结束日期 YYYYMMDD")
    parser.add_argument("--token", type=str, default=None, help="Tushare Token")
    parser.add_argument("--test", action="store_true", help="测试模式（只下3只）")
    parser.add_argument("--source", choices=["baostock", "tushare"], default="baostock", help="数据源")
    args = parser.parse_args()

    service = DataIngestionService(tushare_token=args.token)
    if args.source == "baostock":
        service.sync_security_master()
        service.sync_daily_bars(start_date=args.start, end_date=args.end)
    else:
        service.sync_daily_bars_from_tushare(
            start_date=args.start,
            end_date=args.end,
            stock_limit=args.stocks,
            test_mode=args.test,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _cli_main()
