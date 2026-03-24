"""Training review services and analysis owners."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import logging
from typing import Any, cast

from invest_evolution.application.training.controller import (
    append_session_cycle_record,
    session_current_params,
    session_cycle_history,
)
from invest_evolution.application.training.execution import (
    apply_review_decision_boundary_effects,
    project_manager_compatibility,
    resolve_payload_manager_identity,
)
from invest_evolution.application.training.observability import (
    build_review_eval_projection_boundary,
    build_review_boundary_event,
    finalize_review_boundary_effects,
    record_review_boundary_artifacts,
)
from invest_evolution.application.training.policy import (
    governance_from_item,
    governance_regime,
)
from invest_evolution.application.training.review_contracts import (
    AllocationReviewDigestPayload,
    GovernanceDecisionInputPayload,
    ManagerReviewDigestPayload,
    OptimizationInputEnvelope as OptimizationInputEnvelope,
    ReviewDecisionInputPayload,
    ReviewStageEnvelope as ReviewStageEnvelope,
    SimilarResultCompactPayload,
    SimilaritySummaryInputPayload,
    SimulationStageEnvelope,
    StrategyScoresInputPayload,
    ValidationInputEnvelope as ValidationInputEnvelope,
    build_cycle_contract_stage_snapshots as build_cycle_contract_stage_snapshots,
    build_cycle_run_context as build_cycle_run_context,
    build_execution_snapshot as build_execution_snapshot,
    build_outcome_stage_snapshot as build_outcome_stage_snapshot,
    project_allocation_review_digest,
    project_manager_review_digest,
    build_review_basis_window,
    build_validation_stage_snapshot as build_validation_stage_snapshot,
)
from invest_evolution.investment.contracts import (
    AllocationReviewReport,
    EvalReport,
    ManagerReviewReport,
)


def _dict_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    try:
        return dict(value)
    except Exception:
        return {}


def _list_payload(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if value is None:
        return []
    try:
        return list(value)
    except Exception:
        return []


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _plan_source(selection_mode: str, llm_used: bool) -> str:
    if selection_mode.startswith("meeting"):
        return "meeting"
    if llm_used:
        return "llm"
    return "algorithm"


def _coerce_governance_decision_input(
    payload: GovernanceDecisionInputPayload | dict[str, Any] | None = None,
) -> GovernanceDecisionInputPayload:
    return cast(GovernanceDecisionInputPayload, _dict_payload(payload))


def _coerce_strategy_scores_input(
    payload: StrategyScoresInputPayload | dict[str, Any] | None = None,
) -> StrategyScoresInputPayload:
    return cast(StrategyScoresInputPayload, _dict_payload(payload))


def _coerce_similarity_summary_input(
    payload: SimilaritySummaryInputPayload | dict[str, Any] | None = None,
) -> SimilaritySummaryInputPayload:
    return cast(SimilaritySummaryInputPayload, _dict_payload(payload))


def _coerce_review_decision_input(
    payload: ReviewDecisionInputPayload | dict[str, Any] | None = None,
) -> ReviewDecisionInputPayload:
    return cast(ReviewDecisionInputPayload, _dict_payload(payload))


def _simulation_metric_from_context_or_payload(
    simulation_envelope: SimulationStageEnvelope | None,
    cycle_payload: dict[str, Any],
    *,
    field_name: str,
    default: Any,
) -> Any:
    if simulation_envelope is not None:
        return getattr(simulation_envelope, field_name)
    return cycle_payload.get(field_name, default)


def _build_eval_report_metadata(
    *,
    review_projection: Any,
    trade_dicts: list[dict[str, Any]],
    requested_data_mode: str,
    effective_data_mode: str,
    llm_mode: str,
    degraded: bool,
    degrade_reason: str,
    research_feedback: dict[str, Any] | None,
    manager_results: list[dict[str, Any]],
    portfolio_plan: dict[str, Any],
    dominant_manager_id: str,
    manager_review_report: ManagerReviewDigestPayload | dict[str, Any] | None,
    allocation_review_report: AllocationReviewDigestPayload | dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "manager_id": review_projection.manager_id,
        "manager_config_ref": review_projection.manager_config_ref,
        "trade_count": len(trade_dicts),
        "requested_data_mode": requested_data_mode,
        "effective_data_mode": effective_data_mode,
        "llm_mode": llm_mode,
        "degraded": degraded,
        "degrade_reason": degrade_reason,
        "research_feedback": dict(research_feedback or {}),
        "subject_type": review_projection.subject_type,
        "manager_results": manager_results,
        "portfolio_plan": portfolio_plan,
        "dominant_manager_id": str(dominant_manager_id or ""),
        "compatibility_fields": dict(review_projection.compatibility_fields or {}),
        "manager_review_report": dict(manager_review_report or {}),
        "allocation_review_report": dict(allocation_review_report or {}),
    }


def _coerce_similar_result_compact(
    payload: SimilarResultCompactPayload | dict[str, Any] | None = None,
) -> SimilarResultCompactPayload:
    item = _dict_payload(payload)
    return cast(
        SimilarResultCompactPayload,
        {
            "cycle_id": _coerce_int(item.get("cycle_id")),
            "cutoff_date": str(item.get("cutoff_date") or ""),
            "return_pct": _coerce_float(item.get("return_pct")),
            "is_profit": bool(item.get("is_profit", False)),
            "benchmark_passed": bool(item.get("benchmark_passed", False)),
            "review_applied": bool(item.get("review_applied", False)),
            "regime": str(item.get("regime") or "unknown"),
            "selection_mode": str(item.get("selection_mode") or "unknown"),
            "manager_id": str(item.get("manager_id") or ""),
            "manager_config_ref": str(item.get("manager_config_ref") or ""),
            "similarity_score": _coerce_int(
                item.get("similarity_score", item.get("score"))
            ),
            "matched_features": [
                str(value)
                for value in _list_payload(item.get("matched_features"))
                if str(value or "").strip()
            ],
            "strict_failure_match": bool(item.get("strict_failure_match", False)),
            "evidence_score": _coerce_int(item.get("evidence_score")),
            "failure_signature": _dict_payload(item.get("failure_signature")),
        },
    )


def _compact_similar_result_payload(
    candidate: "NormalizedReviewResult",
    *,
    similarity_score: int,
    matched_features: list[str],
    strict_failure_match: bool,
) -> SimilarResultCompactPayload:
    return cast(
        SimilarResultCompactPayload,
        {
            "cycle_id": int(candidate.cycle_id),
            "cutoff_date": str(candidate.cutoff_date or ""),
            "return_pct": float(candidate.return_pct),
            "is_profit": bool(candidate.is_profit),
            "benchmark_passed": bool(candidate.benchmark_passed),
            "review_applied": bool(candidate.review_applied),
            "regime": str(candidate.regime or "unknown"),
            "selection_mode": str(candidate.selection_mode or "unknown"),
            "manager_id": str(candidate.manager_id or ""),
            "manager_config_ref": str(candidate.manager_config_ref or ""),
            "similarity_score": int(similarity_score),
            "matched_features": [
                str(item)
                for item in matched_features
                if str(item or "").strip()
            ],
            "strict_failure_match": bool(strict_failure_match),
            "evidence_score": _evidence_support_score(candidate),
            "failure_signature": _failure_signature(candidate),
        },
    )


@dataclass(frozen=True)
class NormalizedReviewResult:
    cycle_id: int
    cutoff_date: str
    return_pct: float
    is_profit: bool
    selection_mode: str
    plan_source: str
    benchmark_passed: bool
    review_applied: bool
    regime: str
    llm_used: bool
    manager_id: str
    manager_config_ref: str
    governance_decision: GovernanceDecisionInputPayload = field(
        default_factory=lambda: cast(GovernanceDecisionInputPayload, {})
    )
    strategy_scores: StrategyScoresInputPayload = field(
        default_factory=lambda: cast(StrategyScoresInputPayload, {})
    )
    research_feedback: dict[str, Any] = field(default_factory=dict)
    causal_diagnosis: dict[str, Any] = field(default_factory=dict)
    similarity_summary: SimilaritySummaryInputPayload = field(
        default_factory=lambda: cast(SimilaritySummaryInputPayload, {})
    )
    review_decision: ReviewDecisionInputPayload = field(
        default_factory=lambda: cast(ReviewDecisionInputPayload, {})
    )
    ab_comparison: dict[str, Any] = field(default_factory=dict)
    evidence_score: int = 0
    failure_signature: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": int(self.cycle_id),
            "cutoff_date": str(self.cutoff_date or ""),
            "as_of_date": str(self.cutoff_date or ""),
            "return_pct": float(self.return_pct),
            "is_profit": bool(self.is_profit),
            "selection_mode": str(self.selection_mode or "unknown"),
            "plan_source": str(self.plan_source or "unknown"),
            "benchmark_passed": bool(self.benchmark_passed),
            "review_applied": bool(self.review_applied),
            "regime": str(self.regime or "unknown"),
            "llm_used": bool(self.llm_used),
            "failure_signature": deepcopy(dict(self.failure_signature or {})),
            "evidence_score": int(self.evidence_score),
            "metadata": {
                "manager_id": str(self.manager_id or ""),
                "manager_config_ref": str(self.manager_config_ref or ""),
                "governance_decision": deepcopy(dict(self.governance_decision or {})),
                "strategy_scores": deepcopy(dict(self.strategy_scores or {})),
                "research_feedback": deepcopy(dict(self.research_feedback or {})),
                "causal_diagnosis": deepcopy(dict(self.causal_diagnosis or {})),
                "similarity_summary": deepcopy(dict(self.similarity_summary or {})),
                "review_decision": deepcopy(dict(self.review_decision or {})),
                "ab_comparison": deepcopy(dict(self.ab_comparison or {})),
            },
        }


def _normalize_review_result(
    payload: dict[str, Any],
    *,
    controller: Any | None = None,
) -> NormalizedReviewResult:
    record = dict(payload or {})
    metadata = dict(record.get("metadata") or {})
    selection_mode = str(record.get("selection_mode") or "unknown")
    llm_used = bool(record.get("llm_used", metadata.get("llm_used", False)))
    manager_id, manager_config_ref = resolve_payload_manager_identity(
        record,
        controller=controller,
    )
    governance_decision = _coerce_governance_decision_input(
        metadata.get("governance_decision") or record.get("governance_decision")
    )
    strategy_scores = _coerce_strategy_scores_input(
        metadata.get("strategy_scores") or record.get("strategy_scores")
    )
    regime = str(
        record.get("regime")
        or metadata.get("regime")
        or governance_decision.get("regime")
        or "unknown"
    )
    research_feedback = _dict_payload(
        metadata.get("research_feedback") or record.get("research_feedback")
    )
    causal_diagnosis = _dict_payload(
        metadata.get("causal_diagnosis") or record.get("causal_diagnosis")
    )
    similarity_summary = _coerce_similarity_summary_input(
        metadata.get("similarity_summary") or record.get("similarity_summary")
    )
    review_decision = _coerce_review_decision_input(
        metadata.get("review_decision") or record.get("review_decision")
    )
    ab_comparison = _dict_payload(
        metadata.get("ab_comparison") or record.get("ab_comparison")
    )
    normalized = NormalizedReviewResult(
        cycle_id=_coerce_int(record.get("cycle_id")),
        cutoff_date=str(record.get("cutoff_date") or record.get("as_of_date") or ""),
        return_pct=_coerce_float(record.get("return_pct")),
        is_profit=bool(record.get("is_profit", _coerce_float(record.get("return_pct")) > 0)),
        selection_mode=selection_mode,
        plan_source=str(record.get("plan_source") or _plan_source(selection_mode, llm_used)),
        benchmark_passed=bool(record.get("benchmark_passed", False)),
        review_applied=bool(record.get("review_applied", False)),
        regime=regime,
        llm_used=llm_used,
        manager_id=manager_id,
        manager_config_ref=manager_config_ref,
        governance_decision=governance_decision,
        strategy_scores=strategy_scores,
        research_feedback=research_feedback,
        causal_diagnosis=causal_diagnosis,
        similarity_summary=similarity_summary,
        review_decision=review_decision,
        ab_comparison=ab_comparison,
    )
    failure_signature = _failure_signature(normalized)
    evidence_score = _evidence_support_score(normalized)
    return NormalizedReviewResult(
        **{
            **normalized.__dict__,
            "failure_signature": failure_signature,
            "evidence_score": evidence_score,
        }
    )


def _history_record_to_review_result(item: Any) -> NormalizedReviewResult:
    governance_decision = _coerce_governance_decision_input(governance_from_item(item))
    audit_tags = dict(getattr(item, "audit_tags", {}) or {})
    research_feedback = dict(getattr(item, "research_feedback", {}) or {})
    selection_mode = str(getattr(item, "selection_mode", "unknown") or "unknown")
    llm_used = bool(getattr(item, "llm_used", False))
    execution_defaults = dict(getattr(item, "execution_defaults", {}) or {})
    run_context = dict(getattr(item, "run_context", {}) or {})
    history_projection = project_manager_compatibility(
        None,
        governance_decision=governance_decision,
        portfolio_plan=dict(getattr(item, "portfolio_plan", {}) or {}),
        manager_results=list(getattr(item, "manager_results", []) or []),
        execution_snapshot={
            **run_context,
            "execution_defaults": execution_defaults,
            "dominant_manager_id": str(
                getattr(item, "dominant_manager_id", "")
                or governance_decision.get("dominant_manager_id")
                or run_context.get("dominant_manager_id")
                or ""
            ),
            "manager_config_ref": str(
                run_context.get("manager_config_ref")
                or execution_defaults.get("default_manager_config_ref")
                or run_context.get("active_runtime_config_ref")
                or ""
            ),
            "active_runtime_config_ref": str(
                run_context.get("active_runtime_config_ref")
                or execution_defaults.get("default_manager_config_ref")
                or run_context.get("manager_config_ref")
                or ""
            ),
        },
        dominant_manager_id_hint=str(
            getattr(item, "dominant_manager_id", "")
            or governance_decision.get("dominant_manager_id")
            or ""
        ),
    )

    return _normalize_review_result(
        {
            "cycle_id": int(getattr(item, "cycle_id")),
            "as_of_date": str(getattr(item, "cutoff_date", "") or ""),
            "return_pct": float(getattr(item, "return_pct", 0.0) or 0.0),
            "is_profit": bool(getattr(item, "is_profit", False)),
            "selection_mode": selection_mode,
            "plan_source": _plan_source(selection_mode, llm_used),
            "benchmark_passed": bool(getattr(item, "benchmark_passed", False)),
            "review_applied": bool(getattr(item, "review_applied", False)),
            "regime": governance_regime(
                dict(governance_decision),
                default=str(audit_tags.get("governance_regime") or "unknown"),
            ),
            "metadata": {
                "manager_id": str(history_projection.manager_id or ""),
                "manager_config_ref": str(history_projection.manager_config_ref or ""),
                "governance_decision": governance_decision,
                "strategy_scores": _coerce_strategy_scores_input(
                    getattr(item, "strategy_scores", {}) or {}
                ),
                "research_feedback": research_feedback,
                "causal_diagnosis": dict(getattr(item, "causal_diagnosis", {}) or {}),
                "similarity_summary": _coerce_similarity_summary_input(
                    getattr(item, "similarity_summary", {}) or {}
                ),
                "review_decision": _coerce_review_decision_input(
                    getattr(item, "review_decision", {}) or {}
                ),
                "ab_comparison": dict(getattr(item, "ab_comparison", {}) or {}),
            },
            "governance_decision": governance_decision,
            "strategy_scores": _coerce_strategy_scores_input(
                getattr(item, "strategy_scores", {}) or {}
            ),
            "research_feedback": research_feedback,
            "causal_diagnosis": dict(getattr(item, "causal_diagnosis", {}) or {}),
            "similarity_summary": _coerce_similarity_summary_input(
                getattr(item, "similarity_summary", {}) or {}
            ),
            "review_decision": _coerce_review_decision_input(
                getattr(item, "review_decision", {}) or {}
            ),
            "ab_comparison": dict(getattr(item, "ab_comparison", {}) or {}),
            "llm_used": llm_used,
        }
    )


def _feedback_bias(record: NormalizedReviewResult) -> str:
    feedback = dict(record.research_feedback or {})
    recommendation = dict(feedback.get("recommendation") or {})
    return str(recommendation.get("bias") or "").strip()


def _primary_driver(record: NormalizedReviewResult) -> str:
    diagnosis = dict(record.causal_diagnosis or {})
    return str(diagnosis.get("primary_driver") or "").strip()


def _evidence_support_score(record: NormalizedReviewResult) -> int:
    score = 0
    if _list_payload(record.similarity_summary.get("matched_cycle_ids")):
        score += 1
    diagnosis = _dict_payload(record.causal_diagnosis)
    drivers = [_dict_payload(item) for item in _list_payload(diagnosis.get("drivers"))]
    if any(_list_payload(item.get("evidence_cycle_ids")) for item in drivers):
        score += 1
    feedback = _dict_payload(record.research_feedback)
    if int(feedback.get("sample_count") or 0) > 0:
        score += 1
    return score


def _failure_signature(record: NormalizedReviewResult) -> dict[str, Any]:
    return {
        "return_direction": "profit" if bool(record.is_profit) else "loss",
        "benchmark_passed": bool(record.benchmark_passed),
        "primary_driver": _primary_driver(record),
        "feedback_bias": _feedback_bias(record),
    }


def _strict_failure_match(
    candidate: NormalizedReviewResult,
    current_result: NormalizedReviewResult,
) -> bool:
    if bool(current_result.is_profit):
        return bool(candidate.is_profit) == bool(current_result.is_profit)
    if bool(candidate.is_profit):
        return False
    current_regime = str(current_result.regime or "")
    if current_regime not in {"", "unknown"} and str(candidate.regime or "") != current_regime:
        return False
    if not bool(current_result.benchmark_passed) and bool(candidate.benchmark_passed):
        return False
    current_driver = _primary_driver(current_result)
    candidate_driver = _primary_driver(candidate)
    if current_driver and candidate_driver and candidate_driver != current_driver:
        return False
    current_bias = _feedback_bias(current_result)
    candidate_bias = _feedback_bias(candidate)
    if current_bias and candidate_bias and candidate_bias != current_bias:
        return False
    return True


def _similarity_score(
    candidate: NormalizedReviewResult,
    current_result: NormalizedReviewResult,
) -> tuple[int, list[str]]:
    matched_features: list[str] = []
    score = 0
    if str(candidate.regime or "") == str(current_result.regime or "") and str(
        current_result.regime or ""
    ) not in {"", "unknown"}:
        score += 4
        matched_features.append("regime")
    if str(candidate.selection_mode or "") == str(
        current_result.selection_mode or ""
    ) and str(current_result.selection_mode or "") not in {"", "unknown"}:
        score += 3
        matched_features.append("selection_mode")
    if bool(candidate.benchmark_passed) == bool(current_result.benchmark_passed):
        score += 2
        matched_features.append("benchmark_passed")
    if str(candidate.plan_source or "") == str(current_result.plan_source or "") and str(
        current_result.plan_source or ""
    ) not in {"", "unknown"}:
        score += 2
        matched_features.append("plan_source")
    if str(candidate.manager_id or "") == str(current_result.manager_id or "") and str(
        current_result.manager_id or ""
    ):
        score += 2
        matched_features.append("manager_id")
    if str(candidate.manager_config_ref or "") == str(current_result.manager_config_ref or "") and str(
        current_result.manager_config_ref or ""
    ):
        score += 1
        matched_features.append("manager_config_ref")

    if _primary_driver(candidate) and _primary_driver(candidate) == _primary_driver(current_result):
        score += 3
        matched_features.append("primary_driver")
    if _feedback_bias(candidate) and _feedback_bias(candidate) == _feedback_bias(current_result):
        score += 2
        matched_features.append("feedback_bias")

    current_sign = 1 if bool(current_result.is_profit) else -1
    candidate_sign = 1 if bool(candidate.is_profit) else -1
    if candidate_sign == current_sign:
        score += 1
        matched_features.append("return_direction")
    if _strict_failure_match(candidate, current_result):
        score += 4
        matched_features.append("failure_signature")
    evidence_score = _evidence_support_score(candidate)
    if evidence_score > 0:
        score += min(2, evidence_score)
        matched_features.append("structured_evidence")
    return score, matched_features


def _build_similar_results(
    controller: Any,
    *,
    cycle_id: int,
    current_result: NormalizedReviewResult,
    limit: int = 3,
) -> tuple[list[SimilarResultCompactPayload], SimilaritySummaryInputPayload]:
    minimum_score = 5
    ranked: list[tuple[int, int, NormalizedReviewResult, list[str]]] = []
    history = list(session_cycle_history(controller) or [])
    requires_strict_failure_match = not bool(current_result.is_profit)
    if requires_strict_failure_match:
        minimum_score = 7
    for item in history:
        item_cycle_id = getattr(item, "cycle_id", None)
        if item_cycle_id is None or _coerce_int(item_cycle_id) == int(cycle_id):
            continue
        candidate = _history_record_to_review_result(item)
        if requires_strict_failure_match and not _strict_failure_match(candidate, current_result):
            continue
        score, matched_features = _similarity_score(candidate, current_result)
        if score < minimum_score:
            continue
        ranked.append((score, candidate.cycle_id, candidate, matched_features))

    ranked.sort(key=lambda item: (-item[0], -item[1]))
    selected: list[SimilarResultCompactPayload] = []
    aggregated_features: list[str] = []
    for score, _, candidate, matched_features in ranked[:limit]:
        selected.append(
            _compact_similar_result_payload(
                candidate,
                similarity_score=score,
                matched_features=matched_features,
                strict_failure_match=_strict_failure_match(candidate, current_result),
            )
        )
        for feature in matched_features:
            if feature not in aggregated_features:
                aggregated_features.append(feature)

    regimes = [
        str(item.get("regime") or "unknown")
        for item in selected
        if str(item.get("regime") or "").strip()
    ]
    dominant_regime = str(current_result.regime or "unknown")
    if regimes:
        dominant_regime = max(set(regimes), key=regimes.count)
    matched_primary_driver = _primary_driver(current_result)
    matched_feedback_bias = _feedback_bias(current_result)
    top_score = int(ranked[0][0]) if ranked else 0
    if top_score >= 18:
        similarity_band = "high"
    elif top_score >= 10:
        similarity_band = "medium"
    elif ranked:
        similarity_band = "low"
    else:
        similarity_band = "none"

    summary = cast(
        SimilaritySummaryInputPayload,
        {
        "target_cycle_id": int(cycle_id),
        "matched_cycle_ids": [int(item["cycle_id"]) for item in selected],
        "matched_cycle_ids_truncated": len(ranked) > limit,
        "match_count": len(selected),
        "match_features": aggregated_features,
        "dominant_regime": dominant_regime,
        "similarity_band": similarity_band,
        "summary": (
            f"found {len(selected)} similar cycle(s) across {len(history)} reviewed history records"
            if selected
            else "no sufficiently similar historical cycles found"
        ),
        "compared_history_size": len(history),
        "strict_failure_match_count": sum(
            1 for item in selected if bool(item.get("strict_failure_match", False))
        ),
        "matched_primary_driver": matched_primary_driver,
        "matched_feedback_bias": matched_feedback_bias,
        "avg_evidence_score": round(
            _coerce_float(
                sum(int(item.get("evidence_score") or 0) for item in selected)
                / len(selected)
                if selected
                else 0.0
            ),
            2,
        ),
        },
    )
    return selected, summary


def _build_causal_diagnosis(
    *,
    current_result: NormalizedReviewResult,
    recent_results: list[NormalizedReviewResult],
    similar_results: list[SimilarResultCompactPayload],
) -> dict[str, Any]:
    if not similar_results:
        return {
            "primary_driver": "insufficient_history",
            "summary": "历史相似样本不足，当前先沿 rolling facts 做轻量复盘。",
            "drivers": [],
            "evidence": {"matched_cycle_ids": []},
        }

    drivers: list[dict[str, Any]] = []
    same_regime_losses = [
        item
        for item in similar_results
        if str(item.get("regime") or "") == str(current_result.regime or "")
        and not bool(item.get("is_profit", False))
    ]
    if not bool(current_result.is_profit) and same_regime_losses:
        drivers.append(
            {
                "code": "regime_repeat_loss",
                "label": "同一市场状态下重复亏损",
                "score": round(min(0.8, 0.35 + 0.1 * len(same_regime_losses)), 2),
                "evidence_cycle_ids": [int(item["cycle_id"]) for item in same_regime_losses],
            }
        )

    benchmark_failures = [
        item for item in similar_results if not bool(item.get("benchmark_passed", False))
    ]
    if not bool(current_result.benchmark_passed) and benchmark_failures:
        drivers.append(
            {
                "code": "benchmark_gap",
                "label": "相似样本普遍未跑赢基准",
                "score": round(min(0.7, 0.2 + 0.08 * len(benchmark_failures)), 2),
                "evidence_cycle_ids": [int(item["cycle_id"]) for item in benchmark_failures],
            }
        )

    unapplied_reviews = [
        item for item in recent_results[:-1] if not bool(item.review_applied)
    ]
    if unapplied_reviews:
        drivers.append(
            {
                "code": "review_not_applied",
                "label": "近几轮复盘未形成有效修正",
                "score": round(min(0.6, 0.18 + 0.07 * len(unapplied_reviews)), 2),
                "evidence_cycle_ids": [int(item.cycle_id) for item in unapplied_reviews],
            }
        )

    selection_mode_cluster = [
        item
        for item in similar_results
        if str(item.get("selection_mode") or "") == str(current_result.selection_mode or "")
    ]
    if (
        str(current_result.selection_mode or "") not in {"", "unknown"}
        and len(selection_mode_cluster) >= 2
    ):
        drivers.append(
            {
                "code": "selection_mode_cluster",
                "label": "相似样本集中在同一决策模式",
                "score": round(min(0.5, 0.16 + 0.05 * len(selection_mode_cluster)), 2),
                "evidence_cycle_ids": [int(item["cycle_id"]) for item in selection_mode_cluster],
            }
        )

    drivers.sort(
        key=lambda item: (-float(item.get("score") or 0.0), str(item.get("code") or ""))
    )
    if drivers:
        summary = (
            f"{drivers[0].get('label')}"
            + (f"，其次是{drivers[1].get('label')}" if len(drivers) > 1 else "")
            + "，建议先围绕首要驱动逐步收敛参数。"
        )
        primary_driver = str(drivers[0].get("code") or "mixed_factors")
    else:
        primary_driver = "mixed_factors"
        summary = "相似样本已检索，但未出现足够集中的单一失效模式。"
    return {
        "primary_driver": primary_driver,
        "summary": summary,
        "drivers": drivers,
        "evidence": {
            "matched_cycle_ids": [int(item["cycle_id"]) for item in similar_results],
            "current_cycle_id": int(current_result.cycle_id or 0),
        },
    }


def build_review_input(
    controller: Any,
    *,
    cycle_id: int,
    eval_report: EvalReport | dict[str, Any],
) -> dict[str, Any]:
    review_basis_window = build_review_basis_window(
        controller,
        cycle_id=int(cycle_id),
        review_window=dict(getattr(controller, "experiment_review_window", {}) or {}),
    )
    if isinstance(eval_report, dict):
        current_result = _normalize_review_result(dict(eval_report), controller=controller)
    else:
        to_dict = getattr(eval_report, "to_dict", None)
        raw_payload = to_dict() if callable(to_dict) else vars(eval_report)
        payload = (
            {str(key): value for key, value in dict(raw_payload).items()}
            if isinstance(raw_payload, dict)
            else {}
        )
        current_result = _normalize_review_result(payload, controller=controller)
    cycle_ids = {
        int(item)
        for item in list(review_basis_window.get("cycle_ids") or [])
        if item is not None
    }
    recent_results = [
        _history_record_to_review_result(item)
        for item in list(session_cycle_history(controller) or [])
        if getattr(item, "cycle_id", None) is not None
        and int(getattr(item, "cycle_id")) in cycle_ids
        and int(getattr(item, "cycle_id")) != int(cycle_id)
    ]
    recent_results.append(current_result)
    recent_results = recent_results[-int(review_basis_window.get("size") or 0) :]
    similar_results, similarity_summary = _build_similar_results(
        controller,
        cycle_id=int(cycle_id),
        current_result=current_result,
    )
    causal_diagnosis = _build_causal_diagnosis(
        current_result=current_result,
        recent_results=recent_results,
        similar_results=similar_results,
    )
    return {
        "recent_results": [item.to_dict() for item in recent_results],
        "review_basis_window": review_basis_window,
        "similar_results": similar_results,
        "similarity_summary": similarity_summary,
        "causal_diagnosis": causal_diagnosis,
    }

logger = logging.getLogger(__name__)

class TrainingReviewService:
    """Owns review-phase report building and decision application."""

    def build_eval_report(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        sim_result: Any,
        regime_result: dict[str, Any],
        selected: list[str],
        cycle_payload: dict[str, Any] | None = None,
        trade_dicts: list[dict[str, Any]],
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        data_mode: str,
        selection_mode: str,
        agent_used: bool,
        llm_used: bool,
        manager_output: Any | None,
        research_feedback: dict[str, Any] | None,
        simulation_envelope: SimulationStageEnvelope | None = None,
        manager_results: list[dict[str, Any]] | None = None,
        portfolio_plan: dict[str, Any] | None = None,
        dominant_manager_id: str = "",
        manager_review_report: dict[str, Any] | None = None,
        allocation_review_report: dict[str, Any] | None = None,
    ) -> EvalReport:
        manager_payload = [dict(item) for item in list(manager_results or [])]
        portfolio_payload = dict(portfolio_plan or {})
        cycle_payload = dict(cycle_payload or {})
        execution_snapshot = (
            dict(simulation_envelope.execution_snapshot or {})
            if simulation_envelope is not None
            else dict(cycle_payload.get("execution_snapshot") or {})
        )
        review_projection = build_review_eval_projection_boundary(
            controller,
            manager_output=manager_output,
            cycle_payload=cycle_payload,
            execution_snapshot=execution_snapshot,
            simulation_envelope=simulation_envelope,
            manager_results=manager_payload,
            portfolio_plan=portfolio_payload,
            dominant_manager_id=dominant_manager_id,
        )
        return EvalReport(
            cycle_id=cycle_id,
            as_of_date=cutoff_date,
            return_pct=sim_result.return_pct,
            total_pnl=sim_result.total_pnl,
            total_trades=sim_result.total_trades,
            win_rate=sim_result.win_rate,
            regime=regime_result.get("regime", "unknown"),
            is_profit=bool(sim_result.return_pct > 0),
            selected_codes=list(selected),
            benchmark_passed=bool(
                _simulation_metric_from_context_or_payload(
                    simulation_envelope,
                    cycle_payload,
                    field_name="benchmark_passed",
                    default=False,
                )
            ),
            benchmark_strict_passed=bool(
                _simulation_metric_from_context_or_payload(
                    simulation_envelope,
                    cycle_payload,
                    field_name="benchmark_strict_passed",
                    default=False,
                )
            ),
            sharpe_ratio=float(
                _simulation_metric_from_context_or_payload(
                    simulation_envelope,
                    cycle_payload,
                    field_name="sharpe_ratio",
                    default=0.0,
                )
                or 0.0
            ),
            max_drawdown=float(
                _simulation_metric_from_context_or_payload(
                    simulation_envelope,
                    cycle_payload,
                    field_name="max_drawdown",
                    default=0.0,
                )
                or 0.0
            ),
            excess_return=float(
                _simulation_metric_from_context_or_payload(
                    simulation_envelope,
                    cycle_payload,
                    field_name="excess_return",
                    default=0.0,
                )
                or 0.0
            ),
            data_mode=data_mode,
            selection_mode=selection_mode,
            agent_used=bool(agent_used),
            llm_used=bool(llm_used),
            metadata=_build_eval_report_metadata(
                review_projection=review_projection,
                trade_dicts=trade_dicts,
                requested_data_mode=requested_data_mode,
                effective_data_mode=effective_data_mode,
                llm_mode=llm_mode,
                degraded=degraded,
                degrade_reason=degrade_reason,
                research_feedback=research_feedback,
                manager_results=manager_payload,
                portfolio_plan=portfolio_payload,
                dominant_manager_id=dominant_manager_id,
                manager_review_report=manager_review_report,
                allocation_review_report=allocation_review_report,
            ),
        )

    def apply_review_decision(
        self,
        controller: Any,
        *,
        cycle_id: int,
        review_decision: dict[str, Any],
        review_event: Any,
    ) -> bool:
        resolved_review_decision = _coerce_review_decision_input(review_decision)
        subject_type = str(resolved_review_decision.get("subject_type") or "").strip()
        manager_subject = subject_type == "manager_portfolio" or bool(
            resolved_review_decision.get("manager_budget_adjustments")
        )
        if manager_subject and bool(getattr(controller, "manager_shadow_mode", False)):
            logger.info("manager shadow mode active; review decision remains advisory only")
            return False

        review_applied = apply_review_decision_boundary_effects(
            controller,
            cycle_id=cycle_id,
            review_decision=resolved_review_decision,
            review_event=review_event,
            manager_subject=manager_subject,
        )
        if resolved_review_decision.get("param_adjustments"):
            logger.info(
                "根据复盘调整参数: %s",
                resolved_review_decision.get("param_adjustments"),
            )
        return bool(review_applied)


def _eval_field(eval_report: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(eval_report, dict):
        return eval_report.get(field_name, default)
    return getattr(eval_report, field_name, default)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    deduped: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


@dataclass(frozen=True)
class TrainingReviewStageResult:
    eval_report: Any
    review_decision: ReviewDecisionInputPayload
    review_applied: bool
    review_event: Any
    manager_review_report: ManagerReviewDigestPayload = field(
        default_factory=lambda: cast(ManagerReviewDigestPayload, {})
    )
    allocation_review_report: AllocationReviewDigestPayload = field(
        default_factory=lambda: cast(AllocationReviewDigestPayload, {})
    )
    review_trace: dict[str, Any] = field(default_factory=dict)
    cycle_payload: dict[str, Any] = field(default_factory=dict)


class TrainingReviewStageService:
    """Owns dual-review orchestration for the training hot path."""

    @staticmethod
    def _build_dual_review_decision(
        *,
        current_params: dict[str, Any],
        eval_report: Any,
        review_input: dict[str, Any],
        manager_review_report: dict[str, Any],
        allocation_review_report: dict[str, Any],
        dominant_manager_id: str,
    ) -> tuple[ReviewDecisionInputPayload, dict[str, Any]]:
        return_pct = float(_eval_field(eval_report, "return_pct", 0.0) or 0.0)
        benchmark_passed = bool(_eval_field(eval_report, "benchmark_passed", False))
        current_position_size = float(dict(current_params or {}).get("position_size", 0.0) or 0.0)

        manager_summary = dict(manager_review_report.get("summary") or {})
        verdict_counts = dict(manager_summary.get("verdict_counts") or {})
        manager_actions = [
            str(item).strip()
            for item in list(manager_summary.get("recommended_actions") or [])
            if str(item).strip()
        ]
        allocation_verdict = str(allocation_review_report.get("verdict") or "continue").strip() or "continue"

        should_tighten = (
            return_pct <= 0
            or not benchmark_passed
            or allocation_verdict in {"rebalance", "hold"}
            or int(verdict_counts.get("hold", 0) or 0) > 0
            or int(verdict_counts.get("downweight", 0) or 0) > 0
        )
        param_adjustments: dict[str, Any] = {}
        if should_tighten:
            suggested_position_size = 0.1
            if current_position_size > 0:
                suggested_position_size = round(max(0.05, current_position_size * 0.83), 2)
            if suggested_position_size != current_position_size:
                param_adjustments["position_size"] = suggested_position_size

        strategy_suggestions = []
        if should_tighten:
            strategy_suggestions.append("tighten_position_size")
        if allocation_verdict == "rebalance":
            strategy_suggestions.append("rebalance_portfolio_constraints")
        if allocation_verdict == "hold":
            strategy_suggestions.append("hold_portfolio_changes_for_manual_review")
        strategy_suggestions.extend(manager_actions)
        if dominant_manager_id:
            strategy_suggestions.append(f"retain {dominant_manager_id} as dominant sleeve")
        strategy_suggestions = _dedupe_preserve_order(strategy_suggestions)

        reasoning_parts = []
        reasoning_parts.append(
            "benchmark gate passed" if benchmark_passed else "benchmark gate failed"
        )
        if return_pct <= 0:
            reasoning_parts.append("cycle return was non-positive")
        if allocation_verdict != "continue":
            reasoning_parts.append(f"allocation review verdict is {allocation_verdict}")
        if int(verdict_counts.get("hold", 0) or 0) > 0:
            reasoning_parts.append("at least one manager is on hold")
        if int(verdict_counts.get("downweight", 0) or 0) > 0:
            reasoning_parts.append("manager downweight action is recommended")
        if not reasoning_parts:
            reasoning_parts.append("dual review completed without escalation")

        similarity_summary = _coerce_similarity_summary_input(
            review_input.get("similarity_summary")
        )
        decision = cast(
            ReviewDecisionInputPayload,
            {
            "subject_type": "manager_portfolio",
            "verdict": "adjust" if should_tighten else "continue",
            "decision_source": "dual_review",
            "reasoning": "；".join(reasoning_parts),
            "strategy_suggestions": strategy_suggestions,
            "regime_summary": {
                "current_regime": str(_eval_field(eval_report, "regime", "unknown") or "unknown"),
                "dominant_regime": str(similarity_summary.get("dominant_regime") or ""),
                "match_count": int(similarity_summary.get("match_count") or 0),
            },
            "param_adjustments": param_adjustments,
            "agent_weight_adjustments": {},
            "manager_budget_adjustments": dict(
                manager_review_report.get("manager_budget_adjustments") or {}
            ),
            "causal_diagnosis": dict(review_input.get("causal_diagnosis") or {}),
            "similarity_summary": similarity_summary,
            "similar_results": [
                _coerce_similar_result_compact(item)
                for item in _list_payload(review_input.get("similar_results"))
            ],
            },
        )
        review_trace = {
            "decision_source": "dual_review",
            "review_basis_window": dict(review_input.get("review_basis_window") or {}),
            "recent_results": [
                _dict_payload(item) for item in _list_payload(review_input.get("recent_results"))
            ],
            "similar_results": [
                _coerce_similar_result_compact(item)
                for item in _list_payload(review_input.get("similar_results"))
            ],
            "similarity_summary": similarity_summary,
            "causal_diagnosis": dict(review_input.get("causal_diagnosis") or {}),
            "manager_review_report": dict(manager_review_report or {}),
            "allocation_review_report": dict(allocation_review_report or {}),
        }
        return decision, review_trace

    def run_review_stage(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        sim_result: Any,
        regime_result: dict[str, Any],
        selected: list[str],
        cycle_payload: dict[str, Any] | None = None,
        trade_dicts: list[dict[str, Any]],
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        data_mode: str,
        selection_mode: str,
        agent_used: bool,
        llm_used: bool,
        manager_output: Any | None,
        research_feedback: dict[str, Any] | None,
        optimization_event_factory: Any,
        simulation_envelope: SimulationStageEnvelope | None = None,
        manager_bundle: Any | None = None,
    ) -> TrainingReviewStageResult:
        dual_review_enabled = bool(getattr(controller, "dual_review_enabled", False))
        if simulation_envelope is None:
            raise ValueError("run_review_stage requires simulation_envelope")
        base_cycle_payload = simulation_envelope.to_cycle_payload(
            base_payload=dict(cycle_payload or {})
        )
        manager_results = (
            [item.to_dict() for item in list(getattr(manager_bundle, "manager_results", []) or [])]
            if manager_bundle is not None
            else []
        )
        portfolio_plan_obj = getattr(manager_bundle, "portfolio_plan", None) if manager_bundle is not None else None
        if portfolio_plan_obj is not None and hasattr(portfolio_plan_obj, "to_dict"):
            portfolio_plan = dict(portfolio_plan_obj.to_dict())
        elif isinstance(portfolio_plan_obj, dict):
            portfolio_plan = dict(portfolio_plan_obj)
        else:
            portfolio_plan = {}
        dominant_manager_id = str(getattr(manager_bundle, "dominant_manager_id", "") or "")

        controller._emit_agent_status(
            "ManagerReview",
            "running",
            "双层复盘中...",
            cycle_id=cycle_id,
            stage="dual_review",
            progress_pct=84,
            step=4,
            total_steps=6,
        )
        controller._emit_module_log(
            "review",
            "进入双层复盘",
            "开始汇总经理表现、组合暴露与治理约束",
            cycle_id=cycle_id,
            kind="phase_start",
        )

        eval_report = controller.training_review_service.build_eval_report(
            controller,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            sim_result=sim_result,
            regime_result=regime_result,
            selected=selected,
            cycle_payload=base_cycle_payload,
            trade_dicts=trade_dicts,
            requested_data_mode=requested_data_mode,
            effective_data_mode=effective_data_mode,
            llm_mode=llm_mode,
            degraded=degraded,
            degrade_reason=degrade_reason,
            data_mode=data_mode,
            selection_mode=selection_mode,
            agent_used=agent_used,
            llm_used=llm_used,
            manager_output=manager_output,
            research_feedback=research_feedback,
            simulation_envelope=simulation_envelope,
            manager_results=manager_results,
            portfolio_plan=portfolio_plan,
            dominant_manager_id=dominant_manager_id,
        )
        append_session_cycle_record(controller, base_cycle_payload)
        review_input = build_review_input(
            controller,
            cycle_id=cycle_id,
            eval_report=eval_report,
        )

        manager_review_report: dict[str, Any] = {}
        allocation_review_report: dict[str, Any] = {}
        if manager_bundle is not None and dual_review_enabled:
            manager_review_report = controller.training_manager_review_stage_service.build_manager_review_report(
                cutoff_date=cutoff_date,
                regime=str(regime_result.get("regime") or "unknown"),
                manager_results=list(getattr(manager_bundle, "manager_results", []) or []),
                dominant_manager_id=dominant_manager_id,
                budget_weights=dict(getattr(manager_bundle.run_context, "budget_weights", {}) or {}),
                review_basis_window=review_input["review_basis_window"],
            )
            allocation_review_report = (
                controller.training_allocation_review_stage_service.build_allocation_review_report(
                    cutoff_date=cutoff_date,
                    regime=str(regime_result.get("regime") or "unknown"),
                    portfolio_plan=getattr(manager_bundle, "portfolio_plan", {}),
                    manager_results=list(getattr(manager_bundle, "manager_results", []) or []),
                )
            )

        review_decision, review_trace = self._build_dual_review_decision(
            current_params=dict(session_current_params(controller) or {}),
            eval_report=eval_report,
            review_input=review_input,
            manager_review_report=manager_review_report,
            allocation_review_report=allocation_review_report,
            dominant_manager_id=dominant_manager_id,
        )
        manager_review_digest = project_manager_review_digest(manager_review_report)
        allocation_review_digest = project_allocation_review_digest(
            allocation_review_report
        )

        if hasattr(eval_report, "metadata") and isinstance(getattr(eval_report, "metadata", None), dict):
            eval_report.metadata["manager_review_report"] = dict(manager_review_digest or {})
            eval_report.metadata["allocation_review_report"] = dict(
                allocation_review_digest or {}
            )

        review_cycle_payload = dict(base_cycle_payload or {})
        review_cycle_payload["manager_review_report"] = dict(manager_review_digest)
        review_cycle_payload["allocation_review_report"] = dict(
            allocation_review_digest
        )

        record_review_boundary_artifacts(
            controller,
            cycle_id=cycle_id,
            manager_review_report=manager_review_report,
            allocation_review_report=allocation_review_report,
        )
        review_boundary = build_review_boundary_event(
            controller,
            cycle_id=cycle_id,
            manager_output=manager_output,
            execution_snapshot=dict(
                (simulation_envelope.execution_snapshot if simulation_envelope is not None else {})
                or review_cycle_payload.get("execution_snapshot")
                or {}
            ),
            dominant_manager_id=dominant_manager_id,
            optimization_event_factory=optimization_event_factory,
            review_decision=review_decision,
            eval_report=eval_report,
            manager_review_report=manager_review_digest,
            allocation_review_report=allocation_review_digest,
        )
        review_event = review_boundary.review_event

        review_applied = controller.training_review_service.apply_review_decision(
            controller,
            cycle_id=cycle_id,
            review_decision=review_decision,
            review_event=review_event,
        )
        review_cycle_payload["review_applied"] = review_applied
        finalize_review_boundary_effects(
            controller,
            cycle_id=cycle_id,
            review_decision=review_decision,
            review_trace=review_trace,
            manager_review_report=manager_review_digest,
            allocation_review_report=allocation_review_digest,
            review_event=review_event,
            review_applied=review_applied,
            review_basis_window=review_boundary.review_basis_window,
            manager_id=review_boundary.manager_id,
            active_runtime_config_ref=review_boundary.active_runtime_config_ref,
        )

        return TrainingReviewStageResult(
            eval_report=eval_report,
            review_decision=review_decision,
            review_applied=review_applied,
            review_event=review_event,
            manager_review_report=manager_review_digest,
            allocation_review_report=allocation_review_digest,
            review_trace=review_trace,
            cycle_payload=review_cycle_payload,
        )




def _manager_result_payload(item: Any) -> dict[str, Any]:
    if hasattr(item, "to_dict"):
        return dict(item.to_dict())
    return dict(item or {})


class ManagerReviewStageService:
    """Builds manager-level review artifacts for the multi-manager runtime."""

    @staticmethod
    def _normalize_budget_adjustments(
        adjustments: dict[str, float],
    ) -> dict[str, float]:
        total = sum(max(0.0, float(value or 0.0)) for value in adjustments.values())
        if total <= 0:
            return {}
        return {
            str(manager_id): round(max(0.0, float(value or 0.0)) / total, 8)
            for manager_id, value in adjustments.items()
        }

    def build_manager_review_report(
        self,
        *,
        cutoff_date: str,
        regime: str,
        manager_results: list[Any],
        dominant_manager_id: str = "",
        budget_weights: dict[str, float] | None = None,
        review_basis_window: dict[str, Any] | None = None,
        review_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        reports: list[dict[str, Any]] = []
        verdict_counts: dict[str, int] = {}
        recommendations: list[str] = []
        base_weights = {
            str(key): max(0.0, float(value or 0.0))
            for key, value in dict(budget_weights or {}).items()
        }
        adjusted_weights = dict(base_weights)

        for item in list(manager_results or []):
            payload = _manager_result_payload(item)
            plan = dict(payload.get("plan") or {})
            attribution = dict(payload.get("attribution") or {})
            manager_id = str(payload.get("manager_id") or plan.get("manager_id") or "").strip()
            selected_codes = [
                str(code).strip()
                for code in list(
                    payload.get("selected_codes")
                    or plan.get("selected_codes")
                    or [position.get("code") for position in list(plan.get("positions") or [])]
                )
                if str(code).strip()
            ]
            positions = [dict(position) for position in list(plan.get("positions") or [])]
            position_count = len(positions or selected_codes)
            plan_regime = str(plan.get("regime") or regime or "unknown").strip() or "unknown"
            status = str(payload.get("status") or "").strip() or "planned"

            verdict = "continue"
            findings: list[str] = []
            strengths: list[str] = []
            weaknesses: list[str] = []
            risk_flags: list[str] = []

            if manager_id == dominant_manager_id and position_count > 0:
                strengths.append("manager is currently the dominant sleeve")
            if position_count > 0:
                strengths.append(f"generated {position_count} position(s)")

            if position_count <= 0 or status == "empty":
                verdict = "hold"
                findings.append("manager emitted an empty plan in the current cycle")
                weaknesses.append("no investable ideas survived plan assembly")
                risk_flags.append("empty_plan")
            elif regime not in {"", "unknown"} and plan_regime not in {"", "unknown"} and plan_regime != regime:
                verdict = "downweight"
                findings.append("manager regime does not align with current portfolio regime")
                weaknesses.append("style-context mismatch may degrade allocation quality")
                risk_flags.append("regime_mismatch")
            else:
                findings.append("manager plan is aligned with the current portfolio cycle")

            if verdict == "hold":
                recommendations.append(f"{manager_id}: keep on observe and reduce budget exposure")
                adjusted_weights[manager_id] = base_weights.get(manager_id, 0.0) * 0.25
            elif verdict == "downweight":
                recommendations.append(f"{manager_id}: downweight until regime alignment improves")
                adjusted_weights[manager_id] = base_weights.get(manager_id, 0.0) * 0.5

            report = ManagerReviewReport(
                manager_id=manager_id,
                as_of_date=cutoff_date,
                verdict=verdict,
                findings=findings,
                strengths=strengths,
                weaknesses=weaknesses,
                risk_flags=risk_flags,
                evidence={
                    "position_count": position_count,
                    "selected_codes": selected_codes,
                    "budget_weight": float(base_weights.get(manager_id, payload.get("budget_weight", 0.0)) or 0.0),
                    "active_exposure": float(attribution.get("active_exposure", 0.0) or 0.0),
                    "plan_confidence": float(plan.get("confidence", 0.0) or 0.0),
                    "plan_regime": plan_regime,
                },
                metadata={
                    "status": status,
                    "dominant_manager": manager_id == dominant_manager_id,
                },
            ).to_dict()
            reports.append(report)
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

        normalized_adjustments = self._normalize_budget_adjustments(adjusted_weights)
        if normalized_adjustments == self._normalize_budget_adjustments(base_weights):
            normalized_adjustments = {}

        return {
            "subject_type": "manager_review",
            "as_of_date": str(cutoff_date),
            "regime": str(regime or "unknown"),
            "dominant_manager_id": str(dominant_manager_id or ""),
            "reports": reports,
            "summary": {
                "manager_count": len(reports),
                "active_manager_ids": [item.get("manager_id", "") for item in reports],
                "verdict_counts": verdict_counts,
                "recommended_actions": recommendations,
                "review_basis_window": dict(review_basis_window or {}),
                "reasoning": str(dict(review_decision or {}).get("reasoning") or ""),
            },
            "manager_budget_adjustments": normalized_adjustments,
        }




def _portfolio_plan_payload(portfolio_plan: Any) -> dict[str, Any]:
    if hasattr(portfolio_plan, "to_dict"):
        return dict(portfolio_plan.to_dict())
    return dict(portfolio_plan or {})


class AllocationReviewStageService:
    """Builds portfolio/allocation review artifacts for the multi-manager runtime."""

    def build_allocation_review_report(
        self,
        *,
        cutoff_date: str,
        regime: str,
        portfolio_plan: Any,
        manager_results: list[Any] | None = None,
        review_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del manager_results
        payload = _portfolio_plan_payload(portfolio_plan)
        positions = [dict(position) for position in list(payload.get("positions") or [])]
        active_manager_ids = [
            str(manager_id).strip()
            for manager_id in list(payload.get("active_manager_ids") or [])
            if str(manager_id).strip()
        ]
        allocation_weights = {
            str(key): float(value)
            for key, value in dict(payload.get("manager_weights") or {}).items()
        }
        cash_reserve = float(payload.get("cash_reserve", 0.0) or 0.0)
        overlap_codes = [
            str(position.get("code") or "").strip()
            for position in positions
            if len(list(position.get("source_managers") or [])) > 1
            and str(position.get("code") or "").strip()
        ]
        max_position_weight = max(
            (float(position.get("target_weight") or 0.0) for position in positions),
            default=0.0,
        )

        findings: list[str] = []
        risk_flags: list[str] = []
        verdict = "continue"
        if not positions:
            verdict = "hold"
            findings.append("portfolio assembly produced no active positions")
            risk_flags.append("empty_portfolio")
        else:
            findings.append(f"portfolio carries {len(positions)} merged position(s)")
            if overlap_codes:
                findings.append("overlapping holdings were merged during portfolio assembly")
            if cash_reserve >= 0.4:
                verdict = "rebalance"
                risk_flags.append("high_cash_reserve")
                findings.append("cash reserve remains elevated after manager assembly")
            if max_position_weight > 0.35:
                verdict = "rebalance"
                risk_flags.append("concentration_risk")
                findings.append("single-position weight exceeds the rebalance threshold")

        report = AllocationReviewReport(
            as_of_date=cutoff_date,
            regime=regime,
            verdict=verdict,
            active_manager_ids=active_manager_ids,
            findings=findings,
            risk_flags=risk_flags,
            allocation_weights=allocation_weights,
            evidence={
                "portfolio_selected_count": len(positions),
                "cash_reserve": cash_reserve,
                "overlap_codes": overlap_codes,
                "max_position_weight": round(max_position_weight, 8),
            },
            metadata={
                "reasoning": str(dict(review_decision or {}).get("reasoning") or ""),
                "assembly_mode": str(dict(payload.get("metadata") or {}).get("assembly_mode") or ""),
            },
        ).to_dict()
        report["subject_type"] = "allocation_review"
        report["summary"] = {
            "portfolio_selected_count": len(positions),
            "overlap_code_count": len(overlap_codes),
            "max_position_weight": round(max_position_weight, 8),
        }
        return report
