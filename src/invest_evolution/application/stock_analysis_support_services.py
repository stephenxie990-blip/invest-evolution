"""Composition helpers for stock-analysis support-service wiring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from invest_evolution.application.stock_analysis_ask_stock_assembly import (
    AskStockResponseAssemblyService,
)
from invest_evolution.application.stock_analysis_ask_stock_request_context import (
    AskStockRequestContextService,
)
from invest_evolution.application.stock_analysis_batch_service import (
    BatchAnalysisViewService,
)
from invest_evolution.application.stock_analysis_observation_service import (
    StockAnalysisObservationService,
)
from invest_evolution.application.stock_analysis_parsing_service import (
    StockAnalysisParsingService,
)
from invest_evolution.application.stock_analysis_projection_service import (
    StockAnalysisProjectionService,
)
from invest_evolution.application.stock_analysis_prompt_service import (
    StockAnalysisPromptService,
)
from invest_evolution.application.stock_analysis_tool_runtime import (
    StockAnalysisToolRuntimeSupportService,
)


@dataclass(frozen=True)
class StockAnalysisSupportServices:
    batch_analysis_service: BatchAnalysisViewService
    ask_stock_request_context_service: AskStockRequestContextService
    ask_stock_response_assembly_service: AskStockResponseAssemblyService
    stock_analysis_prompt_service: StockAnalysisPromptService
    stock_analysis_parsing_service: StockAnalysisParsingService
    stock_analysis_observation_service: StockAnalysisObservationService
    stock_analysis_projection_service: StockAnalysisProjectionService
    tool_runtime_support_service: StockAnalysisToolRuntimeSupportService


def build_stock_analysis_support_services(
    *,
    humanize_macd_cross: Callable[[str], str],
    resolve_strategy_name: Callable[[str, str], tuple[str, str]],
    infer_days: Callable[[str, int], int],
    load_strategy: Callable[[str], Any],
    resolve_query_context: Callable[[str], Any],
    resolve_effective_as_of_date: Callable[[str, str], str],
    normalize_as_of_date: Callable[[str | None], str],
    available_tools_provider: Callable[[], list[str]],
    normalize_tool_name: Callable[[str], str],
    catalog_by_name_provider: Callable[[], dict[str, dict[str, Any]]],
    definitions_by_name_provider: Callable[[], dict[str, dict[str, Any]]],
    build_indicator_projection: Callable[..., Any],
    build_batch_analysis_context: Callable[..., tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
    view_from_snapshot: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    snapshot_projector: Callable[..., dict[str, Any]],
    resolve_security: Callable[[str], tuple[str, dict[str, Any]]],
    get_stock_frame: Callable[[str], Any],
    build_tool_unavailable_response: Callable[..., dict[str, Any]],
    query_context_factory: Callable[..., Any],
    window_context_factory: Callable[..., Any],
) -> StockAnalysisSupportServices:
    batch_analysis_service = BatchAnalysisViewService(
        humanize_macd_cross=humanize_macd_cross
    )
    return StockAnalysisSupportServices(
        batch_analysis_service=batch_analysis_service,
        ask_stock_request_context_service=AskStockRequestContextService(
            resolve_strategy_name=resolve_strategy_name,
            infer_days=infer_days,
            load_strategy=load_strategy,
            resolve_query_context=resolve_query_context,
            resolve_effective_as_of_date=resolve_effective_as_of_date,
        ),
        ask_stock_response_assembly_service=AskStockResponseAssemblyService(
            normalize_as_of_date=normalize_as_of_date,
            available_tools_provider=available_tools_provider,
        ),
        stock_analysis_prompt_service=StockAnalysisPromptService(
            normalize_tool_name=normalize_tool_name,
            catalog_by_name_provider=catalog_by_name_provider,
            definitions_by_name_provider=definitions_by_name_provider,
        ),
        stock_analysis_parsing_service=StockAnalysisParsingService(),
        stock_analysis_observation_service=StockAnalysisObservationService(
            build_indicator_projection=build_indicator_projection,
        ),
        stock_analysis_projection_service=StockAnalysisProjectionService(
            build_batch_analysis_context=build_batch_analysis_context,
            view_from_snapshot=view_from_snapshot,
            snapshot_projector=snapshot_projector,
        ),
        tool_runtime_support_service=StockAnalysisToolRuntimeSupportService(
            resolve_security=resolve_security,
            get_stock_frame=get_stock_frame,
            build_tool_unavailable_response=build_tool_unavailable_response,
            query_context_factory=query_context_factory,
            window_context_factory=window_context_factory,
        ),
    )


__all__ = [
    "StockAnalysisSupportServices",
    "build_stock_analysis_support_services",
]
