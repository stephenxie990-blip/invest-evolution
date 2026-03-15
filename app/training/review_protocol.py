from __future__ import annotations

from typing import Any

from app.training.experiment_protocol import build_review_basis_window
from invest.contracts import EvalReport


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


def _normalize_review_result(
    payload: dict[str, Any],
    *,
    controller: Any | None = None,
) -> dict[str, Any]:
    record = dict(payload or {})
    metadata = dict(record.get("metadata") or {})
    selection_mode = str(record.get("selection_mode") or "unknown")
    llm_used = bool(record.get("llm_used", metadata.get("llm_used", False)))
    regime = str(record.get("regime") or metadata.get("regime") or "unknown")
    model_name = str(
        metadata.get("model_name")
        or record.get("model_name")
        or getattr(controller, "model_name", "")
        or ""
    )
    config_name = str(
        metadata.get("config_name")
        or record.get("config_name")
        or getattr(controller, "model_config_path", "")
        or ""
    )
    research_feedback = dict(
        metadata.get("research_feedback") or record.get("research_feedback") or {}
    )
    causal_diagnosis = dict(
        metadata.get("causal_diagnosis") or record.get("causal_diagnosis") or {}
    )
    similarity_summary = dict(
        metadata.get("similarity_summary") or record.get("similarity_summary") or {}
    )
    review_decision = dict(
        metadata.get("review_decision") or record.get("review_decision") or {}
    )
    ab_comparison = dict(
        metadata.get("ab_comparison") or record.get("ab_comparison") or {}
    )
    metadata.update(
        {
            "model_name": model_name,
            "config_name": config_name,
            "research_feedback": research_feedback,
            "causal_diagnosis": causal_diagnosis,
            "similarity_summary": similarity_summary,
            "review_decision": review_decision,
            "ab_comparison": ab_comparison,
        }
    )
    record["cycle_id"] = _coerce_int(record.get("cycle_id"))
    record["return_pct"] = _coerce_float(record.get("return_pct"))
    record["is_profit"] = bool(record.get("is_profit", record["return_pct"] > 0))
    record["selection_mode"] = selection_mode
    record["plan_source"] = str(
        record.get("plan_source") or _plan_source(selection_mode, llm_used)
    )
    record["benchmark_passed"] = bool(record.get("benchmark_passed", False))
    record["review_applied"] = bool(record.get("review_applied", False))
    record["regime"] = regime
    record["llm_used"] = llm_used
    record["metadata"] = metadata
    record["research_feedback"] = research_feedback
    record["causal_diagnosis"] = causal_diagnosis
    record["similarity_summary"] = similarity_summary
    record["review_decision"] = review_decision
    record["ab_comparison"] = ab_comparison
    record["failure_signature"] = _failure_signature(record)
    record["evidence_score"] = _evidence_support_score(record)
    return record


def _history_record_to_review_result(item: Any) -> dict[str, Any]:
    routing_decision = dict(getattr(item, "routing_decision", {}) or {})
    audit_tags = dict(getattr(item, "audit_tags", {}) or {})
    research_feedback = dict(getattr(item, "research_feedback", {}) or {})
    selection_mode = str(getattr(item, "selection_mode", "unknown") or "unknown")
    llm_used = bool(getattr(item, "llm_used", False))

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
            "regime": str(
                routing_decision.get("regime")
                or audit_tags.get("routing_regime")
                or "unknown"
            ),
            "metadata": {
                "model_name": str(getattr(item, "model_name", "") or ""),
                "config_name": str(getattr(item, "config_name", "") or ""),
                "research_feedback": research_feedback,
                "causal_diagnosis": dict(getattr(item, "causal_diagnosis", {}) or {}),
                "similarity_summary": dict(getattr(item, "similarity_summary", {}) or {}),
                "review_decision": dict(getattr(item, "review_decision", {}) or {}),
                "ab_comparison": dict(getattr(item, "ab_comparison", {}) or {}),
            },
            "research_feedback": research_feedback,
            "causal_diagnosis": dict(getattr(item, "causal_diagnosis", {}) or {}),
            "similarity_summary": dict(getattr(item, "similarity_summary", {}) or {}),
            "review_decision": dict(getattr(item, "review_decision", {}) or {}),
            "ab_comparison": dict(getattr(item, "ab_comparison", {}) or {}),
            "llm_used": llm_used,
        }
    )


def _feedback_bias(record: dict[str, Any]) -> str:
    feedback = dict(record.get("research_feedback") or {})
    recommendation = dict(feedback.get("recommendation") or {})
    return str(recommendation.get("bias") or "").strip()


