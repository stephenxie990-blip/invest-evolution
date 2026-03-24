"""Merged training module: research.py."""

from __future__ import annotations


import hashlib
import logging
from typing import Any, cast

from invest_evolution.application.tagging import (
    JudgeReport,
    PeerComparisonResult,
    TaggingResult,
    ValidationCheck,
    ValidationSummary,
    build_failure_tagging_result,
    build_market_tagging_result,
    build_validation_tagging_result,
    normalize_market_tag,
)
from invest_evolution.config import config
from invest_evolution.application.training.observability import evaluate_research_feedback_gate
from invest_evolution.application.training.policy import (
    governance_from_controller,
    governance_from_item,
    governance_regime,
    normalize_governance_decision,
)
from invest_evolution.application.training.review_contracts import (
    ResearchFeedbackPayload,
    ReviewBasisWindowPayload,
    ValidationInputEnvelope,
)
from invest_evolution.application.training.controller import (
    session_current_params,
    session_last_feedback_optimization_cycle_id,
)
from invest_evolution.investment.research import (
    build_research_hypothesis,
    build_research_snapshot,
    resolve_policy_snapshot,
)
from invest_evolution.investment.runtimes.catalog import COMMON_PARAM_DEFAULTS
from invest_evolution.investment.shared.policy import (
    DEFAULT_PROMOTION_GATE_POLICY,
    evaluate_promotion_discipline,
)

logger = logging.getLogger(__name__)


def project_manager_compatibility(*args: Any, **kwargs: Any) -> Any:
    from invest_evolution.application.training.execution import (
        project_manager_compatibility as _project_manager_compatibility,
    )

    return _project_manager_compatibility(*args, **kwargs)


class TrainingResearchService:
    """Persists training-cycle evidence into the shared research case store."""

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
    def _security_payload(controller: Any, code: str, stock_data: dict[str, Any]) -> dict[str, Any]:
        repository = getattr(controller, "research_market_repository", None)
        if repository is not None:
            try:
                matches = repository.query_securities([code])
            except Exception:
                logger.debug("research security lookup failed for %s", code, exc_info=True)
                matches = []
            if matches:
                return dict(matches[0] or {})

        frame = stock_data.get(code)
        name = ""
        if frame is not None and hasattr(frame, "empty") and not frame.empty and "name" in getattr(frame, "columns", []):
            try:
                name = str(frame.iloc[-1].get("name") or "")
            except Exception:
                name = ""
        return {"code": code, "name": name, "industry": "", "source": "training_cycle"}

    @staticmethod
    def _has_scored_horizon(attribution_payload: dict[str, Any]) -> bool:
        horizon_results = dict(attribution_payload.get("horizon_results") or {})
        return any(
            str(dict(item or {}).get("label") or "") != "timeout"
            for item in horizon_results.values()
        )

    def persist_cycle_research_artifacts(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        manager_output: Any | None,
        stock_data: dict[str, Any],
        selected: list[str],
        regime_result: dict[str, Any] | None = None,
        selection_mode: str = "",
    ) -> dict[str, Any]:
        if manager_output is None or not selected:
            return {
                "saved_case_count": 0,
                "saved_attribution_count": 0,
                "case_ids": [],
                "attribution_ids": [],
                "policy_id": "",
            }

        case_store = getattr(controller, "research_case_store", None)
        scenario_engine = getattr(controller, "research_scenario_engine", None)
        attribution_engine = getattr(controller, "research_attribution_engine", None)
        repository = getattr(controller, "research_market_repository", None)
        if case_store is None or scenario_engine is None or attribution_engine is None:
            return {
                "saved_case_count": 0,
                "saved_attribution_count": 0,
                "case_ids": [],
                "attribution_ids": [],
                "policy_id": "",
                "skipped_reason": "research_runtime_unavailable",
            }

        governance_context = dict(governance_from_controller(controller) or {})
        if not governance_context:
            governance_context = dict(regime_result or {})
        manager_id_hint = str(
            getattr(manager_output, "manager_id", "")
            or governance_context.get("dominant_manager_id")
            or ""
        ).strip()
        manager_config_ref_hint = str(
            getattr(manager_output, "manager_config_ref", "")
            or ""
        ).strip()
        manager_projection = project_manager_compatibility(
            None,
            manager_output=manager_output,
            execution_snapshot={
                "active_runtime_config_ref": manager_config_ref_hint,
                "manager_config_ref": manager_config_ref_hint,
                "dominant_manager_id": manager_id_hint,
            },
            dominant_manager_id_hint=manager_id_hint,
        )

        policy = resolve_policy_snapshot(
            manager_runtime=getattr(controller, "manager_runtime", None),
            manager_id=str(manager_projection.manager_id or ""),
            governance_context=governance_context,
            data_window={
                "as_of_date": str(cutoff_date or ""),
                "lookback_days": int(
                    controller.experiment_min_history_days
                    or getattr(config, "min_history_days", 750)
                    or 750
                ),
                "simulation_days": int(
                    controller.experiment_simulation_days
                    or getattr(config, "simulation_days", 30)
                    or 30
                ),
                "universe_definition": (
                    f"stock_count={len(stock_data)}|selection_mode={selection_mode or 'unknown'}"
                ),
                "stock_universe_size": len(stock_data),
            },
            metadata={
                "source": "training_cycle",
                "cycle_id": int(cycle_id),
                "cutoff_date": str(cutoff_date or ""),
                "selection_mode": str(selection_mode or ""),
                "manager_config_ref": str(manager_projection.manager_config_ref or ""),
            },
        )

        data_lineage = {
            "db_path": str(getattr(repository, "db_path", "") or ""),
            "effective_as_of_date": str(cutoff_date or ""),
            "data_source": "training_cycle",
            "stock_count": len(stock_data),
        }
        case_ids: list[str] = []
        attribution_ids: list[str] = []
        attributed_codes: list[str] = []

        for code in [str(item).strip() for item in list(selected or []) if str(item).strip()]:
            try:
                security = self._security_payload(controller, code, stock_data)
                snapshot = build_research_snapshot(
                    manager_output=manager_output,
                    security=security,
                    query_code=code,
                    stock_data=stock_data,
                    governance_context=governance_context,
                    data_lineage=data_lineage,
                )
                scenario = scenario_engine.estimate(
                    snapshot=snapshot,
                    policy=policy,
                    stance=self.estimate_preliminary_stance(snapshot),
                )
                hypothesis = build_research_hypothesis(
                    snapshot=snapshot,
                    policy=policy,
                    scenario=scenario,
                    strategy_name="training_cycle",
                    strategy_display_name="Training Cycle",
                )
                case_record = case_store.save_case(
                    snapshot=snapshot,
                    policy=policy,
                    hypothesis=hypothesis,
                    metadata={
                        "source": "training_cycle",
                        "cycle_id": int(cycle_id),
                        "cutoff_date": str(cutoff_date or ""),
                        "selection_mode": str(selection_mode or ""),
                        "code": code,
                    },
                )
                case_ids.append(str(case_record.get("research_case_id") or ""))
                attribution = attribution_engine.evaluate_case(case_record)
                attribution_payload = attribution.to_dict()
                if self._has_scored_horizon(attribution_payload):
                    attribution_record = case_store.save_attribution(
                        attribution,
                        metadata={
                            "source": "training_cycle",
                            "cycle_id": int(cycle_id),
                            "cutoff_date": str(cutoff_date or ""),
                            "policy_id": policy.policy_id,
                            "research_case_id": str(case_record.get("research_case_id") or ""),
                            "code": code,
                            "regime": str(governance_context.get("regime") or ""),
                        },
                    )
                    attribution_ids.append(str(attribution_record.get("attribution_id") or ""))
                    attributed_codes.append(code)
            except Exception:
                logger.warning(
                    "failed to persist training research artifact for cycle=%s code=%s",
                    cycle_id,
                    code,
                    exc_info=True,
                )

        calibration_report = {}
        if attribution_ids:
            try:
                calibration_report = case_store.write_calibration_report(policy_id=policy.policy_id)
            except Exception:
                logger.debug("failed to write training calibration report", exc_info=True)

        return {
            "policy_id": str(policy.policy_id or ""),
            "saved_case_count": len(case_ids),
            "saved_attribution_count": len(attribution_ids),
            "case_ids": case_ids,
            "attribution_ids": attribution_ids,
            "attributed_codes": attributed_codes,
            "selected_count": len(selected),
            "requested_regime": str(governance_context.get("regime") or ""),
            "calibration_report_path": str(calibration_report.get("path") or ""),
        }

