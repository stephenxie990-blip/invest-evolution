"""Canonical stock analysis facade, strategies, and research bridge."""

from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast

import pandas as pd

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from invest_evolution.agent_runtime.planner import (
    BOUNDED_WORKFLOW_SCHEMA_VERSION,
    build_readonly_task_bus,
)
from invest_evolution.agent_runtime.presentation import (
    build_bounded_entrypoint,
    build_bounded_orchestration,
    build_bounded_policy,
    build_bounded_response_context,
    build_protocol_response,
)
from invest_evolution.common.utils import (
    LLMGateway,
    LLMGatewayError,
    LLMUnavailableError,
)
from invest_evolution.config import OUTPUT_DIR, PROJECT_ROOT, config, normalize_date
from invest_evolution.config.control_plane import resolve_default_llm
from invest_evolution.application.training.execution import (
    build_manager_runtime,
    controller_default_manager_config_ref,
    controller_default_manager_id,
    normalize_path_ref,
    resolve_manager_config_ref,
)
from invest_evolution.application.training.policy import TrainingGovernanceService
from invest_evolution.investment.contracts import GovernanceDecision
from invest_evolution.investment.foundation.compute import (
    build_batch_indicator_snapshot,
    build_batch_summary,
)
from invest_evolution.investment.research import (
    ResearchAttributionEngine,
    ResearchCaseStore,
    ResearchHypothesis,
    ResearchScenarioEngine,
    build_dashboard_projection,
    build_research_hypothesis,
    build_research_snapshot,
    resolve_policy_snapshot,
)
from invest_evolution.market_data import DataManager
from invest_evolution.market_data.repository import MarketDataRepository

logger = logging.getLogger("invest_evolution.application.stock_analysis")

_STOCK_LLM_REACT_FALLBACK_EXCEPTIONS = (
    LLMUnavailableError,
    LLMGatewayError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
    json.JSONDecodeError,
)

_STOCK_TOOL_EXECUTION_EXCEPTIONS = (
    RuntimeError,
    ValueError,
    TypeError,
    LookupError,
    OSError,
)

_STOCK_RESEARCH_BRIDGE_EXCEPTIONS = _STOCK_TOOL_EXECUTION_EXCEPTIONS + (ImportError,)

_STOCK_RESEARCH_PERSISTENCE_EXCEPTIONS = _STOCK_TOOL_EXECUTION_EXCEPTIONS


@dataclass(frozen=True)
class ResearchBridgeRuntimeContext:
    normalized_requested_as_of_date: str
    replay_mode: bool
    current_manager_id: str
    base_config_path: str
    current_params: dict[str, Any]
    stock_count: int
    min_history_days: int
    lookback_days: int
    parameter_source: str

    def build_data_lineage(
        self,
        *,
        repository_db_path: Path,
        data_manager: DataManager,
        effective_as_of_date: str,
        stock_data: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "db_path": str(repository_db_path),
            "requested_as_of_date": self.normalized_requested_as_of_date,
            "effective_as_of_date": effective_as_of_date,
            "data_source": str(
                getattr(data_manager, "last_source", "unknown") or "unknown"
            ),
            "data_resolution": dict(getattr(data_manager, "last_resolution", {}) or {}),
            "stock_count": len(stock_data),
            "min_history_days": self.min_history_days,
            "lookback_days": self.lookback_days,
        }

    def build_policy_data_window(
        self,
        *,
        effective_as_of_date: str,
        stock_data: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "as_of_date": effective_as_of_date,
            "lookback_days": self.lookback_days,
            "simulation_days": int(getattr(config, "simulation_days", 30) or 30),
            "universe_definition": (
                f"stock_count={self.stock_count}|min_history_days={self.min_history_days}"
            ),
            "stock_universe_size": len(stock_data),
        }

    def build_policy_metadata(
        self,
        *,
        controller: Any,
        effective_as_of_date: str,
    ) -> dict[str, Any]:
        return {
            "parameter_source": self.parameter_source,
            "controller_bound": bool(controller is not None),
            "replay_mode": self.replay_mode,
            "requested_as_of_date": self.normalized_requested_as_of_date,
            "effective_as_of_date": effective_as_of_date,
        }


@dataclass(frozen=True)
class ResearchBridgeRuntimeSelection:
    dominant_manager_id: str
    selected_config: str
    runtime_overrides: dict[str, Any]


@dataclass(frozen=True)
class ResearchBridgeDataBundle:
    data_manager: DataManager
    stock_data: dict[str, Any]


@dataclass(frozen=True)
class ResearchBridgeStageResult:
    bundle: Any | None = None
    unavailable: dict[str, Any] | None = None

    @classmethod
    def ok(cls, bundle: Any) -> "ResearchBridgeStageResult":
        return cls(bundle=bundle, unavailable=None)

    @classmethod
    def unavailable_result(
        cls, payload: dict[str, Any]
    ) -> "ResearchBridgeStageResult":
        return cls(bundle=None, unavailable=dict(payload))


@dataclass(frozen=True)
class ResearchBridgeGovernanceBundle:
    decision: GovernanceDecision
    allowed_manager_ids: list[str]
    governance_enabled: bool
    governance_mode: str


@dataclass(frozen=True)
class ResearchBridgeManagerExecution:
    runtime_selection: ResearchBridgeRuntimeSelection
    manager_runtime: Any
    manager_output: Any


@dataclass(frozen=True)
class ResearchBridgeAssemblyBundle:
    controller: Any
    runtime_context: ResearchBridgeRuntimeContext
    data_bundle: ResearchBridgeDataBundle
    governance_bundle: ResearchBridgeGovernanceBundle
    manager_execution: ResearchBridgeManagerExecution


@dataclass(frozen=True)
class ResearchBridgeOutputBundle:
    governance_context: dict[str, Any]
    data_lineage: dict[str, Any]
    snapshot: Any
    policy: Any


@dataclass(frozen=True)
class ResearchResolutionArtifacts:
    snapshot: Any
    policy: Any
    scenario: dict[str, Any]
    hypothesis: Any


@dataclass(frozen=True)
class ResearchResolutionPersistence:
    policy_id: str
    projection: "ResearchPersistenceProjection"

    def display_identifiers(self) -> "ResearchResolutionIdentifiers":
        return ResearchResolutionIdentifiers(
            policy_id=self.policy_id,
            research_case_id=self.projection.identifiers.research_case_id,
            attribution_id=self.projection.identifiers.attribution_id,
        )


@dataclass(frozen=True)
class ResearchResolutionAvailableStageBundle:
    artifacts: "ResearchResolutionArtifacts"
    analysis: "PreDashboardAnalysisSpec"
    persistence: "ResearchResolutionPersistence"


@dataclass(frozen=True)
class ResearchResolutionBasePayload:
    status: str
    requested_as_of_date: str
    as_of_date: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "requested_as_of_date": self.requested_as_of_date,
            "as_of_date": self.as_of_date,
        }


@dataclass(frozen=True)
class ResearchResolutionPayloadFactory:
    base_payload: ResearchResolutionBasePayload

    def merge(self, updates: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            **self.base_payload.to_payload(),
            **dict(updates or {}),
        }


@dataclass(frozen=True)
class ResearchResolutionDetailUpdatesBundle:
    shared_updates: dict[str, Any]
    research_updates: dict[str, Any]
    research_bridge_updates: dict[str, Any]


@dataclass(frozen=True)
class ResearchResolutionIdentifiers:
    policy_id: str
    research_case_id: str
    attribution_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "policy_id": self.policy_id,
            "research_case_id": self.research_case_id,
            "attribution_id": self.attribution_id,
        }


@dataclass(frozen=True)
class ResearchResolutionDisplaySpec:
    dashboard: dict[str, Any]
    research_payload: dict[str, Any]
    research_bridge_payload: dict[str, Any]
    identifiers: ResearchResolutionIdentifiers


@dataclass(frozen=True)
class ResearchResolutionDisplayBundle:
    context: "ResearchResolutionContext"
    dashboard: dict[str, Any]
    identifiers: ResearchResolutionIdentifiers
    research_updates: dict[str, Any]
    research_bridge_updates: dict[str, Any]


@dataclass(frozen=True)
class DashboardProjectionSpec:
    hypothesis: ResearchHypothesis
    matched_signals: list[str]
    core_rules: list[str]
    entry_conditions: list[str]
    supplemental_reason: str = ""


@dataclass(frozen=True)
class ResearchResolutionDashboardProjectionBundle:
    input_spec: DashboardProjectionSpec
    dashboard_projection_builder: Callable[..., dict[str, Any]]

    def render(self) -> dict[str, Any]:
        return self.dashboard_projection_builder(
            hypothesis=self.input_spec.hypothesis,
            matched_signals=list(self.input_spec.matched_signals),
            core_rules=list(self.input_spec.core_rules),
            entry_conditions=list(self.input_spec.entry_conditions),
            supplemental_reason=self.input_spec.supplemental_reason,
        )


@dataclass(frozen=True)
class PreDashboardAnalysisSpec:
    hypothesis: ResearchHypothesis
    matched_signals: list[str]
    supplemental_reason: str


@dataclass(frozen=True)
class FallbackDerivedAnalysisValues:
    score: float
    stance: str
    entry_price: float | None
    stop_loss: float | None
    contradicting_factors: list[str]
    supplemental_reason: str


@dataclass(frozen=True)
class FallbackScoreReasonComponents:
    score: float
    supplemental_reason: str


@dataclass(frozen=True)
class FallbackStanceComponent:
    stance: str


@dataclass(frozen=True)
class FallbackPriceLevels:
    entry_price: float | None
    stop_loss: float | None


@dataclass(frozen=True)
class FallbackRiskSignals:
    contradicting_factors: list[str]


@dataclass(frozen=True)
class ResearchPersistenceAttributionProjection:
    saved: bool
    record: dict[str, Any]
    preview: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "saved": self.saved,
            "record": dict(self.record),
            "preview": dict(self.preview),
        }


@dataclass(frozen=True)
class ResearchPersistenceProjection:
    case: dict[str, Any]
    attribution: ResearchPersistenceAttributionProjection
    calibration_report: dict[str, Any]
    identifiers: ResearchResolutionIdentifiers

    def to_detail_payload(self) -> dict[str, Any]:
        return {
            "case": dict(self.case),
            "attribution": self.attribution.to_dict(),
            "calibration_report": dict(self.calibration_report),
        }


@dataclass(frozen=True)
class ResearchResolutionContext:
    question: str
    query: str
    strategy: Any
    strategy_source: str
    code: str
    requested_as_of_date: str
    effective_as_of_date: str
    execution: dict[str, Any]
    derived: dict[str, Any]
    dashboard_projection_factory: "ResearchResolutionDashboardProjectionFactory"
    payload_factory: ResearchResolutionPayloadFactory


@dataclass(frozen=True)
class ResearchResolutionDashboardProjectionFactory:
    strategy: Any
    dashboard_projection_builder: Callable[..., dict[str, Any]]

    def build_input_spec(
        self,
        analysis: "PreDashboardAnalysisSpec",
    ) -> "DashboardProjectionSpec":
        return DashboardProjectionSpec(
            hypothesis=analysis.hypothesis,
            matched_signals=list(analysis.matched_signals),
            core_rules=list(self.strategy.core_rules),
            entry_conditions=list(self.strategy.entry_conditions),
            supplemental_reason=analysis.supplemental_reason,
        )

    def build(
        self,
        analysis: "PreDashboardAnalysisSpec",
    ) -> ResearchResolutionDashboardProjectionBundle:
        return ResearchResolutionDashboardProjectionBundle(
            input_spec=self.build_input_spec(analysis),
            dashboard_projection_builder=self.dashboard_projection_builder,
        )

    def render(self, analysis: "PreDashboardAnalysisSpec") -> dict[str, Any]:
        return self.build(analysis).render()


@dataclass(frozen=True)
class AskStockResponseRequestHeader:
    request: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        request = dict(self.request)
        return {
            "question": str(request["question"]),
            "query": str(request["query"]),
            "normalized_query": str(request["normalized_query"]),
            "as_of_date": str(request["as_of_date"]),
            "requested_as_of_date": str(request["requested_as_of_date"]),
            "request": request,
        }


@dataclass(frozen=True)
class AskStockResponseResolutionHeader:
    identifiers: dict[str, str]
    security: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        identifiers = dict(self.identifiers)
        security = dict(self.security)
        return {
            "policy_id": str(identifiers.get("policy_id") or ""),
            "research_case_id": str(identifiers.get("research_case_id") or ""),
            "attribution_id": str(identifiers.get("attribution_id") or ""),
            "identifiers": identifiers,
            "resolved": security,
            "resolved_security": security,
            "resolved_entities": {"security": dict(security)},
        }


@dataclass(frozen=True)
class AskStockResponseStrategyHeader:
    entrypoint: dict[str, Any]
    strategy_payload: dict[str, Any]
    strategy_source: str
    days: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "entrypoint": dict(self.entrypoint),
            "strategy": dict(self.strategy_payload),
            "strategy_source": self.strategy_source,
            "days": self.days,
        }


@dataclass(frozen=True)
class AskStockResponseHeaderSpec:
    request: AskStockResponseRequestHeader
    resolution: AskStockResponseResolutionHeader
    strategy: AskStockResponseStrategyHeader


@dataclass(frozen=True)
class AskStockResponseHeaderFactory:
    spec: AskStockResponseHeaderSpec

    def build(self) -> dict[str, Any]:
        return {
            **self.spec.request.to_payload(),
            **self.spec.resolution.to_payload(),
            **self.spec.strategy.to_payload(),
        }


@dataclass(frozen=True)
class ToolObservationEnvelope:
    status: str
    summary: str
    next_actions: list[str]
    artifacts: dict[str, Any]

    @classmethod
    def from_result(
        cls,
        result: Any,
        *,
        summary_keys: tuple[str, ...] = ("summary",),
    ) -> ToolObservationEnvelope:
        if not isinstance(result, dict):
            return cls(
                status="unknown",
                summary="unknown result",
                next_actions=[],
                artifacts={},
            )
        summary = ""
        for key in summary_keys:
            value = str(result.get(key) or "").strip()
            if value:
                summary = value
                break
        return cls(
            status=str(result.get("status", "ok")),
            summary=summary,
            next_actions=list(result.get("next_actions") or []),
            artifacts=dict(result.get("artifacts") or {}),
        )

    def to_dict(self, **payload: Any) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            **payload,
            "next_actions": list(self.next_actions),
            "artifacts": dict(self.artifacts),
        }


@dataclass(frozen=True)
class AskStockResearchBundle:
    dashboard: dict[str, Any]
    research_payload: dict[str, Any]
    research_bridge_payload: dict[str, Any]
    policy_id: str
    research_case_id: str
    attribution_id: str


@dataclass(frozen=True)
class AskStockPresentationBundle:
    request: dict[str, Any]
    identifiers: dict[str, str]
    task_bus: dict[str, Any]
    protocol_bundle: "AskStockProtocolBundle"
    orchestration_bundle: "AskStockOrchestrationBundle"
    sections: "AskStockSectionsBundle"


@dataclass(frozen=True)
class AskStockPresentationAssemblyBundle:
    task_bus: dict[str, Any]
    protocol_bundle: "AskStockProtocolBundle"
    sections: "AskStockSectionsBundle"


@dataclass(frozen=True)
class AskStockIdentifiersProjection:
    policy_id: str
    research_case_id: str
    attribution_id: str

    def to_payload(self) -> dict[str, str]:
        return {
            "policy_id": self.policy_id,
            "research_case_id": self.research_case_id,
            "attribution_id": self.attribution_id,
        }


@dataclass(frozen=True)
class AskStockOrchestrationExtraProjection:
    required_tools: list[str]
    recommended_plan: list[dict[str, Any]]
    tool_plan: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    step_count: int
    llm_reasoning: str
    fallback_used: bool
    gap_fill: dict[str, Any]
    coverage: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "required_tools": list(self.required_tools),
            "recommended_plan": list(self.recommended_plan),
            "tool_plan": list(self.tool_plan),
            "tool_calls": list(self.tool_calls),
            "step_count": self.step_count,
            "llm_reasoning": self.llm_reasoning,
            "fallback_used": self.fallback_used,
            "gap_fill": dict(self.gap_fill),
            "coverage": dict(self.coverage),
        }


@dataclass(frozen=True)
class AskStockSectionPayloadFactory:
    execution: dict[str, Any]
    derived: dict[str, Any]
    research_bundle: "AskStockResearchBundle"
    identifiers: dict[str, str]

    def analysis_payload(self) -> dict[str, Any]:
        return {
            "tool_results": self.execution["results"],
            "result_sequence": self.execution["result_sequence"],
            "derived_signals": self.derived,
            "research_bridge": StockAnalysisService._with_identifiers(
                dict(self.research_bundle.research_bridge_payload),
                self.identifiers,
            ),
        }

    def research_payload(self) -> dict[str, Any]:
        return StockAnalysisService._with_identifiers(
            dict(self.research_bundle.research_payload),
            self.identifiers,
        )


@dataclass(frozen=True)
class AskStockPayloadBundle:
    header_factory: AskStockResponseHeaderFactory
    task_bus: dict[str, Any]
    orchestration: dict[str, Any]
    analysis: dict[str, Any]
    research: dict[str, Any]
    dashboard: dict[str, Any]


@dataclass(frozen=True)
class AskStockExecutionRunBundle:
    recommended_plan: list[dict[str, Any]]
    allowed_tools: list[str]
    execution: dict[str, Any]
    coverage: dict[str, Any]
    derived: dict[str, Any]


@dataclass(frozen=True)
class StockExecutionPlanningBundle:
    plan: list["StockToolPlanStep"]
    recommended_plan: list[dict[str, Any]]
    allowed_tools: list[str]


@dataclass(frozen=True)
class AskStockExecutionProjection:
    mode: str
    tool_plan: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    workflow: list[dict[str, Any]]
    phase_stats: dict[str, Any]
    recommended_plan: list[dict[str, Any]]
    coverage: dict[str, Any]
    task_bus_artifacts: dict[str, Any]
    llm_reasoning: str
    fallback_used: bool
    gap_fill: dict[str, Any]
    available_tools: list[str]


@dataclass(frozen=True)
class AskStockProtocolBundle:
    protocol: dict[str, Any]
    artifacts: dict[str, Any]
    coverage: dict[str, Any]
    artifact_taxonomy: dict[str, Any]


@dataclass(frozen=True)
class AskStockOrchestrationBundle:
    mode: str
    available_tools: list[str]
    allowed_tools: list[str]
    workflow: list[dict[str, Any]]
    phase_stats: dict[str, Any]
    extra: dict[str, Any]


@dataclass(frozen=True)
class AskStockSectionsBundle:
    analysis: dict[str, Any]
    research: dict[str, Any]


@dataclass(frozen=True)
class StockExecutionTraceStep:
    step: int
    source: str
    thought: str
    tool: str
    args: dict[str, Any]
    result: dict[str, Any]
    observation: dict[str, Any]
    raw_status: str

    def as_tool_call_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "source": self.source,
            "thought": self.thought,
            "action": {"tool": self.tool, "args": dict(self.args)},
            "observation": dict(self.observation),
            "raw_status": self.raw_status,
        }

    def as_result_sequence_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "result": dict(self.result),
            "source": self.source,
        }

    def as_plan_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": dict(self.args),
            "thought": self.thought,
        }

    @classmethod
    def from_execution_payload(
        cls,
        *,
        step_number: int,
        tool_call: dict[str, Any],
        result_entry: dict[str, Any] | None,
    ) -> StockExecutionTraceStep:
        action = dict(tool_call.get("action") or {})
        return cls(
            step=int(tool_call.get("step") or step_number),
            source=str(
                tool_call.get("source") or dict(result_entry or {}).get("source") or ""
            ),
            thought=str(tool_call.get("thought") or ""),
            tool=str(action.get("tool") or dict(result_entry or {}).get("tool") or ""),
            args=dict(action.get("args") or {}),
            result=dict(dict(result_entry or {}).get("result") or {}),
            observation=dict(tool_call.get("observation") or {}),
            raw_status=str(tool_call.get("raw_status") or "ok"),
        )


