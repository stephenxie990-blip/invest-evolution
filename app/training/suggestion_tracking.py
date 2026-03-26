from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_EFFECT_WINDOW_CYCLES = 3
_LOWER_IS_BETTER_METRICS = {"avg_max_drawdown"}
_METRIC_TOLERANCES = {
    "avg_return_pct": 0.20,
    "benchmark_pass_rate": 0.10,
    "avg_strategy_score": 0.05,
    "avg_sharpe_ratio": 0.10,
    "avg_max_drawdown": 0.25,
}


def _copy_dict(value: Any) -> dict[str, Any]:
    return deepcopy(dict(value or {}))


def _string_items(values: Any) -> list[str]:
    items: list[str] = []
    for value in list(values or []):
        item = str(value or "").strip()
        if item and item not in items:
            items.append(item)
    return items


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


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _record_field(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _cycle_id(item: Any) -> int:
    try:
        return int(_record_field(item, "cycle_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _effect_target_metrics(proposal_kind: str, patch: dict[str, Any]) -> list[str]:
    if proposal_kind == "scoring_adjustment":
        return ["avg_strategy_score", "benchmark_pass_rate"]
    if proposal_kind == "agent_weight_adjustment":
        return ["avg_return_pct", "avg_strategy_score"]

    metrics = ["avg_return_pct", "benchmark_pass_rate"]
    risk_keys = {"position_size", "cash_reserve", "stop_loss_pct", "trailing_pct", "max_positions"}
    if any(str(key) in risk_keys for key in dict(patch or {}).keys()):
        metrics.append("avg_max_drawdown")
    return metrics


def _metric_value(item: Any, metric_name: str) -> float | None:
    if metric_name == "avg_return_pct":
        return _safe_float(_record_field(item, "return_pct"))
    if metric_name == "benchmark_pass_rate":
        return 1.0 if bool(_record_field(item, "benchmark_passed", False)) else 0.0
    if metric_name == "avg_strategy_score":
        strategy_scores = dict(_record_field(item, "strategy_scores", {}) or {})
        self_assessment = dict(_record_field(item, "self_assessment", {}) or {})
        return _safe_float(
            strategy_scores.get("overall_score")
            if strategy_scores.get("overall_score") is not None
            else self_assessment.get("overall_score")
        )
    if metric_name == "avg_sharpe_ratio":
        self_assessment = dict(_record_field(item, "self_assessment", {}) or {})
        return _safe_float(
            self_assessment.get("sharpe_ratio")
            if self_assessment.get("sharpe_ratio") is not None
            else _record_field(item, "sharpe_ratio")
        )
    if metric_name == "avg_max_drawdown":
        self_assessment = dict(_record_field(item, "self_assessment", {}) or {})
        return _safe_float(
            self_assessment.get("max_drawdown")
            if self_assessment.get("max_drawdown") is not None
            else _record_field(item, "max_drawdown")
        )
    return None


def _aggregate_metric(cycles: list[Any], metric_name: str) -> float | None:
    values = [
        value
        for value in (_metric_value(item, metric_name) for item in list(cycles or []))
        if value is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _effect_metric_status(metric_name: str, baseline: float, observed: float) -> tuple[str, float]:
    delta = observed - baseline
    tolerance = float(_METRIC_TOLERANCES.get(metric_name, 0.05))
    if metric_name in _LOWER_IS_BETTER_METRICS:
        if delta <= -tolerance:
            return "improved", delta
        if delta >= tolerance:
            return "worsened", delta
        return "neutral", delta
    if delta >= tolerance:
        return "improved", delta
    if delta <= -tolerance:
        return "worsened", delta
    return "neutral", delta


def _summarize_effect_result(
    *,
    overall_status: str,
    observed_cycles: int,
    metric_results: list[dict[str, Any]],
) -> str:
    if overall_status == "pending":
        return "awaiting effect window completion"
    if overall_status == "pending_adoption":
        return "waiting for adoption decision"
    if overall_status == "not_applicable":
        return "proposal blocked before adoption"
    if overall_status == "inconclusive":
        return f"effect window completed but evidence was inconclusive across {observed_cycles} cycles"
    improved = sum(1 for item in metric_results if str(item.get("status")) == "improved")
    worsened = sum(1 for item in metric_results if str(item.get("status")) == "worsened")
    neutral = sum(1 for item in metric_results if str(item.get("status")) == "neutral")
    return (
        f"{overall_status}: improved={improved}, worsened={worsened}, "
        f"neutral={neutral}, observed_cycles={observed_cycles}"
    )


def _suggestion_text(
    proposal: dict[str, Any],
    *,
    source: str,
    rationale: str,
    patch: dict[str, Any],
) -> str:
    metadata = _copy_dict(proposal.get("metadata") or {})
    explicit = str(
        proposal.get("suggestion_text")
        or metadata.get("suggestion_text")
        or ""
    ).strip()
    if explicit:
        return explicit

    evidence = _copy_dict(proposal.get("evidence") or {})
    for key in ("strategy_suggestions", "suggestions"):
        suggestions = _string_items(evidence.get(key) or metadata.get(key) or [])
        if suggestions:
            return suggestions[0]

    rationale_text = str(rationale or "").strip()
    if rationale_text:
        return rationale_text

    patch_keys = ", ".join(sorted(str(key) for key in dict(patch or {}).keys())[:3])
    if patch_keys:
        return f"{source}: {patch_keys}"
    return str(source or "learning_proposal").strip() or "learning_proposal"


def ensure_proposal_tracking_fields(
    proposal: dict[str, Any] | None,
    *,
    default_cycle_id: int | None = None,
) -> dict[str, Any]:
    payload = _copy_dict(proposal or {})
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
    patch = _copy_dict(payload.get("patch") or {})
    rationale = str(payload.get("rationale") or "").strip()
    metadata = _copy_dict(payload.get("metadata") or {})
    proposal_kind = str(metadata.get("proposal_kind") or "runtime_param_adjustment").strip()

    payload["suggestion_text"] = _suggestion_text(
        payload,
        source=source,
        rationale=rationale,
        patch=patch,
    )

    effect_window = _copy_dict(payload.get("effect_window") or {})
    window_cycles = int(
        effect_window.get("window_cycles")
        or metadata.get("effect_window_cycles")
        or payload.get("effect_window_cycles")
        or DEFAULT_EFFECT_WINDOW_CYCLES
    )
    window_cycles = max(1, window_cycles)
    payload["effect_window"] = {
        "window_cycles": window_cycles,
        "start_cycle_id": int(effect_window.get("start_cycle_id") or cycle_id + 1),
        "end_cycle_id": int(effect_window.get("end_cycle_id") or cycle_id + window_cycles),
        "evaluation_after_cycle_id": int(
            effect_window.get("evaluation_after_cycle_id") or cycle_id + window_cycles
        ),
    }

    target_metrics = _string_items(
        payload.get("effect_target_metrics")
        or metadata.get("effect_target_metrics")
        or _effect_target_metrics(proposal_kind, patch)
    )
    payload["effect_target_metrics"] = target_metrics

    adoption_ref = _copy_dict(payload.get("adoption_ref") or {})
    payload["adoption_status"] = str(payload.get("adoption_status") or "queued").strip() or "queued"
    payload["adoption_ref"] = {
        "decision_cycle_id": adoption_ref.get("decision_cycle_id"),
        "decision_stage": str(adoption_ref.get("decision_stage") or "proposal_recorded"),
        "decision_reason": str(
            adoption_ref.get("decision_reason") or "queued_for_candidate_governance"
        ),
        "candidate_config_ref": str(adoption_ref.get("candidate_config_ref") or ""),
        "candidate_version_id": str(adoption_ref.get("candidate_version_id") or ""),
        "pending_candidate_ref": str(adoption_ref.get("pending_candidate_ref") or ""),
        "proposal_bundle_id": str(adoption_ref.get("proposal_bundle_id") or ""),
        "block_reasons": _string_items(adoption_ref.get("block_reasons") or []),
    }

    effect_result = _copy_dict(payload.get("effect_result") or {})
    effect_status = str(payload.get("effect_status") or "pending_adoption").strip() or "pending_adoption"
    payload["effect_status"] = effect_status
    payload["effect_result"] = {
        "status": str(effect_result.get("status") or effect_status),
        "observed_cycles": int(effect_result.get("observed_cycles") or 0),
        "summary": str(effect_result.get("summary") or ""),
    }
    return payload


def evaluate_proposal_effect(
    proposal: dict[str, Any] | None,
    *,
    cycle_history: list[Any] | None = None,
    current_cycle_id: int | None = None,
) -> dict[str, Any]:
    payload = ensure_proposal_tracking_fields(proposal)
    adoption_status = str(payload.get("adoption_status") or "queued").strip() or "queued"
    if adoption_status != "adopted_to_candidate":
        return payload

    sorted_cycles = sorted(list(cycle_history or []), key=_cycle_id)
    if current_cycle_id is None:
        current_cycle_id = max((_cycle_id(item) for item in sorted_cycles), default=0)
    effect_window = dict(payload.get("effect_window") or {})
    start_cycle_id = int(effect_window.get("start_cycle_id") or 0)
    end_cycle_id = int(effect_window.get("end_cycle_id") or 0)
    evaluation_after_cycle_id = int(
        effect_window.get("evaluation_after_cycle_id") or end_cycle_id or start_cycle_id
    )
    decision_cycle_id = int(
        dict(payload.get("adoption_ref") or {}).get("decision_cycle_id")
        or payload.get("cycle_id")
        or 0
    )
    observed_window_cycles = [
        item
        for item in sorted_cycles
        if start_cycle_id <= _cycle_id(item) <= end_cycle_id
    ]

    if int(current_cycle_id or 0) < evaluation_after_cycle_id:
        payload["effect_status"] = "pending"
        payload["effect_result"] = {
            "status": "pending",
            "observed_cycles": len(observed_window_cycles),
            "summary": _summarize_effect_result(
                overall_status="pending",
                observed_cycles=len(observed_window_cycles),
                metric_results=[],
            ),
            "evaluation_after_cycle_id": evaluation_after_cycle_id,
        }
        return payload

    baseline_cycles = [
        item
        for item in sorted_cycles
        if _cycle_id(item) <= decision_cycle_id
    ]
    window_cycles = max(1, int(effect_window.get("window_cycles") or DEFAULT_EFFECT_WINDOW_CYCLES))
    baseline_cycles = baseline_cycles[-window_cycles:]
    metric_results: list[dict[str, Any]] = []
    target_metrics = _string_items(payload.get("effect_target_metrics") or [])

    for metric_name in target_metrics:
        baseline_value = _aggregate_metric(baseline_cycles, metric_name)
        observed_value = _aggregate_metric(observed_window_cycles, metric_name)
        if baseline_value is None or observed_value is None:
            continue
        status, delta = _effect_metric_status(metric_name, baseline_value, observed_value)
        metric_results.append(
            {
                "metric": metric_name,
                "baseline_value": baseline_value,
                "observed_value": observed_value,
                "delta": delta,
                "status": status,
                "direction": "lower_is_better"
                if metric_name in _LOWER_IS_BETTER_METRICS
                else "higher_is_better",
                "tolerance": float(_METRIC_TOLERANCES.get(metric_name, 0.05)),
            }
        )

    if not metric_results:
        overall_status = "inconclusive"
    else:
        improved_count = sum(1 for item in metric_results if str(item.get("status")) == "improved")
        worsened_count = sum(1 for item in metric_results if str(item.get("status")) == "worsened")
        if improved_count > worsened_count:
            overall_status = "improved"
        elif worsened_count > improved_count:
            overall_status = "worsened"
        else:
            overall_status = "neutral"

    payload["effect_status"] = overall_status
    payload["effect_result"] = {
        "status": overall_status,
        "observed_cycles": len(observed_window_cycles),
        "baseline_cycle_count": len(baseline_cycles),
        "evaluation_after_cycle_id": evaluation_after_cycle_id,
        "metric_results": metric_results,
        "summary": _summarize_effect_result(
            overall_status=overall_status,
            observed_cycles=len(observed_window_cycles),
            metric_results=metric_results,
        ),
    }
    return payload


def apply_proposal_outcome(
    proposal: dict[str, Any] | None,
    *,
    adoption_status: str,
    decision_cycle_id: int,
    decision_stage: str,
    decision_reason: str,
    candidate_config_ref: str = "",
    candidate_version_id: str = "",
    pending_candidate_ref: str = "",
    proposal_bundle_id: str = "",
    block_reasons: list[str] | None = None,
) -> dict[str, Any]:
    payload = ensure_proposal_tracking_fields(proposal, default_cycle_id=decision_cycle_id)
    normalized_status = str(adoption_status or "queued").strip() or "queued"
    normalized_block_reasons = _string_items(block_reasons or [])

    if normalized_status == "adopted_to_candidate":
        effect_status = "pending"
        effect_summary = "awaiting effect window completion"
    elif normalized_status == "deferred_pending_candidate":
        effect_status = "pending_adoption"
        effect_summary = "waiting for unresolved candidate to resolve"
    elif normalized_status == "rejected_by_proposal_gate":
        effect_status = "not_applicable"
        effect_summary = "proposal blocked before adoption"
    else:
        effect_status = "pending_adoption"
        effect_summary = "awaiting adoption decision"

    payload["adoption_status"] = normalized_status
    payload["adoption_ref"] = {
        "decision_cycle_id": int(decision_cycle_id),
        "decision_stage": str(decision_stage or ""),
        "decision_reason": str(decision_reason or ""),
        "candidate_config_ref": str(candidate_config_ref or ""),
        "candidate_version_id": str(candidate_version_id or ""),
        "pending_candidate_ref": str(pending_candidate_ref or ""),
        "proposal_bundle_id": str(proposal_bundle_id or ""),
        "block_reasons": normalized_block_reasons,
    }
    payload["effect_status"] = effect_status
    payload["effect_result"] = {
        "status": effect_status,
        "observed_cycles": 0,
        "summary": effect_summary,
    }
    return payload


def refresh_cycle_history_suggestion_effects(
    controller: Any | None,
    *,
    cycle_history: list[Any] | None = None,
) -> dict[str, Any]:
    updated_bundle_count = 0
    evaluated_suggestion_count = 0
    completed_effect_count = 0
    sorted_cycles = sorted(list(cycle_history or []), key=_cycle_id)
    current_cycle_id = max((_cycle_id(item) for item in sorted_cycles), default=0)

    try:
        from app.training.proposal_store import update_cycle_proposal_bundle
    except Exception:
        update_cycle_proposal_bundle = None

    for item in sorted_cycles:
        if isinstance(item, dict):
            proposal_bundle = dict(item.get("proposal_bundle") or {})
        else:
            proposal_bundle = dict(getattr(item, "proposal_bundle", {}) or {})
        proposals = [
            ensure_proposal_tracking_fields(dict(entry or {}))
            for entry in list(proposal_bundle.get("proposals") or [])
            if dict(entry or {})
        ]
        if not proposals:
            continue

        changed = False
        refreshed: list[dict[str, Any]] = []
        for proposal in proposals:
            before_status = str(proposal.get("effect_status") or "")
            updated = evaluate_proposal_effect(
                proposal,
                cycle_history=sorted_cycles,
                current_cycle_id=current_cycle_id,
            )
            after_status = str(updated.get("effect_status") or "")
            if updated != proposal:
                changed = True
            if before_status == "pending" and after_status in {
                "improved",
                "worsened",
                "neutral",
                "inconclusive",
            }:
                completed_effect_count += 1
            if after_status in {"improved", "worsened", "neutral", "inconclusive"}:
                evaluated_suggestion_count += 1
            refreshed.append(updated)

        if not changed:
            continue

        proposal_bundle["proposals"] = refreshed
        proposal_bundle["suggestion_tracking_summary"] = build_suggestion_tracking_summary(refreshed)
        bundle_path = str(proposal_bundle.get("bundle_path") or "")
        if bundle_path and callable(update_cycle_proposal_bundle):
            proposal_bundle = update_cycle_proposal_bundle(
                controller,
                bundle_path=bundle_path,
                proposals=refreshed,
            )
        if isinstance(item, dict):
            item["proposal_bundle"] = proposal_bundle
        else:
            setattr(item, "proposal_bundle", proposal_bundle)
        updated_bundle_count += 1

    return {
        "current_cycle_id": current_cycle_id,
        "updated_bundle_count": updated_bundle_count,
        "evaluated_suggestion_count": evaluated_suggestion_count,
        "completed_effect_count": completed_effect_count,
    }


def build_suggestion_tracking_summary(
    proposals: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    normalized = [
        ensure_proposal_tracking_fields(dict(item or {}))
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
        "adopted_suggestion_count": int(adoption_status_counts.get("adopted_to_candidate", 0) or 0),
        "deferred_suggestion_count": int(
            adoption_status_counts.get("deferred_pending_candidate", 0)
        ),
        "rejected_suggestion_count": int(
            adoption_status_counts.get("rejected_by_proposal_gate", 0)
        ),
        "queued_suggestion_count": int(adoption_status_counts.get("queued", 0) or 0),
        "improved_suggestion_count": int(effect_status_counts.get("improved", 0) or 0),
        "worsened_suggestion_count": int(effect_status_counts.get("worsened", 0) or 0),
        "neutral_suggestion_count": int(effect_status_counts.get("neutral", 0) or 0),
        "inconclusive_suggestion_count": int(effect_status_counts.get("inconclusive", 0) or 0),
        "source_counts": source_counts,
    }