def _primary_driver(record: dict[str, Any]) -> str:
    diagnosis = dict(record.get("causal_diagnosis") or {})
    return str(diagnosis.get("primary_driver") or "").strip()


def _evidence_support_score(record: dict[str, Any]) -> int:
    score = 0
    similarity_summary = dict(record.get("similarity_summary") or {})
    if list(similarity_summary.get("matched_cycle_ids") or []):
        score += 1
    diagnosis = dict(record.get("causal_diagnosis") or {})
    drivers = [dict(item) for item in list(diagnosis.get("drivers") or [])]
    if any(list(item.get("evidence_cycle_ids") or []) for item in drivers):
        score += 1
    feedback = dict(record.get("research_feedback") or {})
    if int(feedback.get("sample_count") or 0) > 0:
        score += 1
    return score


def _failure_signature(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "return_direction": "profit" if bool(record.get("is_profit", False)) else "loss",
        "benchmark_passed": bool(record.get("benchmark_passed", False)),
        "primary_driver": _primary_driver(record),
        "feedback_bias": _feedback_bias(record),
    }


def _strict_failure_match(candidate: dict[str, Any], current_result: dict[str, Any]) -> bool:
    if bool(current_result.get("is_profit", False)):
        return bool(candidate.get("is_profit", False)) == bool(current_result.get("is_profit", False))
    if bool(candidate.get("is_profit", False)):
        return False
    current_regime = str(current_result.get("regime") or "")
    if current_regime not in {"", "unknown"} and str(candidate.get("regime") or "") != current_regime:
        return False
    if not bool(current_result.get("benchmark_passed", False)) and bool(candidate.get("benchmark_passed", False)):
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
    candidate: dict[str, Any],
    current_result: dict[str, Any],
) -> tuple[int, list[str]]:
    matched_features: list[str] = []
    score = 0
    candidate_meta = dict(candidate.get("metadata") or {})
    current_meta = dict(current_result.get("metadata") or {})

    if str(candidate.get("regime") or "") == str(current_result.get("regime") or "") and str(
        current_result.get("regime") or ""
    ) not in {"", "unknown"}:
        score += 4
        matched_features.append("regime")
    if str(candidate.get("selection_mode") or "") == str(
        current_result.get("selection_mode") or ""
    ) and str(current_result.get("selection_mode") or "") not in {"", "unknown"}:
        score += 3
        matched_features.append("selection_mode")
    if bool(candidate.get("benchmark_passed", False)) == bool(
        current_result.get("benchmark_passed", False)
    ):
        score += 2
        matched_features.append("benchmark_passed")
    if str(candidate.get("plan_source") or "") == str(current_result.get("plan_source") or "") and str(
        current_result.get("plan_source") or ""
    ) not in {"", "unknown"}:
        score += 2
        matched_features.append("plan_source")
    if str(candidate_meta.get("model_name") or "") == str(
        current_meta.get("model_name") or ""
    ) and str(current_meta.get("model_name") or ""):
        score += 2
        matched_features.append("model_name")
    if str(candidate_meta.get("config_name") or "") == str(
        current_meta.get("config_name") or ""
    ) and str(current_meta.get("config_name") or ""):
        score += 1
        matched_features.append("config_name")

    if _primary_driver(candidate) and _primary_driver(candidate) == _primary_driver(current_result):
        score += 3
        matched_features.append("primary_driver")
    if _feedback_bias(candidate) and _feedback_bias(candidate) == _feedback_bias(current_result):
        score += 2
        matched_features.append("feedback_bias")

    current_sign = 1 if bool(current_result.get("is_profit")) else -1
    candidate_sign = 1 if bool(candidate.get("is_profit")) else -1
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
    current_result: dict[str, Any],
    limit: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    minimum_score = 5
    ranked: list[tuple[int, int, dict[str, Any], list[str]]] = []
    history = list(getattr(controller, "cycle_history", []) or [])
    requires_strict_failure_match = not bool(current_result.get("is_profit", False))
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
        ranked.append((score, candidate["cycle_id"], candidate, matched_features))

    ranked.sort(key=lambda item: (-item[0], -item[1]))
    selected: list[dict[str, Any]] = []
    aggregated_features: list[str] = []
    for score, _, candidate, matched_features in ranked[:limit]:
        enriched = dict(candidate)
        enriched["similarity_score"] = score
        enriched["matched_features"] = list(matched_features)
        enriched["strict_failure_match"] = _strict_failure_match(candidate, current_result)
        enriched["failure_signature"] = _failure_signature(candidate)
        enriched["evidence_score"] = _evidence_support_score(candidate)
        selected.append(enriched)
        for feature in matched_features:
            if feature not in aggregated_features:
                aggregated_features.append(feature)

    regimes = [
        str(item.get("regime") or "unknown")
        for item in selected
        if str(item.get("regime") or "").strip()
    ]
    dominant_regime = str(current_result.get("regime") or "unknown")
    if regimes:
        dominant_regime = max(set(regimes), key=regimes.count)
    matched_primary_driver = _primary_driver(current_result)
    matched_feedback_bias = _feedback_bias(current_result)

    summary = {
        "target_cycle_id": int(cycle_id),
        "matched_cycle_ids": [int(item["cycle_id"]) for item in selected],
        "match_features": aggregated_features,
        "dominant_regime": dominant_regime,
        "compared_history_size": len(history),
        "strict_failure_match_count": sum(
            1 for item in selected if bool(item.get("strict_failure_match", False))
        ),
        "matched_primary_driver": matched_primary_driver,
        "matched_feedback_bias": matched_feedback_bias,
        "avg_evidence_score": round(
            _coerce_float(
                sum(int(item.get("evidence_score") or 0) for item in selected) / len(selected)
                if selected
                else 0.0
            ),
            2,
        ),
    }
    return selected, summary


