from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Callable

import numpy as np
from invest.shared.model_governance import (
    DEFAULT_FREEZE_GATE_POLICY,
    normalize_freeze_gate_policy,
    normalize_research_feedback_gate_policy,
    resolve_model_governance_matrix,
)
from app.training.review_protocol import (
    FAILURE_SIGNATURE_CATALOG,
    FAILURE_SUB_SIGNATURE_CATALOG,
    build_failure_signature,
)


_DEFAULT_OPTIMIZATION_FEEDBACK_GATE = {
    "min_episode_count": 5,
    "blocked_biases": ["tighten_risk", "recalibrate_probability"],
    "max_brier_like_direction_score": 0.28,
    "horizons": {
        "default": {
            "min_hit_rate": 0.45,
            "max_invalidation_rate": 0.35,
            "min_interval_hit_rate": 0.40,
        }
    },
}

_DEFAULT_FREEZE_FEEDBACK_GATE = dict(DEFAULT_FREEZE_GATE_POLICY.get("research_feedback") or {})

TRAINING_AUDIT_SCHEMA_VERSION = "training.audit_summary.v1"
TRAINING_AUDIT_TERMINOLOGY_VERSION = "2026-03-16.prephase1_closure_v1"

_NO_PROPOSAL_REASON_CATALOG: dict[str, str] = {
    "proposal_bundle_missing": "该 cycle 未落盘 proposal bundle，说明学习层产物没有进入标准候选审计路径。",
    "candidate_scope_empty": "本轮虽然有 proposal bundle，但没有 candidate scope 的提案，通常意味着只有 safety/非候选类记录。",
    "observe_only_profitable_cycle": "本轮盈利，系统按 observe-only 纪律不对 active 行为参数发起候选提案。",
    "research_feedback_not_actionable": "研究反馈存在，但证据 gate 通过、冷却中，或未形成 candidate scope 的调参建议。",
    "review_observe_only": "review 完成了复盘，但没有形成参数/权重类 candidate proposal。",
    "optimizer_observe_only": "优化/分析链路有运行，但只形成观察结论，没有形成 candidate proposal。",
    "no_learning_adjustments_requested": "本轮没有任何学习层模块请求 candidate proposal。",
}

_AUDIT_TERM_GUIDE: dict[str, dict[str, Any]] = {
    "freeze_applied": {
        "description": "训练报告层面的冻结状态，表示控制器是否进入 freeze 状态。",
    },
    "is_frozen": {
        "legacy_alias_of": "freeze_applied",
        "description": "历史字段，语义同 freeze_applied；它不是 cycle 内 runtime snapshot 已冻结的意思。",
    },
    "active_pending_candidate_divergence_rate": {
        "description": "active config 与 pending candidate 并存的周期占比，用于衡量候选悬而未决的治理摩擦。",
    },
    "active_candidate_drift_rate": {
        "legacy_alias_of": "active_pending_candidate_divergence_rate",
        "description": "历史命名。这里不是 live runtime 被非法篡改，而是 active 与 pending candidate 并存的占比。",
    },
    "bundle_proposal_count": {
        "description": "proposal bundle 中记录的总 proposal 数，包含所有 scope。",
    },
    "requested_candidate_proposal_count": {
        "description": "真正送入 candidate governance gate 的 proposal 数，只统计 candidate scope。",
    },
    "requested_proposal_count": {
        "legacy_alias_of": "requested_candidate_proposal_count",
        "description": "报告中的 proposal 请求数，现统一表示 candidate scope proposal 数。",
    },
    "episode_count": {
        "description": "独立决策 episode 数，是 research / feedback gate 的正式证据口径。",
    },
    "sample_count": {
        "legacy_alias_of": "episode_count",
        "description": "历史口径兼容字段，不再作为独立证据强度的正式解释。",
    },
    "failure_signature_label": {
        "description": "Phase 1 v1 的失败主标签；仅对亏损周期打标签，盈利周期留空。",
    },
    "failure_signature_sub_label": {
        "description": "Phase 1 v1 的细粒度失败子标签；当前重点覆盖 mean_reversion/value_quality 在 oscillation 下的重复失败模式。",
    },
    "trade_micro_attribution": {
        "description": "从真实 trade_history 提炼的微观归因摘要，用于把 failure signature 从 cycle-level 推进到交易级结构判断。",
    },
    "regime_failure_dashboard": {
        "description": "按 bull/bear/oscillation 分层的失败看板，用于观察重复损失模式。",
    },
    "regime_discipline_dashboard": {
        "description": "按 regime 聚合的风险预算 overlay 与 hard filter 审计摘要，用于确认执行侧约束是否真正生效。",
    },
    "strategy_family": {
        "description": "策略族标识，当前用于把 risk budget layering 从单模型配置提升为 family-aware 基线预算。",
    },
    "budget_layering": {
        "description": "regime runtime profile 中的风险预算分层信息，区分 strategy family baseline、model budget override 与行为类 overlay。",
    },
    "family_budget_correction": {
        "description": "family-aware 风险预算修正层；基于最近重复 failure sub-signature 对 position_size / cash_reserve / max_positions 做小步修正。",
    },
    "selection_intercepts": {
        "description": "单个 cycle 的 hard filter 拦截详情，记录被拦截原因、代码与风险预算收缩结果。",
    },
    "entry_threshold_reason": {
        "description": "selection hard filter 中 entry_threshold 是否执行的解释字段；用于区分 overlay 未激活、模型已在上游消费阈值、或模型声明该阈值不适合 post-selection veto。",
    },
    "entry_threshold_policy_mode": {
        "description": "模型声明的 entry_threshold 语义模式，如 model_managed 或 upstream_signal_filter，用于解释阈值由谁负责消费。",
    },
    "suggestion_adoption_summary": {
        "description": "proposal 级建议的采纳与 effect 跟踪摘要；v1 会在 effect window 结束后给出 improved / worsened / neutral / inconclusive 结果。",
    },
    "adoption_status": {
        "description": "建议当前的采纳状态，如 queued、adopted_to_candidate、deferred_pending_candidate、rejected_by_proposal_gate。",
    },
    "effect_status": {
        "description": "建议后验跟踪状态；当前阶段主要记录 pending / pending_adoption / not_applicable。",
    },
}


