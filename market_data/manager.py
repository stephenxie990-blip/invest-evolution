"""投资进化系统 - 干净的数据主入口。"""

import argparse
import json
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
from .quality import DataQualityService

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

    def diagnose_training_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
    ) -> dict[str, object]:
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

    def diagnose_training_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
    ) -> dict[str, object]:
        cutoff = normalize_date(cutoff_date)
        eligible_count = 0
        for df in self.data.values():
            if int((df["trade_date"] <= cutoff).sum()) >= max(1, int(min_history_days)):
                eligible_count += 1
        dates = self._dates or [None]
        return {
            "cutoff_date": cutoff,
            "target_stock_count": max(1, int(stock_count)),
            "min_history_days": int(min_history_days),
            "eligible_stock_count": eligible_count,
            "ready": eligible_count > 0,
            "issues": [],
            "suggestions": [],
            "offline_available": False,
            "status": {"stock_count": len(self.data), "kline_count": sum(len(df) for df in self.data.values())},
            "date_range": {"min": dates[0], "max": dates[-1]},
            "quality_checks": {},
        }

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
                stock_data[code] = df[
                    ["date", "trade_date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover", "code"]
                ]
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
        self.last_source: str = "unknown"
        self.last_diagnostics: dict[str, object] = {}

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
        quality = DataQualityService(repository=self._offline.repository).audit()
        status = quality["status"]
        date_range = quality["date_range"]
        eligible_count = self._offline.repository.count_codes_with_history(
            cutoff_date=cutoff,
            min_history_days=min_history_days,
        )
        target_stock_count = max(1, int(stock_count))
        issues: list[str] = []
        suggestions: list[str] = []

        if status["stock_count"] <= 0:
            issues.append("security_master 为空")
            suggestions.append("先执行 python3 -m market_data --source baostock --start 20180101 初始化股票主数据")
        if status["kline_count"] <= 0:
            issues.append("daily_bar 为空")
            suggestions.append("先执行 python3 -m market_data --source baostock --start 20180101 下载历史日线")
        if status["kline_count"] > 0 and eligible_count <= 0:
            issues.append(f"截至 {cutoff} 没有股票满足至少 {int(min_history_days)} 个交易日历史")
            suggestions.append("降低 min_history_days，或把 start 日期调早后重新补数")
        if eligible_count > 0 and eligible_count < target_stock_count:
            issues.append(f"满足历史长度要求的股票只有 {eligible_count} 只，低于目标 {target_stock_count} 只")
            suggestions.append("扩大数据覆盖范围，或临时下调 max_stocks")
        if date_range.get("max") and date_range["max"] < cutoff:
            issues.append(f"离线库最新日期 {date_range['max']} 早于训练截断日 {cutoff}")
            suggestions.append("补齐最近日线，避免训练截断日超出离线库覆盖范围")

        ready = status["kline_count"] > 0 and eligible_count > 0
        diagnostics = {
            "cutoff_date": cutoff,
            "target_stock_count": target_stock_count,
            "min_history_days": int(min_history_days),
            "eligible_stock_count": eligible_count,
            "ready": ready,
            "issues": issues,
            "suggestions": suggestions,
            "offline_available": self._offline.available,
            "status": status,
            "date_range": date_range,
            "quality_checks": quality["checks"],
        }
        self.last_diagnostics = diagnostics
        return diagnostics

    def load_stock_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
        include_future_days: int = 0,
    ) -> Dict[str, pd.DataFrame]:
        if self._provider is not None:
            self.last_source = "mock" if isinstance(self._provider, MockDataProvider) else "provider"
            return self._provider.load_stock_data(
                cutoff_date=cutoff_date,
                stock_count=stock_count,
                min_history_days=min_history_days,
                include_future_days=include_future_days,
            )

        if self._prefer_offline and self._offline.available:
            offline_diagnostics = self.diagnose_training_data(
                cutoff_date=cutoff_date,
                stock_count=stock_count,
                min_history_days=min_history_days,
            )
            stock_data = self._offline.get_stocks(
                cutoff_date=cutoff_date,
                stock_count=stock_count,
                min_history_days=min_history_days,
                include_future_days=include_future_days,
            )
            if stock_data:
                self.last_source = "offline"
                return stock_data
            logger.warning(
                "离线数据未命中: cutoff=%s eligible=%s target=%s issues=%s",
                offline_diagnostics["cutoff_date"],
                offline_diagnostics["eligible_stock_count"],
                offline_diagnostics["target_stock_count"],
                "; ".join(str(x) for x in offline_diagnostics["issues"]) or "none",
            )

        if self._online is None:
            try:
                self._online = EvolutionDataLoader()
            except Exception as exc:
                logger.warning("在线加载器初始化失败: %s", exc)
                self.last_source = "mock"
                return generate_mock_stock_data(stock_count)

        try:
            effective_cutoff = normalize_date(cutoff_date)
            if include_future_days > 0:
                cutoff_dt = datetime.strptime(effective_cutoff, "%Y%m%d")
                effective_cutoff = (cutoff_dt + timedelta(days=include_future_days * 2)).strftime("%Y%m%d")
            data = self._online.load_all_data_before(effective_cutoff)
            stocks = data.get("stocks", {})
            if stocks:
                self.last_source = "online"
                return dict(list(stocks.items())[:stock_count])
        except Exception as exc:
            logger.warning("在线数据加载失败: %s", exc)

        logger.warning("所有数据源不可用，使用模拟数据")
        self.last_source = "mock"
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
    parser.add_argument("--financials", action="store_true", help="同步财务快照（当前需配合 tushare）")
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

    service = DataIngestionService(tushare_token=args.token)
    if args.financials:
        if args.source != "tushare":
            raise RuntimeError("财务快照同步当前仅支持 --source tushare")
        financial = service.sync_financial_snapshots_from_tushare(
            stock_limit=args.stocks,
            test_mode=args.test,
        )
        print(json.dumps({"financial": financial}, ensure_ascii=False, indent=2))
        return

    if args.source == "baostock":
        security = service.sync_security_master()
        daily = service.sync_daily_bars(start_date=args.start, end_date=args.end)
        index = service.sync_index_bars(start_date=args.start, end_date=args.end)
        print(json.dumps({"security": security, "daily": daily, "index": index}, ensure_ascii=False, indent=2))
    else:
        daily = service.sync_daily_bars_from_tushare(
            start_date=args.start,
            end_date=args.end,
            stock_limit=args.stocks,
            test_mode=args.test,
        )
        print(json.dumps({"daily": daily}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _cli_main()
