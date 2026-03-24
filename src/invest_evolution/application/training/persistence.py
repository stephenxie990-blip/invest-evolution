"""Merged training module: persistence.py."""

from __future__ import annotations

import hashlib
import json
import logging
from copy import deepcopy
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, cast

import numpy as np

from invest_evolution.application.training.observability import build_self_assessment_snapshot
from invest_evolution.application.training import review_contracts as training_review_contracts
from invest_evolution.application.training.policy import execution_defaults_payload, normalize_governance_decision
from invest_evolution.investment.governance import write_leaderboard
from invest_evolution.investment.shared.policy import normalize_config_ref

logger = logging.getLogger(__name__)


MAX_CYCLE_RESULT_BYTES = 512 * 1024
INLINE_TRADE_LIMIT = 25
INLINE_SIMILAR_RESULTS_LIMIT = 10
INLINE_OPTIMIZATION_EVENT_LIMIT = 10
INLINE_TEXT_LIMIT = 1000


class ArtifactTooLargeError(RuntimeError):
    def __init__(self, path: Path, *, actual_bytes: int, max_bytes: int):
        self.path = Path(path)
        self.actual_bytes = int(actual_bytes)
        self.max_bytes = int(max_bytes)
        super().__init__(
            f"artifact {self.path} exceeds size limit: {self.actual_bytes} > {self.max_bytes} bytes"
        )


