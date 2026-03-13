"""Availability and readiness service facade for market data."""

from __future__ import annotations

from typing import Any, Protocol

from market_data.manager import DataManager


class AvailabilityManagerLike(Protocol):
    def check_training_readiness(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
    ) -> dict[str, object]:
        ...

    def diagnose_training_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
    ) -> dict[str, object]:
        ...

    def get_status_summary(self, *, refresh: bool = False) -> dict[str, object]:
        ...


class DataAvailabilityService:
    """Explicit service boundary for dataset readiness and status checks."""

    def __init__(self, data_manager: AvailabilityManagerLike | None = None, **manager_kwargs: Any):
        self.data_manager = data_manager or DataManager(**manager_kwargs)

    def check_training_readiness(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
    ) -> dict[str, object]:
        return self.data_manager.check_training_readiness(
            cutoff_date=cutoff_date,
            stock_count=stock_count,
            min_history_days=min_history_days,
        )

    def diagnose_training_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
    ) -> dict[str, object]:
        return self.data_manager.diagnose_training_data(
            cutoff_date=cutoff_date,
            stock_count=stock_count,
            min_history_days=min_history_days,
        )

    def get_status_summary(self, *, refresh: bool = False) -> dict[str, object]:
        return self.data_manager.get_status_summary(refresh=refresh)
