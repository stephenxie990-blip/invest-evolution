from __future__ import annotations

from typing import Any, Dict, Iterable

from invest.contracts import ModelOutput
from .contracts import ResearchSnapshot, stable_hash


_MODEL_SCORE_KEYS = {
    "momentum": ["algo_score"],
    "mean_reversion": ["reversion_score", "algo_score"],
    "value_quality": ["value_quality_score", "algo_score"],
    "defensive_low_vol": ["defensive_score", "algo_score"],
}


def _coerce_float(value: Any) -> float | None:
    try:
        if value in (None, "", "None"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_score(model_name: str, item: Dict[str, Any]) -> float | None:
    for key in _MODEL_SCORE_KEYS.get(str(model_name or "").strip().lower(), ["algo_score"]):
        value = _coerce_float(item.get(key))
        if value is not None:
            return value
    return _coerce_float(item.get("algo_score"))


def _normalize_universe(model_name: str, stock_summaries: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    ranked: list[Dict[str, Any]] = []
    for item in list(stock_summaries or []):
        row = dict(item or {})
        row["model_relative_score"] = _pick_score(model_name, row)
        ranked.append(row)
    ranked.sort(
        key=lambda row: (
            row.get("model_relative_score") if row.get("model_relative_score") is not None else float("-inf"),
            row.get("algo_score") if row.get("algo_score") is not None else float("-inf"),
        ),
        reverse=True,
    )
    return ranked


def build_research_snapshot(
    *,
    model_output: ModelOutput,
    security: Dict[str, Any],
    query_code: str,
    stock_data: Dict[str, Any],
    routing_context: Dict[str, Any] | None = None,
    data_lineage: Dict[str, Any] | None = None,
    legacy_signals: Dict[str, Any] | None = None,
) -> ResearchSnapshot:
    signal_packet = model_output.signal_packet
    metadata = dict(signal_packet.metadata or {})
    model_name = str(signal_packet.model_name or model_output.model_name or "unknown")
    universe_rows = _normalize_universe(
        model_name,
        metadata.get("raw_summaries") or metadata.get("stock_summaries") or [],
    )
    selected_map = {item.code: item.to_dict() for item in list(signal_packet.signals or [])}
    summary = next((dict(item) for item in universe_rows if str(item.get("code") or "") == query_code), None)
    selected_signal = dict(selected_map.get(query_code) or {})
    universe_size = len(universe_rows)
    rank = None
    percentile = None
    threshold_score = None
    threshold_gap = None
    approximate = False
    if universe_rows:
        for idx, item in enumerate(universe_rows, start=1):
            if str(item.get("code") or "") == query_code:
                rank = idx
                percentile = round((universe_size - idx + 1) / max(universe_size, 1), 4)
                break
    selected_scores = [float(item.get("score", 0.0) or 0.0) for item in list(selected_map.values())]
    if selected_scores:
        threshold_score = min(selected_scores)
    if summary is not None and threshold_score is not None:
        model_score = _coerce_float(summary.get("model_relative_score"))
        if model_score is None:
            model_score = _coerce_float(summary.get("algo_score"))
            approximate = True
        if model_score is not None:
            threshold_gap = round(model_score - threshold_score, 4)
    market_context = {
        "regime": str(signal_packet.regime or (routing_context or {}).get("regime") or "unknown"),
        "cash_reserve": float(signal_packet.cash_reserve or 0.0),
        "model_name": model_name,
        "config_name": str(signal_packet.config_name or model_output.config_name or "unknown"),
        "market_stats": dict(metadata.get("market_stats") or {}),
        "routing_context": dict(routing_context or {}),
    }
    cross_section_context = {
        "selected_by_policy": query_code in set(signal_packet.selected_codes or []),
        "rank": rank,
        "percentile": percentile,
        "threshold_score": threshold_score,
        "threshold_gap": threshold_gap,
        "threshold_gap_is_approximate": approximate,
        "selected_count": len(signal_packet.selected_codes or []),
        "universe_size": universe_size,
        "top_selected_codes": list(signal_packet.selected_codes or []),
    }
    factor_values = dict(selected_signal.get("factor_values") or {})
    signal_metadata = dict(selected_signal.get("metadata") or {})
    legacy_payload = dict(legacy_signals or {})
    legacy_flags = dict(legacy_payload.get("flags") or {})
    if legacy_flags and "flags" not in signal_metadata:
        signal_metadata["flags"] = legacy_flags
    if legacy_payload.get("matched_signals") and "matched_signals" not in signal_metadata:
        signal_metadata["matched_signals"] = list(legacy_payload.get("matched_signals") or [])
    if legacy_payload.get("latest_close") is not None and "latest_close" not in signal_metadata:
        signal_metadata["latest_close"] = legacy_payload.get("latest_close")
    if legacy_payload.get("ma20") is not None and factor_values.get("ma20") is None:
        factor_values["ma20"] = legacy_payload.get("ma20")
    if legacy_payload.get("rsi") is not None and factor_values.get("rsi") is None:
        factor_values["rsi"] = legacy_payload.get("rsi")
    feature_snapshot = {
        "summary": dict(summary or {}),
        "signal": selected_signal,
        "legacy_signals": legacy_payload,
        "evidence": list(selected_signal.get("evidence") or []),
        "factor_values": factor_values,
        "metadata": signal_metadata,
    }
    universe = {
        "size": universe_size,
        "available_codes": sorted(list(stock_data.keys())),
        "summary_top5": [dict(item) for item in universe_rows[:5]],
    }
    payload = {
        "as_of_date": signal_packet.as_of_date,
        "scope": "single_security",
        "query_code": query_code,
        "market_context": market_context,
        "cross_section_context": cross_section_context,
        "feature_snapshot": feature_snapshot,
    }
    snapshot_hash = stable_hash(payload)
    readiness = {
        "has_model_output": True,
        "has_universe_summary": bool(universe_rows),
        "has_selected_signal": bool(selected_signal),
        "has_query_summary": summary is not None,
    }
    return ResearchSnapshot(
        snapshot_id=f"snapshot_{snapshot_hash[:16]}",
        as_of_date=str(signal_packet.as_of_date),
        scope="single_security",
        security=dict(security or {}),
        universe=universe,
        market_context=market_context,
        cross_section_context=cross_section_context,
        feature_snapshot=feature_snapshot,
        data_lineage=dict(data_lineage or {}),
        readiness=readiness,
        metadata={
            "query_code": query_code,
            "model_reasoning": str(signal_packet.reasoning or ""),
            "agent_context_summary": str(getattr(model_output.agent_context, "summary", "") or ""),
        },
    )
