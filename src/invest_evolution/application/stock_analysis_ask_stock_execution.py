"""Ask-stock execution and research sequencing orchestration helpers."""

from __future__ import annotations

from typing import Any, Callable

from invest_evolution.application.stock_analysis_ask_stock_assembly import (
    AskStockExecutionRunBundle,
    AskStockRequestContext,
)


class AskStockExecutionOrchestrationService:
    def __init__(
        self,
        *,
        build_execution_planning_bundle: Callable[..., Any],
        run_react_executor: Callable[..., dict[str, Any]],
        build_execution_coverage: Callable[..., dict[str, Any]],
        derive_signals: Callable[[dict[str, Any]], dict[str, Any]],
        build_research_bridge: Callable[..., dict[str, Any]],
        research_resolution_service: Any,
        dashboard_projection_builder: Callable[..., dict[str, Any]],
    ) -> None:
        self.build_execution_planning_bundle = build_execution_planning_bundle
        self.run_react_executor = run_react_executor
        self.build_execution_coverage = build_execution_coverage
        self.derive_signals = derive_signals
        self.build_research_bridge = build_research_bridge
        self.research_resolution_service = research_resolution_service
        self.dashboard_projection_builder = dashboard_projection_builder

    def run_execution_stage(
        self,
        *,
        request_context: AskStockRequestContext,
    ) -> AskStockExecutionRunBundle:
        planning_bundle = self.build_execution_planning_bundle(
            strategy=request_context.strategy,
            query=request_context.code,
            days=request_context.days,
        )
        execution = self.run_react_executor(
            question=request_context.question,
            query=request_context.code,
            security=request_context.security,
            strategy=request_context.strategy,
            days=request_context.days,
            planning_bundle=planning_bundle,
        )
        return AskStockExecutionRunBundle(
            recommended_plan=planning_bundle.recommended_plan,
            allowed_tools=planning_bundle.allowed_tools,
            execution=execution,
            coverage=self.build_execution_coverage(
                strategy=request_context.strategy,
                execution=execution,
                recommended_plan=planning_bundle.recommended_plan,
                allowed_tools=planning_bundle.allowed_tools,
            ),
            derived=self.derive_signals(execution),
        )

    def resolve_research_outputs(
        self,
        *,
        request_context: AskStockRequestContext,
        execution_bundle: AskStockExecutionRunBundle,
    ) -> dict[str, Any]:
        research_bridge = self.build_research_bridge(
            code=request_context.code,
            security=request_context.security,
            requested_as_of_date=request_context.requested_as_of_date,
            effective_as_of_date=request_context.effective_as_of_date,
            days=request_context.days,
            derived=execution_bundle.derived,
        )
        return self.research_resolution_service.resolve_outputs(
            research_bridge=research_bridge,
            question=request_context.question,
            query=request_context.query,
            strategy=request_context.strategy,
            strategy_source=request_context.strategy_source,
            code=request_context.code,
            requested_as_of_date=request_context.requested_as_of_date,
            effective_as_of_date=request_context.effective_as_of_date,
            execution=execution_bundle.execution,
            derived=execution_bundle.derived,
            dashboard_projection_builder=self.dashboard_projection_builder,
        )
