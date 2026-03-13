"""Runtime sync facade for market data ingestion."""

from __future__ import annotations

from typing import Any

from market_data.ingestion import DataIngestionService


class MarketSyncService:
    """Explicit service boundary for market data synchronization workflows."""

    def __init__(self, ingestion_service: DataIngestionService | None = None, **ingestion_kwargs: Any):
        self.ingestion_service = ingestion_service or DataIngestionService(**ingestion_kwargs)

    def sync_security_master(self) -> dict[str, Any]:
        return self.ingestion_service.sync_security_master()

    def sync_daily_bars(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.ingestion_service.sync_daily_bars(*args, **kwargs)

    def sync_index_bars(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.ingestion_service.sync_index_bars(*args, **kwargs)

    def sync_trading_calendar(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.ingestion_service.sync_trading_calendar(*args, **kwargs)

    def sync_security_status_daily(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.ingestion_service.sync_security_status_daily(*args, **kwargs)

    def sync_factor_snapshots(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.ingestion_service.sync_factor_snapshots(*args, **kwargs)

    def sync_financial_snapshots_from_akshare_bulk(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.ingestion_service.sync_financial_snapshots_from_akshare_bulk(*args, **kwargs)

    def sync_financial_snapshots_from_akshare(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.ingestion_service.sync_financial_snapshots_from_akshare(*args, **kwargs)

    def sync_financial_snapshots_from_tushare(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.ingestion_service.sync_financial_snapshots_from_tushare(*args, **kwargs)

    def sync_capital_flow_daily_from_akshare(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.ingestion_service.sync_capital_flow_daily_from_akshare(*args, **kwargs)

    def sync_dragon_tiger_list_from_akshare(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.ingestion_service.sync_dragon_tiger_list_from_akshare(*args, **kwargs)

    def sync_daily_bars_from_tushare(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.ingestion_service.sync_daily_bars_from_tushare(*args, **kwargs)
