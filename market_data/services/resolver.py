"""Training dataset resolution facade for market data."""

from __future__ import annotations

from typing import Any, Protocol

import pandas as pd

from market_data.manager import DataManager


class TrainingDatasetManagerLike(Protocol):
    def random_cutoff_date(
        self,
        min_date: str = "20180101",
        max_date: str | None = None,
    ) -> str:
        ...

    def load_stock_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
        include_future_days: int = 0,
        include_capital_flow: bool = False,
    ) -> dict[str, pd.DataFrame]:
        ...


class TrainingDatasetResolver:
    """Service boundary for training dataset selection and loading."""

    def __init__(self, data_manager: TrainingDatasetManagerLike | None = None, **manager_kwargs: Any):
        self.data_manager = data_manager or DataManager(**manager_kwargs)

    def random_cutoff_date(
        self,
        min_date: str = "20180101",
        max_date: str | None = None,
    ) -> str:
        return self.data_manager.random_cutoff_date(min_date=min_date, max_date=max_date)

    def load_stock_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
        include_future_days: int = 0,
        include_capital_flow: bool = False,
    ) -> dict[str, pd.DataFrame]:
        return self.data_manager.load_stock_data(
            cutoff_date=cutoff_date,
            stock_count=stock_count,
            min_history_days=min_history_days,
            include_future_days=include_future_days,
            include_capital_flow=include_capital_flow,
        )