@dataclass(frozen=True)
class StockExecutionResponseBundle:
    mode: str
    trace_steps: list[StockExecutionTraceStep]
    results: dict[str, Any]
    final_reasoning: str
    fallback_used: bool
    workflow: list[str]
    gap_fill: dict[str, Any]
    yaml_planned_steps: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "plan": [step.as_plan_dict() for step in self.trace_steps],
            "tool_calls": [step.as_tool_call_dict() for step in self.trace_steps],
            "results": dict(self.results),
            "result_sequence": [
                step.as_result_sequence_dict() for step in self.trace_steps
            ],
            "final_reasoning": self.final_reasoning,
            "fallback_used": self.fallback_used,
            "workflow": list(self.workflow),
            "phase_stats": self.phase_stats(),
            "gap_fill": dict(self.gap_fill),
        }

    def phase_stats(self) -> dict[str, int]:
        return {
            "llm_react_steps": len(
                [item for item in self.trace_steps if item.source == "llm_react"]
            ),
            "yaml_gap_fill_steps": len(
                [item for item in self.trace_steps if item.source == "yaml_gap_fill"]
            ),
            "yaml_planned_steps": int(self.yaml_planned_steps),
            "total_steps": len(self.trace_steps),
        }

    @classmethod
    def from_payload(cls, execution: dict[str, Any]) -> "StockExecutionResponseBundle":
        tool_calls = [
            dict(item or {}) for item in list(execution.get("tool_calls") or [])
        ]
        result_sequence = [
            dict(item or {}) for item in list(execution.get("result_sequence") or [])
        ]
        trace_steps = [
            StockExecutionTraceStep.from_execution_payload(
                step_number=index,
                tool_call=tool_call,
                result_entry=(
                    result_sequence[index - 1]
                    if index - 1 < len(result_sequence)
                    else None
                ),
            )
            for index, tool_call in enumerate(tool_calls, start=1)
        ]
        phase_stats = dict(execution.get("phase_stats") or {})
        return cls(
            mode=str(execution.get("mode") or ""),
            trace_steps=trace_steps,
            results=dict(execution.get("results") or {}),
            final_reasoning=str(execution.get("final_reasoning") or ""),
            fallback_used=bool(execution.get("fallback_used")),
            workflow=[str(item) for item in list(execution.get("workflow") or [])],
            gap_fill=dict(execution.get("gap_fill") or {}),
            yaml_planned_steps=int(phase_stats.get("yaml_planned_steps") or 0),
        )


@dataclass(frozen=True)
class StockExecutionCoverageAudit:
    required_tools: list[str]
    allowed_tools: list[str]
    planned_tools: list[str]
    executed_tools: list[str]
    missing_required_tools: list[str]
    missing_planned_steps: list[dict[str, Any]]
    out_of_policy_tools: list[str]
    planned_step_count: int
    executed_step_count: int

    def required_tool_coverage(self) -> float:
        if not self.required_tools:
            return 1.0
        return round(
            (len(self.required_tools) - len(self.missing_required_tools))
            / len(self.required_tools),
            3,
        )

    def planned_step_coverage(self) -> float:
        if not self.planned_step_count:
            return 1.0
        return round(
            (self.planned_step_count - len(self.missing_planned_steps))
            / self.planned_step_count,
            3,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "required_tools": list(self.required_tools),
            "allowed_tools": list(self.allowed_tools),
            "planned_tools": list(self.planned_tools),
            "executed_tools": list(self.executed_tools),
            "missing_required_tools": list(self.missing_required_tools),
            "missing_planned_steps": list(self.missing_planned_steps),
            "out_of_policy_tools": list(self.out_of_policy_tools),
            "required_tool_coverage": self.required_tool_coverage(),
            "planned_step_count": self.planned_step_count,
            "executed_step_count": self.executed_step_count,
            "planned_step_coverage": self.planned_step_coverage(),
        }


@dataclass(frozen=True)
class StockQueryContext:
    query: str
    code: str
    security: dict[str, Any]
    price_frame: pd.DataFrame


@dataclass(frozen=True)
class StockIndicatorProjection:
    snapshot: dict[str, Any]
    indicators: dict[str, Any]
    macd_payload: dict[str, Any]
    boll: dict[str, Any]
    projected_fields: dict[str, Any]


@dataclass(frozen=True)
class StockWindowContext:
    query_context: StockQueryContext
    frame: pd.DataFrame

    @property
    def query(self) -> str:
        return self.query_context.query

    @property
    def code(self) -> str:
        return self.query_context.code

    @property
    def security(self) -> dict[str, Any]:
        return dict(self.query_context.security)


@dataclass(frozen=True)
class StockToolCatalog:
    entries: tuple[dict[str, Any], ...]
    by_name: dict[str, dict[str, Any]]
    aliases: dict[str, str]
    definitions_by_name: dict[str, dict[str, Any]]


def _stock_tool_parameters(
    *,
    include_days: bool = True,
    minimum: int = 30,
    maximum: int = 500,
) -> dict[str, Any]:
    properties: dict[str, Any] = {"query": {"type": "string"}}
    if include_days:
        properties["days"] = {
            "type": "integer",
            "minimum": int(minimum),
            "maximum": int(maximum),
        }
    return {
        "type": "object",
        "properties": properties,
        "required": ["query"],
    }


def _stock_tool_catalog_entry(
    *,
    name: str,
    executor: str,
    description: str,
    thought: str,
    include_days: bool = True,
    minimum: int = 30,
    maximum: int = 500,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "executor": executor,
        "description": description,
        "thought": thought,
        "parameters": _stock_tool_parameters(
            include_days=include_days,
            minimum=minimum,
            maximum=maximum,
        ),
    }
    if aliases:
        payload["aliases"] = list(aliases)
    return payload


def _build_stock_tool_catalog_entries() -> tuple[dict[str, Any], ...]:
    return (
        _stock_tool_catalog_entry(
            name="get_daily_history",
            executor="get_daily_history",
            description="获取历史日线 OHLCV 数据，用于结构和趋势分析。",
            thought="先读取日线历史，建立价格结构上下文。",
        ),
        _stock_tool_catalog_entry(
            name="get_indicator_snapshot",
            executor="get_indicator_snapshot",
            description="计算均线、MACD、RSI、ATR、布林带和量比等指标快照。",
            thought="先把关键指标跑全，避免只看单一信号。",
        ),
        _stock_tool_catalog_entry(
            name="analyze_trend",
            executor="analyze_trend",
            description="分析均线、MACD、RSI、结构与趋势方向。",
            thought="结合均线、MACD 和 RSI 判断当前趋势级别。",
        ),
        _stock_tool_catalog_entry(
            name="analyze_support_resistance",
            executor="analyze_support_resistance",
            description="识别支撑阻力、距关键位距离与突破/回踩偏向。",
            thought="再确认支撑阻力和关键价格区间。",
        ),
        _stock_tool_catalog_entry(
            name="get_capital_flow",
            executor="get_capital_flow",
            description="读取本地主力资金流数据，判断净流入/流出方向。",
            thought="查看资金是否顺着当前趋势流入或流出。",
            minimum=1,
            maximum=120,
        ),
        _stock_tool_catalog_entry(
            name="get_intraday_context",
            executor="get_intraday_context",
            description="读取本地60分钟数据，确认短周期顺逆风。",
            thought="必要时用60分钟结构确认短周期是否配合。",
            minimum=1,
            maximum=20,
        ),
        _stock_tool_catalog_entry(
            name="get_realtime_quote",
            executor="get_realtime_quote",
            description="获取最新价格/最近收盘数据，用于风险位和结论确认。",
            thought="最后用最新价格校准入场位和止损位。",
            include_days=False,
            aliases=["get_latest_quote"],
        ),
    )


def _build_stock_tool_catalog() -> StockToolCatalog:
    entries = _build_stock_tool_catalog_entries()
    by_name = {str(item["name"]): dict(item) for item in entries}
    aliases: dict[str, str] = {}
    for item in entries:
        canonical_name = str(item.get("name") or "").strip()
        for alias in list(item.get("aliases") or []):
            alias_name = str(alias or "").strip()
            if alias_name:
                aliases[alias_name] = canonical_name
    definitions_by_name = {
        str(item["name"]): {
            "type": "function",
            "function": {
                "name": str(item["name"]),
                "description": str(item["description"]),
                "parameters": dict(item["parameters"]),
            },
        }
        for item in entries
    }
    return StockToolCatalog(
        entries=entries,
        by_name=by_name,
        aliases=aliases,
        definitions_by_name=definitions_by_name,
    )


def _build_indicator_projection(
    snapshot: dict[str, Any] | None,
    *,
    summary: dict[str, Any] | None = None,
    trend_metrics: dict[str, Any] | None = None,
    quote_row: dict[str, Any] | None = None,
) -> StockIndicatorProjection:
    projected_fields = _project_snapshot_fields(
        dict(snapshot or {}),
        summary=dict(summary or {}),
        trend_metrics=dict(trend_metrics or {}),
        quote_row=dict(quote_row or {}),
    )
    indicators = dict(projected_fields["indicators"])
    return StockIndicatorProjection(
        snapshot=dict(projected_fields["snapshot"]),
        indicators=indicators,
        macd_payload=dict(projected_fields["macd_payload"]),
        boll=dict(projected_fields["boll"]),
        projected_fields=projected_fields,
    )


