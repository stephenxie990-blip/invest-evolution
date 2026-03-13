"""Benchmark facade for market data domain services."""

from __future__ import annotations

from typing import Any, Protocol

import pandas as pd

from config import normalize_date
from market_data.repository import MarketDataRepository


class BenchmarkManagerLike(Protocol):
    def get_benchmark_daily_values(
        self,
        trading_dates: list[str],
        index_code: str = "sh.000300",
    ) -> list[float]:
        ...

    def get_market_index_frame(
        self,
        index_code: str = "sh.000300",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        ...


class BenchmarkRepositoryLike(Protocol):
    def query_index_bars(
        self,
        index_codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        ...


class BenchmarkDataService:
    """Service boundary for benchmark and market index access."""

    def __init__(
        self,
        data_manager: BenchmarkManagerLike | None = None,
        repository: BenchmarkRepositoryLike | None = None,
        **manager_kwargs: Any,
    ):
        self.data_manager = data_manager
        self.repository = repository
        self._manager_kwargs = manager_kwargs
        if self.data_manager is None and self.repository is None:
            from market_data.manager import DataManager

            self.data_manager = DataManager(**manager_kwargs)

    def get_benchmark_daily_values(
        self,
        trading_dates: list[str],
        index_code: str = "sh.000300",
    ) -> list[float]:
        if self.data_manager is not None:
            return self.data_manager.get_benchmark_daily_values(
                trading_dates=trading_dates,
                index_code=index_code,
            )
        if not trading_dates:
            return []

        repository = self._resolve_repository()
        frame = repository.query_index_bars(
            index_codes=[index_code],
            start_date=min(trading_dates),
            end_date=max(trading_dates),
        )
        if frame.empty:
            return []

        aligned = frame.copy()
        aligned["trade_date"] = aligned["trade_date"].astype(str).map(normalize_date)
        aligned = aligned.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
        closes = aligned.set_index("trade_date")["close"].astype(float)
        series = closes.reindex(trading_dates).ffill().bfill()
        return [float(value) for value in series.tolist() if value is not None]

    def get_market_index_frame(
        self,
        index_code: str = "sh.000300",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        if self.data_manager is not None:
            return self.data_manager.get_market_index_frame(
                index_code=index_code,
                start_date=start_date,
                end_date=end_date,
            )

        return self._resolve_repository().query_index_bars(
            index_codes=[index_code],
            start_date=start_date,
            end_date=end_date,
        )

    def _resolve_repository(self) -> BenchmarkRepositoryLike:
        if self.repository is not None:
            return self.repository
        return MarketDataRepository()
