from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from config import normalize_date
from config.control_plane import get_runtime_data_policy
from .ingestion import DataIngestionService

logger = logging.getLogger(__name__)


class MarketDataGateway:
    """统一外部市场数据出站层。

    职责：
    - 统一创建外部同步服务
    - 集中约束运行时在线兜底 / 资金流同步策略
    - 为 CLI / Web API / 训练运行时提供一致的出站边界
    """

    def __init__(
        self,
        *,
        db_path: str | None = None,
        tushare_token: str | None = None,
        runtime_policy: dict[str, Any] | None = None,
        ingestion_factory: Callable[..., Any] | None = None,
        online_loader_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.db_path = db_path
        self.tushare_token = tushare_token
        self.ingestion_factory = ingestion_factory or DataIngestionService
        self.online_loader_factory = online_loader_factory
        self.runtime_policy = dict(runtime_policy or get_runtime_data_policy())
        self.allow_online_fallback = bool(self.runtime_policy.get("allow_online_fallback", False))
        self.allow_capital_flow_sync = bool(self.runtime_policy.get("allow_capital_flow_sync", False))

    def create_ingestion_service(self, *, repository=None):
        kwargs: dict[str, Any] = {"tushare_token": self.tushare_token}
        if repository is not None:
            kwargs["repository"] = repository
        elif self.db_path:
            kwargs["db_path"] = self.db_path
        return self.ingestion_factory(**kwargs)

    def create_online_loader(self) -> tuple[Any | None, str]:
        if not self.allow_online_fallback:
            return None, "disabled_by_control_plane"
        if self.online_loader_factory is None:
            return None, "online_loader_unconfigured"
        try:
            return self.online_loader_factory(), ""
        except Exception as exc:  # pragma: no cover - exercised through caller integration
            logger.warning("在线加载器初始化失败: %s", exc)
            return None, str(exc)

    def ensure_runtime_derivatives(
        self,
        *,
        repository,
        cutoff_date: str,
        stock_count: int,
        min_history_days: int,
        include_future_days: int,
        include_capital_flow: bool = False,
    ) -> None:
        service = self.create_ingestion_service(repository=repository)
        start_date = normalize_date(cutoff_date)
        end_date = normalize_date(cutoff_date)
        if include_future_days > 0:
            cutoff_dt = datetime.strptime(start_date, "%Y%m%d")
            end_date = (cutoff_dt + timedelta(days=include_future_days * 2)).strftime("%Y%m%d")
        history_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=max(120, min_history_days * 2))).strftime("%Y%m%d")
        codes = repository.select_codes_with_history(cutoff_date, min_history_days, stock_count)
        if not codes:
            return
        service.sync_trading_calendar(start_date=history_start, end_date=end_date)
        service.sync_security_status_daily(codes=codes, start_date=history_start, end_date=end_date)
        service.sync_factor_snapshots(codes=codes, start_date=history_start, end_date=end_date)
        if include_capital_flow:
            if not self.allow_capital_flow_sync:
                logger.info("控制面已禁止运行时资金流外部同步；仅使用本地已有资金流数据")
                return
            try:
                service.sync_capital_flow_daily_from_akshare(codes=codes)
            except Exception as exc:
                logger.warning("点时资金流增强同步失败: %s", exc)

    def sync_background_full_refresh(self) -> dict[str, Any]:
        service = self.create_ingestion_service()
        logger.info("开始后台同步股票主数据...")
        security = service.sync_security_master()
        logger.info("开始后台同步日线数据...")
        daily = service.sync_daily_bars()
        logger.info("开始后台同步指数数据...")
        index = service.sync_index_bars()
        logger.info("后台数据同步完成")
        return {"security": security, "daily": daily, "index": index}

    def sync_calendar(self, *, source: str, start_date: str, end_date: str | None = None) -> dict[str, Any]:
        service = self.create_ingestion_service()
        if source == "akshare":
            return service.sync_trading_calendar_from_akshare(start_date=start_date, end_date=end_date)
        if source == "baostock":
            return service.sync_trading_calendar(start_date=start_date, end_date=end_date)
        raise RuntimeError("交易日历同步当前仅支持 --source baostock 或 --source akshare")

    def sync_capital_flow(self, *, source: str, stock_limit: int, offset: int = 0) -> dict[str, Any]:
        if source != "akshare":
            raise RuntimeError("资金流同步当前仅支持 --source akshare")
        service = self.create_ingestion_service()
        return service.sync_capital_flow_daily_from_akshare(stock_limit=stock_limit, offset=offset)

    def sync_dragon_tiger(self, *, source: str, start_date: str, end_date: str | None = None) -> dict[str, Any]:
        if source != "akshare":
            raise RuntimeError("龙虎榜同步当前仅支持 --source akshare")
        service = self.create_ingestion_service()
        return service.sync_dragon_tiger_list_from_akshare(start_date=start_date, end_date=end_date)

    def sync_intraday_60m(
        self,
        *,
        source: str,
        start_date: str,
        end_date: str | None = None,
        stock_limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        if source != "baostock":
            raise RuntimeError("60分钟线同步当前仅支持 --source baostock")
        service = self.create_ingestion_service()
        return service.sync_intraday_bars_60m(
            start_date=start_date,
            end_date=end_date,
            stock_limit=stock_limit,
            offset=offset,
        )

    def sync_financials(
        self,
        *,
        source: str,
        start_date: str,
        end_date: str | None = None,
        stock_limit: int = 200,
        offset: int = 0,
        test_mode: bool = False,
    ) -> dict[str, Any]:
        service = self.create_ingestion_service()
        if source == "tushare":
            return service.sync_financial_snapshots_from_tushare(stock_limit=stock_limit, test_mode=test_mode)
        if source == "akshare":
            return service.sync_financial_snapshots_from_akshare_bulk(
                stock_limit=stock_limit,
                offset=offset,
                start_date=start_date,
                end_date=end_date,
            )
        raise RuntimeError("财务快照同步当前仅支持 --source tushare 或 --source akshare")

    def sync_default_source(
        self,
        *,
        source: str,
        start_date: str,
        end_date: str | None = None,
        stock_limit: int = 200,
        test_mode: bool = False,
    ) -> dict[str, Any]:
        service = self.create_ingestion_service()
        if source == "baostock":
            return self.sync_background_full_refresh()
        if source == "tushare":
            daily = service.sync_daily_bars_from_tushare(
                start_date=start_date,
                end_date=end_date,
                stock_limit=stock_limit,
                test_mode=test_mode,
            )
            return {"daily": daily}
        calendar = self.sync_calendar(source=source, start_date=start_date, end_date=end_date)
        return {"calendar": calendar}