def _merge_policy(defaults: dict[str, Any], override: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(defaults or {})
    patch = dict(override or {})
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_policy(dict(merged.get(key) or {}), value)
        else:
            merged[key] = value
    return merged


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _record_field(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def build_training_audit_semantics() -> dict[str, Any]:
    return {
        "schema_version": TRAINING_AUDIT_SCHEMA_VERSION,
        "terminology_version": TRAINING_AUDIT_TERMINOLOGY_VERSION,
        "metric_terms": {
            key: dict(value)
            for key, value in _AUDIT_TERM_GUIDE.items()
        },
        "no_proposal_reason_catalog": dict(_NO_PROPOSAL_REASON_CATALOG),
    }


def _record_bool(item: Any, key: str, default: bool = False) -> bool:
    value = _record_field(item, key, default)
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _record_dict(item: Any, key: str) -> dict[str, Any]:
    value = _record_field(item, key, {})
    return dict(value or {}) if isinstance(value, dict) else dict(value or {})


def _record_list(item: Any, key: str) -> list[Any]:
    value = _record_field(item, key, [])
    return list(value or [])


def _proposal_bundle_candidate_count(item: Any, gate: dict[str, Any] | None = None) -> int:
    proposal_bundle = _record_dict(item, "proposal_bundle")
    proposal_summary = dict((gate or {}).get("proposal_summary") or {})
    if proposal_summary:
        requested = int(proposal_summary.get("requested_proposal_count") or 0)
        if requested > 0:
            return requested
    proposals = [
        dict(entry or {})
        for entry in list(proposal_bundle.get("proposals") or [])
    ]
    if proposals:
        return sum(
            1
            for proposal in proposals
            if str(dict(proposal).get("target_scope") or "candidate") == "candidate"
        )
    return int(
        proposal_bundle.get("proposal_count")
        or len(list(proposal_bundle.get("proposal_ids") or []))
        or 0
    )


def _proposal_bundle_total_count(item: Any) -> int:
    proposal_bundle = _record_dict(item, "proposal_bundle")
    proposals = list(proposal_bundle.get("proposals") or [])
    if proposals:
        return len(proposals)
    return int(
        proposal_bundle.get("proposal_count")
        or len(list(proposal_bundle.get("proposal_ids") or []))
        or 0
    )


def _has_candidate_adjustments(event: dict[str, Any]) -> bool:
    payload = dict(event or {})
    applied_change = dict(payload.get("applied_change") or {})
    for key in (
        "queued_params",
        "queued_scoring",
        "queued_param_adjustments",
        "queued_agent_weight_adjustments",
        "params",
        "scoring",
        "agent_weights",
    ):
        if dict(applied_change.get(key) or {}):
            return True
    return bool(list(applied_change.get("proposal_refs") or []))


def classify_no_proposal_reason(item: Any) -> str:
    gate = _latest_proposal_gate_payload(item)
    if _proposal_bundle_candidate_count(item, gate) > 0:
        return ""

    proposal_bundle = _record_dict(item, "proposal_bundle")
    if not proposal_bundle:
        return "proposal_bundle_missing"

    if _proposal_bundle_total_count(item) > 0:
        return "candidate_scope_empty"

    execution_snapshot = _record_dict(item, "execution_snapshot")
    is_profit = _record_bool(item, "is_profit", False) or bool(
        execution_snapshot.get("is_profit", False)
    )
    benchmark_passed = _record_bool(item, "benchmark_passed", False) or bool(
        execution_snapshot.get("benchmark_passed", False)
    )
    if is_profit and benchmark_passed:
        return "observe_only_profitable_cycle"

    research_feedback_optimization = _record_dict(item, "research_feedback_optimization")
    if research_feedback_optimization and not bool(
        research_feedback_optimization.get("triggered", False)
    ):
        return "research_feedback_not_actionable"

    review_decision = _record_dict(item, "review_decision")
    if review_decision:
        review_has_adjustments = bool(
            dict(review_decision.get("param_adjustments") or {})
            or dict(review_decision.get("agent_weight_adjustments") or {})
        )
        if not review_has_adjustments:
            return "review_observe_only"

    optimization_events = [
        dict(event or {})
        for event in _record_list(item, "optimization_events")
    ]
    observed_optimizer_stages = {
        "research_feedback",
        "llm_analysis",
        "evolution_engine",
    }
    if any(
        str(event.get("stage") or "") in observed_optimizer_stages and not _has_candidate_adjustments(event)
        for event in optimization_events
    ):
        return "optimizer_observe_only"

    return "no_learning_adjustments_requested"


def build_self_assessment_snapshot(snapshot_factory: Callable[..., Any], cycle_result: Any, cycle_dict: dict[str, Any]) -> Any:
    return snapshot_factory(
        cycle_id=cycle_result.cycle_id,
        cutoff_date=cycle_result.cutoff_date,
        regime=cycle_dict.get("regime", "unknown"),
        plan_source=cycle_dict.get("plan_source", "unknown"),
        return_pct=cycle_result.return_pct,
        is_profit=cycle_result.is_profit,
        sharpe_ratio=float(cycle_dict.get("sharpe_ratio", 0.0) or 0.0),
        max_drawdown=float(cycle_dict.get("max_drawdown", 0.0) or 0.0),
        excess_return=float(cycle_dict.get("excess_return", 0.0) or 0.0),
        benchmark_passed=bool(cycle_dict.get("benchmark_passed", False)),
    )


def rolling_self_assessment(assessment_history: list[Any], freeze_total_cycles: int, window: int | None = None) -> dict[str, Any]:
    if not assessment_history:
        return {}

    w = max(1, window or freeze_total_cycles)
    recent = assessment_history[-w:]
    n = len(recent)
    profit_count = sum(1 for s in recent if s.is_profit)

    return {
        "window": n,
        "profit_count": profit_count,
        "win_rate": profit_count / n if n > 0 else 0.0,
        "avg_return": float(np.mean([s.return_pct for s in recent])) if recent else 0.0,
        "avg_sharpe": float(np.mean([s.sharpe_ratio for s in recent])) if recent else 0.0,
        "avg_max_drawdown": float(np.mean([s.max_drawdown for s in recent])) if recent else 0.0,
        "avg_excess_return": float(np.mean([s.excess_return for s in recent])) if recent else 0.0,
        "benchmark_pass_rate": (sum(1 for s in recent if s.benchmark_passed) / n if n > 0 else 0.0),
    }


def build_governance_metrics(cycle_history: list[Any]) -> dict[str, Any]:
    total_cycles = len(cycle_history)
    promotion_attempt_count = 0
    promotion_applied_count = 0
    promotion_awaiting_gate_count = 0
    active_candidate_drift_count = 0
    candidate_pending_count = 0
    override_pending_count = 0
    rejected_candidate_count = 0
    active_stage_count = 0

    for item in cycle_history:
        promotion_record = dict(_record_field(item, "promotion_record", {}) or {})
        lineage_record = dict(_record_field(item, "lineage_record", {}) or {})
        if bool(promotion_record.get("attempted", False)):
            promotion_attempt_count += 1
        if str(promotion_record.get("gate_status") or "") == "applied_to_active":
            promotion_applied_count += 1
        if str(promotion_record.get("gate_status") or "") == "awaiting_gate":
            promotion_awaiting_gate_count += 1
        active_config_ref = str(lineage_record.get("active_config_ref") or "")
        candidate_config_ref = str(lineage_record.get("candidate_config_ref") or "")
        if candidate_config_ref and candidate_config_ref != active_config_ref:
            active_candidate_drift_count += 1
        deployment_stage = str(lineage_record.get("deployment_stage") or "")
        lineage_status = str(lineage_record.get("lineage_status") or "")
        if deployment_stage == "candidate" or lineage_status == "candidate_pending":
            candidate_pending_count += 1
        if deployment_stage == "override" or lineage_status == "override_pending":
            override_pending_count += 1
        if deployment_stage == "active":
            active_stage_count += 1
        if lineage_status in {"candidate_expired", "candidate_pruned", "override_expired"}:
            rejected_candidate_count += 1

    denominator = total_cycles or 1
    return {
        "total_cycles": total_cycles,
        "promotion_attempt_count": promotion_attempt_count,
        "promotion_applied_count": promotion_applied_count,
        "promotion_awaiting_gate_count": promotion_awaiting_gate_count,
        "active_pending_candidate_divergence_count": active_candidate_drift_count,
        "active_pending_candidate_divergence_rate": active_candidate_drift_count / denominator,
        "active_candidate_drift_count": active_candidate_drift_count,
        "active_candidate_drift_rate": active_candidate_drift_count / denominator,
        "candidate_pending_count": candidate_pending_count,
        "candidate_pending_rate": candidate_pending_count / denominator,
        "override_pending_count": override_pending_count,
        "override_pending_rate": override_pending_count / denominator,
        "rejected_candidate_count": rejected_candidate_count,
        "active_stage_count": active_stage_count,
    }


def _latest_proposal_gate_payload(item: Any) -> dict[str, Any]:
    optimization_events = list(_record_field(item, "optimization_events", []) or [])
    for event in reversed(optimization_events):
        payload = dict(event or {})
        evidence = dict(payload.get("evidence") or {})
        proposal_gate = dict(evidence.get("proposal_gate") or {})
        if proposal_gate:
            return proposal_gate
    return {}


def build_proposal_gate_summary(cycle_history: list[Any]) -> dict[str, Any]:
    cycles_with_proposal_bundle = 0
    cycles_with_requested_proposals = 0
    cycles_without_requested_proposals = 0
    cycles_with_requested_proposals_without_gate = 0
    cycles_with_all_proposals_blocked = 0
    cycles_with_pending_candidate_skip = 0
    cycles_with_gate = 0
    cycles_with_blocked_proposals = 0
    candidate_build_cycles = 0
    candidate_build_skipped_cycles = 0
    requested_proposal_count = 0
    approved_proposal_count = 0
    blocked_proposal_count = 0
    partially_blocked_proposal_count = 0
    block_reason_counts: dict[str, int] = {}
    approved_drift_ratios: list[float] = []
    approved_param_drift_ratios: list[float] = []
    approved_scoring_drift_ratios: list[float] = []
    approved_agent_weight_drift_ratios: list[float] = []
    no_proposal_reason_counts: dict[str, int] = {}
    no_proposal_cycles: list[dict[str, Any]] = []
    bundle_proposal_count = 0

    for item in cycle_history:
        proposal_bundle = dict(_record_field(item, "proposal_bundle", {}) or {})
        gate = _latest_proposal_gate_payload(item)
        proposal_count = _proposal_bundle_candidate_count(item, gate)
        bundle_proposal_count += _proposal_bundle_total_count(item)
        if proposal_bundle:
            cycles_with_proposal_bundle += 1
            if proposal_count > 0:
                cycles_with_requested_proposals += 1
            else:
                cycles_without_requested_proposals += 1
                reason = classify_no_proposal_reason(item)
                if reason:
                    no_proposal_reason_counts[reason] = no_proposal_reason_counts.get(reason, 0) + 1
                    no_proposal_cycles.append(
                        {
                            "cycle_id": int(_record_field(item, "cycle_id", 0) or 0),
                            "cutoff_date": str(_record_field(item, "cutoff_date", "") or ""),
                            "reason": reason,
                            "description": str(_NO_PROPOSAL_REASON_CATALOG.get(reason) or ""),
                        }
                    )
        else:
            reason = classify_no_proposal_reason(item)
            if reason:
                no_proposal_reason_counts[reason] = no_proposal_reason_counts.get(reason, 0) + 1
                no_proposal_cycles.append(
                    {
                        "cycle_id": int(_record_field(item, "cycle_id", 0) or 0),
                        "cutoff_date": str(_record_field(item, "cutoff_date", "") or ""),
                        "reason": reason,
                        "description": str(_NO_PROPOSAL_REASON_CATALOG.get(reason) or ""),
                    }
                )

        latest_stage = ""
        latest_decision: dict[str, Any] = {}
        optimization_events = list(_record_field(item, "optimization_events", []) or [])
        for event in reversed(optimization_events):
            payload = dict(event or {})
            evidence = dict(payload.get("evidence") or {})
            if dict(evidence.get("proposal_gate") or {}):
                latest_stage = str(payload.get("stage") or "")
                latest_decision = dict(payload.get("decision") or {})
                if latest_stage == "candidate_build":
                    candidate_build_cycles += 1
                elif latest_stage == "candidate_build_skipped":
                    candidate_build_skipped_cycles += 1
                    if str(latest_decision.get("skip_reason") or "") == "proposal_governance_rejected":
                        cycles_with_all_proposals_blocked += 1
                    if str(latest_decision.get("pending_candidate_ref") or ""):
                        cycles_with_pending_candidate_skip += 1
                break

        if proposal_count > 0 and not gate:
            cycles_with_requested_proposals_without_gate += 1
        if not gate:
            continue
        cycles_with_gate += 1
        proposal_summary = dict(gate.get("proposal_summary") or {})
        requested_proposal_count += int(proposal_summary.get("requested_proposal_count") or 0)
        approved_proposal_count += int(proposal_summary.get("approved_proposal_count") or 0)
        blocked_proposal_count += int(proposal_summary.get("blocked_proposal_count") or 0)
        partially_blocked_proposal_count += int(
            proposal_summary.get("partially_blocked_proposal_count") or 0
        )
        if int(proposal_summary.get("blocked_proposal_count") or 0) > 0:
            cycles_with_blocked_proposals += 1
        for reason, count in dict(proposal_summary.get("block_reason_counts") or {}).items():
            label = str(reason or "unknown").strip() or "unknown"
            block_reason_counts[label] = block_reason_counts.get(label, 0) + int(count or 0)
        drift_summary = dict(gate.get("drift_summary") or {})
        for scope_name, bucket in (
            ("params", dict(drift_summary.get("approved_params") or {})),
            ("scoring", dict(drift_summary.get("approved_scoring") or {})),
            ("agent_weights", dict(drift_summary.get("approved_agent_weights") or {})),
        ):
            for metric in bucket.values():
                ratio = _safe_float(dict(metric or {}).get("candidate_drift_ratio_vs_baseline"))
                if ratio is None:
                    continue
                approved_drift_ratios.append(ratio)
                if scope_name == "params":
                    approved_param_drift_ratios.append(ratio)
                elif scope_name == "scoring":
                    approved_scoring_drift_ratios.append(ratio)
                elif scope_name == "agent_weights":
                    approved_agent_weight_drift_ratios.append(ratio)

    total_cycles = len(cycle_history)
    denominator = cycles_with_gate or 1
    top_block_reasons = [
        reason
        for reason, _count in sorted(
            block_reason_counts.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )[:3]
    ]
    return {
        "total_cycles": total_cycles,
        "cycles_with_proposal_bundle": cycles_with_proposal_bundle,
        "cycles_with_requested_proposals": cycles_with_requested_proposals,
        "cycles_without_requested_proposals": cycles_without_requested_proposals,
        "bundle_proposal_count": bundle_proposal_count,
        "requested_candidate_proposal_count": requested_proposal_count,
        "cycles_with_requested_proposals_without_gate": cycles_with_requested_proposals_without_gate,
        "cycles_with_gate": cycles_with_gate,
        "cycles_with_blocked_proposals": cycles_with_blocked_proposals,
        "cycles_with_all_proposals_blocked": cycles_with_all_proposals_blocked,
        "cycles_with_pending_candidate_skip": cycles_with_pending_candidate_skip,
        "cycles_with_candidate_generated": candidate_build_cycles,
        "candidate_build_cycles": candidate_build_cycles,
        "candidate_build_skipped_cycles": candidate_build_skipped_cycles,
        "requested_proposal_count": requested_proposal_count,
        "approved_proposal_count": approved_proposal_count,
        "blocked_proposal_count": blocked_proposal_count,
        "partially_blocked_proposal_count": partially_blocked_proposal_count,
        "proposal_block_rate": blocked_proposal_count / max(requested_proposal_count, 1),
        "cycle_block_rate": cycles_with_blocked_proposals / denominator,
        "no_proposal_reason_counts": no_proposal_reason_counts,
        "no_proposal_cycles": no_proposal_cycles,
        "block_reason_counts": block_reason_counts,
        "top_block_reasons": top_block_reasons,
        "avg_candidate_drift_ratio_vs_baseline": float(np.mean(approved_drift_ratios))
        if approved_drift_ratios
        else 0.0,
        "max_candidate_drift_ratio_vs_baseline": max(approved_drift_ratios) if approved_drift_ratios else 0.0,
        "avg_param_drift_ratio_vs_baseline": float(np.mean(approved_param_drift_ratios))
        if approved_param_drift_ratios
        else 0.0,
        "max_param_drift_ratio_vs_baseline": max(approved_param_drift_ratios)
        if approved_param_drift_ratios
        else 0.0,
        "avg_scoring_drift_ratio_vs_baseline": float(np.mean(approved_scoring_drift_ratios))
        if approved_scoring_drift_ratios
        else 0.0,
        "max_scoring_drift_ratio_vs_baseline": max(approved_scoring_drift_ratios)
        if approved_scoring_drift_ratios
        else 0.0,
        "avg_agent_weight_drift_ratio_vs_baseline": float(np.mean(approved_agent_weight_drift_ratios))
        if approved_agent_weight_drift_ratios
        else 0.0,
        "max_agent_weight_drift_ratio_vs_baseline": max(approved_agent_weight_drift_ratios)
        if approved_agent_weight_drift_ratios
        else 0.0,
    }


def build_suggestion_adoption_summary(cycle_history: list[Any]) -> dict[str, Any]:
    total_cycles = len(cycle_history)
    cycles_with_tracking = 0
    suggestion_count = 0
    adoption_status_counts: dict[str, int] = {}
    effect_status_counts: dict[str, int] = {}
    pending_effect_suggestions: list[dict[str, Any]] = []
    evaluated_effect_suggestions: list[dict[str, Any]] = []

    for item in cycle_history:
        proposal_bundle = _record_dict(item, "proposal_bundle")
        proposals = [
            dict(entry or {})
            for entry in list(proposal_bundle.get("proposals") or [])
            if dict(entry or {})
        ]
        if not proposals:
            continue
        cycles_with_tracking += 1
        for proposal in proposals:
            suggestion_count += 1
            adoption_status = str(proposal.get("adoption_status") or "queued").strip() or "queued"
            effect_status = str(proposal.get("effect_status") or "pending_adoption").strip() or "pending_adoption"
            adoption_status_counts[adoption_status] = adoption_status_counts.get(adoption_status, 0) + 1
            effect_status_counts[effect_status] = effect_status_counts.get(effect_status, 0) + 1

            if effect_status == "pending" and len(pending_effect_suggestions) < 5:
                effect_window = dict(proposal.get("effect_window") or {})
                pending_effect_suggestions.append(
                    {
                        "suggestion_id": str(proposal.get("suggestion_id") or ""),
                        "proposal_id": str(proposal.get("proposal_id") or ""),
                        "source": str(proposal.get("source") or "unknown"),
                        "suggestion_text": str(proposal.get("suggestion_text") or ""),
                        "evaluation_after_cycle_id": int(
                            effect_window.get("evaluation_after_cycle_id")
                            or effect_window.get("end_cycle_id")
                            or 0
                        ),
                        "effect_target_metrics": [
                            str(metric).strip()
                            for metric in list(proposal.get("effect_target_metrics") or [])
                            if str(metric).strip()
                        ],
                    }
                )
            if effect_status in {"improved", "worsened", "neutral", "inconclusive"} and len(
                evaluated_effect_suggestions
            ) < 5:
                effect_result = dict(proposal.get("effect_result") or {})
                evaluated_effect_suggestions.append(
                    {
                        "suggestion_id": str(proposal.get("suggestion_id") or ""),
                        "proposal_id": str(proposal.get("proposal_id") or ""),
                        "source": str(proposal.get("source") or "unknown"),
                        "effect_status": effect_status,
                        "summary": str(effect_result.get("summary") or ""),
                    }
                )

    return {
        "schema_version": "training.suggestion_adoption_summary.v1",
        "total_cycles": total_cycles,
        "cycles_with_tracking": cycles_with_tracking,
        "suggestion_count": suggestion_count,
        "adoption_status_counts": adoption_status_counts,
        "effect_status_counts": effect_status_counts,
        "adopted_suggestion_count": int(adoption_status_counts.get("adopted_to_candidate", 0) or 0),
        "deferred_suggestion_count": int(
            adoption_status_counts.get("deferred_pending_candidate", 0) or 0
        ),
        "rejected_suggestion_count": int(
            adoption_status_counts.get("rejected_by_proposal_gate", 0) or 0
        ),
        "queued_suggestion_count": int(adoption_status_counts.get("queued", 0) or 0),
        "pending_effect_count": int(effect_status_counts.get("pending", 0) or 0),
        "pending_adoption_count": int(effect_status_counts.get("pending_adoption", 0) or 0),
        "completed_evaluation_count": sum(
            int(effect_status_counts.get(status, 0) or 0)
            for status in ("improved", "worsened", "neutral", "inconclusive")
        ),
        "improved_suggestion_count": int(effect_status_counts.get("improved", 0) or 0),
        "worsened_suggestion_count": int(effect_status_counts.get("worsened", 0) or 0),
        "neutral_suggestion_count": int(effect_status_counts.get("neutral", 0) or 0),
        "inconclusive_suggestion_count": int(effect_status_counts.get("inconclusive", 0) or 0),
        "pending_effect_suggestions": pending_effect_suggestions,
        "evaluated_effect_suggestions": evaluated_effect_suggestions,
    }


def build_realism_summary(cycle_history: list[Any]) -> dict[str, Any]:
    metrics = [
        dict(_record_field(item, "realism_metrics", {}) or {})
        for item in cycle_history
        if dict(_record_field(item, "realism_metrics", {}) or {})
    ]
    if not metrics:
        return {
            "total_cycles": len(cycle_history),
            "cycles_with_realism_metrics": 0,
            "avg_trade_amount": 0.0,
            "avg_turnover_rate": 0.0,
            "avg_holding_days": 0.0,
            "high_turnover_trade_count": 0,
        }

    avg_trade_amounts = [
        value
        for value in (_safe_float(item.get("avg_trade_amount")) for item in metrics)
        if value is not None
    ]
    avg_turnover_rates = [
        value
        for value in (_safe_float(item.get("avg_turnover_rate")) for item in metrics)
        if value is not None
    ]
    avg_holding_days = [
        value
        for value in (_safe_float(item.get("avg_holding_days")) for item in metrics)
        if value is not None
    ]

    return {
        "total_cycles": len(cycle_history),
        "cycles_with_realism_metrics": len(metrics),
        "avg_trade_amount": float(np.mean(avg_trade_amounts)) if avg_trade_amounts else 0.0,
        "avg_turnover_rate": float(np.mean(avg_turnover_rates)) if avg_turnover_rates else 0.0,
        "avg_holding_days": float(np.mean(avg_holding_days)) if avg_holding_days else 0.0,
        "high_turnover_trade_count": int(sum(int(item.get("high_turnover_trade_count", 0) or 0) for item in metrics)),
    }


def _empty_regime_failure_bucket(regime: str) -> dict[str, Any]:
    return {
        "regime": regime,
        "total_cycles": 0,
        "profit_cycles": 0,
        "loss_cycles": 0,
        "avg_return_pct": 0.0,
        "avg_loss_return_pct": 0.0,
        "benchmark_fail_cycles": 0,
        "benchmark_fail_rate": 0.0,
        "failure_signature_counts": {},
        "failure_sub_signature_counts": {},
        "top_failure_signature": "",
        "top_failure_signature_description": "",
        "top_failure_sub_signature": "",
        "top_failure_sub_signature_description": "",
        "negative_contribution_pct": 0.0,
    }


def _record_regime(item: Any) -> str:
    routing = _record_dict(item, "routing_decision")
    audit_tags = _record_dict(item, "audit_tags")
    regime = str(
        routing.get("regime")
        or audit_tags.get("routing_regime")
        or _record_field(item, "regime", "")
        or "unknown"
    ).strip()
    return regime or "unknown"


def _record_failure_signature(item: Any) -> dict[str, Any]:
    existing = _record_dict(item, "failure_signature")
    if existing.get("schema_version"):
        return existing
    payload = {
        "cycle_id": int(_record_field(item, "cycle_id", 0) or 0),
        "return_pct": _safe_float(_record_field(item, "return_pct", 0.0)) or 0.0,
        "is_profit": _record_bool(item, "is_profit", False),
        "benchmark_passed": _record_bool(item, "benchmark_passed", False),
        "selection_mode": str(_record_field(item, "selection_mode", "unknown") or "unknown"),
        "plan_source": str(_record_field(item, "plan_source", "unknown") or "unknown"),
        "review_applied": _record_bool(item, "review_applied", False),
        "regime": _record_regime(item),
        "research_feedback": _record_dict(item, "research_feedback"),
        "causal_diagnosis": _record_dict(item, "causal_diagnosis"),
        "similarity_summary": _record_dict(item, "similarity_summary"),
        "trade_history": _record_list(item, "trade_history"),
        "strategy_family": str(_record_field(item, "strategy_family", "") or ""),
        "model_name": str(_record_field(item, "model_name", "") or ""),
        "metadata": {
            "strategy_family": str(_record_field(item, "strategy_family", "") or ""),
            "model_name": str(_record_field(item, "model_name", "") or ""),
            "config_name": str(_record_field(item, "config_name", "") or ""),
            "trade_history": _record_list(item, "trade_history"),
        },
    }
    return build_failure_signature(payload)


def build_regime_failure_dashboard(cycle_history: list[Any]) -> dict[str, Any]:
    regimes: dict[str, dict[str, Any]] = {
        regime: _empty_regime_failure_bucket(regime)
        for regime in ("bull", "bear", "oscillation", "unknown")
    }
    return_sums: dict[str, float] = {key: 0.0 for key in regimes}
    loss_return_sums: dict[str, float] = {key: 0.0 for key in regimes}
    overall_signature_counts: dict[str, int] = {}
    total_cycles = len(cycle_history)
    total_loss_cycles = 0

    for item in cycle_history:
        regime = _record_regime(item)
        if regime not in regimes:
            regimes[regime] = _empty_regime_failure_bucket(regime)
            return_sums[regime] = 0.0
            loss_return_sums[regime] = 0.0
        bucket = regimes[regime]
        return_pct = _safe_float(_record_field(item, "return_pct", 0.0)) or 0.0
        is_profit = _record_bool(item, "is_profit", False)
        benchmark_passed = _record_bool(item, "benchmark_passed", False)
        signature = _record_failure_signature(item)
        label = str(signature.get("label") or "")
        sub_label = str(signature.get("sub_label") or "")

        bucket["total_cycles"] += 1
        return_sums[regime] += return_pct
        bucket["negative_contribution_pct"] += min(return_pct, 0.0)
        if is_profit:
            bucket["profit_cycles"] += 1
        else:
            bucket["loss_cycles"] += 1
            total_loss_cycles += 1
            loss_return_sums[regime] += return_pct
            if label:
                counts = dict(bucket.get("failure_signature_counts") or {})
                counts[label] = int(counts.get(label, 0) or 0) + 1
                bucket["failure_signature_counts"] = counts
                overall_signature_counts[label] = overall_signature_counts.get(label, 0) + 1
            if sub_label:
                sub_counts = dict(bucket.get("failure_sub_signature_counts") or {})
                sub_counts[sub_label] = int(sub_counts.get(sub_label, 0) or 0) + 1
                bucket["failure_sub_signature_counts"] = sub_counts
        if not benchmark_passed:
            bucket["benchmark_fail_cycles"] += 1

    for regime, bucket in regimes.items():
        total = int(bucket.get("total_cycles") or 0)
        losses = int(bucket.get("loss_cycles") or 0)
        bucket["avg_return_pct"] = return_sums.get(regime, 0.0) / total if total else 0.0
        bucket["avg_loss_return_pct"] = (
            loss_return_sums.get(regime, 0.0) / losses if losses else 0.0
        )
        bucket["benchmark_fail_rate"] = (
            int(bucket.get("benchmark_fail_cycles") or 0) / total if total else 0.0
        )
        signature_counts = dict(bucket.get("failure_signature_counts") or {})
        if signature_counts:
            top_label = sorted(
                signature_counts.items(),
                key=lambda item: (-int(item[1]), str(item[0])),
            )[0][0]
            bucket["top_failure_signature"] = top_label
            bucket["top_failure_signature_description"] = str(
                FAILURE_SIGNATURE_CATALOG.get(top_label, {}).get("description") or ""
            )
        sub_signature_counts = dict(bucket.get("failure_sub_signature_counts") or {})
        if sub_signature_counts:
            top_sub_label = sorted(
                sub_signature_counts.items(),
                key=lambda item: (-int(item[1]), str(item[0])),
            )[0][0]
            bucket["top_failure_sub_signature"] = top_sub_label
            bucket["top_failure_sub_signature_description"] = str(
                FAILURE_SUB_SIGNATURE_CATALOG.get(top_sub_label, {}).get("description") or ""
            )

    top_repeated_loss_signatures = [
        {
            "label": label,
            "count": count,
            "description": str(FAILURE_SIGNATURE_CATALOG.get(label, {}).get("description") or ""),
        }
        for label, count in sorted(
            overall_signature_counts.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )
        if int(count) >= 2
    ][:5]

    regime_negative_contribution_rank = [
        {
            "regime": regime,
            "negative_contribution_pct": float(bucket.get("negative_contribution_pct") or 0.0),
            "loss_cycles": int(bucket.get("loss_cycles") or 0),
            "top_failure_signature": str(bucket.get("top_failure_signature") or ""),
        }
        for regime, bucket in regimes.items()
        if int(bucket.get("total_cycles") or 0) > 0
    ]
    regime_negative_contribution_rank.sort(
        key=lambda item: (float(item.get("negative_contribution_pct") or 0.0), -int(item.get("loss_cycles") or 0))
    )

    return {
        "schema_version": "training.regime_failure_dashboard.v1",
        "total_cycles": total_cycles,
        "loss_cycles": total_loss_cycles,
        "regimes": regimes,
        "top_repeated_loss_signatures": top_repeated_loss_signatures,
        "regime_negative_contribution_rank": regime_negative_contribution_rank,
    }


def _record_selection_intercepts(item: Any) -> dict[str, Any]:
    direct = _record_dict(item, "selection_intercepts")
    if direct:
        return direct
    execution_snapshot = _record_dict(item, "execution_snapshot")
    return dict(execution_snapshot.get("selection_intercepts") or {})


def _record_regime_runtime_profile(item: Any) -> dict[str, Any]:
    direct = _record_dict(item, "regime_runtime_profile")
    if direct:
        return direct
    execution_snapshot = _record_dict(item, "execution_snapshot")
    return dict(execution_snapshot.get("regime_runtime_profile") or {})


def _record_runtime_budget(item: Any) -> dict[str, Any]:
    intercepts = _record_selection_intercepts(item)
    budget = dict(intercepts.get("budget") or {})
    if budget:
        return budget
    profile = _record_regime_runtime_profile(item)
    layering = dict(profile.get("budget_layering") or {})
    resolved_budget = dict(layering.get("resolved_budget") or {})
    effective_params = dict(profile.get("effective_params") or {})
    params = _record_dict(item, "params")
    return {
        "position_size_cap": resolved_budget.get(
            "position_size",
            effective_params.get("position_size", params.get("position_size")),
        ),
        "cash_reserve": resolved_budget.get(
            "cash_reserve",
            effective_params.get("cash_reserve", params.get("cash_reserve")),
        ),
        "max_positions_cap": resolved_budget.get(
            "max_positions",
            effective_params.get("max_positions", params.get("max_positions")),
        ),
    }


def _empty_regime_discipline_bucket(regime: str) -> dict[str, Any]:
    return {
        "regime": regime,
        "total_cycles": 0,
        "overlay_applied_cycles": 0,
        "budget_correction_applied_cycles": 0,
        "budget_correction_signature_counts": {},
        "top_budget_correction_signature": "",
        "hard_filter_cycles": 0,
        "intercepted_count": 0,
        "reason_counts": {},
        "top_reason": "",
        "avg_exposure_before": 0.0,
        "avg_exposure_after": 0.0,
        "avg_position_size_cap": 0.0,
        "avg_cash_reserve_floor": 0.0,
        "avg_max_positions_cap": 0.0,
        "strategy_families": [],
    }


def build_regime_discipline_dashboard(cycle_history: list[Any]) -> dict[str, Any]:
    regimes: dict[str, dict[str, Any]] = {
        regime: _empty_regime_discipline_bucket(regime)
        for regime in ("bull", "bear", "oscillation", "unknown")
    }
    total_cycles = len(cycle_history)
    total_overlay_applied_cycles = 0
    total_budget_correction_applied_cycles = 0
    total_hard_filter_cycles = 0
    total_intercepted_count = 0
    exposure_before_sums: dict[str, float] = {key: 0.0 for key in regimes}
    exposure_after_sums: dict[str, float] = {key: 0.0 for key in regimes}
    exposure_measure_counts: dict[str, int] = {key: 0 for key in regimes}
    position_size_cap_sums: dict[str, float] = {key: 0.0 for key in regimes}
    cash_reserve_floor_sums: dict[str, float] = {key: 0.0 for key in regimes}
    max_positions_cap_sums: dict[str, float] = {key: 0.0 for key in regimes}
    position_size_cap_counts: dict[str, int] = {key: 0 for key in regimes}
    cash_reserve_floor_counts: dict[str, int] = {key: 0 for key in regimes}
    max_positions_cap_counts: dict[str, int] = {key: 0 for key in regimes}
    overall_reason_counts: dict[str, int] = {}
    overall_budget_correction_signature_counts: dict[str, int] = {}
    overall_strategy_families: set[str] = set()

    for item in cycle_history:
        regime = _record_regime(item)
        if regime not in regimes:
            regimes[regime] = _empty_regime_discipline_bucket(regime)
            exposure_before_sums[regime] = 0.0
            exposure_after_sums[regime] = 0.0
            exposure_measure_counts[regime] = 0
            position_size_cap_sums[regime] = 0.0
            cash_reserve_floor_sums[regime] = 0.0
            max_positions_cap_sums[regime] = 0.0
            position_size_cap_counts[regime] = 0
            cash_reserve_floor_counts[regime] = 0
            max_positions_cap_counts[regime] = 0
        bucket = regimes[regime]
        profile = _record_regime_runtime_profile(item)
        layering = dict(profile.get("budget_layering") or {})
        family_budget_correction = dict(layering.get("family_budget_correction") or {})
        intercepts = _record_selection_intercepts(item)
        bucket["total_cycles"] += 1
        strategy_family = str(profile.get("strategy_family") or _record_field(item, "model_name", "unknown") or "unknown")
        family_list = set(bucket.get("strategy_families") or [])
        if strategy_family:
            family_list.add(strategy_family)
            overall_strategy_families.add(strategy_family)
        bucket["strategy_families"] = sorted(family_list)

        if bool(profile.get("applied", False)):
            bucket["overlay_applied_cycles"] += 1
            total_overlay_applied_cycles += 1
        if bool(family_budget_correction.get("applied", False)):
            bucket["budget_correction_applied_cycles"] += 1
            total_budget_correction_applied_cycles += 1
            signature = str(
                family_budget_correction.get("dominant_failure_sub_signature") or ""
            )
            if signature:
                counts = dict(bucket.get("budget_correction_signature_counts") or {})
                counts[signature] = int(counts.get(signature, 0) or 0) + 1
                bucket["budget_correction_signature_counts"] = counts
                overall_budget_correction_signature_counts[signature] = (
                    int(overall_budget_correction_signature_counts.get(signature, 0) or 0) + 1
                )

        if bool(intercepts.get("active", False)):
            bucket["hard_filter_cycles"] += 1
            total_hard_filter_cycles += 1
            intercepted_count = int(intercepts.get("intercepted_count") or 0)
            bucket["intercepted_count"] += intercepted_count
            total_intercepted_count += intercepted_count
            reason_counts = dict(intercepts.get("reason_counts") or {})
            if reason_counts:
                merged = dict(bucket.get("reason_counts") or {})
                for reason, count in reason_counts.items():
                    merged[reason] = int(merged.get(reason, 0) or 0) + int(count or 0)
                    overall_reason_counts[reason] = (
                        int(overall_reason_counts.get(reason, 0) or 0) + int(count or 0)
                    )
                bucket["reason_counts"] = merged

        exposure_before = _safe_float(intercepts.get("exposure_before"))
        exposure_after = _safe_float(intercepts.get("exposure_after"))
        if exposure_before is not None and exposure_after is not None:
            exposure_before_sums[regime] += exposure_before
            exposure_after_sums[regime] += exposure_after
            exposure_measure_counts[regime] += 1
        budget = _record_runtime_budget(item)
        position_size_cap = _safe_float(budget.get("position_size_cap"))
        cash_reserve_floor = _safe_float(budget.get("cash_reserve"))
        max_positions_cap = _safe_float(budget.get("max_positions_cap"))
        if position_size_cap is not None:
            position_size_cap_sums[regime] += position_size_cap
            position_size_cap_counts[regime] += 1
        if cash_reserve_floor is not None:
            cash_reserve_floor_sums[regime] += cash_reserve_floor
            cash_reserve_floor_counts[regime] += 1
        if max_positions_cap is not None:
            max_positions_cap_sums[regime] += max_positions_cap
            max_positions_cap_counts[regime] += 1

    for regime, bucket in regimes.items():
        measures = exposure_measure_counts.get(regime, 0)
        if measures:
            bucket["avg_exposure_before"] = exposure_before_sums.get(regime, 0.0) / measures
            bucket["avg_exposure_after"] = exposure_after_sums.get(regime, 0.0) / measures
        if position_size_cap_counts.get(regime, 0):
            bucket["avg_position_size_cap"] = (
                position_size_cap_sums.get(regime, 0.0) / position_size_cap_counts.get(regime, 1)
            )
        if cash_reserve_floor_counts.get(regime, 0):
            bucket["avg_cash_reserve_floor"] = (
                cash_reserve_floor_sums.get(regime, 0.0) / cash_reserve_floor_counts.get(regime, 1)
            )
        if max_positions_cap_counts.get(regime, 0):
            bucket["avg_max_positions_cap"] = (
                max_positions_cap_sums.get(regime, 0.0) / max_positions_cap_counts.get(regime, 1)
            )
        reason_counts = dict(bucket.get("reason_counts") or {})
        if reason_counts:
            bucket["top_reason"] = sorted(
                reason_counts.items(),
                key=lambda item: (-int(item[1]), str(item[0])),
            )[0][0]
        correction_counts = dict(bucket.get("budget_correction_signature_counts") or {})
        if correction_counts:
            bucket["top_budget_correction_signature"] = sorted(
                correction_counts.items(),
                key=lambda item: (-int(item[1]), str(item[0])),
            )[0][0]

    top_repeated_intercept_reasons = [
        {
            "reason": reason,
            "count": count,
        }
        for reason, count in sorted(
            overall_reason_counts.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )
        if int(count) >= 2
    ][:5]
    top_repeated_budget_correction_signatures = [
        {
            "sub_label": signature,
            "count": count,
        }
        for signature, count in sorted(
            overall_budget_correction_signature_counts.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )
        if int(count) >= 1
    ][:5]

    return {
        "schema_version": "training.regime_discipline_dashboard.v1",
        "total_cycles": total_cycles,
        "overlay_applied_cycles": total_overlay_applied_cycles,
        "budget_correction_applied_cycles": total_budget_correction_applied_cycles,
        "hard_filter_cycles": total_hard_filter_cycles,
        "intercepted_count": total_intercepted_count,
        "strategy_families": sorted(overall_strategy_families),
        "regimes": regimes,
        "top_repeated_intercept_reasons": top_repeated_intercept_reasons,
        "top_repeated_budget_correction_signatures": top_repeated_budget_correction_signatures,
    }


def evaluate_research_feedback_gate(
    research_feedback: dict[str, Any] | None,
    policy: dict[str, Any] | None = None,
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(research_feedback or {})
    recommendation = dict(payload.get("recommendation") or {})
    bias = str(recommendation.get("bias") or "maintain")
    sample_count = int(payload.get("sample_count") or 0)
    episode_count = int(payload.get("episode_count") or 0)
    evidence_count = episode_count or sample_count
    config = _merge_policy(
        normalize_research_feedback_gate_policy(defaults or _DEFAULT_FREEZE_FEEDBACK_GATE),
        normalize_research_feedback_gate_policy(policy or {}),
    )
    checks: list[dict[str, Any]] = []

    min_episode_count = int(config.get("min_episode_count") or 0)
    sample_check = {
        "name": "min_episode_count",
        "passed": evidence_count >= min_episode_count,
        "actual": evidence_count,
        "required_gte": min_episode_count,
        "sample_count": sample_count,
        "episode_count": episode_count,
    }
    checks.append(sample_check)
    if evidence_count < min_episode_count:
        return {
            "active": False,
            "passed": True,
            "reason": "insufficient_samples",
            "bias": bias,
            "sample_count": sample_count,
            "episode_count": episode_count,
            "evidence_count": evidence_count,
            "checks": checks,
            "failed_checks": [],
            "available_horizons": sorted((payload.get("horizons") or {}).keys()),
        }

    blocked_biases = [str(item).strip() for item in (config.get("blocked_biases") or []) if str(item).strip()]
    if blocked_biases:
        checks.append(
            {
                "name": "blocked_biases",
                "passed": bias not in blocked_biases,
                "actual": bias,
                "blocked": blocked_biases,
            }
        )

    max_brier = _safe_float(config.get("max_brier_like_direction_score"))
    brier = _safe_float(payload.get("brier_like_direction_score"))
    if max_brier is not None and brier is not None:
        checks.append(
            {
                "name": "max_brier_like_direction_score",
                "passed": brier <= max_brier,
                "actual": brier,
                "required_lte": max_brier,
            }
        )

    horizons = dict(payload.get("horizons") or {})
    horizon_defaults = dict((config.get("horizons") or {}).get("default") or {})
    for horizon_key in sorted(horizons.keys()):
        horizon_metrics = dict(horizons.get(horizon_key) or {})
        horizon_policy = _merge_policy(horizon_defaults, dict((config.get("horizons") or {}).get(horizon_key) or {}))
        for metric_name, threshold_key, comparator in (
            ("hit_rate", "min_hit_rate", "gte"),
            ("invalidation_rate", "max_invalidation_rate", "lte"),
            ("interval_hit_rate", "min_interval_hit_rate", "gte"),
        ):
            actual = _safe_float(horizon_metrics.get(metric_name))
            threshold = _safe_float(horizon_policy.get(threshold_key))
            if actual is None or threshold is None:
                continue
            passed = actual >= threshold if comparator == "gte" else actual <= threshold
            checks.append(
                {
                    "name": f"{horizon_key}.{metric_name}",
                    "horizon": horizon_key,
                    "metric": metric_name,
                    "passed": passed,
                    "actual": actual,
                    "required_gte" if comparator == "gte" else "required_lte": threshold,
                }
            )

    failed_checks = [
        item
        for item in checks
        if item.get("passed") is False and item.get("name") != "min_episode_count"
    ]
    return {
        "active": True,
        "passed": not failed_checks,
        "bias": bias,
        "sample_count": sample_count,
        "episode_count": episode_count,
        "evidence_count": evidence_count,
        "checks": checks,
        "failed_checks": failed_checks,
        "available_horizons": sorted(horizons.keys()),
        "recommendation": recommendation,
    }


def evaluate_freeze_gate(
    cycle_history: list[Any],
    freeze_total_cycles: int,
    freeze_profit_required: int,
    freeze_gate_policy: dict[str, Any],
    rolling: dict[str, Any],
    research_feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_freeze_gate_policy = normalize_freeze_gate_policy(freeze_gate_policy)
    governance_metrics = build_governance_metrics(cycle_history)
    proposal_gate_summary = build_proposal_gate_summary(cycle_history)
    realism_summary = build_realism_summary(cycle_history)
    if len(cycle_history) < freeze_total_cycles or not rolling:
        return {
            "ready": False,
            "passed": False,
            "checks": [],
            "governance_metrics": governance_metrics,
            "proposal_gate_summary": proposal_gate_summary,
            "realism_summary": realism_summary,
            "research_feedback_gate": evaluate_research_feedback_gate(
                research_feedback,
                policy=dict((resolved_freeze_gate_policy or {}).get("research_feedback") or {}),
                defaults=_DEFAULT_FREEZE_FEEDBACK_GATE,
            ),
        }

    required_win_rate = freeze_profit_required / max(freeze_total_cycles, 1)
    min_avg_return = float(resolved_freeze_gate_policy.get("avg_return_gt", 0.0) or 0.0)
    min_avg_sharpe = float(resolved_freeze_gate_policy.get("avg_sharpe_gte", 0.8) or 0.8)
    max_avg_drawdown = float(resolved_freeze_gate_policy.get("avg_max_drawdown_lt", 15.0) or 15.0)
    min_benchmark_pass_rate = float(
        resolved_freeze_gate_policy.get("benchmark_pass_rate_gte", 0.60) or 0.60
    )
    governance_policy = dict((resolved_freeze_gate_policy or {}).get("governance") or {})

    checks = [
        {"name": "win_rate", "passed": rolling.get("win_rate", 0.0) >= required_win_rate, "actual": rolling.get("win_rate", 0.0), "required_gte": required_win_rate},
        {"name": "avg_return", "passed": rolling.get("avg_return", 0.0) > min_avg_return, "actual": rolling.get("avg_return", 0.0), "required_gt": min_avg_return},
        {"name": "avg_sharpe", "passed": rolling.get("avg_sharpe", 0.0) >= min_avg_sharpe, "actual": rolling.get("avg_sharpe", 0.0), "required_gte": min_avg_sharpe},
        {"name": "avg_max_drawdown", "passed": rolling.get("avg_max_drawdown", 0.0) < max_avg_drawdown, "actual": rolling.get("avg_max_drawdown", 0.0), "required_lt": max_avg_drawdown},
        {"name": "benchmark_pass_rate", "passed": rolling.get("benchmark_pass_rate", 0.0) >= min_benchmark_pass_rate, "actual": rolling.get("benchmark_pass_rate", 0.0), "required_gte": min_benchmark_pass_rate},
    ]
    if governance_policy:
        max_drift_rate = _safe_float(governance_policy.get("max_active_candidate_drift_rate"))
        if max_drift_rate is not None:
            checks.append(
                {
                    "name": "active_candidate_drift_rate",
                    "passed": governance_metrics.get("active_candidate_drift_rate", 0.0) <= max_drift_rate,
                    "actual": governance_metrics.get("active_candidate_drift_rate", 0.0),
                    "required_lte": max_drift_rate,
                }
            )
        max_pending_count = int(governance_policy.get("max_candidate_pending_count") or 0)
        if "max_candidate_pending_count" in governance_policy:
            checks.append(
                {
                    "name": "candidate_pending_count",
                    "passed": int(governance_metrics.get("candidate_pending_count", 0) or 0) <= max_pending_count,
                    "actual": int(governance_metrics.get("candidate_pending_count", 0) or 0),
                    "required_lte": max_pending_count,
                }
            )
        max_override_pending_count = int(governance_policy.get("max_override_pending_count") or 0)
        if "max_override_pending_count" in governance_policy:
            checks.append(
                {
                    "name": "override_pending_count",
                    "passed": int(governance_metrics.get("override_pending_count", 0) or 0) <= max_override_pending_count,
                    "actual": int(governance_metrics.get("override_pending_count", 0) or 0),
                    "required_lte": max_override_pending_count,
                }
            )
    research_gate = evaluate_research_feedback_gate(
        research_feedback,
        policy=dict((resolved_freeze_gate_policy or {}).get("research_feedback") or {}),
        defaults=_DEFAULT_FREEZE_FEEDBACK_GATE,
    )
    base_passed = all(check.get("passed") for check in checks)
    return {
        "ready": True,
        "passed": base_passed and bool(research_gate.get("passed", True)),
        "checks": checks,
        "governance_metrics": governance_metrics,
        "proposal_gate_summary": proposal_gate_summary,
        "realism_summary": realism_summary,
        "research_feedback_gate": research_gate,
    }


def should_freeze(
    cycle_history: list[Any],
    freeze_total_cycles: int,
    freeze_profit_required: int,
    freeze_gate_policy: dict[str, Any],
    rolling: dict[str, Any],
    research_feedback: dict[str, Any] | None = None,
) -> bool:
    evaluation = evaluate_freeze_gate(
        cycle_history,
        freeze_total_cycles,
        freeze_profit_required,
        freeze_gate_policy,
        rolling,
        research_feedback=research_feedback,
    )
    return bool(evaluation.get("passed"))


def build_freeze_report(
    cycle_history: list[Any],
    current_params: dict[str, Any],
    freeze_total_cycles: int,
    freeze_profit_required: int,
    freeze_gate_policy: dict[str, Any],
    rolling: dict[str, Any],
    research_feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    total = len(cycle_history)
    profits = sum(1 for r in cycle_history if r.is_profit)
    governance_defaults = dict(resolve_model_governance_matrix().get("freeze") or {})
    resolved_freeze_gate_policy = normalize_freeze_gate_policy(freeze_gate_policy)
    evaluation = evaluate_freeze_gate(
        cycle_history,
        freeze_total_cycles,
        freeze_profit_required,
        resolved_freeze_gate_policy,
        rolling,
        research_feedback=research_feedback,
    )
    return {
        "frozen": True,
        "freeze_applied": True,
        "total_cycles": total,
        "total_profit_count": profits,
        "profit_rate": profits / total if total > 0 else 0,
        "recent_10_profit_count": sum(1 for r in cycle_history[-10:] if r.is_profit),
        "final_params": current_params,
        "frozen_time": datetime.now().isoformat(),
        "self_assessment": rolling,
        "research_feedback": dict(research_feedback or {}),
        "audit_semantics": build_training_audit_semantics(),
        "governance_metrics": build_governance_metrics(cycle_history),
        "proposal_gate_summary": build_proposal_gate_summary(cycle_history),
        "suggestion_adoption_summary": build_suggestion_adoption_summary(cycle_history),
        "regime_failure_dashboard": build_regime_failure_dashboard(cycle_history),
        "regime_discipline_dashboard": build_regime_discipline_dashboard(cycle_history),
        "realism_summary": build_realism_summary(cycle_history),
        "freeze_gate": {
            "window": freeze_total_cycles,
            "required_win_rate": freeze_profit_required / max(freeze_total_cycles, 1),
            "required_avg_return": float(
                resolved_freeze_gate_policy.get("avg_return_gt", 0.0) or 0.0
            ),
            "required_avg_sharpe": float(
                resolved_freeze_gate_policy.get("avg_sharpe_gte", 0.8) or 0.8
            ),
            "required_avg_max_drawdown": float(
                resolved_freeze_gate_policy.get("avg_max_drawdown_lt", 15.0) or 15.0
            ),
            "required_benchmark_pass_rate": float(
                resolved_freeze_gate_policy.get("benchmark_pass_rate_gte", 0.60) or 0.60
            ),
            "research_feedback": dict(
                (resolved_freeze_gate_policy or {}).get("research_feedback") or {}
            ),
            "governance": dict((resolved_freeze_gate_policy or {}).get("governance") or {}),
            "governance_reference_policy": governance_defaults,
        },
        "freeze_gate_evaluation": evaluation,
    }


def generate_training_report(
    total_cycle_attempts: int,
    skipped_cycle_count: int,
    cycle_history: list[Any],
    current_params: dict[str, Any],
    is_frozen: bool,
    self_assessment: dict[str, Any],
    research_feedback: dict[str, Any] | None = None,
    freeze_gate_evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attempted = max(total_cycle_attempts, len(cycle_history) + skipped_cycle_count)
    successful = len(cycle_history)
    skipped = max(skipped_cycle_count, attempted - successful)

    if not cycle_history:
        return {
            "status": "no_data",
            "total_cycles": attempted,
            "attempted_cycles": attempted,
            "successful_cycles": 0,
            "skipped_cycles": skipped,
            "profit_cycles": 0,
            "loss_cycles": 0,
            "profit_rate": 0,
            "current_params": current_params,
            "freeze_applied": False,
            "is_frozen": False,
            "self_assessment": self_assessment,
            "research_feedback": dict(research_feedback or {}),
            "audit_semantics": build_training_audit_semantics(),
            "governance_metrics": build_governance_metrics(cycle_history),
            "proposal_gate_summary": build_proposal_gate_summary(cycle_history),
            "suggestion_adoption_summary": build_suggestion_adoption_summary(cycle_history),
            "regime_failure_dashboard": build_regime_failure_dashboard(cycle_history),
            "regime_discipline_dashboard": build_regime_discipline_dashboard(cycle_history),
            "realism_summary": build_realism_summary(cycle_history),
            "freeze_gate_evaluation": dict(freeze_gate_evaluation or {}),
        }

    profits = sum(1 for r in cycle_history if r.is_profit)
    status = "completed_with_skips" if skipped else "completed"
    return {
        "status": status,
        "total_cycles": attempted,
        "attempted_cycles": attempted,
        "successful_cycles": successful,
        "skipped_cycles": skipped,
        "profit_cycles": profits,
        "loss_cycles": successful - profits,
        "profit_rate": profits / successful if successful > 0 else 0,
        "current_params": current_params,
        "freeze_applied": bool(is_frozen),
        "is_frozen": is_frozen,
        "self_assessment": self_assessment,
        "research_feedback": dict(research_feedback or {}),
        "audit_semantics": build_training_audit_semantics(),
        "governance_metrics": build_governance_metrics(cycle_history),
        "proposal_gate_summary": build_proposal_gate_summary(cycle_history),
        "suggestion_adoption_summary": build_suggestion_adoption_summary(cycle_history),
        "regime_failure_dashboard": build_regime_failure_dashboard(cycle_history),
        "regime_discipline_dashboard": build_regime_discipline_dashboard(cycle_history),
        "realism_summary": build_realism_summary(cycle_history),
        "freeze_gate_evaluation": dict(freeze_gate_evaluation or {}),
    }
