"""Research-resolution contracts and display payload helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from invest_evolution.investment.research import ResearchHypothesis


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
class ResearchResolutionAvailableDisplayAdapter:
    identifiers: "ResearchResolutionIdentifiers"
    detail_updates: "ResearchResolutionDetailUpdatesBundle"


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

    def build(self, updates: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            **self.base_payload.to_payload(),
            **dict(updates or {}),
        }

    def build_display_spec(
        self,
        *,
        dashboard: dict[str, Any],
        identifiers: "ResearchResolutionIdentifiers",
        detail_updates: "ResearchResolutionDetailUpdatesBundle",
    ) -> "ResearchResolutionDisplaySpec":
        shared_updates = detail_updates.shared_updates
        return ResearchResolutionDisplaySpec(
            dashboard=dict(dashboard),
            research_payload=self.build(
                detail_updates.research.build_payload(
                    shared_updates=shared_updates
                )
            ),
            research_bridge_payload=self.build(
                detail_updates.research_bridge.build_payload(
                    shared_updates=shared_updates
                )
            ),
            identifiers=identifiers,
        )


@dataclass(frozen=True)
class ResearchResolutionDetailSectionUpdates:
    payload_updates: dict[str, Any]

    def build_payload(
        self,
        *,
        shared_updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            **dict(shared_updates or {}),
            **dict(self.payload_updates),
        }


@dataclass(frozen=True)
class ResearchResolutionDetailUpdatesBundle:
    shared_updates: dict[str, Any]
    research: ResearchResolutionDetailSectionUpdates
    research_bridge: ResearchResolutionDetailSectionUpdates


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

    def to_payload(self) -> dict[str, Any]:
        return {
            "dashboard": dict(self.dashboard),
            "research": dict(self.research_payload),
            "research_bridge": dict(self.research_bridge_payload),
            **self.identifiers.to_dict(),
        }


@dataclass(frozen=True)
class ResearchResolutionDisplayContract:
    dashboard_projection_factory: "ResearchResolutionDashboardProjectionFactory"
    payload_factory: ResearchResolutionPayloadFactory

    def build_spec(
        self,
        *,
        analysis: "PreDashboardAnalysisSpec",
        identifiers: ResearchResolutionIdentifiers,
        detail_updates: "ResearchResolutionDetailUpdatesBundle",
    ) -> ResearchResolutionDisplaySpec:
        return self.payload_factory.build_display_spec(
            dashboard=self.dashboard_projection_factory.render(analysis),
            identifiers=identifiers,
            detail_updates=detail_updates,
        )


@dataclass(frozen=True)
class DashboardProjectionSpec:
    hypothesis: "ResearchHypothesis"
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
    hypothesis: "ResearchHypothesis"
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
class ResearchResolutionRequestInputs:
    question: str
    query: str
    strategy: Any
    strategy_source: str
    code: str
    requested_as_of_date: str
    effective_as_of_date: str


@dataclass(frozen=True)
class ResearchResolutionRuntimeInputs:
    execution: dict[str, Any]
    derived: dict[str, Any]


@dataclass(frozen=True)
class ResearchResolutionContext:
    request: ResearchResolutionRequestInputs
    runtime: ResearchResolutionRuntimeInputs
    display_contract: ResearchResolutionDisplayContract


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


__all__ = [
    "DashboardProjectionSpec",
    "FallbackDerivedAnalysisValues",
    "FallbackPriceLevels",
    "FallbackRiskSignals",
    "FallbackScoreReasonComponents",
    "FallbackStanceComponent",
    "PreDashboardAnalysisSpec",
    "ResearchPersistenceAttributionProjection",
    "ResearchPersistenceProjection",
    "ResearchResolutionArtifacts",
    "ResearchResolutionAvailableDisplayAdapter",
    "ResearchResolutionAvailableStageBundle",
    "ResearchResolutionBasePayload",
    "ResearchResolutionContext",
    "ResearchResolutionDashboardProjectionBundle",
    "ResearchResolutionDashboardProjectionFactory",
    "ResearchResolutionDetailSectionUpdates",
    "ResearchResolutionDetailUpdatesBundle",
    "ResearchResolutionDisplayContract",
    "ResearchResolutionDisplaySpec",
    "ResearchResolutionIdentifiers",
    "ResearchResolutionPayloadFactory",
    "ResearchResolutionPersistence",
    "ResearchResolutionRequestInputs",
    "ResearchResolutionRuntimeInputs",
]
