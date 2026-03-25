from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.stock_analysis_batch_service import BatchAnalysisViewService


@dataclass(frozen=True)
class StockAnalysisSupportServices:
    batch_analysis_service: BatchAnalysisViewService


def build_stock_analysis_support_services(
    *,
    humanize_macd_cross: Callable[[str], str],
) -> StockAnalysisSupportServices:
    return StockAnalysisSupportServices(
        batch_analysis_service=BatchAnalysisViewService(
            humanize_macd_cross=humanize_macd_cross
        )
    )


__all__ = [
    "StockAnalysisSupportServices",
    "build_stock_analysis_support_services",
]
