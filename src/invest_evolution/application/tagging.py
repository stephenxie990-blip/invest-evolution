"""Tagging contracts and canonical tag derivation helpers."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TAGGING_CONTRACT_VERSION = "tagging.v1"
VALIDATION_CONTRACT_VERSION = "validation.v1"
PEER_COMPARISON_CONTRACT_VERSION = "peer_comparison.v1"
JUDGE_CONTRACT_VERSION = "evolution_judge.v1"


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class RawTagEvidence(StrictContractModel):
    failure_signature: dict[str, Any] = Field(default_factory=dict)
    review_result: dict[str, Any] = Field(default_factory=dict)
    run_context: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class TaggingResult(StrictContractModel):
    contract_version: str = TAGGING_CONTRACT_VERSION
    tag_family: Literal["market", "failure", "validation"]
    primary_tag: str = "unknown"
    normalized_tags: list[str] = Field(default_factory=list)
    confidence_score: float = 0.0
    review_required: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    raw_evidence: RawTagEvidence = Field(default_factory=RawTagEvidence)

    @field_validator("confidence_score")
    @classmethod
    def _clamp_confidence_score(cls, value: float) -> float:
        return max(0.0, min(float(value), 1.0))


class ValidationCheck(StrictContractModel):
    name: str
    passed: bool
    reason_code: str
    actual: Any = None
    threshold: Any = None
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationSummary(StrictContractModel):
    contract_version: str = VALIDATION_CONTRACT_VERSION
    validation_task_id: str
    status: Literal["passed", "hold", "failed"]
    shadow_mode: bool = False
    review_required: bool = False
    confidence_score: float = 0.0
    validation_tags: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    checks: list[ValidationCheck] = Field(default_factory=list)
    failed_checks: list[ValidationCheck] = Field(default_factory=list)
    raw_evidence: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""

    @field_validator("confidence_score")
    @classmethod
    def _clamp_confidence_score(cls, value: float) -> float:
        return max(0.0, min(float(value), 1.0))


class PeerComparisonResult(StrictContractModel):
    contract_version: str = PEER_COMPARISON_CONTRACT_VERSION
    compared_market_tag: str = "unknown"
    comparable: bool = False
    compared_count: int = 0
    ranked_peers: list[dict[str, Any]] = Field(default_factory=list)
    dominant_peer: str = ""
    peer_dominated: bool = False
    candidate_outperformed_peers: bool | None = None
    reason_codes: list[str] = Field(default_factory=list)
    summary: str = ""


class JudgeReport(StrictContractModel):
    contract_version: str = JUDGE_CONTRACT_VERSION
    decision: Literal["promote", "reject", "hold", "switch_to_peer", "continue_optimize"]
    shadow_mode: bool = False
    actionable: bool = True
    review_required: bool = False
    validation_status: str = "hold"
    reason_codes: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    summary: str = ""

_PRIMARY_DRIVER_TAGS = {
    "insufficient_history": "insufficient_history",
    "mixed_factors": "mixed_factors",
}
_FEEDBACK_BIAS_TAGS = {
    "tighten_risk": "tighten_risk",
    "recalibrate_probability": "recalibrate_probability",
}


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower()


def build_failure_tagging_result(
    *,
    failure_signature: dict[str, Any] | None = None,
    review_result: dict[str, Any] | None = None,
    run_context: dict[str, Any] | None = None,
) -> TaggingResult:
    signature = dict(failure_signature or {})
    review_payload = dict(review_result or {})
    run_payload = dict(run_context or {})
    if not signature:
        signature = dict(review_payload.get("failure_signature") or {})
    normalized_tags: list[str] = []
    return_direction = _normalize_token(signature.get("return_direction"))
    if return_direction == "loss":
        normalized_tags.append("loss")
    if signature.get("benchmark_passed") is False:
        normalized_tags.append("benchmark_miss")
    primary_driver = _normalize_token(signature.get("primary_driver"))
    if primary_driver in _PRIMARY_DRIVER_TAGS:
        normalized_tags.append(_PRIMARY_DRIVER_TAGS[primary_driver])
    feedback_bias = _normalize_token(signature.get("feedback_bias"))
    if feedback_bias in _FEEDBACK_BIAS_TAGS:
        normalized_tags.append(_FEEDBACK_BIAS_TAGS[feedback_bias])
    reason_codes: list[str] = []
    if normalized_tags:
        confidence_score = 0.9 if signature else 0.6
        reason_codes.append("failure_signature_classified")
    else:
        normalized_tags = ["no_failure_signal"]
        confidence_score = 0.45 if not signature else 0.7
        reason_codes.append("failure_signature_sparse")
    return TaggingResult(
        tag_family="failure",
        primary_tag=normalized_tags[0],
        normalized_tags=normalized_tags,
        confidence_score=confidence_score,
        review_required=confidence_score < 0.6,
        reason_codes=reason_codes,
        raw_evidence=RawTagEvidence(
            failure_signature=signature,
            review_result=review_payload,
            run_context=run_payload,
            extra={
                "primary_driver": primary_driver,
                "feedback_bias": feedback_bias,
            },
        ),
    )

_MARKET_TAG_ALIASES = {
    "bull": "bull",
    "bullish": "bull",
    "uptrend": "bull",
    "bear": "bear",
    "bearish": "bear",
    "downtrend": "bear",
    "oscillation": "oscillation",
    "sideways": "oscillation",
    "range": "oscillation",
    "neutral": "oscillation",
    "transition": "transition",
    "regime_change": "transition",
    "unknown": "unknown",
}


def normalize_market_tag(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _MARKET_TAG_ALIASES.get(text, "unknown")


def build_market_tagging_result(
    *,
    review_result: dict[str, Any] | None = None,
    run_context: dict[str, Any] | None = None,
) -> TaggingResult:
    review_payload = dict(review_result or {})
    run_payload = dict(run_context or {})
    raw_value = (
        review_payload.get("regime")
        or dict(review_payload.get("metadata") or {}).get("regime")
        or run_payload.get("market_tag")
        or run_payload.get("regime")
        or dict(run_payload.get("governance_decision") or {}).get("regime")
        or ""
    )
    normalized = normalize_market_tag(raw_value)
    explicit = str(raw_value or "").strip().lower()
    if normalized == "unknown":
        confidence_score = 0.35
        reason_codes = ["market_tag_unknown", "insufficient_evidence"]
    elif explicit == normalized:
        confidence_score = 1.0
        reason_codes = ["market_tag_explicit"]
    else:
        confidence_score = 0.8
        reason_codes = ["market_tag_normalized_alias"]
    return TaggingResult(
        tag_family="market",
        primary_tag=normalized,
        normalized_tags=[normalized],
        confidence_score=confidence_score,
        review_required=confidence_score < 0.6,
        reason_codes=reason_codes,
        raw_evidence=RawTagEvidence(
            review_result=review_payload,
            run_context=run_payload,
            extra={"raw_market_tag": str(raw_value or "")},
        ),
    )

_PRIORITIZED_VALIDATION_TAGS = [
    "validation_passed",
    "candidate_missing",
    "insufficient_evidence",
    "insufficient_sample",
    "ab_failed",
    "peer_dominated",
    "governance_blocked",
    "needs_more_optimization",
]


def build_validation_tagging_result(
    validation_summary: ValidationSummary | dict[str, Any],
) -> TaggingResult:
    summary = (
        validation_summary
        if isinstance(validation_summary, ValidationSummary)
        else ValidationSummary.model_validate(dict(validation_summary or {}))
    )
    raw_summary_evidence = dict(summary.raw_evidence or {})
    review_result = dict(raw_summary_evidence.get("review_result") or {})
    tags: list[str] = []
    for item in _PRIORITIZED_VALIDATION_TAGS:
        if item == "validation_passed":
            if summary.status == "passed" and not summary.failed_checks:
                tags.append(item)
            continue
        if item in summary.reason_codes:
            tags.append(item)
    if not tags:
        tags = ["insufficient_evidence"]
    primary_tag = tags[0]
    return TaggingResult(
        tag_family="validation",
        primary_tag=primary_tag,
        normalized_tags=tags,
        confidence_score=summary.confidence_score,
        review_required=summary.review_required or summary.confidence_score < 0.6,
        reason_codes=list(summary.reason_codes),
        raw_evidence=RawTagEvidence(
            failure_signature=dict(review_result.get("failure_signature") or {}),
            review_result=review_result,
            run_context=dict(raw_summary_evidence.get("run_context") or {}),
            extra={
                "cycle_result": dict(raw_summary_evidence.get("cycle_result") or {}),
            },
        ),
    )
