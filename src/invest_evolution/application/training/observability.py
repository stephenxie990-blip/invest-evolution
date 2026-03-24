"""Merged training module: observability.py."""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Dict, Optional, cast

import numpy as np

from invest_evolution.application.training.controller import (
    session_current_params,
    session_cycle_history,
)
from invest_evolution.application.training.review_contracts import (
    AllocationReviewDigestPayload,
    ExecutionDefaultsPayload,
    GovernanceDecisionInputPayload,
    GovernanceDecisionPayload,
    ManagerReviewDigestPayload,
    ReviewAppliedEffectsPayload,
    ReviewBasisWindowPayload,
    ReviewDecisionInputPayload,
    build_execution_snapshot,
    build_review_basis_window,
    project_review_applied_effects_payload,
)
from invest_evolution.investment.shared.policy import (
    DEFAULT_FREEZE_GATE_POLICY,
    build_optimization_event_lineage,
    infer_deployment_stage,
    normalize_config_ref,
    normalize_freeze_gate_policy,
    resolve_governance_matrix,
)
from invest_evolution.investment.shared.research_feedback_gate import (
    evaluate_research_feedback_gate as _evaluate_research_feedback_gate,
)

RUNTIME_FREEZE_REPORT_NAME = "runtime_frozen.json"
logger = logging.getLogger(__name__)


def _load_module(name: str):
    return import_module(name)


