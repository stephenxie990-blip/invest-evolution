from __future__ import annotations

from typing import Any

import pytest

from invest_evolution.application.tagging import TAGGING_CONTRACT_VERSION
from invest_evolution.application.tagging import ValidationCheck, ValidationSummary
from invest_evolution.application.tagging import build_failure_tagging_result
from invest_evolution.application.tagging import build_market_tagging_result, normalize_market_tag
from invest_evolution.application.tagging import build_validation_tagging_result


def _deep_get(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            raise KeyError(path)
        current = current[part]
    return current


def _market_case(
    case_id: str,
    *,
    review_result: dict[str, Any] | None = None,
    run_context: dict[str, Any] | None = None,
    primary_tag: str,
    normalized_tags: list[str],
    reason_codes: list[str],
    confidence_score: float,
    review_required: bool,
    raw_checks: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": case_id,
        "family": "market",
        "input": {
            "review_result": review_result or {},
            "run_context": run_context or {},
        },
        "expected": {
            "primary_tag": primary_tag,
            "normalized_tags": normalized_tags,
            "reason_codes": reason_codes,
            "confidence_score": confidence_score,
            "review_required": review_required,
            "raw_checks": raw_checks,
        },
    }


def _failure_case(
    case_id: str,
    *,
    failure_signature: dict[str, Any] | None = None,
    review_result: dict[str, Any] | None = None,
    run_context: dict[str, Any] | None = None,
    primary_tag: str,
    normalized_tags: list[str],
    reason_codes: list[str],
    confidence_score: float,
    review_required: bool,
    raw_checks: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": case_id,
        "family": "failure",
        "input": {
            "failure_signature": failure_signature or {},
            "review_result": review_result or {},
            "run_context": run_context or {},
        },
        "expected": {
            "primary_tag": primary_tag,
            "normalized_tags": normalized_tags,
            "reason_codes": reason_codes,
            "confidence_score": confidence_score,
            "review_required": review_required,
            "raw_checks": raw_checks,
        },
    }


def _check(name: str, passed: bool, reason_code: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "reason_code": reason_code,
        "actual": None,
        "threshold": None,
        "details": {},
    }


def _validation_case(
    case_id: str,
    *,
    summary: dict[str, Any],
    primary_tag: str,
    normalized_tags: list[str],
    reason_codes: list[str],
    confidence_score: float,
    review_required: bool,
    raw_checks: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": case_id,
        "family": "validation",
        "input": {"summary": summary},
        "expected": {
            "primary_tag": primary_tag,
            "normalized_tags": normalized_tags,
            "reason_codes": reason_codes,
            "confidence_score": confidence_score,
            "review_required": review_required,
            "raw_checks": raw_checks,
        },
    }


MARKET_CASES = [
    _market_case(
        "market_explicit_review_bull",
        review_result={"regime": "bull"},
        run_context={"governance_decision": {"regime": "bear"}},
        primary_tag="bull",
        normalized_tags=["bull"],
        reason_codes=["market_tag_explicit"],
        confidence_score=1.0,
        review_required=False,
        raw_checks={"raw_evidence.extra.raw_market_tag": "bull"},
    ),
    _market_case(
        "market_review_metadata_bear",
        review_result={"metadata": {"regime": "bear"}},
        primary_tag="bear",
        normalized_tags=["bear"],
        reason_codes=["market_tag_explicit"],
        confidence_score=1.0,
        review_required=False,
        raw_checks={"raw_evidence.extra.raw_market_tag": "bear"},
    ),
    _market_case(
        "market_run_context_market_tag_oscillation",
        run_context={"market_tag": "oscillation"},
        primary_tag="oscillation",
        normalized_tags=["oscillation"],
        reason_codes=["market_tag_explicit"],
        confidence_score=1.0,
        review_required=False,
        raw_checks={"raw_evidence.run_context.market_tag": "oscillation"},
    ),
    _market_case(
        "market_run_context_regime_transition",
        run_context={"regime": "transition"},
        primary_tag="transition",
        normalized_tags=["transition"],
        reason_codes=["market_tag_explicit"],
        confidence_score=1.0,
        review_required=False,
        raw_checks={"raw_evidence.extra.raw_market_tag": "transition"},
    ),
    _market_case(
        "market_governance_decision_bull",
        run_context={"governance_decision": {"regime": "bull"}},
        primary_tag="bull",
        normalized_tags=["bull"],
        reason_codes=["market_tag_explicit"],
        confidence_score=1.0,
        review_required=False,
        raw_checks={"raw_evidence.run_context.governance_decision.regime": "bull"},
    ),
    _market_case(
        "market_alias_bullish",
        review_result={"regime": "bullish"},
        primary_tag="bull",
        normalized_tags=["bull"],
        reason_codes=["market_tag_normalized_alias"],
        confidence_score=0.8,
        review_required=False,
        raw_checks={"raw_evidence.extra.raw_market_tag": "bullish"},
    ),
    _market_case(
        "market_alias_downtrend",
        review_result={"regime": "downtrend"},
        primary_tag="bear",
        normalized_tags=["bear"],
        reason_codes=["market_tag_normalized_alias"],
        confidence_score=0.8,
        review_required=False,
        raw_checks={"raw_evidence.extra.raw_market_tag": "downtrend"},
    ),
    _market_case(
        "market_alias_sideways",
        review_result={"regime": "sideways"},
        primary_tag="oscillation",
        normalized_tags=["oscillation"],
        reason_codes=["market_tag_normalized_alias"],
        confidence_score=0.8,
        review_required=False,
        raw_checks={"raw_evidence.extra.raw_market_tag": "sideways"},
    ),
    _market_case(
        "market_alias_regime_change",
        review_result={"regime": "regime_change"},
        primary_tag="transition",
        normalized_tags=["transition"],
        reason_codes=["market_tag_normalized_alias"],
        confidence_score=0.8,
        review_required=False,
        raw_checks={"raw_evidence.extra.raw_market_tag": "regime_change"},
    ),
    _market_case(
        "market_conflict_review_beats_routing",
        review_result={"regime": "bull"},
        run_context={"governance_decision": {"regime": "bear"}},
        primary_tag="bull",
        normalized_tags=["bull"],
        reason_codes=["market_tag_explicit"],
        confidence_score=1.0,
        review_required=False,
        raw_checks={"raw_evidence.run_context.governance_decision.regime": "bear"},
    ),
    _market_case(
        "market_unknown_empty",
        review_result={},
        run_context={},
        primary_tag="unknown",
        normalized_tags=["unknown"],
        reason_codes=["market_tag_unknown", "insufficient_evidence"],
        confidence_score=0.35,
        review_required=True,
        raw_checks={"raw_evidence.extra.raw_market_tag": ""},
    ),
    _market_case(
        "market_unknown_unrecognized_value",
        run_context={"market_tag": "mystery"},
        primary_tag="unknown",
        normalized_tags=["unknown"],
        reason_codes=["market_tag_unknown", "insufficient_evidence"],
        confidence_score=0.35,
        review_required=True,
        raw_checks={"raw_evidence.extra.raw_market_tag": "mystery"},
    ),
]


FAILURE_CASES = [
    _failure_case(
        "failure_full_signature",
        failure_signature={
            "return_direction": "loss",
            "benchmark_passed": False,
            "primary_driver": "insufficient_history",
            "feedback_bias": "tighten_risk",
        },
        review_result={"cycle_id": 1},
        run_context={"candidate_runtime_config_ref": "configs/candidate.yaml"},
        primary_tag="loss",
        normalized_tags=["loss", "benchmark_miss", "insufficient_history", "tighten_risk"],
        reason_codes=["failure_signature_classified"],
        confidence_score=0.9,
        review_required=False,
        raw_checks={
            "raw_evidence.failure_signature.primary_driver": "insufficient_history",
            "raw_evidence.run_context.candidate_runtime_config_ref": "configs/candidate.yaml",
        },
    ),
    _failure_case(
        "failure_loss_only",
        failure_signature={"return_direction": "loss"},
        primary_tag="loss",
        normalized_tags=["loss"],
        reason_codes=["failure_signature_classified"],
        confidence_score=0.9,
        review_required=False,
        raw_checks={"raw_evidence.failure_signature.return_direction": "loss"},
    ),
    _failure_case(
        "failure_benchmark_miss_only",
        failure_signature={"benchmark_passed": False},
        primary_tag="benchmark_miss",
        normalized_tags=["benchmark_miss"],
        reason_codes=["failure_signature_classified"],
        confidence_score=0.9,
        review_required=False,
        raw_checks={"raw_evidence.failure_signature.benchmark_passed": False},
    ),
    _failure_case(
        "failure_primary_driver_insufficient_history",
        failure_signature={"primary_driver": "insufficient_history"},
        primary_tag="insufficient_history",
        normalized_tags=["insufficient_history"],
        reason_codes=["failure_signature_classified"],
        confidence_score=0.9,
        review_required=False,
        raw_checks={"raw_evidence.extra.primary_driver": "insufficient_history"},
    ),
    _failure_case(
        "failure_primary_driver_mixed_factors",
        failure_signature={"primary_driver": "mixed_factors"},
        primary_tag="mixed_factors",
        normalized_tags=["mixed_factors"],
        reason_codes=["failure_signature_classified"],
        confidence_score=0.9,
        review_required=False,
        raw_checks={"raw_evidence.extra.primary_driver": "mixed_factors"},
    ),
    _failure_case(
        "failure_feedback_bias_tighten_risk",
        failure_signature={"feedback_bias": "tighten_risk"},
        primary_tag="tighten_risk",
        normalized_tags=["tighten_risk"],
        reason_codes=["failure_signature_classified"],
        confidence_score=0.9,
        review_required=False,
        raw_checks={"raw_evidence.extra.feedback_bias": "tighten_risk"},
    ),
    _failure_case(
        "failure_feedback_bias_recalibrate_probability",
        failure_signature={"feedback_bias": "recalibrate_probability"},
        primary_tag="recalibrate_probability",
        normalized_tags=["recalibrate_probability"],
        reason_codes=["failure_signature_classified"],
        confidence_score=0.9,
        review_required=False,
        raw_checks={"raw_evidence.extra.feedback_bias": "recalibrate_probability"},
    ),
    _failure_case(
        "failure_multi_signal_with_mixed_factors",
        failure_signature={
            "return_direction": "loss",
            "benchmark_passed": False,
            "primary_driver": "mixed_factors",
        },
        primary_tag="loss",
        normalized_tags=["loss", "benchmark_miss", "mixed_factors"],
        reason_codes=["failure_signature_classified"],
        confidence_score=0.9,
        review_required=False,
        raw_checks={"raw_evidence.extra.primary_driver": "mixed_factors"},
    ),
    _failure_case(
        "failure_review_result_fallback",
        review_result={
            "failure_signature": {
                "return_direction": "loss",
                "feedback_bias": "tighten_risk",
            }
        },
        primary_tag="loss",
        normalized_tags=["loss", "tighten_risk"],
        reason_codes=["failure_signature_classified"],
        confidence_score=0.9,
        review_required=False,
        raw_checks={"raw_evidence.review_result.failure_signature.feedback_bias": "tighten_risk"},
    ),
    _failure_case(
        "failure_empty_signature",
        failure_signature={},
        primary_tag="no_failure_signal",
        normalized_tags=["no_failure_signal"],
        reason_codes=["failure_signature_sparse"],
        confidence_score=0.45,
        review_required=True,
        raw_checks={"raw_evidence.failure_signature": {}},
    ),
    _failure_case(
        "failure_sparse_unknown_signature",
        failure_signature={"primary_driver": "other", "feedback_bias": "unknown"},
        primary_tag="no_failure_signal",
        normalized_tags=["no_failure_signal"],
        reason_codes=["failure_signature_sparse"],
        confidence_score=0.7,
        review_required=False,
        raw_checks={
            "raw_evidence.extra.primary_driver": "other",
            "raw_evidence.extra.feedback_bias": "unknown",
        },
    ),
    _failure_case(
        "failure_uppercase_return_and_bias",
        failure_signature={"return_direction": "LOSS", "feedback_bias": "TIGHTEN_RISK"},
        primary_tag="loss",
        normalized_tags=["loss", "tighten_risk"],
        reason_codes=["failure_signature_classified"],
        confidence_score=0.9,
        review_required=False,
        raw_checks={
            "raw_evidence.failure_signature.return_direction": "LOSS",
            "raw_evidence.extra.feedback_bias": "tighten_risk",
        },
    ),
    _failure_case(
        "failure_whitespace_wrapped_tokens",
        failure_signature={"return_direction": " loss ", "feedback_bias": " tighten_risk "},
        primary_tag="loss",
        normalized_tags=["loss", "tighten_risk"],
        reason_codes=["failure_signature_classified"],
        confidence_score=0.9,
        review_required=False,
        raw_checks={
            "raw_evidence.failure_signature.return_direction": " loss ",
            "raw_evidence.extra.feedback_bias": "tighten_risk",
        },
    ),
    _failure_case(
        "failure_uppercase_primary_driver",
        failure_signature={"primary_driver": "MIXED_FACTORS"},
        primary_tag="mixed_factors",
        normalized_tags=["mixed_factors"],
        reason_codes=["failure_signature_classified"],
        confidence_score=0.9,
        review_required=False,
        raw_checks={"raw_evidence.extra.primary_driver": "mixed_factors"},
    ),
]


VALIDATION_CASES = [
    _validation_case(
        "validation_passed_clean",
        summary={
            "validation_task_id": "val_passed_1",
            "status": "passed",
            "confidence_score": 0.95,
            "review_required": False,
            "checks": [],
            "failed_checks": [],
            "validation_tags": ["validation_passed"],
            "reason_codes": [],
            "raw_evidence": {},
            "summary": "ok",
        },
        primary_tag="validation_passed",
        normalized_tags=["validation_passed"],
        reason_codes=[],
        confidence_score=0.95,
        review_required=False,
        raw_checks={"raw_evidence.review_result": {}},
    ),
    _validation_case(
        "validation_passed_status_with_failed_checks",
        summary={
            "validation_task_id": "val_passed_2",
            "status": "passed",
            "confidence_score": 0.91,
            "checks": [],
            "failed_checks": [_check("candidate_ab.outperform_active", False, "ab_failed")],
            "validation_tags": ["ab_failed"],
            "reason_codes": ["ab_failed"],
            "raw_evidence": {},
            "summary": "has failed checks",
        },
        primary_tag="ab_failed",
        normalized_tags=["ab_failed"],
        reason_codes=["ab_failed"],
        confidence_score=0.91,
        review_required=False,
        raw_checks={"raw_evidence.run_context": {}},
    ),
    _validation_case(
        "validation_hold_empty",
        summary={
            "validation_task_id": "val_hold_1",
            "status": "hold",
            "confidence_score": 0.4,
            "checks": [],
            "failed_checks": [],
            "validation_tags": [],
            "reason_codes": [],
            "raw_evidence": {},
            "summary": "empty",
        },
        primary_tag="insufficient_evidence",
        normalized_tags=["insufficient_evidence"],
        reason_codes=[],
        confidence_score=0.4,
        review_required=True,
        raw_checks={"raw_evidence.review_result": {}},
    ),
    _validation_case(
        "validation_failed_ab_failed",
        summary={
            "validation_task_id": "val_failed_1",
            "status": "failed",
            "confidence_score": 0.88,
            "checks": [],
            "failed_checks": [_check("candidate_ab.outperform_active", False, "ab_failed")],
            "validation_tags": ["ab_failed"],
            "reason_codes": ["ab_failed"],
            "raw_evidence": {},
            "summary": "ab failed",
        },
        primary_tag="ab_failed",
        normalized_tags=["ab_failed"],
        reason_codes=["ab_failed"],
        confidence_score=0.88,
        review_required=False,
        raw_checks={"raw_evidence.run_context": {}},
    ),
    _validation_case(
        "validation_failed_governance_blocked",
        summary={
            "validation_task_id": "val_failed_2",
            "status": "failed",
            "confidence_score": 0.86,
            "checks": [],
            "failed_checks": [_check("promotion_discipline.status", False, "governance_blocked")],
            "validation_tags": ["governance_blocked"],
            "reason_codes": ["governance_blocked"],
            "raw_evidence": {},
            "summary": "governance blocked",
        },
        primary_tag="governance_blocked",
        normalized_tags=["governance_blocked"],
        reason_codes=["governance_blocked"],
        confidence_score=0.86,
        review_required=False,
        raw_checks={"raw_evidence.run_context": {}},
    ),
    _validation_case(
        "validation_hold_candidate_missing",
        summary={
            "validation_task_id": "val_hold_2",
            "status": "hold",
            "confidence_score": 0.82,
            "checks": [],
            "failed_checks": [_check("candidate.precheck", False, "candidate_missing")],
            "validation_tags": ["candidate_missing"],
            "reason_codes": ["candidate_missing"],
            "raw_evidence": {},
            "summary": "candidate missing",
        },
        primary_tag="candidate_missing",
        normalized_tags=["candidate_missing"],
        reason_codes=["candidate_missing"],
        confidence_score=0.82,
        review_required=False,
        raw_checks={"raw_evidence.review_result": {}},
    ),
    _validation_case(
        "validation_hold_insufficient_evidence_reason",
        summary={
            "validation_task_id": "val_hold_3",
            "status": "hold",
            "confidence_score": 0.62,
            "checks": [],
            "failed_checks": [],
            "validation_tags": ["insufficient_evidence"],
            "reason_codes": ["insufficient_evidence"],
            "raw_evidence": {},
            "summary": "insufficient evidence",
        },
        primary_tag="insufficient_evidence",
        normalized_tags=["insufficient_evidence"],
        reason_codes=["insufficient_evidence"],
        confidence_score=0.62,
        review_required=False,
        raw_checks={"raw_evidence.review_result": {}},
    ),
    _validation_case(
        "validation_hold_insufficient_sample",
        summary={
            "validation_task_id": "val_hold_4",
            "status": "hold",
            "confidence_score": 0.77,
            "checks": [],
            "failed_checks": [_check("research_feedback.passed", False, "insufficient_sample")],
            "validation_tags": ["insufficient_sample"],
            "reason_codes": ["insufficient_sample"],
            "raw_evidence": {},
            "summary": "insufficient sample",
        },
        primary_tag="insufficient_sample",
        normalized_tags=["insufficient_sample"],
        reason_codes=["insufficient_sample"],
        confidence_score=0.77,
        review_required=False,
        raw_checks={"raw_evidence.run_context": {}},
    ),
    _validation_case(
        "validation_hold_peer_dominated",
        summary={
            "validation_task_id": "val_hold_5",
            "status": "hold",
            "confidence_score": 0.79,
            "checks": [],
            "failed_checks": [],
            "validation_tags": ["peer_dominated"],
            "reason_codes": ["peer_dominated"],
            "raw_evidence": {},
            "summary": "peer dominated",
        },
        primary_tag="peer_dominated",
        normalized_tags=["peer_dominated"],
        reason_codes=["peer_dominated"],
        confidence_score=0.79,
        review_required=False,
        raw_checks={"raw_evidence.run_context": {}},
    ),
    _validation_case(
        "validation_hold_needs_more_optimization",
        summary={
            "validation_task_id": "val_hold_6",
            "status": "hold",
            "confidence_score": 0.83,
            "checks": [],
            "failed_checks": [],
            "validation_tags": ["needs_more_optimization"],
            "reason_codes": ["needs_more_optimization"],
            "raw_evidence": {},
            "summary": "needs more optimization",
        },
        primary_tag="needs_more_optimization",
        normalized_tags=["needs_more_optimization"],
        reason_codes=["needs_more_optimization"],
        confidence_score=0.83,
        review_required=False,
        raw_checks={"raw_evidence.run_context": {}},
    ),
    _validation_case(
        "validation_hold_ab_failed_beats_governance",
        summary={
            "validation_task_id": "val_hold_7",
            "status": "hold",
            "confidence_score": 0.87,
            "checks": [],
            "failed_checks": [
                _check("candidate_ab.outperform_active", False, "ab_failed"),
                _check("promotion_discipline.status", False, "governance_blocked"),
            ],
            "validation_tags": ["ab_failed", "governance_blocked"],
            "reason_codes": ["governance_blocked", "ab_failed"],
            "raw_evidence": {},
            "summary": "ab and governance failed",
        },
        primary_tag="ab_failed",
        normalized_tags=["ab_failed", "governance_blocked"],
        reason_codes=["governance_blocked", "ab_failed"],
        confidence_score=0.87,
        review_required=False,
        raw_checks={"raw_evidence.run_context": {}},
    ),
    _validation_case(
        "validation_hold_candidate_missing_beats_ab_failed",
        summary={
            "validation_task_id": "val_hold_8",
            "status": "hold",
            "confidence_score": 0.84,
            "checks": [],
            "failed_checks": [
                _check("candidate.precheck", False, "candidate_missing"),
                _check("candidate_ab.outperform_active", False, "ab_failed"),
            ],
            "validation_tags": ["candidate_missing", "ab_failed"],
            "reason_codes": ["ab_failed", "candidate_missing"],
            "raw_evidence": {},
            "summary": "candidate missing and ab failed",
        },
        primary_tag="candidate_missing",
        normalized_tags=["candidate_missing", "ab_failed"],
        reason_codes=["ab_failed", "candidate_missing"],
        confidence_score=0.84,
        review_required=False,
        raw_checks={"raw_evidence.run_context": {}},
    ),
    _validation_case(
        "validation_hold_unknown_reason_fallback",
        summary={
            "validation_task_id": "val_hold_9",
            "status": "hold",
            "confidence_score": 0.74,
            "checks": [],
            "failed_checks": [],
            "validation_tags": [],
            "reason_codes": ["mystery_reason"],
            "raw_evidence": {},
            "summary": "unknown reason",
        },
        primary_tag="insufficient_evidence",
        normalized_tags=["insufficient_evidence"],
        reason_codes=["mystery_reason"],
        confidence_score=0.74,
        review_required=False,
        raw_checks={"raw_evidence.run_context": {}},
    ),
    _validation_case(
        "validation_hold_unknown_and_insufficient_sample",
        summary={
            "validation_task_id": "val_hold_10",
            "status": "hold",
            "confidence_score": 0.74,
            "checks": [],
            "failed_checks": [],
            "validation_tags": ["insufficient_sample"],
            "reason_codes": ["mystery_reason", "insufficient_sample"],
            "raw_evidence": {},
            "summary": "mixed reasons",
        },
        primary_tag="insufficient_sample",
        normalized_tags=["insufficient_sample"],
        reason_codes=["mystery_reason", "insufficient_sample"],
        confidence_score=0.74,
        review_required=False,
        raw_checks={"raw_evidence.run_context": {}},
    ),
    _validation_case(
        "validation_passed_but_review_required",
        summary={
            "validation_task_id": "val_passed_3",
            "status": "passed",
            "confidence_score": 0.93,
            "review_required": True,
            "checks": [],
            "failed_checks": [],
            "validation_tags": ["validation_passed"],
            "reason_codes": [],
            "raw_evidence": {},
            "summary": "manual review required",
        },
        primary_tag="validation_passed",
        normalized_tags=["validation_passed"],
        reason_codes=[],
        confidence_score=0.93,
        review_required=True,
        raw_checks={"raw_evidence.review_result": {}},
    ),
    _validation_case(
        "validation_hold_low_confidence_forces_review",
        summary={
            "validation_task_id": "val_hold_11",
            "status": "hold",
            "confidence_score": 0.55,
            "review_required": False,
            "checks": [],
            "failed_checks": [],
            "validation_tags": ["needs_more_optimization"],
            "reason_codes": ["needs_more_optimization"],
            "raw_evidence": {},
            "summary": "low confidence",
        },
        primary_tag="needs_more_optimization",
        normalized_tags=["needs_more_optimization"],
        reason_codes=["needs_more_optimization"],
        confidence_score=0.55,
        review_required=True,
        raw_checks={"raw_evidence.review_result": {}},
    ),
    _validation_case(
        "validation_raw_evidence_cycle_result_preserved",
        summary={
            "validation_task_id": "val_hold_12",
            "status": "hold",
            "confidence_score": 0.81,
            "checks": [],
            "failed_checks": [],
            "validation_tags": ["needs_more_optimization"],
            "reason_codes": ["needs_more_optimization"],
            "raw_evidence": {
                "review_result": {"failure_signature": {"return_direction": "loss"}},
                "run_context": {"candidate_runtime_config_ref": "configs/candidate.yaml"},
                "cycle_result": {"return_pct": -1.2},
            },
            "summary": "raw evidence present",
        },
        primary_tag="needs_more_optimization",
        normalized_tags=["needs_more_optimization"],
        reason_codes=["needs_more_optimization"],
        confidence_score=0.81,
        review_required=False,
        raw_checks={
            "raw_evidence.failure_signature.return_direction": "loss",
            "raw_evidence.extra.cycle_result.return_pct": -1.2,
        },
    ),
    _validation_case(
        "validation_failed_governance_and_needs_more_optimization",
        summary={
            "validation_task_id": "val_failed_3",
            "status": "failed",
            "confidence_score": 0.89,
            "checks": [],
            "failed_checks": [
                _check("promotion_discipline.status", False, "governance_blocked"),
                _check("summary.status", False, "needs_more_optimization"),
            ],
            "validation_tags": ["governance_blocked", "needs_more_optimization"],
            "reason_codes": ["needs_more_optimization", "governance_blocked"],
            "raw_evidence": {},
            "summary": "governance plus optimization",
        },
        primary_tag="governance_blocked",
        normalized_tags=["governance_blocked", "needs_more_optimization"],
        reason_codes=["needs_more_optimization", "governance_blocked"],
        confidence_score=0.89,
        review_required=False,
        raw_checks={"raw_evidence.review_result": {}},
    ),
    _validation_case(
        "validation_failed_peer_dominated_and_needs_more_optimization",
        summary={
            "validation_task_id": "val_failed_4",
            "status": "failed",
            "confidence_score": 0.84,
            "checks": [],
            "failed_checks": [
                _check("peer_comparison", False, "peer_dominated"),
                _check("summary.status", False, "needs_more_optimization"),
            ],
            "validation_tags": ["peer_dominated", "needs_more_optimization"],
            "reason_codes": ["needs_more_optimization", "peer_dominated"],
            "raw_evidence": {},
            "summary": "peer dominated and optimize",
        },
        primary_tag="peer_dominated",
        normalized_tags=["peer_dominated", "needs_more_optimization"],
        reason_codes=["needs_more_optimization", "peer_dominated"],
        confidence_score=0.84,
        review_required=False,
        raw_checks={"raw_evidence.review_result": {}},
    ),
    _validation_case(
        "validation_priority_ignores_reason_order",
        summary={
            "validation_task_id": "val_hold_13",
            "status": "hold",
            "confidence_score": 0.82,
            "checks": [],
            "failed_checks": [],
            "validation_tags": ["candidate_missing", "needs_more_optimization"],
            "reason_codes": ["needs_more_optimization", "candidate_missing"],
            "raw_evidence": {},
            "summary": "priority check",
        },
        primary_tag="candidate_missing",
        normalized_tags=["candidate_missing", "needs_more_optimization"],
        reason_codes=["needs_more_optimization", "candidate_missing"],
        confidence_score=0.82,
        review_required=False,
        raw_checks={"raw_evidence.review_result": {}},
    ),
    _validation_case(
        "validation_hold_duplicate_reason_codes_deduped_by_priority",
        summary={
            "validation_task_id": "val_hold_14",
            "status": "hold",
            "confidence_score": 0.8,
            "checks": [],
            "failed_checks": [],
            "validation_tags": ["insufficient_sample"],
            "reason_codes": ["insufficient_sample", "insufficient_sample"],
            "raw_evidence": {},
            "summary": "duplicate reasons",
        },
        primary_tag="insufficient_sample",
        normalized_tags=["insufficient_sample"],
        reason_codes=["insufficient_sample", "insufficient_sample"],
        confidence_score=0.8,
        review_required=False,
        raw_checks={"raw_evidence.review_result": {}},
    ),
    _validation_case(
        "validation_failed_ab_failed_with_unknown_reason",
        summary={
            "validation_task_id": "val_failed_5",
            "status": "failed",
            "confidence_score": 0.88,
            "checks": [],
            "failed_checks": [_check("candidate_ab.outperform_active", False, "ab_failed")],
            "validation_tags": ["ab_failed"],
            "reason_codes": ["mystery_reason", "ab_failed"],
            "raw_evidence": {},
            "summary": "ab failed plus unknown",
        },
        primary_tag="ab_failed",
        normalized_tags=["ab_failed"],
        reason_codes=["mystery_reason", "ab_failed"],
        confidence_score=0.88,
        review_required=False,
        raw_checks={"raw_evidence.review_result": {}},
    ),
    _validation_case(
        "validation_passed_with_run_context_preserved",
        summary={
            "validation_task_id": "val_passed_5",
            "status": "passed",
            "confidence_score": 0.97,
            "checks": [],
            "failed_checks": [],
            "validation_tags": ["validation_passed"],
            "reason_codes": [],
            "raw_evidence": {
                "run_context": {"candidate_runtime_config_ref": "configs/candidate.yaml"},
            },
            "summary": "pass with run context",
        },
        primary_tag="validation_passed",
        normalized_tags=["validation_passed"],
        reason_codes=[],
        confidence_score=0.97,
        review_required=False,
        raw_checks={"raw_evidence.run_context.candidate_runtime_config_ref": "configs/candidate.yaml"},
    ),
    _validation_case(
        "validation_hold_raw_evidence_review_passthrough",
        summary={
            "validation_task_id": "val_hold_18",
            "status": "hold",
            "confidence_score": 0.7,
            "checks": [],
            "failed_checks": [],
            "validation_tags": ["needs_more_optimization"],
            "reason_codes": ["needs_more_optimization"],
            "raw_evidence": {
                "review_result": {"cycle_id": 12, "failure_signature": {"feedback_bias": "tighten_risk"}},
            },
            "summary": "review passthrough",
        },
        primary_tag="needs_more_optimization",
        normalized_tags=["needs_more_optimization"],
        reason_codes=["needs_more_optimization"],
        confidence_score=0.7,
        review_required=False,
        raw_checks={
            "raw_evidence.review_result.cycle_id": 12,
            "raw_evidence.failure_signature.feedback_bias": "tighten_risk",
        },
    ),
]


BASELINE_CASES = MARKET_CASES + FAILURE_CASES + VALIDATION_CASES


def _build_result(case: dict[str, Any]):
    family = case["family"]
    payload = case["input"]
    if family == "market":
        return build_market_tagging_result(
            review_result=payload["review_result"],
            run_context=payload["run_context"],
        )
    if family == "failure":
        return build_failure_tagging_result(
            failure_signature=payload["failure_signature"],
            review_result=payload["review_result"],
            run_context=payload["run_context"],
        )
    if family == "validation":
        return build_validation_tagging_result(payload["summary"])
    raise AssertionError(f"unsupported family: {family}")


def test_tagging_baseline_has_expected_case_count():
    assert len(MARKET_CASES) == 12
    assert len(FAILURE_CASES) == 14
    assert len(VALIDATION_CASES) == 24
    assert len(BASELINE_CASES) == 50


@pytest.mark.parametrize("case", BASELINE_CASES, ids=[case["id"] for case in BASELINE_CASES])
def test_tagging_baseline_cases(case: dict[str, Any]):
    result = _build_result(case).to_dict()
    expected = case["expected"]

    assert result["contract_version"] == TAGGING_CONTRACT_VERSION
    assert result["tag_family"] == case["family"]
    assert result["primary_tag"] == expected["primary_tag"]
    assert result["normalized_tags"] == expected["normalized_tags"]
    assert result["reason_codes"] == expected["reason_codes"]
    assert result["confidence_score"] == expected["confidence_score"]
    assert result["review_required"] is expected["review_required"]

    for path, value in expected["raw_checks"].items():
        assert _deep_get(result, path) == value


def test_build_failure_tagging_result_preserves_raw_evidence():
    result = build_failure_tagging_result(
        failure_signature={
            "return_direction": "loss",
            "benchmark_passed": False,
            "primary_driver": "insufficient_history",
            "feedback_bias": "tighten_risk",
        },
        review_result={"cycle_id": 9},
        run_context={"candidate_runtime_config_ref": "configs/candidate.yaml"},
    )

    assert result.primary_tag == "loss"
    assert "benchmark_miss" in result.normalized_tags
    assert "insufficient_history" in result.normalized_tags
    assert "tighten_risk" in result.normalized_tags
    assert result.confidence_score == 0.9
    assert result.raw_evidence.failure_signature["primary_driver"] == "insufficient_history"
    assert result.raw_evidence.review_result["cycle_id"] == 9


def test_build_failure_tagging_result_falls_back_when_signature_sparse():
    result = build_failure_tagging_result(failure_signature={})

    assert result.primary_tag == "no_failure_signal"
    assert result.normalized_tags == ["no_failure_signal"]
    assert result.review_required is True
    assert result.confidence_score == 0.45


def test_normalize_market_tag_maps_known_aliases():
    assert normalize_market_tag("bull") == "bull"
    assert normalize_market_tag("bullish") == "bull"
    assert normalize_market_tag("downtrend") == "bear"
    assert normalize_market_tag("sideways") == "oscillation"
    assert normalize_market_tag("regime_change") == "transition"
    assert normalize_market_tag("mystery") == "unknown"


def test_build_market_tagging_result_prefers_explicit_review_regime():
    result = build_market_tagging_result(
        review_result={"regime": "bull"},
        run_context={"governance_decision": {"regime": "bear"}},
    )

    assert result.primary_tag == "bull"
    assert result.normalized_tags == ["bull"]
    assert result.confidence_score == 1.0
    assert result.review_required is False
    assert result.raw_evidence.review_result["regime"] == "bull"


def test_build_market_tagging_result_marks_unknown_as_low_confidence():
    result = build_market_tagging_result(review_result={}, run_context={})

    assert result.primary_tag == "unknown"
    assert result.normalized_tags == ["unknown"]
    assert result.review_required is True
    assert result.confidence_score == 0.35
    assert "insufficient_evidence" in result.reason_codes


def test_build_validation_tagging_result_marks_passed_summary():
    summary = ValidationSummary(
        validation_task_id="val_123",
        status="passed",
        confidence_score=0.92,
        checks=[],
        failed_checks=[],
        validation_tags=["validation_passed"],
        reason_codes=[],
    )

    result = build_validation_tagging_result(summary)

    assert result.primary_tag == "validation_passed"
    assert result.normalized_tags == ["validation_passed"]
    assert result.review_required is False


def test_build_validation_tagging_result_surfaces_failed_reason_codes():
    summary = ValidationSummary(
        validation_task_id="val_123",
        status="failed",
        confidence_score=0.88,
        checks=[],
        failed_checks=[
            ValidationCheck(name="candidate_ab.outperform_active", passed=False, reason_code="ab_failed"),
            ValidationCheck(name="promotion_discipline.status", passed=False, reason_code="governance_blocked"),
        ],
        validation_tags=["ab_failed", "governance_blocked"],
        reason_codes=["ab_failed", "governance_blocked"],
    )

    result = build_validation_tagging_result(summary)

    assert result.primary_tag == "ab_failed"
    assert result.normalized_tags == ["ab_failed", "governance_blocked"]
    assert result.reason_codes == ["ab_failed", "governance_blocked"]


def test_build_validation_tagging_result_falls_back_to_insufficient_evidence():
    summary = ValidationSummary(
        validation_task_id="val_123",
        status="hold",
        confidence_score=0.4,
        checks=[],
        failed_checks=[],
        validation_tags=[],
        reason_codes=[],
    )

    result = build_validation_tagging_result(summary)

    assert result.primary_tag == "insufficient_evidence"
    assert result.normalized_tags == ["insufficient_evidence"]
    assert result.review_required is True
