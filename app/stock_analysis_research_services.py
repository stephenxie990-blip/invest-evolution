from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.stock_analysis_research_resolution_service import ResearchResolutionService


@dataclass(frozen=True)
class StockAnalysisResearchServices:
    research_resolution_service: ResearchResolutionService


def build_stock_analysis_research_services(
    *,
    case_store: Any,
    scenario_engine: Any,
    attribution_engine: Any,
    logger: Any,
) -> StockAnalysisResearchServices:
    return StockAnalysisResearchServices(
        research_resolution_service=ResearchResolutionService(
            case_store=case_store,
            scenario_engine=scenario_engine,
            attribution_engine=attribution_engine,
            logger=logger,
        )
    )


__all__ = [
    "StockAnalysisResearchServices",
    "build_stock_analysis_research_services",
]
