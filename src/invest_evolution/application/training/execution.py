"""Training execution, manager runtime, boundary, and outcome orchestration."""

from __future__ import annotations

import logging
import math
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, cast

from invest_evolution.application.training.review_contracts import (
    AllocationReviewDigestPayload,
    GovernanceDecisionInputPayload,
    LineageRecordPayload,
    ManagerReviewDigestPayload,
    OptimizationInputEnvelope,
    PromotionRecordPayload,
    RealismMetricsPayload,
    ReviewAppliedEffectsPayload,
    ReviewDecisionInputPayload,
    ReviewStageEnvelope,
    RunContextPayload,
    SimilarResultCompactPayload,
    SimilaritySummaryInputPayload,
    SimulationStageEnvelope,
    StageSnapshotsInputPayload,
    StrategyScoresPayload,
    ValidationInputEnvelope,
    ValidationReportInputPayload,
    build_cycle_contract_stage_snapshots,
    build_cycle_run_context,
    build_execution_snapshot,
    build_outcome_stage_snapshot,
    project_review_applied_effects_payload,
    build_validation_stage_snapshot,
)
from invest_evolution.config import (
    EFFECTIVE_RUNTIME_MODE,
    config,
    normalize_date,
)
from invest_evolution.investment.contracts import (
    ManagerResult,
    ManagerRunContext,
    PortfolioPlan,
    PortfolioPlanPosition,
)
from invest_evolution.investment.evolution import EvolutionService, derive_scoring_adjustments
from invest_evolution.investment.foundation.simulator import SimulatedTrader
from invest_evolution.investment.governance.planning import PortfolioAssembler
from invest_evolution.investment.managers import build_default_manager_registry
from invest_evolution.investment.managers.registry import (
    canonical_manager_config_ref as canonical_registry_manager_config_ref,
    normalize_manager_config_ref,
)
from invest_evolution.investment.research import AttributionService
from invest_evolution.investment.runtimes import SimulationService, list_manager_runtime_ids
from invest_evolution.investment.runtimes.catalog import COMMON_EXECUTION_DEFAULTS, COMMON_PARAM_DEFAULTS
from invest_evolution.investment.shared import CognitiveAssistService
from invest_evolution.investment.shared.contracts import PositionPlan, TradingPlan
from invest_evolution.investment.shared.policy import normalize_config_ref

logger = logging.getLogger(__name__)

EXECUTABLE_MAX_SINGLE_POSITION = 0.20


_TRAINING_MODULE_IMPORTS = {
    "controller": "invest_evolution.application.training.controller",
    "policy": "invest_evolution.application.training.policy",
    "research": "invest_evolution.application.training.research",
    "review": "invest_evolution.application.training.review",
    "observability": "invest_evolution.application.training.observability",
    "persistence": "invest_evolution.application.training.persistence",
}

RUNTIME_BUDGET_KEYS = frozenset({"position_size", "cash_reserve", "max_positions"})
SAFETY_RUNTIME_KEYS = frozenset(
    {
        "emergency_stop_loss",
        "max_total_exposure_override",
        "max_position_size_override",
        "force_reduce_position",
        "kill_switch",
        "liquidity_guard_threshold",
    }
)
REGIME_NAMES = frozenset({"bull", "bear", "oscillation"})
ENTRY_THRESHOLD_KEYS = (
    "signal_threshold",
    "min_reversion_score",
    "min_value_quality_score",
    "min_defensive_score",
)
DEFAULT_STRATEGY_FAMILY_REGIME_BUDGETS: dict[str, dict[str, dict[str, Any]]] = {
    "momentum": {
        "bull": {"position_size": 0.22, "cash_reserve": 0.15, "max_positions": 4},
        "bear": {"position_size": 0.10, "cash_reserve": 0.45, "max_positions": 2},
        "oscillation": {"position_size": 0.16, "cash_reserve": 0.28, "max_positions": 3},
    },
    "mean_reversion": {
        "bull": {"position_size": 0.14, "cash_reserve": 0.40, "max_positions": 3},
        "bear": {"position_size": 0.15, "cash_reserve": 0.38, "max_positions": 3},
        "oscillation": {"position_size": 0.18, "cash_reserve": 0.30, "max_positions": 4},
    },
    "defensive_low_vol": {
        "bull": {"position_size": 0.16, "cash_reserve": 0.30, "max_positions": 4},
        "bear": {"position_size": 0.15, "cash_reserve": 0.40, "max_positions": 3},
        "oscillation": {"position_size": 0.17, "cash_reserve": 0.32, "max_positions": 4},
    },
    "value_quality": {
        "bull": {"position_size": 0.18, "cash_reserve": 0.22, "max_positions": 4},
        "bear": {"position_size": 0.12, "cash_reserve": 0.40, "max_positions": 2},
        "oscillation": {"position_size": 0.16, "cash_reserve": 0.28, "max_positions": 3},
    },
}


@lru_cache(maxsize=None)
def _training_module(name: str):
    return import_module(_TRAINING_MODULE_IMPORTS[name])


def _call_training_module(
    module_name: str,
    attr: str,
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    return getattr(_training_module(module_name), attr)(*args, **kwargs)


def _resolve_training_module_attr(module_name: str, attr: str) -> Callable[..., Any] | None:
    try:
        module = _training_module(module_name)
    except Exception:
        return None
    candidate = getattr(module, attr, None)
    return candidate if callable(candidate) else None


def _call_training_module_if_available(
    module_name: str,
    attr: str,
    /,
    *args: Any,
    **kwargs: Any,
) -> Any | None:
    fn = _resolve_training_module_attr(module_name, attr)
    if fn is None:
        return None
    return fn(*args, **kwargs)


def _training_module_proxy(module_name: str, attr: str) -> Callable[..., Any]:
    def _proxy(*args: Any, **kwargs: Any) -> Any:
        return _call_training_module(module_name, attr, *args, **kwargs)

    _proxy.__name__ = attr
    _proxy.__qualname__ = attr
    return _proxy


def _new_optimization_boundary_context(
    *,
    cycle_id: int | None,
    manager_id: str,
    active_runtime_config_ref: str,
    fitness_source_cycles: list[int],
) -> Any:
    context_cls = getattr(_training_module("observability"), "OptimizationBoundaryContext")
    return context_cls(
        cycle_id=cycle_id,
        manager_id=manager_id,
        active_runtime_config_ref=active_runtime_config_ref,
        fitness_source_cycles=fitness_source_cycles,
    )

session_default_manager_id = cast(
    Callable[..., str],
    _training_module_proxy("controller", "session_default_manager_id"),
)
session_default_manager_config_ref = cast(
    Callable[..., str],
    _training_module_proxy("controller", "session_default_manager_config_ref"),
)
session_cycle_history = cast(
    Callable[..., list[Any]],
    _training_module_proxy("controller", "session_cycle_history"),
)
set_session_manager_budget_weights = cast(
    Callable[..., dict[str, float]],
    _training_module_proxy("controller", "set_session_manager_budget_weights"),
)
update_session_current_params = cast(
    Callable[..., dict[str, Any]],
    _training_module_proxy("controller", "update_session_current_params"),
)
set_session_current_params = cast(
    Callable[..., dict[str, Any]],
    _training_module_proxy("controller", "set_session_current_params"),
)
session_current_params = cast(
    Callable[..., dict[str, Any]],
    _training_module_proxy("controller", "session_current_params"),
)
session_consecutive_losses = cast(
    Callable[..., int],
    _training_module_proxy("controller", "session_consecutive_losses"),
)
set_session_consecutive_losses = cast(
    Callable[..., int],
    _training_module_proxy("controller", "set_session_consecutive_losses"),
)
increment_session_consecutive_losses = cast(
    Callable[..., int],
    _training_module_proxy("controller", "increment_session_consecutive_losses"),
)
session_last_feedback_optimization = cast(
    Callable[..., dict[str, Any]],
    _training_module_proxy("controller", "session_last_feedback_optimization"),
)
set_session_last_feedback_optimization = cast(
    Callable[..., dict[str, Any]],
    _training_module_proxy("controller", "set_session_last_feedback_optimization"),
)
set_session_last_feedback_optimization_cycle_id = cast(
    Callable[..., int],
    _training_module_proxy("controller", "set_session_last_feedback_optimization_cycle_id"),
)
session_manager_budget_weights = cast(
    Callable[..., dict[str, float]],
    _training_module_proxy("controller", "session_manager_budget_weights"),
)


normalize_governance_decision = cast(
    Callable[..., dict[str, Any]],
    _training_module_proxy("policy", "normalize_governance_decision"),
)
resolve_training_scope = cast(
    Callable[..., Any],
    _training_module_proxy("policy", "resolve_training_scope"),
)
governance_from_controller = cast(
    Callable[..., dict[str, Any]],
    _training_module_proxy("policy", "governance_from_controller"),
)
dominant_manager_id = cast(
    Callable[..., str],
    _training_module_proxy("policy", "dominant_manager_id"),
)
enforce_allowed_manager_scope_boundary = cast(
    Callable[..., Any],
    _training_module_proxy("policy", "enforce_allowed_manager_scope_boundary"),
)


build_history_peer_entries = cast(
    Callable[..., Any],
    _training_module_proxy("research", "build_history_peer_entries"),
)
build_judge_report = cast(
    Callable[..., Any],
    _training_module_proxy("research", "build_judge_report"),
)
compare_candidate_to_peers = cast(
    Callable[..., Any],
    _training_module_proxy("research", "compare_candidate_to_peers"),
)
run_validation_orchestrator = cast(
    Callable[..., Any],
    _training_module_proxy("research", "run_validation_orchestrator"),
)
build_optimization_lineage = cast(
    Callable[..., dict[str, Any]],
    _training_module_proxy("observability", "build_optimization_lineage"),
)
build_feedback_optimization_event = cast(
    Callable[..., Any],
    _training_module_proxy("observability", "build_feedback_optimization_event"),
)
build_llm_optimization_event = cast(
    Callable[..., Any],
    _training_module_proxy("observability", "build_llm_optimization_event"),
)
build_evolution_optimization_event = cast(
    Callable[..., Any],
    _training_module_proxy("observability", "build_evolution_optimization_event"),
)
build_optimization_error_event = cast(
    Callable[..., Any],
    _training_module_proxy("observability", "build_optimization_error_event"),
)
build_review_boundary_event = cast(
    Callable[..., Any],
    _training_module_proxy("observability", "build_review_boundary_event"),
)
finalize_review_boundary_effects = cast(
    Callable[..., None],
    _training_module_proxy("observability", "finalize_review_boundary_effects"),
)
emit_optimization_start_boundary = cast(
    Callable[..., None],
    _training_module_proxy("observability", "emit_optimization_start_boundary"),
)
record_feedback_optimization_boundary_effects = cast(
    Callable[..., None],
    _training_module_proxy("observability", "record_feedback_optimization_boundary_effects"),
)
record_llm_optimization_boundary_effects = cast(
    Callable[..., None],
    _training_module_proxy("observability", "record_llm_optimization_boundary_effects"),
)
record_evolution_optimization_boundary_effects = cast(
    Callable[..., None],
    _training_module_proxy("observability", "record_evolution_optimization_boundary_effects"),
)
record_runtime_mutation_boundary_effects = cast(
    Callable[..., None],
    _training_module_proxy("observability", "record_runtime_mutation_boundary_effects"),
)
build_runtime_mutation_boundary = cast(
    Callable[..., Any],
    _training_module_proxy("observability", "build_runtime_mutation_boundary"),
)
emit_optimization_error_boundary = cast(
    Callable[..., None],
    _training_module_proxy("observability", "emit_optimization_error_boundary"),
)
emit_optimization_completed_boundary = cast(
    Callable[..., None],
    _training_module_proxy("observability", "emit_optimization_completed_boundary"),
)


def normalize_manager_id(value: Any, *, default: str = "momentum") -> str:
    return _call_training_module("policy", "normalize_manager_id", value, default=default)


def resolve_manager_config_ref(
    manager_id: Any,
    manager_config_ref: Any = None,
) -> str:
    return _call_training_module(
        "policy",
        "resolve_manager_config_ref",
        manager_id,
        manager_config_ref,
    )


def build_manager_runtime(
    *,
    manager_id: Any,
    manager_config_ref: Any = None,
    runtime_overrides: dict[str, Any] | None = None,
) -> Any:
    return _call_training_module(
        "policy",
        "build_manager_runtime",
        manager_id=manager_id,
        manager_config_ref=manager_config_ref,
        runtime_overrides=runtime_overrides,
    )


def controller_default_manager_id(controller: Any, *, default: str = "momentum") -> str:
    return _call_training_module(
        "policy",
        "controller_default_manager_id",
        controller,
        default=default,
    )


def controller_default_manager_config_ref(controller: Any) -> str:
    return _call_training_module(
        "policy",
        "controller_default_manager_config_ref",
        controller,
    )


def runtime_manager_id(manager_runtime: Any, *, fallback: Any = "") -> str:
    return str(
        getattr(manager_runtime, "manager_id", "")
        or getattr(manager_runtime, "runtime_id", fallback)
        or fallback
        or ""
    ).strip()


def runtime_manager_config_ref(manager_runtime: Any, *, fallback: Any = "") -> str:
    direct_ref = str(getattr(manager_runtime, "manager_config_ref", "") or "").strip()
    if direct_ref:
        return direct_ref
    config = getattr(manager_runtime, "config", None)
    runtime_config_ref = str(
        getattr(config, "path", "")
        or getattr(config, "name", "")
        or ""
    ).strip()
    if runtime_config_ref:
        return runtime_config_ref
    return str(fallback or "").strip()


def manager_output_manager_id(manager_output: Any, *, fallback: Any = "") -> str:
    return str(
        getattr(manager_output, "manager_id", "")
        or getattr(getattr(manager_output, "signal_packet", None), "manager_id", "")
        or fallback
        or ""
    ).strip()


def manager_output_manager_config_ref(manager_output: Any, *, fallback: Any = "") -> str:
    return str(
        getattr(manager_output, "manager_config_ref", "")
        or getattr(getattr(manager_output, "signal_packet", None), "manager_config_ref", "")
        or fallback
        or ""
    ).strip()


def normalize_path_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).expanduser().resolve())
    except Exception:
        return text


@lru_cache(maxsize=1)
def _known_manager_ids() -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(manager_id).strip()
                for manager_id in list_manager_runtime_ids()
                if str(manager_id).strip()
            },
            key=len,
            reverse=True,
        )
    )


