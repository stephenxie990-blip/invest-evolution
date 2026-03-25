"""Composition helpers for stock-analysis research-service wiring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from invest_evolution.application.stock_analysis_research_bridge_service import (
    StockAnalysisResearchBridgeService,
)
from invest_evolution.application.stock_analysis_research_resolution_service import (
    ResearchResolutionService,
)


@dataclass(frozen=True)
class StockAnalysisResearchServices:
    research_resolution_service: ResearchResolutionService
    research_bridge_service: StockAnalysisResearchBridgeService


def build_stock_analysis_research_services(
    *,
    case_store: Any,
    scenario_engine: Any,
    attribution_engine: Any,
    repository: Any,
    controller_provider: Callable[[], Any] | None,
    governance_service_factory: Callable[[], Any],
    normalize_as_of_date: Callable[[str | None], str],
    resolve_effective_as_of_date: Callable[[str, str], str],
    logger_instance: Any,
) -> StockAnalysisResearchServices:
    research_resolution_service = ResearchResolutionService(
        case_store=case_store,
        scenario_engine=scenario_engine,
        attribution_engine=attribution_engine,
        logger=logger_instance,
    )
    return StockAnalysisResearchServices(
        research_resolution_service=research_resolution_service,
        research_bridge_service=StockAnalysisResearchBridgeService(
            repository=repository,
            controller_provider=controller_provider,
            research_resolution_service=research_resolution_service,
            governance_service=governance_service_factory(),
            normalize_as_of_date=normalize_as_of_date,
            resolve_effective_as_of_date=resolve_effective_as_of_date,
            logger_instance=logger_instance,
        ),
    )


__all__ = [
    "StockAnalysisResearchServices",
    "build_stock_analysis_research_services",
]