def _build_causal_diagnosis(
    *,
    current_result: dict[str, Any],
    recent_results: list[dict[str, Any]],
    similar_results: list[dict[str, Any]],
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
        if str(item.get("regime") or "") == str(current_result.get("regime") or "")
        and not bool(item.get("is_profit", False))
    ]
    if not bool(current_result.get("is_profit", False)) and same_regime_losses:
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
    if not bool(current_result.get("benchmark_passed", False)) and benchmark_failures:
        drivers.append(
            {
                "code": "benchmark_gap",
                "label": "相似样本普遍未跑赢基准",
                "score": round(min(0.7, 0.2 + 0.08 * len(benchmark_failures)), 2),
                "evidence_cycle_ids": [int(item["cycle_id"]) for item in benchmark_failures],
            }
        )

    unapplied_reviews = [
        item for item in recent_results[:-1] if not bool(item.get("review_applied", False))
    ]
    if unapplied_reviews:
        drivers.append(
            {
                "code": "review_not_applied",
                "label": "近几轮复盘未形成有效修正",
                "score": round(min(0.6, 0.18 + 0.07 * len(unapplied_reviews)), 2),
                "evidence_cycle_ids": [int(item["cycle_id"]) for item in unapplied_reviews],
            }
        )

    selection_mode_cluster = [
        item
        for item in similar_results
        if str(item.get("selection_mode") or "") == str(current_result.get("selection_mode") or "")
    ]
    if (
        str(current_result.get("selection_mode") or "")
        not in {"", "unknown"}
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
        primary_driver = str(drivers[0].get("code") or "mixed_factors")
        summary = (
            f"{drivers[0].get('label')}"
            + (
                f"，其次是{drivers[1].get('label')}"
                if len(drivers) > 1
                else ""
            )
            + "，建议先围绕首要驱动逐步收敛参数。"
        )
    else:
        primary_driver = "mixed_factors"
        summary = "相似样本已检索，但未出现足够集中的单一失效模式。"
    return {
        "primary_driver": primary_driver,
        "summary": summary,
        "drivers": drivers,
        "evidence": {
            "matched_cycle_ids": [int(item["cycle_id"]) for item in similar_results],
            "current_cycle_id": int(current_result.get("cycle_id") or 0),
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
        current_result = _normalize_review_result(eval_report.to_dict(), controller=controller)
    cycle_ids = set(review_basis_window["cycle_ids"])
    recent_results = [
        _history_record_to_review_result(item)
        for item in list(getattr(controller, "cycle_history", []) or [])
        if getattr(item, "cycle_id", None) is not None
        and int(getattr(item, "cycle_id")) in cycle_ids
        and int(getattr(item, "cycle_id")) != int(cycle_id)
    ]
    recent_results.append(current_result)
    recent_results = recent_results[-int(review_basis_window["size"]):]
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
        "recent_results": recent_results,
        "review_basis_window": review_basis_window,
        "similar_results": similar_results,
        "similarity_summary": similarity_summary,
        "causal_diagnosis": causal_diagnosis,
    }
