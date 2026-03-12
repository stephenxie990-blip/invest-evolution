from __future__ import annotations

from typing import Any, Dict

from .contracts import ResearchHypothesis


def build_dashboard_projection(
    *,
    hypothesis: ResearchHypothesis,
    matched_signals: list[str],
    core_rules: list[str],
    entry_conditions: list[str],
    legacy_reason: str = "",
) -> Dict[str, Any]:
    reason_parts = []
    if legacy_reason:
        reason_parts.append(str(legacy_reason))
    if hypothesis.supporting_factors:
        reason_parts.append("支持因素: " + "、".join(hypothesis.supporting_factors[:4]))
    if hypothesis.contradicting_factors:
        reason_parts.append("风险因素: " + "、".join(hypothesis.contradicting_factors[:3]))
    return {
        "signal": hypothesis.stance,
        "score": float(hypothesis.score),
        "entry_price": dict(hypothesis.entry_rule or {}).get("price"),
        "stop_loss": dict(hypothesis.invalidation_rule or {}).get("price"),
        "reason": "；".join(part for part in reason_parts if part),
        "matched_signals": sorted(set(matched_signals or [])),
        "core_rules": list(core_rules or []),
        "entry_conditions": list(entry_conditions or []),
    }