def _call_module_attr(
    loader: Callable[[], Any],
    attr: str,
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    return getattr(loader(), attr)(*args, **kwargs)


def _lazy_module_function(loader: Callable[[], Any], attr: str) -> Callable[..., Any]:
    def _proxy(*args: Any, **kwargs: Any) -> Any:
        return _call_module_attr(loader, attr, *args, **kwargs)

    _proxy.__name__ = attr
    _proxy.__qualname__ = attr
    return _proxy


@lru_cache(maxsize=None)
def _execution_module():
    return _load_module("invest_evolution.application.training.execution")


@lru_cache(maxsize=None)
def _persistence_module():
    return _load_module("invest_evolution.application.training.persistence")


def _execution_proxy(attr: str) -> Callable[..., Any]:
    return _lazy_module_function(_execution_module, attr)


def _persistence_proxy(attr: str) -> Callable[..., Any]:
    return _lazy_module_function(_persistence_module, attr)


@lru_cache(maxsize=None)
def _policy_module():
    return _load_module("invest_evolution.application.training.policy")


def _policy_proxy(attr: str) -> Callable[..., Any]:
    return _lazy_module_function(_policy_module, attr)


_write_runtime_freeze_boundary = cast(
    Callable[..., str],
    _persistence_proxy("write_runtime_freeze_boundary"),
)
_project_manager_compatibility = cast(
    Callable[..., Any],
    _execution_proxy("project_manager_compatibility"),
)
_latest_open_candidate_runtime_config_ref = cast(
    Callable[..., str],
    _execution_proxy("_latest_open_candidate_runtime_config_ref"),
)
_manager_output_manager_id = cast(
    Callable[..., str],
    _execution_proxy("manager_output_manager_id"),
)
_manager_output_manager_config_ref = cast(
    Callable[..., str],
    _execution_proxy("manager_output_manager_config_ref"),
)
_build_manager_compatibility_fields = cast(
    Callable[..., dict[str, Any]],
    _execution_proxy("build_manager_compatibility_fields"),
)
_normalize_governance_decision = cast(
    Callable[..., dict[str, Any]],
    _policy_proxy("normalize_governance_decision"),
)
_governance_from_controller = cast(
    Callable[..., dict[str, Any]],
    _policy_proxy("governance_from_controller"),
)


@dataclass(frozen=True)
class ReviewBoundaryEventBundle:
    review_event: Any
    review_basis_window: ReviewBasisWindowPayload
    manager_id: str
    active_runtime_config_ref: str


@dataclass(frozen=True)
class RuntimeMutationBoundaryBundle:
    mutation_event: Any
    mutation_log_message: str
    auto_apply_runtime_config_ref: str = ""


@dataclass(frozen=True)
class OptimizationBoundaryContext:
    cycle_id: int | None
    manager_id: str
    active_runtime_config_ref: str
    fitness_source_cycles: list[int]


@dataclass(frozen=True)
class SelectionBoundaryProjection:
    manager_output: Any | None
    trading_plan: Any
    strategy_advice: dict[str, Any]
    compatibility_fields: dict[str, Any]


@dataclass(frozen=True)
class ReviewEvalBoundaryProjection:
    manager_id: str
    manager_config_ref: str
    subject_type: str
    compatibility_fields: dict[str, Any]


@dataclass(frozen=True)
class OutcomeExecutionBoundaryProjection:
    execution_snapshot: dict[str, Any]
    governance_decision: GovernanceDecisionPayload
    execution_defaults: ExecutionDefaultsPayload
    compatibility_fields: dict[str, Any]


@dataclass(frozen=True)
class _FreezeBoundarySummary:
    governance_metrics: dict[str, Any]
    realism_summary: dict[str, Any]
    research_feedback_gate: dict[str, Any]


@dataclass(frozen=True)
class _PromotionLineageRuntimeState:
    payload: dict[str, Any]
    shadow_mode: bool
    promotion_decision: dict[str, Any]
    discipline: dict[str, Any]
    deployment_stage: str
    candidate_runtime_config_ref: str
    mutation_event: dict[str, Any]


def _report_field(report: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(report, dict):
        return report.get(field_name, default)
    return getattr(report, field_name, default)


def _dict_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    try:
        return dict(value)
    except Exception:
        return {}


def _list_payload(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if value is None:
        return []
    try:
        return list(value)
    except Exception:
        return []


def _normalize_governance_payload(
    payload: GovernanceDecisionInputPayload | dict[str, Any] | None = None,
    *,
    fallback: dict[str, Any] | None = None,
) -> GovernanceDecisionPayload:
    return cast(
        GovernanceDecisionPayload,
        _dict_payload(
            _normalize_governance_decision(
                _dict_payload(payload),
                fallback=fallback,
            )
        ),
    )


def _coerce_review_decision_input(
    payload: ReviewDecisionInputPayload | dict[str, Any] | None = None,
) -> ReviewDecisionInputPayload:
    return cast(ReviewDecisionInputPayload, _dict_payload(payload))


def build_optimization_lineage(
    context: OptimizationBoundaryContext,
    *,
    candidate_runtime_config_ref: str = "",
    deployment_stage: str = "active",
    runtime_override_keys: list[str] | None = None,
    promotion_status: str = "not_evaluated",
) -> dict[str, Any]:
    return build_optimization_event_lineage(
        cycle_id=int(context.cycle_id) if context.cycle_id is not None else None,
        manager_id=str(context.manager_id or ""),
        active_runtime_config_ref=str(context.active_runtime_config_ref or ""),
        candidate_runtime_config_ref=str(candidate_runtime_config_ref or ""),
        promotion_status=str(promotion_status or "not_evaluated"),
        deployment_stage=str(deployment_stage or "active"),
        review_basis_window={},
        fitness_source_cycles=list(context.fitness_source_cycles or []),
        runtime_override_keys=list(runtime_override_keys or []),
    )


def record_selection_boundary_effects(
    controller: Any,
    *,
    cycle_id: int,
    selected_codes: list[str],
    selection_trace: dict[str, Any],
    active_manager_count: int,
) -> None:
    artifact_recorder = getattr(controller, "artifact_recorder", None)
    if artifact_recorder is not None and hasattr(artifact_recorder, "save_selection_artifact"):
        artifact_recorder.save_selection_artifact(selection_trace, cycle_id)

    controller._emit_agent_status(
        "ManagerExecution",
        "completed",
        f"多经理运行完成，组合选中 {len(selected_codes)} 只股票",
        cycle_id=cycle_id,
        stage="manager_selection",
        progress_pct=58,
        step=2,
        total_steps=6,
        selected_stocks=selected_codes[:10],
        details=selection_trace,
    )
    controller._emit_module_log(
        "selection",
        "多经理组合完成",
        f"最终选中 {len(selected_codes)} 只股票",
        cycle_id=cycle_id,
        kind="manager_selection_result",
        details=selection_trace,
        metrics={
            "selected_count": len(selected_codes),
            "manager_count": int(active_manager_count),
        },
    )
    controller.agent_tracker.mark_selected(cycle_id, selected_codes)


def persist_research_boundary_effects(
    controller: Any,
    *,
    cycle_id: int,
    cutoff_date: str,
    manager_output: Any | None,
    stock_data: dict[str, Any],
    selected: list[str],
    regime_result: dict[str, Any],
    selection_mode: str,
    portfolio_plan: dict[str, Any] | None = None,
    manager_results: list[dict[str, Any]] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    dominant_manager_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    research_artifacts = controller.training_research_service.persist_cycle_research_artifacts(
        controller,
        cycle_id=cycle_id,
        cutoff_date=cutoff_date,
        manager_output=manager_output,
        stock_data=stock_data,
        selected=selected,
        regime_result=regime_result,
        selection_mode=selection_mode,
    )
    compatibility_snapshot = dict(execution_snapshot or {})
    manager_output_id = _manager_output_manager_id(manager_output, fallback="")
    manager_output_config_ref = _manager_output_manager_config_ref(manager_output, fallback="")
    if manager_output_id and not compatibility_snapshot.get("dominant_manager_id"):
        compatibility_snapshot["dominant_manager_id"] = manager_output_id
    if manager_output_config_ref:
        compatibility_snapshot.setdefault("manager_config_ref", manager_output_config_ref)
        compatibility_snapshot.setdefault("active_runtime_config_ref", manager_output_config_ref)
        execution_defaults = dict(compatibility_snapshot.get("execution_defaults") or {})
        if manager_output_id and not execution_defaults.get("default_manager_id"):
            execution_defaults["default_manager_id"] = manager_output_id
        if not execution_defaults.get("default_manager_config_ref"):
            execution_defaults["default_manager_config_ref"] = manager_output_config_ref
        compatibility_snapshot["execution_defaults"] = execution_defaults
    projection = _project_manager_compatibility(
        controller,
        manager_output=manager_output,
        portfolio_plan=dict(portfolio_plan or {}),
        manager_results=list(manager_results or []),
        execution_snapshot=compatibility_snapshot,
        dominant_manager_id_hint=str(dominant_manager_id or ""),
    )
    research_feedback = controller._load_research_feedback(
        cutoff_date=cutoff_date,
        manager_id=str(projection.manager_id or ""),
        manager_config_ref=str(projection.manager_config_ref or ""),
        regime=str(regime_result.get("regime") or ""),
    )
    if research_feedback:
        controller._emit_module_log(
            "review",
            "载入 ask 侧校准反馈",
            dict(research_feedback.get("recommendation") or {}).get(
                "summary",
                "research feedback loaded",
            ),
            cycle_id=cycle_id,
            kind="research_feedback",
            details=research_feedback,
            metrics=controller._research_feedback_brief(research_feedback),
        )
    if research_artifacts:
        controller._emit_module_log(
            "research",
            "训练样本已写入研究归因库",
            f"cases={int(research_artifacts.get('saved_case_count') or 0)}, attributions={int(research_artifacts.get('saved_attribution_count') or 0)}",
            cycle_id=cycle_id,
            kind="research_persisted",
            details=research_artifacts,
            metrics={
                "saved_case_count": int(research_artifacts.get("saved_case_count") or 0),
                "saved_attribution_count": int(research_artifacts.get("saved_attribution_count") or 0),
            },
        )
    return dict(research_artifacts or {}), dict(research_feedback or {})


def write_runtime_snapshot_boundary(
    controller: Any,
    *,
    cycle_id: int,
) -> str:
    return str(
        controller.config_service.write_runtime_snapshot(
            cycle_id=cycle_id,
            output_dir=controller.output_dir,
        )
    )


def build_selection_boundary_projection(selection_result: Any) -> SelectionBoundaryProjection:
    manager_bundle = getattr(selection_result, "manager_bundle", None)
    portfolio_plan_obj = getattr(manager_bundle, "portfolio_plan", None)
    manager_output = None
    if manager_bundle is not None:
        manager_outputs = dict(getattr(manager_bundle, "manager_outputs", {}) or {})
        dominant_manager_id = str(getattr(selection_result, "dominant_manager_id", "") or "").strip()
        manager_output = manager_outputs.get(dominant_manager_id)
        if manager_output is None:
            manager_output = next(iter(manager_outputs.values()), None)

    if portfolio_plan_obj is None:
        portfolio_plan_obj = getattr(selection_result, "portfolio_plan", None)

    to_trading_plan = getattr(portfolio_plan_obj, "to_trading_plan", None)
    if not callable(to_trading_plan):
        raise ValueError("Selection boundary cannot derive TradingPlan without portfolio_plan boundary object")

    return SelectionBoundaryProjection(
        manager_output=manager_output,
        trading_plan=to_trading_plan(),
        strategy_advice={
            "source": "manager_runtime",
            "portfolio_plan": dict(getattr(selection_result, "portfolio_plan", {}) or {}),
            "dominant_manager_id": str(getattr(selection_result, "dominant_manager_id", "") or ""),
        },
        compatibility_fields=dict(getattr(selection_result, "compatibility_fields", {}) or {}),
    )


def _resolve_cycle_payload_boundary(
    *,
    cycle_payload: dict[str, Any] | None = None,
    cycle_dict: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return dict(cycle_payload or cycle_dict or {})


def build_review_eval_projection_boundary(
    controller: Any,
    *,
    manager_output: Any | None,
    cycle_payload: dict[str, Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    simulation_envelope: Any | None = None,
    manager_results: list[dict[str, Any]] | None = None,
    portfolio_plan: dict[str, Any] | None = None,
    dominant_manager_id: str = "",
) -> ReviewEvalBoundaryProjection:
    manager_payload = [dict(item) for item in list(manager_results or [])]
    portfolio_payload = dict(portfolio_plan or {})
    subject_type = "manager_portfolio" if portfolio_payload else "single_manager"
    payload = _resolve_cycle_payload_boundary(
        cycle_payload=cycle_payload,
    )
    projection = _project_manager_compatibility(
        controller,
        manager_output=manager_output,
        portfolio_plan=portfolio_payload,
        manager_results=manager_payload,
        execution_snapshot=(
            dict(simulation_envelope.execution_snapshot or {})
            if simulation_envelope is not None
            else {
                **dict(execution_snapshot or payload.get("execution_snapshot") or {}),
                "active_runtime_config_ref": str(getattr(manager_output, "manager_config_ref", "") or ""),
                "manager_config_ref": str(getattr(manager_output, "manager_config_ref", "") or ""),
            }
        ),
        dominant_manager_id_hint=str(dominant_manager_id or ""),
    )
    return ReviewEvalBoundaryProjection(
        manager_id=str(projection.manager_id or ""),
        manager_config_ref=str(projection.manager_config_ref or ""),
        subject_type=subject_type,
        compatibility_fields=_build_manager_compatibility_fields(
            projection,
            derived=subject_type == "manager_portfolio",
            source="dominant_manager" if subject_type == "manager_portfolio" else "legacy_manager_output",
        ),
    )


def build_outcome_execution_boundary_projection(
    controller: Any,
    *,
    cycle_id: int,
    cycle_payload: dict[str, Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    governance_decision: GovernanceDecisionInputPayload | dict[str, Any] | None = None,
    manager_output: Any | None,
    selection_mode: str,
    benchmark_passed: bool,
    manager_results_payload: list[dict[str, Any]],
    portfolio_payload: dict[str, Any],
    dominant_manager_id: str = "",
) -> OutcomeExecutionBoundaryProjection:
    resolved_cycle_payload = _resolve_cycle_payload_boundary(
        cycle_payload=cycle_payload,
    )
    snapshot_seed = dict(execution_snapshot or resolved_cycle_payload.get("execution_snapshot") or {})
    compatibility_seed = dict(snapshot_seed.get("compatibility_fields") or {})
    execution_snapshot = dict(
        snapshot_seed
        or build_execution_snapshot(
            controller,
            cycle_id=cycle_id,
            manager_output=manager_output,
            selection_mode=selection_mode,
            benchmark_passed=benchmark_passed,
            basis_stage="persistence_fallback",
            manager_results=manager_results_payload,
            portfolio_plan=portfolio_payload,
            dominant_manager_id=dominant_manager_id,
            compatibility_fields=compatibility_seed,
        )
    )
    resolved_governance_decision = _normalize_governance_payload(
        governance_decision or execution_snapshot.get("governance_decision") or {},
        fallback=_governance_from_controller(controller),
    )
    scope_projection = _project_manager_compatibility(
        None,
        manager_output=manager_output,
        governance_decision=resolved_governance_decision,
        portfolio_plan=portfolio_payload,
        manager_results=manager_results_payload,
        execution_snapshot=execution_snapshot,
        dominant_manager_id_hint=str(dominant_manager_id or ""),
    )
    compatibility_fields: dict[str, Any] = (
        _build_manager_compatibility_fields(
            scope_projection,
            derived=True,
            source="dominant_manager",
            field_role="derived_compatibility",
        )
        if portfolio_payload
        else dict(execution_snapshot.get("compatibility_fields") or {})
    )
    merged_execution_snapshot = deepcopy(dict(execution_snapshot or {}))
    merged_execution_snapshot["governance_decision"] = dict(resolved_governance_decision)
    merged_execution_snapshot["manager_id"] = str(scope_projection.manager_id or "")
    merged_execution_snapshot["active_runtime_config_ref"] = str(scope_projection.active_runtime_config_ref or "")
    merged_execution_snapshot["manager_config_ref"] = str(scope_projection.manager_config_ref or "")
    merged_execution_snapshot["subject_type"] = str(
        scope_projection.subject_type or "single_manager"
    )
    merged_execution_snapshot["dominant_manager_id"] = str(
        scope_projection.dominant_manager_id or scope_projection.manager_id or ""
    )
    merged_execution_snapshot["execution_defaults"] = cast(
        ExecutionDefaultsPayload,
        dict(scope_projection.execution_defaults or {}),
    )
    if portfolio_payload:
        merged_execution_snapshot["manager_results"] = deepcopy(list(manager_results_payload or []))
        merged_execution_snapshot["portfolio_plan"] = deepcopy(dict(portfolio_payload or {}))
        merged_execution_snapshot["compatibility_fields"] = dict(compatibility_fields or {})
    return OutcomeExecutionBoundaryProjection(
        execution_snapshot=merged_execution_snapshot,
        governance_decision=resolved_governance_decision,
        execution_defaults=cast(
            ExecutionDefaultsPayload,
            dict(scope_projection.execution_defaults or {}),
        ),
        compatibility_fields=dict(compatibility_fields or {}),
    )

def _build_runtime_adjustment_change(
    config_adjustments: dict[str, Any],
    scoring_adjustments: dict[str, Any],
) -> dict[str, Any]:
    return {
        "params": dict(config_adjustments),
        "scoring": dict(scoring_adjustments),
    }


def _build_feedback_stage_payload(
    *,
    feedback_plan: dict[str, Any],
    feedback_adjustments: dict[str, Any],
    feedback_scoring: dict[str, Any],
) -> dict[str, Any]:
    return {
        "bias": str(feedback_plan.get("bias") or ""),
        "failed_horizons": list(feedback_plan.get("failed_horizons") or []),
        "failed_checks": list(feedback_plan.get("failed_check_names") or []),
        "suggestions": list(feedback_plan.get("suggestions") or []),
        "param_adjustments": dict(feedback_adjustments),
        "scoring_adjustments": dict(feedback_scoring),
        "sample_count": int(feedback_plan.get("sample_count") or 0),
        "severity": float(feedback_plan.get("severity") or 0.0),
        "benchmark_context": dict(feedback_plan.get("benchmark_context") or {}),
        "summary": str(feedback_plan.get("summary") or ""),
    }


def _build_llm_analysis_stage_payload(
    *,
    trade_dicts: list[dict[str, Any]],
    consecutive_losses: int,
    analysis: Any,
) -> dict[str, Any]:
    return {
        "cause": str(getattr(analysis, "cause", "") or ""),
        "suggestions": list(getattr(analysis, "suggestions", []) or []),
        "consecutive_losses": int(consecutive_losses),
        "trade_record_count": len(list(trade_dicts or [])),
    }


def _build_evolution_stage_payload(
    *,
    fitness_scores: list[float],
    best_params: dict[str, Any],
    population_size: int,
) -> dict[str, Any]:
    return {
        "fitness_scores": list(fitness_scores[-5:]),
        "fitness_policy": "benchmark_oriented_v1",
        "best_params": dict(best_params or {}),
        "fitness_sample_count": len(list(fitness_scores or [])),
        "population_size": int(population_size),
    }


def _build_optimization_error_stage_payload(exc: Exception) -> dict[str, Any]:
    return {
        "exception_type": exc.__class__.__name__,
        "message": str(exc),
    }


def _build_review_decision_stage_payload(
    *,
    review_decision: ReviewDecisionInputPayload,
    eval_report: Any,
    manager_review_report: ManagerReviewDigestPayload | dict[str, Any],
    allocation_review_report: AllocationReviewDigestPayload | dict[str, Any],
) -> dict[str, Any]:
    return {
        "strategy_suggestions": _list_payload(
            review_decision.get("strategy_suggestions")
        ),
        "param_adjustments": _dict_payload(review_decision.get("param_adjustments")),
        "agent_weight_adjustments": _dict_payload(
            review_decision.get("agent_weight_adjustments")
        ),
        "manager_budget_adjustments": _dict_payload(
            review_decision.get("manager_budget_adjustments")
        ),
        "return_pct": float(_report_field(eval_report, "return_pct", 0.0) or 0.0),
        "benchmark_passed": bool(_report_field(eval_report, "benchmark_passed", False)),
        "manager_review_count": int(
            _dict_payload(manager_review_report.get("summary")).get("manager_count") or 0
        ),
        "allocation_review_verdict": str(allocation_review_report.get("verdict") or ""),
        "reasoning": str(review_decision.get("reasoning") or ""),
    }


def _build_runtime_mutation_skip_stage_payload(
    *,
    pending_candidate_runtime_config_ref: str,
    config_adjustments: dict[str, Any],
    scoring_adjustments: dict[str, Any],
) -> dict[str, Any]:
    return {
        "skipped": True,
        "pending_candidate_runtime_config_ref": pending_candidate_runtime_config_ref,
        "auto_applied": False,
        "param_adjustments": dict(config_adjustments),
        "scoring_adjustments": dict(scoring_adjustments),
        "skip_reason": "pending_candidate_unresolved",
    }


def _build_runtime_mutation_generated_stage_payload(
    *,
    generated_runtime_config_ref: str,
    auto_applied: bool,
    config_adjustments: dict[str, Any],
    scoring_adjustments: dict[str, Any],
    mutation_meta: dict[str, Any],
) -> dict[str, Any]:
    return {
        "runtime_config_ref": generated_runtime_config_ref,
        "auto_applied": bool(auto_applied),
        "param_adjustments": dict(config_adjustments),
        "scoring_adjustments": dict(scoring_adjustments),
        "mutation_meta": dict(mutation_meta or {}),
    }


def _new_optimization_event(
    event_factory: Callable[..., Any],
    *,
    cycle_id: int | None,
    trigger: str,
    stage: str,
    lineage: dict[str, Any],
    decision: dict[str, Any] | None = None,
    suggestions: list[Any] | None = None,
    applied_change: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    notes: str = "",
    status: str = "ok",
    payload_field: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    kwargs: dict[str, Any] = {
        "cycle_id": int(cycle_id) if cycle_id is not None else None,
        "trigger": trigger,
        "stage": stage,
        "status": status,
        "lineage": dict(lineage or {}),
        "decision": dict(decision or {}),
        "suggestions": list(suggestions or []),
        "applied_change": dict(applied_change or {}),
        "evidence": dict(evidence or {}),
        "notes": str(notes or ""),
    }
    if payload_field is not None:
        kwargs[payload_field] = dict(payload or {})
    return event_factory(**kwargs)


def build_feedback_optimization_event(
    *,
    context: OptimizationBoundaryContext,
    trigger_reason: str,
    feedback_plan: dict[str, Any],
    feedback_adjustments: dict[str, Any],
    feedback_scoring: dict[str, Any],
    event_factory: Callable[..., Any],
) -> Any:
    stage_payload = _build_feedback_stage_payload(
        feedback_plan=feedback_plan,
        feedback_adjustments=feedback_adjustments,
        feedback_scoring=feedback_scoring,
    )
    return _new_optimization_event(
        event_factory,
        cycle_id=context.cycle_id,
        trigger=trigger_reason,
        stage="research_feedback",
        decision={
            "bias": stage_payload.get("bias"),
            "failed_horizons": list(stage_payload.get("failed_horizons") or []),
            "failed_checks": list(stage_payload.get("failed_checks") or []),
        },
        suggestions=list(stage_payload.get("suggestions") or []),
        applied_change=_build_runtime_adjustment_change(
            feedback_adjustments,
            feedback_scoring,
        ),
        lineage=build_optimization_lineage(
            context,
            deployment_stage="active",
            runtime_override_keys=sorted(
                {
                    *(str(key) for key in feedback_adjustments.keys()),
                    *(str(key) for key in feedback_scoring.keys()),
                }
            ),
        ),
        evidence={
            "failed_horizons": list(stage_payload.get("failed_horizons") or []),
            "failed_check_names": list(stage_payload.get("failed_checks") or []),
            "sample_count": int(stage_payload.get("sample_count") or 0),
            "severity": float(stage_payload.get("severity") or 0.0),
            "benchmark_context": dict(stage_payload.get("benchmark_context") or {}),
        },
        notes=str(stage_payload.get("summary") or ""),
        payload_field="research_feedback_payload",
        payload=stage_payload,
    )


def build_llm_optimization_event(
    *,
    context: OptimizationBoundaryContext,
    trade_dicts: list[dict[str, Any]],
    consecutive_losses: int,
    analysis: Any,
    event_factory: Callable[..., Any],
) -> Any:
    stage_payload = _build_llm_analysis_stage_payload(
        trade_dicts=trade_dicts,
        consecutive_losses=consecutive_losses,
        analysis=analysis,
    )
    return _new_optimization_event(
        event_factory,
        cycle_id=context.cycle_id,
        trigger="consecutive_losses",
        stage="llm_analysis",
        decision={"cause": stage_payload.get("cause", "")},
        suggestions=list(stage_payload.get("suggestions") or []),
        lineage=build_optimization_lineage(context, deployment_stage="active"),
        evidence={
            "consecutive_losses": int(stage_payload.get("consecutive_losses") or 0),
            "trade_record_count": int(stage_payload.get("trade_record_count") or 0),
        },
        payload_field="llm_analysis_payload",
        payload=stage_payload,
    )


def build_evolution_optimization_event(
    *,
    context: OptimizationBoundaryContext,
    fitness_scores: list[float],
    best_params: dict[str, Any],
    population_size: int,
    event_factory: Callable[..., Any],
) -> Any:
    stage_payload = _build_evolution_stage_payload(
        fitness_scores=fitness_scores,
        best_params=best_params,
        population_size=population_size,
    )
    return _new_optimization_event(
        event_factory,
        cycle_id=context.cycle_id,
        trigger="consecutive_losses",
        stage="evolution_engine",
        decision={
            "fitness_scores": list(stage_payload.get("fitness_scores") or []),
            "fitness_policy": str(stage_payload.get("fitness_policy") or ""),
        },
        applied_change=dict(best_params or {}),
        lineage=build_optimization_lineage(
            context,
            deployment_stage="active",
            runtime_override_keys=sorted(str(key) for key in (best_params or {}).keys()),
        ),
        evidence={
            "fitness_sample_count": int(stage_payload.get("fitness_sample_count") or 0),
            "population_size": int(stage_payload.get("population_size") or 0),
        },
        notes="population evolved",
        payload_field="evolution_engine_payload",
        payload=stage_payload,
    )


def build_optimization_error_event(
    *,
    context: OptimizationBoundaryContext,
    trigger_reason: str,
    exc: Exception,
    event_factory: Callable[..., Any],
) -> Any:
    stage_payload = _build_optimization_error_stage_payload(exc)
    return _new_optimization_event(
        event_factory,
        cycle_id=context.cycle_id,
        trigger=trigger_reason,
        stage="optimization_error",
        status="error",
        lineage=build_optimization_lineage(context, deployment_stage="active"),
        evidence={"exception_type": str(stage_payload.get("exception_type") or "")},
        notes=str(exc),
        payload_field="optimization_error_payload",
        payload=stage_payload,
    )


def build_review_boundary_event(
    controller: Any,
    *,
    cycle_id: int,
    manager_output: Any | None,
    execution_snapshot: dict[str, Any] | None,
    dominant_manager_id: str,
    optimization_event_factory: Any,
    review_decision: ReviewDecisionInputPayload | dict[str, Any],
    eval_report: Any,
    manager_review_report: ManagerReviewDigestPayload | dict[str, Any],
    allocation_review_report: AllocationReviewDigestPayload | dict[str, Any],
) -> ReviewBoundaryEventBundle:
    resolved_review_decision = _coerce_review_decision_input(review_decision)
    projection = _project_manager_compatibility(
        controller,
        manager_output=manager_output,
        execution_snapshot=dict(execution_snapshot or {}),
        dominant_manager_id_hint=str(dominant_manager_id or ""),
    )
    review_basis_window = cast(
        ReviewBasisWindowPayload,
        build_review_basis_window(
        controller,
        cycle_id=cycle_id,
        review_window=dict(getattr(controller, "experiment_review_window", {}) or {}),
        ),
    )
    stage_payload = _build_review_decision_stage_payload(
        review_decision=resolved_review_decision,
        eval_report=eval_report,
        manager_review_report=manager_review_report,
        allocation_review_report=allocation_review_report,
    )
    review_event = optimization_event_factory(
        cycle_id=cycle_id,
        trigger="dual_review",
        stage="review_decision",
        decision={
            "strategy_suggestions": list(stage_payload.get("strategy_suggestions") or []),
            "param_adjustments": dict(stage_payload.get("param_adjustments") or {}),
            "agent_weight_adjustments": dict(
                stage_payload.get("agent_weight_adjustments") or {}
            ),
            "manager_budget_adjustments": dict(
                stage_payload.get("manager_budget_adjustments") or {}
            ),
        },
        applied_change={},
        lineage=build_optimization_event_lineage(
            cycle_id=cycle_id,
            manager_id=str(projection.manager_id or ""),
            active_runtime_config_ref=normalize_config_ref(projection.active_runtime_config_ref),
            candidate_runtime_config_ref="",
            promotion_status="not_evaluated",
            deployment_stage="active",
            review_basis_window=cast(dict[str, Any], dict(review_basis_window)),
            fitness_source_cycles=[],
            runtime_override_keys=[],
        ),
        evidence={
            "return_pct": float(stage_payload.get("return_pct") or 0.0),
            "benchmark_passed": bool(stage_payload.get("benchmark_passed", False)),
            "strategy_suggestion_count": len(stage_payload.get("strategy_suggestions") or []),
            "param_adjustment_count": len(stage_payload.get("param_adjustments") or {}),
            "agent_weight_adjustment_count": len(
                stage_payload.get("agent_weight_adjustments") or {}
            ),
            "manager_review_count": int(stage_payload.get("manager_review_count") or 0),
            "allocation_review_verdict": str(
                stage_payload.get("allocation_review_verdict") or ""
            ),
        },
        notes=str(resolved_review_decision.get("reasoning") or ""),
        review_decision_payload=stage_payload,
    )
    if not hasattr(review_event, "applied_change"):
        review_event.applied_change = {}
    if not hasattr(review_event, "lineage"):
        review_event.lineage = {}
    return ReviewBoundaryEventBundle(
        review_event=review_event,
        review_basis_window=review_basis_window,
        manager_id=str(projection.manager_id or ""),
        active_runtime_config_ref=normalize_config_ref(projection.active_runtime_config_ref),
    )


def record_review_boundary_artifacts(
    controller: Any,
    *,
    cycle_id: int,
    manager_review_report: dict[str, Any],
    allocation_review_report: dict[str, Any],
) -> None:
    controller.artifact_recorder.save_manager_review_artifact(manager_review_report, cycle_id)
    controller.artifact_recorder.save_allocation_review_artifact(allocation_review_report, cycle_id)


def _resolve_review_applied_effects_payload(review_event: Any) -> ReviewAppliedEffectsPayload:
    payload = dict(getattr(review_event, "review_applied_effects_payload", {}) or {})
    return project_review_applied_effects_payload(payload)


def _project_review_progress_decision(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    progress = dict(payload or {})
    suggestions = [
        str(item).strip()
        for item in list(progress.get("suggestions") or [])
        if str(item).strip()
    ]
    decision = dict(progress.get("decision") or {})
    projected: dict[str, Any] = {
        "suggestion_count": len(suggestions),
    }
    verdict = str(decision.get("verdict") or progress.get("verdict") or "").strip()
    if verdict:
        projected["verdict"] = verdict
    subject_type = str(
        decision.get("subject_type") or progress.get("subject_type") or ""
    ).strip()
    if subject_type:
        projected["subject_type"] = subject_type
    return projected


def finalize_review_boundary_effects(
    controller: Any,
    *,
    cycle_id: int,
    review_decision: ReviewDecisionInputPayload | dict[str, Any],
    review_trace: dict[str, Any],
    manager_review_report: ManagerReviewDigestPayload | dict[str, Any],
    allocation_review_report: AllocationReviewDigestPayload | dict[str, Any],
    review_event: Any,
    review_applied: bool,
    review_basis_window: ReviewBasisWindowPayload,
    manager_id: str,
    active_runtime_config_ref: str,
) -> None:
    resolved_review_decision = _coerce_review_decision_input(review_decision)
    applied_effects = _resolve_review_applied_effects_payload(review_event)
    review_event.lineage = build_optimization_event_lineage(
        cycle_id=cycle_id,
        manager_id=str(manager_id or ""),
        active_runtime_config_ref=str(active_runtime_config_ref or ""),
        candidate_runtime_config_ref="",
        promotion_status="override_pending" if review_applied else "not_evaluated",
        deployment_stage="override" if review_applied else "active",
        review_basis_window=cast(dict[str, Any], dict(review_basis_window)),
        fitness_source_cycles=[],
        runtime_override_keys=sorted(
            {
                *(str(key) for key in dict(applied_effects.get("param_adjustments") or {}).keys()),
                *(str(key) for key in dict(applied_effects.get("agent_weight_adjustments") or {}).keys()),
                *(
                    str(key)
                    for key in dict(
                        applied_effects.get("manager_budget_adjustments") or {}
                    ).keys()
                ),
            }
        ),
    )
    append_event = getattr(controller, "_append_optimization_event", None)
    if callable(append_event):
        append_event(review_event)

    controller._emit_module_log(
        "review",
        "双层复盘结论",
        str(resolved_review_decision.get("reasoning") or "双层复盘完成"),
        cycle_id=cycle_id,
        kind="review_decision",
        details={
            "strategy_suggestions": _list_payload(
                resolved_review_decision.get("strategy_suggestions")
            ),
            "param_adjustments": _dict_payload(
                resolved_review_decision.get("param_adjustments")
            ),
            "agent_weight_adjustments": _dict_payload(
                resolved_review_decision.get("agent_weight_adjustments")
            ),
            "manager_budget_adjustments": _dict_payload(
                resolved_review_decision.get("manager_budget_adjustments")
            ),
            "manager_review_report": manager_review_report,
            "allocation_review_report": allocation_review_report,
            "review_trace": review_trace,
        },
        metrics={
            "review_applied": review_applied,
            "suggestion_count": len(
                _list_payload(resolved_review_decision.get("strategy_suggestions"))
            ),
        },
    )


def _append_optimization_event(controller: Any, event: Any) -> None:
    controller._append_optimization_event(event)


def _emit_optimization_module_log(
    controller: Any,
    *,
    title: str,
    message: str,
    cycle_id: int | None,
    kind: str,
    details: Any,
    metrics: dict[str, Any],
) -> None:
    controller._emit_module_log(
        "optimization",
        title,
        message,
        cycle_id=cycle_id,
        kind=kind,
        details=details,
        metrics=metrics,
    )


def emit_optimization_start_boundary(
    controller: Any,
    *,
    cycle_id: int | None,
    opening_message: str,
    opening_details: dict[str, Any],
) -> None:
    controller._emit_agent_status(
        "EvolutionOptimizer",
        "running",
        opening_message,
        cycle_id=cycle_id,
        stage="optimization",
        progress_pct=90,
        step=5,
        total_steps=6,
        details=opening_details,
    )
    controller._emit_module_log(
        "optimization",
        "触发自我优化",
        opening_message,
        cycle_id=cycle_id,
        kind="optimization_start",
        level="warn",
        details=opening_details,
    )


def record_feedback_optimization_boundary_effects(
    controller: Any,
    *,
    cycle_id: int | None,
    feedback_plan: dict[str, Any],
    feedback_event: Any,
) -> None:
    _append_optimization_event(controller, feedback_event)
    feedback_stage_payload = dict(
        getattr(feedback_event, "research_feedback_payload", {}) or {}
    )
    _emit_optimization_module_log(
        controller,
        title="应用 ask 侧校准调参",
        message=str(
            feedback_plan.get("summary") or "research feedback optimization"
        ),
        cycle_id=cycle_id,
        kind="research_feedback_gate",
        details=feedback_plan,
        metrics={
            "failed_check_count": len(feedback_plan.get("failed_check_names") or []),
            "sample_count": int(feedback_plan.get("sample_count") or 0),
            "param_adjustment_count": len(
                dict(feedback_stage_payload.get("param_adjustments") or {})
            ),
        },
    )


def record_llm_optimization_boundary_effects(
    controller: Any,
    *,
    cycle_id: int | None,
    llm_event: Any,
    analysis: Any,
    adjustments: dict[str, Any],
) -> None:
    _append_optimization_event(controller, llm_event)
    controller._emit_meeting_speech(
        "optimization",
        "EvolutionOptimizer",
        str(getattr(analysis, "cause", "") or ""),
        cycle_id=cycle_id,
        role="optimizer",
        suggestions=list(getattr(analysis, "suggestions", []) or []),
        decision={"adjustments": dict(adjustments or {})},
    )
    _emit_optimization_module_log(
        controller,
        title="LLM 亏损分析",
        message=str(getattr(analysis, "cause", "") or ""),
        cycle_id=cycle_id,
        kind="llm_analysis",
        details=list(getattr(analysis, "suggestions", []) or []),
        metrics={"adjustment_count": len(dict(adjustments or {}))},
    )


def record_evolution_optimization_boundary_effects(
    controller: Any,
    *,
    cycle_id: int | None,
    evo_event: Any,
    best_params: dict[str, Any],
    fitness_scores: list[float],
) -> None:
    _append_optimization_event(controller, evo_event)
    _emit_optimization_module_log(
        controller,
        title="进化引擎完成一轮迭代",
        message="基于最近收益分布更新参数种群",
        cycle_id=cycle_id,
        kind="evolution_engine",
        details=dict(best_params or {}),
        metrics={
            "fitness_samples": list(fitness_scores[-5:]),
            "fitness_policy": "benchmark_oriented_v1",
        },
    )


def record_runtime_mutation_boundary_effects(
    controller: Any,
    *,
    cycle_id: int | None,
    mutation_event: Any,
    mutation_log_message: str,
    adjustment_count: int,
    auto_apply_runtime_config_ref: str = "",
) -> None:
    runtime_stage_payload = dict(
        getattr(mutation_event, "runtime_config_mutation_payload", {}) or {}
    )
    skipped_stage_payload = dict(
        getattr(mutation_event, "runtime_config_mutation_skipped_payload", {}) or {}
    )
    if auto_apply_runtime_config_ref:
        routing_service = getattr(controller, "training_governance_service", None)
        if routing_service is not None:
            routing_service.reload_manager_runtime(controller, auto_apply_runtime_config_ref)
        else:
            controller._reload_manager_runtime(auto_apply_runtime_config_ref)
    _append_optimization_event(controller, mutation_event)
    _emit_optimization_module_log(
        controller,
        title="runtime 配置已变更",
        message=mutation_log_message,
        cycle_id=cycle_id,
        kind=str(getattr(mutation_event, "stage", "") or ""),
        details=runtime_stage_payload or skipped_stage_payload,
        metrics={"adjustment_count": int(adjustment_count)},
    )


def _runtime_mutation_override_keys(
    config_adjustments: dict[str, Any],
    scoring_adjustments: dict[str, Any],
) -> list[str]:
    return sorted(
        {
            *(str(key) for key in config_adjustments.keys()),
            *(str(key) for key in scoring_adjustments.keys()),
        }
    )


def _build_runtime_mutation_lineage(
    *,
    context: OptimizationBoundaryContext,
    candidate_runtime_config_ref: str,
    deployment_stage: str,
    config_adjustments: dict[str, Any],
    scoring_adjustments: dict[str, Any],
    promotion_status: str,
) -> dict[str, Any]:
    return build_optimization_lineage(
        context,
        candidate_runtime_config_ref=candidate_runtime_config_ref,
        deployment_stage=deployment_stage,
        runtime_override_keys=_runtime_mutation_override_keys(
            config_adjustments,
            scoring_adjustments,
        ),
        promotion_status=promotion_status,
    )


def _build_runtime_mutation_skip_event(
    *,
    context: OptimizationBoundaryContext,
    cycle_id: int | None,
    trigger_reason: str,
    pending_candidate_runtime_config_ref: str,
    config_adjustments: dict[str, Any],
    scoring_adjustments: dict[str, Any],
    event_factory: Callable[..., Any],
) -> Any:
    stage_payload = _build_runtime_mutation_skip_stage_payload(
        pending_candidate_runtime_config_ref=pending_candidate_runtime_config_ref,
        config_adjustments=config_adjustments,
        scoring_adjustments=scoring_adjustments,
    )
    return _new_optimization_event(
        event_factory,
        cycle_id=cycle_id,
        trigger=trigger_reason,
        stage="runtime_config_mutation_skipped",
        decision={
            "skipped": bool(stage_payload.get("skipped", False)),
            "pending_candidate_runtime_config_ref": str(
                stage_payload.get("pending_candidate_runtime_config_ref") or ""
            ),
            "auto_applied": bool(stage_payload.get("auto_applied", False)),
        },
        applied_change=_build_runtime_adjustment_change(
            config_adjustments,
            scoring_adjustments,
        ),
        lineage=_build_runtime_mutation_lineage(
            context=context,
            candidate_runtime_config_ref=pending_candidate_runtime_config_ref,
            deployment_stage="candidate",
            config_adjustments=config_adjustments,
            scoring_adjustments=scoring_adjustments,
            promotion_status="candidate_generated",
        ),
        evidence={
            "skip_reason": str(stage_payload.get("skip_reason") or ""),
            "pending_candidate_runtime_config_ref": str(
                stage_payload.get("pending_candidate_runtime_config_ref") or ""
            ),
            "auto_applied": bool(stage_payload.get("auto_applied", False)),
        },
        notes="existing pending candidate reused; skip generating another candidate runtime config",
        payload_field="runtime_config_mutation_skipped_payload",
        payload=stage_payload,
    )


def _build_runtime_mutation_generated_event(
    *,
    context: OptimizationBoundaryContext,
    cycle_id: int | None,
    trigger_reason: str,
    generated_runtime_config_ref: str,
    auto_applied: bool,
    config_adjustments: dict[str, Any],
    scoring_adjustments: dict[str, Any],
    mutation_meta: dict[str, Any],
    event_factory: Callable[..., Any],
) -> Any:
    stage_payload = _build_runtime_mutation_generated_stage_payload(
        generated_runtime_config_ref=generated_runtime_config_ref,
        auto_applied=auto_applied,
        config_adjustments=config_adjustments,
        scoring_adjustments=scoring_adjustments,
        mutation_meta=mutation_meta,
    )
    return _new_optimization_event(
        event_factory,
        cycle_id=cycle_id,
        trigger=trigger_reason,
        stage="runtime_config_mutation",
        decision={
            "runtime_config_ref": str(stage_payload.get("runtime_config_ref") or ""),
            "auto_applied": bool(stage_payload.get("auto_applied", False)),
        },
        applied_change=_build_runtime_adjustment_change(
            config_adjustments,
            scoring_adjustments,
        ),
        lineage=_build_runtime_mutation_lineage(
            context=context,
            candidate_runtime_config_ref=generated_runtime_config_ref,
            deployment_stage="active" if auto_applied else "candidate",
            config_adjustments=config_adjustments,
            scoring_adjustments=scoring_adjustments,
            promotion_status="candidate_auto_applied" if auto_applied else "candidate_generated",
        ),
        evidence={
            "mutation_meta": dict(stage_payload.get("mutation_meta") or {}),
            "auto_applied": bool(stage_payload.get("auto_applied", False)),
        },
        notes=(
            "active runtime config mutated"
            if auto_applied
            else "candidate runtime config generated; active runtime config unchanged"
        ),
        payload_field="runtime_config_mutation_payload",
        payload=stage_payload,
    )


def _runtime_mutation_log_message(
    *,
    generated_runtime_config_ref: str,
    auto_applied: bool,
) -> tuple[str, str]:
    message = (
        f"新的 runtime 配置已生成并已接管 active：{generated_runtime_config_ref}"
        if auto_applied
        else f"新的候选 runtime 配置已生成（未自动接管 active）：{generated_runtime_config_ref}"
    )
    return message, generated_runtime_config_ref if auto_applied else ""


def build_runtime_mutation_boundary(
    controller: Any,
    *,
    context: OptimizationBoundaryContext,
    cycle_id: int | None,
    trigger_reason: str,
    active_runtime_config_ref: str,
    config_adjustments: dict[str, Any],
    scoring_adjustments: dict[str, Any],
    feedback_plan: dict[str, Any] | None,
    event_factory: Callable[..., Any],
) -> RuntimeMutationBoundaryBundle:
    pending_candidate_runtime_config_ref = _latest_open_candidate_runtime_config_ref(controller)
    if pending_candidate_runtime_config_ref and not bool(getattr(controller, "auto_apply_mutation", False)):
        mutation_event = _build_runtime_mutation_skip_event(
            context=context,
            cycle_id=cycle_id,
            trigger_reason=trigger_reason,
            pending_candidate_runtime_config_ref=pending_candidate_runtime_config_ref,
            config_adjustments=config_adjustments,
            scoring_adjustments=scoring_adjustments,
            event_factory=event_factory,
        )
        return RuntimeMutationBoundaryBundle(
            mutation_event=mutation_event,
            mutation_log_message="已有 pending candidate，跳过重复生成新的 runtime 候选配置",
        )

    mutation = controller.runtime_config_mutator.mutate(
        active_runtime_config_ref,
        param_adjustments=config_adjustments,
        scoring_adjustments=scoring_adjustments or None,
        narrative_adjustments={"last_trigger": trigger_reason},
        generation_label=f"cycle_{int(cycle_id or 0):04d}",
        parent_meta={
            "cycle_id": cycle_id,
            "trigger": trigger_reason,
            "auto_apply": getattr(controller, "auto_apply_mutation", False),
            "feedback_bias": dict((feedback_plan or {}).get("recommendation") or {}).get("bias", ""),
        },
    )
    auto_applied = bool(getattr(controller, "auto_apply_mutation", False))
    generated_runtime_config_ref = str(mutation["runtime_config_ref"])
    mutation_event = _build_runtime_mutation_generated_event(
        context=context,
        cycle_id=cycle_id,
        trigger_reason=trigger_reason,
        generated_runtime_config_ref=generated_runtime_config_ref,
        auto_applied=auto_applied,
        config_adjustments=config_adjustments,
        scoring_adjustments=scoring_adjustments,
        mutation_meta=dict(mutation.get("meta") or {}),
        event_factory=event_factory,
    )
    mutation_log_message, auto_apply_runtime_config_ref = _runtime_mutation_log_message(
        generated_runtime_config_ref=generated_runtime_config_ref,
        auto_applied=auto_applied,
    )
    return RuntimeMutationBoundaryBundle(
        mutation_event=mutation_event,
        mutation_log_message=mutation_log_message,
        auto_apply_runtime_config_ref=auto_apply_runtime_config_ref,
    )


def emit_optimization_error_boundary(
    controller: Any,
    *,
    cycle_id: int | None,
    err_event: Any,
    exc: Exception,
) -> None:
    _append_optimization_event(controller, err_event)
    controller._emit_agent_status(
        "EvolutionOptimizer",
        "error",
        f"优化过程出错: {exc}",
        cycle_id=cycle_id,
        stage="optimization",
        progress_pct=92,
        step=5,
        total_steps=6,
    )


def emit_optimization_completed_boundary(
    controller: Any,
    *,
    cycle_id: int | None,
    event_count: int,
    trigger_reason: str,
) -> None:
    controller._emit_agent_status(
        "EvolutionOptimizer",
        "completed",
        "优化完成，继续训练...",
        cycle_id=cycle_id,
        stage="optimization",
        progress_pct=94,
        step=5,
        total_steps=6,
        details={"event_count": int(event_count), "trigger_reason": str(trigger_reason or "")},
    )


class TrainingObservabilityService:
    """Owns controller-side event shaping and progress adaptation."""

    @staticmethod
    def thinking_excerpt(reasoning: Any, limit: int = 200) -> str:
        if not reasoning:
            return ""
        if isinstance(reasoning, dict):
            candidate = (
                reasoning.get("reasoning")
                or reasoning.get("summary")
                or reasoning.get("regime")
                or ""
            )
            return str(candidate)[:limit]
        if isinstance(reasoning, (list, tuple)):
            return "；".join(str(item) for item in reasoning[:5])[:limit]
        return str(reasoning)[:limit]

    def event_context(self, controller: Any, cycle_id: int | None = None) -> dict[str, Any]:
        meta = dict(controller.last_cycle_meta or {})
        context: dict[str, Any] = {"timestamp": datetime.now().isoformat()}
        if cycle_id is not None:
            context["cycle_id"] = cycle_id
        elif meta.get("cycle_id") is not None:
            context["cycle_id"] = meta.get("cycle_id")
        if meta.get("cutoff_date"):
            context["cutoff_date"] = meta.get("cutoff_date")
        return context

    def emit_agent_status(
        self,
        controller: Any,
        *,
        event_emitter: Callable[[str, dict[str, Any]], None],
        agent: str,
        status: str,
        message: str,
        cycle_id: int | None = None,
        stage: str = "",
        progress_pct: int | None = None,
        step: int | None = None,
        total_steps: int | None = None,
        thinking: str = "",
        selected_stocks: list[str] | None = None,
        details: Any = None,
        **extra: Any,
    ) -> None:
        payload = {
            **self.event_context(controller, cycle_id),
            "agent": agent,
            "status": status,
            "message": message,
        }
        if stage:
            payload["stage"] = stage
        if progress_pct is not None:
            payload["progress_pct"] = int(progress_pct)
        if step is not None:
            payload["step"] = int(step)
        if total_steps is not None:
            payload["total_steps"] = int(total_steps)
        if thinking:
            payload["thinking"] = thinking
        if selected_stocks:
            payload["selected_stocks"] = list(selected_stocks)
        if details is not None:
            payload["details"] = details
        payload.update(extra)
        event_emitter("agent_status", payload)
        event_emitter("agent_progress", dict(payload))

    def emit_module_log(
        self,
        controller: Any,
        *,
        event_emitter: Callable[[str, dict[str, Any]], None],
        module: str,
        title: str,
        message: str = "",
        cycle_id: int | None = None,
        kind: str = "log",
        level: str = "info",
        details: Any = None,
        metrics: dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        payload = {
            **self.event_context(controller, cycle_id),
            "module": module,
            "title": title,
            "message": message,
            "kind": kind,
            "level": level,
        }
        if details is not None:
            payload["details"] = details
        if metrics:
            payload["metrics"] = metrics
        payload.update(extra)
        event_emitter("module_log", payload)

    def emit_meeting_speech(
        self,
        controller: Any,
        *,
        event_emitter: Callable[[str, dict[str, Any]], None],
        meeting: str,
        speaker: str,
        speech: str,
        cycle_id: int | None = None,
        role: str = "",
        picks: list[dict[str, Any]] | list[str] | None = None,
        suggestions: list[str] | None = None,
        decision: dict[str, Any] | None = None,
        confidence: Any = None,
        **extra: Any,
    ) -> None:
        payload = {
            **self.event_context(controller, cycle_id),
            "meeting": meeting,
            "speaker": speaker,
            "speech": str(speech or "").strip(),
        }
        if role:
            payload["role"] = role
        if picks:
            payload["picks"] = picks
        if suggestions:
            payload["suggestions"] = suggestions
        if decision:
            payload["decision"] = decision
        if confidence is not None:
            payload["confidence"] = confidence
        payload.update(extra)
        event_emitter("meeting_speech", payload)

    def handle_selection_progress(self, controller: Any, payload: dict[str, Any]) -> None:
        default_agent = "ManagerSelection"
        agent = str(payload.get("agent") or default_agent)
        status = str(payload.get("status") or "running")
        stage = str(payload.get("stage") or "selection")
        progress_pct = payload.get("progress_pct")
        if progress_pct is None:
            progress_pct = {
                "ManagerSelection": 54,
                "TrendHunter": 38,
                "Contrarian": 46,
            }.get(agent, 40)
            if status == "completed":
                progress_pct = min(100, int(progress_pct) + 8)
            elif status == "error":
                progress_pct = int(progress_pct)
        controller._emit_agent_status(
            agent,
            status,
            str(payload.get("message") or ""),
            stage=stage,
            progress_pct=int(progress_pct),
            step=payload.get("step"),
            total_steps=payload.get("total_steps"),
            thinking=controller._thinking_excerpt(
                payload.get("speech") or payload.get("reasoning") or payload.get("overall_view")
            ),
            details=payload.get("details"),
            picks=payload.get("picks"),
        )
        speech = str(payload.get("speech") or payload.get("overall_view") or "").strip()
        if speech:
            controller._emit_meeting_speech(
                "selection",
                agent,
                speech,
                role="selector",
                picks=payload.get("picks"),
                confidence=payload.get("confidence"),
            )
        picks = payload.get("picks") or []
        if picks:
            controller._emit_module_log(
                "selection",
                f"{agent} 输出候选",
                f"推荐 {len(picks)} 只候选股票",
                kind="selection_candidates",
                details=picks[:10],
                metrics={"candidate_count": len(picks)},
            )

    def handle_review_progress(self, controller: Any, payload: dict[str, Any]) -> None:
        default_agent = "DualReview"
        agent = str(payload.get("agent") or default_agent)
        status = str(payload.get("status") or "running")
        stage = str(payload.get("stage") or "review")
        progress_pct = payload.get("progress_pct")
        if progress_pct is None:
            progress_pct = {
                "DualReview": 93,
                "ManagerReview": 90,
                "AllocationReview": 94,
                "Strategist": 82,
                "EvoJudge": 88,
                "ReviewDecision": 92,
            }.get(agent, 85)
        controller._emit_agent_status(
            agent,
            status,
            str(payload.get("message") or ""),
            stage=stage,
            progress_pct=int(progress_pct),
            thinking=controller._thinking_excerpt(payload.get("speech") or payload.get("reasoning")),
            details=payload.get("details"),
        )
        speech = str(payload.get("speech") or payload.get("reasoning") or "").strip()
        review_decision = _project_review_progress_decision(payload)
        if speech:
            controller._emit_meeting_speech(
                "review",
                agent,
                speech,
                role="reviewer",
                suggestions=payload.get("suggestions"),
                decision=review_decision,
                confidence=payload.get("confidence"),
            )
        suggestions = payload.get("suggestions") or []
        if suggestions or review_decision:
            controller._emit_module_log(
                "review",
                f"{agent} 复盘输出",
                str(payload.get("message") or ""),
                kind="review_update",
                details=suggestions or review_decision,
            )

    def mark_cycle_skipped(
        self,
        controller: Any,
        *,
        event_emitter: Callable[[str, dict[str, Any]], None],
        cycle_id: int,
        cutoff_date: str,
        stage: str,
        reason: str,
        **extra: Any,
    ) -> None:
        meta = {
            "status": "no_data",
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "stage": stage,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
            **extra,
        }
        controller.last_cycle_meta = meta
        controller._emit_module_log(
            stage,
            f"周期 #{cycle_id} 已跳过",
            reason,
            cycle_id=cycle_id,
            kind="cycle_skipped",
            level="warn",
            details=extra or None,
        )
        event_emitter("cycle_skipped", meta)

_DEFAULT_OPTIMIZATION_FEEDBACK_GATE = {
    "min_sample_count": 5,
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


DEFAULT_EFFECT_WINDOW_CYCLES = 3
_LOWER_IS_BETTER_EFFECT_METRICS = {"avg_max_drawdown"}
_EFFECT_METRIC_TOLERANCES = {
    "avg_return_pct": 0.20,
    "benchmark_pass_rate": 0.10,
    "avg_strategy_score": 0.05,
    "avg_sharpe_ratio": 0.10,
    "avg_max_drawdown": 0.25,
}


def _proposal_copy_dict(value: Any) -> dict[str, Any]:
    return deepcopy(dict(value or {}))


def _proposal_string_items(values: Any) -> list[str]:
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


def _proposal_cycle_id(item: Any) -> int:
    try:
        return int(_record_field(item, "cycle_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _proposal_effect_target_metrics(
    proposal_kind: str,
    patch: dict[str, Any],
) -> list[str]:
    if proposal_kind == "scoring_adjustment":
        return ["avg_strategy_score", "benchmark_pass_rate"]
    if proposal_kind == "agent_weight_adjustment":
        return ["avg_return_pct", "avg_strategy_score"]
    metrics = ["avg_return_pct", "benchmark_pass_rate"]
    risk_keys = {
        "position_size",
        "cash_reserve",
        "stop_loss_pct",
        "trailing_pct",
        "max_positions",
    }
    if any(str(key) in risk_keys for key in dict(patch or {}).keys()):
        metrics.append("avg_max_drawdown")
    return metrics


def _proposal_metric_value(item: Any, metric_name: str) -> float | None:
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


def _proposal_aggregate_metric(cycles: list[Any], metric_name: str) -> float | None:
    values = [
        value
        for value in (
            _proposal_metric_value(item, metric_name) for item in list(cycles or [])
        )
        if value is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _proposal_effect_metric_status(
    metric_name: str,
    baseline: float,
    observed: float,
) -> tuple[str, float]:
    delta = observed - baseline
    tolerance = float(_EFFECT_METRIC_TOLERANCES.get(metric_name, 0.05))
    if metric_name in _LOWER_IS_BETTER_EFFECT_METRICS:
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


def _proposal_summarize_effect_result(
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
        return (
            "effect window completed but evidence was inconclusive across "
            f"{observed_cycles} cycles"
        )
    improved = sum(
        1 for item in metric_results if str(item.get("status") or "") == "improved"
    )
    worsened = sum(
        1 for item in metric_results if str(item.get("status") or "") == "worsened"
    )
    neutral = sum(
        1 for item in metric_results if str(item.get("status") or "") == "neutral"
    )
    return (
        f"{overall_status}: improved={improved}, worsened={worsened}, "
        f"neutral={neutral}, observed_cycles={observed_cycles}"
    )


def _proposal_suggestion_text(
    proposal: dict[str, Any],
    *,
    source: str,
    rationale: str,
    patch: dict[str, Any],
) -> str:
    metadata = _proposal_copy_dict(proposal.get("metadata") or {})
    explicit = str(
        proposal.get("suggestion_text") or metadata.get("suggestion_text") or ""
    ).strip()
    if explicit:
        return explicit
    evidence = _proposal_copy_dict(proposal.get("evidence") or {})
    for key in ("strategy_suggestions", "suggestions"):
        suggestions = _proposal_string_items(
            evidence.get(key) or metadata.get(key) or []
        )
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
    payload = _proposal_copy_dict(proposal or {})
    cycle_id = int(payload.get("cycle_id") or default_cycle_id or 0)
    payload["cycle_id"] = cycle_id
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
    patch = _proposal_copy_dict(payload.get("patch") or {})
    rationale = str(payload.get("rationale") or "").strip()
    metadata = _proposal_copy_dict(payload.get("metadata") or {})
    proposal_kind = str(
        metadata.get("proposal_kind") or "runtime_param_adjustment"
    ).strip()
    payload["suggestion_text"] = _proposal_suggestion_text(
        payload,
        source=source,
        rationale=rationale,
        patch=patch,
    )

    effect_window = _proposal_copy_dict(payload.get("effect_window") or {})
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

    target_metrics = _proposal_string_items(
        payload.get("effect_target_metrics")
        or metadata.get("effect_target_metrics")
        or _proposal_effect_target_metrics(proposal_kind, patch)
    )
    payload["effect_target_metrics"] = target_metrics

    adoption_ref = _proposal_copy_dict(payload.get("adoption_ref") or {})
    candidate_runtime_config_ref = str(
        adoption_ref.get("candidate_runtime_config_ref")
        or adoption_ref.get("candidate_config_ref")
        or ""
    )
    payload["adoption_status"] = (
        str(payload.get("adoption_status") or "queued").strip() or "queued"
    )
    payload["adoption_ref"] = {
        "decision_cycle_id": adoption_ref.get("decision_cycle_id"),
        "decision_stage": str(adoption_ref.get("decision_stage") or "proposal_recorded"),
        "decision_reason": str(
            adoption_ref.get("decision_reason") or "queued_for_candidate_governance"
        ),
        "candidate_runtime_config_ref": candidate_runtime_config_ref,
        "candidate_config_ref": str(adoption_ref.get("candidate_config_ref") or ""),
        "candidate_version_id": str(adoption_ref.get("candidate_version_id") or ""),
        "pending_candidate_ref": str(adoption_ref.get("pending_candidate_ref") or ""),
        "proposal_bundle_id": str(adoption_ref.get("proposal_bundle_id") or ""),
        "block_reasons": _proposal_string_items(adoption_ref.get("block_reasons") or []),
    }

    effect_result = _proposal_copy_dict(payload.get("effect_result") or {})
    effect_status = (
        str(payload.get("effect_status") or "pending_adoption").strip()
        or "pending_adoption"
    )
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

    sorted_cycles = sorted(list(cycle_history or []), key=_proposal_cycle_id)
    if current_cycle_id is None:
        current_cycle_id = max((_proposal_cycle_id(item) for item in sorted_cycles), default=0)
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
        if start_cycle_id <= _proposal_cycle_id(item) <= end_cycle_id
    ]

    if int(current_cycle_id or 0) < evaluation_after_cycle_id:
        payload["effect_status"] = "pending"
        payload["effect_result"] = {
            "status": "pending",
            "observed_cycles": len(observed_window_cycles),
            "summary": _proposal_summarize_effect_result(
                overall_status="pending",
                observed_cycles=len(observed_window_cycles),
                metric_results=[],
            ),
            "evaluation_after_cycle_id": evaluation_after_cycle_id,
        }
        return payload

    baseline_cycles = [
        item for item in sorted_cycles if _proposal_cycle_id(item) <= decision_cycle_id
    ]
    window_cycles = max(
        1,
        int(effect_window.get("window_cycles") or DEFAULT_EFFECT_WINDOW_CYCLES),
    )
    baseline_cycles = baseline_cycles[-window_cycles:]
    metric_results: list[dict[str, Any]] = []
    target_metrics = _proposal_string_items(payload.get("effect_target_metrics") or [])

    for metric_name in target_metrics:
        baseline_value = _proposal_aggregate_metric(baseline_cycles, metric_name)
        observed_value = _proposal_aggregate_metric(observed_window_cycles, metric_name)
        if baseline_value is None or observed_value is None:
            continue
        status, delta = _proposal_effect_metric_status(
            metric_name,
            baseline_value,
            observed_value,
        )
        metric_results.append(
            {
                "metric": metric_name,
                "baseline_value": baseline_value,
                "observed_value": observed_value,
                "delta": delta,
                "status": status,
                "direction": (
                    "lower_is_better"
                    if metric_name in _LOWER_IS_BETTER_EFFECT_METRICS
                    else "higher_is_better"
                ),
                "tolerance": float(_EFFECT_METRIC_TOLERANCES.get(metric_name, 0.05)),
            }
        )

    if not metric_results:
        overall_status = "inconclusive"
    else:
        improved_count = sum(
            1 for item in metric_results if str(item.get("status") or "") == "improved"
        )
        worsened_count = sum(
            1 for item in metric_results if str(item.get("status") or "") == "worsened"
        )
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
        "summary": _proposal_summarize_effect_result(
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
    candidate_runtime_config_ref: str = "",
    candidate_version_id: str = "",
    pending_candidate_ref: str = "",
    proposal_bundle_id: str = "",
    block_reasons: list[str] | None = None,
) -> dict[str, Any]:
    payload = ensure_proposal_tracking_fields(
        proposal,
        default_cycle_id=decision_cycle_id,
    )
    normalized_status = str(adoption_status or "queued").strip() or "queued"
    normalized_block_reasons = _proposal_string_items(block_reasons or [])
    resolved_candidate_ref = (
        str(candidate_runtime_config_ref or "").strip()
        or str(candidate_config_ref or "").strip()
    )

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
        "candidate_runtime_config_ref": resolved_candidate_ref,
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


def _resolve_update_cycle_proposal_bundle() -> Callable[..., Any] | None:
    try:
        update = getattr(_persistence_module(), "update_cycle_proposal_bundle", None)
    except Exception:
        return None
    return update if callable(update) else None


def refresh_cycle_history_suggestion_effects(
    controller: Any | None,
    *,
    cycle_history: list[Any] | None = None,
) -> dict[str, Any]:
    updated_bundle_count = 0
    evaluated_suggestion_count = 0
    completed_effect_count = 0
    sorted_cycles = sorted(list(cycle_history or []), key=_proposal_cycle_id)
    current_cycle_id = max((_proposal_cycle_id(item) for item in sorted_cycles), default=0)
    update_cycle_proposal_bundle = _resolve_update_cycle_proposal_bundle()

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
        proposal_bundle["suggestion_tracking_summary"] = (
            build_suggestion_tracking_summary(refreshed)
        )
        bundle_path = str(proposal_bundle.get("bundle_path") or "")
        if bundle_path and callable(update_cycle_proposal_bundle):
            try:
                proposal_bundle = dict(
                    update_cycle_proposal_bundle(
                        controller,
                        bundle_path=bundle_path,
                        proposals=refreshed,
                    )
                    or proposal_bundle
                )
            except TypeError:
                proposal_bundle = dict(
                    update_cycle_proposal_bundle(
                        controller,
                        bundle_path,
                        refreshed,
                    )
                    or proposal_bundle
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
        adoption_status_counts[adoption_status] = (
            adoption_status_counts.get(adoption_status, 0) + 1
        )
        effect_status_counts[effect_status] = (
            effect_status_counts.get(effect_status, 0) + 1
        )
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
        "adopted_suggestion_count": int(
            adoption_status_counts.get("adopted_to_candidate", 0) or 0
        ),
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
        "inconclusive_suggestion_count": int(
            effect_status_counts.get("inconclusive", 0) or 0
        ),
        "source_counts": source_counts,
    }


def build_self_assessment_snapshot(
    snapshot_factory: Callable[..., Any],
    cycle_result: Any,
    assessment_payload: dict[str, Any],
) -> Any:
    payload = dict(assessment_payload or {})
    return snapshot_factory(
        cycle_id=cycle_result.cycle_id,
        cutoff_date=cycle_result.cutoff_date,
        regime=str(payload.get("regime") or "unknown"),
        plan_source=str(payload.get("plan_source") or "unknown"),
        return_pct=cycle_result.return_pct,
        is_profit=cycle_result.is_profit,
        sharpe_ratio=float(payload.get("sharpe_ratio", 0.0) or 0.0),
        max_drawdown=float(payload.get("max_drawdown", 0.0) or 0.0),
        excess_return=float(payload.get("excess_return", 0.0) or 0.0),
        benchmark_passed=bool(payload.get("benchmark_passed", False)),
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
        active_runtime_config_ref = str(lineage_record.get("active_runtime_config_ref") or "")
        candidate_runtime_config_ref = str(lineage_record.get("candidate_runtime_config_ref") or "")
        if candidate_runtime_config_ref and candidate_runtime_config_ref != active_runtime_config_ref:
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
        "active_candidate_drift_count": active_candidate_drift_count,
        "active_candidate_drift_rate": active_candidate_drift_count / denominator,
        "candidate_pending_count": candidate_pending_count,
        "candidate_pending_rate": candidate_pending_count / denominator,
        "override_pending_count": override_pending_count,
        "override_pending_rate": override_pending_count / denominator,
        "rejected_candidate_count": rejected_candidate_count,
        "active_stage_count": active_stage_count,
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


def _build_freeze_boundary_summary(
    cycle_history: list[Any],
    *,
    research_feedback: dict[str, Any] | None,
    resolved_freeze_gate_policy: dict[str, Any],
) -> _FreezeBoundarySummary:
    return _FreezeBoundarySummary(
        governance_metrics=build_governance_metrics(cycle_history),
        realism_summary=build_realism_summary(cycle_history),
        research_feedback_gate=evaluate_research_feedback_gate(
            research_feedback,
            policy=dict(
                (resolved_freeze_gate_policy or {}).get("research_feedback") or {}
            ),
            defaults=_DEFAULT_FREEZE_FEEDBACK_GATE,
        ),
    )


def _freeze_boundary_summary_payload(
    summary: _FreezeBoundarySummary,
) -> dict[str, dict[str, Any]]:
    return {
        "governance_metrics": dict(summary.governance_metrics or {}),
        "realism_summary": dict(summary.realism_summary or {}),
        "research_feedback_gate": dict(summary.research_feedback_gate or {}),
    }


def _build_freeze_gate_checks(
    *,
    rolling: dict[str, Any],
    freeze_total_cycles: int,
    freeze_profit_required: int,
    resolved_freeze_gate_policy: dict[str, Any],
    governance_metrics: dict[str, Any],
) -> list[dict[str, Any]]:
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
    return checks


def evaluate_research_feedback_gate(
    research_feedback: dict[str, Any] | None,
    policy: dict[str, Any] | None = None,
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _evaluate_research_feedback_gate(
        research_feedback,
        policy=policy,
        defaults=defaults,
    )


def evaluate_freeze_gate(
    cycle_history: list[Any],
    freeze_total_cycles: int,
    freeze_profit_required: int,
    freeze_gate_policy: dict[str, Any],
    rolling: dict[str, Any],
    research_feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_freeze_gate_policy = normalize_freeze_gate_policy(freeze_gate_policy)
    summary = _build_freeze_boundary_summary(
        cycle_history,
        research_feedback=research_feedback,
        resolved_freeze_gate_policy=resolved_freeze_gate_policy,
    )
    if len(cycle_history) < freeze_total_cycles or not rolling:
        return {
            "ready": False,
            "passed": False,
            "checks": [],
            **_freeze_boundary_summary_payload(summary),
        }
    checks = _build_freeze_gate_checks(
        rolling=rolling,
        freeze_total_cycles=freeze_total_cycles,
        freeze_profit_required=freeze_profit_required,
        resolved_freeze_gate_policy=resolved_freeze_gate_policy,
        governance_metrics=summary.governance_metrics,
    )
    base_passed = all(check.get("passed") for check in checks)
    return {
        "ready": True,
        "passed": base_passed and bool(summary.research_feedback_gate.get("passed", True)),
        "checks": checks,
        **_freeze_boundary_summary_payload(summary),
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


def _build_freeze_gate_report_payload(
    *,
    freeze_total_cycles: int,
    freeze_profit_required: int,
    resolved_freeze_gate_policy: dict[str, Any],
    governance_defaults: dict[str, Any],
) -> dict[str, Any]:
    return {
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
    }


def _build_training_report_state(
    *,
    attempted_cycles: int,
    skipped_cycle_count: int,
    cycle_history: list[Any],
    current_params: dict[str, Any],
    is_frozen: bool,
    self_assessment: dict[str, Any],
    research_feedback: dict[str, Any] | None,
    freeze_gate_evaluation: dict[str, Any] | None,
) -> dict[str, Any]:
    successful = len(cycle_history)
    skipped = max(skipped_cycle_count, attempted_cycles - successful)
    profits = sum(1 for r in cycle_history if r.is_profit)
    return {
        "total_cycles": attempted_cycles,
        "attempted_cycles": attempted_cycles,
        "successful_cycles": successful,
        "skipped_cycles": skipped,
        "profit_cycles": profits,
        "loss_cycles": successful - profits,
        "profit_rate": profits / successful if successful > 0 else 0,
        "current_params": current_params,
        "is_frozen": is_frozen,
        "self_assessment": self_assessment,
        "research_feedback": dict(research_feedback or {}),
        "governance_metrics": build_governance_metrics(cycle_history),
        "realism_summary": build_realism_summary(cycle_history),
        "freeze_gate_evaluation": dict(freeze_gate_evaluation or {}),
    }


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
    governance_defaults = dict(resolve_governance_matrix().get("freeze") or {})
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
        "total_cycles": total,
        "total_profit_count": profits,
        "profit_rate": profits / total if total > 0 else 0,
        "recent_10_profit_count": sum(1 for r in cycle_history[-10:] if r.is_profit),
        "final_params": current_params,
        "frozen_time": datetime.now().isoformat(),
        "self_assessment": rolling,
        "research_feedback": dict(research_feedback or {}),
        "governance_metrics": build_governance_metrics(cycle_history),
        "realism_summary": build_realism_summary(cycle_history),
        "freeze_gate": _build_freeze_gate_report_payload(
            freeze_total_cycles=freeze_total_cycles,
            freeze_profit_required=freeze_profit_required,
            resolved_freeze_gate_policy=resolved_freeze_gate_policy,
            governance_defaults=governance_defaults,
        ),
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
    report_state = _build_training_report_state(
        attempted_cycles=attempted,
        skipped_cycle_count=skipped_cycle_count,
        cycle_history=cycle_history,
        current_params=current_params,
        is_frozen=is_frozen,
        self_assessment=self_assessment,
        research_feedback=research_feedback,
        freeze_gate_evaluation=freeze_gate_evaluation,
    )

    if not cycle_history:
        return {
            "status": "no_data",
            **report_state,
            "is_frozen": False,
        }

    status = (
        "completed_with_skips"
        if int(report_state.get("skipped_cycles", 0) or 0)
        else "completed"
    )
    return {
        "status": status,
        **report_state,
    }

_CYCLE_FILE_RE = re.compile(r"^cycle_(\d+)\.json$")


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return payload


def _cycle_sort_key(path: Path) -> int:
    match = _CYCLE_FILE_RE.match(path.name)
    if not match:
        raise ValueError(f"Unsupported cycle payload name: {path.name}")
    return int(match.group(1))


def load_cycle_payloads(run_dir: str | Path) -> list[dict[str, Any]]:
    root = Path(run_dir).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Run directory does not exist: {root}")

    payloads: list[dict[str, Any]] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        if not _CYCLE_FILE_RE.match(path.name):
            continue
        payload = _read_json(path)
        payload["_artifact_path"] = str(path)
        if "cycle_id" not in payload:
            payload["cycle_id"] = _cycle_sort_key(path)
        payloads.append(payload)
    payloads.sort(key=lambda item: int(item.get("cycle_id") or 0))
    return payloads


def load_run_report(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    run_report_path = root / "run_report.json"
    if run_report_path.exists():
        return _read_json(run_report_path)
    return {}


def _string_list(items: Any) -> list[str]:
    values: list[str] = []
    for item in list(items or []):
        text = str(item or "").strip()
        if text:
            values.append(text)
    return values


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return {key: value for key, value in ordered}


def _safe_cycle_id(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get("cycle_id") or 0)
    except (TypeError, ValueError):
        return 0


def summarize_release_gate_run(
    run_dir: str | Path,
    *,
    run_report: dict[str, Any] | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    cycles = load_cycle_payloads(root)
    report = dict(run_report or load_run_report(root) or {})

    judge_counts: Counter[str] = Counter()
    validation_status_counts: Counter[str] = Counter()
    gate_status_counts: Counter[str] = Counter()
    lineage_status_counts: Counter[str] = Counter()
    combined_reason_counts: Counter[str] = Counter()

    unexpected_reject_count = 0
    governance_blocked_count = 0
    candidate_missing_count = 0
    needs_more_optimization_count = 0
    peer_dominated_count = 0
    review_applied_count = 0
    validation_pass_cycles: list[int] = []
    promote_cycles: list[int] = []
    governance_blocked_cycles: list[int] = []
    cycle_rows: list[dict[str, Any]] = []

    for payload in cycles:
        cycle_id = _safe_cycle_id(payload)
        validation_summary = dict(payload.get("validation_summary") or {})
        judge_report = dict(payload.get("judge_report") or {})
        promotion_record = dict(payload.get("promotion_record") or {})
        lineage_record = dict(payload.get("lineage_record") or {})

        gate_status = str(promotion_record.get("gate_status") or "missing").strip() or "missing"
        lineage_status = (
            str(lineage_record.get("lineage_status") or "missing").strip() or "missing"
        )
        validation_status = str(validation_summary.get("status") or "missing").strip() or "missing"
        new_decision = str(judge_report.get("decision") or "missing").strip() or "missing"
        review_applied = bool(payload.get("review_applied", False))

        validation_reason_codes = _string_list(validation_summary.get("reason_codes"))
        judge_reason_codes = _string_list(judge_report.get("reason_codes"))
        reason_codes = list(dict.fromkeys(validation_reason_codes + judge_reason_codes))

        gate_status_counts[gate_status] += 1
        lineage_status_counts[lineage_status] += 1
        validation_status_counts[validation_status] += 1
        judge_counts[new_decision] += 1
        if review_applied:
            review_applied_count += 1
        for code in reason_codes:
            combined_reason_counts[code] += 1

        if validation_status == "passed":
            validation_pass_cycles.append(cycle_id)
        if new_decision == "promote":
            promote_cycles.append(cycle_id)
        if "governance_blocked" in reason_codes:
            governance_blocked_count += 1
            governance_blocked_cycles.append(cycle_id)
        if "candidate_missing" in reason_codes:
            candidate_missing_count += 1
        if "needs_more_optimization" in reason_codes:
            needs_more_optimization_count += 1
        if "peer_dominated" in reason_codes:
            peer_dominated_count += 1
        if new_decision == "reject" and not any(
            code in {"ab_failed", "governance_blocked"} for code in reason_codes
        ):
            unexpected_reject_count += 1

        cycle_rows.append(
            {
                "cycle_id": cycle_id,
                "cutoff_date": str(payload.get("cutoff_date") or ""),
                "gate_status": gate_status,
                "lineage_status": lineage_status,
                "new_decision": new_decision,
                "validation_status": validation_status,
                "review_applied": review_applied,
                "benchmark_passed": bool(payload.get("benchmark_passed", False)),
                "reason_codes": reason_codes,
                "artifact_path": str(payload.get("_artifact_path") or ""),
            }
        )

    successful_cycles = int(report.get("successful_cycles") or len(cycles))
    attempted_cycles = report.get("attempted_cycles")
    if attempted_cycles is not None:
        attempted_cycles = int(attempted_cycles)

    denominator = max(1, successful_cycles)
    candidate_missing_rate = round(candidate_missing_count / denominator, 4)
    needs_more_optimization_rate = round(needs_more_optimization_count / denominator, 4)

    summary = {
        "label": str(label or root.name),
        "run_dir": str(root),
        "generated_at": datetime.now().isoformat(),
        "window": {
            "attempted_cycles": attempted_cycles,
            "successful_cycles": successful_cycles,
            "successful_cycles_target": report.get("successful_cycles_target"),
            "target_met": report.get("target_met"),
            "status": str(report.get("status") or ""),
        },
        "freeze_gate_evaluation": dict(report.get("freeze_gate_evaluation") or {}),
        "new_governance": {
            "validation_status_counts": _counter_dict(validation_status_counts),
            "judge_counts": _counter_dict(judge_counts),
            "reason_counts": _counter_dict(combined_reason_counts),
            "gate_status_counts": _counter_dict(gate_status_counts),
            "lineage_status_counts": _counter_dict(lineage_status_counts),
            "review_applied_count": review_applied_count,
            "validation_pass_count": len(validation_pass_cycles),
            "promote_count": len(promote_cycles),
            "unexpected_reject_count": unexpected_reject_count,
            "governance_blocked_count": governance_blocked_count,
            "candidate_missing_rate": candidate_missing_rate,
            "needs_more_optimization_rate": needs_more_optimization_rate,
            "peer_dominated_rate": round(peer_dominated_count / denominator, 4),
        },
        "positive_evidence": {
            "validation_pass_count": len(validation_pass_cycles),
            "validation_pass_cycles": validation_pass_cycles,
            "promote_count": len(promote_cycles),
            "promote_cycles": promote_cycles,
        },
        "release_gate_snapshot": {
            "positive_evidence_ready": len(validation_pass_cycles) >= 2 and len(promote_cycles) >= 1,
            "unexpected_reject_free": unexpected_reject_count == 0,
            "governance_block_free": governance_blocked_count == 0,
            "candidate_missing_ready": candidate_missing_rate <= 0.50,
            "needs_more_optimization_ready": needs_more_optimization_rate <= 0.70,
        },
        "governance_blocked_cycles": governance_blocked_cycles,
        "cycles": cycle_rows,
    }
    return summary


def render_release_gate_report_markdown(summary: dict[str, Any]) -> str:
    window = dict(summary.get("window") or {})
    new_governance = dict(summary.get("new_governance") or {})
    positive_evidence = dict(summary.get("positive_evidence") or {})
    gate_snapshot = dict(summary.get("release_gate_snapshot") or {})

    lines = [
        "# Release Gate Report",
        "",
        f"- Label: `{summary.get('label', '')}`",
        f"- Run dir: `{summary.get('run_dir', '')}`",
        f"- Generated at: `{summary.get('generated_at', '')}`",
        f"- Successful cycles: `{window.get('successful_cycles')}`",
        f"- Attempted cycles: `{window.get('attempted_cycles')}`",
        f"- Successful cycle target: `{window.get('successful_cycles_target')}`",
        f"- Target met: `{window.get('target_met')}`",
        "",
        "## New Governance",
        "",
        f"- Validation status counts: `{json.dumps(new_governance.get('validation_status_counts', {}), ensure_ascii=False)}`",
        f"- Judge counts: `{json.dumps(new_governance.get('judge_counts', {}), ensure_ascii=False)}`",
        f"- Reason counts: `{json.dumps(new_governance.get('reason_counts', {}), ensure_ascii=False)}`",
        f"- Validation pass count: `{positive_evidence.get('validation_pass_count')}`",
        f"- Promote count: `{positive_evidence.get('promote_count')}`",
        f"- Unexpected reject count: `{new_governance.get('unexpected_reject_count')}`",
        f"- Governance blocked count: `{new_governance.get('governance_blocked_count')}`",
        f"- Candidate missing rate: `{new_governance.get('candidate_missing_rate')}`",
        f"- Needs more optimization rate: `{new_governance.get('needs_more_optimization_rate')}`",
        f"- Gate status counts: `{json.dumps(new_governance.get('gate_status_counts', {}), ensure_ascii=False)}`",
        f"- Lineage status counts: `{json.dumps(new_governance.get('lineage_status_counts', {}), ensure_ascii=False)}`",
        f"- Review applied count: `{new_governance.get('review_applied_count')}`",
        "",
        "## Gate Snapshot",
        "",
        f"- Positive evidence ready: `{gate_snapshot.get('positive_evidence_ready')}`",
        f"- Unexpected reject free: `{gate_snapshot.get('unexpected_reject_free')}`",
        f"- Governance block free: `{gate_snapshot.get('governance_block_free')}`",
        f"- Candidate missing ready: `{gate_snapshot.get('candidate_missing_ready')}`",
        f"- Needs more optimization ready: `{gate_snapshot.get('needs_more_optimization_ready')}`",
        "",
        "## Cycle Matrix",
        "",
        "| Cycle | Validation | Decision | Gate | Lineage | Review | Reason Codes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    for item in list(summary.get("cycles") or []):
        cycle_id = item.get("cycle_id")
        validation_status = item.get("validation_status")
        new_decision = item.get("new_decision")
        gate_status = item.get("gate_status")
        lineage_status = item.get("lineage_status")
        review_applied = item.get("review_applied")
        reason_codes = ", ".join(_string_list(item.get("reason_codes")))
        safe_reason_codes = reason_codes.replace("|", "/")
        lines.append(
            f"| {cycle_id} | {validation_status} | {new_decision} | {gate_status} | {lineage_status} | {review_applied} | {safe_reason_codes} |"
        )

    return "\n".join(lines) + "\n"


def write_release_gate_report(
    run_dir: str | Path,
    summary: dict[str, Any],
    *,
    json_name: str = "release_gate_divergence_report.json",
    markdown_name: str = "release_gate_divergence_report.md",
) -> tuple[Path, Path]:
    root = Path(run_dir).expanduser().resolve()
    json_path = root / json_name
    markdown_path = root / markdown_name
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_release_gate_report_markdown(summary),
        encoding="utf-8",
    )
    return json_path, markdown_path


@dataclass
class SelfAssessmentSnapshot:
    """单周期自我评估快照（用于冻结门控与追踪）"""

    cycle_id: int
    cutoff_date: str
    regime: str
    plan_source: str
    return_pct: float
    is_profit: bool
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    excess_return: float = 0.0
    benchmark_passed: bool = False


@dataclass
class EventCallbackState:
    callback: Optional[Callable] = None


_event_callback_state = EventCallbackState()


def set_event_callback(callback: Callable) -> None:
    """设置事件回调，用于推送实时事件到前端"""
    _event_callback_state.callback = callback


def emit_event(event_type: str, data: dict) -> None:
    """发射事件到前端"""
    callback = _event_callback_state.callback
    if callback:
        try:
            callback(event_type, data)
        except Exception as exc:
            logger.warning("Event callback failed for %s: %s", event_type, exc)

class FreezeGateService:
    def _resolve_rolling(self, controller: Any, rolling: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return resolve_freeze_rolling_boundary(
            controller,
            rolling=rolling,
            default_resolver=lambda *, window: self.rolling_self_assessment(controller, window=window),
            default_window=controller.freeze_total_cycles,
        )

    def rolling_self_assessment(self, controller: Any, window: Optional[int] = None) -> Dict[str, Any]:
        return rolling_self_assessment(controller.assessment_history, controller.freeze_total_cycles, window=window)

    def evaluate_freeze_gate(self, controller: Any, rolling: Dict[str, Any] | None = None) -> Dict[str, Any]:
        active_rolling = self._resolve_rolling(controller, rolling)
        evaluation = evaluate_freeze_gate(
            session_cycle_history(controller),
            controller.freeze_total_cycles,
            controller.freeze_profit_required,
            controller.freeze_gate_policy,
            active_rolling,
            research_feedback=controller.last_research_feedback,
        )
        return sync_freeze_gate_evaluation_boundary(controller, evaluation)

    def should_freeze(self, controller: Any) -> bool:
        rolling = self._resolve_rolling(controller)
        self.evaluate_freeze_gate(controller, rolling)
        return should_freeze(
            session_cycle_history(controller),
            controller.freeze_total_cycles,
            controller.freeze_profit_required,
            controller.freeze_gate_policy,
            rolling,
            research_feedback=controller.last_research_feedback,
        )

    def freeze_runtime_state(self, controller: Any) -> Dict[str, Any]:
        logger.info(f"\n{'='*50}\n🎉 Runtime 状态固化！\n{'='*50}")

        rolling = self._resolve_rolling(controller)
        report = build_freeze_report(
            session_cycle_history(controller),
            session_current_params(controller),
            controller.freeze_total_cycles,
            controller.freeze_profit_required,
            controller.freeze_gate_policy,
            rolling,
            research_feedback=controller.last_research_feedback,
        )
        sync_freeze_gate_evaluation_boundary(
            controller,
            dict(report.get("freeze_gate_evaluation") or {}),
        )

        path = _write_runtime_freeze_boundary(
            output_dir=controller.output_dir,
            report=report,
            filename=RUNTIME_FREEZE_REPORT_NAME,
        )
        logger.info("固化报告: %s", path)
        return report

    def generate_training_report(self, controller: Any) -> Dict[str, Any]:
        rolling = self._resolve_rolling(controller)
        freeze_gate_evaluation = self.evaluate_freeze_gate(controller, rolling)
        return generate_training_report(
            controller.total_cycle_attempts,
            controller.skipped_cycle_count,
            session_cycle_history(controller),
            session_current_params(controller),
            bool(freeze_gate_evaluation.get("passed")),
            rolling,
            research_feedback=controller.last_research_feedback,
            freeze_gate_evaluation=freeze_gate_evaluation,
        )
def _latest_runtime_config_mutation_event(
    optimization_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    for event in reversed(list(optimization_events or [])):
        if str(event.get("stage") or "") in {
            "runtime_config_mutation",
            "runtime_config_mutation_skipped",
            "candidate_build",
            "candidate_build_skipped",
        }:
            return dict(event)
    return {}


def _candidate_runtime_config_meta_ref(candidate_runtime_config_ref: str) -> str:
    ref = str(candidate_runtime_config_ref or "").strip()
    if not ref:
        return ""
    return str(Path(ref).with_suffix(".json"))


def _resolve_shadow_mode(run_context: dict[str, Any] | None = None) -> bool:
    payload = dict(run_context or {})
    if "shadow_mode" in payload:
        return bool(payload.get("shadow_mode"))
    protocol = dict(payload.get("experiment_protocol") or {})
    nested_protocol = dict(protocol.get("protocol") or {})
    return bool(nested_protocol.get("shadow_mode", protocol.get("shadow_mode", False)))


def _build_promotion_lineage_runtime_state(
    *,
    run_context: dict[str, Any],
    optimization_events: list[dict[str, Any]] | None = None,
) -> _PromotionLineageRuntimeState:
    payload = dict(run_context or {})
    discipline = dict(payload.get("promotion_discipline") or {})
    stage_info = infer_deployment_stage(
        run_context=payload,
        optimization_events=optimization_events,
    )
    return _PromotionLineageRuntimeState(
        payload=payload,
        shadow_mode=_resolve_shadow_mode(payload),
        promotion_decision=dict(payload.get("promotion_decision") or {}),
        discipline=discipline,
        deployment_stage=str(
            payload.get("deployment_stage")
            or discipline.get("deployment_stage")
            or stage_info.get("deployment_stage")
            or "active"
        ),
        candidate_runtime_config_ref=str(payload.get("candidate_runtime_config_ref") or ""),
        mutation_event=_latest_runtime_config_mutation_event(optimization_events),
    )


def _resolve_promotion_gate_status(
    *,
    status: str,
    candidate_runtime_config_ref: str,
    deployment_stage: str,
    applied_to_active: bool,
) -> str:
    if status in {"candidate_expired", "candidate_pruned"}:
        return "rejected"
    if status == "override_expired":
        return "override_rejected"
    if deployment_stage == "override":
        return "override_pending"
    if not candidate_runtime_config_ref:
        return "not_applicable"
    if applied_to_active:
        return "applied_to_active"
    return "awaiting_gate"


def _resolve_lineage_status(
    *,
    promotion_decision: dict[str, Any],
    discipline: dict[str, Any],
    deployment_stage: str,
) -> str:
    if bool(promotion_decision.get("applied_to_active", False)):
        return "candidate_applied"
    discipline_status = str(discipline.get("status") or "")
    if discipline_status == "candidate_expired":
        return "candidate_expired"
    if discipline_status == "candidate_pruned":
        return "candidate_pruned"
    if discipline_status == "override_expired":
        return "override_expired"
    if deployment_stage == "candidate":
        return "candidate_pending"
    if deployment_stage == "override":
        return "override_pending"
    return "active_only"


def _promotion_lineage_common_payload(
    state: _PromotionLineageRuntimeState,
    *,
    cycle_id: int,
) -> dict[str, Any]:
    return {
        "cycle_id": int(cycle_id),
        "basis_stage": str(state.payload.get("basis_stage") or "post_cycle_result"),
        "subject_type": str(state.payload.get("subject_type") or "single_manager"),
        "active_manager_ids": list(
            state.payload.get("portfolio_plan", {}).get("active_manager_ids") or []
        ),
        "candidate_runtime_config_ref": state.candidate_runtime_config_ref,
        "candidate_runtime_config_meta_ref": _candidate_runtime_config_meta_ref(
            state.candidate_runtime_config_ref
        ),
        "deployment_stage": state.deployment_stage,
        "shadow_mode": state.shadow_mode,
        "promotion_discipline": state.discipline,
        "mutation_trigger": str(state.mutation_event.get("trigger") or ""),
        "mutation_stage": str(state.mutation_event.get("stage") or ""),
        "mutation_notes": str(state.mutation_event.get("notes") or ""),
    }


def build_promotion_record(
    *,
    cycle_id: int,
    run_context: dict[str, Any],
    optimization_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    state = _build_promotion_lineage_runtime_state(
        run_context=run_context,
        optimization_events=optimization_events,
    )
    decision = dict(state.promotion_decision or {})
    applied_to_active = bool(decision.get("applied_to_active", False))
    status = str(
        state.discipline.get("status") or decision.get("status") or "not_evaluated"
    )

    return {
        **_promotion_lineage_common_payload(state, cycle_id=cycle_id),
        "dominant_manager_id": str(state.payload.get("dominant_manager_id") or ""),
        "status": status,
        "source": str(decision.get("source") or ""),
        "reason": str(decision.get("reason") or ""),
        "applied_to_active": applied_to_active,
        "attempted": bool(
            state.candidate_runtime_config_ref or state.deployment_stage == "override"
        ),
        "gate_status": _resolve_promotion_gate_status(
            status=status,
            candidate_runtime_config_ref=state.candidate_runtime_config_ref,
            deployment_stage=state.deployment_stage,
            applied_to_active=applied_to_active,
        ),
        "active_runtime_config_ref": str(
            state.payload.get("active_runtime_config_ref") or ""
        ),
        "policy": dict(decision.get("policy") or {}),
        "discipline": dict(state.discipline or {}),
    }


def build_lineage_record(
    controller: Any,
    *,
    cycle_id: int,
    manager_output: Any | None,
    run_context: dict[str, Any],
    optimization_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    state = _build_promotion_lineage_runtime_state(
        run_context=run_context,
        optimization_events=optimization_events,
    )
    projection = _project_manager_compatibility(
        None,
        manager_output=manager_output,
        governance_decision=dict(state.payload.get("governance_decision") or {}),
        portfolio_plan=dict(state.payload.get("portfolio_plan") or {}),
        manager_results=list(state.payload.get("manager_results") or []),
        execution_snapshot=state.payload,
        dominant_manager_id_hint=str(state.payload.get("dominant_manager_id") or ""),
    )
    active_runtime_config_ref = str(
        state.payload.get("active_runtime_config_ref")
        or projection.active_runtime_config_ref
        or ""
    )
    return {
        **_promotion_lineage_common_payload(state, cycle_id=cycle_id),
        "dominant_manager_id": str(
            state.payload.get("dominant_manager_id")
            or projection.dominant_manager_id
            or projection.manager_id
            or ""
        ),
        "manager_config_ref": str(projection.manager_config_ref or ""),
        "active_runtime_config_ref": active_runtime_config_ref,
        "lineage_status": _resolve_lineage_status(
            promotion_decision=state.promotion_decision,
            discipline=state.discipline,
            deployment_stage=state.deployment_stage,
        ),
        "runtime_overrides": dict(state.payload.get("runtime_overrides") or {}),
        "fitness_source_cycles": list(state.payload.get("fitness_source_cycles") or []),
        "review_basis_window": dict(state.payload.get("review_basis_window") or {}),
        "compatibility_fields": dict(state.payload.get("compatibility_fields") or {}),
        "promotion_status": str(
            state.promotion_decision.get("status") or "not_evaluated"
        ),
    }


def _overridden_controller_hook(
    controller: Any,
    hook_name: str,
) -> Any | None:
    hook = getattr(controller, hook_name, None)
    if not callable(hook):
        return None
    class_hook = getattr(type(controller), hook_name, None)
    bound_func = getattr(hook, "__func__", None)
    if bound_func is class_hook:
        return None
    return hook


def resolve_freeze_rolling_boundary(
    controller: Any,
    *,
    rolling: dict[str, Any] | None = None,
    default_resolver: Any,
    default_window: int,
) -> dict[str, Any]:
    hook = _overridden_controller_hook(controller, "_rolling_self_assessment")
    if hook is not None:
        return dict(hook(default_window) or {})
    return dict(rolling or default_resolver(window=default_window) or {})


def sync_freeze_gate_evaluation_boundary(
    controller: Any,
    evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    controller.last_freeze_gate_evaluation = dict(evaluation or {})
    return controller.last_freeze_gate_evaluation