def _infer_manager_id_from_config_ref(manager_config_ref: Any) -> str:
    text = str(manager_config_ref or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    path = Path(text)
    probes = (
        lowered,
        path.name.lower(),
        path.stem.lower(),
    )
    for manager_id in _known_manager_ids():
        normalized_manager_id = manager_id.lower()
        if any(normalized_manager_id in probe for probe in probes):
            return manager_id
    return ""


def _manager_config_ref_matches_manager(
    manager_id: Any,
    manager_config_ref: Any,
) -> bool:
    normalized_manager_id = normalize_manager_id(manager_id, default="").strip()
    resolved_config_ref = str(manager_config_ref or "").strip()
    if not normalized_manager_id or not resolved_config_ref:
        return False
    inferred_manager_id = _infer_manager_id_from_config_ref(resolved_config_ref)
    if inferred_manager_id:
        return inferred_manager_id == normalized_manager_id
    canonical_default_ref = str(
        normalize_manager_config_ref(resolve_manager_config_ref(normalized_manager_id))
        or resolve_manager_config_ref(normalized_manager_id)
        or ""
    ).strip()
    normalized_config_ref = str(
        normalize_manager_config_ref(resolved_config_ref) or resolved_config_ref
    ).strip()
    return bool(canonical_default_ref) and normalized_config_ref == canonical_default_ref


def _canonical_manager_config_ref(
    manager_id: Any,
    *,
    manager_config_ref: Any = None,
    fallback: Any = None,
) -> str:
    normalized_manager_id = normalize_manager_id(manager_id, default="").strip()
    direct_ref = str(manager_config_ref or "").strip()
    fallback_ref = str(fallback or "").strip()
    if normalized_manager_id:
        inferred_direct_manager_id = _infer_manager_id_from_config_ref(direct_ref)
        if direct_ref and (
            not inferred_direct_manager_id
            or inferred_direct_manager_id == normalized_manager_id
            or _manager_config_ref_matches_manager(
                normalized_manager_id,
                direct_ref,
            )
        ):
            return canonical_registry_manager_config_ref(
                normalized_manager_id,
                direct_ref,
            )
        inferred_fallback_manager_id = _infer_manager_id_from_config_ref(fallback_ref)
        if fallback_ref and (
            not inferred_fallback_manager_id
            or inferred_fallback_manager_id == normalized_manager_id
            or _manager_config_ref_matches_manager(
                normalized_manager_id,
                fallback_ref,
            )
        ):
            return canonical_registry_manager_config_ref(
                normalized_manager_id,
                fallback_ref,
            )
        if direct_ref:
            return canonical_registry_manager_config_ref(normalized_manager_id)
        if fallback_ref:
            return canonical_registry_manager_config_ref(normalized_manager_id)
        return canonical_registry_manager_config_ref(normalized_manager_id)
    return canonical_registry_manager_config_ref("", direct_ref or fallback_ref)


@dataclass(frozen=True)
class ManagerCompatibilityProjection:
    manager_id: str
    manager_config_ref: str
    active_runtime_config_ref: str
    execution_defaults: dict[str, str]
    dominant_manager_id: str
    subject_type: str


@dataclass(frozen=True)
class _ManagerProjectionInputs:
    governance_decision: dict[str, Any]
    portfolio_plan: dict[str, Any]
    manager_results: list[Any]
    execution_snapshot: dict[str, Any]
    dominant_manager_id_hint: str


@dataclass(frozen=True)
class _PayloadProjectionState:
    record: dict[str, Any]
    metadata: dict[str, Any]
    execution_snapshot: dict[str, Any]


def _build_manager_projection_inputs(
    *,
    governance_decision: GovernanceDecisionInputPayload | dict[str, Any] | None = None,
    portfolio_plan: dict[str, Any] | None = None,
    manager_results: list[Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    dominant_manager_id_hint: str = "",
) -> _ManagerProjectionInputs:
    snapshot = dict(execution_snapshot or {})
    return _ManagerProjectionInputs(
        governance_decision=dict(
            governance_decision or snapshot.get("governance_decision") or {}
        ),
        portfolio_plan=dict(portfolio_plan or snapshot.get("portfolio_plan") or {}),
        manager_results=list(manager_results or snapshot.get("manager_results") or []),
        execution_snapshot=snapshot,
        dominant_manager_id_hint=str(
            dominant_manager_id_hint or snapshot.get("dominant_manager_id") or ""
        ).strip(),
    )


def _resolve_projection_fallback_identity(
    controller: Any | None,
    *,
    manager_output: Any | None,
) -> tuple[str, str]:
    default_manager_id = (
        controller_default_manager_id(controller, default="")
        if controller is not None
        else ""
    )
    default_manager_config_ref = (
        controller_default_manager_config_ref(controller)
        if controller is not None
        else ""
    )
    if manager_output is None:
        return default_manager_id, default_manager_config_ref
    return (
        manager_output_manager_id(
            manager_output,
            fallback=default_manager_id,
        ),
        manager_output_manager_config_ref(
            manager_output,
            fallback=default_manager_config_ref,
        ),
    )


def _resolve_projection_execution_defaults(
    *,
    scope: Any,
    execution_snapshot: dict[str, Any],
    manager_id: str,
    manager_config_ref: str,
) -> dict[str, str]:
    execution_defaults = {
        str(key): str(value or "")
        for key, value in dict(
            scope.execution_defaults
            or execution_snapshot.get("execution_defaults")
            or {}
        ).items()
    }
    if manager_id and execution_defaults.get("default_manager_id") != manager_id:
        execution_defaults["default_manager_id"] = manager_id
    canonical_default_ref = _canonical_manager_config_ref(
        manager_id,
        manager_config_ref=execution_defaults.get("default_manager_config_ref"),
        fallback=manager_config_ref,
    )
    if canonical_default_ref:
        execution_defaults["default_manager_config_ref"] = canonical_default_ref
    return execution_defaults


def _resolve_projection_subject_type(
    *,
    snapshot: dict[str, Any],
    scope: Any,
) -> str:
    snapshot_subject_type = str(snapshot.get("subject_type") or "").strip()
    scope_subject_type = str(getattr(scope, "subject_type", "") or "").strip()
    portfolio_plan = dict(snapshot.get("portfolio_plan") or {})
    governance_decision = dict(snapshot.get("governance_decision") or {})
    if scope_subject_type == "manager_portfolio":
        return "manager_portfolio"
    if snapshot_subject_type == "manager_portfolio":
        return snapshot_subject_type
    if str(snapshot.get("selection_mode") or "").strip() == "manager_portfolio":
        return "manager_portfolio"
    if list(snapshot.get("manager_results") or []):
        return "manager_portfolio"
    if list(portfolio_plan.get("active_manager_ids") or []):
        return "manager_portfolio"
    if str(portfolio_plan.get("dominant_manager_id") or "").strip():
        return "manager_portfolio"
    try:
        if int(portfolio_plan.get("manager_count") or 0) > 0:
            return "manager_portfolio"
    except (TypeError, ValueError):
        pass
    if len(list(governance_decision.get("active_manager_ids") or [])) > 1:
        return "manager_portfolio"
    return snapshot_subject_type or scope_subject_type or "single_manager"


def project_manager_compatibility(
    controller: Any | None,
    *,
    manager_output: Any | None = None,
    governance_decision: GovernanceDecisionInputPayload | dict[str, Any] | None = None,
    portfolio_plan: dict[str, Any] | None = None,
    manager_results: list[Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    dominant_manager_id_hint: str = "",
) -> ManagerCompatibilityProjection:
    inputs = _build_manager_projection_inputs(
        governance_decision=governance_decision,
        portfolio_plan=portfolio_plan,
        manager_results=manager_results,
        execution_snapshot=execution_snapshot,
        dominant_manager_id_hint=dominant_manager_id_hint,
    )
    snapshot = inputs.execution_snapshot
    scope = resolve_training_scope(
        controller=controller,
        governance_decision=normalize_governance_decision(
            inputs.governance_decision
        ),
        portfolio_plan=inputs.portfolio_plan,
        manager_results=inputs.manager_results,
        execution_snapshot=snapshot,
        dominant_manager_id_hint=inputs.dominant_manager_id_hint,
    )
    fallback_manager_id, fallback_manager_config_ref = (
        _resolve_projection_fallback_identity(
            controller,
            manager_output=manager_output,
        )
    )
    manager_id = str(scope.dominant_manager_id or fallback_manager_id or "").strip()
    active_runtime_config_ref = _canonical_manager_config_ref(
        manager_id,
        manager_config_ref=(
            scope.active_runtime_config_ref
            or snapshot.get("active_runtime_config_ref")
        ),
        fallback=fallback_manager_config_ref,
    )
    manager_config_ref = _canonical_manager_config_ref(
        manager_id,
        manager_config_ref=(
            scope.manager_config_ref
            or snapshot.get("manager_config_ref")
            or active_runtime_config_ref
        ),
        fallback=fallback_manager_config_ref or active_runtime_config_ref,
    )
    active_runtime_config_ref = str(
        active_runtime_config_ref
        or manager_config_ref
        or fallback_manager_config_ref
        or ""
    ).strip()
    manager_config_ref = str(
        manager_config_ref
        or active_runtime_config_ref
        or fallback_manager_config_ref
        or ""
    ).strip()
    return ManagerCompatibilityProjection(
        manager_id=manager_id,
        manager_config_ref=manager_config_ref,
        active_runtime_config_ref=active_runtime_config_ref,
        execution_defaults=_resolve_projection_execution_defaults(
            scope=scope,
            execution_snapshot=snapshot,
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
        ),
        dominant_manager_id=str(scope.dominant_manager_id or manager_id or "").strip(),
        subject_type=_resolve_projection_subject_type(
            snapshot=snapshot,
            scope=scope,
        ),
    )


def build_manager_compatibility_fields(
    projection: ManagerCompatibilityProjection,
    *,
    source: str,
    derived: bool,
    field_role: str | None = None,
) -> dict[str, Any]:
    return {
        "derived": bool(derived),
        "source": str(source or ""),
        "field_role": str(
            field_role
            or ("derived_compatibility" if derived else "primary")
        ),
        "manager_id": str(projection.manager_id or ""),
        "manager_config_ref": str(projection.manager_config_ref or ""),
    }


def _cycle_payload_execution_snapshot(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    record = dict(payload or {})
    metadata = dict(record.get("metadata") or {})
    execution_snapshot = dict(record.get("execution_snapshot") or {})
    if not execution_snapshot.get("execution_defaults") and record.get("execution_defaults"):
        execution_snapshot["execution_defaults"] = dict(record.get("execution_defaults") or {})
    if not execution_snapshot.get("execution_defaults") and metadata.get("execution_defaults"):
        execution_snapshot["execution_defaults"] = dict(metadata.get("execution_defaults") or {})
    if not execution_snapshot.get("dominant_manager_id") and record.get("dominant_manager_id"):
        execution_snapshot["dominant_manager_id"] = record.get("dominant_manager_id")
    if not execution_snapshot.get("dominant_manager_id") and metadata.get("dominant_manager_id"):
        execution_snapshot["dominant_manager_id"] = metadata.get("dominant_manager_id")
    if not execution_snapshot.get("manager_config_ref") and record.get("manager_config_ref"):
        execution_snapshot["manager_config_ref"] = record.get("manager_config_ref")
    if not execution_snapshot.get("manager_config_ref") and metadata.get("manager_config_ref"):
        execution_snapshot["manager_config_ref"] = metadata.get("manager_config_ref")
    if not execution_snapshot.get("active_runtime_config_ref") and record.get("active_runtime_config_ref"):
        execution_snapshot["active_runtime_config_ref"] = record.get("active_runtime_config_ref")
    if not execution_snapshot.get("active_runtime_config_ref") and metadata.get("active_runtime_config_ref"):
        execution_snapshot["active_runtime_config_ref"] = metadata.get("active_runtime_config_ref")
    return execution_snapshot


def _payload_projection_state(
    payload: dict[str, Any] | None = None,
) -> _PayloadProjectionState:
    record = dict(payload or {})
    return _PayloadProjectionState(
        record=record,
        metadata=dict(record.get("metadata") or {}),
        execution_snapshot=_cycle_payload_execution_snapshot(record),
    )


def project_cycle_payload_manager_compatibility(
    controller: Any | None,
    *,
    cycle_payload: dict[str, Any] | None = None,
    manager_output: Any | None = None,
) -> ManagerCompatibilityProjection:
    payload_state = _payload_projection_state(cycle_payload)
    return project_manager_compatibility(
        controller,
        manager_output=manager_output,
        governance_decision=dict(
            payload_state.record.get("governance_decision")
            or payload_state.execution_snapshot.get("governance_decision")
            or {}
        ),
        portfolio_plan=dict(
            payload_state.record.get("portfolio_plan")
            or payload_state.execution_snapshot.get("portfolio_plan")
            or {}
        ),
        manager_results=list(
            payload_state.record.get("manager_results")
            or payload_state.execution_snapshot.get("manager_results")
            or []
        ),
        execution_snapshot=payload_state.execution_snapshot,
        dominant_manager_id_hint=str(
            payload_state.record.get("dominant_manager_id")
            or payload_state.execution_snapshot.get("dominant_manager_id")
            or ""
        ),
    )


def resolve_payload_manager_identity(
    payload: dict[str, Any] | None = None,
    *,
    controller: Any | None = None,
) -> tuple[str, str]:
    payload_state = _payload_projection_state(payload)
    projection = project_cycle_payload_manager_compatibility(
        controller,
        cycle_payload=payload,
    )
    manager_id = str(
        projection.manager_id
        or projection.dominant_manager_id
        or payload_state.metadata.get("manager_id")
        or payload_state.metadata.get("dominant_manager_id")
        or payload_state.record.get("manager_id")
        or payload_state.record.get("dominant_manager_id")
        or (controller_default_manager_id(controller, default="") if controller is not None else "")
        or ""
    )
    manager_config_ref = _canonical_manager_config_ref(
        manager_id,
        manager_config_ref=(
            projection.manager_config_ref
            or payload_state.metadata.get("manager_config_ref")
            or payload_state.record.get("manager_config_ref")
            or payload_state.metadata.get("active_runtime_config_ref")
            or payload_state.record.get("active_runtime_config_ref")
        ),
        fallback=(
            projection.active_runtime_config_ref
            or (controller_default_manager_config_ref(controller) if controller is not None else "")
        ),
    )
    return manager_id, manager_config_ref


def apply_runtime_adjustments_boundary(
    controller: Any,
    adjustments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(adjustments or {})
    if not payload:
        return {}
    if bool(getattr(controller, "current_cycle_runtime_locked", False)):
        deferred = _runtime_copy_dict(
            getattr(controller, "current_cycle_deferred_runtime_adjustments", {}) or {}
        )
        deferred.update(payload)
        setattr(controller, "current_cycle_deferred_runtime_adjustments", deferred)
        return payload
    update_session_current_params(controller, payload)
    manager_runtime = getattr(controller, "manager_runtime", None)
    if manager_runtime is not None and hasattr(manager_runtime, "update_runtime_overrides"):
        manager_runtime.update_runtime_overrides(payload)
    return payload


def apply_review_decision_boundary_effects(
    controller: Any,
    *,
    cycle_id: int,
    review_decision: ReviewDecisionInputPayload | dict[str, Any],
    review_event: Any,
    manager_subject: bool,
) -> bool:
    review_applied = False
    applied_effects = cast(
        ReviewAppliedEffectsPayload,
        getattr(review_event, "review_applied_effects_payload", {}) or {},
    )
    param_adjustments = _dict_payload(review_decision.get("param_adjustments"))
    manager_budget_adjustments = _dict_payload(
        review_decision.get("manager_budget_adjustments")
    )
    agent_weight_adjustments = _dict_payload(
        review_decision.get("agent_weight_adjustments")
    )
    param_adjustments = apply_runtime_adjustments_boundary(
        controller,
        param_adjustments,
    )
    if param_adjustments:
        review_applied = True
        applied_effects["param_adjustments"] = dict(param_adjustments)
        controller._emit_agent_status(
            "ManagerReview" if manager_subject else "Review",
            "completed",
            f"参数已调整: {list(param_adjustments.keys())}",
            cycle_id=cycle_id,
            stage="dual_review" if manager_subject else "review",
            progress_pct=96,
            step=4,
            total_steps=6,
            details=review_decision,
            adjustments=param_adjustments,
        )

    if manager_budget_adjustments and bool(getattr(controller, "manager_allocator_enabled", False)):
        manager_budget_weights = {
            str(key): float(value)
            for key, value in manager_budget_adjustments.items()
        }
        set_session_manager_budget_weights(controller, manager_budget_weights)
        review_applied = True
        applied_effects["manager_budget_adjustments"] = dict(manager_budget_weights)

    if agent_weight_adjustments and not manager_subject:
        existing_weights = dict(getattr(controller, "selection_agent_weights", {}) or {})
        existing_weights.update(
            {
                str(key): float(value)
                for key, value in agent_weight_adjustments.items()
            }
        )
        controller.selection_agent_weights = existing_weights
        review_applied = True
        applied_effects["agent_weight_adjustments"] = {
            str(key): float(value)
            for key, value in agent_weight_adjustments.items()
        }

    review_event.review_applied_effects_payload = project_review_applied_effects_payload(
        cast(dict[str, Any], applied_effects)
    )

    return review_applied
def _latest_open_candidate_runtime_config_ref(controller: Any) -> str:
    cycle_history = session_cycle_history(controller)
    for item in reversed(list(cycle_history or [])):
        lineage_record = dict(
            item.get("lineage_record", {}) if isinstance(item, dict) else getattr(item, "lineage_record", {})
            or {}
        )
        if str(lineage_record.get("lineage_status") or "") in {
            "candidate_pruned",
            "candidate_expired",
            "candidate_applied",
            "override_expired",
        }:
            continue
        if str(lineage_record.get("deployment_stage") or "") != "candidate" and str(
            lineage_record.get("lineage_status") or ""
        ) != "candidate_pending":
            continue
        raw_ref = str(lineage_record.get("candidate_runtime_config_ref") or "").strip()
        ref = normalize_config_ref(raw_ref) or raw_ref
        if ref:
            return ref
    return ""


def record_review_boundary_artifacts(
    controller: Any,
    *,
    cycle_id: int,
    manager_review_report: dict[str, Any],
    allocation_review_report: dict[str, Any],
) -> None:
    controller.artifact_recorder.save_manager_review_artifact(manager_review_report, cycle_id)
    controller.artifact_recorder.save_allocation_review_artifact(allocation_review_report, cycle_id)

def _dict_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, dict):
            return dict(payload)
    if value is None:
        return {}
    try:
        return dict(value)
    except Exception:
        return {}


def _runtime_copy_dict(value: Any) -> dict[str, Any]:
    return deepcopy(dict(value or {}))


def _runtime_safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _runtime_safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return number


def _runtime_policy_lookup(policy: dict[str, Any] | None, path: str, default: Any) -> Any:
    current: Any = dict(policy or {})
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _runtime_normalize_regime(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in REGIME_NAMES else "unknown"


def _runtime_sync_model(controller: Any, params: dict[str, Any]) -> None:
    manager_runtime = getattr(controller, "manager_runtime", None)
    if manager_runtime is not None and hasattr(manager_runtime, "update_runtime_overrides"):
        try:
            manager_runtime.update_runtime_overrides(dict(params or {}))
        except Exception:
            logger.debug("runtime sync skipped for manager_runtime", exc_info=True)
    model = getattr(controller, "investment_model", None)
    if model is None:
        return
    if hasattr(model, "runtime_overrides"):
        model.runtime_overrides = _runtime_copy_dict(params)
        return
    update_runtime_overrides = getattr(model, "update_runtime_overrides", None)
    if callable(update_runtime_overrides):
        try:
            update_runtime_overrides(dict(params or {}))
        except Exception:
            logger.debug("runtime sync skipped for investment_model", exc_info=True)


def _runtime_resolve_controller_regime_controls(controller: Any) -> dict[str, dict[str, Any]]:
    configured = _runtime_copy_dict(getattr(controller, "regime_controls", {}) or {})
    if not configured:
        manager_runtime = getattr(controller, "manager_runtime", None)
        config_section = getattr(manager_runtime, "config_section", None)
        if callable(config_section):
            try:
                configured = _runtime_copy_dict(config_section("regime_controls", {}) or {})
            except Exception:
                configured = {}
    normalized: dict[str, dict[str, Any]] = {}
    for regime, params in configured.items():
        regime_name = _runtime_normalize_regime(regime)
        if regime_name == "unknown":
            continue
        normalized[regime_name] = _runtime_copy_dict(params or {})
    return normalized


def _runtime_resolve_strategy_family(controller: Any) -> str:
    explicit = str(getattr(controller, "strategy_family", "") or "").strip().lower()
    if explicit:
        return explicit
    governance_decision = governance_from_controller(controller)
    dominant_manager = str(governance_decision.get("dominant_manager_id") or "").strip().lower()
    if dominant_manager:
        return dominant_manager
    default_manager_id = controller_default_manager_id(controller, default="").strip().lower()
    return default_manager_id or "unknown"


def _runtime_resolve_strategy_family_regime_budgets(
    controller: Any,
) -> dict[str, dict[str, Any]]:
    family = _runtime_resolve_strategy_family(controller)
    configured = _runtime_copy_dict(getattr(controller, "strategy_family_risk_budgets", {}) or {})
    if family and family in configured and isinstance(configured.get(family), dict):
        configured = _runtime_copy_dict(configured.get(family) or {})
    family_budget = _runtime_copy_dict(
        DEFAULT_STRATEGY_FAMILY_REGIME_BUDGETS.get(family, {}) or {}
    )
    for regime, params in configured.items():
        regime_name = _runtime_normalize_regime(regime)
        if regime_name == "unknown":
            continue
        baseline = dict(family_budget.get(regime_name) or {})
        baseline.update(
            {
                str(key): value
                for key, value in dict(params or {}).items()
                if str(key) in RUNTIME_BUDGET_KEYS
            }
        )
        family_budget[regime_name] = baseline
    return family_budget


def _runtime_clamp_between(
    value: Any,
    minimum: float,
    maximum: float,
    *,
    digits: int = 4,
) -> float | None:
    number = _runtime_safe_float(value)
    if number is None:
        return None
    return round(max(minimum, min(maximum, number)), digits)


def _runtime_sanitize_regime_overlay(
    controller: Any,
    *,
    base_params: dict[str, Any],
    overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = _runtime_copy_dict(overlay or {})
    clean: dict[str, Any] = {}
    risk_clamps = dict(
        _runtime_policy_lookup(getattr(controller, "risk_policy", {}), "clamps", {}) or {}
    )
    review_clamps = dict(
        _runtime_policy_lookup(getattr(controller, "review_policy", {}), "param_clamps", {}) or {}
    )
    for key, value in raw.items():
        if value is None:
            continue
        if key == "position_size":
            bounds = dict(risk_clamps.get("position_size") or {"min": 0.0, "max": 1.0})
            clamped = _runtime_clamp_between(
                value,
                float(bounds.get("min", 0.0)),
                float(bounds.get("max", 1.0)),
            )
            if clamped is not None:
                clean[key] = clamped
            continue
        if key == "cash_reserve":
            bounds = dict(review_clamps.get("cash_reserve") or {"min": 0.0, "max": 0.80})
            clamped = _runtime_clamp_between(
                value,
                float(bounds.get("min", 0.0)),
                float(bounds.get("max", 0.80)),
            )
            if clamped is not None:
                clean[key] = clamped
            continue
        if key == "signal_threshold":
            bounds = dict(review_clamps.get("signal_threshold") or {"min": 0.30, "max": 0.95})
            clamped = _runtime_clamp_between(
                value,
                float(bounds.get("min", 0.30)),
                float(bounds.get("max", 0.95)),
            )
            if clamped is not None:
                clean[key] = clamped
            continue
        if key == "max_positions":
            parsed = _runtime_safe_int(value)
            if parsed is not None:
                clean[key] = max(1, parsed)
            continue
        if key == "max_hold_days":
            bounds = dict(review_clamps.get("max_hold_days") or {"min": 5, "max": 60})
            parsed = _runtime_safe_int(value)
            if parsed is not None:
                clean[key] = max(
                    int(bounds.get("min", 5)),
                    min(int(bounds.get("max", 60)), parsed),
                )
            continue
        parsed_float = _runtime_safe_float(value)
        if parsed_float is not None:
            baseline_value = base_params.get(key)
            if isinstance(baseline_value, int) and not isinstance(baseline_value, bool):
                clean[key] = int(round(parsed_float))
            else:
                clean[key] = round(parsed_float, 6)
            continue
        clean[key] = value
    return clean


def resolve_entry_threshold_spec(params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = _runtime_copy_dict(params or {})
    for key in ENTRY_THRESHOLD_KEYS:
        value = _runtime_safe_float(payload.get(key))
        if value is not None:
            return {"key": key, "value": value}
    return {"key": "", "value": None}


def build_regime_runtime_profile(
    controller: Any,
    *,
    regime: str | None = None,
    base_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_params = _runtime_copy_dict(base_params or resolve_active_runtime_params(controller))
    governance_decision = governance_from_controller(controller)
    requested_regime = _runtime_normalize_regime(
        regime
        or governance_decision.get("regime")
        or dict(getattr(controller, "last_routing_decision", {}) or {}).get("regime")
    )
    strategy_family = _runtime_resolve_strategy_family(controller)
    strategy_family_budgets = _runtime_resolve_strategy_family_regime_budgets(controller)
    regime_controls = _runtime_resolve_controller_regime_controls(controller)
    raw_family_budget = _runtime_copy_dict(strategy_family_budgets.get(requested_regime, {}) or {})
    raw_model_overlay = _runtime_copy_dict(regime_controls.get(requested_regime, {}) or {})
    raw_model_budget_override = {
        key: value
        for key, value in raw_model_overlay.items()
        if str(key) in RUNTIME_BUDGET_KEYS
    }
    raw_behavior_overlay = {
        key: value
        for key, value in raw_model_overlay.items()
        if str(key) not in RUNTIME_BUDGET_KEYS
    }
    raw_overlay = _runtime_copy_dict(raw_family_budget)
    raw_overlay.update(raw_model_budget_override)
    raw_overlay.update(raw_behavior_overlay)
    overlay = _runtime_sanitize_regime_overlay(
        controller,
        base_params=active_params,
        overlay=raw_overlay,
    )
    family_budget = _runtime_sanitize_regime_overlay(
        controller,
        base_params=active_params,
        overlay=raw_family_budget,
    )
    model_budget_override = _runtime_sanitize_regime_overlay(
        controller,
        base_params=active_params,
        overlay=raw_model_budget_override,
    )
    behavior_overlay = _runtime_sanitize_regime_overlay(
        controller,
        base_params=active_params,
        overlay=raw_behavior_overlay,
    )
    effective_params = _runtime_copy_dict(active_params)
    effective_params.update(overlay)
    resolved_budget = {
        key: effective_params.get(key)
        for key in RUNTIME_BUDGET_KEYS
        if effective_params.get(key) is not None
    }
    source_parts: list[str] = []
    if family_budget:
        source_parts.append("strategy_family_risk_budget")
    if raw_model_overlay:
        source_parts.append("model_regime_controls")
    profile_source = "+".join(source_parts) if source_parts else "base_runtime"
    return {
        "schema_version": "training.regime_runtime_profile.v1",
        "regime": requested_regime,
        "strategy_family": strategy_family,
        "source": profile_source if overlay else "base_runtime",
        "controls_configured": bool(regime_controls or strategy_family_budgets),
        "applied": bool(overlay),
        "control_keys": sorted(raw_overlay.keys()),
        "budget_control_keys": sorted(
            {
                str(key)
                for key in list(raw_family_budget.keys()) + list(raw_model_budget_override.keys())
            }
        ),
        "behavior_control_keys": sorted(raw_behavior_overlay.keys()),
        "base_params": active_params,
        "overlay": overlay,
        "effective_params": effective_params,
        "entry_threshold": resolve_entry_threshold_spec(behavior_overlay),
        "budget_layering": {
            "schema_version": "training.regime_budget_layering.v1",
            "strategy_family": strategy_family,
            "family_budget": family_budget,
            "model_budget_override": model_budget_override,
            "behavior_overlay": behavior_overlay,
            "resolved_budget": resolved_budget,
            "budget_keys": sorted(RUNTIME_BUDGET_KEYS),
            "source": profile_source,
        },
    }


def apply_regime_runtime_profile(
    controller: Any,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_params = _runtime_copy_dict(dict(profile or {}).get("effective_params") or {})
    if not effective_params:
        effective_params = _runtime_copy_dict(resolve_active_runtime_params(controller))
    setattr(controller, "current_cycle_effective_runtime_params", _runtime_copy_dict(effective_params))
    setattr(controller, "current_cycle_regime_profile", deepcopy(dict(profile or {})))
    _runtime_sync_model(controller, effective_params)
    return _runtime_copy_dict(effective_params)


def resolve_effective_runtime_params(controller: Any) -> dict[str, Any]:
    if bool(getattr(controller, "current_cycle_runtime_locked", False)):
        effective = _runtime_copy_dict(
            getattr(controller, "current_cycle_effective_runtime_params", {}) or {}
        )
        if effective:
            return effective
    return resolve_active_runtime_params(controller)


def resolve_active_runtime_params(controller: Any) -> dict[str, Any]:
    if bool(getattr(controller, "current_cycle_runtime_locked", False)):
        frozen = _runtime_copy_dict(getattr(controller, "current_cycle_frozen_params", {}) or {})
        if frozen:
            return frozen
    return _runtime_copy_dict(session_current_params(controller) or {})


def begin_cycle_runtime_window(controller: Any, *, cycle_id: int) -> dict[str, Any]:
    active_params = _runtime_copy_dict(session_current_params(controller) or {})
    setattr(controller, "current_cycle_frozen_params", _runtime_copy_dict(active_params))
    setattr(controller, "current_cycle_start_params", _runtime_copy_dict(active_params))
    setattr(controller, "current_cycle_effective_runtime_params", _runtime_copy_dict(active_params))
    setattr(controller, "current_cycle_learning_proposals", [])
    setattr(controller, "current_cycle_runtime_violations", [])
    setattr(controller, "current_cycle_safety_overrides", {})
    setattr(controller, "current_cycle_deferred_runtime_adjustments", {})
    setattr(
        controller,
        "current_cycle_regime_profile",
        {
            "schema_version": "training.regime_runtime_profile.v1",
            "regime": "unknown",
            "source": "base_runtime",
            "controls_configured": bool(_runtime_resolve_controller_regime_controls(controller)),
            "applied": False,
            "control_keys": [],
            "base_params": _runtime_copy_dict(active_params),
            "overlay": {},
            "effective_params": _runtime_copy_dict(active_params),
            "entry_threshold": resolve_entry_threshold_spec(active_params),
        },
    )
    setattr(controller, "current_cycle_selection_intercepts", {})
    setattr(controller, "current_cycle_runtime_locked", True)
    setattr(controller, "current_cycle_runtime_started_at", datetime.now().isoformat())
    setattr(controller, "current_cycle_runtime_started_for", int(cycle_id))
    _runtime_sync_model(controller, active_params)
    return _runtime_copy_dict(active_params)


def apply_safety_override(
    controller: Any,
    adjustments: dict[str, Any] | None,
    *,
    source: str,
) -> dict[str, Any]:
    clean = _runtime_copy_dict(adjustments or {})
    for key in clean:
        if key not in SAFETY_RUNTIME_KEYS:
            raise ValueError(f"Non-safety param {key} cannot override frozen runtime")
    if not clean:
        return {}

    update_session_current_params(controller, clean)
    if bool(getattr(controller, "current_cycle_runtime_locked", False)):
        frozen = _runtime_copy_dict(getattr(controller, "current_cycle_frozen_params", {}) or {})
        frozen.update(clean)
        setattr(controller, "current_cycle_frozen_params", frozen)
        effective = _runtime_copy_dict(
            getattr(controller, "current_cycle_effective_runtime_params", {}) or {}
        )
        effective.update(clean)
        setattr(controller, "current_cycle_effective_runtime_params", effective)
        safety_overrides = _runtime_copy_dict(
            getattr(controller, "current_cycle_safety_overrides", {}) or {}
        )
        safety_overrides.update(clean)
        setattr(controller, "current_cycle_safety_overrides", safety_overrides)

    _runtime_sync_model(controller, resolve_effective_runtime_params(controller))
    proposals = getattr(controller, "current_cycle_learning_proposals", None)
    if not isinstance(proposals, list):
        proposals = []
        setattr(controller, "current_cycle_learning_proposals", proposals)
    proposal = {
        "cycle_id": int(
            getattr(controller, "current_cycle_runtime_started_for", 0)
            or getattr(controller, "current_cycle_id", 0)
            or 0
        ),
        "source": str(source or "safety_override"),
        "target_scope": "safety",
        "patch": _runtime_copy_dict(clean),
        "rationale": "approved safety override",
        "metadata": {"proposal_kind": "safety_override"},
        "active_params_snapshot": resolve_active_runtime_params(controller),
        "created_at": datetime.now().isoformat(),
    }
    ensure_tracking_fields = _resolve_training_module_attr(
        "observability",
        "ensure_proposal_tracking_fields",
    )
    if callable(ensure_tracking_fields):
        proposal = dict(
            ensure_tracking_fields(
                proposal,
                default_cycle_id=int(proposal["cycle_id"]),
            )
        )
    proposals.append(proposal)
    return clean


def finalize_cycle_runtime_window(controller: Any) -> dict[str, Any]:
    proposals = deepcopy(list(getattr(controller, "current_cycle_learning_proposals", []) or []))
    safety_overrides = _runtime_copy_dict(getattr(controller, "current_cycle_safety_overrides", {}) or {})
    deferred_adjustments = _runtime_copy_dict(
        getattr(controller, "current_cycle_deferred_runtime_adjustments", {}) or {}
    )
    start_params = _runtime_copy_dict(getattr(controller, "current_cycle_start_params", {}) or {})
    current_params = _runtime_copy_dict(session_current_params(controller) or {})
    violations: list[dict[str, Any]] = []
    if bool(getattr(controller, "current_cycle_runtime_locked", False)):
        for key in sorted(set(start_params) | set(current_params)):
            if key in safety_overrides:
                continue
            before = start_params.get(key)
            after = current_params.get(key)
            if before == after:
                continue
            violations.append(
                {
                    "key": key,
                    "before": before,
                    "after": after,
                    "violation_type": "illegal_cycle_runtime_mutation",
                }
            )

    if violations:
        logger.warning(
            "Detected illegal runtime mutation during cycle: %s",
            [item["key"] for item in violations],
        )
        update_session_current_params(controller, _runtime_copy_dict(start_params))
        _runtime_sync_model(controller, start_params)

    summary = {
        "cycle_id": int(
            getattr(controller, "current_cycle_runtime_started_for", 0)
            or getattr(controller, "current_cycle_id", 0)
            or 0
        ),
        "proposal_count": len(proposals),
        "violation_count": len(violations),
        "violations": deepcopy(violations),
        "safety_override_keys": sorted(safety_overrides.keys()),
        "deferred_runtime_adjustment_keys": sorted(deferred_adjustments.keys()),
        "deferred_runtime_adjustments": deepcopy(deferred_adjustments),
        "frozen_params": resolve_active_runtime_params(controller),
        "effective_runtime_params": resolve_effective_runtime_params(controller),
        "regime_runtime_profile": deepcopy(
            dict(getattr(controller, "current_cycle_regime_profile", {}) or {})
        ),
        "selection_intercepts": deepcopy(
            dict(getattr(controller, "current_cycle_selection_intercepts", {}) or {})
        ),
    }

    setattr(controller, "last_cycle_learning_proposals", proposals)
    setattr(controller, "last_cycle_runtime_summary", deepcopy(summary))
    setattr(controller, "current_cycle_runtime_violations", deepcopy(violations))
    setattr(controller, "current_cycle_learning_proposals", [])
    setattr(controller, "current_cycle_frozen_params", {})
    setattr(controller, "current_cycle_start_params", {})
    setattr(controller, "current_cycle_effective_runtime_params", {})
    setattr(controller, "current_cycle_safety_overrides", {})
    setattr(controller, "current_cycle_deferred_runtime_adjustments", {})
    setattr(controller, "current_cycle_regime_profile", {})
    setattr(controller, "current_cycle_selection_intercepts", {})
    setattr(controller, "current_cycle_runtime_locked", False)
    setattr(controller, "current_cycle_runtime_started_at", "")
    setattr(controller, "current_cycle_runtime_started_for", 0)
    if deferred_adjustments:
        update_session_current_params(controller, deferred_adjustments)
        manager_runtime = getattr(controller, "manager_runtime", None)
        if manager_runtime is not None and hasattr(manager_runtime, "update_runtime_overrides"):
            manager_runtime.update_runtime_overrides(deferred_adjustments)
    _runtime_sync_model(controller, _runtime_copy_dict(session_current_params(controller) or {}))
    return summary


def _cycle_learning_proposals(
    controller: Any,
    *,
    cycle_id: int,
) -> list[dict[str, Any]]:
    proposals = [
        _runtime_copy_dict(item)
        for item in list(getattr(controller, "current_cycle_learning_proposals", []) or [])
        if _runtime_copy_dict(item)
    ]
    if proposals:
        return proposals
    feedback_payload = _runtime_copy_dict(session_last_feedback_optimization(controller) or {})
    return [
        _runtime_copy_dict(item)
        for item in list(feedback_payload.get("learning_proposals") or [])
        if _runtime_copy_dict(item)
    ]


def _proposal_block_reason_map(gate_result: dict[str, Any]) -> dict[str, list[str]]:
    blocked: dict[str, list[str]] = {}
    for proposal in list(gate_result.get("blocked_proposals") or []):
        payload = _runtime_copy_dict(proposal or {})
        proposal_id = str(payload.get("proposal_id") or "")
        if proposal_id:
            blocked[proposal_id] = [
                str(reason).strip()
                for reason in list(payload.get("block_reasons") or [])
                if str(reason).strip()
            ]
    return blocked


def _fallback_candidate_proposal_gate(
    proposal_bundle: dict[str, Any],
) -> dict[str, Any]:
    proposals = [_runtime_copy_dict(item) for item in list(proposal_bundle.get("proposals") or [])]
    param_adjustments: dict[str, Any] = {}
    scoring_adjustments: dict[str, Any] = {}
    agent_weight_adjustments: dict[str, Any] = {}
    proposal_refs: list[str] = []
    requested_source_summary: dict[str, int] = {}

    for item in proposals:
        source = str(item.get("source") or "unknown").strip() or "unknown"
        requested_source_summary[source] = int(requested_source_summary.get(source, 0) or 0) + 1
        if str(item.get("target_scope") or "candidate") != "candidate":
            continue
        patch = _runtime_copy_dict(item.get("patch") or {})
        if not patch:
            continue
        proposal_id = str(item.get("proposal_id") or "").strip()
        if proposal_id:
            proposal_refs.append(proposal_id)
        proposal_kind = str(
            _runtime_copy_dict(item.get("metadata") or {}).get("proposal_kind") or source
        ).lower()
        if "scoring" in proposal_kind:
            scoring_adjustments.update(patch)
        elif "agent_weight" in proposal_kind:
            agent_weight_adjustments.update(patch)
        else:
            param_adjustments.update(patch)

    return {
        "passed": True,
        "filtered_adjustments": {
            "params": param_adjustments,
            "scoring": scoring_adjustments,
            "agent_weights": agent_weight_adjustments,
            "proposal_refs": proposal_refs,
        },
        "blocked_adjustments": {
            "params": {},
            "scoring": {},
            "agent_weights": {},
        },
        "blocked_proposals": [],
        "proposal_summary": {
            "requested_source_summary": requested_source_summary,
            "requested_proposal_count": len(proposals),
            "approved_proposal_count": len(proposal_refs),
            "approved_proposal_refs": list(proposal_refs),
            "blocked_proposal_refs": [],
        },
    }


def _resolve_candidate_proposal_gate(
    controller: Any,
    *,
    cycle_id: int,
    proposal_bundle: dict[str, Any],
) -> dict[str, Any]:
    candidate_functions: list[Callable[..., Any]] = []
    training_policy_gate = _resolve_training_module_attr(
        "policy",
        "evaluate_candidate_proposal_gate",
    )
    if callable(training_policy_gate):
        candidate_functions.append(training_policy_gate)
    try:
        shared_policy_module = import_module("invest_evolution.investment.shared.policy")
        shared_policy_gate = getattr(shared_policy_module, "evaluate_candidate_proposal_gate", None)
        if callable(shared_policy_gate):
            candidate_functions.append(shared_policy_gate)
    except Exception:
        pass

    for gate_fn in candidate_functions:
        invocations: tuple[Callable[[], Any], ...] = (
            lambda: gate_fn(
                controller,
                cycle_id=cycle_id,
                proposal_bundle=proposal_bundle,
            ),
            lambda: gate_fn(
                cycle_id=cycle_id,
                proposal_bundle=proposal_bundle,
                controller=controller,
            ),
            lambda: gate_fn(
                proposal_bundle=proposal_bundle,
                cycle_id=cycle_id,
            ),
            lambda: gate_fn(proposal_bundle, cycle_id=cycle_id),
        )
        for invoke in invocations:
            try:
                result = invoke()
            except TypeError:
                continue
            except Exception:
                logger.warning("proposal gate evaluation failed", exc_info=True)
                break
            if isinstance(result, dict):
                return _runtime_copy_dict(result)
    return _fallback_candidate_proposal_gate(proposal_bundle)


def _runtime_override_keys(
    *,
    param_adjustments: dict[str, Any],
    scoring_adjustments: dict[str, Any],
    agent_weight_adjustments: dict[str, Any],
) -> list[str]:
    return sorted(
        {
            *(str(key) for key in param_adjustments.keys()),
            *(str(key) for key in scoring_adjustments.keys()),
            *(str(key) for key in agent_weight_adjustments.keys()),
        }
    )


def _resolve_candidate_event(
    event_factory: Any,
    *,
    cycle_id: int,
    trigger_reason: str,
    stage: str,
    decision: dict[str, Any],
    applied_change: dict[str, Any],
    lineage: dict[str, Any],
    evidence: dict[str, Any],
    notes: str,
) -> Any:
    event = _call_training_module_if_available(
        "observability",
        "_new_optimization_event",
        event_factory,
        cycle_id=int(cycle_id),
        trigger=trigger_reason,
        stage=stage,
        decision=decision,
        applied_change=applied_change,
        lineage=lineage,
        evidence=evidence,
        notes=notes,
    )
    if event is not None:
        return event
    return event_factory(
        cycle_id=int(cycle_id),
        trigger=trigger_reason,
        stage=stage,
        status="ok",
        decision=dict(decision or {}),
        suggestions=[],
        applied_change=dict(applied_change or {}),
        lineage=dict(lineage or {}),
        evidence=dict(evidence or {}),
        notes=str(notes or ""),
    )


def _refresh_bundle_tracking(
    controller: Any,
    *,
    cycle_id: int,
    bundle: dict[str, Any],
    gate_result: dict[str, Any],
    decision_stage: str,
    decision_reason: str,
    candidate_runtime_config_ref: str = "",
    candidate_version_id: str = "",
    pending_candidate_ref: str = "",
) -> dict[str, Any]:
    proposals = [_runtime_copy_dict(item) for item in list(bundle.get("proposals") or [])]
    if not proposals:
        return _runtime_copy_dict(bundle)
    apply_proposal_outcome_fn = _resolve_training_module_attr("observability", "apply_proposal_outcome")
    build_tracking_summary_fn = _resolve_training_module_attr(
        "observability",
        "build_suggestion_tracking_summary",
    )
    update_bundle_fn = _resolve_training_module_attr("persistence", "update_cycle_proposal_bundle")
    approved_refs = {
        str(item).strip()
        for item in list(
            _runtime_copy_dict(gate_result.get("proposal_summary") or {}).get("approved_proposal_refs")
            or _runtime_copy_dict(gate_result.get("filtered_adjustments") or {}).get("proposal_refs")
            or []
        )
        if str(item).strip()
    }
    blocked_reason_map = _proposal_block_reason_map(gate_result)
    updated_proposals: list[dict[str, Any]] = []
    bundle_id = str(bundle.get("proposal_bundle_id") or "")
    for proposal in proposals:
        payload = _runtime_copy_dict(proposal)
        if str(payload.get("target_scope") or "candidate") != "candidate":
            updated_proposals.append(payload)
            continue
        proposal_id = str(payload.get("proposal_id") or "")
        if proposal_id in blocked_reason_map and callable(apply_proposal_outcome_fn):
            updated_proposals.append(
                _runtime_copy_dict(
                    apply_proposal_outcome_fn(
                        payload,
                        adoption_status="rejected_by_proposal_gate",
                        decision_cycle_id=int(cycle_id),
                        decision_stage=decision_stage,
                        decision_reason=decision_reason,
                        proposal_bundle_id=bundle_id,
                        block_reasons=blocked_reason_map.get(proposal_id) or [],
                    )
                )
            )
            continue
        if proposal_id in approved_refs and callable(apply_proposal_outcome_fn):
            adoption_status = (
                "deferred_pending_candidate"
                if pending_candidate_ref
                else ("adopted_to_candidate" if candidate_runtime_config_ref else "queued")
            )
            updated_proposals.append(
                _runtime_copy_dict(
                    apply_proposal_outcome_fn(
                        payload,
                        adoption_status=adoption_status,
                        decision_cycle_id=int(cycle_id),
                        decision_stage=decision_stage,
                        decision_reason=decision_reason,
                        candidate_runtime_config_ref=candidate_runtime_config_ref,
                        candidate_config_ref=candidate_runtime_config_ref,
                        candidate_version_id=candidate_version_id,
                        pending_candidate_ref=pending_candidate_ref,
                        proposal_bundle_id=bundle_id,
                    )
                )
            )
            continue
        updated_proposals.append(payload)

    updated_bundle = _runtime_copy_dict(bundle)
    updated_bundle["proposals"] = updated_proposals
    if callable(build_tracking_summary_fn):
        updated_bundle["suggestion_tracking_summary"] = _runtime_copy_dict(
            build_tracking_summary_fn(updated_proposals)
        )
    updated_bundle["proposal_count"] = len(updated_proposals)
    updated_bundle["proposal_ids"] = [
        str(_runtime_copy_dict(item).get("proposal_id") or "")
        for item in updated_proposals
    ]
    bundle_path = str(bundle.get("bundle_path") or "")
    if bundle_path and callable(update_bundle_fn):
        try:
            persisted = update_bundle_fn(
                controller,
                bundle_path=bundle_path,
                proposals=updated_proposals,
            )
        except TypeError:
            persisted = update_bundle_fn(
                controller,
                bundle_path,
                updated_proposals,
            )
        if isinstance(persisted, dict):
            updated_bundle = _runtime_copy_dict(persisted)
    return updated_bundle


def _attach_suggestion_summary_to_event(event: Any, bundle: dict[str, Any]) -> None:
    summary = _runtime_copy_dict(bundle.get("suggestion_tracking_summary") or {})
    if not summary:
        return
    if isinstance(event, dict):
        evidence = _runtime_copy_dict(event.get("evidence") or {})
        evidence["suggestion_tracking_summary"] = summary
        event["evidence"] = evidence
        return
    evidence = _runtime_copy_dict(getattr(event, "evidence", {}) or {})
    evidence["suggestion_tracking_summary"] = summary
    setattr(event, "evidence", evidence)


def _mutate_candidate_runtime(
    controller: Any,
    *,
    active_runtime_config_ref: str,
    trigger_reason: str,
    cycle_id: int,
    proposal_bundle_id: str,
    proposal_refs: list[str],
    param_adjustments: dict[str, Any],
    scoring_adjustments: dict[str, Any],
    agent_weight_adjustments: dict[str, Any],
) -> dict[str, Any]:
    mutator = getattr(controller, "runtime_config_mutator", None) or getattr(
        controller,
        "model_mutator",
        None,
    )
    mutate = getattr(mutator, "mutate", None)
    if not callable(mutate):
        raise RuntimeError("candidate mutation runtime is unavailable")
    kwargs = {
        "param_adjustments": param_adjustments or None,
        "scoring_adjustments": scoring_adjustments or None,
        "narrative_adjustments": {"last_trigger": trigger_reason},
        "generation_label": f"cycle_{int(cycle_id):04d}",
        "parent_meta": {
            "cycle_id": int(cycle_id),
            "trigger": trigger_reason,
            "proposal_bundle_id": str(proposal_bundle_id or ""),
            "proposal_refs": list(proposal_refs or []),
        },
    }
    invocations: tuple[Callable[[], Any], ...] = (
        lambda: mutate(
            active_runtime_config_ref,
            **kwargs,
        ),
        lambda: mutate(
            active_runtime_config_ref,
            agent_weight_adjustments=agent_weight_adjustments or None,
            **kwargs,
        ),
        lambda: mutate(
            active_runtime_config_ref,
            adjustments={
                "params": param_adjustments,
                "scoring": scoring_adjustments,
                "agent_weights": agent_weight_adjustments,
            },
            generation_label=kwargs["generation_label"],
            parent_meta=kwargs["parent_meta"],
        ),
    )
    for invoke in invocations:
        try:
            result = invoke()
        except TypeError:
            continue
        if isinstance(result, dict):
            return _runtime_copy_dict(result)
    raise RuntimeError("candidate mutation runtime cannot satisfy mutate signature")


def build_cycle_candidate_from_proposals(
    controller: Any,
    *,
    cycle_id: int,
    proposal_bundle: dict[str, Any] | None = None,
    event_factory: Any,
    trigger_reason: str = "cycle_review_completed",
) -> Any | None:
    resolved_cycle_id = int(cycle_id)
    bundle = _runtime_copy_dict(proposal_bundle or {})
    if not bundle:
        proposals = _cycle_learning_proposals(controller, cycle_id=resolved_cycle_id)
        persisted_bundle = _call_training_module_if_available(
            "persistence",
            "persist_cycle_proposal_bundle",
            controller,
            cycle_id=resolved_cycle_id,
            execution_snapshot={
                "active_runtime_config_ref": str(
                    resolve_effective_runtime_params(controller).get("active_runtime_config_ref")
                    or ""
                ),
                "model_name": str(getattr(controller, "model_name", "") or ""),
            },
            proposals=proposals or None,
        )
        if isinstance(persisted_bundle, dict):
            bundle = _runtime_copy_dict(persisted_bundle)
        else:
            bundle = {
                "cycle_id": resolved_cycle_id,
                "model_name": str(getattr(controller, "model_name", "") or ""),
                "active_runtime_config_ref": str(
                    getattr(controller, "model_config_path", "") or ""
                ),
                "proposals": proposals,
                "proposal_count": len(proposals),
                "proposal_ids": [
                    str(_runtime_copy_dict(item).get("proposal_id") or "")
                    for item in proposals
                ],
                "proposal_bundle_id": f"proposal_bundle_{resolved_cycle_id:04d}",
                "bundle_path": "",
                "suggestion_tracking_summary": {},
            }
    proposals = [_runtime_copy_dict(item) for item in list(bundle.get("proposals") or [])]
    if not proposals:
        return None

    active_runtime_config_ref = str(
        normalize_config_ref(
            bundle.get("active_runtime_config_ref")
            or bundle.get("active_config_ref")
            or _runtime_copy_dict(bundle.get("execution_snapshot") or {}).get("active_runtime_config_ref")
            or getattr(controller, "model_config_path", "")
            or ""
        )
        or bundle.get("active_runtime_config_ref")
        or bundle.get("active_config_ref")
        or _runtime_copy_dict(bundle.get("execution_snapshot") or {}).get("active_runtime_config_ref")
        or getattr(controller, "model_config_path", "")
        or ""
    ).strip()
    gate_result = _resolve_candidate_proposal_gate(
        controller,
        cycle_id=resolved_cycle_id,
        proposal_bundle=bundle,
    )
    allowed_adjustments = _runtime_copy_dict(gate_result.get("filtered_adjustments") or {})
    param_adjustments = _runtime_copy_dict(allowed_adjustments.get("params") or {})
    scoring_adjustments = _runtime_copy_dict(allowed_adjustments.get("scoring") or {})
    agent_weight_adjustments = _runtime_copy_dict(allowed_adjustments.get("agent_weights") or {})
    proposal_refs = [
        str(item).strip()
        for item in list(allowed_adjustments.get("proposal_refs") or [])
        if str(item).strip()
    ]
    runtime_override_keys = _runtime_override_keys(
        param_adjustments=param_adjustments,
        scoring_adjustments=scoring_adjustments,
        agent_weight_adjustments=agent_weight_adjustments,
    )
    projection = project_manager_compatibility(
        controller,
        execution_snapshot={
            "active_runtime_config_ref": active_runtime_config_ref,
            "manager_config_ref": active_runtime_config_ref,
        },
    )
    manager_id = str(
        projection.manager_id
        or controller_default_manager_id(controller, default="")
        or ""
    ).strip()
    fitness_source_cycles = [
        int(getattr(item, "cycle_id"))
        for item in list(session_cycle_history(controller) or [])[-10:]
        if getattr(item, "cycle_id", None) is not None
    ]
    context = _new_optimization_boundary_context(
        cycle_id=resolved_cycle_id,
        manager_id=manager_id,
        active_runtime_config_ref=active_runtime_config_ref,
        fitness_source_cycles=fitness_source_cycles,
    )
    if not (param_adjustments or scoring_adjustments or agent_weight_adjustments):
        blocked_adjustments = _runtime_copy_dict(gate_result.get("blocked_adjustments") or {})
        blocked_proposals = list(gate_result.get("blocked_proposals") or [])
        has_blocked = bool(blocked_proposals) or any(
            _runtime_copy_dict(blocked_adjustments.get(scope) or {})
            for scope in ("params", "scoring", "agent_weights")
        )
        if not has_blocked:
            return None
        event = _resolve_candidate_event(
            event_factory,
            cycle_id=resolved_cycle_id,
            trigger_reason=trigger_reason,
            stage="candidate_build_skipped",
            decision={
                "skipped": True,
                "skip_reason": "proposal_governance_rejected",
                "proposal_bundle_id": str(bundle.get("proposal_bundle_id") or ""),
                "proposal_bundle_path": str(bundle.get("bundle_path") or ""),
            },
            applied_change={
                "params": param_adjustments,
                "scoring": scoring_adjustments,
                "agent_weights": agent_weight_adjustments,
                "proposal_refs": proposal_refs,
                "proposal_count": len(proposal_refs),
            },
            lineage=build_optimization_lineage(
                context,
                candidate_runtime_config_ref="",
                deployment_stage="active",
                runtime_override_keys=[],
                promotion_status="proposal_rejected",
            ),
            evidence={
                "proposal_bundle_id": str(bundle.get("proposal_bundle_id") or ""),
                "proposal_bundle_path": str(bundle.get("bundle_path") or ""),
                "proposal_source_summary": _runtime_copy_dict(
                    _runtime_copy_dict(gate_result.get("proposal_summary") or {}).get(
                        "requested_source_summary"
                    )
                    or {}
                ),
                "proposal_gate": gate_result,
            },
            notes="all candidate changes blocked by proposal governance gate",
        )
        updated_bundle = _refresh_bundle_tracking(
            controller,
            cycle_id=resolved_cycle_id,
            bundle=bundle,
            gate_result=gate_result,
            decision_stage="candidate_build_skipped",
            decision_reason="proposal_governance_rejected",
        )
        _attach_suggestion_summary_to_event(event, updated_bundle)
        return event

    pending_candidate_ref = _latest_open_candidate_runtime_config_ref(controller)
    if pending_candidate_ref and not bool(getattr(controller, "auto_apply_mutation", False)):
        event = _resolve_candidate_event(
            event_factory,
            cycle_id=resolved_cycle_id,
            trigger_reason=trigger_reason,
            stage="candidate_build_skipped",
            decision={
                "skipped": True,
                "skip_reason": "pending_candidate_unresolved",
                "pending_candidate_ref": pending_candidate_ref,
                "auto_applied": False,
                "proposal_bundle_id": str(bundle.get("proposal_bundle_id") or ""),
                "proposal_bundle_path": str(bundle.get("bundle_path") or ""),
            },
            applied_change={
                "params": param_adjustments,
                "scoring": scoring_adjustments,
                "agent_weights": agent_weight_adjustments,
                "proposal_refs": proposal_refs,
                "proposal_count": len(proposal_refs),
            },
            lineage=build_optimization_lineage(
                context,
                candidate_runtime_config_ref=pending_candidate_ref,
                deployment_stage="candidate",
                runtime_override_keys=runtime_override_keys,
                promotion_status="candidate_generated",
            ),
            evidence={
                "skip_reason": "pending_candidate_unresolved",
                "pending_candidate_ref": pending_candidate_ref,
                "proposal_bundle_id": str(bundle.get("proposal_bundle_id") or ""),
                "proposal_bundle_path": str(bundle.get("bundle_path") or ""),
                "proposal_source_summary": _runtime_copy_dict(
                    _runtime_copy_dict(gate_result.get("proposal_summary") or {}).get(
                        "requested_source_summary"
                    )
                    or {}
                ),
                "proposal_gate": gate_result,
            },
            notes="existing pending candidate reused; skip generating another candidate runtime config",
        )
        updated_bundle = _refresh_bundle_tracking(
            controller,
            cycle_id=resolved_cycle_id,
            bundle=bundle,
            gate_result=gate_result,
            decision_stage="candidate_build_skipped",
            decision_reason="pending_candidate_unresolved",
            pending_candidate_ref=pending_candidate_ref,
        )
        _attach_suggestion_summary_to_event(event, updated_bundle)
        return event

    mutation = _mutate_candidate_runtime(
        controller,
        active_runtime_config_ref=active_runtime_config_ref,
        trigger_reason=trigger_reason,
        cycle_id=resolved_cycle_id,
        proposal_bundle_id=str(bundle.get("proposal_bundle_id") or ""),
        proposal_refs=proposal_refs,
        param_adjustments=param_adjustments,
        scoring_adjustments=scoring_adjustments,
        agent_weight_adjustments=agent_weight_adjustments,
    )
    raw_candidate_runtime_ref = str(
        mutation.get("runtime_config_ref")
        or mutation.get("config_path")
        or mutation.get("runtime_config_path")
        or ""
    ).strip()
    candidate_runtime_config_ref = str(
        normalize_config_ref(raw_candidate_runtime_ref) or raw_candidate_runtime_ref
    ).strip()
    candidate_version_id = str(
        _runtime_copy_dict(mutation.get("meta") or {}).get("version_id") or ""
    ).strip()
    auto_applied = bool(getattr(controller, "auto_apply_mutation", False))
    if auto_applied:
        reload_model = getattr(controller, "_reload_investment_model", None)
        if callable(reload_model):
            try:
                reload_model(candidate_runtime_config_ref)
            except TypeError:
                reload_model()
    event = _resolve_candidate_event(
        event_factory,
        cycle_id=resolved_cycle_id,
        trigger_reason=trigger_reason,
        stage="candidate_build",
        decision={
            "config_path": candidate_runtime_config_ref,
            "runtime_config_ref": candidate_runtime_config_ref,
            "meta_path": str(mutation.get("meta_path") or ""),
            "auto_applied": auto_applied,
            "proposal_bundle_id": str(bundle.get("proposal_bundle_id") or ""),
            "proposal_bundle_path": str(bundle.get("bundle_path") or ""),
            "candidate_version_id": candidate_version_id,
        },
        applied_change={
            "params": param_adjustments,
            "scoring": scoring_adjustments,
            "agent_weights": agent_weight_adjustments,
            "proposal_refs": proposal_refs,
            "proposal_count": len(proposal_refs),
        },
        lineage=build_optimization_lineage(
            context,
            candidate_runtime_config_ref=candidate_runtime_config_ref,
            deployment_stage="active" if auto_applied else "candidate",
            runtime_override_keys=runtime_override_keys,
            promotion_status="candidate_auto_applied" if auto_applied else "candidate_generated",
        ),
        evidence={
            "mutation_meta": _runtime_copy_dict(mutation.get("meta") or {}),
            "auto_applied": auto_applied,
            "proposal_bundle_id": str(bundle.get("proposal_bundle_id") or ""),
            "proposal_bundle_path": str(bundle.get("bundle_path") or ""),
            "proposal_source_summary": _runtime_copy_dict(
                _runtime_copy_dict(gate_result.get("proposal_summary") or {}).get(
                    "requested_source_summary"
                )
                or {}
            ),
            "proposal_gate": gate_result,
        },
        notes=(
            "active runtime config mutated"
            if auto_applied
            else "candidate runtime config generated; active runtime config unchanged"
        ),
    )
    updated_bundle = _refresh_bundle_tracking(
        controller,
        cycle_id=resolved_cycle_id,
        bundle=bundle,
        gate_result=gate_result,
        decision_stage="candidate_build",
        decision_reason="candidate_auto_applied" if auto_applied else "candidate_generated",
        candidate_runtime_config_ref=candidate_runtime_config_ref,
        candidate_version_id=candidate_version_id,
    )
    _attach_suggestion_summary_to_event(event, updated_bundle)
    append_event = getattr(controller, "_append_optimization_event", None)
    if callable(append_event):
        append_event(event)
    emit_module_log = getattr(controller, "_emit_module_log", None)
    if callable(emit_module_log):
        event_evidence = (
            _runtime_copy_dict(event.get("evidence") or {})
            if isinstance(event, dict)
            else _runtime_copy_dict(getattr(event, "evidence", {}) or {})
        )
        emit_module_log(
            "optimization",
            "候选 runtime 配置已生成",
            (
                f"已自动接管 active：{candidate_runtime_config_ref}"
                if auto_applied
                else f"候选配置已生成（未自动接管 active）：{candidate_runtime_config_ref}"
            ),
            cycle_id=resolved_cycle_id,
            kind="candidate_build",
            details=event_evidence,
            metrics={"adjustment_count": len(runtime_override_keys)},
        )
    return event


@dataclass(frozen=True, init=False)
class TrainingSelectionResult:
    regime_result: dict[str, Any]
    selected_codes: list[str]
    selected_data: dict[str, Any]
    selection_mode: str
    agent_used: bool
    manager_bundle: Any | None = None
    manager_results: list[dict[str, Any]] = field(default_factory=list)
    portfolio_plan: dict[str, Any] = field(default_factory=dict)
    dominant_manager_id: str = ""
    selection_trace: dict[str, Any] = field(default_factory=dict)
    compatibility_fields: dict[str, Any] = field(default_factory=dict)
    regime_runtime_profile: dict[str, Any] = field(default_factory=dict, compare=False)
    selection_intercepts: dict[str, Any] = field(default_factory=dict, compare=False)

    def __init__(
        self,
        *,
        regime_result: dict[str, Any],
        selected_codes: list[str] | None = None,
        selected_data: dict[str, Any] | None = None,
        selection_mode: str = "manager_portfolio",
        agent_used: bool = False,
        manager_bundle: Any | None = None,
        manager_results: list[dict[str, Any]] | None = None,
        portfolio_plan: dict[str, Any] | Any | None = None,
        dominant_manager_id: str = "",
        selection_trace: dict[str, Any] | None = None,
        compatibility_fields: dict[str, Any] | None = None,
        regime_runtime_profile: dict[str, Any] | None = None,
        selection_intercepts: dict[str, Any] | None = None,
        meeting_log: dict[str, Any] | None = None,
        selected: list[str] | None = None,
    ) -> None:
        normalized_portfolio_plan: dict[str, Any]
        normalized_portfolio_plan = _dict_payload(portfolio_plan)
        if not normalized_portfolio_plan and manager_bundle is not None:
            bundle_portfolio = getattr(manager_bundle, "portfolio_plan", None)
            normalized_portfolio_plan = _dict_payload(bundle_portfolio)

        normalized_selected_codes = [
            str(code).strip()
            for code in list(selected_codes or selected or [])
            if str(code).strip()
        ]
        if not normalized_selected_codes:
            normalized_selected_codes = [
                str(code).strip()
                for code in list(normalized_portfolio_plan.get("selected_codes") or [])
                if str(code).strip()
            ]

        normalized_manager_results = [
            _dict_payload(item)
            for item in list(manager_results or [])
        ]
        if not normalized_manager_results and manager_bundle is not None:
            normalized_manager_results = [
                _dict_payload(item)
                for item in list(getattr(manager_bundle, "manager_results", []) or [])
            ]

        normalized_dominant_manager_id = str(
            dominant_manager_id or getattr(manager_bundle, "dominant_manager_id", "") or ""
        ).strip()
        normalized_selection_trace = dict(selection_trace or meeting_log or {})
        if not normalized_selection_trace and manager_bundle is not None:
            normalized_selection_trace = {
                "selected": list(normalized_selected_codes),
                "active_managers": list(normalized_portfolio_plan.get("active_manager_ids") or []),
                "dominant_manager_id": normalized_dominant_manager_id,
                "portfolio_plan": dict(normalized_portfolio_plan),
                "manager_results": list(normalized_manager_results),
            }

        object.__setattr__(self, "regime_result", dict(regime_result or {}))
        object.__setattr__(self, "selected_codes", normalized_selected_codes)
        object.__setattr__(self, "selected_data", dict(selected_data or {}))
        object.__setattr__(self, "selection_mode", str(selection_mode or "manager_portfolio"))
        object.__setattr__(self, "agent_used", bool(agent_used))
        object.__setattr__(self, "manager_bundle", manager_bundle)
        object.__setattr__(self, "manager_results", normalized_manager_results)
        object.__setattr__(self, "portfolio_plan", normalized_portfolio_plan)
        object.__setattr__(self, "dominant_manager_id", normalized_dominant_manager_id)
        object.__setattr__(self, "selection_trace", normalized_selection_trace)
        object.__setattr__(self, "compatibility_fields", dict(compatibility_fields or {}))
        object.__setattr__(self, "regime_runtime_profile", dict(regime_runtime_profile or {}))
        object.__setattr__(self, "selection_intercepts", dict(selection_intercepts or {}))

    @property
    def selected(self) -> list[str]:
        return list(self.selected_codes)

    @property
    def meeting_log(self) -> dict[str, Any]:
        return dict(self.selection_trace or {})


class TrainingSelectionService:
    """Owns manager-runtime selection orchestration for the training hot path."""

    @staticmethod
    def _requested_regime(controller: Any) -> str:
        return str(dict(governance_from_controller(controller) or {}).get("regime") or "")

    @staticmethod
    def _bundle_dominant_output(bundle: Any) -> Any | None:
        resolver = getattr(bundle, "dominant_manager_output", None)
        if callable(resolver):
            return resolver()
        dominant_output = dict(getattr(bundle, "manager_outputs", {}) or {}).get(
            getattr(bundle, "dominant_manager_id", "")
        )
        if dominant_output is not None:
            return dominant_output
        manager_outputs = dict(getattr(bundle, "manager_outputs", {}) or {})
        if manager_outputs:
            return next(iter(manager_outputs.values()))
        return None

    @staticmethod
    def _bundle_manager_results_payload(bundle: Any) -> list[dict[str, Any]]:
        resolver = getattr(bundle, "manager_results_payload", None)
        if callable(resolver):
            resolved = resolver()
            if not isinstance(resolved, list):
                return []
            return [_dict_payload(item) for item in resolved]
        return [
            _dict_payload(item)
            for item in list(getattr(bundle, "manager_results", []) or [])
        ]

    @staticmethod
    def _bundle_portfolio_plan_payload(bundle: Any) -> dict[str, Any]:
        resolver = getattr(bundle, "portfolio_plan_payload", None)
        if callable(resolver):
            return _dict_payload(resolver())
        return _dict_payload(getattr(bundle, "portfolio_plan", None))

    @classmethod
    def _bundle_regime_result(
        cls,
        bundle: Any,
        *,
        selected_codes: list[str],
        trading_plan: Any,
    ) -> dict[str, Any]:
        resolver = getattr(bundle, "build_regime_result", None)
        if callable(resolver):
            return _dict_payload(resolver(selected_codes=selected_codes))
        portfolio_plan = getattr(bundle, "portfolio_plan", None)
        return {
            "regime": getattr(getattr(bundle, "run_context", None), "regime", "unknown"),
            "confidence": float(getattr(portfolio_plan, "confidence", 0.0) or 0.0),
            "reasoning": getattr(portfolio_plan, "reasoning", ""),
            "suggested_exposure": max(
                0.0,
                min(1.0, 1.0 - float(getattr(portfolio_plan, "cash_reserve", 0.0) or 0.0)),
            ),
            "decision_source": "manager_runtime",
            "params": {
                "top_n": len(selected_codes),
                "max_positions": int(getattr(trading_plan, "max_positions", 0) or 0),
                "manager_weights": dict(
                    getattr(getattr(bundle, "run_context", None), "budget_weights", {}) or {}
                ),
            },
        }

    @classmethod
    def _bundle_selection_trace(
        cls,
        bundle: Any,
        *,
        selected_codes: list[str],
        portfolio_plan_payload: dict[str, Any],
        manager_results_payload: list[dict[str, Any]],
    ) -> dict[str, Any]:
        resolver = getattr(bundle, "build_selection_trace", None)
        if callable(resolver):
            return _dict_payload(resolver(selected_codes=selected_codes))
        portfolio_plan = getattr(bundle, "portfolio_plan", None)
        return {
            "selected": list(selected_codes),
            "active_managers": list(getattr(portfolio_plan, "active_manager_ids", []) or []),
            "dominant_manager_id": getattr(bundle, "dominant_manager_id", ""),
            "portfolio_plan": dict(portfolio_plan_payload),
            "manager_results": list(manager_results_payload),
            "decision_source": "manager_runtime",
        }

    def run_selection_stage(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
    ) -> TrainingSelectionResult | None:
        regime_runtime_profile = build_regime_runtime_profile(
            controller,
            regime=self._requested_regime(controller),
        )
        apply_regime_runtime_profile(controller, regime_runtime_profile)
        bundle = controller.training_manager_execution_service.execute_manager_selection(
            controller,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            stock_data=stock_data,
        )
        dominant_output = self._bundle_dominant_output(bundle)

        trading_plan = bundle.portfolio_plan.to_trading_plan()
        selected_codes = [position.code for position in list(trading_plan.positions or [])]
        if not selected_codes:
            controller._mark_cycle_skipped(
                cycle_id,
                cutoff_date,
                stage="selection",
                reason="多经理运行未产出可交易标的",
            )
            return None

        manager_results_payload = self._bundle_manager_results_payload(bundle)
        portfolio_plan_payload = self._bundle_portfolio_plan_payload(bundle)
        regime_result = self._bundle_regime_result(
            bundle,
            selected_codes=selected_codes,
            trading_plan=trading_plan,
        )
        resolved_regime = str(regime_result.get("regime") or "").strip().lower()
        if resolved_regime and (
            resolved_regime
            != str(regime_runtime_profile.get("regime") or "").strip().lower()
        ):
            regime_runtime_profile = build_regime_runtime_profile(
                controller,
                regime=resolved_regime,
                base_params=dict(regime_runtime_profile.get("base_params") or {}),
            )
            apply_regime_runtime_profile(controller, regime_runtime_profile)
        selection_trace = self._bundle_selection_trace(
            bundle,
            selected_codes=selected_codes,
            portfolio_plan_payload=portfolio_plan_payload,
            manager_results_payload=manager_results_payload,
        )
        selected_data = {
            code: stock_data[code]
            for code in selected_codes
            if code in stock_data
        }
        if not selected_data:
            controller._mark_cycle_skipped(
                cycle_id,
                cutoff_date,
                stage="selection",
                reason="多经理选股结果在数据集中不可用",
            )
            return None

        _call_training_module(
            "observability",
            "record_selection_boundary_effects",
            controller,
            cycle_id=cycle_id,
            selected_codes=selected_codes,
            selection_trace=selection_trace,
            active_manager_count=len(bundle.portfolio_plan.active_manager_ids),
        )

        compatibility_projection = project_manager_compatibility(
            controller,
            manager_output=dominant_output,
            portfolio_plan=portfolio_plan_payload,
            manager_results=manager_results_payload,
            execution_snapshot={
                "active_runtime_config_ref": str(
                    getattr(dominant_output, "manager_config_ref", "") or ""
                ),
                "manager_config_ref": str(
                    getattr(dominant_output, "manager_config_ref", "") or ""
                ),
            },
            dominant_manager_id_hint=str(bundle.dominant_manager_id or ""),
        )
        compatibility_fields = build_manager_compatibility_fields(
            compatibility_projection,
            derived=True,
            source="dominant_manager",
            field_role="derived_compatibility",
        )

        run_context = getattr(bundle, "run_context", None)
        run_context_metadata = dict(getattr(run_context, "metadata", {}) or {}) if run_context is not None else {}
        subject_type = str(run_context_metadata.get("subject_type") or "").strip()
        resolved_selection_mode = "single_manager" if subject_type == "single_manager" else "manager_portfolio"
        return TrainingSelectionResult(
            regime_result=regime_result,
            selected_codes=selected_codes,
            selected_data=selected_data,
            selection_mode=resolved_selection_mode,
            agent_used=False,
            manager_bundle=bundle,
            manager_results=manager_results_payload,
            portfolio_plan=portfolio_plan_payload,
            dominant_manager_id=str(bundle.dominant_manager_id or ""),
            selection_trace=selection_trace,
            compatibility_fields=compatibility_fields,
            regime_runtime_profile=regime_runtime_profile,
            selection_intercepts={},
        )

class TrainingSimulationService:
    """Owns simulation bootstrap, date resolution, and evaluation payload assembly."""

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    @staticmethod
    def _safe_optional_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return number

    @staticmethod
    def _frame_last_text(frame: Any, column: str, default: str) -> str:
        if column not in frame.columns or frame.empty:
            return default
        return str(frame[column].iloc[-1])

    @staticmethod
    def _frame_last_float(frame: Any, column: str, default: float = 0.0) -> float:
        if column not in frame.columns:
            return default
        series = frame[column].dropna()
        if series.empty:
            return default
        try:
            return float(series.iloc[-1])
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _resolve_trader_execution_policy(controller: Any) -> dict[str, Any]:
        execution_policy = dict(getattr(controller, "execution_policy", {}) or {})
        return {
            "initial_capital": float(
                execution_policy.get(
                    "initial_capital",
                    getattr(config, "initial_capital", COMMON_EXECUTION_DEFAULTS["initial_capital"]),
                )
                or getattr(config, "initial_capital", COMMON_EXECUTION_DEFAULTS["initial_capital"])
            ),
            "commission_rate": float(
                execution_policy.get(
                    "commission_rate",
                    COMMON_EXECUTION_DEFAULTS["commission_rate"],
                )
                or COMMON_EXECUTION_DEFAULTS["commission_rate"]
            ),
            "stamp_tax_rate": float(
                execution_policy.get(
                    "stamp_tax_rate",
                    COMMON_EXECUTION_DEFAULTS["stamp_tax_rate"],
                )
                or COMMON_EXECUTION_DEFAULTS["stamp_tax_rate"]
            ),
            "slippage_rate": float(
                execution_policy.get(
                    "slippage_rate",
                    COMMON_EXECUTION_DEFAULTS["slippage_rate"],
                )
                or COMMON_EXECUTION_DEFAULTS["slippage_rate"]
            ),
        }

    @staticmethod
    def _build_trade_action(trade: Any) -> str:
        action = getattr(trade, "action", "")
        action_value = getattr(action, "value", action)
        return str(action_value)

    def _build_trade_core_payload(self, trade: Any) -> dict[str, Any]:
        return {
            "date": getattr(trade, "date", ""),
            "action": self._build_trade_action(trade),
            "ts_code": getattr(trade, "ts_code", ""),
            "price": self._safe_float(getattr(trade, "price", 0.0)),
            "shares": self._safe_int(getattr(trade, "shares", 0)),
            "pnl": self._safe_float(getattr(trade, "pnl", 0.0)),
            "pnl_pct": self._safe_float(getattr(trade, "pnl_pct", 0.0)),
            "reason": getattr(trade, "reason", ""),
            "source": getattr(trade, "source", ""),
            "entry_reason": getattr(trade, "entry_reason", ""),
            "exit_reason": getattr(trade, "exit_reason", ""),
            "exit_trigger": getattr(trade, "exit_trigger", ""),
            "entry_date": getattr(trade, "entry_date", ""),
            "entry_price": self._safe_float(getattr(trade, "entry_price", 0.0)),
            "holding_days": self._safe_int(getattr(trade, "holding_days", 0)),
        }

    def _build_trade_price_context(self, trade: Any) -> dict[str, Any]:
        return {
            "stop_loss_price": self._safe_float(getattr(trade, "stop_loss_price", 0.0)),
            "take_profit_price": self._safe_float(getattr(trade, "take_profit_price", 0.0)),
            "trailing_pct": self._safe_optional_float(getattr(trade, "trailing_pct", None)),
            "capital_before": self._safe_float(getattr(trade, "capital_before", 0.0)),
            "capital_after": self._safe_float(getattr(trade, "capital_after", 0.0)),
        }

    def _build_trade_market_context(self, trade: Any) -> dict[str, Any]:
        return {
            "open_price": self._safe_optional_float(getattr(trade, "open_price", 0.0)),
            "high_price": self._safe_optional_float(getattr(trade, "high_price", 0.0)),
            "low_price": self._safe_optional_float(getattr(trade, "low_price", 0.0)),
            "volume": self._safe_optional_float(getattr(trade, "volume", None)),
            "amount": self._safe_optional_float(getattr(trade, "amount", None)),
            "pct_chg": self._safe_optional_float(getattr(trade, "pct_chg", 0.0)),
        }

    def _build_trade_payload(self, trade: Any) -> dict[str, Any]:
        return {
            **self._build_trade_core_payload(trade),
            **self._build_trade_price_context(trade),
            **self._build_trade_market_context(trade),
        }

    def build_trader(
        self,
        controller: Any,
        *,
        selected_data: dict[str, Any],
        trading_plan: Any,
    ) -> SimulatedTrader:
        current_params = session_current_params(controller)
        execution_policy = self._resolve_trader_execution_policy(controller)
        trader = SimulatedTrader(
            initial_capital=execution_policy["initial_capital"],
            max_positions=trading_plan.max_positions or len(selected_data),
            position_size_pct=current_params.get(
                "position_size",
                COMMON_PARAM_DEFAULTS["position_size"],
            ),
            commission_rate=execution_policy["commission_rate"],
            stamp_tax_rate=execution_policy["stamp_tax_rate"],
            slippage_rate=execution_policy["slippage_rate"],
            risk_policy=controller.risk_policy,
        )
        trader.set_stock_data(selected_data)
        trader.set_stock_info(self._build_stock_info(selected_data))
        trader.set_trading_plan(trading_plan)
        return trader

    def resolve_trading_dates(
        self,
        *,
        selected_data: dict[str, Any],
        cutoff_date: str,
        simulation_days: int,
    ) -> list[str]:
        all_dates: set[str] = set()
        for frame in selected_data.values():
            date_col = "trade_date" if "trade_date" in frame.columns else "date"
            if date_col not in frame.columns:
                continue
            all_dates.update(frame[date_col].apply(normalize_date).tolist())
        dates_after = sorted(date for date in all_dates if date > cutoff_date)
        return dates_after[:simulation_days]

    def build_benchmark_context(
        self,
        controller: Any,
        *,
        cutoff_date: str,
        trading_dates: list[str],
    ) -> tuple[list[float], Any]:
        benchmark_daily_values = controller.data_manager.get_benchmark_daily_values(
            trading_dates,
            index_code="sh.000300",
        )
        market_index_start = (
            datetime.strptime(cutoff_date, "%Y%m%d") - timedelta(days=180)
        ).strftime("%Y%m%d")
        market_index_frame = controller.data_manager.get_market_index_frame(
            index_code="sh.000300",
            start_date=market_index_start,
            end_date=trading_dates[-1] if trading_dates else cutoff_date,
        )
        return benchmark_daily_values, market_index_frame

    def build_trade_dicts(self, sim_result: Any) -> list[dict[str, Any]]:
        return [
            self._build_trade_payload(trade)
            for trade in list(getattr(sim_result, "trade_history", []) or [])
        ]

    def build_cycle_payload_projection(
        self,
        *,
        cycle_id: int,
        cutoff_date: str,
        sim_result: Any,
        selected: list[str],
        is_profit: bool,
        regime_result: dict[str, Any],
        governance_decision: GovernanceDecisionInputPayload | dict[str, Any] | None = None,
        trading_plan: Any,
        data_mode: str,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        selection_mode: str,
        agent_used: bool,
        llm_used: bool,
    ) -> dict[str, Any]:
        """Build the simulation-stage compatibility payload exported at the boundary."""
        resolved_governance_decision = normalize_governance_decision(dict(governance_decision or {}))
        return {
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "return_pct": sim_result.return_pct,
            "profit_loss": sim_result.total_pnl,
            "total_trades": sim_result.total_trades,
            "winning_trades": sim_result.winning_trades,
            "losing_trades": sim_result.losing_trades,
            "win_rate": sim_result.win_rate,
            "selected_stocks": selected,
            "is_profit": is_profit,
            "regime": regime_result.get("regime", "unknown"),
            "governance_decision": resolved_governance_decision,
            "plan_source": trading_plan.source,
            "data_mode": data_mode,
            "requested_data_mode": requested_data_mode,
            "effective_data_mode": effective_data_mode,
            "llm_mode": llm_mode,
            "degraded": degraded,
            "degrade_reason": degrade_reason,
            "selection_mode": selection_mode,
            "agent_used": agent_used,
            "llm_used": llm_used,
            "initial_capital": sim_result.initial_capital,
            "final_value": sim_result.final_value,
        }

    def build_cycle_dict(
        self,
        *,
        cycle_id: int,
        cutoff_date: str,
        sim_result: Any,
        selected: list[str],
        is_profit: bool,
        regime_result: dict[str, Any],
        governance_decision: GovernanceDecisionInputPayload | dict[str, Any] | None = None,
        trading_plan: Any,
        data_mode: str,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        selection_mode: str,
        agent_used: bool,
        llm_used: bool,
    ) -> dict[str, Any]:
        """Compat-only alias for legacy callers that still expect cycle_dict naming."""
        return self.build_cycle_payload_projection(
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            sim_result=sim_result,
            selected=selected,
            is_profit=is_profit,
            regime_result=regime_result,
            governance_decision=governance_decision,
            trading_plan=trading_plan,
            data_mode=data_mode,
            requested_data_mode=requested_data_mode,
            effective_data_mode=effective_data_mode,
            llm_mode=llm_mode,
            degraded=degraded,
            degrade_reason=degrade_reason,
            selection_mode=selection_mode,
            agent_used=agent_used,
            llm_used=llm_used,
        )

    def evaluate_cycle(
        self,
        controller: Any,
        *,
        cycle_payload: dict[str, Any],
        trade_dicts: list[dict[str, Any]],
        sim_result: Any,
        benchmark_daily_values: list[float],
    ) -> bool:
        evaluation_summary = self.evaluate_cycle_summary(
            controller,
            cycle_payload=cycle_payload,
            trade_dicts=trade_dicts,
            sim_result=sim_result,
            benchmark_daily_values=benchmark_daily_values,
        )
        cycle_payload.update(evaluation_summary)
        return bool(evaluation_summary["benchmark_passed"])

    def evaluate_cycle_summary(
        self,
        controller: Any,
        *,
        cycle_payload: dict[str, Any],
        trade_dicts: list[dict[str, Any]],
        sim_result: Any,
        benchmark_daily_values: list[float],
    ) -> dict[str, Any]:
        evaluation_payload = dict(cycle_payload or {})
        benchmark_summary = self._build_benchmark_summary(
            controller,
            sim_result=sim_result,
            trade_dicts=trade_dicts,
            benchmark_daily_values=benchmark_daily_values,
        )
        strategy_eval = controller.strategy_evaluator.evaluate(
            {
                **evaluation_payload,
                **benchmark_summary,
            },
            trade_dicts,
            sim_result.daily_records,
        )
        return {
            **benchmark_summary,
            "strategy_scores": self._build_strategy_scores_payload(strategy_eval),
        }

    @staticmethod
    def _collect_simulation_daily_values(sim_result: Any) -> list[float]:
        return [
            float(row.get("total_value") or 0.0)
            for row in list(getattr(sim_result, "daily_records", []) or [])
            if isinstance(row, dict) and row.get("total_value") is not None
        ]

    def _build_benchmark_summary(
        self,
        controller: Any,
        *,
        sim_result: Any,
        trade_dicts: list[dict[str, Any]],
        benchmark_daily_values: list[float],
    ) -> dict[str, Any]:
        daily_values = self._collect_simulation_daily_values(sim_result)
        summary = {
            "benchmark_passed": False,
            "benchmark_strict_passed": False,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
            "excess_return": 0.0,
            "benchmark_return": 0.0,
            "benchmark_source": "none",
        }
        if len(daily_values) < 2:
            return summary
        aligned_benchmark = (
            benchmark_daily_values
            if len(benchmark_daily_values) == len(daily_values)
            else None
        )
        benchmark_metrics = controller.benchmark_evaluator.evaluate(
            daily_values=daily_values,
            benchmark_daily_values=aligned_benchmark,
            trade_history=trade_dicts,
        )
        summary.update(
            {
                "benchmark_passed": bool(benchmark_metrics.passed),
                "benchmark_strict_passed": bool(benchmark_metrics.passed),
                "sharpe_ratio": float(benchmark_metrics.sharpe_ratio),
                "max_drawdown": float(benchmark_metrics.max_drawdown),
                "excess_return": float(benchmark_metrics.excess_return),
                "benchmark_return": float(benchmark_metrics.benchmark_return),
                "benchmark_source": "index_bar:sh.000300" if aligned_benchmark else "none",
            }
        )
        return summary

    @staticmethod
    def _build_strategy_scores_payload(strategy_eval: Any) -> StrategyScoresPayload:
        return cast(
            StrategyScoresPayload,
            {
            "signal_accuracy": float(strategy_eval.signal_accuracy),
            "timing_score": float(strategy_eval.timing_score),
            "risk_control_score": float(strategy_eval.risk_control_score),
            "overall_score": float(strategy_eval.overall_score),
            },
        )

    def _build_stock_info(self, selected_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            code: {
                "name": self._frame_last_text(frame, "name", code),
                "industry": self._frame_last_text(frame, "industry", "其他"),
                "market_cap": self._frame_last_float(frame, "market_cap"),
                "roe": self._frame_last_float(frame, "roe"),
            }
            for code, frame in selected_data.items()
        }

def _population_size(controller: Any) -> int:
    service = _resolve_evolution_service(controller)
    if service is None:
        return 0
    try:
        return int(getattr(service, "population_size"))
    except Exception:
        return 0


def _resolve_evolution_service(controller: Any) -> EvolutionService | Any | None:
    service = getattr(controller, "evolution_service", None)
    if service is not None:
        return service
    engine = getattr(controller, "evolution_engine", None)
    if engine is None:
        return None
    return EvolutionService(engine=engine)

def _benchmark_oriented_fitness(result: Any) -> float:
    base_return = max(min(float(getattr(result, "return_pct", 0.0) or 0.0), 50.0), -50.0)
    strategy_scores = dict(getattr(result, "strategy_scores", {}) or {})
    overall_score = max(0.0, min(1.0, float(strategy_scores.get("overall_score", 0.0) or 0.0)))
    benchmark_bonus = 2.5 if bool(getattr(result, "benchmark_passed", False)) else -2.5
    return round(base_return + overall_score * 3.0 + benchmark_bonus, 4)


def _project_optimization_manager_scope(
    controller: Any,
    optimization_input: OptimizationInputEnvelope,
) -> Any:
    simulation = optimization_input.simulation
    execution_snapshot = dict(simulation.execution_snapshot or {})
    return project_manager_compatibility(
        controller,
        governance_decision=dict(simulation.governance_decision or {}),
        portfolio_plan=dict(execution_snapshot.get("portfolio_plan") or {}),
        manager_results=list(execution_snapshot.get("manager_results") or []),
        execution_snapshot=execution_snapshot,
        dominant_manager_id_hint=str(execution_snapshot.get("dominant_manager_id") or ""),
    )


def _trade_win_rate(trade_dicts: list[dict[str, Any]]) -> float:
    total = len(trade_dicts)
    if total <= 0:
        return 0.0
    winning_trades = sum(1 for item in trade_dicts if float(item.get("pnl") or 0.0) > 0.0)
    return round(winning_trades / total, 4)


def _build_loss_analysis_payload(
    optimization_input: OptimizationInputEnvelope,
    trade_dicts: list[dict[str, Any]],
) -> dict[str, Any]:
    simulation = optimization_input.simulation
    return {
        "cycle_id": int(simulation.cycle_id),
        "cutoff_date": str(simulation.cutoff_date or ""),
        "regime": str(simulation.regime or "unknown"),
        "selected_stocks": list(simulation.selected_stocks or []),
        "return_pct": float(simulation.return_pct or 0.0),
        "total_trades": len(trade_dicts),
        "win_rate": _trade_win_rate(trade_dicts),
        "benchmark_passed": bool(simulation.benchmark_passed),
        "benchmark_strict_passed": bool(simulation.benchmark_strict_passed),
        "sharpe_ratio": float(simulation.sharpe_ratio or 0.0),
        "max_drawdown": float(simulation.max_drawdown or 0.0),
        "excess_return": float(simulation.excess_return or 0.0),
        "strategy_scores": dict(simulation.strategy_scores or {}),
        "governance_decision": dict(simulation.governance_decision or {}),
        "execution_snapshot": dict(simulation.execution_snapshot or {}),
        "research_feedback": dict(optimization_input.research_feedback or {}),
        "research_feedback_optimization": dict(
            optimization_input.research_feedback_optimization or {}
        ),
    }


def _apply_feedback_optimization_step(
    controller: Any,
    *,
    boundary_context: Any,
    trigger_reason: str,
    feedback_plan: dict[str, Any] | None,
    event_factory: Callable[..., Any],
) -> tuple[Any | None, dict[str, Any], dict[str, Any]]:
    if not feedback_plan:
        return None, {}, {}
    feedback_adjustments = dict(feedback_plan.get("param_adjustments") or {})
    feedback_scoring = dict(feedback_plan.get("scoring_adjustments") or {})
    applied_adjustments = (
        apply_runtime_adjustments_boundary(controller, feedback_adjustments)
        if feedback_adjustments
        else {}
    )
    feedback_event = build_feedback_optimization_event(
        context=boundary_context,
        trigger_reason=trigger_reason,
        feedback_plan=feedback_plan,
        feedback_adjustments=feedback_adjustments,
        feedback_scoring=feedback_scoring,
        event_factory=event_factory,
    )
    return feedback_event, applied_adjustments, feedback_scoring


def _apply_llm_optimization_step(
    controller: Any,
    *,
    boundary_context: Any,
    optimization_input: OptimizationInputEnvelope,
    trade_dicts: list[dict[str, Any]],
    event_factory: Callable[..., Any],
) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    analysis_payload = _build_loss_analysis_payload(optimization_input, trade_dicts)
    analysis = controller.llm_optimizer.analyze_loss(analysis_payload, trade_dicts)
    logger.info("LLM 分析: %s", analysis.cause)
    logger.info("建议: %s", analysis.suggestions)
    llm_event = build_llm_optimization_event(
        context=boundary_context,
        trade_dicts=trade_dicts,
        consecutive_losses=session_consecutive_losses(controller),
        analysis=analysis,
        event_factory=event_factory,
    )

    adjustments = controller.llm_optimizer.generate_runtime_fix(analysis) or {}
    applied_adjustments = (
        apply_runtime_adjustments_boundary(controller, adjustments)
        if adjustments
        else {}
    )
    scoring_adjustments = (
        derive_scoring_adjustments(str(boundary_context.manager_id or ""), analysis)
        if adjustments
        else {}
    )
    if applied_adjustments:
        llm_event.applied_change = dict(applied_adjustments)
        logger.info("参数已更新: %s", session_current_params(controller))
    return analysis, llm_event, applied_adjustments, scoring_adjustments


def _apply_evolution_optimization_step(
    controller: Any,
    *,
    boundary_context: Any,
    event_factory: Callable[..., Any],
) -> tuple[Any | None, dict[str, Any], list[float]]:
    cycle_history = session_cycle_history(controller)
    if len(cycle_history) < 3:
        return None, {}, []

    fitness_scores = [
        _benchmark_oriented_fitness(result)
        for result in cycle_history[-10:]
    ]
    evolution_service = _resolve_evolution_service(controller)
    if evolution_service is None:
        raise RuntimeError("evolution runtime is unavailable")
    if _population_size(controller) == 0:
        evolution_service.initialize_population(session_current_params(controller))
    pop_size = _population_size(controller)
    if len(fitness_scores) > pop_size:
        fitness_scores = fitness_scores[-pop_size:]
    elif len(fitness_scores) < pop_size:
        fitness_scores = fitness_scores + [0.0] * (pop_size - len(fitness_scores))

    evolution_service.evolve(fitness_scores)
    best_params = dict(evolution_service.get_best_params() or {})
    evo_event = build_evolution_optimization_event(
        context=boundary_context,
        fitness_scores=list(fitness_scores),
        best_params=best_params,
        population_size=_population_size(controller),
        event_factory=event_factory,
    )
    applied_adjustments = (
        apply_runtime_adjustments_boundary(controller, best_params)
        if best_params
        else {}
    )
    if applied_adjustments:
        logger.info("遗传算法优化参数: %s", best_params)
    return evo_event, applied_adjustments, list(fitness_scores)


def _finalize_runtime_mutation_step(
    controller: Any,
    *,
    boundary_context: Any,
    cycle_id: int | None,
    trigger_reason: str,
    active_runtime_config_ref: str,
    config_adjustments: dict[str, Any],
    scoring_adjustments: dict[str, Any],
    feedback_plan: dict[str, Any] | None,
    event_factory: Callable[..., Any],
) -> Any | None:
    if not config_adjustments:
        return None
    return build_runtime_mutation_boundary(
        controller,
        context=boundary_context,
        cycle_id=cycle_id,
        trigger_reason=trigger_reason,
        active_runtime_config_ref=active_runtime_config_ref,
        config_adjustments=dict(config_adjustments),
        scoring_adjustments=dict(scoring_adjustments),
        feedback_plan=feedback_plan,
        event_factory=event_factory,
    )


def trigger_loss_optimization(
    controller: Any,
    optimization_input: OptimizationInputEnvelope,
    trade_dicts: list[dict[str, Any]],
    *,
    event_factory: Callable[..., Any],
    trigger_reason: str = 'consecutive_losses',
    feedback_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cycle_id = optimization_input.cycle_id
    runtime_projection = _project_optimization_manager_scope(controller, optimization_input)
    manager_id = str(runtime_projection.manager_id or '')
    active_runtime_config_ref = normalize_config_ref(
        runtime_projection.active_runtime_config_ref
        or runtime_projection.manager_config_ref
        or ''
    )
    fitness_source_cycles = [
        int(getattr(item, 'cycle_id'))
        for item in list(session_cycle_history(controller) or [])[-10:]
        if getattr(item, 'cycle_id', None) is not None
    ]
    boundary_context = _new_optimization_boundary_context(
        cycle_id=int(cycle_id) if cycle_id is not None else None,
        manager_id=manager_id,
        active_runtime_config_ref=active_runtime_config_ref,
        fitness_source_cycles=fitness_source_cycles,
    )

    if trigger_reason == 'research_feedback':
        opening_message = 'ask 侧校准反馈触发自我优化...'
        opening_details = {
            'bias': dict((feedback_plan or {}).get('recommendation') or {}).get('bias'),
            'sample_count': int((feedback_plan or {}).get('sample_count') or 0),
        }
    else:
        consecutive_losses = session_consecutive_losses(controller)
        opening_message = f"连续 {consecutive_losses} 次亏损，触发自我优化..."
        opening_details = {'consecutive_losses': consecutive_losses}

    logger.info('⚠️ %s', opening_message)
    emit_optimization_start_boundary(
        controller,
        cycle_id=cycle_id,
        opening_message=opening_message,
        opening_details=opening_details,
    )
    events: list[dict[str, Any]] = []
    config_adjustments: dict[str, Any] = {}
    scoring_adjustments: dict[str, Any] = {}

    try:
        feedback_event, feedback_adjustments, feedback_scoring = _apply_feedback_optimization_step(
            controller,
            boundary_context=boundary_context,
            trigger_reason=trigger_reason,
            feedback_plan=feedback_plan,
            event_factory=event_factory,
        )
        if feedback_event is not None:
            config_adjustments.update(feedback_adjustments)
            scoring_adjustments.update(feedback_scoring)
            events.append(feedback_event.to_dict())
            record_feedback_optimization_boundary_effects(
                controller,
                cycle_id=cycle_id,
                feedback_plan=feedback_plan,
                feedback_event=feedback_event,
            )

        if trigger_reason == 'consecutive_losses':
            analysis, llm_event, llm_adjustments, llm_scoring_adjustments = _apply_llm_optimization_step(
                controller,
                boundary_context=boundary_context,
                optimization_input=optimization_input,
                trade_dicts=trade_dicts,
                event_factory=event_factory,
            )
            config_adjustments.update(llm_adjustments)
            scoring_adjustments.update(llm_scoring_adjustments)
            events.append(llm_event.to_dict())
            record_llm_optimization_boundary_effects(
                controller,
                cycle_id=cycle_id,
                llm_event=llm_event,
                analysis=analysis,
                adjustments=llm_adjustments,
            )

            evo_event, evo_adjustments, fitness_scores = _apply_evolution_optimization_step(
                controller,
                boundary_context=boundary_context,
                event_factory=event_factory,
            )
            if evo_event is not None:
                config_adjustments.update(evo_adjustments)
                events.append(evo_event.to_dict())
                record_evolution_optimization_boundary_effects(
                    controller,
                    cycle_id=cycle_id,
                    evo_event=evo_event,
                    best_params=evo_adjustments,
                    fitness_scores=fitness_scores,
                )

        mutation_boundary = _finalize_runtime_mutation_step(
            controller,
            boundary_context=boundary_context,
            cycle_id=cycle_id,
            trigger_reason=trigger_reason,
            active_runtime_config_ref=active_runtime_config_ref,
            config_adjustments=config_adjustments,
            scoring_adjustments=scoring_adjustments,
            feedback_plan=feedback_plan,
            event_factory=event_factory,
        )
        if mutation_boundary is not None:
            events.append(mutation_boundary.mutation_event.to_dict())
            record_runtime_mutation_boundary_effects(
                controller,
                cycle_id=cycle_id,
                mutation_event=mutation_boundary.mutation_event,
                mutation_log_message=mutation_boundary.mutation_log_message,
                adjustment_count=len(config_adjustments),
                auto_apply_runtime_config_ref=mutation_boundary.auto_apply_runtime_config_ref,
            )

    except Exception as exc:
        err_event = build_optimization_error_event(
            context=boundary_context,
            trigger_reason=trigger_reason,
            exc=exc,
            event_factory=event_factory,
        )
        events.append(err_event.to_dict())
        emit_optimization_error_boundary(
            controller,
            cycle_id=cycle_id,
            err_event=err_event,
            exc=exc,
        )
        logger.error('优化过程出错: %s', exc)

    if trigger_reason == 'consecutive_losses':
        set_session_consecutive_losses(controller, 0)
    logger.info('✅ 优化完成，继续训练...')
    emit_optimization_completed_boundary(
        controller,
        cycle_id=cycle_id,
        event_count=len(events),
        trigger_reason=trigger_reason,
    )

    if controller.on_optimize:
        controller.on_optimize(session_current_params(controller))
    return events


_latest_runtime_config_mutation_event = cast(
    Callable[[list[dict[str, Any]] | None], dict[str, Any]],
    _training_module_proxy("observability", "_latest_runtime_config_mutation_event"),
)
_candidate_runtime_config_meta_ref = cast(
    Callable[[str], str],
    _training_module_proxy("observability", "_candidate_runtime_config_meta_ref"),
)


def build_promotion_record(
    *,
    cycle_id: int,
    run_context: RunContextPayload | dict[str, Any],
    optimization_events: list[dict[str, Any]] | None = None,
) -> PromotionRecordPayload:
    return _call_training_module(
        "observability",
        "build_promotion_record",
        cycle_id=cycle_id,
        run_context=cast(dict[str, Any], run_context),
        optimization_events=optimization_events,
    )


def build_lineage_record(
    controller: Any,
    *,
    cycle_id: int,
    manager_output: Any | None,
    run_context: RunContextPayload | dict[str, Any],
    optimization_events: list[dict[str, Any]] | None = None,
) -> LineageRecordPayload:
    return _call_training_module(
        "observability",
        "build_lineage_record",
        controller,
        cycle_id=cycle_id,
        manager_output=manager_output,
        run_context=cast(dict[str, Any], run_context),
        optimization_events=optimization_events,
    )



class TrainingOutcomeService:
    """Builds cycle audit metadata and training result payloads."""

    @staticmethod
    def _finite_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def build_realism_metrics(
        *,
        trade_dicts: list[dict[str, Any]],
        selection_mode: str,
        optimization_events: list[dict[str, Any]] | None = None,
    ) -> RealismMetricsPayload:
        trades = [dict(item) for item in list(trade_dicts or []) if isinstance(item, dict)]
        trade_amounts = [
            amount
            for amount in (
                TrainingOutcomeService._finite_float(item.get("amount"))
                for item in trades
            )
            if amount is not None
        ]
        avg_trade_amount = (
            sum(trade_amounts) / len(trade_amounts)
            if trade_amounts
            else 0.0
        )
        turnover_values = [
            turnover_rate
            for turnover_rate in (
                TrainingOutcomeService._finite_float(item.get("turnover_rate"))
                for item in trades
            )
            if turnover_rate is not None
        ]
        holding_days = [
            int(item.get("holding_days", 0) or 0)
            for item in trades
            if int(item.get("holding_days", 0) or 0) > 0
        ]
        source_counts: dict[str, int] = {}
        exit_trigger_counts: dict[str, int] = {}
        for item in trades:
            source = str(item.get("source") or "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1
            trigger = str(item.get("exit_trigger") or "")
            if trigger:
                exit_trigger_counts[trigger] = exit_trigger_counts.get(trigger, 0) + 1
        total_trades = len(trades) or 1
        return cast(RealismMetricsPayload, {
            "trade_record_count": len(trades),
            "selection_mode": str(selection_mode or ""),
            "optimization_event_count": len(list(optimization_events or [])),
            "avg_trade_amount": round(avg_trade_amount, 2),
            "avg_turnover_rate": round(
                (sum(turnover_values) / len(turnover_values)) if turnover_values else 0.0,
                4,
            ),
            "high_turnover_trade_count": sum(1 for value in turnover_values if value >= 10.0),
            "avg_holding_days": round(
                (sum(holding_days) / len(holding_days)) if holding_days else 0.0,
                2,
            ),
            "source_mix": {
                key: round(value / total_trades, 4)
                for key, value in sorted(source_counts.items())
            },
            "exit_trigger_mix": {
                key: round(value / total_trades, 4)
                for key, value in sorted(exit_trigger_counts.items())
            },
        })

    def build_audit_tags(
        self,
        controller: Any,
        *,
        data_mode: str,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        selection_mode: str,
        agent_used: bool,
        llm_used: bool,
        benchmark_passed: bool,
        review_applied: bool,
        regime_result: dict[str, Any],
    ) -> dict[str, Any]:
        effective_runtime_mode = str(
            getattr(controller, "effective_runtime_mode", EFFECTIVE_RUNTIME_MODE)
            or EFFECTIVE_RUNTIME_MODE
        ).strip()
        governance_decision = governance_from_controller(controller)
        normalized_selection_mode = str(selection_mode or "").strip()
        if normalized_selection_mode == "single_manager":
            resolved_subject_type = "single_manager"
        elif normalized_selection_mode == "manager_portfolio":
            resolved_subject_type = "manager_portfolio"
        else:
            # Legacy selection modes keep the historical effective-runtime fallback.
            resolved_subject_type = (
                "manager_portfolio"
                if effective_runtime_mode == EFFECTIVE_RUNTIME_MODE
                else "single_manager"
            )
        return {
            "data_mode": data_mode,
            "requested_data_mode": requested_data_mode,
            "effective_data_mode": effective_data_mode,
            "llm_mode": llm_mode,
            "degraded": degraded,
            "degrade_reason": degrade_reason,
            "selection_mode": selection_mode,
            "meeting_fallback": False,
            "agent_used": agent_used,
            "llm_used": llm_used,
            "mock_data_used": data_mode == "mock",
            "benchmark_passed": benchmark_passed,
            "review_applied": review_applied,
            "governance_enabled": controller.governance_enabled,
            "governance_mode": controller.governance_mode,
            "governance_dominant_manager": dominant_manager_id(governance_decision),
            "governance_regime": str(governance_decision.get("regime") or regime_result.get("regime", "unknown")),
            "subject_type": resolved_subject_type,
            "dual_review_enabled": bool(getattr(controller, "dual_review_enabled", False)),
        }

    @staticmethod
    def _manager_results_payload(manager_results: list[Any] | None = None) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for item in list(manager_results or []):
            if hasattr(item, "to_dict"):
                payloads.append(dict(item.to_dict()))
            elif isinstance(item, dict):
                payloads.append(dict(item))
        return payloads

    @staticmethod
    def _portfolio_payload(portfolio_plan: Any | None = None) -> dict[str, Any]:
        if portfolio_plan is None:
            return {}
        if hasattr(portfolio_plan, "to_dict"):
            return dict(portfolio_plan.to_dict())
        if isinstance(portfolio_plan, dict):
            return dict(portfolio_plan)
        return {}

    @staticmethod
    def _derive_portfolio_attribution(
        portfolio_plan: dict[str, Any],
        portfolio_attribution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        explicit = dict(portfolio_attribution or {})
        if explicit:
            return explicit
        return {
            str(position.get("code") or ""): float(position.get("target_weight") or 0.0)
            for position in list(portfolio_plan.get("positions") or [])
            if str(position.get("code") or "").strip()
        }

    @staticmethod
    def _build_run_context_evaluation_context(
        *,
        ab_comparison: dict[str, Any] | None,
        research_feedback: dict[str, Any] | None,
        portfolio_attribution: dict[str, Any],
        manager_review_report: ManagerReviewDigestPayload | dict[str, Any] | None,
        allocation_review_report: AllocationReviewDigestPayload | dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "ab_comparison": dict(ab_comparison or {}),
            "research_feedback": dict(research_feedback or {}),
            "portfolio_attribution": dict(portfolio_attribution or {}),
            "manager_review_report": dict(manager_review_report or {}),
            "allocation_review_report": dict(allocation_review_report or {}),
        }

    @staticmethod
    def _build_outcome_stage_snapshots(
        *,
        review_envelope: ReviewStageEnvelope,
        cycle_id: int,
        execution_snapshot: dict[str, Any],
        run_context: dict[str, Any],
        promotion_record: PromotionRecordPayload | dict[str, Any],
        lineage_record: LineageRecordPayload | dict[str, Any],
        realism_metrics: RealismMetricsPayload,
    ) -> StageSnapshotsInputPayload:
        snapshots = deepcopy(dict(review_envelope.stage_snapshots or {}))
        snapshots["outcome"] = build_outcome_stage_snapshot(
            cycle_id=cycle_id,
            execution_snapshot=cast(dict[str, Any], execution_snapshot),
            run_context=cast(dict[str, Any], run_context),
            promotion_record=promotion_record,
            lineage_record=lineage_record,
            realism_metrics=realism_metrics,
        )
        return cast(StageSnapshotsInputPayload, snapshots)

    @staticmethod
    def _resolve_outcome_boundary_projection(
        controller: Any,
        *,
        cycle_id: int,
        resolved_cycle_payload: dict[str, Any],
        simulation_envelope: SimulationStageEnvelope,
        manager_output: Any | None,
        selection_mode: str,
        benchmark_passed: bool,
        manager_results_payload: list[dict[str, Any]],
        portfolio_payload: dict[str, Any],
        dominant_manager_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        outcome_boundary = _call_training_module(
            "observability",
            "build_outcome_execution_boundary_projection",
            controller,
            cycle_id=cycle_id,
            cycle_payload=resolved_cycle_payload,
            execution_snapshot=dict(simulation_envelope.execution_snapshot or {}),
            governance_decision=dict(simulation_envelope.governance_decision or {}),
            manager_output=manager_output,
            selection_mode=selection_mode,
            benchmark_passed=benchmark_passed,
            manager_results_payload=manager_results_payload,
            portfolio_payload=portfolio_payload,
            dominant_manager_id=dominant_manager_id,
        )
        return (
            dict(outcome_boundary.execution_snapshot or {}),
            dict(outcome_boundary.governance_decision or {}),
            dict(outcome_boundary.execution_defaults or {}),
            dict(outcome_boundary.compatibility_fields or {}),
        )

    def _build_outcome_cycle_artifacts(
        self,
        controller: Any,
        *,
        cycle_id: int,
        trade_dicts: list[dict[str, Any]],
        selection_mode: str,
        optimization_events: list[dict[str, Any]],
        manager_output: Any | None,
        execution_snapshot: dict[str, Any],
        review_envelope: ReviewStageEnvelope,
        ab_comparison: dict[str, Any] | None,
        research_feedback: dict[str, Any] | None,
        portfolio_attribution_payload: dict[str, Any],
        manager_review_report: ManagerReviewDigestPayload | dict[str, Any] | None,
        allocation_review_report: AllocationReviewDigestPayload | dict[str, Any] | None,
    ) -> tuple[
        RunContextPayload | dict[str, Any],
        PromotionRecordPayload | dict[str, Any],
        LineageRecordPayload | dict[str, Any],
        RealismMetricsPayload,
        StageSnapshotsInputPayload,
    ]:
        run_context = build_cycle_run_context(
            controller,
            cycle_id=cycle_id,
            manager_output=manager_output,
            optimization_events=optimization_events,
            execution_snapshot=execution_snapshot,
            evaluation_context=self._build_run_context_evaluation_context(
                ab_comparison=ab_comparison,
                research_feedback=research_feedback,
                portfolio_attribution=portfolio_attribution_payload,
                manager_review_report=manager_review_report,
                allocation_review_report=allocation_review_report,
            ),
        )
        promotion_record = build_promotion_record(
            cycle_id=cycle_id,
            run_context=cast(dict[str, Any], run_context),
            optimization_events=optimization_events,
        )
        lineage_record = build_lineage_record(
            controller,
            cycle_id=cycle_id,
            manager_output=manager_output,
            run_context=cast(dict[str, Any], run_context),
            optimization_events=optimization_events,
        )
        realism_metrics = self.build_realism_metrics(
            trade_dicts=trade_dicts,
            selection_mode=selection_mode,
            optimization_events=optimization_events,
        )
        stage_snapshots = self._build_outcome_stage_snapshots(
            review_envelope=review_envelope,
            cycle_id=cycle_id,
            execution_snapshot=cast(dict[str, Any], execution_snapshot),
            run_context=cast(dict[str, Any], run_context),
            promotion_record=promotion_record,
            lineage_record=lineage_record,
            realism_metrics=realism_metrics,
        )
        return (
            run_context,
            promotion_record,
            lineage_record,
            realism_metrics,
            stage_snapshots,
        )

    @staticmethod
    def _build_cycle_result_payload(
        *,
        cycle_id: int,
        cutoff_date: str,
        selected: list[str],
        sim_result: Any,
        is_profit: bool,
        trade_dicts: list[dict[str, Any]],
        execution_snapshot: dict[str, Any],
        review_envelope: ReviewStageEnvelope,
        data_mode: str,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        selection_mode: str,
        agent_used: bool,
        llm_used: bool,
        benchmark_passed: bool,
        simulation_envelope: SimulationStageEnvelope,
        review_applied: bool,
        config_snapshot_path: str,
        optimization_events: list[dict[str, Any]],
        audit_tags: dict[str, Any],
        execution_defaults: dict[str, Any],
        governance_decision: GovernanceDecisionInputPayload | dict[str, Any],
        research_feedback: dict[str, Any] | None,
        research_artifacts: dict[str, Any] | None,
        ab_comparison: dict[str, Any] | None,
        experiment_spec: dict[str, Any],
        run_context: dict[str, Any],
        promotion_record: PromotionRecordPayload | dict[str, Any],
        lineage_record: LineageRecordPayload | dict[str, Any],
        manager_results_payload: list[dict[str, Any]],
        portfolio_payload: dict[str, Any],
        portfolio_attribution_payload: dict[str, Any],
        manager_review_report: ManagerReviewDigestPayload | dict[str, Any] | None,
        allocation_review_report: AllocationReviewDigestPayload | dict[str, Any] | None,
        dominant_manager_id: str,
        compatibility_fields: dict[str, Any],
        realism_metrics: RealismMetricsPayload,
        stage_snapshots: StageSnapshotsInputPayload,
        validation_report: ValidationReportInputPayload | None,
        peer_comparison_report: dict[str, Any] | None,
        judge_report: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "selected_stocks": selected,
            "initial_capital": sim_result.initial_capital,
            "final_value": sim_result.final_value,
            "return_pct": sim_result.return_pct,
            "is_profit": is_profit,
            "trade_history": trade_dicts,
            "params": dict(execution_snapshot.get("runtime_overrides") or {}),
            "analysis": review_envelope.analysis,
            "data_mode": data_mode,
            "requested_data_mode": requested_data_mode,
            "effective_data_mode": effective_data_mode,
            "llm_mode": llm_mode,
            "degraded": degraded,
            "degrade_reason": degrade_reason,
            "selection_mode": selection_mode,
            "agent_used": agent_used,
            "llm_used": llm_used,
            "benchmark_passed": benchmark_passed,
            "strategy_scores": dict(simulation_envelope.strategy_scores or {}),
            "review_applied": review_applied,
            "config_snapshot_path": config_snapshot_path,
            "optimization_events": optimization_events,
            "audit_tags": audit_tags,
            "execution_defaults": execution_defaults,
            "governance_decision": dict(governance_decision),
            "research_feedback": dict(research_feedback or {}),
            "research_artifacts": dict(research_artifacts or {}),
            "ab_comparison": dict(ab_comparison or {}),
            "experiment_spec": experiment_spec,
            "execution_snapshot": execution_snapshot,
            "run_context": run_context,
            "promotion_record": promotion_record,
            "lineage_record": lineage_record,
            "manager_results": manager_results_payload,
            "portfolio_plan": portfolio_payload,
            "portfolio_attribution": portfolio_attribution_payload,
            "manager_review_report": dict(manager_review_report or {}),
            "allocation_review_report": dict(allocation_review_report or {}),
            "dominant_manager_id": str(dominant_manager_id or ""),
            "compatibility_fields": compatibility_fields,
            "review_decision": dict(review_envelope.review_decision or {}),
            "causal_diagnosis": dict(review_envelope.causal_diagnosis or {}),
            "similarity_summary": dict(review_envelope.similarity_summary or {}),
            "similar_results": deepcopy(list(review_envelope.similar_results or [])),
            "realism_metrics": realism_metrics,
            "stage_snapshots": dict(stage_snapshots or {}),
            "validation_report": dict(validation_report or {}),
            "validation_summary": dict((validation_report or {}).get("summary") or {}),
            "peer_comparison_report": dict(peer_comparison_report or {}),
            "judge_report": dict(judge_report or {}),
        }

    def build_cycle_result(
        self,
        controller: Any,
        *,
        result_factory: Any,
        cycle_id: int,
        cutoff_date: str,
        selected: list[str],
        sim_result: Any,
        is_profit: bool,
        trade_dicts: list[dict[str, Any]],
        data_mode: str,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        selection_mode: str,
        agent_used: bool,
        llm_used: bool,
        benchmark_passed: bool,
        cycle_payload: dict[str, Any] | None = None,
        simulation_envelope: SimulationStageEnvelope | None = None,
        review_envelope: ReviewStageEnvelope | None = None,
        review_applied: bool,
        config_snapshot_path: str,
        optimization_events: list[dict[str, Any]],
        audit_tags: dict[str, Any],
        manager_output: Any | None,
        research_feedback: dict[str, Any] | None,
        research_artifacts: dict[str, Any] | None = None,
        ab_comparison: dict[str, Any] | None = None,
        validation_report: ValidationReportInputPayload | None = None,
        peer_comparison_report: dict[str, Any] | None = None,
        judge_report: dict[str, Any] | None = None,
        stage_snapshots: StageSnapshotsInputPayload | None = None,
        manager_results: list[Any] | None = None,
        portfolio_plan: Any | None = None,
        portfolio_attribution: dict[str, Any] | None = None,
        manager_review_report: ManagerReviewDigestPayload | dict[str, Any] | None = None,
        allocation_review_report: AllocationReviewDigestPayload | dict[str, Any] | None = None,
        dominant_manager_id: str = "",
    ) -> Any:
        if simulation_envelope is None or review_envelope is None:
            raise ValueError("build_cycle_result requires simulation_envelope and review_envelope")
        resolved_cycle_payload = dict(cycle_payload or {})
        if not resolved_cycle_payload:
            resolved_cycle_payload = review_envelope.to_cycle_payload()
        manager_results_payload = self._manager_results_payload(manager_results)
        portfolio_payload = self._portfolio_payload(portfolio_plan)
        portfolio_attribution_payload = self._derive_portfolio_attribution(
            portfolio_payload,
            portfolio_attribution=portfolio_attribution,
        )
        experiment_spec = dict(getattr(controller, "experiment_spec", {}) or {})
        execution_snapshot, governance_decision, execution_defaults, compatibility_fields = (
            self._resolve_outcome_boundary_projection(
                controller,
                cycle_id=cycle_id,
                resolved_cycle_payload=resolved_cycle_payload,
                simulation_envelope=simulation_envelope,
                manager_output=manager_output,
                selection_mode=selection_mode,
                benchmark_passed=benchmark_passed,
                manager_results_payload=manager_results_payload,
                portfolio_payload=portfolio_payload,
                dominant_manager_id=dominant_manager_id,
            )
        )
        (
            run_context,
            promotion_record,
            lineage_record,
            realism_metrics,
            stage_snapshots,
        ) = self._build_outcome_cycle_artifacts(
            controller,
            cycle_id=cycle_id,
            trade_dicts=trade_dicts,
            selection_mode=selection_mode,
            optimization_events=optimization_events,
            manager_output=manager_output,
            execution_snapshot=execution_snapshot,
            review_envelope=review_envelope,
            ab_comparison=ab_comparison,
            research_feedback=research_feedback,
            portfolio_attribution_payload=portfolio_attribution_payload,
            manager_review_report=manager_review_report,
            allocation_review_report=allocation_review_report,
        )
        return result_factory(
            **self._build_cycle_result_payload(
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                selected=selected,
                sim_result=sim_result,
                is_profit=is_profit,
                trade_dicts=trade_dicts,
                execution_snapshot=execution_snapshot,
                review_envelope=review_envelope,
                data_mode=data_mode,
                requested_data_mode=requested_data_mode,
                effective_data_mode=effective_data_mode,
                llm_mode=llm_mode,
                degraded=degraded,
                degrade_reason=degrade_reason,
                selection_mode=selection_mode,
                agent_used=agent_used,
                llm_used=llm_used,
                benchmark_passed=benchmark_passed,
                simulation_envelope=simulation_envelope,
                review_applied=review_applied,
                config_snapshot_path=config_snapshot_path,
                optimization_events=optimization_events,
                audit_tags=audit_tags,
                execution_defaults=execution_defaults,
                governance_decision=governance_decision,
                research_feedback=research_feedback,
                research_artifacts=research_artifacts,
                ab_comparison=ab_comparison,
                experiment_spec=experiment_spec,
                run_context=cast(dict[str, Any], run_context),
                promotion_record=promotion_record,
                lineage_record=lineage_record,
                manager_results_payload=manager_results_payload,
                portfolio_payload=portfolio_payload,
                portfolio_attribution_payload=portfolio_attribution_payload,
                manager_review_report=manager_review_report,
                allocation_review_report=allocation_review_report,
                dominant_manager_id=dominant_manager_id,
                compatibility_fields=compatibility_fields,
                realism_metrics=realism_metrics,
                stage_snapshots=stage_snapshots,
                validation_report=validation_report,
                peer_comparison_report=peer_comparison_report,
                judge_report=judge_report,
            )
        )
@dataclass(frozen=True)
class SelectionStageContext:
    selection_result: Any
    manager_output: Any | None
    regime_result: dict[str, Any]
    trading_plan: Any
    selected: list[str]
    selected_data: dict[str, Any]
    selection_mode: str
    agent_used: bool
    manager_bundle: Any | None
    manager_results_payload: list[dict[str, Any]]
    portfolio_plan_payload: dict[str, Any]
    dominant_manager_id: str
    portfolio_attribution_payload: dict[str, Any]
    compatibility_fields: dict[str, Any]
    regime_runtime_profile: dict[str, Any] = field(default_factory=dict)
    selection_intercepts: dict[str, Any] = field(default_factory=dict)

    def execution_snapshot_inputs(self, *, persistence_enabled: bool) -> dict[str, Any]:
        return {
            "manager_results": self.manager_results_payload if persistence_enabled else [],
            "portfolio_plan": self.portfolio_plan_payload if persistence_enabled else {},
            "dominant_manager_id": self.dominant_manager_id if persistence_enabled else "",
            "compatibility_fields": self.compatibility_fields if persistence_enabled else {},
        }

    def research_boundary_inputs(
        self,
        *,
        execution_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "manager_output": self.manager_output,
            "selected": list(self.selected),
            "regime_result": dict(self.regime_result or {}),
            "selection_mode": self.selection_mode,
            "portfolio_plan": dict(self.portfolio_plan_payload or {}),
            "manager_results": list(self.manager_results_payload or []),
            "execution_snapshot": dict(execution_snapshot or {}),
            "dominant_manager_id": self.dominant_manager_id,
        }

    def outcome_persistence_inputs(self, *, persistence_enabled: bool) -> dict[str, Any]:
        if not persistence_enabled:
            minimal_manager_results = [
                {
                    "manager_id": str(item.get("manager_id") or ""),
                    "manager_config_ref": str(item.get("manager_config_ref") or ""),
                }
                for item in list(self.manager_results_payload or [])
                if str(item.get("manager_id") or "").strip()
            ]
            active_manager_ids = [
                str(item).strip()
                for item in list(
                    dict(self.portfolio_plan_payload or {}).get("active_manager_ids")
                    or []
                )
                if str(item).strip()
            ]
            if not active_manager_ids:
                active_manager_ids = [
                    str(item.get("manager_id") or "").strip()
                    for item in minimal_manager_results
                    if str(item.get("manager_id") or "").strip()
                ]
            minimal_portfolio_plan = {}
            if self.selection_mode == "manager_portfolio":
                minimal_portfolio_plan = {
                    "active_manager_ids": active_manager_ids,
                    "dominant_manager_id": str(self.dominant_manager_id or ""),
                    "manager_count": len(active_manager_ids),
                }
            return {
                "manager_results": minimal_manager_results,
                "portfolio_plan": minimal_portfolio_plan,
                "portfolio_attribution": {},
                "dominant_manager_id": str(self.dominant_manager_id or ""),
            }
        return {
            "manager_results": list(self.manager_results_payload or []),
            "portfolio_plan": dict(self.portfolio_plan_payload or {}),
            "portfolio_attribution": dict(self.portfolio_attribution_payload or {}),
            "dominant_manager_id": self.dominant_manager_id,
        }


@dataclass(frozen=True)
class SimulationStageContext:
    sim_result: Any
    is_profit: bool
    trade_dicts: list[dict[str, Any]]
    benchmark_passed: bool
    research_artifacts: dict[str, Any]
    research_feedback: dict[str, Any]
    simulation_envelope: SimulationStageEnvelope
    cycle_payload: dict[str, Any] = field(default_factory=dict)

    def outcome_inputs(self) -> dict[str, Any]:
        return {
            "research_feedback": dict(self.research_feedback or {}),
            "research_artifacts": dict(self.research_artifacts or {}),
        }


@dataclass(frozen=True)
class OptimizationStageContext:
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewStageContext:
    review_stage_result: Any
    review_decision: ReviewDecisionInputPayload
    review_applied: bool
    review_envelope: ReviewStageEnvelope
    run_context: dict[str, Any]
    ab_comparison: dict[str, Any]
    cycle_payload: dict[str, Any] = field(default_factory=dict)

    def outcome_review_inputs(self, *, persistence_enabled: bool) -> dict[str, Any]:
        if not persistence_enabled:
            return {
                "manager_review_report": {},
                "allocation_review_report": {},
            }
        return {
            "manager_review_report": dict(
                self.cycle_payload.get("manager_review_report")
                or getattr(self.review_stage_result, "manager_review_report", {})
                or {}
            ),
            "allocation_review_report": dict(
                self.cycle_payload.get("allocation_review_report")
                or getattr(self.review_stage_result, "allocation_review_report", {})
                or {}
            ),
        }


@dataclass(frozen=True)
class OutcomeStageContext:
    cycle_result: Any
    cycle_payload: dict[str, Any]


@dataclass(frozen=True)
class ValidationStageContext:
    validation_input: ValidationInputEnvelope
    validation_report: ValidationReportInputPayload
    peer_comparison_report: dict[str, Any]
    judge_report: dict[str, Any]

    def cycle_result_updates(self) -> dict[str, dict[str, Any]]:
        return {
            "validation_report": dict(self.validation_report or {}),
            "validation_summary": dict((self.validation_report or {}).get("summary") or {}),
            "peer_comparison_report": dict(self.peer_comparison_report or {}),
            "judge_report": dict(self.judge_report or {}),
        }


def _build_self_assessment_payload(
    simulation_envelope: SimulationStageEnvelope,
    *,
    plan_source: str,
) -> dict[str, Any]:
    return {
        "regime": str(simulation_envelope.regime or "unknown"),
        "plan_source": str(plan_source or "unknown"),
        "sharpe_ratio": float(simulation_envelope.sharpe_ratio or 0.0),
        "max_drawdown": float(simulation_envelope.max_drawdown or 0.0),
        "excess_return": float(simulation_envelope.excess_return or 0.0),
        "benchmark_passed": bool(simulation_envelope.benchmark_passed),
    }


def _build_review_cycle_payload(
    simulation_envelope: SimulationStageEnvelope,
    *,
    review_decision: ReviewDecisionInputPayload | dict[str, Any],
    manager_review_report: ManagerReviewDigestPayload | dict[str, Any],
    allocation_review_report: AllocationReviewDigestPayload | dict[str, Any],
    ab_comparison: dict[str, Any],
    review_applied: bool,
) -> dict[str, Any]:
    causal_diagnosis = _dict_payload(review_decision.get("causal_diagnosis"))
    similarity_summary = cast(
        SimilaritySummaryInputPayload,
        _dict_payload(review_decision.get("similarity_summary")),
    )
    return {
        "cycle_id": int(simulation_envelope.cycle_id),
        "analysis": str(review_decision.get("reasoning") or ""),
        "review_decision": dict(review_decision or {}),
        "causal_diagnosis": causal_diagnosis,
        "similarity_summary": similarity_summary,
        "similar_results": cast(
            list[SimilarResultCompactPayload],
            deepcopy(list(review_decision.get("similar_results") or [])),
        ),
        "manager_review_report": dict(manager_review_report or {}),
        "allocation_review_report": dict(allocation_review_report or {}),
        "ab_comparison": dict(ab_comparison or {}),
        "review_applied": bool(review_applied),
    }


def _resolve_validation_regime_summary(
    *,
    selection_context: SelectionStageContext,
    review_context: ReviewStageContext,
) -> dict[str, Any]:
    return dict(
        review_context.review_decision.get("regime_summary")
        or review_context.review_envelope.review_decision.get("regime_summary")
        or selection_context.regime_result.get("regime_summary")
        or {}
    )


class TrainingExecutionService:
    """Owns the execution pipeline once cycle data is loaded."""

    @staticmethod
    def _evaluate_simulation_summary(
        controller: Any,
        *,
        cycle_id: int,
        sim_result: Any,
        selected_stocks: list[str],
        is_profit: bool,
        trade_dicts: list[dict[str, Any]],
        benchmark_daily_values: list[float],
        compatibility_cycle_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        evaluation_cycle_payload = dict(compatibility_cycle_payload or {})
        evaluation_cycle_payload.update(
            {
                "cycle_id": cycle_id,
                "is_profit": bool(is_profit),
                "profit_loss": float(getattr(sim_result, "total_pnl", 0.0) or 0.0),
                "return_pct": float(getattr(sim_result, "return_pct", 0.0) or 0.0),
                "total_trades": int(getattr(sim_result, "total_trades", 0) or 0),
                "winning_trades": int(
                    getattr(sim_result, "winning_trades", 0) or 0
                ),
                "losing_trades": int(getattr(sim_result, "losing_trades", 0) or 0),
                "win_rate": float(getattr(sim_result, "win_rate", 0.0) or 0.0),
                "selected_stocks": list(selected_stocks or []),
            }
        )
        evaluation_summary = controller.training_simulation_service.evaluate_cycle_summary(
            controller,
            cycle_payload=evaluation_cycle_payload,
            trade_dicts=trade_dicts,
            sim_result=sim_result,
            benchmark_daily_values=benchmark_daily_values,
        )
        return dict(evaluation_summary or {})

    @staticmethod
    def _build_simulation_compatibility_payload(
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        sim_result: Any,
        selected: list[str],
        is_profit: bool,
        selection_context: SelectionStageContext,
        governance_decision: GovernanceDecisionInputPayload | dict[str, Any],
        data_mode: str,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        llm_used: bool,
    ) -> dict[str, Any]:
        return controller.training_simulation_service.build_cycle_payload_projection(
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            sim_result=sim_result,
            selected=selected,
            is_profit=is_profit,
            regime_result=selection_context.regime_result,
            governance_decision=governance_decision,
            trading_plan=selection_context.trading_plan,
            data_mode=data_mode,
            requested_data_mode=requested_data_mode,
            effective_data_mode=effective_data_mode,
            llm_mode=llm_mode,
            degraded=degraded,
            degrade_reason=degrade_reason,
            selection_mode=selection_context.selection_mode,
            agent_used=selection_context.agent_used,
            llm_used=llm_used,
        )

    @staticmethod
    def _build_simulation_cycle_payload(
        simulation_envelope: SimulationStageEnvelope,
        *,
        compatibility_cycle_payload: dict[str, Any],
        simulation_summary: dict[str, Any],
        research_artifacts: dict[str, Any],
        research_feedback: dict[str, Any],
    ) -> dict[str, Any]:
        cycle_payload = dict(compatibility_cycle_payload or {})
        cycle_payload.update(
            {
                "research_artifacts": dict(research_artifacts or {}),
                "research_feedback": dict(research_feedback or {}),
                "benchmark_return": float(simulation_summary["benchmark_return"] or 0.0),
                "benchmark_source": str(simulation_summary["benchmark_source"] or "none"),
            }
        )
        return simulation_envelope.to_cycle_payload(base_payload=cycle_payload)

    @staticmethod
    def _build_simulation_stage_context(
        *,
        sim_result: Any,
        is_profit: bool,
        trade_dicts: list[dict[str, Any]],
        benchmark_passed: bool,
        cycle_payload: dict[str, Any],
        research_artifacts: dict[str, Any],
        research_feedback: dict[str, Any],
        simulation_envelope: SimulationStageEnvelope,
    ) -> SimulationStageContext:
        return SimulationStageContext(
            sim_result=sim_result,
            is_profit=bool(is_profit),
            trade_dicts=list(trade_dicts or []),
            benchmark_passed=bool(benchmark_passed),
            cycle_payload=dict(cycle_payload or {}),
            research_artifacts=dict(research_artifacts or {}),
            research_feedback=dict(research_feedback or {}),
            simulation_envelope=simulation_envelope,
        )

    @staticmethod
    def _append_stage_event(
        events: list[dict[str, Any]],
        event: Any | None,
    ) -> None:
        if event is None:
            return
        to_dict = getattr(event, "to_dict", None)
        if callable(to_dict):
            payload = to_dict()
            if isinstance(payload, dict):
                events.append(dict(payload))
                return
        if isinstance(event, dict):
            events.append(dict(event))

    @staticmethod
    def _extend_stage_events(
        events: list[dict[str, Any]],
        additions: list[dict[str, Any]] | None,
    ) -> None:
        if not additions:
            return
        events.extend(dict(item) for item in additions)

    @staticmethod
    def _build_optimization_stage_context(
        *,
        optimization_events: list[dict[str, Any]],
    ) -> OptimizationStageContext:
        return OptimizationStageContext(events=optimization_events)

    @staticmethod
    def _build_outcome_stage_inputs(
        controller: Any,
        *,
        config_snapshot_path: str,
        result_factory: Any,
        cycle_id: int,
        cutoff_date: str,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        data_mode: str,
        llm_used: bool,
        optimization_events: list[dict[str, Any]],
        selection_context: SelectionStageContext,
        simulation_context: SimulationStageContext,
        review_context: ReviewStageContext,
    ) -> dict[str, Any]:
        manager_persistence_enabled = bool(getattr(controller, "manager_persistence_enabled", False))
        audit_tags = controller.training_outcome_service.build_audit_tags(
            controller,
            data_mode=data_mode,
            requested_data_mode=requested_data_mode,
            effective_data_mode=effective_data_mode,
            llm_mode=llm_mode,
            degraded=degraded,
            degrade_reason=degrade_reason,
            selection_mode=selection_context.selection_mode,
            agent_used=selection_context.agent_used,
            llm_used=llm_used,
            benchmark_passed=simulation_context.benchmark_passed,
            review_applied=review_context.review_applied,
            regime_result=selection_context.regime_result,
        )
        return {
            "result_factory": result_factory,
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "selected": selection_context.selected,
            "sim_result": simulation_context.sim_result,
            "is_profit": simulation_context.is_profit,
            "trade_dicts": simulation_context.trade_dicts,
            "data_mode": data_mode,
            "requested_data_mode": requested_data_mode,
            "effective_data_mode": effective_data_mode,
            "llm_mode": llm_mode,
            "degraded": degraded,
            "degrade_reason": degrade_reason,
            "selection_mode": selection_context.selection_mode,
            "agent_used": selection_context.agent_used,
            "llm_used": llm_used,
            "benchmark_passed": simulation_context.benchmark_passed,
            "cycle_payload": dict(review_context.cycle_payload or {}),
            "simulation_envelope": simulation_context.simulation_envelope,
            "review_envelope": review_context.review_envelope,
            "review_applied": review_context.review_applied,
            "config_snapshot_path": config_snapshot_path,
            "optimization_events": optimization_events,
            "audit_tags": audit_tags,
            "manager_output": selection_context.manager_output,
            "ab_comparison": dict(review_context.ab_comparison or {}),
            **simulation_context.outcome_inputs(),
            **selection_context.outcome_persistence_inputs(
                persistence_enabled=manager_persistence_enabled
            ),
            **review_context.outcome_review_inputs(
                persistence_enabled=manager_persistence_enabled
            ),
        }

    @staticmethod
    def _build_review_run_context(
        controller: Any,
        *,
        cycle_id: int,
        manager_output: Any,
        optimization_events: list[dict[str, Any]],
        simulation_context: SimulationStageContext,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            build_cycle_run_context(
                controller,
                cycle_id=cycle_id,
                manager_output=manager_output,
                optimization_events=optimization_events,
                execution_snapshot=dict(
                    simulation_context.simulation_envelope.execution_snapshot or {}
                ),
            ),
        )

    @staticmethod
    def _log_ab_comparison(
        controller: Any,
        *,
        cycle_id: int,
        ab_comparison: dict[str, Any],
    ) -> None:
        if not ab_comparison:
            return
        comparison = dict(ab_comparison.get("comparison") or {})
        controller._emit_module_log(
            "promotion",
            "候选策略 A/B 对照完成",
            f"winner={comparison.get('winner', 'inconclusive')}",
            cycle_id=cycle_id,
            kind="candidate_ab_comparison",
            details=ab_comparison,
            metrics={
                "return_lift_pct": comparison.get("return_lift_pct"),
                "strategy_score_lift": comparison.get("strategy_score_lift"),
                "benchmark_lift": comparison.get("benchmark_lift"),
            },
        )

    def _build_review_ab_comparison(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
        selection_context: SelectionStageContext,
        run_context: dict[str, Any],
    ) -> dict[str, Any]:
        ab_projection = project_manager_compatibility(
            controller,
            manager_output=selection_context.manager_output,
            execution_snapshot=cast(dict[str, Any], run_context),
            dominant_manager_id_hint=str(selection_context.dominant_manager_id or ""),
        )
        ab_comparison = controller.training_ab_service.run_candidate_ab_comparison(
            controller,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            stock_data=stock_data,
            manager_id=str(ab_projection.manager_id or ""),
            active_runtime_config_ref=str(
                ab_projection.active_runtime_config_ref
                or run_context.get("active_runtime_config_ref")
                or ""
            ),
            candidate_runtime_config_ref=str(
                run_context.get("candidate_runtime_config_ref") or ""
            ),
            baseline_regime=str(selection_context.regime_result.get("regime") or ""),
        )
        self._log_ab_comparison(
            controller,
            cycle_id=cycle_id,
            ab_comparison=dict(ab_comparison or {}),
        )
        return dict(ab_comparison or {})

    @staticmethod
    def _build_review_stage_outputs(
        *,
        review_stage_result: Any,
        simulation_context: SimulationStageContext,
        review_decision: ReviewDecisionInputPayload | dict[str, Any],
        review_applied: bool,
        ab_comparison: dict[str, Any],
    ) -> tuple[ReviewStageEnvelope, dict[str, Any]]:
        manager_review_report = dict(getattr(review_stage_result, "manager_review_report", {}) or {})
        allocation_review_report = dict(
            getattr(review_stage_result, "allocation_review_report", {}) or {}
        )
        review_cycle_payload = _build_review_cycle_payload(
            simulation_context.simulation_envelope,
            review_decision=review_decision,
            manager_review_report=manager_review_report,
            allocation_review_report=allocation_review_report,
            ab_comparison=dict(ab_comparison or {}),
            review_applied=bool(review_applied),
        )
        review_envelope = ReviewStageEnvelope.from_structured_inputs(
            simulation=simulation_context.simulation_envelope,
            analysis=str(review_cycle_payload.get("analysis") or ""),
            review_decision=dict(review_decision or {}),
            causal_diagnosis=dict(review_cycle_payload.get("causal_diagnosis") or {}),
            similarity_summary=dict(review_cycle_payload.get("similarity_summary") or {}),
            similar_results=cast(
                list[SimilarResultCompactPayload],
                deepcopy(list(review_cycle_payload.get("similar_results") or [])),
            ),
            manager_review_report=manager_review_report,
            allocation_review_report=allocation_review_report,
            ab_comparison=dict(ab_comparison or {}),
            review_applied=bool(review_applied),
            stage_snapshots=cast(
                StageSnapshotsInputPayload,
                dict(simulation_context.simulation_envelope.stage_snapshots or {}),
            ),
        )
        final_cycle_payload = review_envelope.to_cycle_payload(
            base_payload=(
                dict(review_stage_result.cycle_payload or {})
                if getattr(review_stage_result, "cycle_payload", None)
                else simulation_context.cycle_payload
            )
        )
        return review_envelope, dict(final_cycle_payload or {})

    def _build_review_stage_context(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
        selection_context: SelectionStageContext,
        simulation_context: SimulationStageContext,
        review_stage_result: Any,
        optimization_events: list[dict[str, Any]],
    ) -> ReviewStageContext:
        review_decision = cast(
            ReviewDecisionInputPayload,
            dict(getattr(review_stage_result, "review_decision", {}) or {}),
        )
        review_applied = bool(getattr(review_stage_result, "review_applied", False))
        run_context = self._build_review_run_context(
            controller,
            cycle_id=cycle_id,
            optimization_events=optimization_events,
            manager_output=selection_context.manager_output,
            simulation_context=simulation_context,
        )
        proposal_bundle = dict(getattr(review_stage_result, "proposal_bundle", {}) or {})
        if proposal_bundle:
            run_context["proposal_bundle"] = deepcopy(proposal_bundle)
        ab_comparison = self._build_review_ab_comparison(
            controller,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            stock_data=stock_data,
            selection_context=selection_context,
            run_context=run_context,
        )
        review_envelope, review_cycle_payload = self._build_review_stage_outputs(
            review_stage_result=review_stage_result,
            simulation_context=simulation_context,
            review_decision=review_decision,
            review_applied=review_applied,
            ab_comparison=dict(ab_comparison or {}),
        )
        if proposal_bundle:
            review_cycle_payload["proposal_bundle"] = deepcopy(proposal_bundle)
        return ReviewStageContext(
            review_stage_result=review_stage_result,
            review_decision=review_decision,
            review_applied=review_applied,
            review_envelope=review_envelope,
            run_context=dict(run_context or {}),
            ab_comparison=dict(ab_comparison or {}),
            cycle_payload=review_cycle_payload,
        )

    @staticmethod
    def _build_validation_input(
        *,
        selection_context: SelectionStageContext,
        simulation_context: SimulationStageContext,
        review_context: ReviewStageContext,
        outcome_context: OutcomeStageContext,
    ) -> ValidationInputEnvelope:
        return ValidationInputEnvelope.from_cycle_result(
            outcome_context.cycle_result,
            review_envelope=review_context.review_envelope,
            regime=str(selection_context.regime_result.get("regime") or "unknown"),
            research_feedback=simulation_context.research_feedback,
            regime_summary=_resolve_validation_regime_summary(
                selection_context=selection_context,
                review_context=review_context,
            ),
        )

    @staticmethod
    def _build_validation_market_tag(
        validation_report: ValidationReportInputPayload | dict[str, Any],
    ) -> str:
        return str(
            dict(validation_report.get("market_tagging") or {}).get("primary_tag")
            or "unknown"
        )

    @staticmethod
    def _resolve_validation_manager_id(
        controller: Any,
        *,
        validation_input: ValidationInputEnvelope,
    ) -> str:
        validation_projection = project_manager_compatibility(
            controller,
            execution_snapshot=dict(validation_input.run_context or {}),
            dominant_manager_id_hint=str(validation_input.manager_id or ""),
        )
        return str(validation_projection.manager_id or "")

    @staticmethod
    def _build_validation_report(
        controller: Any,
        *,
        cycle_id: int,
        resolved_validation_manager_id: str,
        validation_input: ValidationInputEnvelope,
        optimization_events: list[dict[str, Any]],
    ) -> ValidationReportInputPayload:
        return cast(
            ValidationReportInputPayload,
            dict(
                run_validation_orchestrator(
                    cycle_id=cycle_id,
                    manager_id=resolved_validation_manager_id,
                    validation_input=validation_input,
                    run_context=dict(validation_input.run_context or {}),
                    review_result=dict(validation_input.review_result or {}),
                    cycle_result=dict(validation_input.cycle_result or {}),
                    cycle_history=list(session_cycle_history(controller) or []),
                    optimization_events=optimization_events,
                    feedback_policy=dict(
                        (
                            dict(getattr(controller, "promotion_gate_policy", {}) or {}).get(
                                "research_feedback"
                            )
                            or {}
                        )
                    ),
                    governance_policy=dict(
                        (getattr(controller, "quality_gate_matrix", {}) or {}).get(
                            "promotion"
                        )
                        or {}
                    ),
                )
                or {}
            ),
        )

    def _build_validation_peer_comparison_report(
        self,
        controller: Any,
        *,
        resolved_validation_manager_id: str,
        cycle_result: Any,
        validation_report: ValidationReportInputPayload | dict[str, Any],
    ) -> dict[str, Any]:
        return dict(
            compare_candidate_to_peers(
                self._build_validation_peer_candidate(
                    resolved_validation_manager_id=resolved_validation_manager_id,
                    cycle_result=cycle_result,
                ),
                build_history_peer_entries(list(session_cycle_history(controller) or [])),
                market_tag=self._build_validation_market_tag(validation_report),
            ).to_dict()
            or {}
        )

    @staticmethod
    def _build_validation_judge_report(
        *,
        validation_report: ValidationReportInputPayload | dict[str, Any],
        peer_comparison_report: dict[str, Any],
    ) -> dict[str, Any]:
        return dict(
            build_judge_report(
                dict(validation_report.get("summary") or {}),
                peer_comparison=peer_comparison_report,
                failure_tagging=dict(validation_report.get("failure_tagging") or {}),
            ).to_dict()
            or {}
        )

    def _build_validation_stage_context(
        self,
        controller: Any,
        *,
        cycle_id: int,
        optimization_events: list[dict[str, Any]],
        outcome_context: OutcomeStageContext,
        validation_input: ValidationInputEnvelope,
    ) -> ValidationStageContext:
        resolved_validation_manager_id = self._resolve_validation_manager_id(
            controller,
            validation_input=validation_input,
        )
        validation_report = self._build_validation_report(
            controller,
            cycle_id=cycle_id,
            resolved_validation_manager_id=resolved_validation_manager_id,
            validation_input=validation_input,
            optimization_events=optimization_events,
        )
        peer_comparison_report = self._build_validation_peer_comparison_report(
            controller,
            resolved_validation_manager_id=resolved_validation_manager_id,
            cycle_result=outcome_context.cycle_result,
            validation_report=validation_report,
        )
        judge_report = self._build_validation_judge_report(
            validation_report=validation_report,
            peer_comparison_report=peer_comparison_report,
        )
        return ValidationStageContext(
            validation_input=validation_input,
            validation_report=cast(
                ValidationReportInputPayload,
                dict(validation_report or {}),
            ),
            peer_comparison_report=peer_comparison_report,
            judge_report=judge_report,
        )

    @staticmethod
    def _build_validation_peer_candidate(
        *,
        resolved_validation_manager_id: str,
        cycle_result: Any,
    ) -> dict[str, Any]:
        return {
            "manager_id": resolved_validation_manager_id,
            "score": float(
                dict(getattr(cycle_result, "strategy_scores", {}) or {}).get("overall_score")
                or 0.0
            ),
            "avg_return_pct": float(getattr(cycle_result, "return_pct", 0.0) or 0.0),
            "benchmark_pass_rate": 1.0 if bool(getattr(cycle_result, "benchmark_passed", False)) else 0.0,
        }

    @staticmethod
    def _build_validation_contract_snapshots(
        *,
        cycle_result: Any,
        simulation_context: SimulationStageContext,
        review_context: ReviewStageContext,
        validation_context: ValidationStageContext,
    ) -> dict[str, Any]:
        return dict(
            build_cycle_contract_stage_snapshots(
                simulation_envelope=simulation_context.simulation_envelope,
                review_envelope=review_context.review_envelope,
                execution_snapshot=dict(getattr(cycle_result, "execution_snapshot", {}) or {}),
                validation_report=validation_context.validation_report,
                run_context=dict(getattr(cycle_result, "run_context", {}) or {}),
            )
        )

    @staticmethod
    def _attach_contract_stage_snapshots(
        cycle_result: Any,
        *,
        contract_stage_snapshots: dict[str, Any],
    ) -> None:
        snapshots_payload = dict(contract_stage_snapshots or {})
        cycle_result.execution_snapshot["contract_stage_snapshots"] = snapshots_payload
        cycle_result.run_context["contract_stage_snapshots"] = dict(snapshots_payload)
        existing_stage_snapshots = dict(getattr(cycle_result, "stage_snapshots", {}) or {})
        merged_stage_snapshots: dict[str, Any] = dict(existing_stage_snapshots)
        for stage_name, stage_payload in snapshots_payload.items():
            existing_stage_payload = existing_stage_snapshots.get(stage_name)
            if isinstance(existing_stage_payload, dict) and isinstance(stage_payload, dict):
                merged_stage_payload = dict(existing_stage_payload)
                for field_name, field_value in dict(stage_payload).items():
                    existing_field_value = merged_stage_payload.get(field_name)
                    if isinstance(existing_field_value, dict) and isinstance(field_value, dict):
                        merged_stage_payload[field_name] = {
                            **existing_field_value,
                            **field_value,
                        }
                        continue
                    merged_stage_payload[field_name] = field_value
                merged_stage_snapshots[stage_name] = merged_stage_payload
                continue
            merged_stage_snapshots[stage_name] = (
                dict(stage_payload) if isinstance(stage_payload, dict) else stage_payload
            )
        cycle_result.stage_snapshots = merged_stage_snapshots

    @staticmethod
    def _apply_validation_context_to_cycle_result(
        cycle_result: Any,
        *,
        cycle_id: int,
        validation_context: ValidationStageContext,
    ) -> None:
        updates = validation_context.cycle_result_updates()
        cycle_result.validation_report = updates["validation_report"]
        cycle_result.validation_summary = updates["validation_summary"]
        cycle_result.peer_comparison_report = updates["peer_comparison_report"]
        cycle_result.judge_report = updates["judge_report"]
        result_stage_snapshots = dict(getattr(cycle_result, "stage_snapshots", {}) or {})
        result_stage_snapshots["validation"] = build_validation_stage_snapshot(
            cycle_id=cycle_id,
            validation_report=validation_context.validation_report,
            judge_report=validation_context.judge_report,
        )
        cycle_result.stage_snapshots = result_stage_snapshots

    def _apply_finalization_stage_context(
        self,
        *,
        cycle_id: int,
        cycle_result: Any,
        simulation_context: SimulationStageContext,
        review_context: ReviewStageContext,
        validation_context: ValidationStageContext,
    ) -> None:
        self._apply_validation_context_to_cycle_result(
            cycle_result,
            cycle_id=cycle_id,
            validation_context=validation_context,
        )
        self._attach_contract_stage_snapshots(
            cycle_result,
            contract_stage_snapshots=self._build_validation_contract_snapshots(
                cycle_result=cycle_result,
                simulation_context=simulation_context,
                review_context=review_context,
                validation_context=validation_context,
            ),
        )

    @staticmethod
    def _build_finalization_assessment_payload(
        *,
        selection_context: SelectionStageContext,
        simulation_context: SimulationStageContext,
    ) -> dict[str, Any]:
        return _build_self_assessment_payload(
            simulation_context.simulation_envelope,
            plan_source=str(
                getattr(selection_context.trading_plan, "source", "") or "unknown"
            ),
        )

    @staticmethod
    def _resolve_selection_portfolio_plan_obj(
        *,
        manager_bundle: Any | None,
        portfolio_plan_payload: dict[str, Any],
    ) -> Any | None:
        portfolio_plan_obj = getattr(manager_bundle, "portfolio_plan", None)
        if portfolio_plan_obj is None and portfolio_plan_payload:
            return dict(portfolio_plan_payload)
        return portfolio_plan_obj

    def _build_selection_stage_context(
        self,
        controller: Any,
        *,
        selection_result: Any,
        selection_boundary: Any,
    ) -> SelectionStageContext:
        manager_bundle = getattr(selection_result, "manager_bundle", None)
        portfolio_plan_payload = dict(getattr(selection_result, "portfolio_plan", {}) or {})
        portfolio_plan_obj = self._resolve_selection_portfolio_plan_obj(
            manager_bundle=manager_bundle,
            portfolio_plan_payload=portfolio_plan_payload,
        )
        portfolio_attribution_payload = (
            controller.training_manager_execution_service.attribution_service.build_portfolio_attribution(
                portfolio_plan_obj
            )
            if portfolio_plan_obj is not None
            else {}
        )
        return SelectionStageContext(
            selection_result=selection_result,
            manager_output=selection_boundary.manager_output,
            regime_result=dict(getattr(selection_result, "regime_result", {}) or {}),
            trading_plan=selection_boundary.trading_plan,
            selected=list(getattr(selection_result, "selected_codes", []) or []),
            selected_data=dict(getattr(selection_result, "selected_data", {}) or {}),
            selection_mode=str(getattr(selection_result, "selection_mode", "") or ""),
            agent_used=bool(getattr(selection_result, "agent_used", False)),
            manager_bundle=manager_bundle,
            manager_results_payload=[
                dict(item)
                for item in list(getattr(selection_result, "manager_results", []) or [])
                if isinstance(item, dict)
            ],
            portfolio_plan_payload=portfolio_plan_payload,
            dominant_manager_id=str(
                getattr(selection_result, "dominant_manager_id", "") or ""
            ),
            portfolio_attribution_payload=dict(portfolio_attribution_payload or {}),
            compatibility_fields=dict(
                getattr(selection_result, "compatibility_fields", {}) or {}
            ),
            regime_runtime_profile=dict(
                getattr(selection_result, "regime_runtime_profile", {}) or {}
            ),
            selection_intercepts=dict(
                getattr(selection_result, "selection_intercepts", {}) or {}
            ),
        )

    def _run_selection_stage(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
    ) -> SelectionStageContext | None:
        selection_agent = "ManagerSelection"
        selection_stage = "manager_selection"
        logger.info("多经理组合选股中...")
        controller._emit_agent_status(
            selection_agent,
            "running",
            "多经理组合选股中...",
            cycle_id=cycle_id,
            stage=selection_stage,
            progress_pct=26,
            step=2,
            total_steps=6,
        )
        controller._emit_module_log(
            "selection",
            "进入多经理选股",
            "系统开始汇总经理候选与组合计划",
            cycle_id=cycle_id,
            kind="phase_start",
        )
        selection_result = controller.training_selection_service.run_selection_stage(
            controller,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            stock_data=stock_data,
        )
        if selection_result is None:
            return None

        selection_boundary = _call_training_module(
            "observability",
            "build_selection_boundary_projection",
            selection_result,
        )
        selection_context = self._build_selection_stage_context(
            controller,
            selection_result=selection_result,
            selection_boundary=selection_boundary,
        )
        logger.info(
            "市场状态(v2): %s",
            selection_context.regime_result.get("regime", "unknown"),
        )
        logger.info("最终选中股票: %s", selection_context.selected)
        return selection_context

    def _run_simulation_stage(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
        selection_context: SelectionStageContext,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        data_mode: str,
        llm_used: bool,
    ) -> SimulationStageContext | None:
        trader = controller.training_simulation_service.build_trader(
            controller,
            selected_data=selection_context.selected_data,
            trading_plan=selection_context.trading_plan,
        )
        simulation_days = max(
            1,
            int(
                controller.experiment_simulation_days
                or getattr(config, "simulation_days", 30)
            ),
        )
        trading_dates = controller.training_simulation_service.resolve_trading_dates(
            selected_data=selection_context.selected_data,
            cutoff_date=cutoff_date,
            simulation_days=simulation_days,
        )
        if len(trading_dates) < simulation_days:
            logger.warning("截断日期后交易日不足: %s < %s", len(trading_dates), simulation_days)
            controller._mark_cycle_skipped(
                cycle_id,
                cutoff_date,
                stage="simulation",
                reason=f"截断日期后交易日不足: {len(trading_dates)} < {simulation_days}",
            )
            return None

        controller._emit_agent_status(
            "SimulatedTrader",
            "running",
            f"模拟交易中... 初始资金 {trader.initial_capital:.2f}",
            cycle_id=cycle_id,
            stage="simulation",
            progress_pct=68,
            step=3,
            total_steps=6,
            details={
                "simulation_days": simulation_days,
                "selected_count": len(selection_context.selected),
            },
        )
        controller._emit_module_log(
            "simulation",
            "开始模拟交易",
            f"模拟 {simulation_days} 个交易日，标的 {', '.join(selection_context.selected[:5])}",
            cycle_id=cycle_id,
            kind="simulation_start",
            metrics={
                "simulation_days": simulation_days,
                "selected_count": len(selection_context.selected),
            },
        )

        benchmark_daily_values, market_index_frame = (
            controller.training_simulation_service.build_benchmark_context(
                controller,
                cutoff_date=cutoff_date,
                trading_dates=trading_dates,
            )
        )
        if market_index_frame is not None and not market_index_frame.empty:
            trader.set_market_index_data(market_index_frame)
        sim_result = trader.run_simulation(trading_dates[0], trading_dates)
        is_profit = sim_result.return_pct > 0

        controller.agent_tracker.record_outcomes(cycle_id, sim_result.per_stock_pnl)
        governance_decision = governance_from_controller(controller)
        trade_dicts = controller.training_simulation_service.build_trade_dicts(sim_result)
        compatibility_cycle_payload = self._build_simulation_compatibility_payload(
            controller,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            sim_result=sim_result,
            selected=selection_context.selected,
            is_profit=is_profit,
            selection_context=selection_context,
            governance_decision=governance_decision,
            data_mode=data_mode,
            requested_data_mode=requested_data_mode,
            effective_data_mode=effective_data_mode,
            llm_mode=llm_mode,
            degraded=degraded,
            degrade_reason=degrade_reason,
            llm_used=llm_used,
        )
        simulation_summary = self._evaluate_simulation_summary(
            controller,
            cycle_id=cycle_id,
            sim_result=sim_result,
            selected_stocks=selection_context.selected,
            is_profit=is_profit,
            trade_dicts=trade_dicts,
            benchmark_daily_values=benchmark_daily_values,
            compatibility_cycle_payload=compatibility_cycle_payload,
        )
        benchmark_passed = bool(simulation_summary["benchmark_passed"])
        manager_persistence_enabled = bool(getattr(controller, "manager_persistence_enabled", False))
        execution_snapshot = build_execution_snapshot(
            controller,
            cycle_id=cycle_id,
            manager_output=selection_context.manager_output,
            selection_mode=selection_context.selection_mode,
            benchmark_passed=benchmark_passed,
            **selection_context.execution_snapshot_inputs(
                persistence_enabled=manager_persistence_enabled
            ),
        )
        mutable_execution_snapshot = cast(dict[str, Any], execution_snapshot)
        if getattr(controller, "last_cutoff_policy_context", None):
            mutable_execution_snapshot["cutoff_policy_context"] = dict(
                controller.last_cutoff_policy_context or {}
            )
        mutable_execution_snapshot["effective_runtime_params"] = resolve_effective_runtime_params(
            controller
        )
        mutable_execution_snapshot["regime_runtime_profile"] = dict(
            selection_context.regime_runtime_profile or {}
        )
        mutable_execution_snapshot["selection_intercepts"] = dict(
            selection_context.selection_intercepts or {}
        )
        simulation_envelope = SimulationStageEnvelope.from_structured_inputs(
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            regime=str(selection_context.regime_result.get("regime") or "unknown"),
            selection_mode=selection_context.selection_mode,
            selected_stocks=selection_context.selected,
            return_pct=float(sim_result.return_pct or 0.0),
            benchmark_passed=bool(simulation_summary["benchmark_passed"]),
            benchmark_strict_passed=bool(
                simulation_summary["benchmark_strict_passed"]
            ),
            sharpe_ratio=float(simulation_summary["sharpe_ratio"] or 0.0),
            max_drawdown=float(simulation_summary["max_drawdown"] or 0.0),
            excess_return=float(simulation_summary["excess_return"] or 0.0),
            strategy_scores=dict(simulation_summary["strategy_scores"] or {}),
            governance_decision=dict(governance_decision or {}),
            execution_snapshot=cast(dict[str, Any], execution_snapshot),
        )
        research_artifacts, research_feedback = _call_training_module(
            "observability",
            "persist_research_boundary_effects",
            controller,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            stock_data=stock_data,
            **selection_context.research_boundary_inputs(
                execution_snapshot=dict(simulation_envelope.execution_snapshot or {}),
            ),
        )
        cycle_payload = self._build_simulation_cycle_payload(
            simulation_envelope,
            compatibility_cycle_payload=compatibility_cycle_payload,
            simulation_summary=simulation_summary,
            research_artifacts=dict(research_artifacts or {}),
            research_feedback=dict(research_feedback or {}),
        )
        controller._emit_agent_status(
            "SimulatedTrader",
            "completed",
            f"模拟完成，收益 {sim_result.return_pct:+.2f}% ，共 {sim_result.total_trades} 笔交易",
            cycle_id=cycle_id,
            stage="simulation",
            progress_pct=78,
            step=3,
            total_steps=6,
            details={
                "final_value": sim_result.final_value,
                "win_rate": sim_result.win_rate,
            },
        )
        controller._emit_module_log(
            "simulation",
            "模拟交易完成",
            f"期末资金 {sim_result.final_value:.2f}，收益 {sim_result.return_pct:+.2f}%",
            cycle_id=cycle_id,
            kind="simulation_result",
            details=trade_dicts[:12],
            metrics={
                "return_pct": sim_result.return_pct,
                "trade_count": sim_result.total_trades,
                "win_rate": sim_result.win_rate,
            },
        )
        return self._build_simulation_stage_context(
            sim_result=sim_result,
            is_profit=is_profit,
            trade_dicts=trade_dicts,
            benchmark_passed=benchmark_passed,
            cycle_payload=cycle_payload,
            research_artifacts=dict(research_artifacts or {}),
            research_feedback=dict(research_feedback or {}),
            simulation_envelope=simulation_envelope,
        )

    def _apply_optimization_stage(
        self,
        controller: Any,
        *,
        cycle_id: int,
        simulation_context: SimulationStageContext,
        optimization_events: list[dict[str, Any]],
    ) -> OptimizationStageContext:
        feedback_plan = controller._build_feedback_optimization_plan(
            simulation_context.research_feedback,
            cycle_id=cycle_id,
        )
        feedback_brief = controller._feedback_optimization_brief(
            feedback_plan,
            triggered=False,
        )
        set_session_last_feedback_optimization(controller, dict(feedback_brief or {}))

        def build_optimization_input() -> OptimizationInputEnvelope:
            return OptimizationInputEnvelope(
                simulation=simulation_context.simulation_envelope,
                research_feedback=dict(simulation_context.research_feedback or {}),
                research_feedback_optimization=dict(
                    session_last_feedback_optimization(controller) or {}
                ),
            )

        if not simulation_context.is_profit:
            consecutive_losses = increment_session_consecutive_losses(controller)
            logger.warning("亏损！连续亏损: %s", consecutive_losses)
            if consecutive_losses >= controller.max_losses_before_optimize:
                self._extend_stage_events(
                    optimization_events,
                    controller._trigger_optimization(
                        build_optimization_input(),
                        simulation_context.trade_dicts,
                        trigger_reason="consecutive_losses",
                        feedback_plan=feedback_plan or None,
                    ),
                )
                if feedback_plan:
                    set_session_last_feedback_optimization_cycle_id(
                        controller,
                        cycle_id,
                    )
                    set_session_last_feedback_optimization(
                        controller,
                        controller._feedback_optimization_brief(
                            feedback_plan,
                            triggered=True,
                        )
                        or {},
                    )
                    feedback_plan = {}
        else:
            set_session_consecutive_losses(controller, 0)
            logger.info(
                "盈利！收益率: %.2f%%",
                simulation_context.sim_result.return_pct,
            )

        if feedback_plan:
            self._extend_stage_events(
                optimization_events,
                controller._trigger_optimization(
                    build_optimization_input(),
                    simulation_context.trade_dicts,
                    trigger_reason="research_feedback",
                    feedback_plan=feedback_plan,
                ),
            )
            set_session_last_feedback_optimization_cycle_id(controller, cycle_id)
            set_session_last_feedback_optimization(
                controller,
                controller._feedback_optimization_brief(
                    feedback_plan,
                    triggered=True,
                )
                or {},
            )
        return self._build_optimization_stage_context(
            optimization_events=optimization_events,
        )

    def _run_review_stage(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
        selection_context: SelectionStageContext,
        simulation_context: SimulationStageContext,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        data_mode: str,
        llm_used: bool,
        optimization_event_factory: Any,
        optimization_events: list[dict[str, Any]],
    ) -> ReviewStageContext:
        logger.info("周期结语：双层复盘中...")
        review_stage_result = controller.training_review_stage_service.run_review_stage(
            controller,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            sim_result=simulation_context.sim_result,
            regime_result=selection_context.regime_result,
            selected=selection_context.selected,
            trade_dicts=simulation_context.trade_dicts,
            requested_data_mode=requested_data_mode,
            effective_data_mode=effective_data_mode,
            llm_mode=llm_mode,
            degraded=degraded,
            degrade_reason=degrade_reason,
            data_mode=data_mode,
            selection_mode=selection_context.selection_mode,
            agent_used=selection_context.agent_used,
            llm_used=llm_used,
            manager_output=selection_context.manager_output,
            research_feedback=simulation_context.research_feedback,
            optimization_event_factory=optimization_event_factory,
            simulation_envelope=simulation_context.simulation_envelope,
            manager_bundle=selection_context.manager_bundle,
        )
        self._append_stage_event(
            optimization_events,
            getattr(review_stage_result, "review_event", None),
        )
        current_cycle_learning_proposals = list(
            getattr(controller, "current_cycle_learning_proposals", []) or []
        )
        review_learning_proposals = [
            _runtime_copy_dict(item)
            for item in list(
                dict(getattr(review_stage_result, "review_decision", {}) or {}).get(
                    "learning_proposals"
                )
                or dict(getattr(review_stage_result, "review_trace", {}) or {}).get(
                    "learning_proposals"
                )
                or []
            )
        ]
        for proposal in review_learning_proposals:
            proposal_id = str(proposal.get("proposal_id") or "").strip()
            if proposal_id and any(
                str(_runtime_copy_dict(item).get("proposal_id") or "").strip()
                == proposal_id
                for item in current_cycle_learning_proposals
            ):
                continue
            current_cycle_learning_proposals.append(proposal)
        setattr(
            controller,
            "current_cycle_learning_proposals",
            current_cycle_learning_proposals,
        )
        proposal_bundle = _call_training_module_if_available(
            "persistence",
            "persist_cycle_proposal_bundle",
            controller,
            cycle_id=cycle_id,
            execution_snapshot=dict(
                simulation_context.simulation_envelope.execution_snapshot or {}
            ),
            proposals=_cycle_learning_proposals(controller, cycle_id=cycle_id),
        )
        proposal_bundle_payload = (
            _runtime_copy_dict(proposal_bundle)
            if isinstance(proposal_bundle, dict)
            else {}
        )
        if proposal_bundle_payload:
            candidate_event = build_cycle_candidate_from_proposals(
                controller,
                cycle_id=cycle_id,
                proposal_bundle=proposal_bundle_payload,
                event_factory=optimization_event_factory,
                trigger_reason="cycle_review_completed",
            )
            refreshed_bundle = _runtime_copy_dict(
                getattr(controller, "last_cycle_proposal_bundle", {})
                or proposal_bundle_payload
            )
            review_stage_payload = dict(
                getattr(review_stage_result, "cycle_payload", {}) or {}
            )
            review_stage_payload["proposal_bundle"] = refreshed_bundle
            object.__setattr__(review_stage_result, "cycle_payload", review_stage_payload)
            object.__setattr__(review_stage_result, "proposal_bundle", refreshed_bundle)
            if candidate_event is not None:
                self._append_stage_event(optimization_events, candidate_event)
        return self._build_review_stage_context(
            controller,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            stock_data=stock_data,
            selection_context=selection_context,
            simulation_context=simulation_context,
            review_stage_result=review_stage_result,
            optimization_events=optimization_events,
        )

    def _build_outcome_stage(
        self,
        controller: Any,
        *,
        result_factory: Any,
        cycle_id: int,
        cutoff_date: str,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        data_mode: str,
        llm_used: bool,
        optimization_events: list[dict[str, Any]],
        selection_context: SelectionStageContext,
        simulation_context: SimulationStageContext,
        review_context: ReviewStageContext,
    ) -> OutcomeStageContext:
        config_snapshot_path = _call_training_module(
            "observability",
            "write_runtime_snapshot_boundary",
            controller,
            cycle_id=cycle_id,
        )
        cycle_result = controller.training_outcome_service.build_cycle_result(
            controller,
            **self._build_outcome_stage_inputs(
                controller,
                config_snapshot_path=config_snapshot_path,
                result_factory=result_factory,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                requested_data_mode=requested_data_mode,
                effective_data_mode=effective_data_mode,
                llm_mode=llm_mode,
                degraded=degraded,
                degrade_reason=degrade_reason,
                data_mode=data_mode,
                llm_used=llm_used,
                optimization_events=optimization_events,
                selection_context=selection_context,
                simulation_context=simulation_context,
                review_context=review_context,
            ),
        )
        return OutcomeStageContext(
            cycle_result=cycle_result,
            cycle_payload=dict(review_context.cycle_payload or {}),
        )

    def _run_validation_stage(
        self,
        controller: Any,
        *,
        cycle_id: int,
        optimization_events: list[dict[str, Any]],
        selection_context: SelectionStageContext,
        simulation_context: SimulationStageContext,
        review_context: ReviewStageContext,
        outcome_context: OutcomeStageContext,
    ) -> ValidationStageContext:
        return self._build_validation_stage_context(
            controller,
            cycle_id=cycle_id,
            optimization_events=optimization_events,
            outcome_context=outcome_context,
            validation_input=self._build_validation_input(
                selection_context=selection_context,
                simulation_context=simulation_context,
                review_context=review_context,
                outcome_context=outcome_context,
            ),
        )

    def _finalize_cycle_stage(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        selection_context: SelectionStageContext,
        simulation_context: SimulationStageContext,
        review_context: ReviewStageContext,
        outcome_context: OutcomeStageContext,
        validation_context: ValidationStageContext,
    ) -> Any:
        cycle_result = outcome_context.cycle_result
        self._apply_finalization_stage_context(
            cycle_id=cycle_id,
            cycle_result=cycle_result,
            simulation_context=simulation_context,
            review_context=review_context,
            validation_context=validation_context,
        )
        controller.training_lifecycle_service.finalize_cycle(
            controller,
            cycle_result=cycle_result,
            assessment_payload=self._build_finalization_assessment_payload(
                selection_context=selection_context,
                simulation_context=simulation_context,
            ),
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            sim_result=simulation_context.sim_result,
            is_profit=simulation_context.is_profit,
            selected=selection_context.selected,
            trade_dicts=simulation_context.trade_dicts,
            review_applied=review_context.review_applied,
            selection_mode=selection_context.selection_mode,
            requested_data_mode=requested_data_mode,
            effective_data_mode=effective_data_mode,
            llm_mode=llm_mode,
            degraded=degraded,
            degrade_reason=degrade_reason,
            research_feedback=simulation_context.research_feedback,
        )
        return cycle_result

    def execute_loaded_cycle(
        self,
        controller: Any,
        *,
        result_factory: Any,
        optimization_event_factory: Any,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
        diagnostics: dict[str, Any],
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        data_mode: str,
        llm_used: bool,
        optimization_events: list[dict[str, Any]],
    ) -> Any | None:
        del diagnostics
        cycle_result: Any | None = None
        enforce_allowed_manager_scope_boundary(controller)
        controller._maybe_apply_allocator(stock_data, cutoff_date, cycle_id)
        enforce_allowed_manager_scope_boundary(controller)
        begin_cycle_runtime_window(controller, cycle_id=cycle_id)
        try:
            selection_context = self._run_selection_stage(
                controller,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                stock_data=stock_data,
            )
            if selection_context is None:
                return None

            simulation_context = self._run_simulation_stage(
                controller,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                stock_data=stock_data,
                selection_context=selection_context,
                requested_data_mode=requested_data_mode,
                effective_data_mode=effective_data_mode,
                llm_mode=llm_mode,
                degraded=degraded,
                degrade_reason=degrade_reason,
                data_mode=data_mode,
                llm_used=llm_used,
            )
            if simulation_context is None:
                return None

            optimization_context = self._apply_optimization_stage(
                controller,
                cycle_id=cycle_id,
                simulation_context=simulation_context,
                optimization_events=optimization_events,
            )
            review_context = self._run_review_stage(
                controller,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                stock_data=stock_data,
                selection_context=selection_context,
                simulation_context=simulation_context,
                requested_data_mode=requested_data_mode,
                effective_data_mode=effective_data_mode,
                llm_mode=llm_mode,
                degraded=degraded,
                degrade_reason=degrade_reason,
                data_mode=data_mode,
                llm_used=llm_used,
                optimization_event_factory=optimization_event_factory,
                optimization_events=optimization_context.events,
            )
            outcome_context = self._build_outcome_stage(
                controller,
                result_factory=result_factory,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                data_mode=data_mode,
                requested_data_mode=requested_data_mode,
                effective_data_mode=effective_data_mode,
                llm_mode=llm_mode,
                degraded=degraded,
                degrade_reason=degrade_reason,
                llm_used=llm_used,
                optimization_events=optimization_context.events,
                selection_context=selection_context,
                simulation_context=simulation_context,
                review_context=review_context,
            )
            validation_context = self._run_validation_stage(
                controller,
                cycle_id=cycle_id,
                optimization_events=optimization_context.events,
                selection_context=selection_context,
                simulation_context=simulation_context,
                review_context=review_context,
                outcome_context=outcome_context,
            )
            cycle_result = self._finalize_cycle_stage(
                controller,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                requested_data_mode=requested_data_mode,
                effective_data_mode=effective_data_mode,
                llm_mode=llm_mode,
                degraded=degraded,
                degrade_reason=degrade_reason,
                selection_context=selection_context,
                simulation_context=simulation_context,
                review_context=review_context,
                outcome_context=outcome_context,
                validation_context=validation_context,
            )
            return cycle_result
        finally:
            discipline_summary = finalize_cycle_runtime_window(controller)
            if cycle_result is not None:
                cycle_result.run_context["runtime_discipline"] = deepcopy(
                    discipline_summary
                )
                cycle_result.execution_snapshot["runtime_discipline"] = deepcopy(
                    discipline_summary
                )
            if discipline_summary.get("violation_count"):
                controller._emit_module_log(
                    "runtime_discipline",
                    "检测到 cycle 内非法 runtime 变更",
                    "已回滚到 cycle 起点 active 参数",
                    cycle_id=cycle_id,
                    kind="runtime_mutation_violation",
                    details=discipline_summary,
                    metrics={
                        "violation_count": int(
                            discipline_summary.get("violation_count") or 0
                        ),
                        "proposal_count": int(
                            discipline_summary.get("proposal_count") or 0
                        ),
                    },
                )


__all__ = [
    "OutcomeStageContext",
    "ReviewStageContext",
    "SelectionStageContext",
    "SimulationStageContext",
    "TrainingExecutionService",
    "ValidationStageContext",
]

@dataclass
class ManagerExecutionBundle:
    run_context: ManagerRunContext
    manager_results: list[ManagerResult]
    portfolio_plan: PortfolioPlan
    dominant_manager_id: str
    manager_outputs: dict[str, Any] = field(default_factory=dict)
    execution_payload: dict[str, Any] = field(default_factory=dict)

    def portfolio_plan_payload(self) -> dict[str, Any]:
        return dict(self.portfolio_plan.to_dict())

    def manager_results_payload(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in list(self.manager_results or [])]

    def dominant_manager_output(self) -> Any | None:
        dominant_output = self.manager_outputs.get(self.dominant_manager_id)
        if dominant_output is not None:
            return dominant_output
        if self.manager_outputs:
            return next(iter(self.manager_outputs.values()))
        return None

    def build_regime_result(
        self,
        *,
        selected_codes: list[str],
        decision_source: str = "manager_runtime",
    ) -> dict[str, Any]:
        return {
            "regime": self.run_context.regime,
            "confidence": float(self.portfolio_plan.confidence or 0.0),
            "reasoning": self.portfolio_plan.reasoning,
            "suggested_exposure": max(
                0.0,
                min(1.0, 1.0 - float(self.portfolio_plan.cash_reserve or 0.0)),
            ),
            "decision_source": str(decision_source or "manager_runtime"),
            "params": {
                "top_n": len(selected_codes),
                "max_positions": self.portfolio_plan.to_trading_plan().max_positions,
                "manager_weights": dict(self.run_context.budget_weights or {}),
            },
        }

    def build_selection_trace(
        self,
        *,
        selected_codes: list[str],
        decision_source: str = "manager_runtime",
    ) -> dict[str, Any]:
        return {
            "selected": list(selected_codes),
            "active_managers": list(self.portfolio_plan.active_manager_ids),
            "dominant_manager_id": self.dominant_manager_id,
            "portfolio_plan": self.portfolio_plan_payload(),
            "manager_results": self.manager_results_payload(),
            "decision_source": str(decision_source or "manager_runtime"),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_context": self.run_context.to_dict(),
            "manager_results": self.manager_results_payload(),
            "portfolio_plan": self.portfolio_plan_payload(),
            "dominant_manager_id": self.dominant_manager_id,
            "execution_payload": dict(self.execution_payload or {}),
        }


class ManagerExecutionService:
    """Minimal multi-manager runtime seam built on top of the new contracts."""

    def __init__(
        self,
        *,
        registry=None,
        assembler: PortfolioAssembler | None = None,
        attribution_service: AttributionService | None = None,
        simulation_service: SimulationService | None = None,
        cognitive_assist_service: CognitiveAssistService | None = None,
    ) -> None:
        self.registry = registry or build_default_manager_registry()
        self.assembler = assembler or PortfolioAssembler()
        self.attribution_service = attribution_service or AttributionService()
        self.simulation_service = simulation_service or SimulationService()
        self.cognitive_assist_service = cognitive_assist_service or CognitiveAssistService()

    def build_run_context(
        self,
        controller: Any,
        *,
        cutoff_date: str,
        stock_data: dict[str, Any],
    ) -> ManagerRunContext:
        governance_context = governance_from_controller(controller)
        # Default to multi-manager semantics unless the controller explicitly disables them.
        # This keeps unit tests and legacy controllers stable, while still allowing strict
        # single-manager isolation in readiness runs via `manager_arch_enabled=False`.
        manager_arch_enabled = bool(getattr(controller, "manager_arch_enabled", True))
        dominant = str(
            dominant_manager_id(governance_context)
            or session_default_manager_id(controller)
            or "momentum"
        ).strip()
        active_manager_ids = (
            self._resolve_active_manager_ids(controller)
            if manager_arch_enabled
            else ([dominant] if dominant else [])
        )
        regime = str(governance_context.get("regime") or "unknown")
        budget_weights = (
            self._resolve_budget_weights(controller, active_manager_ids)
            if manager_arch_enabled
            else ({dominant: 1.0} if dominant else {})
        )
        return ManagerRunContext(
            as_of_date=cutoff_date,
            regime=regime,
            market_stats=self._resolve_market_stats(governance_context),
            factor_snapshot={},
            budget_weights=budget_weights,
            runtime_params=dict(session_current_params(controller) or {}),
            active_manager_ids=active_manager_ids,
            governance_context=governance_context,
            review_context={},
            metadata={
                "stock_universe_size": len(stock_data or {}),
                "subject_type": "manager_portfolio" if manager_arch_enabled else "single_manager",
                "dominant_manager_id": dominant,
            },
        )

    def execute_manager_selection(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
    ) -> ManagerExecutionBundle:
        del cycle_id
        run_context = self.build_run_context(
            controller,
            cutoff_date=cutoff_date,
            stock_data=stock_data,
        )
        manager_plans, manager_outputs = self._collect_manager_execution_artifacts(
            stock_data=stock_data,
            run_context=run_context,
        )
        attributions = {
            item.manager_id: item
            for item in self.attribution_service.build_manager_attributions(
                manager_plans,
                manager_weights=run_context.budget_weights,
            )
        }
        manager_results = [
            self._build_manager_result(
                plan,
                attribution=attributions.get(plan.manager_id),
            )
            for plan in manager_plans
        ]
        dominant_manager_id = self._resolve_dominant_manager_id(
            manager_results,
            run_context=run_context,
        )
        portfolio_plan = self._build_portfolio_plan(
            controller,
            manager_results=manager_results,
            manager_plans=manager_plans,
            run_context=run_context,
            dominant_manager_id=dominant_manager_id,
        )
        execution_payload = self.simulation_service.build_execution_payload(portfolio_plan)
        return ManagerExecutionBundle(
            run_context=run_context,
            manager_results=manager_results,
            portfolio_plan=portfolio_plan,
            dominant_manager_id=dominant_manager_id,
            manager_outputs=manager_outputs,
            execution_payload=execution_payload,
        )

    def _collect_manager_execution_artifacts(
        self,
        *,
        stock_data: dict[str, Any],
        run_context: ManagerRunContext,
    ) -> tuple[list[Any], dict[str, Any]]:
        manager_outputs: dict[str, Any] = {}
        manager_plans: list[Any] = []
        for manager_id in run_context.active_manager_ids:
            manager = self.registry.build_manager(
                manager_id,
                runtime_overrides=dict(run_context.runtime_params or {}),
            )
            artifacts = manager.run(stock_data, run_context)
            manager_plans.append(artifacts.manager_plan)
            manager_outputs[manager_id] = artifacts.manager_output
        return manager_plans, manager_outputs

    def _build_manager_result(
        self,
        plan: Any,
        *,
        attribution: Any | None,
    ) -> ManagerResult:
        return ManagerResult(
            manager_id=plan.manager_id,
            as_of_date=plan.as_of_date,
            status="planned" if plan.positions else "empty",
            plan=plan,
            metrics={"position_count": len(plan.positions)},
            attribution=attribution,
            evidence=self.cognitive_assist_service.explain_plan(plan),
        )

    @staticmethod
    def _empty_portfolio_plan(run_context: ManagerRunContext) -> PortfolioPlan:
        return PortfolioPlan(
            as_of_date=run_context.as_of_date,
            regime=run_context.regime,
            positions=[],
            cash_reserve=1.0,
            active_manager_ids=[],
            manager_weights={},
            confidence=0.0,
            reasoning="no dominant manager available",
            metadata={"assembly_mode": "dominant_manager_only"},
        )

    @staticmethod
    def _portfolio_positions_from_plan(plan: Any) -> list[PortfolioPlanPosition]:
        return [
            PortfolioPlanPosition(
                code=position.code,
                target_weight=float(position.target_weight or 0.0),
                rank=int(position.rank or 0),
                source_managers=[plan.manager_id],
                manager_weights={plan.manager_id: 1.0},
                entry_method=position.entry_method,
                entry_price=position.entry_price,
                stop_loss_pct=position.stop_loss_pct,
                take_profit_pct=position.take_profit_pct,
                trailing_pct=position.trailing_pct,
                max_hold_days=position.max_hold_days,
                thesis=position.thesis,
                metadata=dict(position.metadata or {}),
            )
            for position in list(plan.positions or [])
        ]

    @classmethod
    def _portfolio_plan_from_dominant_result(cls, dominant_result: ManagerResult) -> PortfolioPlan:
        plan = dominant_result.plan
        return PortfolioPlan(
            as_of_date=plan.as_of_date,
            regime=plan.regime,
            positions=cls._portfolio_positions_from_plan(plan),
            cash_reserve=float(plan.cash_reserve or 0.0),
            active_manager_ids=[plan.manager_id],
            manager_weights={plan.manager_id: 1.0},
            confidence=float(plan.confidence or 0.0),
            reasoning=str(plan.reasoning or ""),
            metadata={"assembly_mode": "dominant_manager_only"},
        )

    def _build_portfolio_plan(
        self,
        controller: Any,
        *,
        manager_results: list[ManagerResult],
        manager_plans: list[Any],
        run_context: ManagerRunContext,
        dominant_manager_id: str,
    ) -> PortfolioPlan:
        if bool(getattr(controller, "portfolio_assembly_enabled", True)):
            return self.assembler.assemble(
                manager_plans,
                manager_weights=run_context.budget_weights,
                regime=run_context.regime,
                as_of_date=run_context.as_of_date,
            )
        dominant_result = next(
            (item for item in manager_results if item.manager_id == dominant_manager_id),
            manager_results[0] if manager_results else None,
        )
        if dominant_result is None:
            return self._empty_portfolio_plan(run_context)
        return self._portfolio_plan_from_dominant_result(dominant_result)

    @staticmethod
    def _normalize_budget_weights(
        active_manager_ids: list[str],
        raw_budget_weights: dict[str, float],
    ) -> dict[str, float]:
        if not active_manager_ids:
            return {}
        if not raw_budget_weights:
            equal = round(1.0 / len(active_manager_ids), 8)
            return {manager_id: equal for manager_id in active_manager_ids}
        raw = {
            manager_id: max(0.0, float(raw_budget_weights.get(manager_id, 0.0)))
            for manager_id in active_manager_ids
        }
        total = sum(raw.values())
        if total <= 0:
            equal = round(1.0 / len(active_manager_ids), 8)
            return {manager_id: equal for manager_id in active_manager_ids}
        return {
            manager_id: round(weight / total, 8)
            for manager_id, weight in raw.items()
        }

    def _resolve_active_manager_ids(self, controller: Any) -> list[str]:
        active_manager_ids = list(getattr(controller, "manager_active_ids", []) or [])
        if active_manager_ids:
            return active_manager_ids
        configured_ids = list(getattr(config, "manager_active_ids", []) or [])
        if configured_ids:
            return configured_ids
        return self.registry.list_manager_ids()

    def _resolve_budget_weights(
        self,
        controller: Any,
        active_manager_ids: list[str],
    ) -> dict[str, float]:
        raw_budget_weights = dict(session_manager_budget_weights(controller) or {})
        if not raw_budget_weights:
            raw_budget_weights = dict(getattr(config, "manager_budget_weights", {}) or {})
        if not bool(getattr(controller, "manager_allocator_enabled", True)):
            raw_budget_weights = {}
        return self._normalize_budget_weights(active_manager_ids, raw_budget_weights)

    @staticmethod
    def _resolve_market_stats(governance_context: dict[str, Any]) -> dict[str, Any]:
        return dict(
            governance_context.get("market_stats")
            or dict(governance_context.get("evidence") or {})
            .get("market_observation", {})
            .get("stats")
            or {}
        )

    @staticmethod
    def _resolve_dominant_manager_id(
        manager_results: list[ManagerResult],
        *,
        run_context: ManagerRunContext,
    ) -> str:
        if not manager_results:
            return ""
        ordered = sorted(
            manager_results,
            key=lambda item: (
                run_context.budget_weights.get(item.manager_id, 0.0),
                len(item.plan.positions),
                item.plan.confidence,
            ),
            reverse=True,
        )
        return ordered[0].manager_id


def _selection_overlap_ratio(left: list[str], right: list[str]) -> float | None:
    left_set = {str(item).strip() for item in list(left or []) if str(item).strip()}
    right_set = {str(item).strip() for item in list(right or []) if str(item).strip()}
    union = left_set | right_set
    if not union:
        return None
    return round(len(left_set & right_set) / len(union), 4)


def _clamp_pct(value: Any, *, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return float(default)


class TrainingABService:
    """Runs side-effect-free candidate-vs-active comparative training."""

    @staticmethod
    def _arm_execution_snapshot(
        *,
        manager_id: str,
        runtime_config_ref: str,
    ) -> dict[str, Any]:
        resolved_manager_id = str(manager_id or "")
        resolved_runtime_config_ref = str(runtime_config_ref or "")
        return {
            "active_runtime_config_ref": resolved_runtime_config_ref,
            "manager_config_ref": resolved_runtime_config_ref,
            "execution_defaults": {
                "default_manager_id": resolved_manager_id,
                "default_manager_config_ref": resolved_runtime_config_ref,
            },
            "dominant_manager_id": resolved_manager_id,
        }

    @staticmethod
    def _project_arm_manager(
        controller: Any,
        *,
        manager_output: Any,
        manager_id: str,
        runtime_config_ref: str,
    ) -> Any:
        return project_manager_compatibility(
            controller,
            manager_output=manager_output,
            execution_snapshot=TrainingABService._arm_execution_snapshot(
                manager_id=manager_id,
                runtime_config_ref=runtime_config_ref,
            ),
            dominant_manager_id_hint=str(manager_id or ""),
        )

    @staticmethod
    def _selected_codes_from_plan(trading_plan: TradingPlan) -> list[str]:
        return [
            str(position.code).strip()
            for position in list(getattr(trading_plan, "positions", []) or [])
            if str(getattr(position, "code", "")).strip()
        ]

    @staticmethod
    def _build_arm_payload(
        *,
        status: str,
        arm_name: str,
        runtime_config_ref: str,
        manager_projection: Any | None = None,
        selected_stocks: list[str] | None = None,
        selection_mode: str = "",
        regime: str = "unknown",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": str(status or ""),
            "arm": str(arm_name or ""),
            "runtime_config_ref": str(runtime_config_ref or ""),
        }
        if manager_projection is not None:
            payload["manager_id"] = str(getattr(manager_projection, "manager_id", "") or "")
            payload["manager_config_ref"] = str(
                getattr(manager_projection, "manager_config_ref", "") or ""
            )
        if selected_stocks is not None:
            payload["selected_stocks"] = list(selected_stocks)
        if selection_mode:
            payload["selection_mode"] = str(selection_mode or "")
        if regime:
            payload["regime"] = str(regime or "unknown")
        if extra:
            payload.update(dict(extra))
        return payload

    @classmethod
    def _build_arm_error_payload(
        cls,
        *,
        arm_name: str,
        runtime_config_ref: str,
        exc: Exception,
    ) -> dict[str, Any]:
        return cls._build_arm_payload(
            status="error",
            arm_name=arm_name,
            runtime_config_ref=runtime_config_ref,
            extra={"error": str(exc)},
        )

    @classmethod
    def _build_arm_skip_payload(
        cls,
        *,
        status: str,
        arm_name: str,
        runtime_config_ref: str,
        manager_projection: Any,
        selected_stocks: list[str],
        selection_mode: str,
        regime: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return cls._build_arm_payload(
            status=status,
            arm_name=arm_name,
            runtime_config_ref=runtime_config_ref,
            manager_projection=manager_projection,
            selected_stocks=selected_stocks,
            selection_mode=selection_mode,
            regime=regime,
            extra=extra,
        )

    @staticmethod
    def _build_cycle_stub(
        *,
        cycle_id: int,
        sim_result: Any,
        selected_stocks: list[str],
    ) -> dict[str, Any]:
        return {
            "cycle_id": cycle_id,
            "return_pct": getattr(sim_result, "return_pct", 0.0),
            "profit_loss": getattr(sim_result, "total_pnl", 0.0),
            "total_trades": getattr(sim_result, "total_trades", 0),
            "winning_trades": getattr(sim_result, "winning_trades", 0),
            "losing_trades": getattr(sim_result, "losing_trades", 0),
            "win_rate": getattr(sim_result, "win_rate", 0.0),
            "selected_stocks": list(selected_stocks),
        }

    @staticmethod
    def _collect_daily_values(sim_result: Any) -> list[float]:
        return [
            float(row.get("total_value") or 0.0)
            for row in list(getattr(sim_result, "daily_records", []) or [])
            if isinstance(row, dict) and row.get("total_value") is not None
        ]

    @staticmethod
    def _strategy_overall_score(payload: dict[str, Any]) -> float:
        return float(dict(payload.get("strategy_scores") or {}).get("overall_score", 0.0) or 0.0)

    @staticmethod
    def _benchmark_score(payload: dict[str, Any]) -> float:
        return 1.0 if bool(payload.get("benchmark_passed", False)) else 0.0

    @staticmethod
    def _arm_metric(payload: dict[str, Any], key: str) -> float:
        return float(payload.get(key) or 0.0)

    @classmethod
    def _comparison_lifts(
        cls,
        *,
        active_arm: dict[str, Any],
        candidate_arm: dict[str, Any],
    ) -> dict[str, float]:
        active_return = cls._arm_metric(active_arm, "return_pct")
        candidate_return = cls._arm_metric(candidate_arm, "return_pct")
        active_score = cls._strategy_overall_score(active_arm)
        candidate_score = cls._strategy_overall_score(candidate_arm)
        active_benchmark = cls._benchmark_score(active_arm)
        candidate_benchmark = cls._benchmark_score(candidate_arm)
        active_win_rate = cls._arm_metric(active_arm, "win_rate")
        candidate_win_rate = cls._arm_metric(candidate_arm, "win_rate")
        return {
            "return_lift_pct": round(candidate_return - active_return, 4),
            "strategy_score_lift": round(candidate_score - active_score, 4),
            "benchmark_lift": round(candidate_benchmark - active_benchmark, 4),
            "win_rate_lift": round(candidate_win_rate - active_win_rate, 4),
        }

    @staticmethod
    def _winner_from_return_lift(return_lift: float) -> str:
        if return_lift > 0:
            return "candidate"
        if return_lift < 0:
            return "active"
        return "tie"

    @classmethod
    def _evaluate_benchmark_pass(
        cls,
        controller: Any,
        *,
        sim_result: Any,
        trade_dicts: list[dict[str, Any]],
        benchmark_daily_values: list[float],
    ) -> bool:
        daily_values = cls._collect_daily_values(sim_result)
        if len(daily_values) < 2:
            return False
        aligned_benchmark = (
            benchmark_daily_values
            if len(benchmark_daily_values) == len(daily_values)
            else None
        )
        benchmark_metrics = controller.benchmark_evaluator.evaluate(
            daily_values=daily_values,
            benchmark_daily_values=aligned_benchmark,
            trade_history=trade_dicts,
        )
        return bool(getattr(benchmark_metrics, "passed", False))

    @staticmethod
    def _build_strategy_scores(
        controller: Any,
        *,
        cycle_stub: dict[str, Any],
        trade_dicts: list[dict[str, Any]],
        sim_result: Any,
    ) -> dict[str, Any]:
        strategy_evaluator = controller.strategy_evaluator.__class__(
            policy=dict(getattr(controller.strategy_evaluator, "policy", {}) or {})
        )
        strategy_eval = strategy_evaluator.evaluate(
            cycle_stub,
            trade_history=trade_dicts,
            daily_records=list(getattr(sim_result, "daily_records", []) or []),
        )
        return dict(strategy_eval.to_dict())

    @classmethod
    def _build_arm_success_metrics(
        cls,
        *,
        controller: Any,
        cycle_id: int,
        sim_result: Any,
        trade_dicts: list[dict[str, Any]],
        benchmark_daily_values: list[float],
        selected_stocks: list[str],
    ) -> dict[str, Any]:
        benchmark_passed = cls._evaluate_benchmark_pass(
            controller,
            sim_result=sim_result,
            trade_dicts=trade_dicts,
            benchmark_daily_values=benchmark_daily_values,
        )
        cycle_stub = cls._build_cycle_stub(
            cycle_id=cycle_id,
            sim_result=sim_result,
            selected_stocks=selected_stocks,
        )
        return {
            "return_pct": round(float(sim_result.return_pct or 0.0), 4),
            "benchmark_passed": benchmark_passed,
            "trade_count": int(getattr(sim_result, "total_trades", 0) or 0),
            "final_value": round(float(getattr(sim_result, "final_value", 0.0) or 0.0), 4),
            "win_rate": round(float(getattr(sim_result, "win_rate", 0.0) or 0.0), 4),
            "strategy_scores": cls._build_strategy_scores(
                controller,
                cycle_stub=cycle_stub,
                trade_dicts=trade_dicts,
                sim_result=sim_result,
            ),
        }

    @classmethod
    def _build_arm_success_payload(
        cls,
        *,
        controller: Any,
        cycle_id: int,
        sim_result: Any,
        trade_dicts: list[dict[str, Any]],
        benchmark_daily_values: list[float],
        arm_name: str,
        runtime_config_ref: str,
        manager_projection: Any,
        selected_stocks: list[str],
        selection_mode: str,
        regime: str,
    ) -> dict[str, Any]:
        return cls._build_arm_payload(
            status="ok",
            arm_name=arm_name,
            runtime_config_ref=runtime_config_ref,
            manager_projection=manager_projection,
            selected_stocks=selected_stocks,
            selection_mode=selection_mode,
            regime=regime,
            extra=cls._build_arm_success_metrics(
                controller=controller,
                cycle_id=cycle_id,
                sim_result=sim_result,
                trade_dicts=trade_dicts,
                benchmark_daily_values=benchmark_daily_values,
                selected_stocks=selected_stocks,
            ),
        )

    @staticmethod
    def _normalize_ab_runtime_refs(
        *,
        active_runtime_config_ref: str,
        candidate_runtime_config_ref: str,
    ) -> tuple[str, str]:
        return (
            str(active_runtime_config_ref or "").strip(),
            str(candidate_runtime_config_ref or "").strip(),
        )

    def _evaluate_ab_arm(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
        manager_id: str,
        runtime_config_ref: str,
        arm_name: str,
        ) -> dict[str, Any]:
        return self._evaluate_arm(
            controller,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            stock_data=stock_data,
            manager_id=manager_id,
            runtime_config_ref=runtime_config_ref,
            arm_name=arm_name,
        )

    def _build_arm_selection_context(
        self,
        controller: Any,
        *,
        stock_data: dict[str, Any],
        manager_output: Any,
        manager_id: str,
        runtime_config_ref: str,
    ) -> dict[str, Any]:
        trading_plan = self._derive_trading_plan(manager_output)
        selected = self._selected_codes_from_plan(trading_plan)
        return {
            "manager_projection": self._project_arm_manager(
                controller,
                manager_output=manager_output,
                manager_id=manager_id,
                runtime_config_ref=runtime_config_ref,
            ),
            "trading_plan": trading_plan,
            "selected_stocks": selected,
            "selected_data": {
                code: stock_data[code] for code in selected if code in stock_data
            },
            "regime": str(
                getattr(manager_output.signal_packet, "regime", "unknown") or "unknown"
            ),
            "selection_mode": str(
                getattr(trading_plan, "source", "") or "ab_manager_adapter"
            ),
        }

    def _build_arm_simulation_context(
        self,
        controller: Any,
        *,
        cutoff_date: str,
        manager_runtime: Any,
        selected_data: dict[str, Any],
        trading_plan: Any,
    ) -> dict[str, Any]:
        trader = self._build_trader(
            controller,
            manager_runtime=manager_runtime,
            selected_data=selected_data,
            trading_plan=trading_plan,
        )
        simulation_days = max(
            1,
            int(
                controller.experiment_simulation_days
                or getattr(config, "simulation_days", 30)
            ),
        )
        trading_dates = controller.training_simulation_service.resolve_trading_dates(
            selected_data=selected_data,
            cutoff_date=cutoff_date,
            simulation_days=simulation_days,
        )
        benchmark_daily_values, market_index_frame = (
            controller.training_simulation_service.build_benchmark_context(
                controller,
                cutoff_date=cutoff_date,
                trading_dates=trading_dates,
            )
        )
        return {
            "trader": trader,
            "simulation_days": simulation_days,
            "trading_dates": trading_dates,
            "benchmark_daily_values": benchmark_daily_values,
            "market_index_frame": market_index_frame,
        }

    def _build_ab_result(
        self,
        *,
        cycle_id: int,
        cutoff_date: str,
        baseline_regime: str,
        active_arm: dict[str, Any],
        candidate_arm: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "enabled": True,
            "cycle_id": int(cycle_id),
            "cutoff_date": str(cutoff_date or ""),
            "market_regime": str(baseline_regime or ""),
            "active": active_arm,
            "candidate": candidate_arm,
            "comparison": self._build_ab_comparison(
                active_arm=active_arm,
                candidate_arm=candidate_arm,
            ),
        }

    def _evaluate_ab_pair(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
        manager_id: str,
        active_ref: str,
        candidate_ref: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return (
            self._evaluate_ab_arm(
                controller,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                stock_data=stock_data,
                manager_id=manager_id,
                runtime_config_ref=active_ref,
                arm_name="active",
            ),
            self._evaluate_ab_arm(
                controller,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                stock_data=stock_data,
                manager_id=manager_id,
                runtime_config_ref=candidate_ref,
                arm_name="candidate",
            ),
        )

    @staticmethod
    def _build_ab_comparison(
        *,
        active_arm: dict[str, Any],
        candidate_arm: dict[str, Any],
    ) -> dict[str, Any]:
        comparable = active_arm.get("status") == "ok" and candidate_arm.get("status") == "ok"
        comparison: dict[str, Any] = {
            "candidate_present": True,
            "comparable": comparable,
            "winner": "inconclusive",
            "return_lift_pct": None,
            "strategy_score_lift": None,
            "benchmark_lift": None,
            "win_rate_lift": None,
            "selection_overlap_ratio": _selection_overlap_ratio(
                list(active_arm.get("selected_stocks") or []),
                list(candidate_arm.get("selected_stocks") or []),
            ),
        }
        if not comparable:
            return comparison

        lifts = TrainingABService._comparison_lifts(
            active_arm=active_arm,
            candidate_arm=candidate_arm,
        )
        winner = TrainingABService._winner_from_return_lift(
            float(lifts["return_lift_pct"])
        )
        comparison.update(
            {
                "winner": winner,
                **lifts,
                "candidate_outperformed": bool(
                    float(lifts["return_lift_pct"]) >= 0.0
                    and float(lifts["benchmark_lift"]) >= 0.0
                    and float(lifts["strategy_score_lift"]) >= 0.0
                ),
            }
        )
        return comparison

    @staticmethod
    def _signal_packet_selected_codes(signal_packet: Any) -> list[str]:
        selected_codes = [
            str(code).strip()
            for code in list(getattr(signal_packet, "selected_codes", []) or [])
            if str(code).strip()
        ]
        if selected_codes:
            return selected_codes
        top_codes = getattr(signal_packet, "top_codes", None)
        if not callable(top_codes):
            return []
        raw_top_codes = top_codes(getattr(signal_packet, "max_positions", None))
        if not isinstance(raw_top_codes, (list, tuple, set)):
            return []
        return [
            str(code).strip()
            for code in list(raw_top_codes)
            if str(code).strip()
        ]

    @staticmethod
    def _signal_packet_reason(signal_packet: Any, agent_context: Any) -> str:
        return str(
            getattr(signal_packet, "reasoning", "")
            or getattr(agent_context, "summary", "")
            or "A/B manager-derived selection"
        )

    @staticmethod
    def _signal_packet_params(signal_packet: Any) -> tuple[dict[str, Any], float, float]:
        params = dict(getattr(signal_packet, "params", {}) or {})
        cash_reserve = _clamp_pct(
            getattr(signal_packet, "cash_reserve", 0.0),
            default=0.0,
        )
        available_weight = max(0.0, 1.0 - cash_reserve)
        return params, cash_reserve, available_weight

    @staticmethod
    def _default_position_weight(
        *,
        params: dict[str, Any],
        available_weight: float,
        selected_count: int,
    ) -> float:
        requested_position_size = _clamp_pct(
            params.get("position_size", COMMON_PARAM_DEFAULTS["position_size"]),
            default=COMMON_PARAM_DEFAULTS["position_size"],
        )
        if selected_count <= 0:
            return 0.0
        return min(requested_position_size, available_weight / selected_count)

    @staticmethod
    def _position_risk_value(
        signal: Any,
        params: dict[str, Any],
        *,
        attr: str,
        default_key: str,
    ) -> float:
        return _clamp_pct(
            getattr(signal, attr, None)
            if signal is not None
            else params.get(attr),
            default=float(params.get(default_key, COMMON_PARAM_DEFAULTS[default_key])),
        )

    @classmethod
    def _build_ab_position(
        cls,
        *,
        manager_output: Any,
        code: str,
        rank: int,
        signal: Any,
        params: dict[str, Any],
        available_weight: float,
        default_weight: float,
        default_reason: str,
    ) -> PositionPlan:
        hinted_weight = _clamp_pct(
            getattr(signal, "weight_hint", None),
            default=default_weight,
        )
        resolved_weight = min(
            available_weight,
            default_weight,
            EXECUTABLE_MAX_SINGLE_POSITION,
            hinted_weight,
        )
        return PositionPlan(
            code=code,
            priority=rank,
            weight=resolved_weight,
            entry_method="market",
            stop_loss_pct=cls._position_risk_value(
                signal,
                params,
                attr="stop_loss_pct",
                default_key="stop_loss_pct",
            ),
            take_profit_pct=cls._position_risk_value(
                signal,
                params,
                attr="take_profit_pct",
                default_key="take_profit_pct",
            ),
            trailing_pct=cls._position_risk_value(
                signal,
                params,
                attr="trailing_pct",
                default_key="trailing_pct",
            ),
            max_hold_days=int(
                params.get("max_hold_days", COMMON_PARAM_DEFAULTS["max_hold_days"])
                or COMMON_PARAM_DEFAULTS["max_hold_days"]
            ),
            reason=default_reason,
            source=str(manager_output_manager_id(manager_output) or "ab_manager_adapter"),
        )

    @staticmethod
    def _build_empty_ab_trading_plan(signal_packet: Any) -> TradingPlan:
        return TradingPlan(
            date=str(getattr(signal_packet, "as_of_date", "") or ""),
            positions=[],
            cash_reserve=1.0,
            max_positions=max(1, int(getattr(signal_packet, "max_positions", 1) or 1)),
            source="ab_manager_adapter",
            reasoning=str(getattr(signal_packet, "reasoning", "") or ""),
        )

    @classmethod
    def _derive_trading_plan(cls, manager_output: Any) -> TradingPlan:
        signal_packet = getattr(manager_output, "signal_packet", None)
        agent_context = getattr(manager_output, "agent_context", None)
        if signal_packet is None:
            raise ValueError("manager output missing signal_packet")

        selected_codes = cls._signal_packet_selected_codes(signal_packet)
        if not selected_codes:
            return cls._build_empty_ab_trading_plan(signal_packet)

        signals = {
            str(getattr(item, "code", "") or ""): item
            for item in list(getattr(signal_packet, "signals", []) or [])
        }
        params, cash_reserve, available_weight = cls._signal_packet_params(signal_packet)
        default_weight = cls._default_position_weight(
            params=params,
            available_weight=available_weight,
            selected_count=len(selected_codes),
        )
        default_reason = cls._signal_packet_reason(signal_packet, agent_context)

        positions = [
            cls._build_ab_position(
                manager_output=manager_output,
                code=code,
                rank=rank,
                signal=signals.get(code),
                params=params,
                available_weight=available_weight,
                default_weight=default_weight,
                default_reason=default_reason,
            )
            for rank, code in enumerate(selected_codes, start=1)
        ]
        effective_cash_reserve = max(
            cash_reserve,
            round(
                max(
                    0.0,
                    1.0 - sum(float(position.weight or 0.0) for position in positions),
                ),
                8,
            ),
        )
        return TradingPlan(
            date=str(getattr(signal_packet, "as_of_date", "") or ""),
            positions=positions,
            cash_reserve=effective_cash_reserve,
            max_positions=max(
                1,
                int(
                    getattr(signal_packet, "max_positions", len(positions))
                    or len(positions)
                    or 1
                ),
            ),
            source="ab_manager_adapter",
            reasoning=default_reason,
        )

    def _build_trader(
        self,
        controller: Any,
        *,
        manager_runtime: Any,
        selected_data: dict[str, Any],
        trading_plan: Any,
    ) -> SimulatedTrader:
        runtime_owner = SimpleNamespace(
            execution_policy={
                **dict(getattr(controller, "execution_policy", {}) or {}),
                "initial_capital": float(
                    manager_runtime.execution_param(
                        "initial_capital",
                        getattr(
                            config,
                            "initial_capital",
                            COMMON_EXECUTION_DEFAULTS["initial_capital"],
                        ),
                    )
                    or getattr(
                        config,
                        "initial_capital",
                        COMMON_EXECUTION_DEFAULTS["initial_capital"],
                    )
                ),
                "commission_rate": float(
                    manager_runtime.execution_param(
                        "commission_rate",
                        COMMON_EXECUTION_DEFAULTS["commission_rate"],
                    )
                    or COMMON_EXECUTION_DEFAULTS["commission_rate"]
                ),
                "stamp_tax_rate": float(
                    manager_runtime.execution_param(
                        "stamp_tax_rate",
                        COMMON_EXECUTION_DEFAULTS["stamp_tax_rate"],
                    )
                    or COMMON_EXECUTION_DEFAULTS["stamp_tax_rate"]
                ),
                "slippage_rate": float(
                    manager_runtime.execution_param(
                        "slippage_rate",
                        COMMON_EXECUTION_DEFAULTS["slippage_rate"],
                    )
                    or COMMON_EXECUTION_DEFAULTS["slippage_rate"]
                ),
            },
            current_params={
                **dict(session_current_params(controller) or {}),
                "position_size": float(
                    manager_runtime.param(
                        "position_size",
                        COMMON_PARAM_DEFAULTS["position_size"],
                    )
                    or COMMON_PARAM_DEFAULTS["position_size"]
                ),
            },
            risk_policy={
                **dict(getattr(controller, "risk_policy", {}) or {}),
                **dict(manager_runtime.config_section("risk", {}) or {}),
                "stop_loss_pct": float(manager_runtime.risk_param("stop_loss_pct") or 0.0),
                "take_profit_pct": float(manager_runtime.risk_param("take_profit_pct") or 0.0),
                "trailing_pct": manager_runtime.risk_param("trailing_pct"),
            },
        )
        return controller.training_simulation_service.build_trader(
            runtime_owner,
            selected_data=selected_data,
            trading_plan=trading_plan,
        )

    def _evaluate_arm(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
        manager_id: str,
        runtime_config_ref: str,
        arm_name: str,
    ) -> dict[str, Any]:
        try:
            manager_runtime = build_manager_runtime(
                manager_id=manager_id,
                manager_config_ref=runtime_config_ref,
                runtime_overrides={},
            )
        except Exception as exc:
            logger.warning(
                "A/B arm init failed for %s (%s): %s",
                arm_name,
                runtime_config_ref,
                exc,
            )
            return self._build_arm_error_payload(
                arm_name=arm_name,
                runtime_config_ref=runtime_config_ref,
                exc=exc,
            )

        try:
            manager_output = manager_runtime.process(stock_data, cutoff_date)
            arm_context = self._build_arm_selection_context(
                controller,
                stock_data=stock_data,
                manager_output=manager_output,
                manager_id=manager_id,
                runtime_config_ref=runtime_config_ref,
            )
            manager_projection = arm_context["manager_projection"]
            trading_plan = arm_context["trading_plan"]
            selected = arm_context["selected_stocks"]
            selected_data = arm_context["selected_data"]
            regime = str(arm_context["regime"] or "unknown")
            selection_mode = str(arm_context["selection_mode"] or "ab_manager_adapter")
            if not selected or not selected_data:
                return self._build_arm_skip_payload(
                    status="no_selection",
                    arm_name=arm_name,
                    runtime_config_ref=runtime_config_ref,
                    manager_projection=manager_projection,
                    selected_stocks=selected,
                    selection_mode=str(getattr(trading_plan, "source", "") or "ab_manager_adapter_empty"),
                    regime=regime,
                )

            simulation_context = self._build_arm_simulation_context(
                controller,
                cutoff_date=cutoff_date,
                manager_runtime=manager_runtime,
                selected_data=selected_data,
                trading_plan=trading_plan,
            )
            trader = simulation_context["trader"]
            simulation_days = int(simulation_context["simulation_days"] or 0)
            trading_dates = list(simulation_context["trading_dates"] or [])
            if len(trading_dates) < simulation_days:
                return self._build_arm_skip_payload(
                    status="insufficient_future_days",
                    arm_name=arm_name,
                    runtime_config_ref=runtime_config_ref,
                    manager_projection=manager_projection,
                    selected_stocks=selected,
                    selection_mode=selection_mode,
                    regime=regime,
                    extra={
                        "available_trading_days": len(trading_dates),
                        "required_trading_days": simulation_days,
                    },
                )

            benchmark_daily_values = list(
                simulation_context["benchmark_daily_values"] or []
            )
            market_index_frame = simulation_context["market_index_frame"]
            if market_index_frame is not None and not market_index_frame.empty:
                trader.set_market_index_data(market_index_frame)
            sim_result = trader.run_simulation(trading_dates[0], trading_dates)
            trade_dicts = controller.training_simulation_service.build_trade_dicts(sim_result)
            return self._build_arm_success_payload(
                controller=controller,
                cycle_id=cycle_id,
                sim_result=sim_result,
                trade_dicts=trade_dicts,
                benchmark_daily_values=benchmark_daily_values,
                arm_name=arm_name,
                runtime_config_ref=runtime_config_ref,
                manager_projection=manager_projection,
                selected_stocks=selected,
                selection_mode=selection_mode,
                regime=regime,
            )
        except Exception as exc:
            logger.warning(
                "A/B arm execution failed for %s (%s): %s",
                arm_name,
                runtime_config_ref,
                exc,
            )
            return self._build_arm_error_payload(
                arm_name=arm_name,
                runtime_config_ref=runtime_config_ref,
                exc=exc,
            )

    def run_candidate_ab_comparison(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
        manager_id: str,
        active_runtime_config_ref: str,
        candidate_runtime_config_ref: str,
        baseline_regime: str = "",
    ) -> dict[str, Any]:
        active_ref, candidate_ref = self._normalize_ab_runtime_refs(
            active_runtime_config_ref=active_runtime_config_ref,
            candidate_runtime_config_ref=candidate_runtime_config_ref,
        )
        if not active_ref or not candidate_ref or active_ref == candidate_ref:
            return {}

        active_arm, candidate_arm = self._evaluate_ab_pair(
            controller,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            stock_data=stock_data,
            manager_id=manager_id,
            active_ref=active_ref,
            candidate_ref=candidate_ref,
        )
        return self._build_ab_result(
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            baseline_regime=baseline_regime,
            active_arm=active_arm,
            candidate_arm=candidate_arm,
        )
