"""Ask-stock response and execution projection contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from invest_evolution.agent_runtime.presentation import build_protocol_response


@dataclass(frozen=True)
class AskStockResponseRequestHeader:
    request: "AskStockRequestProjection"

    def to_payload(self) -> dict[str, Any]:
        request = self.request.to_payload()
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
    identifiers: "AskStockIdentifiersProjection"
    security: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        identifiers = self.identifiers.to_payload()
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
    ) -> "ToolObservationEnvelope":
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
class AskStockRequestProjection:
    question: str
    query: str
    normalized_query: str
    requested_as_of_date: str
    as_of_date: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "query": self.query,
            "normalized_query": self.normalized_query,
            "requested_as_of_date": self.requested_as_of_date,
            "as_of_date": self.as_of_date,
        }


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
class AskStockExecutionSurfaceSpec:
    task_bus: dict[str, Any]
    protocol: dict[str, Any]
    artifacts: dict[str, Any]
    coverage: dict[str, Any]
    artifact_taxonomy: dict[str, Any]
    orchestration_extra: dict[str, Any]

    def to_presentation_spec(
        self,
        *,
        sections: "AskStockSectionsBundle",
    ) -> "AskStockPresentationSpec":
        return AskStockPresentationSpec(
            task_bus=dict(self.task_bus),
            protocol=dict(self.protocol),
            artifacts=dict(self.artifacts),
            coverage=dict(self.coverage),
            artifact_taxonomy=dict(self.artifact_taxonomy),
            sections=sections,
        )


@dataclass(frozen=True)
class AskStockPresentationSpec:
    task_bus: dict[str, Any]
    protocol: dict[str, Any]
    artifacts: dict[str, Any]
    coverage: dict[str, Any]
    artifact_taxonomy: dict[str, Any]
    sections: "AskStockSectionsBundle"


@dataclass(frozen=True)
class AskStockResponseAssemblySpec:
    header_factory: AskStockResponseHeaderFactory
    presentation_spec: AskStockPresentationSpec
    orchestration: dict[str, Any]
    dashboard: dict[str, Any]

    def build_payload(self) -> dict[str, Any]:
        return {
            "status": "ok",
            **self.header_factory.build(),
            "task_bus": dict(self.presentation_spec.task_bus),
            "orchestration": dict(self.orchestration),
            "analysis": dict(self.presentation_spec.sections.analysis),
            "research": dict(self.presentation_spec.sections.research),
            "dashboard": dict(self.dashboard),
        }

    def render_protocol_response(self) -> dict[str, Any]:
        return build_protocol_response(
            payload=self.build_payload(),
            protocol=dict(self.presentation_spec.protocol),
            task_bus=dict(self.presentation_spec.task_bus),
            artifacts=dict(self.presentation_spec.artifacts),
            coverage=dict(self.presentation_spec.coverage),
            artifact_taxonomy=dict(self.presentation_spec.artifact_taxonomy),
            default_reply="已完成问股分析。",
        )


@dataclass(frozen=True)
class AskStockResponseInputs:
    header_factory: AskStockResponseHeaderFactory
    presentation_spec: AskStockPresentationSpec
    orchestration: dict[str, Any]
    dashboard: dict[str, Any]


@dataclass(frozen=True)
class AskStockExecutionStageAdapter:
    identifiers: AskStockIdentifiersProjection
    execution_projection: AskStockExecutionProjection
    execution_surface_spec: AskStockExecutionSurfaceSpec


@dataclass(frozen=True)
class AskStockSectionsBundle:
    analysis: dict[str, Any]
    research: dict[str, Any]