# Execution planning helpers
class StockAnalysisExecutionMixin:
    # Declared here so the extracted execution helpers stay type-safe when mixed
    # back into StockAnalysisService.
    _tool_catalog: StockToolCatalog
    _tool_registry: dict[str, Callable[..., dict[str, Any]]]
    gateway: Any

    def _stock_tool_aliases(self) -> dict[str, str]:
        return dict(self._tool_catalog.aliases)

    def _normalize_tool_name(self, tool_name: str) -> str:
        raw = str(tool_name or "").strip()
        return self._stock_tool_aliases().get(raw, raw)

    def _action_signature(self, tool_name: str, args: dict[str, Any]) -> str:
        payload = {
            "tool": self._normalize_tool_name(tool_name),
            "args": dict(args or {}),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _build_plan(
        self, *, strategy: StockAnalysisStrategy, query: str, days: int
    ) -> list[StockToolPlanStep]:
        plan = strategy.tool_call_plan or []
        if plan:
            rendered: list[StockToolPlanStep] = []
            for step in plan:
                rendered.append(
                    StockToolPlanStep(
                        tool=step.tool,
                        thought=step.thought,
                        args=self._render_template_args(
                            step.args, query=query, days=days
                        ),
                    )
                )
            return rendered

        derived: list[StockToolPlanStep] = []
        for tool in strategy.required_tools:
            if tool == "get_daily_history":
                derived.append(
                    StockToolPlanStep(
                        tool=tool,
                        thought="先拉取历史K线，建立价格结构基础。",
                        args={"query": query, "days": days},
                    )
                )
            elif tool == "analyze_trend":
                derived.append(
                    StockToolPlanStep(
                        tool=tool,
                        thought="继续计算趋势、均线与动量信号。",
                        args={"query": query, "days": max(120, days)},
                    )
                )
            elif tool in {"get_realtime_quote", "get_latest_quote"}:
                derived.append(
                    StockToolPlanStep(
                        tool="get_realtime_quote",
                        thought="最后确认最新收盘/报价。",
                        args={"query": query},
                    )
                )
        return derived

    def _build_trace_step(
        self,
        *,
        step_number: int,
        source: str,
        tool_name: str,
        args: dict[str, Any],
        thought: str,
        result: dict[str, Any],
    ) -> StockExecutionTraceStep:
        return StockExecutionTraceStep(
            step=step_number,
            source=source,
            thought=thought or self._default_thought(tool_name),
            tool=tool_name,
            args=dict(args),
            result=dict(result),
            observation=self._summarize_observation(tool_name, result),
            raw_status=str(result.get("status", "ok"))
            if isinstance(result, dict)
            else "ok",
        )

    def _record_execution_step(
        self,
        *,
        trace_steps: list[StockExecutionTraceStep],
        results: dict[str, Any],
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        source: str,
        thought: str,
    ) -> None:
        results[tool_name] = result
        trace_steps.append(
            self._build_trace_step(
                step_number=len(trace_steps) + 1,
                source=source,
                tool_name=tool_name,
                args=args,
                thought=thought,
                result=result,
            ),
        )

    def _execute_tool_step(
        self,
        *,
        trace_steps: list[StockExecutionTraceStep],
        results: dict[str, Any],
        tool_name: str,
        args: dict[str, Any],
        source: str,
        thought: str,
    ) -> dict[str, Any]:
        result = self._run_stock_tool(tool_name, args)
        self._record_execution_step(
            trace_steps=trace_steps,
            results=results,
            tool_name=tool_name,
            args=args,
            result=result,
            source=source,
            thought=thought,
        )
        return result

    @staticmethod
    def _build_execution_bundle(
        *,
        mode: str,
        trace_steps: list[StockExecutionTraceStep],
        results: dict[str, Any],
        final_reasoning: str,
        fallback_used: bool,
        workflow: list[str],
        gap_fill: dict[str, Any],
        yaml_planned_steps: int,
    ) -> StockExecutionResponseBundle:
        return StockExecutionResponseBundle(
            mode=mode,
            trace_steps=list(trace_steps),
            results=dict(results),
            final_reasoning=final_reasoning,
            fallback_used=fallback_used,
            workflow=list(workflow),
            gap_fill=dict(gap_fill),
            yaml_planned_steps=yaml_planned_steps,
        )

    @staticmethod
    def _execution_bundle_from_payload(
        execution: dict[str, Any],
    ) -> StockExecutionResponseBundle:
        return StockExecutionResponseBundle.from_payload(execution)

    def _build_execution_planning_bundle(
        self,
        *,
        strategy: StockAnalysisStrategy,
        query: str,
        days: int,
    ) -> StockExecutionPlanningBundle:
        plan = self._build_plan(strategy=strategy, query=query, days=days)
        return StockExecutionPlanningBundle(
            plan=plan,
            recommended_plan=[step.to_dict() for step in plan],
            allowed_tools=self._strategy_allowed_tools(strategy),
        )

    @classmethod
    def _trace_steps_from_execution(
        cls,
        execution: dict[str, Any],
    ) -> list[StockExecutionTraceStep]:
        return list(cls._execution_bundle_from_payload(execution).trace_steps)

    @staticmethod
    def _build_gap_fill_state(
        *,
        enabled: bool,
        applied: bool,
        reason: str,
        missing_plan_steps_before_fill: list[dict[str, Any]] | None = None,
        filled_steps: list[dict[str, Any]] | None = None,
        missing_plan_steps_after_fill: list[dict[str, Any]] | None = None,
        required_tool_coverage_before_fill: float | None = None,
        required_tool_coverage_after_fill: float | None = None,
        planned_step_coverage_before_fill: float | None = None,
        planned_step_coverage_after_fill: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "enabled": enabled,
            "applied": applied,
            "reason": reason,
            "missing_plan_steps_before_fill": list(
                missing_plan_steps_before_fill or []
            ),
            "filled_steps": list(filled_steps or []),
            "missing_plan_steps_after_fill": list(missing_plan_steps_after_fill or []),
        }
        if required_tool_coverage_before_fill is not None:
            payload["required_tool_coverage_before_fill"] = (
                required_tool_coverage_before_fill
            )
        if required_tool_coverage_after_fill is not None:
            payload["required_tool_coverage_after_fill"] = (
                required_tool_coverage_after_fill
            )
        if planned_step_coverage_before_fill is not None:
            payload["planned_step_coverage_before_fill"] = (
                planned_step_coverage_before_fill
            )
        if planned_step_coverage_after_fill is not None:
            payload["planned_step_coverage_after_fill"] = (
                planned_step_coverage_after_fill
            )
        return payload

    def _build_execution_response(
        self,
        *,
        mode: str,
        trace_steps: list[StockExecutionTraceStep],
        results: dict[str, Any],
        final_reasoning: str,
        fallback_used: bool,
        workflow: list[str],
        gap_fill: dict[str, Any],
        yaml_planned_steps: int,
    ) -> dict[str, Any]:
        return self._build_execution_bundle(
            mode=mode,
            trace_steps=trace_steps,
            results=results,
            final_reasoning=final_reasoning,
            fallback_used=fallback_used,
            workflow=workflow,
            gap_fill=gap_fill,
            yaml_planned_steps=yaml_planned_steps,
        ).to_payload()

    def _strategy_allowed_tools(self, strategy: StockAnalysisStrategy) -> list[str]:
        allowed: list[str] = []
        seen: set[str] = set()
        for step in list(strategy.tool_call_plan or []):
            tool_name = str(step.tool or "").strip()
            if tool_name and tool_name in self._tool_registry and tool_name not in seen:
                allowed.append(tool_name)
                seen.add(tool_name)
        for tool_name in list(strategy.required_tools or []):
            normalized = (
                "get_realtime_quote"
                if tool_name == "get_latest_quote"
                else str(tool_name or "").strip()
            )
            if (
                normalized
                and normalized in self._tool_registry
                and normalized not in seen
            ):
                allowed.append(normalized)
                seen.add(normalized)
        return allowed

    def _normalize_plan_step(self, item: dict[str, Any]) -> dict[str, Any]:
        step = dict(item or {})
        tool_name = self._normalize_tool_name(str(step.get("tool") or ""))
        return {
            "tool": tool_name,
            "args": dict(step.get("args") or {}),
            "thought": str(step.get("thought") or self._default_thought(tool_name)),
        }

    def _normalized_plan_steps(
        self, recommended_plan: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [self._normalize_plan_step(item) for item in list(recommended_plan or [])]

    def _executed_tool_state(
        self, execution: dict[str, Any]
    ) -> tuple[list[str], set[str]]:
        executed_tools: list[str] = []
        executed_signatures: set[str] = set()
        for item in list(execution.get("tool_calls") or []):
            action = dict(item.get("action") or {})
            tool_name = self._normalize_tool_name(str(action.get("tool") or ""))
            args = dict(action.get("args") or {})
            if tool_name:
                executed_tools.append(tool_name)
                executed_signatures.add(self._action_signature(tool_name, args))
        return list(dict.fromkeys(executed_tools)), executed_signatures

    def _build_execution_coverage_audit(
        self,
        *,
        required_tools: list[str],
        allowed_tools: list[str],
        execution: dict[str, Any],
        recommended_plan: list[dict[str, Any]],
    ) -> StockExecutionCoverageAudit:
        normalized_plan = self._normalized_plan_steps(recommended_plan)
        planned_tools = list(
            dict.fromkeys(
                [str(step.get("tool") or "") for step in normalized_plan if step.get("tool")]
            )
        )
        executed_tools, executed_signatures = self._executed_tool_state(execution)
        missing_planned_steps = [
            step
            for step in normalized_plan
            if self._action_signature(
                str(step.get("tool") or ""), dict(step.get("args") or {})
            )
            not in executed_signatures
        ]
        return StockExecutionCoverageAudit(
            required_tools=list(required_tools),
            allowed_tools=list(allowed_tools),
            planned_tools=planned_tools,
            executed_tools=executed_tools,
            missing_required_tools=[
                tool for tool in required_tools if tool not in executed_tools
            ],
            missing_planned_steps=missing_planned_steps,
            out_of_policy_tools=[
                tool for tool in executed_tools if tool not in allowed_tools
            ],
            planned_step_count=len(normalized_plan),
            executed_step_count=len(list(execution.get("tool_calls") or [])),
        )

    def _missing_plan_steps(
        self,
        *,
        execution: dict[str, Any],
        recommended_plan: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return self._build_execution_coverage_audit(
            required_tools=[],
            allowed_tools=[],
            execution=execution,
            recommended_plan=recommended_plan,
        ).missing_planned_steps

    def _build_execution_coverage(
        self,
        *,
        strategy: StockAnalysisStrategy,
        execution: dict[str, Any],
        recommended_plan: list[dict[str, Any]],
        allowed_tools: list[str],
    ) -> dict[str, Any]:
        return self._build_execution_coverage_audit(
            required_tools=self._strategy_allowed_tools(strategy),
            allowed_tools=allowed_tools,
            execution=execution,
            recommended_plan=recommended_plan,
        ).to_payload()

    def _execute_plan_deterministic(
        self, *, plan: list[StockToolPlanStep], fallback_reason: str = ""
    ) -> dict[str, Any]:
        trace_steps: list[StockExecutionTraceStep] = []
        results: dict[str, Any] = {}
        for step in plan:
            self._execute_tool_step(
                trace_steps=trace_steps,
                results=results,
                tool_name=step.tool,
                args=step.args,
                source="yaml_plan",
                thought=step.thought,
            )
        return self._build_execution_response(
            mode="yaml_react_like",
            trace_steps=trace_steps,
            results=results,
            final_reasoning="",
            fallback_used=True,
            workflow=["yaml_strategy_loaded", "yaml_plan_execute", "finalize"],
            gap_fill=self._build_gap_fill_state(
                enabled=False,
                applied=False,
                reason=fallback_reason or "yaml_only_mode",
            ),
            yaml_planned_steps=len(plan),
        )

    def _execute_plan_with_llm(
        self,
        *,
        question: str,
        query: str,
        security: dict[str, Any],
        strategy: StockAnalysisStrategy,
        days: int,
        allowed_tools: list[str],
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._stock_system_prompt(strategy)},
            {
                "role": "user",
                "content": self._build_stock_user_prompt(
                    question=question,
                    query=query,
                    security=security,
                    strategy=strategy,
                    days=days,
                ),
            },
        ]
        tool_defs = self._stock_tool_definitions(allowed_tools=allowed_tools)
        trace_steps: list[StockExecutionTraceStep] = []
        results: dict[str, Any] = {}
        final_reasoning = ""
        max_steps = max(1, min(8, int(strategy.max_steps or 4)))
        for _ in range(max_steps):
            try:
                response = self.gateway.completion_raw(
                    messages=messages,
                    temperature=0.2,
                    max_tokens=1400,
                    tools=tool_defs,
                    tool_choice="auto",
                )
            except (LLMUnavailableError, LLMGatewayError) as exc:
                logger.warning("stock llm planner unavailable: %s", exc)
                raise
            choice = response.choices[0].message
            llm_tool_calls = getattr(choice, "tool_calls", None) or []
            if llm_tool_calls:
                messages.append(
                    self._build_llm_assistant_tool_message(
                        choice=choice, tool_calls=llm_tool_calls
                    )
                )
                for tc in llm_tool_calls:
                    args = self._parse_tool_args(tc.function.arguments)
                    result = self._execute_tool_step(
                        trace_steps=trace_steps,
                        results=results,
                        tool_name=tc.function.name,
                        args=args,
                        source="llm_react",
                        thought=(getattr(choice, "content", "") or "").strip(),
                    )
                    messages.append(
                        self._build_llm_tool_result_message(
                            tool_call_id=tc.id,
                            tool_name=tc.function.name,
                            result=result,
                        )
                    )
                continue
            final_reasoning = (getattr(choice, "content", "") or "").strip()
            break

        if not trace_steps:
            raise RuntimeError("llm react produced no tool calls")
        return self._build_execution_response(
            mode="llm_react",
            trace_steps=trace_steps,
            results=results,
            final_reasoning=final_reasoning,
            fallback_used=False,
            workflow=["yaml_strategy_loaded", "llm_react", "gap_audit_pending"],
            gap_fill=self._build_gap_fill_state(
                enabled=True,
                applied=False,
                reason="awaiting_gap_audit",
            ),
            yaml_planned_steps=0,
        )

    def _apply_yaml_gap_fill(
        self,
        *,
        execution: dict[str, Any],
        strategy: StockAnalysisStrategy,
        plan: list[StockToolPlanStep],
        allowed_tools: list[str],
    ) -> dict[str, Any]:
        recommended_plan = [step.to_dict() for step in plan]
        pre_coverage = self._build_execution_coverage(
            strategy=strategy,
            execution=execution,
            recommended_plan=recommended_plan,
            allowed_tools=allowed_tools,
        )
        missing_steps = self._missing_plan_steps(
            execution=execution, recommended_plan=recommended_plan
        )
        execution_bundle = self._execution_bundle_from_payload(execution)
        trace_steps = list(execution_bundle.trace_steps)
        results = dict(execution_bundle.results)
        filled_steps: list[dict[str, Any]] = []
        for step in missing_steps:
            tool_name = self._normalize_tool_name(str(step.get("tool") or ""))
            args = dict(step.get("args") or {})
            filled_step = {
                "tool": tool_name,
                "args": args,
                "thought": str(step.get("thought") or self._default_thought(tool_name)),
            }
            filled_steps.append(filled_step)
            self._execute_tool_step(
                trace_steps=trace_steps,
                results=results,
                tool_name=tool_name,
                args=args,
                source="yaml_gap_fill",
                thought=filled_step["thought"],
            )
        merged = self._build_execution_response(
            mode="llm_react_yaml_hybrid",
            trace_steps=trace_steps,
            results=results,
            final_reasoning=execution_bundle.final_reasoning,
            fallback_used=False,
            workflow=[
                "yaml_strategy_loaded",
                "llm_react",
                "gap_audit",
                "yaml_gap_fill" if filled_steps else "yaml_gap_fill_skipped",
                "finalize",
            ],
            gap_fill={},
            yaml_planned_steps=len(recommended_plan),
        )
        post_coverage = self._build_execution_coverage(
            strategy=strategy,
            execution=merged,
            recommended_plan=recommended_plan,
            allowed_tools=allowed_tools,
        )
        merged["gap_fill"] = self._build_gap_fill_state(
            enabled=True,
            applied=bool(filled_steps),
            reason="missing_yaml_steps_detected"
            if filled_steps
            else "llm_already_satisfied_yaml_plan",
            missing_plan_steps_before_fill=pre_coverage.get(
                "missing_planned_steps", []
            ),
            filled_steps=filled_steps,
            missing_plan_steps_after_fill=post_coverage.get(
                "missing_planned_steps", []
            ),
            required_tool_coverage_before_fill=pre_coverage.get(
                "required_tool_coverage", 0.0
            ),
            required_tool_coverage_after_fill=post_coverage.get(
                "required_tool_coverage", 0.0
            ),
            planned_step_coverage_before_fill=pre_coverage.get(
                "planned_step_coverage", 0.0
            ),
            planned_step_coverage_after_fill=post_coverage.get(
                "planned_step_coverage", 0.0
            ),
        )
        return merged

    def _run_stock_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        executor = self._tool_registry.get(tool_name)
        if executor is None:
            return {
                "status": "error",
                "summary": f"未知工具 {tool_name}",
                "next_actions": ["只使用可用的 stock tools"],
                "artifacts": {},
            }
        try:
            return executor(**args)
        except _STOCK_TOOL_EXECUTION_EXCEPTIONS as exc:
            return {
                "status": "error",
                "summary": f"{tool_name} 执行失败: {exc}",
                "next_actions": ["检查参数或换用其它工具"],
                "artifacts": {},
            }

    @staticmethod
    def _humanize_macd_cross(cross: str) -> str:
        mapping = {
            "golden_cross": "金叉",
            "dead_cross": "死叉",
            "bullish": "看多",
            "bearish": "看空",
            "neutral": "中性",
        }
        return mapping.get(str(cross or "neutral"), "中性")

    def _derive_signals(self, execution: dict[str, Any]) -> dict[str, Any]:
        history = dict(execution["results"].get("get_daily_history") or {})
        trend = dict(execution["results"].get("analyze_trend") or {})
        quote = dict(
            execution["results"].get("get_realtime_quote")
            or execution["results"].get("get_latest_quote")
            or {}
        )
        indicator_result = dict(
            execution["results"].get("get_indicator_snapshot") or {}
        )
        support_result = dict(
            execution["results"].get("analyze_support_resistance") or {}
        )
        capital_flow = dict(execution["results"].get("get_capital_flow") or {})
        summary: dict[str, Any] = (
            dict(trend.get("summary") or {})
            if isinstance(trend.get("summary"), dict)
            else {}
        )
        trend_metrics: dict[str, Any] = (
            dict(trend.get("trend") or {})
            if isinstance(trend.get("trend"), dict)
            else {}
        )
        quote_row: dict[str, Any] = (
            dict(quote.get("quote") or {})
            if isinstance(quote.get("quote"), dict)
            else {}
        )
        snapshot_payload: dict[str, Any] = (
            dict(indicator_result.get("snapshot") or {})
            if isinstance(indicator_result.get("snapshot"), dict)
            else {}
        )
        support_levels: dict[str, Any] = (
            dict(support_result.get("levels") or {})
            if isinstance(support_result.get("levels"), dict)
            else {}
        )
        flow_metrics: dict[str, Any] = (
            dict(capital_flow.get("metrics") or {})
            if isinstance(capital_flow.get("metrics"), dict)
            else {}
        )
        indicator_projection = _build_indicator_projection(
            snapshot_payload,
            summary=summary,
            trend_metrics=trend_metrics,
            quote_row=quote_row,
        )
        boll = dict(indicator_projection.boll)
        projected = indicator_projection.projected_fields
        latest_close = float(projected["latest_close"] or 0.0)
        ma20 = float(projected["ma20"] or 0.0)
        volume_ratio = projected["volume_ratio"]
        macd_cross = str(projected["macd_cross"] or "neutral")
        rsi = float(projected["rsi"] or 50.0)
        algo_score = float(summary.get("algo_score") or 0.0)
        ma_stack = str(projected["ma_stack"] or "mixed")
        main_net_inflow_sum = float(flow_metrics.get("main_net_inflow_sum") or 0.0)
        distance_to_support_pct = float(
            support_levels.get("distance_to_support_pct") or 0.0
        )
        distance_to_resistance_pct = float(
            support_levels.get("distance_to_resistance_pct") or 0.0
        )
        flags = {
            "多头排列": ma_stack == "bullish",
            "空头排列": ma_stack == "bearish",
            "MACD金叉": macd_cross == "golden_cross",
            "MACD死叉": macd_cross == "dead_cross",
            "RSI超卖": rsi < 30,
            "RSI超买": rsi > 70,
            "价格站上MA20": latest_close > ma20 if ma20 else False,
            "跌破MA20": latest_close < ma20 if ma20 else False,
            "量比放大": volume_ratio is not None and float(volume_ratio) >= 1.1,
            "趋势向上": trend.get("signal") == "bullish",
            "趋势向下": trend.get("signal") == "bearish",
            "结构未破坏": trend.get("structure") in {"uptrend", "range"},
            "结构走弱": trend.get("structure") == "downtrend",
            "资金净流入": main_net_inflow_sum > 0,
            "接近支撑": 0.0 <= distance_to_support_pct <= 3.0,
            "逼近阻力": 0.0 <= distance_to_resistance_pct <= 3.0,
            "布林下轨附近": float(boll.get("position") or 0.5) <= 0.2,
            "布林上轨附近": float(boll.get("position") or 0.5) >= 0.8,
        }
        matched_signals = [name for name, ok in flags.items() if ok]
        return {
            "history_count": int(history.get("count") or 0),
            "latest_close": round(latest_close, 2) if latest_close else None,
            "ma20": round(ma20, 2) if ma20 else None,
            "volume_ratio": round(float(volume_ratio), 3)
            if volume_ratio is not None
            else None,
            "macd": self._humanize_macd_cross(macd_cross),
            "macd_cross": macd_cross,
            "rsi": round(rsi, 2),
            "algo_score": round(algo_score, 3),
            "ma_trend": "多头"
            if ma_stack == "bullish"
            else "空头"
            if ma_stack == "bearish"
            else "交叉",
            "ma_stack": ma_stack,
            "trend_signal": trend.get("signal") or "observe",
            "structure": trend.get("structure") or "range",
            "support_20": support_levels.get("support_20"),
            "resistance_20": support_levels.get("resistance_20"),
            "distance_to_support_pct": round(distance_to_support_pct, 2),
            "distance_to_resistance_pct": round(distance_to_resistance_pct, 2),
            "main_net_inflow_sum": round(main_net_inflow_sum, 2),
            "bollinger_position": boll.get("position"),
            "flags": flags,
            "matched_signals": matched_signals,
        }

    @staticmethod
    def _render_template_args(
        payload: dict[str, Any], *, query: str, days: int
    ) -> dict[str, Any]:
        def render(value: Any) -> Any:
            if isinstance(value, str):
                return (
                    value.replace("{{query}}", query)
                    .replace("{{days}}", str(days))
                    .replace("{{history_days}}", str(days))
                    .replace("{{trend_days}}", str(max(120, days)))
                )
            if isinstance(value, dict):
                return {k: render(v) for k, v in value.items()}
            if isinstance(value, list):
                return [render(v) for v in value]
            return value

        rendered = render(payload)
        for key, value in list(rendered.items()):
            if isinstance(value, str) and value.isdigit():
                rendered[key] = int(value)
        return rendered

    @staticmethod
    def _parse_tool_args(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if raw is None:
            return {}
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return {}
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("tool arguments must decode to a JSON object")
            return parsed
        raise ValueError("tool arguments must be a JSON object or JSON string")

    def _stock_tool_catalog_entries(self) -> tuple[dict[str, Any], ...]:
        return self._tool_catalog.entries

    def _stock_tool_catalog_by_name(self) -> dict[str, dict[str, Any]]:
        return dict(self._tool_catalog.by_name)

    def _stock_tool_definitions(
        self, allowed_tools: list[str] | None = None
    ) -> list[dict[str, Any]]:
        catalog = [
            dict(item) for item in self._tool_catalog.definitions_by_name.values()
        ]
        if not allowed_tools:
            return catalog
        allowed = {
            str(name or "").strip() for name in allowed_tools if str(name or "").strip()
        }
        return [
            item
            for item in catalog
            if str(dict(item.get("function") or {}).get("name") or "") in allowed
        ]

    def _default_thought(self, tool_name: str) -> str:
        metadata = self._stock_tool_catalog_by_name().get(
            self._normalize_tool_name(tool_name), {}
        )
        return str(
            metadata.get("thought") or f"调用 {tool_name} 获取下一步分析所需信息。"
        )

    @staticmethod
    def _build_llm_assistant_tool_message(
        *, choice: Any, tool_calls: list[Any]
    ) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": getattr(choice, "content", "") or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        }

    @staticmethod
    def _build_llm_tool_result_message(
        *,
        tool_call_id: str,
        tool_name: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": json.dumps(result, ensure_ascii=False),
        }

    @staticmethod
    def _observation_envelope(
        result: Any,
        *,
        summary_keys: tuple[str, ...] = ("summary",),
    ) -> ToolObservationEnvelope:
        return ToolObservationEnvelope.from_result(result, summary_keys=summary_keys)

    @staticmethod
    def _observation_section(result: dict[str, Any], key: str) -> dict[str, Any]:
        payload = result.get(key)
        return dict(payload or {}) if isinstance(payload, dict) else {}

    @classmethod
    def _project_tool_observation(
        cls,
        result: dict[str, Any],
        *,
        summary_keys: tuple[str, ...] = ("summary",),
        **payload: Any,
    ) -> dict[str, Any]:
        return cls._observation_envelope(
            result,
            summary_keys=summary_keys,
        ).to_dict(**payload)

    @staticmethod
    def _summarize_observation(
        tool_name: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        envelope = StockAnalysisExecutionMixin._observation_envelope(result)
        if not isinstance(result, dict):
            return envelope.to_dict()
        if tool_name == "get_daily_history":
            items = list(result.get("items") or [])
            last_trade_date = items[-1].get("trade_date") if items else None
            return envelope.to_dict(
                count=int(result.get("count") or len(items)),
                last_trade_date=last_trade_date,
            )
        if tool_name == "get_indicator_snapshot":
            snapshot_payload = StockAnalysisExecutionMixin._observation_section(
                result,
                "snapshot",
            )
            indicator_projection = _build_indicator_projection(snapshot_payload)
            return StockAnalysisExecutionMixin._project_tool_observation(
                result,
                summary_keys=("observation_summary", "summary"),
                latest_close=indicator_projection.snapshot.get("latest_close"),
                rsi_14=indicator_projection.indicators.get("rsi_14"),
                ma_stack=indicator_projection.indicators.get("ma_stack"),
                macd_cross=indicator_projection.macd_payload.get("cross"),
            )
        if tool_name == "analyze_trend":
            trend = StockAnalysisExecutionMixin._observation_section(result, "trend")
            return StockAnalysisExecutionMixin._project_tool_observation(
                result,
                summary_keys=("observation_summary", "summary"),
                signal=result.get("signal"),
                structure=result.get("structure"),
                latest_close=trend.get("latest_close"),
                ma20=trend.get("ma20"),
                volume_ratio=trend.get("volume_ratio"),
                macd_cross=trend.get("macd_cross"),
            )
        if tool_name == "analyze_support_resistance":
            levels = StockAnalysisExecutionMixin._observation_section(result, "levels")
            return StockAnalysisExecutionMixin._project_tool_observation(
                result,
                summary_keys=("observation_summary", "summary"),
                support_20=levels.get("support_20"),
                resistance_20=levels.get("resistance_20"),
                bias=levels.get("bias"),
            )
        if tool_name == "get_capital_flow":
            metrics = StockAnalysisExecutionMixin._observation_section(
                result,
                "metrics",
            )
            return StockAnalysisExecutionMixin._project_tool_observation(
                result,
                summary_keys=("observation_summary", "summary"),
                direction=metrics.get("direction"),
                main_net_inflow_sum=metrics.get("main_net_inflow_sum"),
            )
        if tool_name == "get_intraday_context":
            metrics = StockAnalysisExecutionMixin._observation_section(
                result,
                "metrics",
            )
            return StockAnalysisExecutionMixin._project_tool_observation(
                result,
                summary_keys=("observation_summary", "summary"),
                intraday_bias=metrics.get("intraday_bias"),
                latest_trade_date=metrics.get("latest_trade_date"),
            )
        if tool_name in {"get_realtime_quote", "get_latest_quote"}:
            quote = StockAnalysisExecutionMixin._observation_section(result, "quote")
            return envelope.to_dict(
                close=quote.get("close"),
                trade_date=quote.get("trade_date"),
            )
        return envelope.to_dict()

    def _stock_system_prompt(self, strategy: StockAnalysisStrategy) -> str:
        return (
            "You are a bounded stock-analysis planning agent. "
            "The tool catalog is restricted by the strategy YAML and defines your hard boundary. "
            "Use the provided tools to gather evidence before concluding. "
            "Prefer the strategy's required tools, stay close to the YAML workflow, and stop once the evidence is sufficient. "
            "If you already have enough evidence, return a short final reasoning summary. "
            "Do not invent market data or tool results."
        )

    def _build_stock_user_prompt(
        self,
        *,
        question: str,
        query: str,
        security: dict[str, Any],
        strategy: StockAnalysisStrategy,
        days: int,
    ) -> str:
        return (
            f"User question: {question}\n"
            f"Target security: {security.get('name') or ''} ({query})\n"
            f"Strategy: {strategy.display_name} / {strategy.name}\n"
            f"Description: {strategy.description}\n"
            f"Required tools: {', '.join(strategy.required_tools)}\n"
            f"Analysis steps: {' -> '.join(strategy.analysis_steps)}\n"
            f"Core rules: {'; '.join(strategy.core_rules)}\n"
            f"Entry conditions: {'; '.join(strategy.entry_conditions)}\n"
            f"Scoring rules: {json.dumps(strategy.scoring, ensure_ascii=False)}\n"
            f"Recommended lookback days: {days}\n"
            f"Suggested planner prompt: {strategy.planner_prompt}\n"
            "First decide the next most useful tool call."
        )


# Research bridge owners
class StockAnalysisResearchBridgeService:
    def __init__(
        self,
        *,
        repository: Any,
        controller_provider: Callable[[], Any] | None,
        research_resolution_service: Any,
        governance_service: TrainingGovernanceService,
        normalize_as_of_date: Callable[[str | None], str],
        resolve_effective_as_of_date: Callable[[str, str], str],
        logger_instance: Any,
    ) -> None:
        self.repository = repository
        self._controller_provider = controller_provider
        self.research_resolution_service = research_resolution_service
        self.governance_service = governance_service
        self.normalize_as_of_date = normalize_as_of_date
        self.resolve_effective_as_of_date = resolve_effective_as_of_date
        self._logger = logger_instance

    def resolve_outputs(
        self,
        *,
        question: str,
        query: str,
        strategy: Any,
        strategy_source: str,
        code: str,
        security: dict[str, Any],
        requested_as_of_date: str,
        effective_as_of_date: str,
        days: int,
        execution: dict[str, Any],
        derived: dict[str, Any],
        dashboard_projection_builder: Callable[
            ..., dict[str, Any]
        ] = build_dashboard_projection,
    ) -> dict[str, Any]:
        research_bridge = self.build_research_bridge(
            code=code,
            security=security,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
            days=days,
            derived=derived,
        )
        return self.research_resolution_service.resolve_outputs(
            research_bridge=research_bridge,
            question=question,
            query=query,
            strategy=strategy,
            strategy_source=strategy_source,
            code=code,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
            execution=execution,
            derived=derived,
            dashboard_projection_builder=dashboard_projection_builder,
        )

    def _resolve_live_controller(self) -> Any | None:
        if self._controller_provider is None:
            return None
        try:
            return self._controller_provider()
        except _STOCK_RESEARCH_BRIDGE_EXCEPTIONS:
            self._logger.warning(
                "Failed to resolve live controller for ask_stock research bridge",
                exc_info=True,
            )
            return None

    def _ensure_query_in_stock_data(
        self,
        *,
        stock_data: dict[str, Any],
        code: str,
        cutoff_date: str,
    ) -> dict[str, Any]:
        enriched = dict(stock_data or {})
        if code in enriched:
            return enriched
        query_frame = self.repository.get_stock(code, cutoff_date=cutoff_date)
        if query_frame.empty:
            return enriched
        query_frame = query_frame.copy()
        if "trade_date" in query_frame.columns:
            query_frame["trade_date"] = query_frame["trade_date"].astype(str)
            query_frame = query_frame.sort_values("trade_date").reset_index(drop=True)
        enriched[code] = query_frame
        return enriched

    def _build_unavailable_bridge(
        self,
        *,
        stage: str,
        error: str,
        effective_as_of_date: str,
        parameter_source: str = "",
        **details: Any,
    ) -> dict[str, Any]:
        detail_payload: dict[str, Any] = {
            "stage": stage,
            "as_of_date": effective_as_of_date,
        }
        if parameter_source:
            detail_payload["parameter_source"] = parameter_source
        detail_payload.update(details)
        return {
            "status": "unavailable",
            "error": error,
            "details": detail_payload,
        }

    def _resolve_bridge_stage(
        self,
        *,
        result: ResearchBridgeStageResult,
        stage: str,
        error: str,
        effective_as_of_date: str,
        parameter_source: str,
    ) -> Any | dict[str, Any]:
        if result.unavailable is not None:
            return result.unavailable
        if result.bundle is not None:
            return result.bundle
        return self._build_unavailable_bridge(
            stage=stage,
            error=error,
            effective_as_of_date=effective_as_of_date,
            parameter_source=parameter_source,
        )

    def _resolve_bridge_runtime_context(
        self,
        *,
        controller: Any,
        code: str,
        requested_as_of_date: str,
        effective_as_of_date: str,
        days: int,
    ) -> ResearchBridgeRuntimeContext:
        normalized_requested_as_of_date = self.normalize_as_of_date(
            requested_as_of_date
        )
        latest_live_date = self.resolve_effective_as_of_date(code, "")
        replay_mode = (
            bool(normalized_requested_as_of_date)
            and bool(latest_live_date)
            and str(effective_as_of_date) < str(latest_live_date)
        )
        current_manager_id = str(
            controller_default_manager_id(
                controller,
                default=str(
                    getattr(config, "default_manager_id", "momentum") or "momentum"
                ),
            )
            or "momentum"
        )
        fallback_config_path = resolve_manager_config_ref(
            current_manager_id,
            getattr(config, "default_manager_config_ref", ""),
        )
        base_config_path = str(
            controller_default_manager_config_ref(controller)
            or fallback_config_path
            or ""
        )
        query_history_frame = self.repository.get_stock(
            code, cutoff_date=effective_as_of_date
        )
        query_history_days = int(len(query_history_frame))
        return ResearchBridgeRuntimeContext(
            normalized_requested_as_of_date=normalized_requested_as_of_date,
            replay_mode=replay_mode,
            current_manager_id=current_manager_id,
            base_config_path=normalize_path_ref(base_config_path)
            or fallback_config_path,
            current_params={}
            if replay_mode
            else dict(getattr(controller, "current_params", {}) or {}),
            stock_count=max(10, int(getattr(config, "max_stocks", 50) or 50)),
            min_history_days=max(
                30,
                min(
                    60,
                    query_history_days if query_history_days > 0 else int(days or 60),
                ),
            ),
            lookback_days=max(60, int(days or 60)),
            parameter_source=(
                "config_default_replay_safe"
                if replay_mode
                else "live_controller"
                if controller is not None
                else "config_default"
            ),
        )

    def _load_bridge_stock_data(
        self,
        *,
        code: str,
        effective_as_of_date: str,
        stock_count: int,
        min_history_days: int,
        parameter_source: str,
    ) -> ResearchBridgeStageResult:
        data_manager = DataManager(
            db_path=str(self.repository.db_path),
            prefer_offline=True,
            allow_mock_fallback=False,
        )
        try:
            stock_data = data_manager.load_stock_data(
                cutoff_date=effective_as_of_date,
                stock_count=stock_count,
                min_history_days=min_history_days,
                include_capital_flow=False,
            )
        except _STOCK_RESEARCH_BRIDGE_EXCEPTIONS as exc:
            self._logger.warning(
                "Research bridge data load failed for %s", code, exc_info=True
            )
            return ResearchBridgeStageResult.unavailable_result(
                self._build_unavailable_bridge(
                    stage="load_stock_data",
                    error=str(exc),
                    effective_as_of_date=effective_as_of_date,
                    parameter_source=parameter_source,
                )
            )
        stock_data = self._ensure_query_in_stock_data(
            stock_data=stock_data,
            code=code,
            cutoff_date=effective_as_of_date,
        )
        if not stock_data:
            return ResearchBridgeStageResult.unavailable_result(
                self._build_unavailable_bridge(
                    stage="empty_universe",
                    error="research bridge returned empty stock universe",
                    effective_as_of_date=effective_as_of_date,
                    parameter_source=parameter_source,
                )
            )
        return ResearchBridgeStageResult.ok(
            ResearchBridgeDataBundle(
                data_manager=data_manager,
                stock_data=stock_data,
            )
        )

    def _resolve_allowed_manager_ids(self, controller: Any) -> list[str]:
        return [
            str(item).strip()
            for item in (
                getattr(controller, "experiment_allowed_manager_ids", None)
                or getattr(controller, "governance_allowed_manager_ids", None)
                or getattr(config, "governance_allowed_manager_ids", None)
                or []
            )
            if str(item).strip()
        ]

    def _resolve_governance_settings(
        self, controller: Any
    ) -> tuple[list[str], bool, str]:
        allowed_manager_ids = self._resolve_allowed_manager_ids(controller)
        governance_enabled = bool(
            getattr(
                controller,
                "governance_enabled",
                getattr(config, "governance_enabled", True),
            )
        )
        governance_mode = (
            str(
                getattr(
                    controller,
                    "governance_mode",
                    getattr(config, "governance_mode", "rule"),
                )
                or "rule"
            )
            .strip()
            .lower()
        )
        return allowed_manager_ids, governance_enabled, governance_mode

    def _resolve_governance_bundle(
        self,
        *,
        controller: Any,
        data_bundle: ResearchBridgeDataBundle,
        effective_as_of_date: str,
        runtime_context: ResearchBridgeRuntimeContext,
        code: str,
    ) -> ResearchBridgeStageResult:
        allowed_manager_ids, governance_enabled, governance_mode = (
            self._resolve_governance_settings(controller)
        )
        decision, unavailable = self._resolve_governance_decision(
            controller=controller,
            stock_data=data_bundle.stock_data,
            effective_as_of_date=effective_as_of_date,
            current_manager_id=runtime_context.current_manager_id,
            data_manager=data_bundle.data_manager,
            allowed_manager_ids=allowed_manager_ids,
            parameter_source=runtime_context.parameter_source,
            code=code,
        )
        if unavailable is not None:
            return ResearchBridgeStageResult.unavailable_result(unavailable)
        if decision is None:
            return ResearchBridgeStageResult.unavailable_result(
                self._build_unavailable_bridge(
                    stage="governance",
                    error="governance decision unavailable",
                    effective_as_of_date=effective_as_of_date,
                    parameter_source=runtime_context.parameter_source,
                )
            )
        return ResearchBridgeStageResult.ok(
            ResearchBridgeGovernanceBundle(
                decision=decision,
                allowed_manager_ids=allowed_manager_ids,
                governance_enabled=governance_enabled,
                governance_mode=governance_mode,
            )
        )

    def _resolve_governance_decision(
        self,
        *,
        controller: Any,
        stock_data: dict[str, Any],
        effective_as_of_date: str,
        current_manager_id: str,
        data_manager: DataManager,
        allowed_manager_ids: list[str],
        parameter_source: str,
        code: str,
    ) -> tuple[GovernanceDecision | None, dict[str, Any] | None]:
        try:
            decision = self.governance_service.decide_governance(
                controller,
                stock_data=stock_data,
                cutoff_date=effective_as_of_date,
                current_manager_id=current_manager_id,
                data_manager=data_manager,
                output_dir=getattr(controller, "output_dir", OUTPUT_DIR),
                allowed_manager_ids=allowed_manager_ids or None,
                current_cycle_id=getattr(controller, "current_cycle_id", None),
                safe_leaderboard_refresh=True,
            )
        except _STOCK_RESEARCH_BRIDGE_EXCEPTIONS as exc:
            self._logger.warning(
                "Research bridge governance failed for %s", code, exc_info=True
            )
            return None, self._build_unavailable_bridge(
                stage="governance",
                error=str(exc),
                effective_as_of_date=effective_as_of_date,
                parameter_source=parameter_source,
            )
        return decision, None

    @staticmethod
    def _resolve_runtime_selection(
        *,
        decision: GovernanceDecision,
        current_manager_id: str,
        base_config_path: str,
        current_params: dict[str, Any],
        replay_mode: bool,
    ) -> ResearchBridgeRuntimeSelection:
        dominant_manager_id = str(
            getattr(decision, "dominant_manager_id", "")
            or current_manager_id
            or "momentum"
        )
        allocation_plan = dict(getattr(decision, "allocation_plan", {}) or {})
        decision_metadata = dict(getattr(decision, "metadata", {}) or {})
        selected_config = str(
            dict(allocation_plan.get("selected_manager_config_refs") or {}).get(
                dominant_manager_id
            )
            or decision_metadata.get("dominant_manager_config")
            or resolve_manager_config_ref(dominant_manager_id)
        )
        selected_config = normalize_path_ref(selected_config) or str(selected_config)
        runtime_overrides = (
            current_params
            if (
                not replay_mode
                and dominant_manager_id == current_manager_id
                and selected_config == base_config_path
            )
            else {}
        )
        return ResearchBridgeRuntimeSelection(
            dominant_manager_id=dominant_manager_id,
            selected_config=selected_config,
            runtime_overrides=runtime_overrides,
        )

    def _build_governance_context(
        self,
        *,
        decision: GovernanceDecision,
        effective_as_of_date: str,
        requested_as_of_date: str,
        governance_mode: str,
        governance_enabled: bool,
        dominant_manager_id: str,
        allowed_manager_ids: list[str],
    ) -> dict[str, Any]:
        return {
            "as_of_date": effective_as_of_date,
            "requested_as_of_date": requested_as_of_date,
            "governance_mode": governance_mode if governance_enabled else "off",
            "dominant_manager_id": dominant_manager_id,
            "active_manager_ids": list(
                getattr(decision, "active_manager_ids", []) or [dominant_manager_id]
            ),
            "manager_budget_weights": dict(
                getattr(decision, "manager_budget_weights", {}) or {}
            ),
            "portfolio_constraints": dict(
                getattr(decision, "portfolio_constraints", {}) or {}
            ),
            "decision_source": str(getattr(decision, "decision_source", "") or ""),
            "regime": str(getattr(decision, "regime", "") or "unknown"),
            "regime_confidence": float(
                getattr(decision, "regime_confidence", 0.0) or 0.0
            ),
            "decision_confidence": float(
                getattr(decision, "decision_confidence", 0.0) or 0.0
            ),
            "allowed_manager_ids": allowed_manager_ids or [dominant_manager_id],
            "cash_reserve_hint": float(
                getattr(decision, "cash_reserve_hint", 0.0) or 0.0
            ),
        }

    def _execute_manager_bridge(
        self,
        *,
        code: str,
        effective_as_of_date: str,
        runtime_context: ResearchBridgeRuntimeContext,
        data_bundle: ResearchBridgeDataBundle,
        governance_bundle: ResearchBridgeGovernanceBundle,
    ) -> ResearchBridgeStageResult:
        runtime_selection = self._resolve_runtime_selection(
            decision=governance_bundle.decision,
            current_manager_id=runtime_context.current_manager_id,
            base_config_path=runtime_context.base_config_path,
            current_params=dict(runtime_context.current_params),
            replay_mode=runtime_context.replay_mode,
        )
        try:
            manager_runtime = build_manager_runtime(
                manager_id=runtime_selection.dominant_manager_id,
                manager_config_ref=runtime_selection.selected_config,
                runtime_overrides=dict(runtime_selection.runtime_overrides),
            )
            manager_output = manager_runtime.process(
                data_bundle.stock_data,
                effective_as_of_date,
            )
        except _STOCK_RESEARCH_BRIDGE_EXCEPTIONS as exc:
            self._logger.warning(
                "Research bridge model execution failed for %s", code, exc_info=True
            )
            return ResearchBridgeStageResult.unavailable_result(
                self._build_unavailable_bridge(
                    stage="model_process",
                    error=str(exc),
                    effective_as_of_date=effective_as_of_date,
                    dominant_manager_id=runtime_selection.dominant_manager_id,
                    selected_config=runtime_selection.selected_config,
                )
            )
        return ResearchBridgeStageResult.ok(
            ResearchBridgeManagerExecution(
                runtime_selection=runtime_selection,
                manager_runtime=manager_runtime,
                manager_output=manager_output,
            )
        )

    def _run_bridge_output_stage(
        self,
        *,
        controller: Any,
        security: dict[str, Any],
        code: str,
        effective_as_of_date: str,
        derived: dict[str, Any],
        runtime_context: ResearchBridgeRuntimeContext,
        data_bundle: ResearchBridgeDataBundle,
        governance_bundle: ResearchBridgeGovernanceBundle,
        manager_execution: ResearchBridgeManagerExecution,
    ) -> ResearchBridgeOutputBundle:
        governance_context = self._build_governance_context(
            decision=governance_bundle.decision,
            effective_as_of_date=effective_as_of_date,
            requested_as_of_date=runtime_context.normalized_requested_as_of_date,
            governance_mode=governance_bundle.governance_mode,
            governance_enabled=governance_bundle.governance_enabled,
            dominant_manager_id=manager_execution.runtime_selection.dominant_manager_id,
            allowed_manager_ids=governance_bundle.allowed_manager_ids,
        )
        data_lineage = runtime_context.build_data_lineage(
            repository_db_path=self.repository.db_path,
            data_manager=data_bundle.data_manager,
            effective_as_of_date=effective_as_of_date,
            stock_data=data_bundle.stock_data,
        )
        snapshot = build_research_snapshot(
            manager_output=manager_execution.manager_output,
            security=security,
            query_code=code,
            stock_data=data_bundle.stock_data,
            governance_context=governance_context,
            data_lineage=data_lineage,
            derived_signals=derived,
        )
        policy = resolve_policy_snapshot(
            manager_runtime=manager_execution.manager_runtime,
            manager_id=manager_execution.runtime_selection.dominant_manager_id,
            governance_context=governance_context,
            data_window=runtime_context.build_policy_data_window(
                effective_as_of_date=effective_as_of_date,
                stock_data=data_bundle.stock_data,
            ),
            metadata=runtime_context.build_policy_metadata(
                controller=controller,
                effective_as_of_date=effective_as_of_date,
            ),
        )
        return ResearchBridgeOutputBundle(
            governance_context=governance_context,
            data_lineage=data_lineage,
            snapshot=snapshot,
            policy=policy,
        )

    def _resolve_bridge_assembly(
        self,
        *,
        controller: Any,
        code: str,
        effective_as_of_date: str,
        runtime_context: ResearchBridgeRuntimeContext,
    ) -> ResearchBridgeAssemblyBundle | dict[str, Any]:
        data_bundle_result = self._resolve_bridge_stage(
            result=self._load_bridge_stock_data(
                code=code,
                effective_as_of_date=effective_as_of_date,
                stock_count=runtime_context.stock_count,
                min_history_days=runtime_context.min_history_days,
                parameter_source=runtime_context.parameter_source,
            ),
            stage="load_stock_data",
            error="research bridge data bundle unavailable",
            effective_as_of_date=effective_as_of_date,
            parameter_source=runtime_context.parameter_source,
        )
        if isinstance(data_bundle_result, dict):
            return data_bundle_result
        governance_bundle_result = self._resolve_bridge_stage(
            result=self._resolve_governance_bundle(
                controller=controller,
                data_bundle=data_bundle_result,
                effective_as_of_date=effective_as_of_date,
                runtime_context=runtime_context,
                code=code,
            ),
            stage="governance",
            error="governance decision unavailable",
            effective_as_of_date=effective_as_of_date,
            parameter_source=runtime_context.parameter_source,
        )
        if isinstance(governance_bundle_result, dict):
            return governance_bundle_result
        manager_execution_result = self._resolve_bridge_stage(
            result=self._execute_manager_bridge(
                code=code,
                effective_as_of_date=effective_as_of_date,
                runtime_context=runtime_context,
                data_bundle=data_bundle_result,
                governance_bundle=governance_bundle_result,
            ),
            stage="model_process",
            error="research bridge manager execution unavailable",
            effective_as_of_date=effective_as_of_date,
            parameter_source=runtime_context.parameter_source,
        )
        if isinstance(manager_execution_result, dict):
            return manager_execution_result
        return ResearchBridgeAssemblyBundle(
            controller=controller,
            runtime_context=runtime_context,
            data_bundle=data_bundle_result,
            governance_bundle=governance_bundle_result,
            manager_execution=manager_execution_result,
        )

    @staticmethod
    def _run_bridge_finalize_stage(
        *,
        controller: Any,
        runtime_context: ResearchBridgeRuntimeContext,
        governance_bundle: ResearchBridgeGovernanceBundle,
        manager_execution: ResearchBridgeManagerExecution,
        bridge_outputs: ResearchBridgeOutputBundle,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "controller_bound": bool(controller is not None),
            "replay_mode": runtime_context.replay_mode,
            "parameter_source": runtime_context.parameter_source,
            "governance_decision": governance_bundle.decision.to_dict(),
            "manager_output": manager_execution.manager_output,
            "snapshot": bridge_outputs.snapshot,
            "policy": bridge_outputs.policy,
        }

    def build_research_bridge(
        self,
        *,
        code: str,
        security: dict[str, Any],
        requested_as_of_date: str,
        effective_as_of_date: str,
        days: int,
        derived: dict[str, Any],
    ) -> dict[str, Any]:
        controller = self._resolve_live_controller()
        runtime_context = self._resolve_bridge_runtime_context(
            controller=controller,
            code=code,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
            days=days,
        )
        assembly = self._resolve_bridge_assembly(
            controller=controller,
            code=code,
            effective_as_of_date=effective_as_of_date,
            runtime_context=runtime_context,
        )
        if isinstance(assembly, dict):
            return assembly
        bridge_outputs = self._run_bridge_output_stage(
            controller=assembly.controller,
            security=security,
            code=code,
            effective_as_of_date=effective_as_of_date,
            derived=derived,
            runtime_context=assembly.runtime_context,
            data_bundle=assembly.data_bundle,
            governance_bundle=assembly.governance_bundle,
            manager_execution=assembly.manager_execution,
        )
        return self._run_bridge_finalize_stage(
            controller=assembly.controller,
            runtime_context=assembly.runtime_context,
            governance_bundle=assembly.governance_bundle,
            manager_execution=assembly.manager_execution,
            bridge_outputs=bridge_outputs,
        )


# Batch analysis and research resolution owners
class BatchAnalysisViewService:
    def __init__(self, *, humanize_macd_cross: Callable[[str], str]):
        self._humanize_macd_cross = humanize_macd_cross

    @staticmethod
    def empty_snapshot() -> dict[str, Any]:
        return {
            "samples": 0,
            "latest_trade_date": None,
            "latest_close": None,
            "indicators": {},
            "ready": False,
        }

    def build_batch_analysis_context(
        self, frame: pd.DataFrame, code: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        cutoff = normalize_date(str(frame["trade_date"].max()))
        batch = build_batch_indicator_snapshot(frame, cutoff)
        summary = build_batch_summary(frame, code, cutoff) or {}
        snapshot = (
            dict(batch.streaming_snapshot)
            if batch is not None
            else self.empty_snapshot()
        )
        return summary, snapshot, {"cutoff": cutoff, "batch": batch}

    def view_from_snapshot(
        self, summary: dict[str, Any], snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        fields = _project_snapshot_fields(snapshot, summary=summary)
        indicators = dict(fields["indicators"])
        macd = dict(fields["macd_payload"])
        boll = dict(fields["boll"])
        latest = float(fields["latest_close"] or 0.0)
        ma5 = float(fields["ma5"] or 0.0)
        ma10 = float(fields["ma10"] or 0.0)
        ma20 = float(fields["ma20"] or 0.0)
        ma60 = float(fields["ma60"] or 0.0)
        volume_ratio = fields["volume_ratio"]
        rsi = float(fields["rsi"] or 50.0)
        ma_stack = str(fields["ma_stack"] or "mixed")
        macd_cross = str(fields["macd_cross"] or "neutral")
        signal = "observe"
        if ma_stack == "bullish" and macd_cross in {"golden_cross", "bullish"}:
            signal = "bullish"
        elif ma_stack == "bearish" and macd_cross in {"dead_cross", "bearish"}:
            signal = "bearish"
        structure = "range"
        if latest > ma20 and ma20 >= ma60:
            structure = "uptrend"
        elif latest < ma20 and ma20 <= ma60:
            structure = "downtrend"
        summary_view = dict(summary)
        summary_view.update(
            {
                "close": round(latest, 2) if latest else summary_view.get("close"),
                "rsi": round(rsi, 1),
                "macd": self._humanize_macd_cross(macd_cross),
                "ma_trend": "多头"
                if ma_stack == "bullish"
                else "空头"
                if ma_stack == "bearish"
                else "交叉",
                "bb_pos": boll.get("position", summary_view.get("bb_pos", 0.5)),
                "vol_ratio": volume_ratio
                if volume_ratio is not None
                else summary_view.get("vol_ratio"),
            }
        )
        trend_view = {
            "latest_close": round(latest, 2) if latest else None,
            "ma5": round(ma5, 2) if ma5 else None,
            "ma10": round(ma10, 2) if ma10 else None,
            "ma20": round(ma20, 2) if ma20 else None,
            "ma60": round(ma60, 2) if ma60 else None,
            "volume_ratio": round(float(volume_ratio), 3)
            if volume_ratio is not None
            else None,
            "macd_cross": macd_cross,
            "rsi_14": round(rsi, 2),
            "bollinger_position": boll.get("position"),
            "atr_14": indicators.get("atr_14"),
        }
        return {
            "summary": summary_view,
            "trend": trend_view,
            "signal": signal,
            "structure": structure,
            "indicators": indicators,
            "macd": macd,
            "boll": boll,
        }


class ResearchResolutionService:
    def __init__(
        self,
        *,
        case_store: Any,
        scenario_engine: Any,
        attribution_engine: Any,
        logger: Any,
    ):
        self.case_store = case_store
        self.scenario_engine = scenario_engine
        self.attribution_engine = attribution_engine
        self._logger = logger

    @staticmethod
    def normalize_as_of_date(value: str | None = None) -> str:
        raw = str(value or "").strip()
        return normalize_date(raw) if raw else ""

    def _build_resolution_base_payload(
        self,
        *,
        research_bridge: dict[str, Any],
        requested_as_of_date: str,
        effective_as_of_date: str,
    ) -> ResearchResolutionBasePayload:
        return ResearchResolutionBasePayload(
            status=str(research_bridge.get("status") or "unavailable"),
            requested_as_of_date=self.normalize_as_of_date(requested_as_of_date),
            as_of_date=effective_as_of_date,
        )

    def _build_resolution_payload_factory(
        self,
        *,
        research_bridge: dict[str, Any],
        requested_as_of_date: str,
        effective_as_of_date: str,
    ) -> ResearchResolutionPayloadFactory:
        return ResearchResolutionPayloadFactory(
            base_payload=self._build_resolution_base_payload(
                research_bridge=research_bridge,
                requested_as_of_date=requested_as_of_date,
                effective_as_of_date=effective_as_of_date,
            )
        )

    @staticmethod
    def _build_resolution_identifiers(
        *, policy_id: str = "", research_case_id: str = "", attribution_id: str = ""
    ) -> ResearchResolutionIdentifiers:
        return ResearchResolutionIdentifiers(
            policy_id=policy_id,
            research_case_id=research_case_id,
            attribution_id=attribution_id,
        )

    @staticmethod
    def _build_resolution_identifier_updates(
        identifiers: ResearchResolutionIdentifiers,
    ) -> dict[str, str]:
        return identifiers.to_dict()

    @staticmethod
    def _build_research_bridge_detail_updates(
        *,
        research_bridge: dict[str, Any],
        identifiers: ResearchResolutionIdentifiers,
    ) -> dict[str, Any]:
        return {
            "controller_bound": bool(research_bridge.get("controller_bound")),
            "replay_mode": bool(research_bridge.get("replay_mode")),
            "parameter_source": str(research_bridge.get("parameter_source") or ""),
            "governance_decision": dict(
                research_bridge.get("governance_decision") or {}
            ),
            "manager_output": research_bridge["manager_output"].to_dict(),
            **ResearchResolutionService._build_resolution_identifier_updates(
                identifiers
            ),
        }

    @staticmethod
    def _build_research_detail_updates(
        *,
        snapshot: Any,
        policy: Any,
        hypothesis: Any,
        scenario: dict[str, Any],
        persistence: ResearchPersistenceProjection,
    ) -> dict[str, Any]:
        return {
            "snapshot": snapshot.to_dict(),
            "policy": policy.to_dict(),
            "hypothesis": hypothesis.to_dict(),
            "scenario": dict(scenario or {}),
            **persistence.to_detail_payload(),
        }

    @staticmethod
    def _build_unavailable_detail_updates_bundle(
        *,
        research_bridge: dict[str, Any],
    ) -> ResearchResolutionDetailUpdatesBundle:
        return ResearchResolutionDetailUpdatesBundle(
            shared_updates={
                "error": str(research_bridge.get("error") or ""),
                "fallback": "canonical_dashboard_fallback",
                "details": dict(research_bridge.get("details") or {}),
            },
            research_updates={},
            research_bridge_updates={},
        )

    @staticmethod
    def _build_available_detail_updates_bundle(
        *,
        research_bridge: dict[str, Any],
        stages: ResearchResolutionAvailableStageBundle,
        identifiers: ResearchResolutionIdentifiers,
    ) -> ResearchResolutionDetailUpdatesBundle:
        return ResearchResolutionDetailUpdatesBundle(
            shared_updates={},
            research_updates=ResearchResolutionService._build_research_detail_updates(
                snapshot=stages.artifacts.snapshot,
                policy=stages.artifacts.policy,
                hypothesis=stages.artifacts.hypothesis,
                scenario=stages.artifacts.scenario,
                persistence=stages.persistence.projection,
            ),
            research_bridge_updates=(
                ResearchResolutionService._build_research_bridge_detail_updates(
                    research_bridge=research_bridge,
                    identifiers=identifiers,
                )
            ),
        )

    def _build_resolution_context(
        self,
        *,
        research_bridge: dict[str, Any],
        question: str,
        query: str,
        strategy: Any,
        strategy_source: str,
        code: str,
        requested_as_of_date: str,
        effective_as_of_date: str,
        execution: dict[str, Any],
        derived: dict[str, Any],
        dashboard_projection_builder: Callable[..., dict[str, Any]],
    ) -> ResearchResolutionContext:
        return ResearchResolutionContext(
            question=question,
            query=query,
            strategy=strategy,
            strategy_source=strategy_source,
            code=code,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
            execution=dict(execution or {}),
            derived=dict(derived or {}),
            dashboard_projection_factory=ResearchResolutionDashboardProjectionFactory(
                strategy=strategy,
                dashboard_projection_builder=dashboard_projection_builder,
            ),
            payload_factory=self._build_resolution_payload_factory(
                research_bridge=research_bridge,
                requested_as_of_date=requested_as_of_date,
                effective_as_of_date=effective_as_of_date,
            ),
        )

    def _build_resolution_display_bundle(
        self,
        *,
        context: ResearchResolutionContext,
        dashboard: dict[str, Any],
        identifiers: ResearchResolutionIdentifiers | None = None,
        research_updates: dict[str, Any] | None = None,
        research_bridge_updates: dict[str, Any] | None = None,
        shared_updates: dict[str, Any] | None = None,
    ) -> ResearchResolutionDisplayBundle:
        combined_research_updates = {
            **dict(shared_updates or {}),
            **dict(research_updates or {}),
        }
        combined_research_bridge_updates = {
            **dict(shared_updates or {}),
            **dict(research_bridge_updates or {}),
        }
        return ResearchResolutionDisplayBundle(
            context=context,
            dashboard=dict(dashboard),
            identifiers=identifiers or self._build_resolution_identifiers(),
            research_updates=combined_research_updates,
            research_bridge_updates=combined_research_bridge_updates,
        )

    def _build_resolution_display_spec(
        self,
        *,
        bundle: ResearchResolutionDisplayBundle,
    ) -> ResearchResolutionDisplaySpec:
        return ResearchResolutionDisplaySpec(
            dashboard=dict(bundle.dashboard),
            research_payload=bundle.context.payload_factory.merge(
                bundle.research_updates,
            ),
            research_bridge_payload=bundle.context.payload_factory.merge(
                bundle.research_bridge_updates,
            ),
            identifiers=bundle.identifiers,
        )

    def _run_resolution_finalize_stage(
        self,
        *,
        spec: ResearchResolutionDisplaySpec,
    ) -> dict[str, Any]:
        return {
            "dashboard": dict(spec.dashboard),
            "research": dict(spec.research_payload),
            "research_bridge": dict(spec.research_bridge_payload),
            **spec.identifiers.to_dict(),
        }

    def _build_unavailable_display_bundle(
        self,
        *,
        context: ResearchResolutionContext,
        research_bridge: dict[str, Any],
    ) -> ResearchResolutionDisplayBundle:
        analysis = self._run_fallback_analysis_stage(
            strategy=context.strategy,
            derived=context.derived,
            execution=context.execution,
        )
        detail_updates = self._build_unavailable_detail_updates_bundle(
            research_bridge=research_bridge,
        )
        return self._build_resolution_display_bundle(
            context=context,
            dashboard=context.dashboard_projection_factory.build(analysis).render(),
            shared_updates=detail_updates.shared_updates,
            research_updates=detail_updates.research_updates,
            research_bridge_updates=detail_updates.research_bridge_updates,
        )

    def persist_research_case_artifacts(
        self,
        *,
        snapshot: Any,
        policy: Any,
        hypothesis: Any,
        question: str,
        query: str,
        strategy: Any,
        strategy_source: str,
        execution_mode: str,
        code: str,
        effective_as_of_date: str,
    ) -> ResearchPersistenceProjection:
        case_record = None
        attribution_preview = None
        attribution_record = None
        calibration_report = None
        research_case_id = ""
        attribution_id = ""
        try:
            case_record = self.case_store.save_case(
                snapshot=snapshot,
                policy=policy,
                hypothesis=hypothesis,
                metadata={
                    "question": question,
                    "query": query,
                    "strategy": strategy.name,
                    "strategy_source": strategy_source,
                    "execution_mode": execution_mode,
                },
            )
            research_case_id = str(case_record.get("research_case_id") or "")
            attribution = self.attribution_engine.evaluate_case(case_record)
            attribution_preview = attribution.to_dict()
            has_scored_horizon = any(
                str((result or {}).get("label") or "") != "timeout"
                for result in dict(attribution.horizon_results or {}).values()
            )
            if has_scored_horizon:
                attribution_record = self.case_store.save_attribution(
                    attribution,
                    metadata={
                        "policy_id": policy.policy_id,
                        "research_case_id": research_case_id,
                        "code": code,
                        "as_of_date": effective_as_of_date,
                    },
                )
                attribution_id = str(attribution_record.get("attribution_id") or "")
                calibration_report = self.case_store.write_calibration_report(
                    policy_id=policy.policy_id
                )
        except _STOCK_RESEARCH_PERSISTENCE_EXCEPTIONS:
            self._logger.warning(
                "Failed to persist/evaluate research case for %s", code, exc_info=True
            )
        return ResearchPersistenceProjection(
            case=dict(case_record or {}),
            attribution=ResearchPersistenceAttributionProjection(
                saved=bool(attribution_record),
                record=dict(attribution_record or {}),
                preview=dict(attribution_preview or {}),
            ),
            calibration_report=dict(calibration_report or {}),
            identifiers=self._build_resolution_identifiers(
                research_case_id=research_case_id,
                attribution_id=attribution_id,
            ),
        )

    @staticmethod
    def estimate_preliminary_stance(snapshot: Any) -> str:
        cross = dict(getattr(snapshot, "cross_section_context", {}) or {})
        percentile = cross.get("percentile")
        percentile_f = float(percentile or 0.0) if percentile is not None else 0.0
        selected_by_policy = bool(cross.get("selected_by_policy"))
        raw_score = 50.0 + percentile_f * 40.0 + (8.0 if selected_by_policy else 0.0)
        if raw_score >= 82:
            return "候选买入"
        if raw_score >= 68:
            return "偏强关注"
        if raw_score <= 35:
            return "减仓/回避"
        if raw_score <= 45:
            return "偏弱回避"
        return "持有观察"

    @staticmethod
    def _build_pre_dashboard_analysis_spec(
        *,
        hypothesis: ResearchHypothesis,
        matched_signals: list[str],
        supplemental_reason: str = "",
    ) -> PreDashboardAnalysisSpec:
        return PreDashboardAnalysisSpec(
            hypothesis=hypothesis,
            matched_signals=list(matched_signals),
            supplemental_reason=str(supplemental_reason or ""),
        )

    @staticmethod
    def _build_normal_hypothesis(
        *,
        artifacts: ResearchResolutionArtifacts,
    ) -> ResearchHypothesis:
        return artifacts.hypothesis

    @staticmethod
    def _build_normal_analysis_spec(
        *,
        artifacts: ResearchResolutionArtifacts,
        context: ResearchResolutionContext,
    ) -> PreDashboardAnalysisSpec:
        matched_signals = list(context.derived.get("matched_signals") or [])
        hypothesis = ResearchResolutionService._build_normal_hypothesis(
            artifacts=artifacts,
        )
        return ResearchResolutionService._build_pre_dashboard_analysis_spec(
            hypothesis=hypothesis,
            matched_signals=matched_signals,
            supplemental_reason="",
        )

    @staticmethod
    def _build_fallback_rule_score_contribution(
        *,
        strategy: Any,
        flags: dict[str, Any],
    ) -> tuple[float, list[str]]:
        score_delta = 0.0
        reason_parts: list[str] = []
        for label, delta in strategy.scoring.items():
            if flags.get(label):
                score_delta += float(delta)
                reason_parts.append(f"{label}{'+' if delta >= 0 else ''}{delta:g}")
        return score_delta, reason_parts

    @staticmethod
    def _build_fallback_algo_score_contribution(
        *,
        derived: dict[str, Any],
    ) -> tuple[float, str]:
        algo_score = float(derived.get("algo_score") or 0.0)
        delta = max(-10.0, min(10.0, algo_score * 2.0))
        if not algo_score:
            return delta, ""
        return delta, f"algo_score 调整 {delta:+.1f}"

    @staticmethod
    def _build_fallback_reasoning_excerpt(
        *,
        execution: dict[str, Any],
    ) -> str:
        final_reasoning = str(execution.get("final_reasoning") or "").strip()
        if not final_reasoning:
            return ""
        return f"分析摘要: {final_reasoning[:120]}"

    @classmethod
    def _build_fallback_score_reason_components(
        cls,
        *,
        strategy: Any,
        derived: dict[str, Any],
        execution: dict[str, Any],
    ) -> FallbackScoreReasonComponents:
        flags = dict(derived.get("flags") or {})
        rule_score_delta, reason_parts = cls._build_fallback_rule_score_contribution(
            strategy=strategy,
            flags=flags,
        )
        algo_score_delta, algo_reason = cls._build_fallback_algo_score_contribution(
            derived=derived,
        )
        reasoning_excerpt = cls._build_fallback_reasoning_excerpt(execution=execution)
        supplemental_reason_parts = [
            *reason_parts,
            *([algo_reason] if algo_reason else []),
            *([reasoning_excerpt] if reasoning_excerpt else []),
        ]
        return FallbackScoreReasonComponents(
            score=50.0 + rule_score_delta + algo_score_delta,
            supplemental_reason="；".join(supplemental_reason_parts),
        )

    @staticmethod
    def _build_fallback_stance_component(
        *,
        score: float,
    ) -> FallbackStanceComponent:
        stance = "持有观察"
        if score >= 82:
            stance = "候选买入"
        elif score >= 70:
            stance = "偏强关注"
        elif score <= 35:
            stance = "减仓/回避"
        elif score <= 45:
            stance = "偏弱回避"
        return FallbackStanceComponent(stance=stance)

    @staticmethod
    def _build_fallback_price_levels(
        *,
        derived: dict[str, Any],
        stance: str,
    ) -> FallbackPriceLevels:
        latest_price = float(derived.get("latest_close") or 0.0)
        entry_price = (
            round(latest_price * 0.99, 2)
            if latest_price and stance in {"候选买入", "偏强关注"}
            else None
        )
        stop_loss = round(latest_price * 0.94, 2) if latest_price else None
        return FallbackPriceLevels(
            entry_price=entry_price,
            stop_loss=stop_loss,
        )

    @staticmethod
    def _build_fallback_risk_signals(
        *,
        derived: dict[str, Any],
    ) -> FallbackRiskSignals:
        flags = dict(derived.get("flags") or {})
        contradicting_factors = [
            label
            for label in (
                "空头排列",
                "MACD死叉",
                "RSI超买",
                "趋势向下",
                "结构走弱",
                "逼近阻力",
                "跌破MA20",
            )
            if flags.get(label)
        ]
        return FallbackRiskSignals(contradicting_factors=contradicting_factors)

    @staticmethod
    def _derive_fallback_analysis_values(
        *,
        strategy: Any,
        derived: dict[str, Any],
        execution: dict[str, Any],
    ) -> FallbackDerivedAnalysisValues:
        score_components = (
            ResearchResolutionService._build_fallback_score_reason_components(
                strategy=strategy,
                derived=derived,
                execution=execution,
            )
        )
        stance_component = ResearchResolutionService._build_fallback_stance_component(
            score=score_components.score,
        )
        price_levels = ResearchResolutionService._build_fallback_price_levels(
            derived=derived,
            stance=stance_component.stance,
        )
        risk_signals = ResearchResolutionService._build_fallback_risk_signals(
            derived=derived,
        )
        return FallbackDerivedAnalysisValues(
            score=round(max(0.0, min(100.0, score_components.score)), 1),
            stance=stance_component.stance,
            entry_price=price_levels.entry_price,
            stop_loss=price_levels.stop_loss,
            contradicting_factors=risk_signals.contradicting_factors,
            supplemental_reason=score_components.supplemental_reason,
        )

    @staticmethod
    def _build_fallback_hypothesis(
        *,
        strategy: Any,
        values: FallbackDerivedAnalysisValues,
        matched_signals: list[str],
    ) -> ResearchHypothesis:
        return ResearchHypothesis(
            hypothesis_id="hypothesis_dashboard_fallback",
            snapshot_id="snapshot_dashboard_fallback",
            policy_id="policy_dashboard_fallback",
            stance=values.stance,
            score=values.score,
            entry_rule={
                "kind": (
                    "limit_pullback" if values.entry_price is not None else "observe_only"
                ),
                "price": values.entry_price,
                "source": strategy.display_name,
            },
            invalidation_rule={
                "kind": "stop_loss",
                "price": values.stop_loss,
                "source": strategy.name,
            },
            supporting_factors=matched_signals,
            contradicting_factors=values.contradicting_factors,
            metadata={"source": "dashboard_fallback", "strategy_name": strategy.name},
        )

    def _run_fallback_analysis_stage(
        self,
        *,
        strategy: Any,
        derived: dict[str, Any],
        execution: dict[str, Any],
    ) -> PreDashboardAnalysisSpec:
        matched_signals = list(derived.get("matched_signals") or [])
        values = self._derive_fallback_analysis_values(
            strategy=strategy,
            derived=derived,
            execution=execution,
        )
        fallback_hypothesis = self._build_fallback_hypothesis(
            strategy=strategy,
            values=values,
            matched_signals=matched_signals,
        )
        return self._build_pre_dashboard_analysis_spec(
            hypothesis=fallback_hypothesis,
            matched_signals=matched_signals,
            supplemental_reason=values.supplemental_reason,
        )

    def build_canonical_fallback_projection(
        self,
        *,
        strategy: Any,
        derived: dict[str, Any],
        execution: dict[str, Any],
        dashboard_projection_builder: Callable[
            ..., dict[str, Any]
        ] = build_dashboard_projection,
    ) -> dict[str, Any]:
        fallback_analysis = self._run_fallback_analysis_stage(
            strategy=strategy,
            derived=derived,
            execution=execution,
        )
        return ResearchResolutionDashboardProjectionFactory(
            strategy=strategy,
            dashboard_projection_builder=dashboard_projection_builder,
        ).build(fallback_analysis).render()

    def _run_resolution_artifact_stage(
        self,
        *,
        research_bridge: dict[str, Any],
        strategy: Any,
    ) -> ResearchResolutionArtifacts:
        snapshot = research_bridge["snapshot"]
        policy = research_bridge["policy"]
        preliminary_stance = self.estimate_preliminary_stance(snapshot)
        scenario = self.scenario_engine.estimate(
            snapshot=snapshot,
            policy=policy,
            stance=preliminary_stance,
        )
        hypothesis = build_research_hypothesis(
            snapshot=snapshot,
            policy=policy,
            scenario=scenario,
            strategy_name=strategy.name,
            strategy_display_name=strategy.display_name,
        )
        return ResearchResolutionArtifacts(
            snapshot=snapshot,
            policy=policy,
            scenario=dict(scenario or {}),
            hypothesis=hypothesis,
        )

    def _run_resolution_persistence_stage(
        self,
        *,
        artifacts: ResearchResolutionArtifacts,
        context: ResearchResolutionContext,
    ) -> ResearchResolutionPersistence:
        persistence = self.persist_research_case_artifacts(
            snapshot=artifacts.snapshot,
            policy=artifacts.policy,
            hypothesis=artifacts.hypothesis,
            question=context.question,
            query=context.query,
            strategy=context.strategy,
            strategy_source=context.strategy_source,
            execution_mode=str(context.execution.get("mode") or ""),
            code=context.code,
            effective_as_of_date=context.effective_as_of_date,
        )
        return ResearchResolutionPersistence(
            policy_id=str(artifacts.policy.policy_id or ""),
            projection=persistence,
        )

    def _run_normal_analysis_stage(
        self,
        *,
        artifacts: ResearchResolutionArtifacts,
        context: ResearchResolutionContext,
    ) -> PreDashboardAnalysisSpec:
        normal_analysis = self._build_normal_analysis_spec(
            artifacts=artifacts,
            context=context,
        )
        return normal_analysis

    def _run_available_resolution_stages(
        self,
        *,
        context: ResearchResolutionContext,
        research_bridge: dict[str, Any],
    ) -> ResearchResolutionAvailableStageBundle:
        artifacts = self._run_resolution_artifact_stage(
            research_bridge=research_bridge,
            strategy=context.strategy,
        )
        return ResearchResolutionAvailableStageBundle(
            artifacts=artifacts,
            analysis=self._run_normal_analysis_stage(
                artifacts=artifacts,
                context=context,
            ),
            persistence=self._run_resolution_persistence_stage(
                artifacts=artifacts,
                context=context,
            ),
        )

    def _build_available_display_bundle(
        self,
        *,
        context: ResearchResolutionContext,
        research_bridge: dict[str, Any],
        stages: ResearchResolutionAvailableStageBundle,
    ) -> ResearchResolutionDisplayBundle:
        identifiers = stages.persistence.display_identifiers()
        detail_updates = self._build_available_detail_updates_bundle(
            research_bridge=research_bridge,
            stages=stages,
            identifiers=identifiers,
        )
        return self._build_resolution_display_bundle(
            context=context,
            dashboard=context.dashboard_projection_factory.build(
                stages.analysis
            ).render(),
            identifiers=identifiers,
            shared_updates=detail_updates.shared_updates,
            research_updates=detail_updates.research_updates,
            research_bridge_updates=detail_updates.research_bridge_updates,
        )

    def _run_resolution_display_stage(
        self,
        *,
        bundle: ResearchResolutionDisplayBundle,
    ) -> ResearchResolutionDisplaySpec:
        return self._build_resolution_display_spec(
            bundle=bundle,
        )

    def resolve_outputs(
        self,
        *,
        research_bridge: dict[str, Any],
        question: str,
        query: str,
        strategy: Any,
        strategy_source: str,
        code: str,
        requested_as_of_date: str,
        effective_as_of_date: str,
        execution: dict[str, Any],
        derived: dict[str, Any],
        dashboard_projection_builder: Callable[
            ..., dict[str, Any]
        ] = build_dashboard_projection,
    ) -> dict[str, Any]:
        context = self._build_resolution_context(
            research_bridge=research_bridge,
            question=question,
            query=query,
            strategy=strategy,
            strategy_source=strategy_source,
            code=code,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
            execution=execution,
            derived=derived,
            dashboard_projection_builder=dashboard_projection_builder,
        )
        if research_bridge.get("status") != "ok":
            return self._run_resolution_finalize_stage(
                spec=self._run_resolution_display_stage(
                    bundle=self._build_unavailable_display_bundle(
                        context=context,
                        research_bridge=research_bridge,
                    )
                ),
            )
        available_stages = self._run_available_resolution_stages(
            context=context,
            research_bridge=research_bridge,
        )
        return self._run_resolution_finalize_stage(
            spec=self._run_resolution_display_stage(
                bundle=self._build_available_display_bundle(
                    context=context,
                    research_bridge=research_bridge,
                    stages=available_stages,
                )
            ),
        )


# Strategy contracts and store
@dataclass
class StockToolPlanStep:
    tool: str
    args: dict[str, Any]
    thought: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": self.args,
            "thought": self.thought,
        }


@dataclass
class StockAnalysisStrategy:
    name: str
    display_name: str
    description: str
    required_tools: list[str]
    analysis_steps: list[str]
    entry_conditions: list[str]
    scoring: dict[str, float]
    core_rules: list[str]
    tool_call_plan: list[StockToolPlanStep]
    aliases: list[str]
    planner_prompt: str
    react_enabled: bool
    max_steps: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "required_tools": self.required_tools,
            "analysis_steps": self.analysis_steps,
            "entry_conditions": self.entry_conditions,
            "scoring": self.scoring,
            "core_rules": self.core_rules,
            "tool_call_plan": [step.to_dict() for step in self.tool_call_plan],
            "aliases": self.aliases,
            "planner_prompt": self.planner_prompt,
            "react_enabled": self.react_enabled,
            "max_steps": self.max_steps,
        }


DEFAULT_STOCK_STRATEGIES: dict[str, dict[str, Any]] = {
    "chan_theory.yaml": {
        "name": "chan_theory",
        "display_name": "缠论",
        "aliases": ["缠论", "chan", "chan theory"],
        "description": "基于结构、趋势、背离、支撑阻力与资金确认的离线问股模板。",
        "required_tools": [
            "get_daily_history",
            "get_indicator_snapshot",
            "analyze_support_resistance",
            "get_capital_flow",
            "get_realtime_quote",
        ],
        "planner_prompt": "先确认结构，再确认指标与关键价位，最后看资金和最新价格是否共振。",
        "react_enabled": True,
        "max_steps": 5,
        "tool_call_plan": [
            {
                "tool": "get_daily_history",
                "args": {"query": "{{query}}", "days": 60},
                "thought": "先看近60日结构，确认价格区间和走势背景。",
            },
            {
                "tool": "get_indicator_snapshot",
                "args": {"query": "{{query}}", "days": 120},
                "thought": "把均线、MACD、RSI、ATR、布林带先跑全。",
            },
            {
                "tool": "analyze_support_resistance",
                "args": {"query": "{{query}}", "days": 120},
                "thought": "确认关键支撑阻力与当前价格所处位置。",
            },
            {
                "tool": "get_capital_flow",
                "args": {"query": "{{query}}", "days": 20},
                "thought": "看看资金是否支持当前结构。",
            },
            {
                "tool": "get_realtime_quote",
                "args": {"query": "{{query}}"},
                "thought": "最后确认最新价格，用于入场位和止损位参考。",
            },
        ],
        "core_rules": ["结构优先", "趋势级别", "背离/动能", "关键价位", "风险控制"],
        "analysis_steps": [
            "获取近60日日线",
            "识别指标状态",
            "判断支撑阻力",
            "观察资金确认",
            "结合最新价格输出结论",
        ],
        "entry_conditions": ["结构未破坏", "MACD/均线改善", "靠近支撑或突破有效"],
        "scoring": {
            "多头排列": 10,
            "MACD金叉": 8,
            "RSI超卖": 5,
            "资金净流入": 6,
            "接近支撑": 4,
            "MACD死叉": -8,
            "空头排列": -10,
            "逼近阻力": -4,
        },
    },
    "trend_following.yaml": {
        "name": "trend_following",
        "display_name": "趋势跟随",
        "aliases": ["趋势跟随", "trend following", "趋势策略"],
        "description": "基于均线、量价、资金和关键价位的趋势问股模板。",
        "required_tools": [
            "get_daily_history",
            "get_indicator_snapshot",
            "analyze_trend",
            "get_capital_flow",
            "get_realtime_quote",
        ],
        "planner_prompt": "优先确认中期趋势，再验证量价与资金是否共振，最后检查当前价格位置。",
        "react_enabled": True,
        "max_steps": 5,
        "tool_call_plan": [
            {
                "tool": "get_daily_history",
                "args": {"query": "{{query}}", "days": 120},
                "thought": "先拉长窗口确认中期趋势。",
            },
            {
                "tool": "get_indicator_snapshot",
                "args": {"query": "{{query}}", "days": 180},
                "thought": "用统一指标快照确认 MA 栈、MACD、RSI 和 ATR。",
            },
            {
                "tool": "analyze_trend",
                "args": {"query": "{{query}}", "days": 180},
                "thought": "进一步判断趋势方向与结构是否完整。",
            },
            {
                "tool": "get_capital_flow",
                "args": {"query": "{{query}}", "days": 20},
                "thought": "观察资金是否顺着趋势流入。",
            },
            {
                "tool": "get_realtime_quote",
                "args": {"query": "{{query}}"},
                "thought": "确认最新价格是否仍站在关键均线之上。",
            },
        ],
        "core_rules": ["均线顺势", "量能确认", "资金确认", "止损先行"],
        "analysis_steps": [
            "获取近120日日线",
            "计算指标快照",
            "验证趋势结构",
            "观察资金确认",
            "输出趋势建议",
        ],
        "entry_conditions": ["价格站上MA20", "多头排列", "资金净流入"],
        "scoring": {
            "价格站上MA20": 10,
            "量比放大": 6,
            "趋势向上": 8,
            "资金净流入": 5,
            "接近支撑": 3,
            "跌破MA20": -12,
            "趋势向下": -8,
            "空头排列": -10,
        },
    },
}


class StockAnalysisStrategyStore:
    def __init__(self, strategy_dir: Path):
        self.strategy_dir = Path(strategy_dir)
        self.strategy_dir.mkdir(parents=True, exist_ok=True)

    def ensure_default_strategies(self) -> None:
        if yaml is None:
            return
        for filename, payload in DEFAULT_STOCK_STRATEGIES.items():
            path = self.strategy_dir / filename
            if path.exists():
                continue
            path.write_text(
                yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

    def list_strategies(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.strategy_dir.glob("*.yaml")):
            strategy = self.load_path(path)
            if strategy is not None:
                items.append(strategy.to_dict())
        return items

    def load_strategy(self, name: str) -> StockAnalysisStrategy:
        path = self._strategy_path(name)
        strategy = self.load_path(path)
        if strategy is None:
            raise FileNotFoundError(f"stock strategy not found: {name}")
        return strategy

    def _strategy_path(self, name: str) -> Path:
        return self.strategy_dir / f"{str(name or 'chan_theory').strip()}.yaml"

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        return [str(item) for item in (value or []) if str(item).strip()]

    @staticmethod
    def _coerce_scoring(value: Any) -> dict[str, float]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, float] = {}
        for key, score in value.items():
            name = str(key or "").strip()
            if not name:
                continue
            try:
                normalized[name] = float(score)
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid strategy scoring weight: key=%s value=%r type=%s",
                    name,
                    score,
                    type(score).__name__,
                )
        return normalized

    @staticmethod
    def _build_tool_plan(value: Any) -> list[StockToolPlanStep]:
        tool_plan: list[StockToolPlanStep] = []
        for item in list(value or []):
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "").strip()
            if not tool:
                continue
            tool_plan.append(
                StockToolPlanStep(
                    tool=tool,
                    args=dict(item.get("args") or {}),
                    thought=str(item.get("thought") or ""),
                )
            )
        return tool_plan

    @staticmethod
    def _load_yaml_payload(path: Path) -> dict[str, Any] | None:
        if yaml is None or not path.exists():
            return None
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return payload if isinstance(payload, dict) else None

    def _build_strategy(
        self, *, path: Path, payload: dict[str, Any]
    ) -> StockAnalysisStrategy:
        return StockAnalysisStrategy(
            name=str(payload.get("name") or path.stem),
            display_name=str(
                payload.get("display_name") or payload.get("name") or path.stem
            ),
            description=str(payload.get("description") or ""),
            required_tools=self._string_list(payload.get("required_tools")),
            analysis_steps=self._string_list(payload.get("analysis_steps")),
            entry_conditions=self._string_list(payload.get("entry_conditions")),
            scoring=self._coerce_scoring(payload.get("scoring")),
            core_rules=self._string_list(payload.get("core_rules")),
            tool_call_plan=self._build_tool_plan(payload.get("tool_call_plan")),
            aliases=self._string_list(payload.get("aliases")),
            planner_prompt=str(
                payload.get("planner_prompt")
                or "优先按策略规则逐步调用工具，再给出简短结论。"
            ),
            react_enabled=bool(payload.get("react_enabled", True)),
            max_steps=max(1, min(8, int(payload.get("max_steps", 4) or 4))),
        )

    def load_path(self, path: Path) -> StockAnalysisStrategy | None:
        payload = self._load_yaml_payload(path)
        if payload is None:
            return None
        return self._build_strategy(path=path, payload=payload)


# Canonical facade
_STOCK_CODE_RE = re.compile(r"\b(?:sh|sz)\.\d{6}\b|\b\d{6}(?:\.(?:SH|SZ|sh|sz))?\b")
_DAY_COUNT_RE = re.compile(r"(\d{2,4})\s*(?:个)?(?:交易)?(?:日|天)")


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    values = (
        frame[column]
        if column in frame.columns
        else pd.Series(index=frame.index, dtype="float64")
    )
    return cast(pd.Series, pd.to_numeric(values, errors="coerce"))


def _project_snapshot_fields(
    snapshot: dict[str, Any],
    *,
    summary: dict[str, Any] | None = None,
    trend_metrics: dict[str, Any] | None = None,
    quote_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = dict(summary or {})
    trend_metrics = dict(trend_metrics or {})
    quote_row = dict(quote_row or {})
    snapshot_payload = dict(snapshot or {})
    indicators = dict(snapshot_payload.get("indicators") or {})
    macd_payload = dict(indicators.get("macd_12_26_9") or {})
    boll = dict(indicators.get("bollinger_20") or {})
    latest_close = float(
        snapshot_payload.get("latest_close")
        or trend_metrics.get("latest_close")
        or quote_row.get("close")
        or summary.get("close")
        or 0.0
    )
    ma5 = float(
        indicators.get("sma_5") or trend_metrics.get("ma5") or latest_close or 0.0
    )
    ma10 = float(
        indicators.get("sma_10") or trend_metrics.get("ma10") or latest_close or 0.0
    )
    ma20 = float(
        indicators.get("sma_20") or trend_metrics.get("ma20") or latest_close or 0.0
    )
    ma60 = float(indicators.get("sma_60") or trend_metrics.get("ma60") or ma20 or 0.0)
    volume_ratio = indicators.get("volume_ratio_5_20") or trend_metrics.get(
        "volume_ratio"
    )
    rsi = float(
        indicators.get("rsi_14")
        or trend_metrics.get("rsi_14")
        or summary.get("rsi")
        or 50.0
    )
    ma_stack = str(indicators.get("ma_stack") or "mixed")
    macd_cross = str(
        macd_payload.get("cross") or trend_metrics.get("macd_cross") or "neutral"
    )
    return {
        "snapshot": snapshot_payload,
        "indicators": indicators,
        "macd_payload": macd_payload,
        "boll": boll,
        "latest_close": latest_close,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "volume_ratio": volume_ratio,
        "rsi": rsi,
        "ma_stack": ma_stack,
        "macd_cross": macd_cross,
        "atr_14": indicators.get("atr_14") or trend_metrics.get("atr_14"),
    }


class StockAnalysisService(StockAnalysisExecutionMixin):
    def __init__(
        self,
        db_path: str | Path | None = None,
        strategy_dir: str | Path | None = None,
        *,
        gateway: LLMGateway | None = None,
        model: str = "",
        api_key: str = "",
        api_base: str = "",
        project_root: str | Path | None = None,
        enable_llm_react: bool = True,
        controller_provider: Callable[[], Any] | None = None,
    ):
        self.repository = MarketDataRepository(str(db_path) if db_path else None)
        self.repository.initialize_schema()
        self.strategy_dir = self._init_strategy_dir(strategy_dir)
        self.strategy_store = StockAnalysisStrategyStore(self.strategy_dir)
        self._project_root = self._resolve_project_root(
            project_root=project_root,
            strategy_dir=strategy_dir,
            db_path=db_path,
        )
        self._runtime_state_dir = self._init_runtime_state_dir(self._project_root)
        self._analysis_as_of_date: str = ""
        self._controller_provider = controller_provider
        self.strategy_store.ensure_default_strategies()
        self._tool_catalog = _build_stock_tool_catalog()
        self._tool_registry = self._build_tool_registry()
        self.gateway = gateway or self._build_gateway(
            model=model, api_key=api_key, api_base=api_base
        )
        self.enable_llm_react = bool(enable_llm_react)
        self._init_research_services()

    def _init_strategy_dir(self, strategy_dir: str | Path | None) -> Path:
        resolved = Path(strategy_dir or (PROJECT_ROOT / "stock_strategies"))
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def _resolve_project_root(
        self,
        *,
        project_root: str | Path | None,
        strategy_dir: str | Path | None,
        db_path: str | Path | None,
    ) -> Path:
        if project_root is not None:
            return Path(project_root)
        if strategy_dir is not None:
            return Path(strategy_dir).expanduser().resolve().parent
        if db_path is not None:
            return Path(db_path).expanduser().resolve().parent
        return PROJECT_ROOT

    def _init_runtime_state_dir(self, project_root: Path) -> Path:
        runtime_state_dir = project_root / "runtime" / "state"
        runtime_state_dir.mkdir(parents=True, exist_ok=True)
        return runtime_state_dir

    def _build_tool_registry(self) -> dict[str, Callable[..., dict[str, Any]]]:
        registry: dict[str, Callable[..., dict[str, Any]]] = {}
        for item in self._stock_tool_catalog_entries():
            tool_name = str(item.get("name") or "").strip()
            executor_attr = str(item.get("executor") or "").strip()
            if tool_name and executor_attr:
                registry[tool_name] = cast(
                    Callable[..., dict[str, Any]], getattr(self, executor_attr)
                )
        for alias, canonical_name in self._stock_tool_aliases().items():
            if alias and canonical_name in registry:
                registry[alias] = registry[canonical_name]
        return registry

    def _init_research_services(self) -> None:
        self.case_store = ResearchCaseStore(self._runtime_state_dir)
        self.scenario_engine = ResearchScenarioEngine(self.case_store)
        self.attribution_engine = ResearchAttributionEngine(self.repository)
        self.batch_analysis_service = BatchAnalysisViewService(
            humanize_macd_cross=self._humanize_macd_cross
        )
        self.research_resolution_service = ResearchResolutionService(
            case_store=self.case_store,
            scenario_engine=self.scenario_engine,
            attribution_engine=self.attribution_engine,
            logger=logger,
        )
        self.research_bridge_service = StockAnalysisResearchBridgeService(
            repository=self.repository,
            controller_provider=self._controller_provider,
            research_resolution_service=self.research_resolution_service,
            governance_service=TrainingGovernanceService(),
            normalize_as_of_date=self._normalize_as_of_date,
            resolve_effective_as_of_date=self._resolve_effective_as_of_date,
            logger_instance=logger,
        )

    def list_strategies(self) -> list[dict[str, Any]]:
        return self.strategy_store.list_strategies()

    def get_daily_history(self, query: str, *, days: int = 60) -> dict[str, Any]:
        window_context, unavailable = self._resolve_window_context(
            query,
            days=days,
            minimum=10,
            summary="未找到历史K线数据",
            next_actions=["确认股票代码或先同步本地历史数据"],
        )
        if unavailable is not None:
            unavailable["items"] = []
            return unavailable
        assert window_context is not None
        code = window_context.code
        security = window_context.security
        frame = window_context.frame
        items = frame.to_dict(orient="records")
        return self._build_tool_records_response(
            query=query,
            code=code,
            security=security,
            records_key="items",
            records=items,
            summary=f"已获取 {len(items)} 条历史K线",
            next_actions=["可继续做趋势和结构分析"],
            artifacts={
                "last_trade_date": items[-1].get("trade_date") if items else None
            },
        )

    def get_realtime_quote(self, query: str) -> dict[str, Any]:
        context, unavailable = self._resolve_price_query_context(
            query,
            summary="未找到最新报价",
            next_actions=["确认股票代码或先同步本地日线数据"],
        )
        if unavailable is not None:
            return unavailable
        code = context.code
        security = dict(context.security)
        frame = cast(pd.DataFrame, context.price_frame)
        row = dict(frame.tail(1).to_dict(orient="records")[0])
        return self._build_tool_response(
            status="ok",
            query=query,
            code=code,
            security=security,
            quote=row,
            summary=f"最新参考价格 {row.get('close')}",
            next_actions=["可据此计算入场位和止损位"],
            artifacts={"trade_date": row.get("trade_date")},
        )

    @staticmethod
    def _empty_snapshot() -> dict[str, Any]:
        return BatchAnalysisViewService.empty_snapshot()

    def _resolve_query_context(self, query: str) -> StockQueryContext:
        code, security = self.resolve_security(query)
        return StockQueryContext(
            query=query,
            code=code,
            security=dict(security),
            price_frame=self._get_stock_frame(code),
        )

    def _resolve_price_query_context(
        self,
        query: str,
        *,
        summary: str,
        next_actions: list[str],
        status: str = "not_found",
    ) -> tuple[StockQueryContext, dict[str, Any] | None]:
        context = self._resolve_query_context(query)
        if context.price_frame.empty:
            return context, self._build_tool_unavailable_response(
                status=status,
                query=context.query,
                code=context.code,
                security=context.security,
                summary=summary,
                next_actions=next_actions,
            )
        return context, None

    def _resolve_window_context(
        self,
        query: str,
        *,
        days: int,
        minimum: int,
        summary: str,
        next_actions: list[str],
        status: str = "not_found",
        copy_frame: bool = False,
    ) -> tuple[StockWindowContext | None, dict[str, Any] | None]:
        context, unavailable = self._resolve_price_query_context(
            query,
            summary=summary,
            next_actions=next_actions,
            status=status,
        )
        if unavailable is not None:
            return None, unavailable
        frame = self._tail_frame(
            cast(pd.DataFrame, context.price_frame),
            days=days,
            minimum=minimum,
        )
        if copy_frame:
            frame = frame.copy()
        return StockWindowContext(query_context=context, frame=frame), None

    def _build_batch_analysis_context(
        self, frame: pd.DataFrame, code: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return self.batch_analysis_service.build_batch_analysis_context(frame, code)

    def _view_from_snapshot(
        self, summary: dict[str, Any], snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        return self.batch_analysis_service.view_from_snapshot(summary, snapshot)

    @staticmethod
    def _tail_frame(
        frame: pd.DataFrame, *, days: int, minimum: int = 1
    ) -> pd.DataFrame:
        return frame.tail(max(minimum, int(days)))

    @staticmethod
    def _resolve_frame_date_window(
        frame: pd.DataFrame, *, days: int
    ) -> tuple[str, str]:
        window = frame.tail(max(1, int(days)))
        return str(window["trade_date"].min()), str(frame["trade_date"].max())

    def _resolve_price_window(
        self, frame: pd.DataFrame, *, days: int
    ) -> dict[str, str]:
        start_date, end_date = self._resolve_frame_date_window(frame, days=days)
        return {
            "start_date": start_date,
            "end_date": end_date,
        }

    def _build_snapshot_projection(
        self, frame: pd.DataFrame, code: str
    ) -> dict[str, Any]:
        summary, snapshot, meta = self._build_batch_analysis_context(frame, code)
        view = self._view_from_snapshot(summary, snapshot)
        fields = _project_snapshot_fields(
            snapshot, summary=summary, trend_metrics=dict(view.get("trend") or {})
        )
        return {
            "summary": summary,
            "snapshot": snapshot,
            "meta": meta,
            "view": view,
            "fields": fields,
        }

    @staticmethod
    def _build_tool_response(
        *,
        status: str,
        query: str,
        code: str,
        security: dict[str, Any] | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        response: dict[str, Any] = {
            "status": status,
            "query": query,
            "code": code,
        }
        if security is not None:
            response["security"] = security
        response.update(payload)
        return response

    @staticmethod
    def _build_tool_common_payload(
        *,
        summary: str,
        next_actions: list[str],
        artifacts: dict[str, Any] | None = None,
        observation_summary: str = "",
        metrics: dict[str, Any] | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        common_payload: dict[str, Any] = {
            "summary": summary,
            "next_actions": next_actions,
            "artifacts": dict(artifacts or {}),
        }
        if observation_summary:
            common_payload["observation_summary"] = observation_summary
        if metrics is not None:
            common_payload["metrics"] = metrics
        common_payload.update(payload)
        return common_payload

    def _build_tool_unavailable_response(
        self,
        *,
        status: str,
        query: str,
        code: str,
        summary: str,
        next_actions: list[str],
        artifacts: dict[str, Any] | None = None,
        security: dict[str, Any] | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        return self._build_tool_response(
            status=status,
            query=query,
            code=code,
            security=security,
            **self._build_tool_common_payload(
                summary=summary,
                next_actions=next_actions,
                artifacts=artifacts,
                **payload,
            ),
        )

    def _build_tool_analysis_response(
        self,
        *,
        query: str,
        code: str,
        security: dict[str, Any] | None,
        summary: str,
        next_actions: list[str],
        artifacts: dict[str, Any] | None = None,
        observation_summary: str = "",
        **payload: Any,
    ) -> dict[str, Any]:
        return self._build_tool_response(
            status="ok",
            query=query,
            code=code,
            security=security,
            **self._build_tool_common_payload(
                summary=summary,
                next_actions=next_actions,
                artifacts=artifacts,
                observation_summary=observation_summary,
                **payload,
            ),
        )

    def _build_tool_records_response(
        self,
        *,
        query: str,
        code: str,
        security: dict[str, Any] | None,
        records_key: str,
        records: list[dict[str, Any]],
        summary: str,
        next_actions: list[str],
        artifacts: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        records_payload = {
            records_key: records,
            "count": int(len(records)),
            **payload,
        }
        return self._build_tool_response(
            status="ok",
            query=query,
            code=code,
            security=security,
            **self._build_tool_common_payload(
                summary=summary,
                next_actions=next_actions,
                artifacts=artifacts,
                metrics=metrics,
                **records_payload,
            ),
        )

    def analyze_trend(self, query: str, *, days: int = 120) -> dict[str, Any]:
        window_context, unavailable = self._resolve_window_context(
            query,
            days=days,
            minimum=30,
            summary="未找到趋势分析所需数据",
            next_actions=["先检查数据或改用更长时间范围"],
        )
        if unavailable is not None:
            return unavailable
        assert window_context is not None
        code = window_context.code
        security = window_context.security
        frame = window_context.frame
        projection = self._build_snapshot_projection(frame, code)
        snapshot = dict(projection["snapshot"])
        meta = dict(projection["meta"])
        view = dict(projection["view"])
        trend = view["trend"]
        return self._build_tool_analysis_response(
            query=query,
            code=code,
            security=security,
            signal=view["signal"],
            structure=view["structure"],
            summary=view["summary"],
            indicator_snapshot=snapshot,
            trend=trend,
            observation_summary=(
                f"趋势={view['signal']}, 结构={view['structure']}, "
                f"MA20={trend.get('ma20', 0.0):.2f}, RSI={trend.get('rsi_14', 50.0):.1f}"
            ),
            next_actions=["结合支撑阻力、资金流和最新价格生成结论"],
            artifacts={
                "cutoff_date": meta["cutoff"],
                "latest_trade_date": snapshot.get("latest_trade_date"),
            },
        )

    def get_indicator_snapshot(self, query: str, *, days: int = 180) -> dict[str, Any]:
        window_context, unavailable = self._resolve_window_context(
            query,
            days=days,
            minimum=30,
            summary="未找到指标快照所需数据",
            next_actions=["确认股票代码或同步本地行情数据"],
        )
        if unavailable is not None:
            return unavailable
        assert window_context is not None
        code = window_context.code
        security = window_context.security
        frame = window_context.frame
        projection = self._build_snapshot_projection(frame, code)
        snapshot = dict(projection["snapshot"])
        fields = dict(projection["fields"])
        indicators = dict(fields["indicators"])
        macd_payload = dict(fields["macd_payload"])
        return self._build_tool_analysis_response(
            query=query,
            code=code,
            security=security,
            days=int(days),
            snapshot=snapshot,
            summary="已生成指标快照",
            observation_summary=(
                f"RSI={indicators.get('rsi_14')}, MA栈={indicators.get('ma_stack')}, "
                f"MACD={macd_payload.get('cross', 'neutral')}"
            ),
            next_actions=["可继续分析支撑阻力、资金流或最新价格位置"],
            artifacts={"latest_trade_date": snapshot.get("latest_trade_date")},
        )

    def analyze_support_resistance(
        self, query: str, *, days: int = 120
    ) -> dict[str, Any]:
        window_context, unavailable = self._resolve_window_context(
            query,
            days=days,
            minimum=30,
            summary="未找到支撑阻力分析所需数据",
            next_actions=["确认股票代码或同步本地行情数据"],
            copy_frame=True,
        )
        if unavailable is not None:
            return unavailable
        assert window_context is not None
        code = window_context.code
        security = window_context.security
        frame = window_context.frame
        highs = _numeric_series(frame, "high").dropna()
        lows = _numeric_series(frame, "low").dropna()
        closes = _numeric_series(frame, "close").dropna()
        if highs.empty or lows.empty or closes.empty:
            return self._build_tool_unavailable_response(
                status="not_found",
                query=query,
                code=code,
                summary="高低收盘序列不可用",
                next_actions=["检查行情数据完整性"],
            )
        projection = self._build_snapshot_projection(frame, code)
        snapshot = dict(projection["snapshot"])
        fields = dict(projection["fields"])
        latest_close = float(fields["latest_close"] or closes.iloc[-1] or 0.0)
        support_20 = (
            float(lows.tail(20).min()) if len(lows) >= 20 else float(lows.min())
        )
        resistance_20 = (
            float(highs.tail(20).max()) if len(highs) >= 20 else float(highs.max())
        )
        support_60 = float(lows.tail(60).min()) if len(lows) >= 60 else support_20
        resistance_60 = (
            float(highs.tail(60).max()) if len(highs) >= 60 else resistance_20
        )
        atr = float(fields.get("atr_14") or 0.0)
        distance_to_support = (
            0.0 if latest_close <= 0 else (latest_close - support_20) / latest_close
        )
        distance_to_resistance = (
            0.0 if latest_close <= 0 else (resistance_20 - latest_close) / latest_close
        )
        bias = "neutral"
        if latest_close >= resistance_20 * 0.985:
            bias = "breakout_test"
        elif latest_close <= support_20 * 1.015:
            bias = "support_test"
        return self._build_tool_analysis_response(
            query=query,
            code=code,
            security=security,
            levels={
                "support_20": round(support_20, 2),
                "resistance_20": round(resistance_20, 2),
                "support_60": round(support_60, 2),
                "resistance_60": round(resistance_60, 2),
                "latest_close": round(latest_close, 2),
                "atr_14": round(atr, 4),
                "distance_to_support_pct": round(distance_to_support * 100.0, 2),
                "distance_to_resistance_pct": round(distance_to_resistance * 100.0, 2),
                "bias": bias,
            },
            summary="已识别关键支撑阻力位",
            observation_summary=(
                f"支撑{support_20:.2f}/阻力{resistance_20:.2f}, "
                f"距支撑{distance_to_support * 100:.1f}%, 距阻力{distance_to_resistance * 100:.1f}%"
            ),
            next_actions=["结合趋势、资金流和价格确认入场/风控位置"],
            artifacts={"latest_trade_date": snapshot.get("latest_trade_date")},
        )

    def get_capital_flow(self, query: str, *, days: int = 20) -> dict[str, Any]:
        context, unavailable = self._resolve_price_query_context(
            query,
            summary="未找到资金流查询所需行情基线",
            next_actions=["确认股票代码或同步本地行情数据"],
        )
        if unavailable is not None:
            return unavailable
        code = context.code
        security = dict(context.security)
        price_frame = cast(pd.DataFrame, context.price_frame)
        price_window = self._resolve_price_window(price_frame, days=days)
        start_date = str(price_window["start_date"])
        end_date = str(price_window["end_date"])
        frame = self.repository.query_capital_flow_daily(
            codes=[code], start_date=start_date, end_date=end_date
        )
        if frame.empty:
            frame = (
                self.repository.query_capital_flow_daily(
                    codes=[code],
                    end_date=self._current_analysis_cutoff(),
                )
                .sort_values("trade_date")
                .tail(max(1, int(days)))
            )
        if frame.empty:
            return self._build_tool_unavailable_response(
                status="no_data",
                query=query,
                code=code,
                security=security,
                summary="本地暂无资金流数据",
                next_actions=["可继续依赖价格与指标工具分析，或补充资金流数据"],
                artifacts={"start_date": start_date, "end_date": end_date},
            )
        frame = frame.sort_values("trade_date").tail(max(1, int(days)))
        latest = dict(frame.tail(1).to_dict(orient="records")[0])
        main_sum = float(_numeric_series(frame, "main_net_inflow").fillna(0).sum())
        ratio_mean = float(
            _numeric_series(frame, "main_net_inflow_ratio").fillna(0).mean()
        )
        direction = "inflow" if main_sum > 0 else "outflow" if main_sum < 0 else "flat"
        return self._build_tool_records_response(
            query=query,
            code=code,
            security=security,
            records_key="items",
            records=frame.to_dict(orient="records"),
            metrics={
                "main_net_inflow_sum": round(main_sum, 2),
                "main_net_inflow_ratio_avg": round(ratio_mean, 4),
                "latest_trade_date": latest.get("trade_date"),
                "direction": direction,
            },
            summary=f"近{len(frame)}日主力资金{direction}",
            next_actions=["结合趋势与支撑阻力判断资金是否确认当前结构"],
            artifacts={"start_date": start_date, "end_date": end_date},
            observation_summary=f"主力净流入合计={main_sum:.2f}, 平均占比={ratio_mean:.2f}",
        )

    def get_intraday_context(self, query: str, *, days: int = 5) -> dict[str, Any]:
        context, unavailable = self._resolve_price_query_context(
            query,
            summary="未找到分时上下文查询所需行情基线",
            next_actions=["确认股票代码或同步本地行情数据"],
        )
        if unavailable is not None:
            return unavailable
        code = context.code
        security = dict(context.security)
        daily = cast(pd.DataFrame, context.price_frame)
        price_window = self._resolve_price_window(daily, days=days)
        start_date = str(price_window["start_date"])
        end_date = str(price_window["end_date"])
        frame = self.repository.query_intraday_bars_60m(
            codes=[code], start_date=start_date, end_date=end_date
        )
        if frame.empty:
            return self._build_tool_unavailable_response(
                status="no_data",
                query=query,
                code=code,
                security=security,
                summary="本地暂无60分钟数据",
                next_actions=["仍可使用日线与指标工具完成分析"],
                artifacts={"start_date": start_date, "end_date": end_date},
            )
        latest_day = str(frame["trade_date"].max())
        latest = cast(pd.DataFrame, frame[frame["trade_date"] == latest_day].copy())
        latest = cast(pd.DataFrame, latest.sort_values(by=["bar_time"]))
        close_series = _numeric_series(latest, "close")
        high_series = _numeric_series(latest, "high")
        low_series = _numeric_series(latest, "low")
        first_close = float(close_series.iloc[0])
        last_close = float(close_series.iloc[-1])
        day_range = float(high_series.max() - low_series.min())
        intraday_bias = (
            "up"
            if last_close > first_close
            else "down"
            if last_close < first_close
            else "flat"
        )
        return self._build_tool_records_response(
            query=query,
            code=code,
            security=security,
            records_key="bars",
            records=latest.to_dict(orient="records"),
            metrics={
                "latest_trade_date": latest_day,
                "first_close": round(first_close, 2),
                "last_close": round(last_close, 2),
                "day_range": round(day_range, 2),
                "intraday_bias": intraday_bias,
            },
            summary=f"最新60分钟结构偏{intraday_bias}",
            next_actions=["必要时用来确认日线结论是否得到短周期配合"],
            artifacts={"start_date": start_date, "end_date": end_date},
            observation_summary=f"首收={first_close:.2f}, 末收={last_close:.2f}, 振幅={day_range:.2f}",
        )

    def ask_stock(
        self,
        *,
        question: str,
        query: str,
        strategy: str = "chan_theory",
        days: int = 60,
        as_of_date: str = "",
    ) -> dict[str, Any]:
        strategy_name, strategy_source = self._resolve_strategy_name(
            question=question, strategy=strategy
        )
        resolved_days = self._infer_days(question=question, default_days=days)
        strat = self.load_strategy(strategy_name)
        base_context = self._resolve_query_context(query)
        code = base_context.code
        security = dict(base_context.security)
        effective_as_of_date = self._resolve_effective_as_of_date(code, as_of_date)
        with self._analysis_scope(effective_as_of_date):
            execution_bundle = self._run_ask_stock_execution_stage(
                question=question,
                code=code,
                security=security,
                strategy=strat,
                days=resolved_days,
            )
        research_resolution = self._resolve_ask_stock_research_outputs(
            question=question,
            query=query,
            strategy=strat,
            strategy_source=strategy_source,
            code=code,
            security=security,
            requested_as_of_date=as_of_date,
            effective_as_of_date=effective_as_of_date,
            days=resolved_days,
            execution=execution_bundle.execution,
            derived=execution_bundle.derived,
        )
        research_bundle = self._build_ask_stock_research_bundle(research_resolution)
        presentation_bundle = self._build_ask_stock_presentation_bundle(
            question=question,
            query=query,
            code=code,
            as_of_date=as_of_date,
            effective_as_of_date=effective_as_of_date,
            strategy=strat,
            strategy_source=strategy_source,
            execution_bundle=execution_bundle,
            research_bundle=research_bundle,
        )
        payload_bundle = self._build_ask_stock_payload_bundle(
            security=security,
            strategy=strat,
            strategy_source=strategy_source,
            days=resolved_days,
            research_bundle=research_bundle,
            presentation_bundle=presentation_bundle,
        )
        payload = self._build_ask_stock_payload(
            payload_bundle=payload_bundle,
        )
        return build_protocol_response(
            payload=payload,
            protocol=presentation_bundle.protocol_bundle.protocol,
            task_bus=presentation_bundle.task_bus,
            artifacts=presentation_bundle.protocol_bundle.artifacts,
            coverage=presentation_bundle.protocol_bundle.coverage,
            artifact_taxonomy=presentation_bundle.protocol_bundle.artifact_taxonomy,
            default_reply="已完成问股分析。",
        )

    def _run_ask_stock_execution_stage(
        self,
        *,
        question: str,
        code: str,
        security: dict[str, Any],
        strategy: StockAnalysisStrategy,
        days: int,
    ) -> AskStockExecutionRunBundle:
        planning_bundle = self._build_execution_planning_bundle(
            strategy=strategy,
            query=code,
            days=days,
        )
        execution = self._run_react_executor(
            question=question,
            query=code,
            security=security,
            strategy=strategy,
            days=days,
            planning_bundle=planning_bundle,
        )
        return AskStockExecutionRunBundle(
            recommended_plan=planning_bundle.recommended_plan,
            allowed_tools=planning_bundle.allowed_tools,
            execution=execution,
            coverage=self._build_execution_coverage(
                strategy=strategy,
                execution=execution,
                recommended_plan=planning_bundle.recommended_plan,
                allowed_tools=planning_bundle.allowed_tools,
            ),
            derived=self._derive_signals(execution),
        )

    def _build_ask_stock_task_artifacts(
        self,
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

    def _build_ask_stock_execution_projection(
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
            available_tools=sorted(self._tool_registry.keys()),
        )

    @staticmethod
    def _build_ask_stock_identifiers_projection(
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

    def _build_ask_stock_task_bus(
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
    def _build_ask_stock_context_artifacts(
        *,
        execution_projection: AskStockExecutionProjection,
        policy_id: str,
        research_case_id: str,
        attribution_id: str,
    ) -> dict[str, Any]:
        return {
            **dict(execution_projection.task_bus_artifacts),
            "policy_id": policy_id,
            "research_case_id": research_case_id,
            "attribution_id": attribution_id,
        }

    def _build_ask_stock_bounded_context(
        self,
        *,
        execution_projection: AskStockExecutionProjection,
        policy_id: str,
        research_case_id: str,
        attribution_id: str,
    ) -> dict[str, Any]:
        return build_bounded_response_context(
            schema_version=BOUNDED_WORKFLOW_SCHEMA_VERSION,
            domain="stock",
            operation="ask_stock",
            artifacts=self._build_ask_stock_context_artifacts(
                execution_projection=execution_projection,
                policy_id=policy_id,
                research_case_id=research_case_id,
                attribution_id=attribution_id,
            ),
            workflow=cast(list[str], list(execution_projection.workflow)),
            phase_stats=dict(execution_projection.phase_stats),
            coverage=dict(execution_projection.coverage),
        )

    def _build_ask_stock_request(
        self,
        *,
        question: str,
        query: str,
        code: str,
        as_of_date: str,
        effective_as_of_date: str,
    ) -> dict[str, Any]:
        return {
            "question": question,
            "query": query,
            "normalized_query": code,
            "requested_as_of_date": self._normalize_as_of_date(as_of_date),
            "as_of_date": effective_as_of_date,
        }

    @staticmethod
    def _build_ask_stock_entrypoint() -> dict[str, Any]:
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

    def _build_ask_stock_orchestration_extra_projection(
        self,
        *,
        strategy: StockAnalysisStrategy,
        execution_projection: AskStockExecutionProjection,
    ) -> AskStockOrchestrationExtraProjection:
        return AskStockOrchestrationExtraProjection(
            required_tools=list(strategy.required_tools),
            recommended_plan=list(execution_projection.recommended_plan),
            tool_plan=list(execution_projection.tool_plan),
            tool_calls=list(execution_projection.tool_calls),
            step_count=len(execution_projection.tool_calls),
            llm_reasoning=execution_projection.llm_reasoning,
            fallback_used=execution_projection.fallback_used,
            gap_fill=dict(execution_projection.gap_fill),
            coverage=dict(execution_projection.coverage),
        )

    @staticmethod
    def _with_identifiers(
        payload: dict[str, Any], identifiers: dict[str, str]
    ) -> dict[str, Any]:
        return {**dict(payload), "identifiers": dict(identifiers)}

    @staticmethod
    def _build_ask_stock_protocol_bundle(
        bounded_context: dict[str, Any],
    ) -> AskStockProtocolBundle:
        return AskStockProtocolBundle(
            protocol=dict(bounded_context["protocol"]),
            artifacts=dict(bounded_context["artifacts"]),
            coverage=dict(bounded_context["coverage"]),
            artifact_taxonomy=dict(bounded_context["artifact_taxonomy"]),
        )

    def _build_ask_stock_presentation_assembly_bundle(
        self,
        *,
        question: str,
        query: str,
        execution: dict[str, Any],
        derived: dict[str, Any],
        execution_projection: AskStockExecutionProjection,
        research_bundle: AskStockResearchBundle,
        identifiers: dict[str, str],
    ) -> AskStockPresentationAssemblyBundle:
        section_payload_factory = AskStockSectionPayloadFactory(
            execution=execution,
            derived=derived,
            research_bundle=research_bundle,
            identifiers=identifiers,
        )
        task_bus = self._build_ask_stock_task_bus(
            question=question,
            query=query,
            execution_projection=execution_projection,
        )
        bounded_context = self._build_ask_stock_bounded_context(
            execution_projection=execution_projection,
            policy_id=research_bundle.policy_id,
            research_case_id=research_bundle.research_case_id,
            attribution_id=research_bundle.attribution_id,
        )
        return AskStockPresentationAssemblyBundle(
            task_bus=task_bus,
            protocol_bundle=self._build_ask_stock_protocol_bundle(bounded_context),
            sections=self._build_ask_stock_sections_bundle(
                factory=section_payload_factory,
            ),
        )

    def _build_ask_stock_orchestration_bundle(
        self,
        *,
        strategy: StockAnalysisStrategy,
        execution_projection: AskStockExecutionProjection,
        allowed_tools: list[str],
    ) -> AskStockOrchestrationBundle:
        extra_projection = self._build_ask_stock_orchestration_extra_projection(
            strategy=strategy,
            execution_projection=execution_projection,
        )
        return AskStockOrchestrationBundle(
            mode=execution_projection.mode,
            available_tools=list(execution_projection.available_tools),
            allowed_tools=list(allowed_tools),
            workflow=list(execution_projection.workflow),
            phase_stats=dict(execution_projection.phase_stats),
            extra=extra_projection.to_payload(),
        )

    @staticmethod
    def _build_ask_stock_sections_bundle(
        *,
        factory: AskStockSectionPayloadFactory,
    ) -> AskStockSectionsBundle:
        return AskStockSectionsBundle(
            analysis=factory.analysis_payload(),
            research=factory.research_payload(),
        )

    @staticmethod
    def _build_ask_stock_research_bundle(
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

    def _build_ask_stock_presentation_bundle(
        self,
        *,
        question: str,
        query: str,
        code: str,
        as_of_date: str,
        effective_as_of_date: str,
        strategy: StockAnalysisStrategy,
        strategy_source: str,
        execution_bundle: AskStockExecutionRunBundle,
        research_bundle: AskStockResearchBundle,
    ) -> AskStockPresentationBundle:
        task_bus_artifacts = self._build_ask_stock_task_artifacts(
            code=code,
            strategy_name=strategy.name,
            strategy_source=strategy_source,
            derived=execution_bundle.derived,
            execution=execution_bundle.execution,
        )
        execution_projection = self._build_ask_stock_execution_projection(
            execution=execution_bundle.execution,
            recommended_plan=execution_bundle.recommended_plan,
            coverage=execution_bundle.coverage,
            task_bus_artifacts=task_bus_artifacts,
        )
        request = self._build_ask_stock_request(
            question=question,
            query=query,
            code=code,
            as_of_date=as_of_date,
            effective_as_of_date=effective_as_of_date,
        )
        identifiers_projection = self._build_ask_stock_identifiers_projection(
            policy_id=research_bundle.policy_id,
            research_case_id=research_bundle.research_case_id,
            attribution_id=research_bundle.attribution_id,
        )
        identifiers = identifiers_projection.to_payload()
        assembly_bundle = self._build_ask_stock_presentation_assembly_bundle(
            question=question,
            query=query,
            execution=execution_bundle.execution,
            derived=execution_bundle.derived,
            execution_projection=execution_projection,
            research_bundle=research_bundle,
            identifiers=identifiers,
        )
        return AskStockPresentationBundle(
            request=request,
            identifiers=identifiers,
            task_bus=assembly_bundle.task_bus,
            protocol_bundle=assembly_bundle.protocol_bundle,
            orchestration_bundle=self._build_ask_stock_orchestration_bundle(
                strategy=strategy,
                execution_projection=execution_projection,
                allowed_tools=execution_bundle.allowed_tools,
            ),
            sections=assembly_bundle.sections,
        )

    def _build_ask_stock_orchestration_payload(
        self,
        *,
        strategy: StockAnalysisStrategy,
        orchestration_bundle: AskStockOrchestrationBundle,
    ) -> dict[str, Any]:
        return build_bounded_orchestration(
            mode=orchestration_bundle.mode,
            available_tools=list(orchestration_bundle.available_tools),
            allowed_tools=list(orchestration_bundle.allowed_tools),
            workflow=cast(list[str], list(orchestration_bundle.workflow)),
            phase_stats=dict(orchestration_bundle.phase_stats),
            policy=build_bounded_policy(
                source="yaml_strategy",
                agent_kind="bounded_stock_agent",
                workflow_mode="llm_react_with_yaml_gap_fill",
                react_enabled=bool(strategy.react_enabled),
                tool_catalog_scope="strategy_restricted",
                fixed_boundary=True,
                fixed_workflow=True,
            ),
            extra=dict(orchestration_bundle.extra),
        )

    def _build_ask_stock_response_header_factory(
        self,
        *,
        request: dict[str, Any],
        identifiers: dict[str, str],
        security: dict[str, Any],
        strategy: StockAnalysisStrategy,
        strategy_source: str,
        days: int,
    ) -> AskStockResponseHeaderFactory:
        return AskStockResponseHeaderFactory(
            spec=AskStockResponseHeaderSpec(
                request=AskStockResponseRequestHeader(
                    request=dict(request),
                ),
                resolution=AskStockResponseResolutionHeader(
                    identifiers=dict(identifiers),
                    security=dict(security),
                ),
                strategy=AskStockResponseStrategyHeader(
                    entrypoint=self._build_ask_stock_entrypoint(),
                    strategy_payload=strategy.to_dict(),
                    strategy_source=strategy_source,
                    days=days,
                ),
            )
        )

    def _build_ask_stock_payload_bundle(
        self,
        *,
        security: dict[str, Any],
        strategy: StockAnalysisStrategy,
        strategy_source: str,
        days: int,
        research_bundle: AskStockResearchBundle,
        presentation_bundle: AskStockPresentationBundle,
    ) -> AskStockPayloadBundle:
        orchestration_bundle = presentation_bundle.orchestration_bundle
        return AskStockPayloadBundle(
            header_factory=self._build_ask_stock_response_header_factory(
                request=presentation_bundle.request,
                identifiers=presentation_bundle.identifiers,
                security=security,
                strategy=strategy,
                strategy_source=strategy_source,
                days=days,
            ),
            task_bus=dict(presentation_bundle.task_bus),
            orchestration=self._build_ask_stock_orchestration_payload(
                strategy=strategy,
                orchestration_bundle=orchestration_bundle,
            ),
            analysis=dict(presentation_bundle.sections.analysis),
            research=dict(presentation_bundle.sections.research),
            dashboard=dict(research_bundle.dashboard),
        )

    def _build_ask_stock_payload(
        self,
        *,
        payload_bundle: AskStockPayloadBundle,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            **payload_bundle.header_factory.build(),
            "task_bus": dict(payload_bundle.task_bus),
            "orchestration": dict(payload_bundle.orchestration),
            "analysis": dict(payload_bundle.analysis),
            "research": dict(payload_bundle.research),
            "dashboard": dict(payload_bundle.dashboard),
        }

    def _resolve_ask_stock_research_outputs(
        self,
        *,
        question: str,
        query: str,
        strategy: StockAnalysisStrategy,
        strategy_source: str,
        code: str,
        security: dict[str, Any],
        requested_as_of_date: str,
        effective_as_of_date: str,
        days: int,
        execution: dict[str, Any],
        derived: dict[str, Any],
    ) -> dict[str, Any]:
        research_bridge = self._build_research_bridge(
            code=code,
            security=security,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
            days=days,
            derived=derived,
        )
        return self.research_resolution_service.resolve_outputs(
            research_bridge=research_bridge,
            question=question,
            query=query,
            strategy=strategy,
            strategy_source=strategy_source,
            code=code,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
            execution=execution,
            derived=derived,
            dashboard_projection_builder=build_dashboard_projection,
        )

    def _build_research_bridge(
        self,
        *,
        code: str,
        security: dict[str, Any],
        requested_as_of_date: str,
        effective_as_of_date: str,
        days: int,
        derived: dict[str, Any],
    ) -> dict[str, Any]:
        return self.research_bridge_service.build_research_bridge(
            code=code,
            security=security,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
            days=days,
            derived=derived,
        )

    @staticmethod
    def _normalize_as_of_date(value: str | None = None) -> str:
        raw = str(value or "").strip()
        return normalize_date(raw) if raw else ""

    def _current_analysis_cutoff(self) -> str | None:
        return self._normalize_as_of_date(self._analysis_as_of_date) or None

    def _resolve_effective_as_of_date(
        self, code: str, requested_as_of_date: str = ""
    ) -> str:
        requested = self._normalize_as_of_date(requested_as_of_date)
        frame = self.repository.get_stock(code, cutoff_date=requested or None)
        if frame.empty:
            return requested
        trade_dates = frame.get("trade_date")
        if trade_dates is None or len(trade_dates) == 0:
            return requested
        return normalize_date(str(pd.Series(trade_dates).astype(str).max()))

    @contextmanager
    def _analysis_scope(self, as_of_date: str | None = None):
        previous = self._analysis_as_of_date
        self._analysis_as_of_date = self._normalize_as_of_date(as_of_date)
        try:
            yield self._analysis_as_of_date
        finally:
            self._analysis_as_of_date = previous

    def _get_stock_frame(self, code: str) -> pd.DataFrame:
        frame = self.repository.get_stock(
            code, cutoff_date=self._current_analysis_cutoff()
        )
        if frame.empty:
            return frame
        result = frame.copy()
        if "trade_date" in result.columns:
            result["trade_date"] = result["trade_date"].astype(str)
            result = result.sort_values("trade_date").reset_index(drop=True)
        return result

    def resolve_security(self, query: str) -> tuple[str, dict[str, Any]]:
        raw = str(query or "").strip()
        if not raw:
            raise ValueError("query is required")
        match = _STOCK_CODE_RE.search(raw)
        if match:
            token = match.group(0)
            code = self._normalize_code(token)
            sec = self.repository.query_securities([code])
            return code, (sec[0] if sec else {"code": code, "name": "", "industry": ""})
        candidates = self.repository.query_securities()
        for row in candidates:
            name = str(row.get("name") or "")
            code = str(row.get("code") or "")
            if raw == name or raw == code or raw in name or name in raw or code in raw:
                return code, row
        raise ValueError(f"无法识别股票: {raw}")

    def load_strategy(self, name: str) -> StockAnalysisStrategy:
        return self.strategy_store.load_strategy(name)

    def _build_gateway(self, *, model: str, api_key: str, api_base: str) -> LLMGateway:
        resolved = resolve_default_llm("fast", project_root=self._project_root)
        return LLMGateway(
            model=model or resolved.model or "",
            api_key=api_key or resolved.api_key or "",
            api_base=api_base or resolved.api_base or "",
            timeout=60,
            max_retries=2,
            unavailable_message=str(resolved.issue or ""),
        )

    def _resolve_strategy_name(
        self, *, question: str, strategy: str
    ) -> tuple[str, str]:
        explicit = str(strategy or "").strip()
        if explicit and explicit not in {"auto"}:
            if explicit != "chan_theory":
                return explicit, "explicit"
        low = str(question or "").lower()
        for item in self.list_strategies():
            names = [str(item.get("name") or ""), str(item.get("display_name") or "")]
            names.extend([str(x) for x in item.get("aliases") or []])
            for alias in names:
                token = alias.strip().lower()
                if token and token in low:
                    return str(
                        item.get("name") or explicit or "chan_theory"
                    ), "inferred"
        return explicit or "chan_theory", "default"

    @staticmethod
    def _infer_days(*, question: str, default_days: int) -> int:
        text = str(question or "")
        match = _DAY_COUNT_RE.search(text)
        if not match:
            return max(30, int(default_days or 60))
        return max(30, min(500, int(match.group(1))))

    def _run_react_executor(
        self,
        *,
        question: str,
        query: str,
        security: dict[str, Any],
        strategy: StockAnalysisStrategy,
        days: int,
        planning_bundle: StockExecutionPlanningBundle | None = None,
    ) -> dict[str, Any]:
        resolved_planning_bundle = planning_bundle or self._build_execution_planning_bundle(
            strategy=strategy,
            query=query,
            days=days,
        )
        if self.enable_llm_react and strategy.react_enabled and self.gateway.available:
            try:
                llm_execution = self._execute_plan_with_llm(
                    question=question,
                    query=query,
                    security=security,
                    strategy=strategy,
                    days=days,
                    allowed_tools=resolved_planning_bundle.allowed_tools,
                )
                if llm_execution.get("tool_calls"):
                    return self._apply_yaml_gap_fill(
                        execution=llm_execution,
                        strategy=strategy,
                        plan=resolved_planning_bundle.plan,
                        allowed_tools=resolved_planning_bundle.allowed_tools,
                    )
            except _STOCK_LLM_REACT_FALLBACK_EXCEPTIONS as exc:  # pragma: no cover
                logger.warning(
                    "stock llm react failed for %s via %s, fallback to yaml plan: %s",
                    query,
                    strategy.name,
                    exc,
                )
        return self._execute_plan_deterministic(
            plan=resolved_planning_bundle.plan,
            fallback_reason="llm_react_unavailable_or_empty",
        )

    @staticmethod
    def _normalize_code(token: str) -> str:
        raw = str(token or "").strip()
        if raw.lower().startswith(("sh.", "sz.")):
            return raw.lower()
        if "." in raw:
            code, market = raw.split(".", 1)
            return f"{market.lower()}.{code}"
        if raw.startswith(("6", "9")):
            return f"sh.{raw}"
        return f"sz.{raw}"


__all__ = [
    "BatchAnalysisViewService",
    "ResearchResolutionService",
    "StockAnalysisService",
    "StockAnalysisStrategy",
    "StockAnalysisStrategyStore",
    "StockToolPlanStep",
]
