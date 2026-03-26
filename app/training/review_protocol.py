from __future__ import annotations

from pathlib import Path
from typing import Any

from app.training.experiment_protocol import build_review_basis_window
from invest.contracts import EvalReport
from invest.shared.model_governance import normalize_strategy_family_name


FAILURE_SIGNATURE_CATALOG: dict[str, dict[str, str]] = {
    "trend_chase_failed": {
        "description": "bull 环境下追涨/追趋势后失败。",
    },
    "mean_revert_failed": {
        "description": "oscillation 环境下均值回归类判断失败。",
    },
    "late_exit": {
        "description": "复盘/风控提示已出现，但退出或收缩偏慢。",
    },
    "early_stopout": {
        "description": "小幅亏损且基准并未显著失效，疑似过早止损。",
    },
    "overexposed_in_bear": {
        "description": "bear 环境下风险暴露过重导致亏损。",
    },
    "weak_signal_entry": {
        "description": "信号质量不足仍然入场，最终形成亏损。",
    },
    "unclassified_loss": {
        "description": "当前字段不足以归入更具体的失败类型。",
    },
}

FAILURE_SUB_SIGNATURE_CATALOG: dict[str, dict[str, str]] = {
    "false_rebound_entry": {
        "description": "均值回归类在震荡中把局部反弹当成可回归拐点，入场过早。",
    },
    "chop_stopout": {
        "description": "震荡噪音触发止损或洗出，回归逻辑未必错，但仓位/节奏不匹配。",
    },
    "overcrowded_reversion_book": {
        "description": "回归类仓位铺得过满，在重复震荡里被组合层面拖累。",
    },
    "slow_reversion_timeout": {
        "description": "回归方向可能最终成立，但兑现太慢，当前持有窗口承接不住。",
    },
    "quality_trap_in_range": {
        "description": "价值质量类在震荡中落入质量陷阱，基本面优势未转化为价格确认。",
    },
    "defensive_lag": {
        "description": "价值质量类过于保守，亏损不大但系统性跑不过基准。",
    },
    "concentration_mismatch": {
        "description": "价值质量类持仓过于集中，少数失误就拖累整轮表现。",
    },
    "diluted_edge": {
        "description": "价值质量类持仓过散，把有限 alpha 稀释掉了。",
    },
    "oscillation_generic_loss": {
        "description": "震荡环境下出现亏损，但当前证据不足以稳定细分到更具体的家族模式。",
    },
}


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
    trade_history = _normalized_trade_history(record)
    metadata.update(
        {
            "model_name": model_name,
            "config_name": config_name,
            "research_feedback": research_feedback,
            "causal_diagnosis": causal_diagnosis,
            "similarity_summary": similarity_summary,
            "review_decision": review_decision,
            "ab_comparison": ab_comparison,
            "trade_history": trade_history,
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
    record["trade_history"] = trade_history
    record["trade_micro_attribution"] = _build_trade_micro_attribution(record)
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
                "trade_history": list(getattr(item, "trade_history", []) or []),
            },
            "research_feedback": research_feedback,
            "causal_diagnosis": dict(getattr(item, "causal_diagnosis", {}) or {}),
            "similarity_summary": dict(getattr(item, "similarity_summary", {}) or {}),
            "review_decision": dict(getattr(item, "review_decision", {}) or {}),
            "ab_comparison": dict(getattr(item, "ab_comparison", {}) or {}),
            "trade_history": list(getattr(item, "trade_history", []) or []),
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
    if (int(feedback.get("episode_count") or 0) or int(feedback.get("sample_count") or 0)) > 0:
        score += 1
    return score


def _normalized_trade_history(record: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = dict(record.get("metadata") or {})
    raw_trades = record.get("trade_history")
    if raw_trades is None:
        raw_trades = metadata.get("trade_history")
    normalized: list[dict[str, Any]] = []
    for item in list(raw_trades or []):
        trade = dict(item or {}) if isinstance(item, dict) else {}
        if trade:
            normalized.append(trade)
    return normalized


def _build_trade_micro_attribution(record: dict[str, Any]) -> dict[str, Any]:
    trades = _normalized_trade_history(record)
    sells = [
        trade
        for trade in trades
        if str(trade.get("action") or "").strip().upper() in {"SELL", "卖出"}
    ]
    losing_sells = [
        trade
        for trade in sells
        if _coerce_float(trade.get("pnl"), 0.0) < 0
    ]
    abs_losses = [abs(_coerce_float(trade.get("pnl"), 0.0)) for trade in losing_sells]
    total_abs_loss = sum(abs_losses)
    dominant_loss_share = (
        max(abs_losses) / total_abs_loss
        if total_abs_loss > 0 and abs_losses
        else 0.0
    )
    holding_days = [
        _coerce_int(trade.get("holding_days"), 0)
        for trade in losing_sells
        if _coerce_int(trade.get("holding_days"), 0) > 0
    ]
    avg_holding_days = (
        sum(holding_days) / len(holding_days)
        if holding_days
        else 0.0
    )
    stop_loss_exit_count = sum(
        1
        for trade in losing_sells
        if str(trade.get("exit_trigger") or "").strip().lower() == "stop_loss"
    )
    timeout_exit_count = sum(
        1
        for trade in losing_sells
        if "timeout" in str(trade.get("exit_reason") or "").strip().lower()
        or "hold" in str(trade.get("exit_reason") or "").strip().lower()
        or str(trade.get("exit_trigger") or "").strip().lower() == "max_hold_days"
    )
    small_loss_trade_count = sum(
        1
        for trade in losing_sells
        if 0 < abs(_coerce_float(trade.get("pnl_pct"), 0.0)) <= 2.5
    )
    material_loss_trade_count = sum(
        1
        for trade in losing_sells
        if abs(_coerce_float(trade.get("pnl_pct"), 0.0)) >= 4.0
    )
    unique_loss_codes = {
        str(trade.get("ts_code") or "").strip()
        for trade in losing_sells
        if str(trade.get("ts_code") or "").strip()
    }
    return {
        "trade_count": len(trades),
        "sell_trade_count": len(sells),
        "loss_trade_count": len(losing_sells),
        "unique_loss_codes": sorted(unique_loss_codes),
        "dominant_loss_share": round(dominant_loss_share, 4),
        "avg_holding_days": round(avg_holding_days, 2),
        "stop_loss_exit_count": stop_loss_exit_count,
        "timeout_exit_count": timeout_exit_count,
        "small_loss_trade_count": small_loss_trade_count,
        "material_loss_trade_count": material_loss_trade_count,
        "concentrated_loss_book": dominant_loss_share >= 0.55 and len(losing_sells) >= 2,
        "diffuse_loss_book": dominant_loss_share <= 0.45 and len(losing_sells) >= 3,
        "rapid_exit_loss_book": avg_holding_days > 0 and avg_holding_days <= 3.0,
        "slow_exit_loss_book": avg_holding_days >= 8.0,
    }


def _resolve_strategy_family(record: dict[str, Any]) -> str:
    metadata = dict(record.get("metadata") or {})
    candidates = [
        record.get("strategy_family"),
        metadata.get("strategy_family"),
        record.get("model_name"),
        metadata.get("model_name"),
        record.get("config_name"),
        metadata.get("config_name"),
    ]
    for raw in candidates:
        value = str(raw or "").strip()
        if not value:
            continue
        normalized = normalize_strategy_family_name(Path(value).stem)
        if normalized:
            return normalized
    return "unknown"


def _oscillation_failure_sub_signature(
    *,
    strategy_family: str,
    benchmark_passed: bool,
    loss_size: float,
    primary_driver: str,
    feedback_bias: str,
    review_applied: bool,
    evidence_score: int,
    trade_micro_attribution: dict[str, Any],
) -> tuple[str, list[str], str]:
    reason_codes = ["oscillation_regime", f"family_{strategy_family or 'unknown'}"]

    if strategy_family == "mean_reversion":
        if (
            int(trade_micro_attribution.get("stop_loss_exit_count") or 0) >= 2
            and bool(trade_micro_attribution.get("rapid_exit_loss_book"))
            and int(trade_micro_attribution.get("small_loss_trade_count") or 0) >= 2
        ):
            return (
                "chop_stopout",
                reason_codes + ["rapid_stopouts", "small_loss_cluster"],
                "多笔小亏且快速被 stop_loss 洗出，更像是震荡噪音造成的 chop stopout。",
            )
        if bool(trade_micro_attribution.get("diffuse_loss_book")) and int(
            trade_micro_attribution.get("loss_trade_count") or 0
        ) >= 3:
            return (
                "overcrowded_reversion_book",
                reason_codes + ["diffuse_loss_book", "crowded_positions"],
                "亏损分散在多只回归仓位上，说明组合展开过满而不是单笔判断偶发失误。",
            )
        if primary_driver == "regime_repeat_loss" and evidence_score >= 2:
            return (
                "overcrowded_reversion_book",
                reason_codes + ["regime_repeat_loss", "structured_evidence"],
                "同类回归亏损在震荡环境中重复出现，更像是组合铺得过满而不是单笔偶发失误。",
            )
        if bool(trade_micro_attribution.get("slow_exit_loss_book")) or int(
            trade_micro_attribution.get("timeout_exit_count") or 0
        ) > 0:
            return (
                "slow_reversion_timeout",
                reason_codes + ["slow_realization", "holding_window_timeout"],
                "回归判断可能并非方向错误，但持有窗口过长仍未兑现，说明节奏错配。",
            )
        if benchmark_passed and 0 < loss_size <= 1.0:
            return (
                "chop_stopout",
                reason_codes + ["small_loss", "benchmark_held"],
                "亏损不大且相对基准并未显著恶化，更像是震荡噪音把回归仓位洗出。",
            )
        if not benchmark_passed and (
            loss_size >= 1.0
            or int(trade_micro_attribution.get("material_loss_trade_count") or 0) >= 1
        ):
            return (
                "false_rebound_entry",
                reason_codes + ["benchmark_gap", "material_loss"],
                "震荡中的局部反弹被误判为可回归拐点，入场后继续走弱并显著落后基准。",
            )
        return (
            "slow_reversion_timeout",
            reason_codes + ["slow_realization"],
            "回归判断没有快速兑现，当前持有节奏对震荡回归的承接能力不足。",
        )

    if strategy_family == "value_quality":
        if bool(trade_micro_attribution.get("concentrated_loss_book")):
            return (
                "concentration_mismatch",
                reason_codes + ["concentrated_loss_book"],
                "亏损主要集中在少数持仓，说明当前集中度配置超过了震荡环境下的风格承载能力。",
            )
        if bool(trade_micro_attribution.get("diffuse_loss_book")) and int(
            trade_micro_attribution.get("loss_trade_count") or 0
        ) >= 4:
            return (
                "diluted_edge",
                reason_codes + ["diffuse_loss_book", "diluted_positions"],
                "亏损分散而单票不极端，更像是组合过散把有限的价值质量优势摊薄了。",
            )
        if primary_driver == "regime_repeat_loss" and loss_size >= 1.0:
            return (
                "concentration_mismatch",
                reason_codes + ["regime_repeat_loss", "material_loss"],
                "价值质量组合在震荡中被少数持仓反复拖累，说明当前集中度与风格承载能力不匹配。",
            )
        if not benchmark_passed and loss_size <= 0.8 and feedback_bias != "tighten_risk":
            return (
                "defensive_lag",
                reason_codes + ["benchmark_gap", "small_loss"],
                "亏损不大但持续跑输基准，更像是震荡中防守过重、收益兑现过慢。",
            )
        if not benchmark_passed and loss_size > 0.8:
            return (
                "quality_trap_in_range",
                reason_codes + ["benchmark_gap", "material_loss"],
                "标的看起来便宜或稳健，但震荡区间里没有获得资金确认，形成质量陷阱式亏损。",
            )
        if review_applied or benchmark_passed:
            return (
                "diluted_edge",
                reason_codes + ["diffused_book"],
                "组合更像是边际优势被摊薄，而不是单一风险暴露失控。",
            )

    return (
        "oscillation_generic_loss",
        reason_codes + ["insufficient_family_specific_structure"],
        "当前证据足以识别为震荡亏损，但还不足以稳定归入更具体的家族失败模式。",
    )


def build_failure_signature(record: dict[str, Any]) -> dict[str, Any]:
    is_profit = bool(record.get("is_profit", False))
    regime = str(record.get("regime") or "unknown").strip() or "unknown"
    return_pct = _coerce_float(record.get("return_pct"))
    benchmark_passed = bool(record.get("benchmark_passed", False))
    selection_mode = str(record.get("selection_mode") or "unknown").strip() or "unknown"
    plan_source = str(record.get("plan_source") or "unknown").strip() or "unknown"
    primary_driver = _primary_driver(record)
    feedback_bias = _feedback_bias(record)
    evidence_score = _evidence_support_score(record)
    review_applied = bool(record.get("review_applied", False))
    strategy_family = _resolve_strategy_family(record)
    trade_micro_attribution = dict(record.get("trade_micro_attribution") or {})
    loss_size = abs(return_pct) if return_pct < 0 else 0.0
    sub_label = ""
    sub_description = ""

    if is_profit:
        label = ""
        confidence = 0.0
        reason = "盈利周期不打 failure signature。"
        reason_codes = ["profit_cycle"]
    elif regime == "bear" and loss_size > 0:
        label = "overexposed_in_bear"
        confidence = 0.74 if primary_driver == "regime_repeat_loss" else 0.68
        reason = "bear 环境下出现亏损，并伴随风险收紧或重复亏损证据。"
        reason_codes = ["bear_regime", "loss_cycle"]
        if feedback_bias == "tighten_risk":
            confidence += 0.08
            reason_codes.append("tighten_risk_feedback")
        if primary_driver == "regime_repeat_loss":
            reason_codes.append("regime_repeat_loss")
        if loss_size >= 1.0:
            confidence += 0.04
            reason_codes.append("material_loss")
    elif regime == "bull" and loss_size > 0 and (
        plan_source in {"meeting", "llm"} or selection_mode.startswith("meeting")
    ):
        label = "trend_chase_failed"
        confidence = 0.66
        reason = "bull 环境下由 meeting/LLM 主导的进攻性决策出现亏损。"
        reason_codes = ["bull_regime", "aggressive_plan_source", "loss_cycle"]
    elif regime == "oscillation" and loss_size > 0:
        label = "mean_revert_failed"
        confidence = 0.62
        sub_label, sub_reason_codes, sub_reason = _oscillation_failure_sub_signature(
            strategy_family=strategy_family,
            benchmark_passed=benchmark_passed,
            loss_size=loss_size,
            primary_driver=primary_driver,
            feedback_bias=feedback_bias,
            review_applied=review_applied,
            evidence_score=evidence_score,
            trade_micro_attribution=trade_micro_attribution,
        )
        sub_description = str(
            FAILURE_SUB_SIGNATURE_CATALOG.get(sub_label, {}).get("description") or ""
        )
        reason = (
            "oscillation 环境下的逆向/回归判断未兑现。"
            if not sub_reason
            else f"oscillation 环境下的逆向/回归判断未兑现；{sub_reason}"
        )
        reason_codes = ["oscillation_regime", "loss_cycle"] + list(sub_reason_codes)
    elif feedback_bias == "tighten_risk" and not review_applied and loss_size >= 1.0:
        label = "late_exit"
        confidence = 0.58
        reason = "风险收紧信号已出现，但本轮仍形成较明显亏损。"
        reason_codes = ["tighten_risk_feedback", "review_not_applied", "loss_cycle"]
    elif benchmark_passed and 0 < loss_size <= 1.0:
        label = "early_stopout"
        confidence = 0.54
        reason = "亏损幅度较小且基准未失效，疑似过早止损/过早离场。"
        reason_codes = ["small_loss", "benchmark_held", "loss_cycle"]
    elif not benchmark_passed:
        label = "weak_signal_entry"
        confidence = 0.56
        reason = "未跑赢基准且形成亏损，说明入场信号质量偏弱。"
        reason_codes = ["benchmark_gap", "loss_cycle"]
        if evidence_score <= 1:
            confidence += 0.04
            reason_codes.append("low_structured_evidence")
    else:
        label = "unclassified_loss"
        confidence = 0.35
        reason = "当前可用字段不足以稳定归类到更具体的失败模式。"
        reason_codes = ["loss_cycle", "insufficient_structure"]

    confidence = round(min(0.95, confidence), 2)
    return {
        "schema_version": "failure_signature.v1",
        "label": label,
        "description": str(FAILURE_SIGNATURE_CATALOG.get(label, {}).get("description") or ""),
        "confidence": confidence,
        "reason": reason,
        "reason_codes": reason_codes,
        "return_direction": "profit" if is_profit else "loss",
        "benchmark_passed": benchmark_passed,
        "strategy_family": strategy_family,
        "sub_label": sub_label,
        "sub_description": sub_description,
        "trade_micro_attribution": trade_micro_attribution,
        "primary_driver": primary_driver,
        "feedback_bias": feedback_bias,
        "regime": regime,
        "selection_mode": selection_mode,
        "plan_source": plan_source,
        "evidence_score": evidence_score,
    }


def _failure_signature(record: dict[str, Any]) -> dict[str, Any]:
    return build_failure_signature(record)


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
    current_label = str(dict(current_result.get("failure_signature") or {}).get("label") or "")
    candidate_label = str(dict(candidate.get("failure_signature") or {}).get("label") or "")
    if (
        current_label
        and candidate_label
        and current_label != "unclassified_loss"
        and candidate_label != "unclassified_loss"
        and candidate_label != current_label
    ):
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
