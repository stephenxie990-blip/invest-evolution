"""Ask-stock response assembly orchestration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, cast

from invest_evolution.agent_runtime.planner import (
    BOUNDED_WORKFLOW_SCHEMA_VERSION,
    build_readonly_task_bus,
)
from invest_evolution.agent_runtime.presentation import (
    build_bounded_entrypoint,
    build_bounded_orchestration,
    build_bounded_policy,
    build_bounded_response_context,
)
from invest_evolution.application.stock_analysis_response_contracts import (
    AskStockExecutionProjection,
    AskStockExecutionStageAdapter,
    AskStockExecutionSurfaceSpec,
    AskStockIdentifiersProjection,
    AskStockPresentationSpec,
    AskStockRequestProjection,
    AskStockResponseAssemblySpec,
    AskStockResponseHeaderFactory,
    AskStockResponseHeaderSpec,
    AskStockResponseInputs,
    AskStockResponseRequestHeader,
    AskStockResponseResolutionHeader,
    AskStockResponseStrategyHeader,
    AskStockSectionsBundle,
)


@dataclass(frozen=True)
class AskStockResearchBundle:
    dashboard: dict[str, Any]
    research_payload: dict[str, Any]
    research_bridge_payload: dict[str, Any]
    policy_id: str
    research_case_id: str
    attribution_id: str


@dataclass(frozen=True)
class AskStockRequestContext:
    question: str
    query: str
    code: str
    security: dict[str, Any]
    requested_as_of_date: str
    effective_as_of_date: str
    strategy: Any
    strategy_source: str
    days: int


@dataclass(frozen=True)
class AskStockExecutionRunBundle:
    recommended_plan: list[dict[str, Any]]
    allowed_tools: list[str]
    execution: dict[str, Any]
    coverage: dict[str, Any]
    derived: dict[str, Any]


@dataclass(frozen=True)
class AskStockStageContract:
    request: AskStockRequestContext
    execution: AskStockExecutionRunBundle
    research: AskStockResearchBundle


@dataclass(frozen=True)
class AskStockAssemblyStageBundle:
    presentation_spec: AskStockPresentationSpec
    response_inputs: AskStockResponseInputs


class AskStockResponseAssemblyService:
    def __init__(
        self,
        *,
        normalize_as_of_date: Callable[[str | None], str],
        available_tools_provider: Callable[[], list[str]],
    ) -> None:
        self.normalize_as_of_date = normalize_as_of_date
        self.available_tools_provider = available_tools_provider

    @staticmethod
    def build_task_artifacts(
        *,
        code: str,
        strategy_name: str,
        strategy_source: str,
        derived: dict[str, Any],
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "code": code,
            "strategy": strategy_name,
            "strategy_source": strategy_source,
            "latest_close": derived.get("latest_close"),
            "gap_fill_applied": bool(
                dict(execution.get("gap_fill") or {}).get("applied")
            ),
        }

    def build_execution_projection(
        self,
        *,
        execution: dict[str, Any],
        recommended_plan: list[dict[str, Any]],
        coverage: dict[str, Any],
        task_bus_artifacts: dict[str, Any],
    ) -> AskStockExecutionProjection:
        return AskStockExecutionProjection(
            mode=str(execution["mode"]),
            tool_plan=list(execution["plan"]),
            tool_calls=list(execution["tool_calls"]),
            workflow=list(execution.get("workflow") or []),
            phase_stats=dict(execution.get("phase_stats") or {}),
            recommended_plan=list(recommended_plan),
            coverage=dict(coverage),
            task_bus_artifacts=dict(task_bus_artifacts),
            llm_reasoning=str(execution.get("final_reasoning", "")),
            fallback_used=bool(
                execution.get("fallback_used", execution["mode"] == "yaml_react_like")
            ),
            gap_fill=dict(execution.get("gap_fill") or {}),
            available_tools=list(self.available_tools_provider()),
        )

    @staticmethod
    def build_identifiers_projection(
        *,
        policy_id: str,
        research_case_id: str,
        attribution_id: str,
    ) -> AskStockIdentifiersProjection:
        return AskStockIdentifiersProjection(
            policy_id=policy_id,
            research_case_id=research_case_id,
            attribution_id=attribution_id,
        )

    @staticmethod
    def with_identifiers(
        payload: dict[str, Any],
        identifiers: AskStockIdentifiersProjection,
    ) -> dict[str, Any]:
        return {**dict(payload), "identifiers": identifiers.to_payload()}

    def build_task_bus(
        self,
        *,
        question: str,
        query: str,
        execution_projection: AskStockExecutionProjection,
    ) -> dict[str, Any]:
        return build_readonly_task_bus(
            intent="stock_analysis",
            operation="ask_stock",
            user_goal=question or query,
            mode=execution_projection.mode,
            available_tools=list(execution_projection.available_tools),
            recommended_plan=list(execution_projection.recommended_plan),
            tool_calls=list(execution_projection.tool_calls),
            artifacts=dict(execution_projection.task_bus_artifacts),
            coverage=dict(execution_projection.coverage),
            status="ok",
        )

    @staticmethod
    def build_bounded_context(
        *,
        execution_projection: AskStockExecutionProjection,
        identifiers: AskStockIdentifiersProjection,
    ) -> dict[str, Any]:
        return build_bounded_response_context(
            schema_version=BOUNDED_WORKFLOW_SCHEMA_VERSION,
            domain="stock",
            operation="ask_stock",
            artifacts={
                **dict(execution_projection.task_bus_artifacts),
                **identifiers.to_payload(),
            },
            workflow=cast(list[str], list(execution_projection.workflow)),
            phase_stats=dict(execution_projection.phase_stats),
            coverage=dict(execution_projection.coverage),
        )

    def build_request_projection(
        self,
        *,
        request_context: AskStockRequestContext,
    ) -> AskStockRequestProjection:
        return AskStockRequestProjection(
            question=request_context.question,
            query=request_context.query,
            normalized_query=request_context.code,
            requested_as_of_date=self.normalize_as_of_date(
                request_context.requested_as_of_date
            ),
            as_of_date=request_context.effective_as_of_date,
        )

    @staticmethod
    def build_entrypoint() -> dict[str, Any]:
        return build_bounded_entrypoint(
            kind="commander_tool_service",
            runtime_tool="invest_ask_stock",
            runtime_method="CommanderRuntime.ask_stock",
            service="StockAnalysisService",
            domain="stock",
            agent_kind="bounded_stock_agent",
            standalone_agent=False,
            meeting_path=False,
            agent_system="commander_brain_tooling",
        )

    @staticmethod
    def build_orchestration_extra_projection(
        *,
        strategy: Any,
        execution_projection: AskStockExecutionProjection,
    ) -> dict[str, Any]:
        return {
            "required_tools": list(strategy.required_tools),
            "recommended_plan": list(execution_projection.recommended_plan),
            "tool_plan": list(execution_projection.tool_plan),
            "tool_calls": list(execution_projection.tool_calls),
            "step_count": len(execution_projection.tool_calls),
            "llm_reasoning": execution_projection.llm_reasoning,
            "fallback_used": execution_projection.fallback_used,
            "gap_fill": dict(execution_projection.gap_fill),
            "coverage": dict(execution_projection.coverage),
        }

    def build_execution_surface_spec(
        self,
        *,
        request_context: AskStockRequestContext,
        execution_projection: AskStockExecutionProjection,
        identifiers: AskStockIdentifiersProjection,
    ) -> AskStockExecutionSurfaceSpec:
        task_bus = self.build_task_bus(
            question=request_context.question,
            query=request_context.query,
            execution_projection=execution_projection,
        )
        bounded_context = self.build_bounded_context(
            execution_projection=execution_projection,
            identifiers=identifiers,
        )
        orchestration_extra = self.build_orchestration_extra_projection(
            strategy=request_context.strategy,
            execution_projection=execution_projection,
        )
        return AskStockExecutionSurfaceSpec(
            task_bus=task_bus,
            protocol=dict(bounded_context["protocol"]),
            artifacts=dict(bounded_context["artifacts"]),
            coverage=dict(bounded_context["coverage"]),
            artifact_taxonomy=dict(bounded_context["artifact_taxonomy"]),
            orchestration_extra=orchestration_extra,
        )

    def build_presentation_spec(
        self,
        *,
        stage_contract: AskStockStageContract,
        execution_surface_spec: AskStockExecutionSurfaceSpec,
        identifiers: AskStockIdentifiersProjection,
    ) -> AskStockPresentationSpec:
        return execution_surface_spec.to_presentation_spec(
            sections=self.build_sections_bundle(
                execution_bundle=stage_contract.execution,
                research_bundle=stage_contract.research,
                identifiers=identifiers,
            ),
        )

    def build_analysis_section_payload(
        self,
        *,
        execution_bundle: AskStockExecutionRunBundle,
        research_bundle: AskStockResearchBundle,
        identifiers: AskStockIdentifiersProjection,
    ) -> dict[str, Any]:
        return {
            "tool_results": execution_bundle.execution["results"],
            "result_sequence": execution_bundle.execution["result_sequence"],
            "derived_signals": execution_bundle.derived,
            "research_bridge": self.with_identifiers(
                dict(research_bundle.research_bridge_payload),
                identifiers,
            ),
        }

    def build_research_section_payload(
        self,
        *,
        research_bundle: AskStockResearchBundle,
        identifiers: AskStockIdentifiersProjection,
    ) -> dict[str, Any]:
        return self.with_identifiers(
            dict(research_bundle.research_payload),
            identifiers,
        )

    def build_sections_bundle(
        self,
        *,
        execution_bundle: AskStockExecutionRunBundle,
        research_bundle: AskStockResearchBundle,
        identifiers: AskStockIdentifiersProjection,
    ) -> AskStockSectionsBundle:
        return AskStockSectionsBundle(
            analysis=self.build_analysis_section_payload(
                execution_bundle=execution_bundle,
                research_bundle=research_bundle,
                identifiers=identifiers,
            ),
            research=self.build_research_section_payload(
                research_bundle=research_bundle,
                identifiers=identifiers,
            ),
        )

    @staticmethod
    def build_research_bundle(
        research_resolution: dict[str, Any],
    ) -> AskStockResearchBundle:
        return AskStockResearchBundle(
            dashboard=dict(research_resolution["dashboard"]),
            research_payload=dict(research_resolution["research"]),
            research_bridge_payload=dict(research_resolution["research_bridge"]),
            policy_id=str(research_resolution.get("policy_id") or ""),
            research_case_id=str(research_resolution.get("research_case_id") or ""),
            attribution_id=str(research_resolution.get("attribution_id") or ""),
        )

    def build_stage_contract(
        self,
        *,
        request_context: AskStockRequestContext,
        execution_bundle: AskStockExecutionRunBundle,
        research_resolution: dict[str, Any],
    ) -> AskStockStageContract:
        return AskStockStageContract(
            request=request_context,
            execution=execution_bundle,
            research=self.build_research_bundle(research_resolution),
        )

    def build_orchestration_payload(
        self,
        *,
        strategy: Any,
        execution_projection: AskStockExecutionProjection,
        allowed_tools: list[str],
        orchestration_extra: dict[str, Any],
    ) -> dict[str, Any]:
        return build_bounded_orchestration(
            mode=execution_projection.mode,
            available_tools=list(execution_projection.available_tools),
            allowed_tools=list(allowed_tools),
            workflow=cast(list[str], list(execution_projection.workflow)),
            phase_stats=dict(execution_projection.phase_stats),
            policy=self.build_orchestration_policy(strategy),
            extra=dict(orchestration_extra),
        )

    @staticmethod
    def build_orchestration_policy(strategy: Any) -> dict[str, Any]:
        return build_bounded_policy(
            source="yaml_strategy",
            agent_kind="bounded_stock_agent",
            workflow_mode="llm_react_with_yaml_gap_fill",
            react_enabled=bool(strategy.react_enabled),
            tool_catalog_scope="strategy_restricted",
            fixed_boundary=True,
            fixed_workflow=True,
        )

    def build_response_header_spec(
        self,
        *,
        request: AskStockRequestProjection,
        identifiers: AskStockIdentifiersProjection,
        security: dict[str, Any],
        strategy: Any,
        strategy_source: str,
        days: int,
    ) -> AskStockResponseHeaderSpec:
        return AskStockResponseHeaderSpec(
            request=AskStockResponseRequestHeader(request=request),
            resolution=AskStockResponseResolutionHeader(
                identifiers=identifiers,
                security=dict(security),
            ),
            strategy=AskStockResponseStrategyHeader(
                entrypoint=self.build_entrypoint(),
                strategy_payload=strategy.to_dict(),
                strategy_source=strategy_source,
                days=days,
            ),
        )

    def build_response_header_factory(
        self,
        *,
        request: AskStockRequestProjection,
        identifiers: AskStockIdentifiersProjection,
        security: dict[str, Any],
        strategy: Any,
        strategy_source: str,
        days: int,
    ) -> AskStockResponseHeaderFactory:
        return AskStockResponseHeaderFactory(
            spec=self.build_response_header_spec(
                request=request,
                identifiers=identifiers,
                security=security,
                strategy=strategy,
                strategy_source=strategy_source,
                days=days,
            )
        )

    @staticmethod
    def build_response_assembly_spec(
        *,
        response_inputs: AskStockResponseInputs,
    ) -> AskStockResponseAssemblySpec:
        return AskStockResponseAssemblySpec(
            header_factory=response_inputs.header_factory,
            presentation_spec=response_inputs.presentation_spec,
            orchestration=dict(response_inputs.orchestration),
            dashboard=dict(response_inputs.dashboard),
        )

    def build_response_inputs(
        self,
        *,
        stage_contract: AskStockStageContract,
        execution_stage: AskStockExecutionStageAdapter,
        presentation_spec: AskStockPresentationSpec,
    ) -> AskStockResponseInputs:
        return AskStockResponseInputs(
            header_factory=self.build_response_header_factory(
                request=self.build_request_projection(
                    request_context=stage_contract.request
                ),
                identifiers=execution_stage.identifiers,
                security=stage_contract.request.security,
                strategy=stage_contract.request.strategy,
                strategy_source=stage_contract.request.strategy_source,
                days=stage_contract.request.days,
            ),
            presentation_spec=presentation_spec,
            orchestration=self.build_orchestration_payload(
                strategy=stage_contract.request.strategy,
                execution_projection=execution_stage.execution_projection,
                allowed_tools=stage_contract.execution.allowed_tools,
                orchestration_extra=execution_stage.execution_surface_spec.orchestration_extra,
            ),
            dashboard=dict(stage_contract.research.dashboard),
        )

    def build_assembly_stage_bundle(
        self,
        *,
        stage_contract: AskStockStageContract,
    ) -> AskStockAssemblyStageBundle:
        execution_stage = self.build_execution_stage_adapter(stage_contract=stage_contract)
        presentation_spec = self.build_presentation_spec(
            stage_contract=stage_contract,
            execution_surface_spec=execution_stage.execution_surface_spec,
            identifiers=execution_stage.identifiers,
        )
        return AskStockAssemblyStageBundle(
            presentation_spec=presentation_spec,
            response_inputs=self.build_response_inputs(
                stage_contract=stage_contract,
                execution_stage=execution_stage,
                presentation_spec=presentation_spec,
            ),
        )

    def build_execution_stage_adapter(
        self,
        *,
        stage_contract: AskStockStageContract,
    ) -> AskStockExecutionStageAdapter:
        identifiers = self.build_identifiers_projection(
            policy_id=stage_contract.research.policy_id,
            research_case_id=stage_contract.research.research_case_id,
            attribution_id=stage_contract.research.attribution_id,
        )
        execution_projection = self.build_execution_projection(
            execution=stage_contract.execution.execution,
            recommended_plan=stage_contract.execution.recommended_plan,
            coverage=stage_contract.execution.coverage,
            task_bus_artifacts=self.build_task_artifacts(
                code=stage_contract.request.code,
                strategy_name=stage_contract.request.strategy.name,
                strategy_source=stage_contract.request.strategy_source,
                derived=stage_contract.execution.derived,
                execution=stage_contract.execution.execution,
            ),
        )
        return AskStockExecutionStageAdapter(
            identifiers=identifiers,
            execution_projection=execution_projection,
            execution_surface_spec=self.build_execution_surface_spec(
                request_context=stage_contract.request,
                execution_projection=execution_projection,
                identifiers=identifiers,
            ),
        )
