from __future__ import annotations

from typing import Any, Dict

from .contracts import PolicySnapshot, ResearchHypothesis, ResearchSnapshot, stable_hash


def build_research_hypothesis(
    *,
    snapshot: ResearchSnapshot,
    policy: PolicySnapshot,
    scenario: Dict[str, Any],
    strategy_name: str,
    strategy_display_name: str,
) -> ResearchHypothesis:
    cross = dict(snapshot.cross_section_context or {})
    features = dict(snapshot.feature_snapshot or {})
    signal = dict(features.get("signal") or {})
    summary = dict(features.get("summary") or {})
    feature_metadata = dict(features.get("metadata") or {})
    factor_values = dict(features.get("factor_values") or signal.get("factor_values") or {})
    latest_close = (
        summary.get("close")
        or feature_metadata.get("latest_close")
        or factor_values.get("latest_close")
    )
    latest_close = float(latest_close or 0.0) if latest_close not in (None, "") else 0.0
    percentile = cross.get("percentile")
    percentile_f = float(percentile or 0.0) if percentile is not None else 0.0
    selected_by_policy = bool(cross.get("selected_by_policy"))
    raw_score = 50.0 + percentile_f * 40.0 + (8.0 if selected_by_policy else 0.0)
    score = round(max(0.0, min(100.0, raw_score)), 1)
    stance = "持有观察"
    if score >= 82:
        stance = "候选买入"
    elif score >= 68:
        stance = "偏强关注"
    elif score <= 35:
        stance = "减仓/回避"
    elif score <= 45:
        stance = "偏弱回避"
    stop_loss_pct = float(signal.get("stop_loss_pct") or 0.06)
    take_profit_pct = float(signal.get("take_profit_pct") or 0.08)
    entry_price = round(latest_close * 0.99, 2) if latest_close and stance in {"候选买入", "偏强关注"} else None
    stop_loss = round(latest_close * (1.0 - stop_loss_pct), 2) if latest_close else None
    take_profit = round(latest_close * (1.0 + take_profit_pct), 2) if latest_close and stance in {"候选买入", "偏强关注"} else None
    supporting_factors = []
    contradicting_factors = []
    canonical_flags = dict(feature_metadata.get("flags") or signal.get("flags") or {})
    canonical_matched = list(feature_metadata.get("matched_signals") or signal.get("matched_signals") or [])
    for label in canonical_matched:
        if str(label).strip():
            supporting_factors.append(str(label))
    for label, matched in canonical_flags.items():
        if matched:
            supporting_factors.append(str(label))
        else:
            contradicting_factors.append(str(label))
    supporting_factors.extend(str(item) for item in list(features.get("evidence") or signal.get("evidence") or [])[:3])
    if factor_values.get("rsi") is not None and float(factor_values.get("rsi") or 0.0) > 75.0:
        contradicting_factors.append("RSI过热")
    if cross.get("threshold_gap") is not None and float(cross.get("threshold_gap") or 0.0) < 0:
        contradicting_factors.append("未达到当前策略入选阈值")
    evaluation_protocol = {
        "clock": ["T+5", "T+10", "T+20", "T+60"],
        "label_set": ["hit", "miss", "invalidated", "timeout", "not_triggered"],
        "benchmark_code": "sh.000300",
        "strategy_lens": strategy_name,
    }
    horizon20 = dict((scenario.get("horizons") or {}).get("T+20") or {})
    expected_interval = dict(horizon20.get("interval") or {})
    probability = float(horizon20.get("positive_return_probability") or 0.5)
    confidence = round(max(0.35, min(0.95, 0.4 + probability * 0.5 + (0.08 if selected_by_policy else 0.0))), 4)
    hypothesis_hash = stable_hash(
        {
            "snapshot_id": snapshot.snapshot_id,
            "policy_id": policy.policy_id,
            "stance": stance,
            "entry": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }
    )
    return ResearchHypothesis(
        hypothesis_id=f"hypothesis_{hypothesis_hash[:16]}",
        snapshot_id=snapshot.snapshot_id,
        policy_id=policy.policy_id,
        stance=stance,
        score=score,
        rank=cross.get("rank"),
        percentile=percentile_f if percentile is not None else None,
        selected_by_policy=selected_by_policy,
        entry_rule={
            "kind": "limit_pullback" if entry_price is not None else "observe_only",
            "price": entry_price,
            "source": strategy_display_name,
        },
        invalidation_rule={
            "kind": "stop_loss",
            "price": stop_loss,
            "source": policy.model_name,
        },
        de_risk_rule={
            "kind": "take_profit" if take_profit is not None else "reassess",
            "price": take_profit,
            "source": policy.model_name,
        },
        supporting_factors=list(dict.fromkeys(supporting_factors))[:8],
        contradicting_factors=list(dict.fromkeys(contradicting_factors))[:8],
        scenario_distribution=dict(scenario or {}),
        expected_return_interval=expected_interval,
        confidence=confidence,
        evaluation_protocol=evaluation_protocol,
        metadata={
            "strategy_name": strategy_name,
            "strategy_display_name": strategy_display_name,
            "latest_close": latest_close,
        },
    )
