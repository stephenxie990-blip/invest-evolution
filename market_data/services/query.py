"""Read/query facade for market data."""

from __future__ import annotations

from typing import Any, Sequence

import pandas as pd

from market_data.datasets import WebDatasetService


class MarketQueryService:
    """Explicit read-side facade for status and web-facing dataset queries."""

    def __init__(self, dataset_service: WebDatasetService | None = None, **dataset_kwargs: Any):
        self.dataset_service = dataset_service or WebDatasetService(**dataset_kwargs)

    def get_status_summary(self, *, refresh: bool = False) -> dict[str, Any]:
        return self.dataset_service.get_status_summary(refresh=refresh)

    def get_capital_flow(
        self,
        *,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return self.dataset_service.get_capital_flow(
            codes=codes,
            start_date=start_date,
            end_date=end_date,
        )

    def get_dragon_tiger_events(
        self,
        *,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return self.dataset_service.get_dragon_tiger_events(
            codes=codes,
            start_date=start_date,
            end_date=end_date,
        )

    def get_intraday_60m_bars(
        self,
        *,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return self.dataset_service.get_intraday_60m_bars(
            codes=codes,
            start_date=start_date,
            end_date=end_date,
        )
