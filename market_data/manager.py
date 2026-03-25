"""投资进化系统 - 干净的数据主入口。"""

import argparse
import json
import logging
import os
import random
from datetime import datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Protocol, cast


import numpy as np
import pandas as pd

from config import PROJECT_ROOT, config, normalize_date
from config.control_plane import get_runtime_data_policy
from .datasets import CapitalFlowDatasetService, EventDatasetService, IntradayDatasetBuilder, TrainingDatasetBuilder, WebDatasetService
from .gateway import MarketDataGateway
from .ingestion import DataIngestionService
from .quality import DataQualityService
from .universe_policy import DEFAULT_MAX_STALENESS_DAYS, select_universe_codes

logger = logging.getLogger(__name__)


def _load_baostock_module():
    return import_module("baostock")


def _load_benchmark_service_class():
    return import_module("market_data.services.benchmark").BenchmarkDataService


def _load_market_query_service_class():
    return import_module("market_data.services.query").MarketQueryService


_DEFAULT_QUALITY_CACHE_TTL_SECONDS = 300

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


class _BaoStockResult(Protocol):
    fields: list[str]

    def next(self) -> bool:
        ...

    def get_row_data(self) -> list[str]:
        ...


def _dict_of_objects(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): cast(object, item) for key, item in value.items()}


def _dict_of_bools(value: object) -> dict[str, bool]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): bool(item) for key, item in value.items()}


def _string_list(value: object) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return []
    return [str(item) for item in value if str(item).strip()]


def _int_value(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str, bytes)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _float_value(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, str, bytes)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return default


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
        include_capital_flow: bool = False,
    ) -> Dict[str, pd.DataFrame]:
        ...


class DataSourceUnavailableError(RuntimeError):
    """Raised when live training data cannot be resolved and mock is not explicitly enabled."""

    error_code = "data_source_unavailable"

    def __init__(
        self,
        message: str,
        *,
        cutoff_date: str,
        stock_count: int,
        min_history_days: int,
        requested_data_mode: str,
        available_sources: dict[str, bool] | None = None,
        offline_diagnostics: dict[str, object] | None = None,
        online_error: str = "",
        suggestions: list[str] | None = None,
        allow_mock_fallback: bool = False,
    ) -> None:
        normalized_cutoff = normalize_date(cutoff_date)
        offline_payload = _dict_of_objects(offline_diagnostics or {})
        suggestion_items = _string_list(suggestions or offline_payload.get("suggestions"))
        payload = {
            "error": str(message),
            "error_code": self.error_code,
            "cutoff_date": normalized_cutoff,
            "stock_count": max(1, int(stock_count)),
            "min_history_days": int(min_history_days),
            "requested_data_mode": str(requested_data_mode or "live"),
            "available_sources": {
                "offline": bool((available_sources or {}).get("offline", False)),
                "online": bool((available_sources or {}).get("online", False)),
                "mock": bool((available_sources or {}).get("mock", False)),
            },
            "offline_diagnostics": offline_payload,
            "online_error": str(online_error or ""),
            "suggestions": suggestion_items,
            "allow_mock_fallback": bool(allow_mock_fallback),
        }
        super().__init__(payload["error"])
        self.payload = payload

    def to_dict(self) -> dict[str, object]:
        return dict(self.payload)

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "DataSourceUnavailableError":
        return cls(
            str(payload.get("error") or "训练数据源不可用"),
            cutoff_date=str(payload.get("cutoff_date") or "19700101"),
            stock_count=_int_value(payload.get("stock_count"), 1),
            min_history_days=_int_value(payload.get("min_history_days"), 1),
            requested_data_mode=str(payload.get("requested_data_mode") or "live"),
            available_sources=_dict_of_bools(payload.get("available_sources")),
            offline_diagnostics=_dict_of_objects(payload.get("offline_diagnostics")),
            online_error=str(payload.get("online_error") or ""),
            suggestions=_string_list(payload.get("suggestions")),
            allow_mock_fallback=bool(payload.get("allow_mock_fallback", False)),
        )


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
        include_capital_flow: bool = False,
    ) -> Dict[str, pd.DataFrame]:
        del include_future_days
        selected: Dict[str, pd.DataFrame] = {}
        cutoff = normalize_date(cutoff_date)
        candidates: list[dict[str, object]] = []
        for code, df in self.data.items():
            trade_dates = df["trade_date"].astype(str).map(normalize_date)
            history_mask = trade_dates <= cutoff
            history_days = int(history_mask.sum())
            last_trade_date = str(trade_dates[history_mask].max()) if history_days > 0 else ""
            candidates.append(
                {
                    "code": str(code),
                    "history_days": history_days,
                    "last_trade_date": last_trade_date,
                }
            )
        selected_codes = select_universe_codes(
            candidates=candidates,
            cutoff_date=cutoff,
            stock_count=stock_count,
            min_history_days=min_history_days,
            max_staleness_days=DEFAULT_MAX_STALENESS_DAYS,
        )
        for code in selected_codes:
            selected[code] = self.data[code].copy()
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

        bs = _load_baostock_module()

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
                if rs is None:
                    continue

                result = cast(_BaoStockResult, rs)
                rows: list[dict[str, str]] = []
                while result.next():
                    rows.append(dict(zip(result.fields, result.get_row_data())))
                if not rows:
                    continue

                df = pd.DataFrame(rows)
                df["trade_date"] = df["date"].map(normalize_date)
                df["pct_chg"] = pd.to_numeric(df.get("pctChg"), errors="coerce")
                df["turnover"] = pd.to_numeric(df.get("turn"), errors="coerce")
                for column in ("open", "high", "low", "close", "volume", "amount"):
                    df[column] = pd.to_numeric(df[column], errors="coerce")
                stock_data[code] = cast(
                    pd.DataFrame,
                    df[
                        ["date", "trade_date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover", "code"]
                    ],
                )
        finally:
            bs.logout()

        return {"cutoff_date": cutoff, "stocks": stock_data}


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
        MarketQueryService = _load_market_query_service_class()
        BenchmarkDataService = _load_benchmark_service_class()

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
        ready = bool(eligible_count > 0)

        if stock_count_available <= 0:
            issues.append("security_master 为空")
            suggestions.append("先初始化股票主数据")
        if not date_max:
            issues.append("daily_bar 为空")
            suggestions.append("先下载历史日线")
            ready = False
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
            "ready": ready,
            "issues": issues,
            "suggestions": suggestions,
            "offline_available": self._offline.available,
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

        ready = kline_count > 0 and eligible_count > 0
        if financial_count <= 0:
            suggestions.append("可选：执行 python3 -m market_data --source akshare --financials 补齐财务快照；如已配置 TUSHARE_TOKEN 也可使用 tushare")
        if index_kline_count <= 0:
            suggestions.append("先执行 python3 -m market_data --source baostock 补齐指数日线")

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
            try:
                offline_diagnostics = self.check_training_readiness(
                    cutoff_date=cutoff_date,
                    stock_count=stock_count,
                    min_history_days=min_history_days,
                )
            except Exception:
                logger.debug("离线训练就绪诊断失败", exc_info=True)
                offline_diagnostics = {}

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
