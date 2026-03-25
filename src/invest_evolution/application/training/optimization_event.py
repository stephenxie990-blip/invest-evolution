"""Optimization event compatibility contracts for the training facade."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from invest_evolution.application.training import review_contracts as training_review_contracts
from invest_evolution.investment.shared.policy import resolve_governance_matrix


def _empty_review_applied_effects_payload() -> training_review_contracts.ReviewAppliedEffectsPayload:
    return {}


def _empty_review_decision_stage_payload() -> (
    training_review_contracts.ReviewDecisionOptimizationStagePayload
):
    return {}


def _empty_research_feedback_stage_payload() -> (
    training_review_contracts.ResearchFeedbackOptimizationStagePayload
):
    return {}


def _empty_llm_analysis_stage_payload() -> training_review_contracts.LlmAnalysisOptimizationStagePayload:
    return {}


def _empty_evolution_engine_stage_payload() -> (
    training_review_contracts.EvolutionEngineOptimizationStagePayload
):
    return {}


def _empty_runtime_config_mutation_stage_payload() -> (
    training_review_contracts.RuntimeConfigMutationOptimizationStagePayload
):
    return {}


def _empty_runtime_config_mutation_skipped_stage_payload() -> (
    training_review_contracts.RuntimeConfigMutationSkippedOptimizationStagePayload
):
    return {}


def _empty_optimization_error_stage_payload() -> training_review_contracts.OptimizationErrorStagePayload:
    return {}


@dataclass
class OptimizationEvent:
    trigger: str
    stage: str
    cycle_id: int | None = None
    status: str = "ok"
    suggestions: list[str] = field(default_factory=list)
    decision: dict[str, Any] = field(default_factory=dict)
    applied_change: dict[str, Any] = field(default_factory=dict)
    lineage: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    review_applied_effects_payload: training_review_contracts.ReviewAppliedEffectsPayload = (
        field(default_factory=_empty_review_applied_effects_payload)
    )
    review_decision_payload: training_review_contracts.ReviewDecisionOptimizationStagePayload = (
        field(default_factory=_empty_review_decision_stage_payload)
    )
    research_feedback_payload: training_review_contracts.ResearchFeedbackOptimizationStagePayload = (
        field(default_factory=_empty_research_feedback_stage_payload)
    )
    llm_analysis_payload: training_review_contracts.LlmAnalysisOptimizationStagePayload = (
        field(default_factory=_empty_llm_analysis_stage_payload)
    )
    evolution_engine_payload: training_review_contracts.EvolutionEngineOptimizationStagePayload = (
        field(default_factory=_empty_evolution_engine_stage_payload)
    )
    runtime_config_mutation_payload: training_review_contracts.RuntimeConfigMutationOptimizationStagePayload = (
        field(default_factory=_empty_runtime_config_mutation_stage_payload)
    )
    runtime_config_mutation_skipped_payload: training_review_contracts.RuntimeConfigMutationSkippedOptimizationStagePayload = (
        field(default_factory=_empty_runtime_config_mutation_skipped_stage_payload)
    )
    optimization_error_payload: training_review_contracts.OptimizationErrorStagePayload = (
        field(default_factory=_empty_optimization_error_stage_payload)
    )
    event_id: str = field(default_factory=lambda: f"opt_{uuid4().hex[:12]}")
    contract_version: str = field(
        default_factory=lambda: str(
            resolve_governance_matrix().get("optimization", {}).get("contract_version", "optimization_event.v2")
        )
    )
    ts: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> training_review_contracts.OptimizationEventPayload:
        payload = cast(
            training_review_contracts.OptimizationEventPayload,
            {
                "event_id": self.event_id,
                "contract_version": self.contract_version,
                "cycle_id": self.cycle_id,
                "trigger": self.trigger,
                "stage": self.stage,
                "status": self.status,
                "suggestions": list(self.suggestions),
                "decision": dict(self.decision),
                "applied_change": dict(self.applied_change),
                "lineage": dict(self.lineage),
                "evidence": dict(self.evidence),
                "notes": self.notes,
                "ts": self.ts,
            },
        )
        if self.review_applied_effects_payload:
            payload["review_applied_effects_payload"] = cast(
                training_review_contracts.ReviewAppliedEffectsPayload,
                dict(self.review_applied_effects_payload),
            )
        if self.review_decision_payload:
            payload["review_decision_payload"] = cast(
                training_review_contracts.ReviewDecisionOptimizationStagePayload,
                dict(self.review_decision_payload),
            )
        if self.research_feedback_payload:
            payload["research_feedback_payload"] = cast(
                training_review_contracts.ResearchFeedbackOptimizationStagePayload,
                dict(self.research_feedback_payload),
            )
        if self.llm_analysis_payload:
            payload["llm_analysis_payload"] = cast(
                training_review_contracts.LlmAnalysisOptimizationStagePayload,
                dict(self.llm_analysis_payload),
            )
        if self.evolution_engine_payload:
            payload["evolution_engine_payload"] = cast(
                training_review_contracts.EvolutionEngineOptimizationStagePayload,
                dict(self.evolution_engine_payload),
            )
        if self.runtime_config_mutation_payload:
            payload["runtime_config_mutation_payload"] = cast(
                training_review_contracts.RuntimeConfigMutationOptimizationStagePayload,
                dict(self.runtime_config_mutation_payload),
            )
        if self.runtime_config_mutation_skipped_payload:
            payload["runtime_config_mutation_skipped_payload"] = cast(
                training_review_contracts.RuntimeConfigMutationSkippedOptimizationStagePayload,
                dict(self.runtime_config_mutation_skipped_payload),
            )
        if self.optimization_error_payload:
            payload["optimization_error_payload"] = cast(
                training_review_contracts.OptimizationErrorStagePayload,
                dict(self.optimization_error_payload),
            )
        return payload

