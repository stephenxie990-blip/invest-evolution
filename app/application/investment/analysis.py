"""Application-layer stock analysis facade."""

from __future__ import annotations

from typing import Any

from app.stock_analysis import StockAnalysisService


class StockAnalysisOrchestrator(StockAnalysisService):
    """Phase 6 facade that preserves the existing stock analysis behavior."""


def build_stock_analysis_orchestrator(*args: Any, **kwargs: Any) -> StockAnalysisOrchestrator:
    return StockAnalysisOrchestrator(*args, **kwargs)