def write_json_boundary(path: Path, payload: dict[str, Any], *, max_bytes: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    encoded = text.encode("utf-8")
    if max_bytes is not None and len(encoded) > int(max_bytes):
        raise ArtifactTooLargeError(path, actual_bytes=len(encoded), max_bytes=int(max_bytes))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _canonicalize_hash_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize_hash_payload(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        return [_canonicalize_hash_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize_hash_payload(item) for item in value]
    return value


def _stable_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _canonicalize_hash_payload(dict(payload)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _proposal_store_dir(base: Any) -> Path:
    if hasattr(base, "output_dir"):
        root = _resolved_output_dir(getattr(base, "output_dir"))
    else:
        root = _resolved_output_dir(base)
    store_dir = root / "proposal_store"
    store_dir.mkdir(parents=True, exist_ok=True)
    return store_dir


def _string_items(values: Any) -> list[str]:
    output: list[str] = []
    for value in list(values or []):
        item = str(value or "").strip()
        if item and item not in output:
            output.append(item)
    return output


def _proposal_sequence(proposal: dict[str, Any]) -> int:
    for candidate in (
        str(proposal.get("proposal_id") or "").strip(),
        str(proposal.get("suggestion_id") or "").strip(),
    ):
        if not candidate:
            continue
        try:
            return int(candidate.split("_")[-1])
        except (TypeError, ValueError):
            continue
    return 0


def _suggestion_text(
    proposal: dict[str, Any],
    *,
    source: str,
    rationale: str,
    patch: dict[str, Any],
) -> str:
    explicit = str(
        proposal.get("suggestion_text")
        or dict(proposal.get("metadata") or {}).get("suggestion_text")
        or ""
    ).strip()
    if explicit:
        return explicit
    if rationale:
        return rationale
    patch_keys = ", ".join(sorted(str(key) for key in dict(patch or {}).keys())[:3])
    if patch_keys:
        return f"{source}: {patch_keys}"
    return source or "learning_proposal"


def _ensure_proposal_tracking_fields_fallback(
    proposal: dict[str, Any] | None,
    *,
    default_cycle_id: int | None = None,
) -> dict[str, Any]:
    payload = deepcopy(dict(proposal or {}))
    cycle_id = int(payload.get("cycle_id") or default_cycle_id or 0)
    sequence = max(1, _proposal_sequence(payload) or 1)
    proposal_id = str(payload.get("proposal_id") or "").strip()
    if not proposal_id:
        proposal_id = f"proposal_{cycle_id:04d}_{sequence:03d}"
        payload["proposal_id"] = proposal_id

    suggestion_id = str(payload.get("suggestion_id") or "").strip()
    if not suggestion_id:
        suggestion_id = f"suggestion_{cycle_id:04d}_{sequence:03d}"
        payload["suggestion_id"] = suggestion_id

    source = str(payload.get("source") or "unknown").strip() or "unknown"
    patch = deepcopy(dict(payload.get("patch") or {}))
    rationale = str(payload.get("rationale") or "").strip()
    payload["suggestion_text"] = _suggestion_text(
        payload,
        source=source,
        rationale=rationale,
        patch=patch,
    )

    effect_window = deepcopy(dict(payload.get("effect_window") or {}))
    window_cycles = int(effect_window.get("window_cycles") or 3)
    window_cycles = max(1, window_cycles)
    payload["effect_window"] = {
        "window_cycles": window_cycles,
        "start_cycle_id": int(effect_window.get("start_cycle_id") or cycle_id + 1),
        "end_cycle_id": int(effect_window.get("end_cycle_id") or cycle_id + window_cycles),
        "evaluation_after_cycle_id": int(
            effect_window.get("evaluation_after_cycle_id") or cycle_id + window_cycles
        ),
    }
    payload["effect_target_metrics"] = _string_items(payload.get("effect_target_metrics") or []) or [
        "avg_return_pct",
        "benchmark_pass_rate",
    ]
    adoption_ref = deepcopy(dict(payload.get("adoption_ref") or {}))
    payload["adoption_status"] = str(payload.get("adoption_status") or "queued").strip() or "queued"
    payload["adoption_ref"] = {
        "decision_cycle_id": adoption_ref.get("decision_cycle_id"),
        "decision_stage": str(adoption_ref.get("decision_stage") or "proposal_recorded"),
        "decision_reason": str(adoption_ref.get("decision_reason") or "queued_for_candidate_governance"),
        "candidate_config_ref": str(adoption_ref.get("candidate_config_ref") or ""),
        "candidate_version_id": str(adoption_ref.get("candidate_version_id") or ""),
        "pending_candidate_ref": str(adoption_ref.get("pending_candidate_ref") or ""),
        "proposal_bundle_id": str(adoption_ref.get("proposal_bundle_id") or ""),
        "block_reasons": _string_items(adoption_ref.get("block_reasons") or []),
    }
    effect_status = str(payload.get("effect_status") or "pending_adoption").strip() or "pending_adoption"
    payload["effect_status"] = effect_status
    payload["effect_result"] = {
        "status": str(dict(payload.get("effect_result") or {}).get("status") or effect_status),
        "observed_cycles": int(dict(payload.get("effect_result") or {}).get("observed_cycles") or 0),
        "summary": str(dict(payload.get("effect_result") or {}).get("summary") or ""),
    }
    return payload


def _build_suggestion_tracking_summary_fallback(
    proposals: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    normalized = [
        _ensure_proposal_tracking_fields_fallback(dict(item or {}))
        for item in list(proposals or [])
    ]
    adoption_status_counts: dict[str, int] = {}
    effect_status_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    pending_evaluation_count = 0
    completed_evaluation_count = 0

    for proposal in normalized:
        adoption_status = str(proposal.get("adoption_status") or "queued")
        effect_status = str(proposal.get("effect_status") or "pending_adoption")
        source = str(proposal.get("source") or "unknown")
        adoption_status_counts[adoption_status] = adoption_status_counts.get(adoption_status, 0) + 1
        effect_status_counts[effect_status] = effect_status_counts.get(effect_status, 0) + 1
        source_counts[source] = source_counts.get(source, 0) + 1
        if effect_status == "pending":
            pending_evaluation_count += 1
        if effect_status in {"improved", "worsened", "neutral", "inconclusive"}:
            completed_evaluation_count += 1

    return {
        "schema_version": "training.suggestion_tracking_summary.v1",
        "suggestion_count": len(normalized),
        "adoption_status_counts": adoption_status_counts,
        "effect_status_counts": effect_status_counts,
        "pending_evaluation_count": pending_evaluation_count,
        "completed_evaluation_count": completed_evaluation_count,
        "source_counts": source_counts,
    }


def _resolve_suggestion_tracking_helpers() -> tuple[
    Callable[..., dict[str, Any]],
    Callable[[list[dict[str, Any]] | None], dict[str, Any]],
]:
    ensure_fn: Callable[..., dict[str, Any]] = _ensure_proposal_tracking_fields_fallback
    summary_fn: Callable[[list[dict[str, Any]] | None], dict[str, Any]] = (
        _build_suggestion_tracking_summary_fallback
    )
    try:
        module = import_module("invest_evolution.application.training.observability")
    except Exception:
        return ensure_fn, summary_fn

    resolved_ensure = getattr(module, "ensure_proposal_tracking_fields", None)
    resolved_summary = getattr(module, "build_suggestion_tracking_summary", None)
    if callable(resolved_ensure):
        ensure_fn = cast(Callable[..., dict[str, Any]], resolved_ensure)
    if callable(resolved_summary):
        summary_fn = cast(
            Callable[[list[dict[str, Any]] | None], dict[str, Any]],
            resolved_summary,
        )
    return ensure_fn, summary_fn


def _bool(value: Any) -> bool:
    return bool(value)


def _finite_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _truncate_text(value: Any, *, limit: int = INLINE_TEXT_LIMIT) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _dict_subset(payload: dict[str, Any], allowed_keys: tuple[str, ...]) -> dict[str, Any]:
    return {
        key: _jsonable(payload.get(key))
        for key in allowed_keys
        if key in payload and payload.get(key) not in (None, "", [], {})
    }


def _list_preview(items: list[Any] | None, *, limit: int) -> tuple[list[Any], bool]:
    values = list(items or [])
    return values[:limit], len(values) > limit


def _summary_or_empty(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _compact_value(value: Any, *, text_limit: int = 160, list_limit: int = 5, dict_limit: int = 8) -> Any:
    normalized = _jsonable(value)
    if isinstance(normalized, str):
        return _truncate_text(normalized, limit=text_limit)
    if isinstance(normalized, list):
        preview, truncated = _list_preview(normalized, limit=list_limit)
        return {
            "items": [_compact_value(item, text_limit=text_limit, list_limit=list_limit, dict_limit=dict_limit) for item in preview],
            "count": len(normalized),
            "truncated": truncated,
        }
    if isinstance(normalized, dict):
        items = list(normalized.items())
        preview = {
            str(key): _compact_value(item, text_limit=text_limit, list_limit=list_limit, dict_limit=dict_limit)
            for key, item in items[:dict_limit]
        }
        if len(items) > dict_limit:
            preview["_item_count"] = len(items)
            preview["_truncated"] = True
        return preview
    return normalized


def _trade_preview(trade_history: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], bool]:
    trades = list(trade_history or [])
    preview = [_jsonable(dict(item or {})) for item in trades[:INLINE_TRADE_LIMIT]]
    return preview, len(trades) > INLINE_TRADE_LIMIT


def _similar_results_preview(
    results: list[training_review_contracts.SimilarResultCompactPayload] | list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], bool]:
    preview: list[dict[str, Any]] = []
    for item in list(results or [])[:INLINE_SIMILAR_RESULTS_LIMIT]:
        payload = dict(item or {})
        preview.append(
            _dict_subset(
                payload,
                (
                    "cycle_id",
                    "cutoff_date",
                    "return_pct",
                    "regime",
                    "benchmark_passed",
                    "review_applied",
                    "selection_mode",
                    "manager_id",
                    "manager_config_ref",
                    "similarity_score",
                    "matched_features",
                ),
            )
        )
    return preview, len(list(results or [])) > INLINE_SIMILAR_RESULTS_LIMIT


def _optimization_events_preview(events: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], bool]:
    payload = [
        _jsonable(
            training_review_contracts.project_persisted_optimization_event(
                dict(item or {})
            )
        )
        for item in list(events or [])[:INLINE_OPTIMIZATION_EVENT_LIMIT]
    ]
    return payload, len(list(events or [])) > INLINE_OPTIMIZATION_EVENT_LIMIT


def _tagging_digest(payload: dict[str, Any] | None) -> training_review_contracts.TaggingDigestPayload:
    report = dict(payload or {})
    return cast(training_review_contracts.TaggingDigestPayload, {
        "primary_tag": str(report.get("primary_tag") or "unknown"),
        "confidence_score": float(report.get("confidence_score") or 0.0),
        "review_required": bool(report.get("review_required", False)),
        "reason_codes": [str(item) for item in list(report.get("reason_codes") or [])],
    })


def _persisted_tagging_digest(
    payload: dict[str, Any] | None,
) -> training_review_contracts.PersistedTaggingDigestPayload:
    report = dict(payload or {})
    digest = _tagging_digest(report)
    return cast(training_review_contracts.PersistedTaggingDigestPayload, {
        **digest,
        "contract_version": str(report.get("contract_version") or ""),
        "tag_family": str(report.get("tag_family") or ""),
        "normalized_tags": [str(item) for item in list(report.get("normalized_tags") or [])],
    })


def _validation_summary_digest(
    payload: dict[str, Any] | None,
) -> training_review_contracts.ValidationSummaryCompactPayload:
    summary = dict(payload or {})
    checks = list(summary.get("checks") or [])
    failed_checks = list(summary.get("failed_checks") or [])
    return cast(training_review_contracts.ValidationSummaryCompactPayload, {
        "contract_version": str(summary.get("contract_version") or ""),
        "validation_task_id": str(summary.get("validation_task_id") or ""),
        "status": str(summary.get("status") or ""),
        "shadow_mode": bool(summary.get("shadow_mode", False)),
        "review_required": bool(summary.get("review_required", False)),
        "confidence_score": float(summary.get("confidence_score") or 0.0),
        "reason_codes": [str(item) for item in list(summary.get("reason_codes") or [])],
        "check_count": len(checks),
        "failed_check_count": len(failed_checks),
    })


def _validation_report_summary(
    payload: dict[str, Any] | None,
) -> training_review_contracts.ValidationReportSummaryPayload:
    report = dict(payload or {})
    return cast(training_review_contracts.ValidationReportSummaryPayload, {
        "validation_task_id": str(report.get("validation_task_id") or ""),
        "shadow_mode": bool(report.get("shadow_mode", False)),
        "summary": _validation_summary_summary(
            dict(report.get("summary") or {}),
            include_raw_evidence=False,
        ),
        "market_tagging": _persisted_tagging_digest(dict(report.get("market_tagging") or {})),
        "failure_tagging": _persisted_tagging_digest(dict(report.get("failure_tagging") or {})),
        "validation_tagging": _persisted_tagging_digest(dict(report.get("validation_tagging") or {})),
    })


def _peer_comparison_digest(
    payload: dict[str, Any] | None,
) -> training_review_contracts.PeerComparisonCompactPayload:
    report = dict(payload or {})
    return cast(training_review_contracts.PeerComparisonCompactPayload, {
        "compared_market_tag": str(report.get("compared_market_tag") or ""),
        "comparable": bool(report.get("comparable", False)),
        "compared_count": int(report.get("compared_count") or 0),
        "dominant_peer": str(report.get("dominant_peer") or ""),
        "peer_dominated": bool(report.get("peer_dominated", False)),
        "candidate_outperformed_peers": bool(report.get("candidate_outperformed_peers", False)),
        "reason_codes": [str(item) for item in list(report.get("reason_codes") or [])],
    })


def _peer_comparison_summary(
    payload: dict[str, Any] | None,
) -> training_review_contracts.PersistedPeerComparisonPayload:
    report = dict(payload or {})
    ranked_peers = [
        cast(
            training_review_contracts.PeerComparisonPeerSummaryPayload,
            _dict_subset(dict(item or {}), ("manager_id", "market_tag", "score", "sample_count", "cycle_id")),
        )
        for item in list(report.get("ranked_peers") or [])[:5]
    ]
    return cast(training_review_contracts.PersistedPeerComparisonPayload, {
        **_peer_comparison_digest(report),
        "ranked_peers": ranked_peers,
    })


def _review_decision_digest(
    payload: dict[str, Any] | None,
) -> training_review_contracts.ReviewDecisionSummaryCompactPayload:
    report = dict(payload or {})
    return cast(training_review_contracts.ReviewDecisionSummaryCompactPayload, {
        "reasoning": _truncate_text(report.get("reasoning")),
        "analysis": _truncate_text(report.get("analysis")),
        "verdict": str(report.get("verdict") or ""),
        "subject_type": str(report.get("subject_type") or ""),
        "regime_summary": dict(report.get("regime_summary") or {}),
        "causal_diagnosis": dict(report.get("causal_diagnosis") or {}),
        "param_adjustments": dict(report.get("param_adjustments") or {}),
        "manager_budget_adjustments": dict(report.get("manager_budget_adjustments") or {}),
        "agent_weight_adjustments": dict(report.get("agent_weight_adjustments") or {}),
    })


def _similarity_summary_persisted(
    payload: dict[str, Any] | None,
) -> training_review_contracts.SimilaritySummaryPersistedPayload | None:
    similarity_summary = dict(payload or {})
    if not similarity_summary:
        return None
    matched_ids, matched_ids_truncated = _list_preview(
        list(similarity_summary.get("matched_cycle_ids") or []),
        limit=INLINE_SIMILAR_RESULTS_LIMIT,
    )
    return cast(training_review_contracts.SimilaritySummaryPersistedPayload, {
        "matched_cycle_ids": [int(item) for item in matched_ids],
        "matched_cycle_ids_truncated": matched_ids_truncated,
        "match_count": int(similarity_summary.get("match_count") or len(matched_ids)),
        "similarity_band": str(similarity_summary.get("similarity_band") or ""),
        "summary": _truncate_text(similarity_summary.get("summary")),
    })


def _review_decision_summary(
    payload: dict[str, Any] | None,
) -> training_review_contracts.PersistedReviewDecisionSummaryPayload:
    report = dict(payload or {})
    summary = cast(training_review_contracts.PersistedReviewDecisionSummaryPayload, {
        **_review_decision_digest(report),
    })
    similarity_summary = _similarity_summary_persisted(dict(report.get("similarity_summary") or {}))
    if similarity_summary:
        summary["similarity_summary"] = similarity_summary
    return summary


def _research_feedback_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    report = dict(payload or {})
    recommendation = dict(report.get("recommendation") or {})
    scope = dict(report.get("scope") or {})
    return {
        "sample_count": int(report.get("sample_count") or 0),
        "matched_case_count": int(report.get("matched_case_count") or 0),
        "recommendation": _dict_subset(recommendation, ("bias", "reason_codes", "summary")),
        "scope": _dict_subset(
            scope,
            ("effective_scope", "overall_sample_count", "regime_sample_count", "covered_regimes"),
        ),
    }


def _validation_checks_summary(
    payload: list[dict[str, Any]] | None,
) -> list[training_review_contracts.ValidationCheckSummaryPayload]:
    preview: list[dict[str, Any]] = []
    for item in list(payload or [])[:INLINE_SIMILAR_RESULTS_LIMIT]:
        preview.append(
            {
                "name": str(item.get("name") or ""),
                "passed": bool(item.get("passed", False)),
                "reason_code": str(item.get("reason_code") or ""),
                "actual": _compact_value(item.get("actual")),
                "threshold": _compact_value(item.get("threshold")),
            }
        )
    return cast(list[training_review_contracts.ValidationCheckSummaryPayload], preview)


def _validation_raw_evidence_summary(
    payload: dict[str, Any] | None,
) -> training_review_contracts.ValidationRawEvidenceSummaryPayload:
    evidence = dict(payload or {})
    cycle_result = dict(evidence.get("cycle_result") or {})
    return cast(training_review_contracts.ValidationRawEvidenceSummaryPayload, {
        "run_context": _run_context_validation_summary(dict(evidence.get("run_context") or {})),
        "review_result": _review_decision_summary(dict(evidence.get("review_result") or {})),
        "cycle_result": cast(training_review_contracts.ValidationRawEvidenceCycleResultPayload, {
            "return_pct": float(cycle_result.get("return_pct") or 0.0),
            "benchmark_passed": bool(cycle_result.get("benchmark_passed", False)),
            "strategy_scores": cast(dict[str, Any], _jsonable(dict(cycle_result.get("strategy_scores") or {}))),
            "ab_comparison": cast(dict[str, Any], _jsonable(dict(cycle_result.get("ab_comparison") or {}))),
            "research_feedback": cast(dict[str, Any], _research_feedback_summary(
                dict(cycle_result.get("research_feedback") or {})
            )),
        }),
    })


def _validation_summary_summary(
    payload: dict[str, Any] | None,
    *,
    include_raw_evidence: bool = True,
) -> training_review_contracts.PersistedValidationSummaryPayload:
    summary = dict(payload or {})
    digest = _validation_summary_digest(summary)
    result = cast(training_review_contracts.PersistedValidationSummaryPayload, {
        **digest,
        "validation_tags": [str(item) for item in list(summary.get("validation_tags") or [])],
        "summary": _truncate_text(summary.get("summary")),
        "checks": _validation_checks_summary(list(summary.get("checks") or [])),
        "failed_checks": _validation_checks_summary(list(summary.get("failed_checks") or [])),
    })
    if include_raw_evidence:
        result["raw_evidence"] = _validation_raw_evidence_summary(dict(summary.get("raw_evidence") or {}))
    return result


def _result_artifact_paths(output_dir: str | Path, cycle_id: int) -> dict[str, str]:
    root = _resolved_output_dir(output_dir)
    return {
        "validation_report_path": str(root / "validation" / f"cycle_{cycle_id}_validation.json"),
        "peer_comparison_report_path": str(root / "validation" / f"cycle_{cycle_id}_peer_comparison.json"),
        "judge_report_path": str(root / "validation" / f"cycle_{cycle_id}_judge.json"),
        "trade_history_path": str(root / "details" / f"cycle_{cycle_id}_trades.json"),
        "optimization_events_path": str(root / "optimization_events.jsonl"),
    }


def _resolved_output_dir(output_dir: str | Path) -> Path:
    return Path(output_dir).expanduser().resolve(strict=False)


def persist_cycle_proposal_bundle(
    controller: Any,
    *,
    cycle_id: int,
    execution_snapshot: dict[str, Any] | None = None,
    proposals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ensure_tracking_fields, build_tracking_summary = _resolve_suggestion_tracking_helpers()
    snapshot = deepcopy(dict(execution_snapshot or {}))
    proposal_items = [
        ensure_tracking_fields(
            deepcopy(dict(item or {})),
            default_cycle_id=int(cycle_id),
        )
        for item in list(
            proposals
            if proposals is not None
            else getattr(controller, "current_cycle_learning_proposals", [])
            or []
        )
    ]
    model_name = str(
        snapshot.get("model_name")
        or getattr(controller, "model_name", "")
        or ""
    )
    active_runtime_config_ref = normalize_config_ref(
        snapshot.get("active_runtime_config_ref")
        or snapshot.get("active_config_ref")
        or getattr(controller, "model_config_path", "")
        or ""
    )
    active_version_id = str(
        snapshot.get("active_version_id")
        or snapshot.get("active_runtime_version_id")
        or ""
    )
    runtime_fingerprint = str(
        snapshot.get("runtime_fingerprint")
        or snapshot.get("active_runtime_fingerprint")
        or ""
    )
    proposal_ids = [
        str(dict(item or {}).get("proposal_id") or "")
        for item in proposal_items
    ]
    bundle_signature = {
        "cycle_id": int(cycle_id),
        "model_name": model_name,
        "active_runtime_config_ref": active_runtime_config_ref,
        "active_version_id": active_version_id,
        "runtime_fingerprint": runtime_fingerprint,
        "proposal_ids": proposal_ids,
    }
    proposal_bundle_id = f"proposal_bundle_{int(cycle_id):04d}_{_stable_hash(bundle_signature)[:8]}"
    suggestion_tracking_summary = build_tracking_summary(proposal_items)
    payload = {
        "schema_version": "training.proposal_bundle.v1",
        "proposal_bundle_id": proposal_bundle_id,
        "cycle_id": int(cycle_id),
        "created_at": datetime.now().isoformat(),
        "model_name": model_name,
        "active_runtime_config_ref": active_runtime_config_ref,
        "active_version_id": active_version_id,
        "runtime_fingerprint": runtime_fingerprint,
        "execution_snapshot": snapshot,
        "proposal_count": len(proposal_items),
        "proposal_ids": proposal_ids,
        "proposals": proposal_items,
        "suggestion_tracking_summary": suggestion_tracking_summary,
    }
    path = _proposal_store_dir(controller) / f"cycle_{int(cycle_id):04d}_{proposal_bundle_id}.json"
    write_json_boundary(path, payload)
    payload["bundle_path"] = str(path)
    setattr(controller, "last_cycle_proposal_bundle", deepcopy(payload))
    return payload


def load_cycle_proposal_bundle(bundle_path: str | Path) -> dict[str, Any]:
    ensure_tracking_fields, build_tracking_summary = _resolve_suggestion_tracking_helpers()
    path = Path(bundle_path).expanduser().resolve(strict=False)
    payload = dict(json.loads(path.read_text(encoding="utf-8")) or {})
    cycle_id = int(payload.get("cycle_id") or 0)
    proposals = [
        ensure_tracking_fields(
            deepcopy(dict(item or {})),
            default_cycle_id=cycle_id,
        )
        for item in list(payload.get("proposals") or [])
    ]
    payload["proposals"] = proposals
    payload["proposal_count"] = len(proposals)
    payload["proposal_ids"] = [
        str(dict(item).get("proposal_id") or "")
        for item in proposals
    ]
    payload["suggestion_tracking_summary"] = build_tracking_summary(proposals)
    payload["bundle_path"] = str(path)
    return payload


def list_cycle_proposal_bundles(base: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
    items = [
        load_cycle_proposal_bundle(path)
        for path in sorted(_proposal_store_dir(base).glob("cycle_*.json"))
    ]
    if limit is not None:
        return items[-max(0, int(limit)) :]
    return items


def update_cycle_proposal_bundle(
    controller: Any | None,
    *,
    bundle_path: str | Path,
    proposals: list[dict[str, Any]] | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_tracking_fields, build_tracking_summary = _resolve_suggestion_tracking_helpers()
    payload = load_cycle_proposal_bundle(bundle_path)
    cycle_id = int(payload.get("cycle_id") or 0)
    if proposals is not None:
        payload["proposals"] = [
            ensure_tracking_fields(
                deepcopy(dict(item or {})),
                default_cycle_id=cycle_id,
            )
            for item in list(proposals or [])
        ]
        payload["proposal_count"] = len(list(payload.get("proposals") or []))
        payload["proposal_ids"] = [
            str(dict(item).get("proposal_id") or "")
            for item in list(payload.get("proposals") or [])
        ]
    payload["suggestion_tracking_summary"] = build_tracking_summary(
        list(payload.get("proposals") or [])
    )
    for key, value in dict(extra_fields or {}).items():
        payload[key] = deepcopy(value)

    path = Path(bundle_path).expanduser().resolve(strict=False)
    persisted = dict(payload)
    persisted.pop("bundle_path", None)
    write_json_boundary(path, persisted)
    payload["bundle_path"] = str(path)
    if controller is not None:
        setattr(controller, "last_cycle_proposal_bundle", deepcopy(payload))
    return payload


def _stage_snapshot_refs(
    payload: dict[str, Any] | None,
) -> training_review_contracts.StageSnapshotRefsPayload:
    stage_names = sorted(
        str(key).strip()
        for key, value in dict(payload or {}).items()
        if str(key).strip() and isinstance(value, dict)
    )
    return cast(training_review_contracts.StageSnapshotRefsPayload, {
        "stage_names": stage_names,
        "count": len(stage_names),
    })


def _project_stage_snapshot_map(
    payload: dict[str, Any] | None,
    *,
    projectors: dict[str, Any],
) -> dict[str, Any]:
    snapshots = dict(payload or {})
    projected: dict[str, Any] = {}
    for stage_name, projector in projectors.items():
        snapshot = snapshots.get(stage_name)
        if isinstance(snapshot, dict):
            projected[stage_name] = projector(dict(snapshot or {}))
    return projected


def _contract_stage_snapshot_summary_fields(
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    snapshots = dict(payload or {})
    summary: dict[str, Any] = {
        "contract_stage_refs": _stage_snapshot_refs(snapshots),
    }
    contract_stage_snapshots = _bounded_contract_stage_snapshots_summary(snapshots)
    if contract_stage_snapshots:
        summary["contract_stage_snapshots"] = contract_stage_snapshots
    return summary


def _portfolio_plan_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    plan = dict(payload or {})
    metadata = dict(plan.get("metadata") or {})
    return _summary_or_empty(
        {
            "active_manager_ids": _jsonable(list(plan.get("active_manager_ids") or [])),
            "budget_weights": _jsonable(dict(plan.get("budget_weights") or {})),
            "dominant_manager_id": str(
                plan.get("dominant_manager_id")
                or metadata.get("dominant_manager_id")
                or ""
            ),
            "dominant_manager_config": str(metadata.get("dominant_manager_config") or ""),
            "manager_count": int(plan.get("manager_count") or len(list(plan.get("active_manager_ids") or []))),
        }
    )


def _manager_results_summary(payload: list[dict[str, Any]] | None) -> dict[str, Any]:
    preview, truncated = _list_preview(list(payload or []), limit=5)
    return {
        "count": len(list(payload or [])),
        "truncated": truncated,
        "items": [
            _dict_subset(
                dict(item or {}),
                (
                    "manager_id",
                    "manager_config_ref",
                    "score",
                    "return_pct",
                    "benchmark_passed",
                    "selection_mode",
                    "subject_type",
                ),
            )
            for item in preview
        ],
    }


def _promotion_decision_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    decision = dict(payload or {})
    return _summary_or_empty(
        {
            "status": str(decision.get("status") or ""),
            "applied_to_active": bool(decision.get("applied_to_active", False)),
            "gate_status": str(decision.get("gate_status") or ""),
            "active_runtime_config_ref": str(decision.get("active_runtime_config_ref") or ""),
            "candidate_runtime_config_ref": str(decision.get("candidate_runtime_config_ref") or ""),
        }
    )


def _ab_comparison_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    comparison = dict(payload or {})
    summary = dict(comparison.get("comparison") or {})
    return _summary_or_empty(
        {
            "winner": str(summary.get("winner") or ""),
            "return_lift_pct": summary.get("return_lift_pct"),
            "benchmark_lift_pct": summary.get("benchmark_lift_pct"),
            "sample_count": summary.get("sample_count"),
            "status": str(summary.get("status") or ""),
        }
    )


def _governance_guardrail_summary(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        items = list(payload.items())
    else:
        items = [
            (str(dict(item or {}).get("name") or f"check_{idx}"), dict(item or {}))
            for idx, item in enumerate(list(payload or []))
            if isinstance(item, dict)
        ]
    return _summary_or_empty(
        {
            str(key): _summary_or_empty(
                {
                    "passed": bool(dict(value or {}).get("passed", False)),
                    "reason_codes": _jsonable(list(dict(value or {}).get("reason_codes") or [])),
                    "status": str(dict(value or {}).get("status") or ""),
                    "summary": _truncate_text(dict(value or {}).get("summary")),
                }
            )
            for key, value in items
            if isinstance(value, dict)
        }
    )


def _governance_decision_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    decision = normalize_governance_decision(dict(payload or {}))
    metadata = dict(decision.get("metadata") or {})
    evidence = dict(decision.get("evidence") or {})
    return _summary_or_empty(
        {
            "dominant_manager_id": str(decision.get("dominant_manager_id") or ""),
            "active_manager_ids": _jsonable(list(decision.get("active_manager_ids") or [])),
            "manager_budget_weights": _jsonable(dict(decision.get("manager_budget_weights") or {})),
            "regime": str(decision.get("regime") or ""),
            "regime_source": str(decision.get("regime_source") or ""),
            "regime_confidence": decision.get("regime_confidence"),
            "decision_source": str(decision.get("decision_source") or ""),
            "decision_confidence": decision.get("decision_confidence"),
            "cash_reserve_hint": decision.get("cash_reserve_hint"),
            "allocation_plan": _portfolio_plan_summary(dict(decision.get("allocation_plan") or {})),
            "portfolio_constraints": _dict_subset(
                dict(decision.get("portfolio_constraints") or {}),
                ("cash_reserve", "max_active_managers", "max_position_size"),
            ),
            "guardrail_checks": _governance_guardrail_summary(
                decision.get("guardrail_checks")
            ),
            "reasoning": _truncate_text(decision.get("reasoning")),
            "metadata": _dict_subset(
                metadata,
                (
                    "manager_id",
                    "manager_config_ref",
                    "active_runtime_config_ref",
                    "candidate_runtime_config_ref",
                    "subject_type",
                    "selection_mode",
                ),
            ),
            "evidence": _summary_or_empty(
                {
                    "has_research_feedback": bool(evidence.get("research_feedback")),
                    "has_ab_comparison": bool(evidence.get("ab_comparison")),
                    "has_peer_comparison": bool(evidence.get("peer_comparison")),
                    "benchmark_passed": evidence.get("benchmark_passed"),
                }
            ),
        }
    )


def _promotion_record_summary(
    payload: dict[str, Any] | None,
) -> training_review_contracts.PromotionRecordPersistedPayload:
    record = dict(payload or {})
    return cast(training_review_contracts.PromotionRecordPersistedPayload, _summary_or_empty(
        {
            "status": str(record.get("status") or ""),
            "gate_status": str(record.get("gate_status") or ""),
            "applied_to_active": bool(record.get("applied_to_active", False)),
            "review_applied": bool(record.get("review_applied", False)),
            "active_runtime_config_ref": str(record.get("active_runtime_config_ref") or ""),
            "candidate_runtime_config_ref": str(record.get("candidate_runtime_config_ref") or ""),
            "applied_runtime_config_ref": str(record.get("applied_runtime_config_ref") or ""),
        }
    ))


def _lineage_record_summary(
    payload: dict[str, Any] | None,
) -> training_review_contracts.LineageRecordPersistedPayload:
    record = dict(payload or {})
    return cast(training_review_contracts.LineageRecordPersistedPayload, _summary_or_empty(
        {
            "lineage_status": str(record.get("lineage_status") or ""),
            "active_runtime_config_ref": str(record.get("active_runtime_config_ref") or ""),
            "candidate_runtime_config_ref": str(record.get("candidate_runtime_config_ref") or ""),
            "parent_cycle_id": record.get("parent_cycle_id"),
            "candidate_cycle_id": record.get("candidate_cycle_id"),
            "generation": record.get("generation"),
        }
    ))


def _judge_report_summary(
    payload: dict[str, Any] | None,
) -> training_review_contracts.JudgeReportSummaryPayload:
    report = dict(payload or {})
    next_actions, next_actions_truncated = _list_preview(
        list(report.get("next_actions") or []),
        limit=5,
    )
    return cast(training_review_contracts.JudgeReportSummaryPayload, _summary_or_empty(
        {
            "decision": str(report.get("decision") or ""),
            "validation_status": str(report.get("validation_status") or ""),
            "reason_codes": _jsonable(list(report.get("reason_codes") or [])),
            "summary": _truncate_text(report.get("summary")),
            "actionable": bool(report.get("actionable", False)),
            "review_required": bool(report.get("review_required", False)),
            "shadow_mode": bool(report.get("shadow_mode", False)),
            "next_actions": [_truncate_text(item, limit=200) for item in next_actions],
            "next_actions_truncated": next_actions_truncated,
        }
    ))
 

def _realism_metrics_mix_summary(payload: dict[str, Any] | None) -> dict[str, float]:
    mix: dict[str, float] = {}
    for key, value in dict(payload or {}).items():
        number = _finite_float(value)
        if number is not None:
            mix[str(key)] = number
    return mix


def _realism_metrics_summary(
    payload: dict[str, Any] | None,
) -> training_review_contracts.RealismMetricsSummaryPayload:
    metrics = dict(payload or {})
    summary: dict[str, Any] = {}
    if "trade_record_count" in metrics:
        summary["trade_record_count"] = int(metrics.get("trade_record_count") or 0)
    if "selection_mode" in metrics:
        summary["selection_mode"] = str(metrics.get("selection_mode") or "")
    if "optimization_event_count" in metrics:
        summary["optimization_event_count"] = int(metrics.get("optimization_event_count") or 0)
    if "avg_trade_amount" in metrics:
        summary["avg_trade_amount"] = _finite_float(metrics.get("avg_trade_amount")) or 0.0
    if "avg_turnover_rate" in metrics:
        summary["avg_turnover_rate"] = _finite_float(metrics.get("avg_turnover_rate")) or 0.0
    if "high_turnover_trade_count" in metrics:
        summary["high_turnover_trade_count"] = int(metrics.get("high_turnover_trade_count") or 0)
    if "avg_holding_days" in metrics:
        summary["avg_holding_days"] = _finite_float(metrics.get("avg_holding_days")) or 0.0
    source_mix = _realism_metrics_mix_summary(dict(metrics.get("source_mix") or {}))
    exit_trigger_mix = _realism_metrics_mix_summary(dict(metrics.get("exit_trigger_mix") or {}))
    if source_mix:
        summary["source_mix"] = source_mix
    if exit_trigger_mix:
        summary["exit_trigger_mix"] = exit_trigger_mix
    return cast(training_review_contracts.RealismMetricsSummaryPayload, summary)


def _run_context_validation_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    run_context = dict(payload or {})
    return _summary_or_empty(
        {
            "basis_stage": str(run_context.get("basis_stage") or ""),
            "subject_type": str(run_context.get("subject_type") or ""),
            "shadow_mode": bool(run_context.get("shadow_mode", False)),
            "active_runtime_config_ref": str(run_context.get("active_runtime_config_ref") or ""),
            "candidate_runtime_config_ref": str(run_context.get("candidate_runtime_config_ref") or ""),
            "manager_id": str(run_context.get("manager_id") or ""),
            "manager_config_ref": str(run_context.get("manager_config_ref") or ""),
            "promotion_decision": _promotion_decision_summary(
                dict(run_context.get("promotion_decision") or {})
            ),
        }
    )


def _project_simulation_stage_snapshot(
    snapshot: dict[str, Any] | None,
) -> training_review_contracts.SimulationStageSnapshotPersistedPayload:
    payload = dict(snapshot or {})
    return cast(training_review_contracts.SimulationStageSnapshotPersistedPayload, {
        "stage": "simulation",
        "cycle_id": int(payload.get("cycle_id") or 0),
        "cutoff_date": str(payload.get("cutoff_date") or ""),
        "regime": str(payload.get("regime") or "unknown"),
        "selection_mode": str(payload.get("selection_mode") or ""),
        "selected_stocks": [str(item) for item in list(payload.get("selected_stocks") or [])],
        "return_pct": float(payload.get("return_pct") or 0.0),
        "benchmark_passed": bool(payload.get("benchmark_passed", False)),
        "benchmark_strict_passed": bool(payload.get("benchmark_strict_passed", False)),
    })


def _project_review_stage_snapshot(
    snapshot: dict[str, Any] | None,
) -> training_review_contracts.ReviewStageSnapshotPersistedPayload:
    payload = dict(snapshot or {})
    projected = cast(training_review_contracts.ReviewStageSnapshotPersistedPayload, {
        "stage": "review",
        "cycle_id": int(payload.get("cycle_id") or 0),
        "analysis": _truncate_text(payload.get("analysis")),
        "review_applied": bool(payload.get("review_applied", False)),
    })
    similarity_summary = _similarity_summary_persisted(dict(payload.get("similarity_summary") or {}))
    if similarity_summary:
        projected["similarity_summary"] = similarity_summary
    manager_review_report = training_review_contracts.project_persisted_manager_review_digest(
        dict(payload.get("manager_review_report") or {})
    )
    allocation_review_report = (
        training_review_contracts.project_persisted_allocation_review_digest(
            dict(payload.get("allocation_review_report") or {})
        )
    )
    if manager_review_report:
        projected["manager_review_report"] = manager_review_report
    if allocation_review_report:
        projected["allocation_review_report"] = allocation_review_report
    return projected


def _project_validation_stage_snapshot(
    snapshot: dict[str, Any] | None,
) -> training_review_contracts.ValidationStageSnapshotPersistedPayload:
    payload = dict(snapshot or {})
    return cast(training_review_contracts.ValidationStageSnapshotPersistedPayload, {
        "stage": "validation",
        "cycle_id": int(payload.get("cycle_id") or 0),
        "validation_task_id": str(payload.get("validation_task_id") or ""),
        "shadow_mode": bool(payload.get("shadow_mode", False)),
        "validation_summary": _validation_summary_summary(
            dict(payload.get("validation_summary") or {}),
            include_raw_evidence=False,
        ),
        "market_tagging": _persisted_tagging_digest(dict(payload.get("market_tagging") or {})),
        "failure_tagging": _persisted_tagging_digest(dict(payload.get("failure_tagging") or {})),
        "validation_tagging": _persisted_tagging_digest(dict(payload.get("validation_tagging") or {})),
        "judge_report": _judge_report_summary(dict(payload.get("judge_report") or {})),
    })


def _project_outcome_stage_snapshot(
    snapshot: dict[str, Any] | None,
) -> training_review_contracts.OutcomeStageSnapshotPersistedPayload:
    payload = dict(snapshot or {})
    projected = cast(training_review_contracts.OutcomeStageSnapshotPersistedPayload, {
        "stage": "outcome",
        "cycle_id": int(payload.get("cycle_id") or 0),
        "promotion_record": _promotion_record_summary(dict(payload.get("promotion_record") or {})),
        "lineage_record": _lineage_record_summary(dict(payload.get("lineage_record") or {})),
    })
    realism_metrics = _realism_metrics_summary(dict(payload.get("realism_metrics") or {}))
    if realism_metrics:
        projected["realism_metrics"] = realism_metrics
    return projected


def _project_stage_snapshot(
    snapshot: dict[str, Any] | None,
) -> training_review_contracts.StageSnapshotPersistedPayload:
    payload = dict(snapshot or {})
    stage = str(payload.get("stage") or "")
    if stage == "simulation":
        return _project_simulation_stage_snapshot(payload)
    if stage == "review":
        return _project_review_stage_snapshot(payload)
    if stage == "validation":
        return _project_validation_stage_snapshot(payload)
    if stage == "outcome":
        return _project_outcome_stage_snapshot(payload)
    return cast(training_review_contracts.StageSnapshotPersistedPayload, {
        "stage": stage,
        "cycle_id": int(payload.get("cycle_id") or 0),
    })


def _project_simulation_contract_stage_snapshot(
    snapshot: dict[str, Any] | None,
) -> training_review_contracts.SimulationContractStageSnapshotSummaryPayload:
    payload = dict(snapshot or {})
    return cast(training_review_contracts.SimulationContractStageSnapshotSummaryPayload, {
        "stage": "simulation",
        "cycle_id": int(payload.get("cycle_id") or 0),
        "cutoff_date": str(payload.get("cutoff_date") or ""),
        "regime": str(payload.get("regime") or "unknown"),
        "selection_mode": str(payload.get("selection_mode") or ""),
        "return_pct": float(payload.get("return_pct") or 0.0),
        "benchmark_passed": bool(payload.get("benchmark_passed", False)),
        "benchmark_strict_passed": bool(payload.get("benchmark_strict_passed", False)),
    })


def _project_review_contract_stage_snapshot(
    snapshot: dict[str, Any] | None,
) -> training_review_contracts.ReviewContractStageSnapshotSummaryPayload:
    payload = dict(snapshot or {})
    similarity_summary = dict(payload.get("similarity_summary") or {})
    return cast(training_review_contracts.ReviewContractStageSnapshotSummaryPayload, {
        "stage": "review",
        "cycle_id": int(payload.get("cycle_id") or 0),
        "analysis": _truncate_text(payload.get("analysis"), limit=240),
        "similarity_summary": cast(training_review_contracts.SimilaritySummaryCompactPayload, {
            "match_count": int(
                similarity_summary.get("match_count")
                or len(list(similarity_summary.get("matched_cycle_ids") or []))
            ),
            "similarity_band": str(similarity_summary.get("similarity_band") or ""),
            "summary": _truncate_text(similarity_summary.get("summary"), limit=200),
        }),
    })


def _project_validation_contract_stage_snapshot(
    snapshot: dict[str, Any] | None,
) -> training_review_contracts.ValidationContractStageSnapshotSummaryPayload:
    payload = dict(snapshot or {})
    validation_summary = dict(payload.get("validation_summary") or {})
    return cast(training_review_contracts.ValidationContractStageSnapshotSummaryPayload, {
        "stage": "validation",
        "cycle_id": int(payload.get("cycle_id") or 0),
        "validation_task_id": str(payload.get("validation_task_id") or ""),
        "shadow_mode": bool(payload.get("shadow_mode", False)),
        "validation_summary": cast(training_review_contracts.ValidationSummaryCompactPayload, {
            **_validation_summary_digest(validation_summary),
        }),
        "judge_decision": str(dict(payload.get("judge_report") or {}).get("decision") or ""),
    })


def _project_outcome_contract_stage_snapshot(
    snapshot: dict[str, Any] | None,
) -> training_review_contracts.OutcomeContractStageSnapshotSummaryPayload:
    payload = dict(snapshot or {})
    promotion_record = dict(payload.get("promotion_record") or {})
    lineage_record = dict(payload.get("lineage_record") or {})
    return cast(training_review_contracts.OutcomeContractStageSnapshotSummaryPayload, {
        "stage": "outcome",
        "cycle_id": int(payload.get("cycle_id") or 0),
        "promotion_record": cast(training_review_contracts.PromotionRecordCompactPayload, {
            "status": str(promotion_record.get("status") or ""),
            "gate_status": str(promotion_record.get("gate_status") or ""),
            "applied_to_active": bool(promotion_record.get("applied_to_active", False)),
        }),
        "lineage_record": cast(training_review_contracts.LineageRecordCompactPayload, {
            "lineage_status": str(lineage_record.get("lineage_status") or ""),
        }),
    })


def _project_contract_stage_snapshots(
    payload: dict[str, Any] | None,
) -> training_review_contracts.ContractStageSnapshotsSummaryPayload:
    return cast(training_review_contracts.ContractStageSnapshotsSummaryPayload, _project_stage_snapshot_map(
        payload,
        projectors={
            "simulation": _project_simulation_contract_stage_snapshot,
            "review": _project_review_contract_stage_snapshot,
            "validation": _project_validation_contract_stage_snapshot,
            "outcome": _project_outcome_contract_stage_snapshot,
        },
    ))


def _bounded_contract_stage_snapshots_summary(
    payload: dict[str, Any] | None,
    *,
    max_source_bytes: int = 64 * 1024,
    max_bytes: int = 2048,
) -> training_review_contracts.ContractStageSnapshotsSummaryPayload:
    normalized = _jsonable(dict(payload or {}))
    if not normalized:
        return cast(training_review_contracts.ContractStageSnapshotsSummaryPayload, {})
    source_bytes = json.dumps(normalized, ensure_ascii=False).encode("utf-8")
    if len(source_bytes) > max_source_bytes:
        return cast(training_review_contracts.ContractStageSnapshotsSummaryPayload, {})
    projected = _project_contract_stage_snapshots(normalized)
    if not projected:
        return cast(training_review_contracts.ContractStageSnapshotsSummaryPayload, {})
    encoded = json.dumps(projected, ensure_ascii=False).encode("utf-8")
    if len(encoded) > max_bytes:
        return cast(training_review_contracts.ContractStageSnapshotsSummaryPayload, {})
    return projected


def _proposal_bundle_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    bundle = dict(payload or {})
    if not bundle:
        return {}
    proposal_ids_preview, proposal_ids_truncated = _list_preview(
        list(bundle.get("proposal_ids") or []),
        limit=10,
    )
    summary = _summary_or_empty(
        {
            "schema_version": str(bundle.get("schema_version") or ""),
            "proposal_bundle_id": str(bundle.get("proposal_bundle_id") or ""),
            "cycle_id": int(bundle.get("cycle_id") or 0) if bundle.get("cycle_id") is not None else 0,
            "bundle_path": str(bundle.get("bundle_path") or ""),
            "proposal_count": int(
                bundle.get("proposal_count")
                or len(list(bundle.get("proposals") or []))
            ),
            "proposal_ids": [str(item) for item in proposal_ids_preview],
            "proposal_ids_truncated": proposal_ids_truncated,
            "suggestion_tracking_summary": _jsonable(
                dict(bundle.get("suggestion_tracking_summary") or {})
            ),
        }
    )
    return summary


def _execution_snapshot_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = dict(payload or {})
    summary = {
        "basis_stage": str(snapshot.get("basis_stage") or ""),
        "cycle_id": int(snapshot.get("cycle_id") or 0),
        "active_runtime_config_ref": str(snapshot.get("active_runtime_config_ref") or ""),
        "manager_config_ref": str(snapshot.get("manager_config_ref") or ""),
        "dominant_manager_id": str(snapshot.get("dominant_manager_id") or snapshot.get("manager_id") or ""),
        "subject_type": str(snapshot.get("subject_type") or ""),
        "runtime_overrides": _jsonable(dict(snapshot.get("runtime_overrides") or {})),
        "execution_defaults": _jsonable(dict(snapshot.get("execution_defaults") or {})),
        "portfolio_plan": _portfolio_plan_summary(dict(snapshot.get("portfolio_plan") or {})),
        "manager_results": _manager_results_summary(list(snapshot.get("manager_results") or [])),
        "proposal_bundle": _proposal_bundle_summary(
            dict(snapshot.get("proposal_bundle") or {})
        ),
        **_contract_stage_snapshot_summary_fields(
            dict(snapshot.get("contract_stage_snapshots") or {})
        ),
    }
    return summary


def _run_context_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    run_context = dict(payload or {})
    summary = {
        "basis_stage": str(run_context.get("basis_stage") or ""),
        "subject_type": str(run_context.get("subject_type") or ""),
        "shadow_mode": bool(run_context.get("shadow_mode", False)),
        "active_runtime_config_ref": str(run_context.get("active_runtime_config_ref") or ""),
        "candidate_runtime_config_ref": str(run_context.get("candidate_runtime_config_ref") or ""),
        "manager_id": str(run_context.get("manager_id") or ""),
        "manager_config_ref": str(run_context.get("manager_config_ref") or ""),
        "runtime_overrides": _jsonable(dict(run_context.get("runtime_overrides") or {})),
        "review_basis_window": _jsonable(dict(run_context.get("review_basis_window") or {})),
        "fitness_source_cycles": _jsonable(list(run_context.get("fitness_source_cycles") or [])),
        "proposal_bundle": _proposal_bundle_summary(
            dict(run_context.get("proposal_bundle") or {})
        ),
        "promotion_decision": _promotion_decision_summary(
            dict(run_context.get("promotion_decision") or {})
        ),
        "ab_comparison": _ab_comparison_summary(dict(run_context.get("ab_comparison") or {})),
        **_contract_stage_snapshot_summary_fields(
            dict(run_context.get("contract_stage_snapshots") or {})
        ),
    }
    return summary


def _stage_snapshots_summary(
    payload: dict[str, Any] | None,
) -> training_review_contracts.StageSnapshotsPersistedPayload:
    return cast(training_review_contracts.StageSnapshotsPersistedPayload, _project_stage_snapshot_map(
        payload,
        projectors={
            "simulation": _project_simulation_stage_snapshot,
            "review": _project_review_stage_snapshot,
            "validation": _project_validation_stage_snapshot,
            "outcome": _project_outcome_stage_snapshot,
        },
    ))


def _result_stage_snapshots_summary(result: Any) -> training_review_contracts.StageSnapshotsPersistedPayload:
    return _stage_snapshots_summary(dict(getattr(result, "stage_snapshots", {}) or {}))


def _result_compatibility_fields_summary(result: Any) -> dict[str, Any]:
    return _jsonable(dict(getattr(result, "compatibility_fields", {}) or {}))


def _result_execution_defaults_summary(result: Any) -> Any:
    return _jsonable(
        execution_defaults_payload(
            normalize_governance_decision(dict(getattr(result, "governance_decision", {}) or {})),
            portfolio_plan=dict(getattr(result, "portfolio_plan", {}) or {}),
            manager_results=list(getattr(result, "manager_results", []) or []),
            execution_snapshot=dict(getattr(result, "execution_snapshot", {}) or {}),
            fallback=dict(getattr(result, "execution_defaults", {}) or {}),
        )
    )


def write_trade_history_artifact(
    output_dir: str | Path,
    *,
    cycle_id: int,
    trade_history: list[dict[str, Any]] | None,
) -> Path | None:
    trades = [_jsonable(dict(item or {})) for item in list(trade_history or [])]
    if not trades:
        return None
    path = _resolved_output_dir(output_dir) / "details" / f"cycle_{cycle_id}_trades.json"
    write_json_boundary(path, {"cycle_id": int(cycle_id), "trades": trades})
    return path


def _scoring_mutation_summary(optimization_events: list[dict[str, Any]] | None) -> tuple[int, list[str]]:
    scoring_changed_keys: list[str] = []
    scoring_mutation_count = 0
    for event in list(optimization_events or []):
        runtime_payload = dict(event.get("runtime_config_mutation_payload") or {})
        skipped_payload = dict(event.get("runtime_config_mutation_skipped_payload") or {})
        scoring = dict(
            runtime_payload.get("scoring_adjustments")
            or skipped_payload.get("scoring_adjustments")
            or {}
        )
        if not scoring:
            applied = dict(event.get("applied_change") or {})
            scoring = dict(applied.get("scoring") or {})
        if not scoring:
            continue
        scoring_mutation_count += 1
        for section_name, section_values in scoring.items():
            if isinstance(section_values, dict):
                for key in section_values.keys():
                    scoring_changed_keys.append(f"{section_name}.{key}")
    return scoring_mutation_count, sorted(set(scoring_changed_keys))


def build_cycle_result_persistence_payload(controller: Any, result: Any) -> dict[str, Any]:
    scoring_mutation_count, scoring_changed_keys = _scoring_mutation_summary(
        list(getattr(result, "optimization_events", []) or [])
    )
    trades_preview, trades_truncated = _trade_preview(list(getattr(result, "trade_history", []) or []))
    similar_results_preview, similar_results_truncated = _similar_results_preview(
        list(getattr(result, "similar_results", []) or [])
    )
    optimization_events_preview, optimization_events_truncated = _optimization_events_preview(
        list(getattr(result, "optimization_events", []) or [])
    )
    proposal_bundle = dict(getattr(result, "proposal_bundle", {}) or {})
    if not proposal_bundle:
        proposal_bundle = dict(getattr(result, "run_context", {}) or {}).get("proposal_bundle") or {}
    proposal_bundle_summary = _proposal_bundle_summary(dict(proposal_bundle or {}))
    execution_snapshot = dict(result.execution_snapshot or {})
    run_context = dict(result.run_context or {})
    manager_id = str(
        run_context.get("manager_id")
        or execution_snapshot.get("manager_id")
        or getattr(result, "dominant_manager_id", "")
        or ""
    )
    manager_config_ref = str(
        run_context.get("manager_config_ref")
        or execution_snapshot.get("manager_config_ref")
        or ""
    )
    active_runtime_config_ref = str(
        run_context.get("active_runtime_config_ref")
        or execution_snapshot.get("active_runtime_config_ref")
        or manager_config_ref
        or ""
    )
    artifact_paths = _result_artifact_paths(
        getattr(controller, "output_dir", ""),
        int(getattr(result, "cycle_id", 0) or 0),
    )
    data = {
        "cycle_id": result.cycle_id,
        "cutoff_date": result.cutoff_date,
        "selected_stocks": result.selected_stocks,
        "initial_capital": result.initial_capital,
        "final_value": result.final_value,
        "return_pct": result.return_pct,
        "is_profit": _bool(result.is_profit),
        "params": result.params,
        "trade_count": len(result.trade_history),
        "trades": trades_preview,
        "trades_truncated": trades_truncated,
        "analysis": result.analysis,
        "data_mode": result.data_mode,
        "requested_data_mode": result.requested_data_mode,
        "effective_data_mode": result.effective_data_mode,
        "llm_mode": result.llm_mode,
        "degraded": _bool(result.degraded),
        "degrade_reason": result.degrade_reason,
        "selection_mode": result.selection_mode,
        "manager_id": manager_id,
        "manager_config_ref": manager_config_ref,
        "active_runtime_config_ref": active_runtime_config_ref,
        "agent_used": _bool(result.agent_used),
        "llm_used": _bool(result.llm_used),
        "benchmark_passed": _bool(result.benchmark_passed),
        "strategy_scores": _jsonable(dict(result.strategy_scores or {})),
        "review_applied": _bool(result.review_applied),
        "config_snapshot_path": result.config_snapshot_path,
        "optimization_event_count": len(list(getattr(result, "optimization_events", []) or [])),
        "optimization_events": optimization_events_preview,
        "optimization_events_truncated": optimization_events_truncated,
        "audit_tags": _jsonable(
            {
                key: _bool(value) if isinstance(value, (bool, np.bool_)) else value
                for key, value in result.audit_tags.items()
            }
        ),
        "execution_defaults": _result_execution_defaults_summary(result),
        "governance_decision": _governance_decision_summary(
            dict(getattr(result, "governance_decision", {}) or {})
        ),
        "allocation_plan": _jsonable(
            (getattr(result, "governance_decision", {}) or {}).get("allocation_plan")
            or getattr(controller, "last_allocation_plan", {})
            or {}
        ),
        "research_feedback": _research_feedback_summary(dict(result.research_feedback or {})),
        "research_artifacts": _jsonable(dict(result.research_artifacts or {})),
        "ab_comparison": _jsonable(dict(result.ab_comparison or {})),
        "experiment_spec": _jsonable(dict(result.experiment_spec or {})),
        "execution_snapshot": _execution_snapshot_summary(execution_snapshot),
        "run_context": _run_context_summary(run_context),
        "promotion_record": _promotion_record_summary(dict(result.promotion_record or {})),
        "lineage_record": _lineage_record_summary(dict(result.lineage_record or {})),
        "proposal_bundle": proposal_bundle_summary,
        "manager_results": _manager_results_summary(list(getattr(result, "manager_results", []) or [])),
        "portfolio_plan": _portfolio_plan_summary(dict(getattr(result, "portfolio_plan", {}) or {})),
        "portfolio_attribution": _jsonable(dict(getattr(result, "portfolio_attribution", {}) or {})),
        "manager_review_report": _jsonable(
            training_review_contracts.project_persisted_manager_review_digest(
                dict(getattr(result, "manager_review_report", {}) or {})
            )
        ),
        "allocation_review_report": _jsonable(
            training_review_contracts.project_persisted_allocation_review_digest(
                dict(getattr(result, "allocation_review_report", {}) or {})
            )
        ),
        "dominant_manager_id": str(getattr(result, "dominant_manager_id", "") or ""),
        "compatibility_fields": _result_compatibility_fields_summary(result),
        "review_decision": _review_decision_summary(dict(result.review_decision or {})),
        "causal_diagnosis": _jsonable(dict(result.causal_diagnosis or {})),
        "similarity_summary": _jsonable(dict(result.similarity_summary or {})),
        "similar_results": similar_results_preview,
        "similar_results_truncated": similar_results_truncated,
        "realism_metrics": _jsonable(dict(result.realism_metrics or {})),
        "stage_snapshots": _result_stage_snapshots_summary(result),
        "validation_report": _validation_report_summary(dict(result.validation_report or {})),
        "validation_summary": _validation_summary_summary(dict(result.validation_summary or {})),
        "peer_comparison_report": _peer_comparison_summary(dict(result.peer_comparison_report or {})),
        "judge_report": _judge_report_summary(dict(result.judge_report or {})),
        "scoring_mutation_count": scoring_mutation_count,
        "scoring_changed_keys": scoring_changed_keys,
        "artifacts": artifact_paths,
    }
    snapshot = next(
        (item for item in list(getattr(controller, "assessment_history", []) or []) if item.cycle_id == result.cycle_id),
        None,
    )
    if snapshot:
        data["self_assessment"] = {
            "regime": snapshot.regime,
            "plan_source": snapshot.plan_source,
            "sharpe_ratio": snapshot.sharpe_ratio,
            "max_drawdown": snapshot.max_drawdown,
            "excess_return": snapshot.excess_return,
            "benchmark_passed": _bool(snapshot.benchmark_passed),
        }
    if result.strategy_scores:
        data.setdefault("self_assessment", {})
        data["self_assessment"].update(
            {
                "signal_accuracy": float(result.strategy_scores.get("signal_accuracy", 0.0) or 0.0),
                "timing_score": float(result.strategy_scores.get("timing_score", 0.0) or 0.0),
                "risk_control_score": float(result.strategy_scores.get("risk_control_score", 0.0) or 0.0),
                "overall_score": float(result.strategy_scores.get("overall_score", 0.0) or 0.0),
            }
        )
    return data


def build_validation_report_payloads(result: Any) -> dict[str, dict[str, Any]]:
    return {
        "validation": dict(getattr(result, "validation_report", {}) or {}),
        "peer_comparison": dict(getattr(result, "peer_comparison_report", {}) or {}),
        "judge": dict(getattr(result, "judge_report", {}) or {}),
    }


def write_validation_report_boundaries(
    output_dir: str | Path,
    *,
    cycle_id: int,
    report_payloads: dict[str, dict[str, Any]],
) -> None:
    base_dir = _resolved_output_dir(output_dir) / "validation"
    for suffix, payload in report_payloads.items():
        if not payload:
            continue
        path = base_dir / f"cycle_{cycle_id}_{suffix}.json"
        write_json_boundary(path, payload)


def write_runtime_freeze_boundary(
    *,
    output_dir: str | Path,
    report: dict[str, Any],
    filename: str = "runtime_frozen.json",
) -> Path:
    path = _resolved_output_dir(output_dir) / filename
    write_json_boundary(path, report)
    return path


def validation_report_artifacts(result: Any) -> dict[str, dict[str, Any]]:
    return build_validation_report_payloads(result)


def write_validation_report_artifacts(
    *,
    output_dir: str | Path,
    cycle_id: int,
    report_payloads: dict[str, dict[str, Any]],
) -> None:
    write_validation_report_boundaries(
        output_dir,
        cycle_id=cycle_id,
        report_payloads=report_payloads,
    )


def refresh_training_leaderboards_boundary(controller: Any) -> None:
    run_root = Path(controller.output_dir)
    aggregate_root = run_root.parent
    aggregate_enabled = bool(getattr(controller, "aggregate_leaderboard_enabled", True))
    leaderboard_policy = {
        "quality_gate_matrix": dict(getattr(controller, "quality_gate_matrix", {}) or {}),
        "train": {
            "promotion_gate": dict(getattr(controller, "promotion_gate_policy", {}) or {}),
            "freeze_gate": dict(getattr(controller, "freeze_gate_policy", {}) or {}),
            "quality_gate_matrix": dict(getattr(controller, "quality_gate_matrix", {}) or {}),
        },
    }
    write_leaderboard(
        run_root,
        run_root / "leaderboard.json",
        policy=leaderboard_policy,
    )
    if aggregate_enabled and aggregate_root != run_root:
        write_leaderboard(
            aggregate_root,
            aggregate_root / "leaderboard.json",
            policy=leaderboard_policy,
        )

class TrainingPersistenceService:
    def record_self_assessment(
        self,
        controller: Any,
        snapshot_factory: Any,
        cycle_result: Any,
        assessment_payload: Dict[str, Any],
    ) -> None:
        snapshot = build_self_assessment_snapshot(snapshot_factory, cycle_result, assessment_payload)
        controller.assessment_history.append(snapshot)

    def refresh_leaderboards(self, controller: Any) -> None:
        refresh_training_leaderboards_boundary(controller)

    def save_cycle_result(self, controller: Any, result: Any) -> None:
        path = Path(controller.output_dir) / f"cycle_{result.cycle_id}.json"
        write_trade_history_artifact(
            controller.output_dir,
            cycle_id=int(result.cycle_id),
            trade_history=list(getattr(result, "trade_history", []) or []),
        )
        data = build_cycle_result_persistence_payload(controller, result)
        if "execution_defaults" not in data:
            data["execution_defaults"] = dict(getattr(result, "execution_defaults", {}) or {})
        write_json_boundary(path, data, max_bytes=MAX_CYCLE_RESULT_BYTES)
        self.save_validation_reports(controller, result)
        try:
            self.refresh_leaderboards(controller)
        except ArtifactTooLargeError:
            raise
        except Exception as exc:
            logger.warning(
                "leaderboard update failed: cycle_id=%s error=%s",
                getattr(result, "cycle_id", ""),
                exc,
                exc_info=True,
            )
            event_emitter = getattr(controller, "_emit_runtime_event", None)
            if callable(event_emitter):
                event_emitter(
                    "warning",
                    {
                        "cycle_id": int(getattr(result, "cycle_id", 0) or 0),
                        "severity": "warning",
                        "risk_level": "medium",
                        "message": "leaderboard update failed",
                        "error": str(exc),
                    },
                )

    def save_validation_reports(self, controller: Any, result: Any) -> None:
        write_validation_report_artifacts(
            output_dir=controller.output_dir,
            cycle_id=int(result.cycle_id),
            report_payloads=validation_report_artifacts(result),
        )