_HARD_FAIL_REASON_CODES = {
    "ab_failed",
    "peer_dominated",
}
_SOFT_FAIL_REASON_CODES = {
    "candidate_missing",
    "governance_blocked",
    "insufficient_evidence",
    "insufficient_sample",
    "needs_more_optimization",
}
_DEFAULT_PROMOTION_FEEDBACK_POLICY = dict(DEFAULT_PROMOTION_GATE_POLICY.get("research_feedback") or {})


def build_validation_task_id(
    *,
    cycle_id: int,
    candidate_runtime_config_ref: str = "",
    active_runtime_config_ref: str = "",
    manager_id: str = "",
    shadow_mode: bool = False,
) -> str:
    payload = "|".join(
        [
            str(int(cycle_id)),
            str(candidate_runtime_config_ref or "").strip(),
            str(active_runtime_config_ref or "").strip(),
            str(manager_id or "").strip(),
            "shadow" if shadow_mode else "live",
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f"val_{digest[:12]}"


def validate_candidate_precheck(
    run_context: dict[str, Any] | None,
) -> list[ValidationCheck]:
    payload = dict(run_context or {})
    candidate_runtime_config_ref = str(payload.get("candidate_runtime_config_ref") or "").strip()
    active_runtime_config_ref = str(payload.get("active_runtime_config_ref") or "").strip()
    checks = [
        ValidationCheck(
            name="candidate.present",
            passed=bool(candidate_runtime_config_ref),
            actual=1 if candidate_runtime_config_ref else 0,
            threshold=1,
            reason_code="candidate_present" if candidate_runtime_config_ref else "candidate_missing",
            details={"candidate_runtime_config_ref": candidate_runtime_config_ref},
        ),
        ValidationCheck(
            name="active_runtime_config.present",
            passed=bool(active_runtime_config_ref),
            actual=1 if active_runtime_config_ref else 0,
            threshold=1,
            reason_code="active_runtime_config_present" if active_runtime_config_ref else "insufficient_evidence",
            details={"active_runtime_config_ref": active_runtime_config_ref},
        ),
    ]
    return checks


def validate_candidate_regimes(
    *,
    market_tag: str,
    regime_summary: dict[str, Any] | None = None,
    dominant_regime_share_threshold: float = 0.75,
    min_samples: int = 2,
    shadow_mode: bool = False,
    review_basis_window: dict[str, Any] | None = None,
) -> list[ValidationCheck]:
    summary = dict(regime_summary or {})
    if market_tag == "unknown" and not summary:
        return [
            ValidationCheck(
                name="regime_summary.available",
                passed=False,
                actual=0,
                threshold=1,
                reason_code="insufficient_evidence",
                details={"market_tag": market_tag},
            )
        ]
    sample_count = int(summary.get("sample_count") or 0)
    dominant_regime_share = float(summary.get("dominant_regime_share") or 0.0)
    basis_window = dict(review_basis_window or {})
    basis_cycle_ids = list(basis_window.get("cycle_ids") or [])
    basis_window_size = int(basis_window.get("size") or 0)
    incomplete_shadow_rolling_window = (
        shadow_mode
        and str(basis_window.get("mode") or "").strip() == "rolling"
        and basis_window_size > 0
        and len(basis_cycle_ids) < basis_window_size
    )
    checks = [
        ValidationCheck(
            name="regime_summary.sample_count",
            passed=sample_count >= min_samples,
            actual=sample_count,
            threshold=min_samples,
            reason_code="regime_sample_sufficient"
            if sample_count >= min_samples
            else "insufficient_sample",
            details={"market_tag": market_tag},
        ),
    ]
    if sample_count >= min_samples:
        dominant_regime_passed = dominant_regime_share <= dominant_regime_share_threshold
        if not dominant_regime_passed and incomplete_shadow_rolling_window:
            dominant_regime_passed = True
        checks.append(
            ValidationCheck(
                name="regime_summary.dominant_regime_share",
                passed=dominant_regime_passed,
                actual=dominant_regime_share,
                threshold=dominant_regime_share_threshold,
                reason_code="regime_diversified"
                if dominant_regime_passed
                else "needs_more_optimization",
                details={
                    "market_tag": market_tag,
                    "shadow_rolling_window_advisory": incomplete_shadow_rolling_window,
                    "review_basis_window": basis_window,
                },
            )
        )
    return checks


def validate_candidate_ab(ab_comparison: dict[str, Any] | None) -> list[ValidationCheck]:
    payload = dict(ab_comparison or {})
    comparison = dict(payload.get("comparison") or payload)
    if not comparison:
        return [
            ValidationCheck(
                name="candidate_ab.available",
                passed=False,
                actual=0,
                threshold=1,
                reason_code="insufficient_evidence",
            )
        ]
    candidate_present = bool(comparison.get("candidate_present", True))
    comparable = bool(comparison.get("comparable", False))
    candidate_outperformed = bool(comparison.get("candidate_outperformed", False))
    checks = [
        ValidationCheck(
            name="candidate_ab.candidate_present",
            passed=candidate_present,
            actual=1 if candidate_present else 0,
            threshold=1,
            reason_code="candidate_present" if candidate_present else "candidate_missing",
        ),
        ValidationCheck(
            name="candidate_ab.comparable",
            passed=comparable,
            actual=1 if comparable else 0,
            threshold=1,
            reason_code="candidate_ab_comparable" if comparable else "insufficient_evidence",
            details={"winner": comparison.get("winner")},
        ),
    ]
    if candidate_present and comparable:
        checks.append(
            ValidationCheck(
                name="candidate_ab.outperform_active",
                passed=candidate_outperformed,
                actual=1 if candidate_outperformed else 0,
                threshold=1,
                reason_code="candidate_ab_passed" if candidate_outperformed else "ab_failed",
                details={
                    "winner": comparison.get("winner"),
                    "return_lift_pct": comparison.get("return_lift_pct"),
                    "strategy_score_lift": comparison.get("strategy_score_lift"),
                },
            )
        )
    return checks


def validate_candidate_feedback(
    research_feedback: dict[str, Any] | None,
    *,
    policy: dict[str, Any] | None = None,
    shadow_mode: bool = False,
) -> list[ValidationCheck]:
    policy_payload = dict(policy or {})
    feedback_payload = dict(research_feedback or {})
    evaluation = evaluate_research_feedback_gate(
        feedback_payload,
        policy=policy_payload,
        defaults=_DEFAULT_PROMOTION_FEEDBACK_POLICY,
    )
    evidence_source = "research_feedback"
    if not evaluation.get("active", False):
        overall_feedback = dict(feedback_payload.get("overall_feedback") or {})
        if int(overall_feedback.get("sample_count") or 0) > int(feedback_payload.get("sample_count") or 0):
            fallback_evaluation = evaluate_research_feedback_gate(
                overall_feedback,
                policy=policy_payload,
                defaults=_DEFAULT_PROMOTION_FEEDBACK_POLICY,
            )
            if fallback_evaluation.get("active", False):
                evaluation = fallback_evaluation
                evidence_source = "overall_feedback_fallback"
    sample_count = int(evaluation.get("sample_count") or 0)
    checks: list[ValidationCheck] = []
    if not evaluation.get("active", False):
        threshold = int(
            policy_payload.get("min_sample_count")
            or _DEFAULT_PROMOTION_FEEDBACK_POLICY.get("min_sample_count")
            or 0
        )
        checks.append(
            ValidationCheck(
                name="research_feedback.active",
                passed=False,
                actual=sample_count,
                threshold=threshold,
                reason_code="insufficient_sample",
                details={
                    "reason": evaluation.get("reason", "insufficient_samples"),
                    "evidence_source": evidence_source,
                },
            )
        )
        return checks
    failed_checks = list(evaluation.get("failed_checks") or [])
    if failed_checks:
        if shadow_mode:
            checks.append(
                ValidationCheck(
                    name="research_feedback.passed",
                    passed=True,
                    actual=sample_count,
                    threshold=sample_count,
                    reason_code="research_feedback_advisory",
                    details={
                        "advisory": True,
                        "failed_check_names": [
                            str(item.get("name") or "") for item in failed_checks if str(item.get("name") or "")
                        ],
                        "bias": evaluation.get("bias"),
                        "evidence_source": evidence_source,
                    },
                )
            )
            return checks
        checks.append(
            ValidationCheck(
                name="research_feedback.passed",
                passed=False,
                actual=len(failed_checks),
                threshold=0,
                reason_code="needs_more_optimization",
                details={
                    "failed_check_names": [
                        str(item.get("name") or "") for item in failed_checks if str(item.get("name") or "")
                    ],
                    "bias": evaluation.get("bias"),
                    "evidence_source": evidence_source,
                },
            )
        )
        return checks
    checks.append(
        ValidationCheck(
            name="research_feedback.passed",
            passed=True,
            actual=sample_count,
            threshold=sample_count,
            reason_code="research_feedback_passed",
            details={"evidence_source": evidence_source},
        )
    )
    return checks


def validate_candidate_governance(
    *,
    run_context: dict[str, Any] | None,
    cycle_history: list[Any] | None = None,
    policy: dict[str, Any] | None = None,
    optimization_events: list[dict[str, Any]] | None = None,
) -> list[ValidationCheck]:
    discipline = evaluate_promotion_discipline(
        run_context=dict(run_context or {}),
        cycle_history=list(cycle_history or []),
        policy=policy,
        optimization_events=optimization_events,
    )
    payload = dict(run_context or {})
    status = str(discipline.get("status") or "active_aligned")
    violations = {
        str(item).strip()
        for item in list(discipline.get("violations") or [])
        if str(item).strip()
    }
    shadow_feedback_advisory = bool(payload.get("shadow_mode")) and status == "candidate_pruned" and violations == {
        "blocked_research_feedback"
    }
    shadow_candidate_prune_advisory = bool(payload.get("shadow_mode")) and status == "candidate_pruned" and bool(
        violations
    ) and violations.issubset({"blocked_research_feedback", "failed_candidate_ab"})
    blocked_statuses = {"candidate_pruned", "candidate_expired", "override_expired"}
    blocked = status in blocked_statuses and not shadow_candidate_prune_advisory
    return [
        ValidationCheck(
            name="promotion_discipline.status",
            passed=not blocked,
            actual=status,
            threshold=sorted(blocked_statuses),
            reason_code="governance_passed" if not blocked else "governance_blocked",
            details={
                "discipline": discipline,
                "shadow_feedback_advisory": shadow_feedback_advisory,
                "shadow_candidate_prune_advisory": shadow_candidate_prune_advisory,
            },
        )
    ]


def build_validation_summary(
    *,
    validation_task_id: str,
    checks: list[ValidationCheck],
    raw_evidence: dict[str, Any] | None = None,
    shadow_mode: bool = False,
    confidence_score: float = 1.0,
) -> ValidationSummary:
    failed_checks = [item for item in checks if not item.passed]
    reason_codes: list[str] = []
    for item in failed_checks:
        if item.reason_code not in reason_codes:
            reason_codes.append(item.reason_code)
    if not failed_checks:
        status = "passed"
        validation_tags = ["validation_passed"]
    elif any(code in _HARD_FAIL_REASON_CODES for code in reason_codes):
        status = "failed"
        validation_tags = [
            code
            for code in reason_codes
            if code in _HARD_FAIL_REASON_CODES or code in _SOFT_FAIL_REASON_CODES
        ]
    else:
        status = "hold"
        validation_tags = [
            code
            for code in reason_codes
            if code in _SOFT_FAIL_REASON_CODES or code == "insufficient_evidence"
        ] or ["insufficient_evidence"]
    review_required = bool(
        status != "passed" or confidence_score < 0.6 or "insufficient_evidence" in reason_codes
    )
    if status == "passed":
        summary_text = "candidate validation passed"
    elif status == "failed":
        summary_text = f"candidate validation failed: {', '.join(reason_codes)}"
    else:
        summary_text = f"candidate validation held: {', '.join(reason_codes or ['insufficient_evidence'])}"
    return ValidationSummary(
        validation_task_id=validation_task_id,
        status=status,
        shadow_mode=shadow_mode,
        review_required=review_required,
        confidence_score=confidence_score,
        validation_tags=validation_tags,
        reason_codes=reason_codes,
        checks=checks,
        failed_checks=failed_checks,
        raw_evidence=dict(raw_evidence or {}),
        summary=summary_text,
    )

def _resolve_shadow_mode(
    run_context: dict[str, Any] | None = None,
) -> bool:
    payload = dict(run_context or {})
    if "shadow_mode" in payload:
        return bool(payload.get("shadow_mode"))
    protocol = dict(payload.get("experiment_protocol") or {})
    return bool(protocol.get("shadow_mode", False))


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return dict(cast(dict[str, Any], value or {}))


def _resolve_feedback_policy(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(policy or {})
    nested = dict(payload.get("research_feedback") or {})
    return nested or payload


def _resolve_research_feedback(
    validation_input: ValidationInputEnvelope,
) -> ResearchFeedbackPayload:
    for source in (
        dict(validation_input.cycle_result or {}),
        dict(validation_input.review_result or {}),
        dict(validation_input.run_context or {}),
    ):
        feedback = _payload_dict(source.get("research_feedback"))
        if feedback:
            return cast(ResearchFeedbackPayload, feedback)
    return cast(ResearchFeedbackPayload, {})


def _candidate_precheck_checks(run_payload: dict[str, Any], *, candidate_present: bool) -> list[Any]:
    checks = validate_candidate_precheck(run_payload)
    if candidate_present:
        return checks
    return [check for check in checks if getattr(check, "name", "") == "active_runtime_config.present"]


def _resolve_regime_summary(
    validation_input: ValidationInputEnvelope,
    research_feedback: dict[str, Any],
) -> dict[str, Any]:
    review_payload = dict(validation_input.review_result or {})
    cycle_payload = dict(validation_input.cycle_result or {})
    explicit_summary = _payload_dict(
        review_payload.get("regime_summary") or cycle_payload.get("regime_summary")
    )
    feedback_payload = dict(research_feedback or {})
    scope = dict(feedback_payload.get("scope") or {})
    regime_sample_count = _int_or_zero(scope.get("regime_sample_count"))
    overall_sample_count = _int_or_zero(scope.get("overall_sample_count"))
    effective_scope = str(scope.get("effective_scope") or "").strip()

    sample_count = 0
    if regime_sample_count > 0:
        sample_count = regime_sample_count
    elif effective_scope == "overall" and overall_sample_count > 0:
        sample_count = overall_sample_count
    else:
        sample_count = _int_or_zero(feedback_payload.get("sample_count"))

    backfilled_summary: dict[str, Any] = {}
    if sample_count > 0:
        backfilled_summary["sample_count"] = sample_count
    if regime_sample_count > 0 and overall_sample_count > 0:
        backfilled_summary["dominant_regime_share"] = min(
            1.0,
            max(0.0, regime_sample_count / overall_sample_count),
        )

    if explicit_summary:
        summary = dict(explicit_summary)
        if _int_or_zero(summary.get("sample_count")) <= 0 and "sample_count" in backfilled_summary:
            summary["sample_count"] = backfilled_summary["sample_count"]
        if "dominant_regime_share" not in summary and "dominant_regime_share" in backfilled_summary:
            summary["dominant_regime_share"] = backfilled_summary["dominant_regime_share"]
        return summary

    return backfilled_summary


def _resolve_review_basis_window(
    validation_input: ValidationInputEnvelope,
) -> ReviewBasisWindowPayload:
    for source in (
        dict(validation_input.review_result or {}),
        dict(validation_input.cycle_result or {}),
        dict(validation_input.run_context or {}),
    ):
        basis_window = _payload_dict(source.get("review_basis_window"))
        if basis_window:
            return cast(ReviewBasisWindowPayload, basis_window)
    cycle_id = int(getattr(validation_input, "cycle_id", 0) or 0)
    return cast(
        ReviewBasisWindowPayload,
        {
            "mode": "single_cycle",
            "size": 1,
            "cycle_ids": [cycle_id] if cycle_id > 0 else [],
            "current_cycle_id": cycle_id,
        },
    )


def _coerce_validation_input(
    *,
    cycle_id: int,
    manager_id: str = "",
    run_context: dict[str, Any] | None,
    review_result: dict[str, Any] | None,
    cycle_result: dict[str, Any] | None,
    validation_input: ValidationInputEnvelope | None,
) -> ValidationInputEnvelope:
    if validation_input is not None:
        return validation_input
    return ValidationInputEnvelope(
        cycle_id=int(cycle_id),
        manager_id=str(manager_id or ""),
        run_context=dict(run_context or {}),
        review_result=dict(review_result or {}),
        cycle_result=dict(cycle_result or {}),
    )


def run_validation_orchestrator(
    *,
    cycle_id: int,
    manager_id: str = "",
    run_context: dict[str, Any] | None,
    review_result: dict[str, Any] | None = None,
    cycle_result: dict[str, Any] | None = None,
    validation_input: ValidationInputEnvelope | None = None,
    cycle_history: list[Any] | None = None,
    optimization_events: list[dict[str, Any]] | None = None,
    feedback_policy: dict[str, Any] | None = None,
    governance_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    input_envelope = _coerce_validation_input(
        cycle_id=cycle_id,
        manager_id=manager_id,
        run_context=run_context,
        review_result=review_result,
        cycle_result=cycle_result,
        validation_input=validation_input,
    )
    run_payload = dict(input_envelope.run_context or {})
    review_payload = dict(input_envelope.review_result or {})
    cycle_payload = dict(input_envelope.cycle_result or {})
    governance_decision = normalize_governance_decision(
        dict(cycle_payload.get("governance_decision") or run_payload.get("governance_decision") or {})
    )
    run_payload["governance_decision"] = governance_decision
    cycle_payload["governance_decision"] = governance_decision
    resolved_manager_id = str(
        manager_id
        or input_envelope.manager_id
        or governance_decision.get("dominant_manager_id")
        or run_payload.get("dominant_manager_id")
        or ""
    ).strip()
    shadow_mode = _resolve_shadow_mode(run_payload)
    validation_task_id = build_validation_task_id(
        cycle_id=cycle_id,
        candidate_runtime_config_ref=str(run_payload.get("candidate_runtime_config_ref") or ""),
        active_runtime_config_ref=str(run_payload.get("active_runtime_config_ref") or ""),
        manager_id=resolved_manager_id,
        shadow_mode=shadow_mode,
    )
    market_tagging = build_market_tagging_result(
        review_result=review_payload,
        run_context={
            **run_payload,
            "governance_decision": governance_decision,
        },
    )
    failure_tagging = build_failure_tagging_result(
        failure_signature=_payload_dict(review_payload.get("failure_signature")),
        review_result=review_payload,
        run_context=run_payload,
    )
    research_feedback = _resolve_research_feedback(input_envelope)
    regime_summary = _resolve_regime_summary(
        input_envelope,
        cast(dict[str, Any], research_feedback),
    )
    review_basis_window = _resolve_review_basis_window(input_envelope)
    candidate_present = bool(str(run_payload.get("candidate_runtime_config_ref") or "").strip())
    checks = [
        *_candidate_precheck_checks(run_payload, candidate_present=candidate_present),
        *validate_candidate_regimes(
            market_tag=market_tagging.primary_tag,
            regime_summary=regime_summary,
            shadow_mode=shadow_mode,
            review_basis_window=cast(dict[str, Any], review_basis_window),
        ),
        *(
            validate_candidate_ab(
                dict(cycle_payload.get("ab_comparison") or run_payload.get("ab_comparison") or {})
            )
            if candidate_present
            else []
        ),
        *validate_candidate_feedback(
            cast(dict[str, Any], research_feedback),
            policy=_resolve_feedback_policy(feedback_policy),
            shadow_mode=shadow_mode,
        ),
        *validate_candidate_governance(
            run_context=run_payload,
            cycle_history=cycle_history,
            policy=governance_policy,
            optimization_events=optimization_events,
        ),
    ]
    confidence_score = (
        float(market_tagging.confidence_score) + float(failure_tagging.confidence_score)
    ) / 2.0
    summary = build_validation_summary(
        validation_task_id=validation_task_id,
        checks=checks,
        raw_evidence={
            "run_context": run_payload,
            "review_result": review_payload,
            "cycle_result": cycle_payload,
        },
        shadow_mode=shadow_mode,
        confidence_score=confidence_score,
    )
    validation_tagging = build_validation_tagging_result(summary)
    checkpoint = {
        "validation_task_id": validation_task_id,
        "stages_completed": [
            "precheck",
            "regime_validation",
            "candidate_ab_validation",
            "research_feedback_validation",
            "governance_snapshot",
            "summary",
        ],
    }
    return {
        "contract_version": summary.contract_version,
        "validation_task_id": validation_task_id,
        "shadow_mode": shadow_mode,
        "market_tagging": market_tagging.to_dict(),
        "failure_tagging": failure_tagging.to_dict(),
        "validation_tagging": validation_tagging.to_dict(),
        "summary": summary.to_dict(),
        "checks": [item.to_dict() for item in checks],
        "failed_checks": [item.to_dict() for item in summary.failed_checks],
        "checkpoint": checkpoint,
    }

def _entry_field(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _score_tuple(payload: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(payload.get("score") or 0.0),
        float(payload.get("avg_return_pct") or 0.0),
        float(payload.get("benchmark_pass_rate") or 0.0),
    )


def select_peer_candidates(
    peer_entries: list[dict[str, Any]] | None,
    *,
    market_tag: str,
    max_peers: int = 3,
) -> list[dict[str, Any]]:
    target_market_tag = normalize_market_tag(market_tag)
    candidates: list[dict[str, Any]] = []
    for item in list(peer_entries or []):
        payload = dict(item or {})
        peer_market_tag = normalize_market_tag(
            payload.get("market_tag") or payload.get("regime") or payload.get("governance_regime") or "unknown"
        )
        if target_market_tag != "unknown" and peer_market_tag != target_market_tag:
            continue
        if not bool(payload.get("active", True)):
            continue
        if int(payload.get("sample_count") or payload.get("window") or 0) <= 0:
            continue
        payload["market_tag"] = peer_market_tag
        candidates.append(payload)
    candidates.sort(key=_score_tuple, reverse=True)
    return candidates[: max(1, int(max_peers))]


def build_history_peer_entries(cycle_history: list[Any] | None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in list(cycle_history or []):
        lineage_record = dict(_entry_field(item, "lineage_record", {}) or {})
        deployment_stage = str(lineage_record.get("deployment_stage") or "active")
        if deployment_stage != "active":
            continue
        governance_decision = governance_from_item(item)
        strategy_scores = dict(_entry_field(item, "strategy_scores", {}) or {})
        entries.append(
            {
                "manager_id": str(
                    _entry_field(item, "dominant_manager_id", "")
                    or governance_decision.get("dominant_manager_id")
                    or ""
                ),
                "market_tag": normalize_market_tag(
                    governance_regime(
                        governance_decision,
                        default=dict(_entry_field(item, "audit_tags", {}) or {}).get("governance_regime") or "unknown",
                    )
                ),
                "score": float(strategy_scores.get("overall_score") or 0.0),
                "avg_return_pct": float(_entry_field(item, "return_pct", 0.0) or 0.0),
                "benchmark_pass_rate": 1.0 if bool(_entry_field(item, "benchmark_passed", False)) else 0.0,
                "sample_count": 1,
                "active": True,
                "cycle_id": _entry_field(item, "cycle_id"),
            }
        )
    return entries


def compare_candidate_to_peers(
    candidate_metrics: dict[str, Any] | None,
    peer_entries: list[dict[str, Any]] | None,
    *,
    market_tag: str,
    max_peers: int = 3,
) -> PeerComparisonResult:
    candidate = dict(candidate_metrics or {})
    target_market_tag = normalize_market_tag(market_tag)
    peers = select_peer_candidates(peer_entries, market_tag=target_market_tag, max_peers=max_peers)
    if not peers:
        return PeerComparisonResult(
            compared_market_tag=target_market_tag,
            comparable=False,
            compared_count=0,
            reason_codes=["insufficient_evidence"],
            summary="no comparable peers found",
        )
    candidate_tuple = _score_tuple(candidate)
    dominant_peer = ""
    peer_dominated = False
    if _score_tuple(peers[0]) > candidate_tuple:
        dominant_peer = str(peers[0].get("manager_id") or peers[0].get("name") or "")
        peer_dominated = True
    return PeerComparisonResult(
        compared_market_tag=target_market_tag,
        comparable=True,
        compared_count=len(peers),
        ranked_peers=peers,
        dominant_peer=dominant_peer,
        peer_dominated=peer_dominated,
        candidate_outperformed_peers=not peer_dominated,
        reason_codes=["peer_dominated"] if peer_dominated else ["candidate_outperformed_peers"],
        summary=(
            f"dominant peer detected: {dominant_peer}"
            if peer_dominated
            else "candidate outperformed selected peers"
        ),
    )

def build_judge_report(
    validation_summary: ValidationSummary | dict[str, Any],
    *,
    peer_comparison: PeerComparisonResult | dict[str, Any] | None = None,
    failure_tagging: TaggingResult | dict[str, Any] | None = None,
    shadow_mode: bool | None = None,
) -> JudgeReport:
    summary = (
        validation_summary
        if isinstance(validation_summary, ValidationSummary)
        else ValidationSummary.model_validate(dict(validation_summary or {}))
    )
    peer = (
        peer_comparison
        if isinstance(peer_comparison, PeerComparisonResult)
        else PeerComparisonResult.model_validate(dict(peer_comparison or {}))
    )
    failure = (
        failure_tagging
        if isinstance(failure_tagging, TaggingResult)
        else TaggingResult.model_validate(
            dict(
                failure_tagging
                or {
                    "tag_family": "failure",
                    "primary_tag": "no_failure_signal",
                    "normalized_tags": ["no_failure_signal"],
                    "confidence_score": 1.0,
                }
            )
        )
    )
    effective_shadow_mode = bool(summary.shadow_mode if shadow_mode is None else shadow_mode)
    reason_codes = list(summary.reason_codes)
    candidate_missing = "candidate_missing" in reason_codes
    hard_reject = any(code in {"ab_failed", "governance_blocked"} for code in reason_codes)
    if peer.peer_dominated and "peer_dominated" not in reason_codes and not candidate_missing:
        reason_codes.append("peer_dominated")
    if summary.confidence_score < 0.6 or "insufficient_evidence" in reason_codes or candidate_missing:
        decision = "hold"
        next_actions = ["request_review", "collect_more_evidence"]
    elif hard_reject:
        decision = "reject"
        next_actions = ["reject_candidate", "record_rejection_reason"]
    elif peer.peer_dominated:
        decision = "switch_to_peer"
        next_actions = ["switch_to_dominant_peer", "preserve_candidate_for_audit"]
    elif summary.status == "passed":
        decision = "promote"
        next_actions = ["promote_candidate", "refresh_governance_snapshots"]
    elif any(
        code == "needs_more_optimization" for code in reason_codes
    ) or any(tag in {"loss", "benchmark_miss"} for tag in failure.normalized_tags):
        decision = "continue_optimize"
        next_actions = ["trigger_next_optimization_round", "preserve_validation_context"]
    else:
        decision = "hold"
        next_actions = ["request_review"]
    review_required = bool(summary.review_required or summary.confidence_score < 0.6)
    return JudgeReport(
        decision=decision,
        shadow_mode=effective_shadow_mode,
        actionable=not effective_shadow_mode,
        review_required=review_required,
        validation_status=summary.status,
        reason_codes=reason_codes,
        next_actions=next_actions,
        summary=f"judge decision={decision}; validation_status={summary.status}",
    )
class TrainingFeedbackService:
    @staticmethod
    def research_feedback_summary(
        feedback: dict[str, Any] | None = None,
        *,
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(feedback or {})
        recommendation = dict(payload.get("recommendation") or {})
        t20 = dict(payload.get("horizons") or {}).get("T+20") or {}
        scope = dict(payload.get("scope") or {})
        return {
            "available": bool(payload),
            "source": dict(source or {}),
            "sample_count": int(payload.get("sample_count") or 0),
            "bias": str(recommendation.get("bias") or "unknown"),
            "summary": str(recommendation.get("summary") or ""),
            "brier_like_direction_score": payload.get("brier_like_direction_score"),
            "t20_hit_rate": t20.get("hit_rate"),
            "t20_invalidation_rate": t20.get("invalidation_rate"),
            "available_horizons": sorted((payload.get("horizons") or {}).keys()),
            "effective_scope": str(scope.get("effective_scope") or "overall"),
            "requested_regime": str(scope.get("requested_regime") or ""),
        }

    @staticmethod
    def research_feedback_brief(feedback: dict[str, Any] | None = None) -> dict[str, Any]:
        summary = TrainingFeedbackService.research_feedback_summary(feedback)
        return {
            "sample_count": int(summary.get("sample_count") or 0),
            "bias": str(summary.get("bias") or "unknown"),
            "brier_like_direction_score": summary.get("brier_like_direction_score"),
            "t20_hit_rate": summary.get("t20_hit_rate"),
        }

    @staticmethod
    def feedback_brief(plan: dict[str, Any] | None = None, *, triggered: bool = False) -> dict[str, Any]:
        payload = dict(plan or {})
        if not payload:
            return {}
        return {
            "triggered": bool(triggered),
            "trigger": str(payload.get("trigger") or "research_feedback"),
            "bias": str(payload.get("bias") or ""),
            "failed_horizons": list(payload.get("failed_horizons") or []),
            "failed_check_names": list(payload.get("failed_check_names") or []),
            "summary": str(payload.get("summary") or ""),
            "sample_count": int(payload.get("sample_count") or 0),
            "cooldown_cycles": int(payload.get("cooldown_cycles") or 0),
        }

    def load_research_feedback(
        self,
        controller: Any,
        *,
        cutoff_date: str,
        manager_id: str,
        manager_config_ref: str,
        regime: str = "",
    ) -> dict[str, Any]:
        return load_research_feedback_boundary(
            controller,
            cutoff_date=cutoff_date,
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
            regime=regime,
        )

    def build_feedback_optimization_plan(
        self,
        controller: Any,
        feedback: dict[str, Any] | None,
        *,
        cycle_id: int,
    ) -> dict[str, Any]:
        payload = dict(feedback or {})
        evaluation = evaluate_research_feedback_gate(
            payload,
            policy=controller.research_feedback_optimization_policy,
            defaults={
                "min_sample_count": 5,
                "blocked_biases": ["tighten_risk", "recalibrate_probability"],
                "max_brier_like_direction_score": 0.28,
                "horizons": {
                    "default": {
                        "min_hit_rate": 0.45,
                        "max_invalidation_rate": 0.35,
                        "min_interval_hit_rate": 0.40,
                    }
                },
            },
        )
        if not evaluation.get("active") or evaluation.get("passed", True):
            return {}

        cooldown_cycles = int(controller.research_feedback_optimization_policy.get("cooldown_cycles", 3) or 3)
        last_feedback_cycle_id = session_last_feedback_optimization_cycle_id(controller)
        if last_feedback_cycle_id and cycle_id - last_feedback_cycle_id < cooldown_cycles:
            return {}

        bias = str(evaluation.get("bias") or dict(payload.get("recommendation") or {}).get("bias") or "maintain")
        failed_checks = list(evaluation.get("failed_checks") or [])
        failed_horizons = sorted({str(item.get("horizon") or "").strip() for item in failed_checks if str(item.get("horizon") or "").strip()})
        fail_count = max(1, len(failed_checks))
        benchmark_window = min(10, max(3, int(getattr(controller, "freeze_total_cycles", 10) or 10)))
        rolling = controller.freeze_gate_service.rolling_self_assessment(
            controller,
            window=benchmark_window,
        )
        benchmark_required = float(
            controller.research_feedback_optimization_policy.get(
                "benchmark_pass_rate_gte",
                controller.freeze_gate_policy.get("benchmark_pass_rate_gte", 0.60),
            )
            or controller.freeze_gate_policy.get("benchmark_pass_rate_gte", 0.60)
            or 0.60
        )
        benchmark_pass_rate = float(rolling.get("benchmark_pass_rate", benchmark_required) or benchmark_required)
        benchmark_gap = max(0.0, benchmark_required - benchmark_pass_rate)
        severity = min(
            3.4,
            1.0
            + 0.30 * max(0, fail_count - 1)
            + (0.40 if bias == "tighten_risk" else 0.20)
            + min(0.90, benchmark_gap * 2.5),
        )

        current_params = session_current_params(controller)
        current_position = float(current_params.get("position_size", COMMON_PARAM_DEFAULTS["position_size"]) or COMMON_PARAM_DEFAULTS["position_size"])
        current_stop = float(current_params.get("stop_loss_pct", COMMON_PARAM_DEFAULTS["stop_loss_pct"]) or COMMON_PARAM_DEFAULTS["stop_loss_pct"])
        current_take_profit = float(current_params.get("take_profit_pct", COMMON_PARAM_DEFAULTS["take_profit_pct"]) or COMMON_PARAM_DEFAULTS["take_profit_pct"])
        current_cash = float(current_params.get("cash_reserve", COMMON_PARAM_DEFAULTS["cash_reserve"]) or COMMON_PARAM_DEFAULTS["cash_reserve"])
        current_trailing = float(current_params.get("trailing_pct", COMMON_PARAM_DEFAULTS["trailing_pct"]) or COMMON_PARAM_DEFAULTS["trailing_pct"])
        current_hold_days = int(
            current_params.get("max_hold_days", COMMON_PARAM_DEFAULTS["max_hold_days"])
            or COMMON_PARAM_DEFAULTS["max_hold_days"]
        )
        current_signal_threshold = current_params.get("signal_threshold")

        raw_adjustments: dict[str, Any] = {
            "position_size": current_position * max(0.45, 1.0 - 0.16 * severity),
            "cash_reserve": current_cash + 0.05 + 0.03 * min(severity, 3.0),
            "max_hold_days": current_hold_days - max(4, int(round(3 * severity + benchmark_gap * 12))),
        }
        suggestions = [
            f"ask校准在 {', '.join(failed_horizons) if failed_horizons else '多周期'} 上显示风险偏高，先自动收紧风险暴露",
        ]
        if benchmark_gap > 0:
            suggestions.append(
                f"近窗 benchmark 通过率 {benchmark_pass_rate:.0%} 低于目标 {benchmark_required:.0%}，提高信号门槛并缩短持有周期"
            )
        if bias == "tighten_risk":
            raw_adjustments["stop_loss_pct"] = current_stop * max(0.55, 1.0 - 0.12 * severity)
            raw_adjustments["trailing_pct"] = current_trailing * max(0.60, 1.0 - 0.10 * severity)
            raw_adjustments["take_profit_pct"] = current_take_profit * max(0.82, 1.0 - 0.06 * severity)
            suggestions.append("优先收紧止损、跟踪止盈与仓位")
        elif bias == "recalibrate_probability":
            raw_adjustments["take_profit_pct"] = current_take_profit * max(0.85, 1.0 - 0.05 * severity)
            suggestions.append("优先下调仓位并收紧概率兑现预期")
        if current_signal_threshold is not None:
            raw_adjustments["signal_threshold"] = float(current_signal_threshold) + 0.015 + 0.02 * severity + 0.10 * benchmark_gap

        param_adjustments = sanitize_runtime_param_adjustments_boundary(
            controller,
            raw_adjustments,
        )
        if not param_adjustments:
            return {}

        recommendation = dict(payload.get("recommendation") or {})
        summary = str(recommendation.get("summary") or "research feedback optimization")
        return {
            "trigger": "research_feedback",
            "bias": bias,
            "summary": summary,
            "sample_count": int(payload.get("sample_count") or 0),
            "recommendation": recommendation,
            "severity": round(severity, 4),
            "benchmark_context": {
                "window": benchmark_window,
                "current_pass_rate": round(benchmark_pass_rate, 4),
                "required_pass_rate": round(benchmark_required, 4),
                "gap": round(benchmark_gap, 4),
            },
            "failed_horizons": failed_horizons,
            "failed_check_names": [str(item.get("name") or "") for item in failed_checks if str(item.get("name") or "")],
            "cooldown_cycles": cooldown_cycles,
            "evaluation": evaluation,
            "param_adjustments": param_adjustments,
            "scoring_adjustments": {},
            "suggestions": suggestions,
        }


def load_research_feedback_boundary(
    controller: Any,
    *,
    cutoff_date: str,
    manager_id: str,
    manager_config_ref: str,
    regime: str = "",
) -> dict[str, Any]:
    try:
        feedback = controller.research_case_store.build_training_feedback(
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
            as_of_date=cutoff_date,
            regime=regime,
            limit=200,
        )
    except Exception:
        logger.debug("research calibration feedback unavailable", exc_info=True)
        feedback = {}
    controller.last_research_feedback = dict(feedback or {})
    return controller.last_research_feedback


def sanitize_runtime_param_adjustments_boundary(
    controller: Any,
    adjustments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy_service = getattr(controller, "training_policy_service", None)
    if policy_service is not None and hasattr(policy_service, "sanitize_runtime_param_adjustments"):
        return dict(policy_service.sanitize_runtime_param_adjustments(controller, adjustments) or {})
    sanitize = getattr(controller, "_sanitize_runtime_param_adjustments", None)
    if callable(sanitize):
        payload = sanitize(adjustments)
        if isinstance(payload, dict):
            return {
                str(key): value
                for key, value in payload.items()
            }
    return dict(adjustments or {})
