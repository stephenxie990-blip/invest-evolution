"""Research resolution orchestration for stock analysis outputs."""

from __future__ import annotations

from typing import Any, Callable

from invest_evolution.application.stock_analysis_resolution_contracts import (
    FallbackDerivedAnalysisValues,
    FallbackPriceLevels,
    FallbackRiskSignals,
    FallbackScoreReasonComponents,
    FallbackStanceComponent,
    PreDashboardAnalysisSpec,
    ResearchPersistenceAttributionProjection,
    ResearchPersistenceProjection,
    ResearchResolutionArtifacts,
    ResearchResolutionAvailableDisplayAdapter,
    ResearchResolutionAvailableStageBundle,
    ResearchResolutionBasePayload,
    ResearchResolutionContext,
    ResearchResolutionDashboardProjectionFactory,
    ResearchResolutionDetailSectionUpdates,
    ResearchResolutionDetailUpdatesBundle,
    ResearchResolutionDisplayContract,
    ResearchResolutionDisplaySpec,
    ResearchResolutionIdentifiers,
    ResearchResolutionPayloadFactory,
    ResearchResolutionPersistence,
    ResearchResolutionRequestInputs,
    ResearchResolutionRuntimeInputs,
)
from invest_evolution.config import normalize_date
from invest_evolution.investment.research import (
    ResearchHypothesis,
    build_dashboard_projection,
    build_research_hypothesis,
)

_STOCK_RESEARCH_PERSISTENCE_EXCEPTIONS = (
    RuntimeError,
    ValueError,
    TypeError,
    LookupError,
    OSError,
)


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
    def _build_resolution_detail_updates_bundle(
        *,
        shared_updates: dict[str, Any] | None = None,
        research_updates: dict[str, Any] | None = None,
        research_bridge_updates: dict[str, Any] | None = None,
    ) -> ResearchResolutionDetailUpdatesBundle:
        return ResearchResolutionDetailUpdatesBundle(
            shared_updates=dict(shared_updates or {}),
            research=ResearchResolutionDetailSectionUpdates(
                payload_updates=dict(research_updates or {}),
            ),
            research_bridge=ResearchResolutionDetailSectionUpdates(
                payload_updates=dict(research_bridge_updates or {}),
            ),
        )

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
        return ResearchResolutionService._build_resolution_detail_updates_bundle(
            shared_updates={
                "error": str(research_bridge.get("error") or ""),
                "fallback": "canonical_dashboard_fallback",
                "details": dict(research_bridge.get("details") or {}),
            },
        )

    @staticmethod
    def _build_available_detail_updates_bundle(
        *,
        research_bridge: dict[str, Any],
        stages: ResearchResolutionAvailableStageBundle,
        identifiers: ResearchResolutionIdentifiers,
    ) -> ResearchResolutionDetailUpdatesBundle:
        return ResearchResolutionService._build_resolution_detail_updates_bundle(
            research_updates=ResearchResolutionService._build_research_detail_updates(
                snapshot=stages.artifacts.snapshot,
                policy=stages.artifacts.policy,
                hypothesis=stages.artifacts.hypothesis,
                scenario=stages.artifacts.scenario,
                persistence=stages.persistence.projection,
            ),
            research_bridge_updates=ResearchResolutionService._build_research_bridge_detail_updates(
                research_bridge=research_bridge,
                identifiers=identifiers,
            ),
        )

    @classmethod
    def _build_available_display_adapter(
        cls,
        *,
        research_bridge: dict[str, Any],
        stages: ResearchResolutionAvailableStageBundle,
    ) -> ResearchResolutionAvailableDisplayAdapter:
        identifiers = stages.persistence.display_identifiers()
        return ResearchResolutionAvailableDisplayAdapter(
            identifiers=identifiers,
            detail_updates=cls._build_available_detail_updates_bundle(
                research_bridge=research_bridge,
                stages=stages,
                identifiers=identifiers,
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
            request=self._build_resolution_request_inputs(
                question=question,
                query=query,
                strategy=strategy,
                strategy_source=strategy_source,
                code=code,
                requested_as_of_date=requested_as_of_date,
                effective_as_of_date=effective_as_of_date,
            ),
            runtime=self._build_resolution_runtime_inputs(
                execution=execution,
                derived=derived,
            ),
            display_contract=self._build_resolution_display_contract(
                research_bridge=research_bridge,
                strategy=strategy,
                requested_as_of_date=requested_as_of_date,
                effective_as_of_date=effective_as_of_date,
                dashboard_projection_builder=dashboard_projection_builder,
            ),
        )

    @staticmethod
    def _build_resolution_request_inputs(
        *,
        question: str,
        query: str,
        strategy: Any,
        strategy_source: str,
        code: str,
        requested_as_of_date: str,
        effective_as_of_date: str,
    ) -> ResearchResolutionRequestInputs:
        return ResearchResolutionRequestInputs(
            question=question,
            query=query,
            strategy=strategy,
            strategy_source=strategy_source,
            code=code,
            requested_as_of_date=requested_as_of_date,
            effective_as_of_date=effective_as_of_date,
        )

    @staticmethod
    def _build_resolution_runtime_inputs(
        *,
        execution: dict[str, Any],
        derived: dict[str, Any],
    ) -> ResearchResolutionRuntimeInputs:
        return ResearchResolutionRuntimeInputs(
            execution=dict(execution or {}),
            derived=dict(derived or {}),
        )

    def _build_resolution_display_contract(
        self,
        *,
        research_bridge: dict[str, Any],
        strategy: Any,
        requested_as_of_date: str,
        effective_as_of_date: str,
        dashboard_projection_builder: Callable[..., dict[str, Any]],
    ) -> ResearchResolutionDisplayContract:
        return ResearchResolutionDisplayContract(
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

    def _build_resolution_display_spec(
        self,
        *,
        context: ResearchResolutionContext,
        analysis: PreDashboardAnalysisSpec,
        identifiers: ResearchResolutionIdentifiers | None = None,
        detail_updates: ResearchResolutionDetailUpdatesBundle | None = None,
    ) -> ResearchResolutionDisplaySpec:
        resolved_identifiers = identifiers or self._build_resolution_identifiers()
        resolved_detail_updates = (
            detail_updates
            or self._build_resolution_detail_updates_bundle()
        )
        return context.display_contract.build_spec(
            analysis=analysis,
            identifiers=resolved_identifiers,
            detail_updates=resolved_detail_updates,
        )

    def _run_resolution_finalize_stage(
        self,
        *,
        spec: ResearchResolutionDisplaySpec,
    ) -> dict[str, Any]:
        return spec.to_payload()

    def _build_unavailable_display_spec(
        self,
        *,
        context: ResearchResolutionContext,
        research_bridge: dict[str, Any],
    ) -> ResearchResolutionDisplaySpec:
        analysis = self._run_fallback_analysis_stage(
            strategy=context.request.strategy,
            derived=context.runtime.derived,
            execution=context.runtime.execution,
        )
        detail_updates = self._build_unavailable_detail_updates_bundle(
            research_bridge=research_bridge,
        )
        return self._build_resolution_display_spec(
            context=context,
            analysis=analysis,
            detail_updates=detail_updates,
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
    def _resolve_analysis_stage_matched_signals(
        *,
        derived: dict[str, Any],
    ) -> list[str]:
        return list(derived.get("matched_signals") or [])

    @staticmethod
    def _build_analysis_stage_spec(
        *,
        hypothesis: ResearchHypothesis,
        matched_signals: list[str],
        supplemental_reason: str = "",
    ) -> PreDashboardAnalysisSpec:
        return ResearchResolutionService._build_pre_dashboard_analysis_spec(
            hypothesis=hypothesis,
            matched_signals=matched_signals,
            supplemental_reason=supplemental_reason,
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
        matched_signals = self._resolve_analysis_stage_matched_signals(
            derived=derived
        )
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
        return self._build_analysis_stage_spec(
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
        ).render(fallback_analysis)

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
            question=context.request.question,
            query=context.request.query,
            strategy=context.request.strategy,
            strategy_source=context.request.strategy_source,
            execution_mode=str(context.runtime.execution.get("mode") or ""),
            code=context.request.code,
            effective_as_of_date=context.request.effective_as_of_date,
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
        return self._build_analysis_stage_spec(
            hypothesis=artifacts.hypothesis,
            matched_signals=self._resolve_analysis_stage_matched_signals(
                derived=context.runtime.derived
            ),
        )

    def _run_available_resolution_stages(
        self,
        *,
        context: ResearchResolutionContext,
        research_bridge: dict[str, Any],
    ) -> ResearchResolutionAvailableStageBundle:
        artifacts = self._run_resolution_artifact_stage(
            research_bridge=research_bridge,
            strategy=context.request.strategy,
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

    def _build_available_display_spec(
        self,
        *,
        context: ResearchResolutionContext,
        research_bridge: dict[str, Any],
        stages: ResearchResolutionAvailableStageBundle,
    ) -> ResearchResolutionDisplaySpec:
        display_adapter = self._build_available_display_adapter(
            research_bridge=research_bridge,
            stages=stages,
        )
        return self._build_resolution_display_spec(
            context=context,
            analysis=stages.analysis,
            identifiers=display_adapter.identifiers,
            detail_updates=display_adapter.detail_updates,
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
                spec=self._build_unavailable_display_spec(
                    context=context,
                    research_bridge=research_bridge,
                ),
            )
        available_stages = self._run_available_resolution_stages(
            context=context,
            research_bridge=research_bridge,
        )
        return self._run_resolution_finalize_stage(
            spec=self._build_available_display_spec(
                context=context,
                research_bridge=research_bridge,
                stages=available_stages,
            ),
        )


__all__ = ["ResearchResolutionService"]
