"""Training controller, session state, and cycle data orchestration."""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING

import numpy as np

from invest_evolution.config import config, normalize_date


def _policy_module():
    from invest_evolution.application.training import policy as _policy

    return _policy


def governance_from_item(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _policy_module().governance_from_item(*args, **kwargs)


def governance_regime(*args: Any, **kwargs: Any) -> str:
    return _policy_module().governance_regime(*args, **kwargs)


def governance_from_controller(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _policy_module().governance_from_controller(*args, **kwargs)


def normalize_governance_decision(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _policy_module().normalize_governance_decision(*args, **kwargs)


def execution_defaults_payload(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _policy_module().execution_defaults_payload(*args, **kwargs)


@dataclass
class TrainingSessionState:
    current_params: dict[str, Any] = field(default_factory=dict)
    consecutive_losses: int = 0
    default_manager_id: str = ""
    default_manager_config_ref: str = ""
    manager_budget_weights: dict[str, float] = field(default_factory=dict)
    last_governance_decision: dict[str, Any] = field(default_factory=dict)
    last_feedback_optimization: dict[str, Any] = field(default_factory=dict)
    last_feedback_optimization_cycle_id: int = 0
    cycle_history: list[Any] = field(default_factory=list)
    cycle_records: list[dict[str, Any]] = field(default_factory=list)


def resolve_training_session_state(controller: Any) -> TrainingSessionState | None:
    state = getattr(controller, "session_state", None)
    return state if isinstance(state, TrainingSessionState) else None


def session_current_params(controller: Any) -> dict[str, Any]:
    state = resolve_training_session_state(controller)
    if state is not None:
        return state.current_params
    value = getattr(controller, "current_params", {})
    return value if isinstance(value, dict) else {}


def set_session_current_params(controller: Any, value: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(value or {})
    state = resolve_training_session_state(controller)
    if state is not None:
        state.current_params = payload
    else:
        setattr(controller, "current_params", payload)
    return payload


def update_session_current_params(controller: Any, value: dict[str, Any] | None) -> dict[str, Any]:
    current = dict(session_current_params(controller))
    current.update(dict(value or {}))
    return set_session_current_params(controller, current)


def session_consecutive_losses(controller: Any) -> int:
    state = resolve_training_session_state(controller)
    if state is not None:
        return int(state.consecutive_losses)
    return int(getattr(controller, "consecutive_losses", 0) or 0)


def set_session_consecutive_losses(controller: Any, value: int) -> int:
    normalized = int(value or 0)
    state = resolve_training_session_state(controller)
    if state is not None:
        state.consecutive_losses = normalized
    else:
        setattr(controller, "consecutive_losses", normalized)
    return normalized


def increment_session_consecutive_losses(controller: Any, step: int = 1) -> int:
    return set_session_consecutive_losses(
        controller,
        session_consecutive_losses(controller) + int(step or 0),
    )


def session_default_manager_id(controller: Any) -> str:
    state = resolve_training_session_state(controller)
    if state is not None:
        return str(state.default_manager_id or "")
    return str(getattr(controller, "default_manager_id", "") or "")


def session_default_manager_config_ref(controller: Any) -> str:
    state = resolve_training_session_state(controller)
    if state is not None:
        return str(state.default_manager_config_ref or "")
    return str(getattr(controller, "default_manager_config_ref", "") or "")


def set_session_default_manager(
    controller: Any,
    *,
    manager_id: str,
    manager_config_ref: str,
) -> None:
    normalized_manager_id = str(manager_id or "")
    normalized_config_ref = str(manager_config_ref or "")
    state = resolve_training_session_state(controller)
    if state is not None:
        state.default_manager_id = normalized_manager_id
        state.default_manager_config_ref = normalized_config_ref
    else:
        setattr(controller, "default_manager_id", normalized_manager_id)
        setattr(controller, "default_manager_config_ref", normalized_config_ref)


def session_manager_budget_weights(controller: Any) -> dict[str, float]:
    state = resolve_training_session_state(controller)
    if state is not None:
        return state.manager_budget_weights
    value = getattr(controller, "manager_budget_weights", {})
    return value if isinstance(value, dict) else {}


def set_session_manager_budget_weights(
    controller: Any,
    value: dict[str, float] | None,
) -> dict[str, float]:
    payload = {
        str(key): float(weight)
        for key, weight in dict(value or {}).items()
    }
    state = resolve_training_session_state(controller)
    if state is not None:
        state.manager_budget_weights = payload
    else:
        setattr(controller, "manager_budget_weights", payload)
    return payload


def session_last_governance_decision(controller: Any) -> dict[str, Any]:
    state = resolve_training_session_state(controller)
    if state is not None:
        return state.last_governance_decision
    value = getattr(controller, "last_governance_decision", {})
    return value if isinstance(value, dict) else {}


def set_session_last_governance_decision(
    controller: Any,
    value: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(value or {})
    state = resolve_training_session_state(controller)
    if state is not None:
        state.last_governance_decision = payload
    else:
        setattr(controller, "last_governance_decision", payload)
    return payload


def session_last_feedback_optimization(controller: Any) -> dict[str, Any]:
    state = resolve_training_session_state(controller)
    if state is not None:
        return state.last_feedback_optimization
    value = getattr(controller, "last_feedback_optimization", {})
    return value if isinstance(value, dict) else {}


def set_session_last_feedback_optimization(
    controller: Any,
    value: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(value or {})
    state = resolve_training_session_state(controller)
    if state is not None:
        state.last_feedback_optimization = payload
    else:
        setattr(controller, "last_feedback_optimization", payload)
    return payload


def session_last_feedback_optimization_cycle_id(controller: Any) -> int:
    state = resolve_training_session_state(controller)
    if state is not None:
        return int(state.last_feedback_optimization_cycle_id)
    return int(getattr(controller, "last_feedback_optimization_cycle_id", 0) or 0)


def set_session_last_feedback_optimization_cycle_id(controller: Any, value: int) -> int:
    normalized = int(value or 0)
    state = resolve_training_session_state(controller)
    if state is not None:
        state.last_feedback_optimization_cycle_id = normalized
    else:
        setattr(controller, "last_feedback_optimization_cycle_id", normalized)
    return normalized


def session_cycle_history(controller: Any) -> list[Any]:
    state = resolve_training_session_state(controller)
    if state is not None:
        return state.cycle_history
    value = getattr(controller, "cycle_history", [])
    return value if isinstance(value, list) else []


def set_session_cycle_history(controller: Any, value: list[Any] | None) -> list[Any]:
    payload = list(value or [])
    state = resolve_training_session_state(controller)
    if state is not None:
        state.cycle_history = payload
    else:
        setattr(controller, "cycle_history", payload)
    return payload


def session_cycle_records(controller: Any) -> list[dict[str, Any]]:
    state = resolve_training_session_state(controller)
    if state is not None:
        return state.cycle_records
    value = getattr(controller, "cycle_records", [])
    return value if isinstance(value, list) else []


def set_session_cycle_records(
    controller: Any,
    value: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    payload = [dict(item) for item in list(value or [])]
    state = resolve_training_session_state(controller)
    if state is not None:
        state.cycle_records = payload
    else:
        setattr(controller, "cycle_records", payload)
    return payload


def append_session_cycle_record(controller: Any, record: dict[str, Any] | None) -> None:
    records = session_cycle_records(controller)
    normalized = dict(record or {})
    if resolve_training_session_state(controller) is not None:
        records.append(normalized)
        return
    updated = list(records)
    updated.append(normalized)
    setattr(controller, "cycle_records", updated)


@dataclass(frozen=True)
class TrainingCycleContext:
    cycle_id: int
    cutoff_date: str
    requested_data_mode: str
    llm_mode: str
    cutoff_policy_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrainingDataLoadResult:
    diagnostics: dict[str, Any]
    stock_data: dict[str, Any]
    requested_data_mode: str
    effective_data_mode: str
    data_mode: str
    degraded: bool
    degrade_reason: str
    min_history_days: int


class TrainingCycleDataService:
    """Owns cutoff resolution and the data-loading phase of a cycle."""

    @staticmethod
    def _cycle_regime(item: Any) -> str:
        audit_tags = dict(getattr(item, "audit_tags", {}) or {})
        return governance_regime(
            governance_from_item(item),
            default=str(
                audit_tags.get("governance_regime")
                or getattr(item, "regime", "")
                or "unknown"
            ),
        )

    def _resolve_regime_coverage(
        self,
        controller: Any,
        *,
        policy: dict[str, Any],
    ) -> dict[str, int]:
        target_regimes = list(policy.get("target_regimes") or []) or [
            "bull",
            "bear",
            "oscillation",
        ]
        coverage = {str(regime): 0 for regime in target_regimes}
        min_regime_samples = max(0, int(policy.get("min_regime_samples") or 0))
        for item in list(session_cycle_history(controller) or []):
            regime = self._cycle_regime(item)
            if regime in coverage:
                coverage[regime] += 1
        if min_regime_samples <= 0:
            return coverage
        return {
            regime: min(int(count), min_regime_samples)
            for regime, count in coverage.items()
        }

    @staticmethod
    def _resolve_fallback_cutoff(
        controller: Any,
        *,
        cycle_id: int,
        min_date: str,
        max_date: str,
        fallback_mode: str,
        policy: dict[str, Any],
    ) -> str:
        if fallback_mode == "fixed":
            fixed = str(
                policy.get("date") or policy.get("cutoff_date") or max_date or min_date
            )
            return normalize_date(fixed)
        if fallback_mode == "sequence":
            dates = [
                normalize_date(str(item))
                for item in list(policy.get("dates") or [])
                if str(item or "").strip()
            ]
            if dates:
                return dates[min(max(cycle_id - 1, 0), len(dates) - 1)]
        if fallback_mode == "rolling":
            anchor = normalize_date(str(policy.get("anchor_date") or min_date))
            step_days = max(
                1,
                int(
                    policy.get("step_days")
                    or getattr(controller, "experiment_simulation_days", 30)
                    or 30
                ),
            )
            candidate_dt = datetime.strptime(anchor, "%Y%m%d") + timedelta(
                days=step_days * max(cycle_id - 1, 0)
            )
            candidate = candidate_dt.strftime("%Y%m%d")
            if max_date:
                candidate = min(candidate, normalize_date(max_date))
            return candidate
        return normalize_date(
            controller.data_manager.random_cutoff_date(
                min_date=min_date,
                max_date=max_date or None,
            )
        )

    def _resolve_regime_balanced_cutoff(
        self,
        controller: Any,
        *,
        cycle_id: int,
        min_date: str,
        max_date: str,
        policy: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        target_regimes = list(policy.get("target_regimes") or []) or [
            "bull",
            "bear",
            "oscillation",
        ]
        probe_count = max(3, int(policy.get("probe_count") or 9))
        coverage = self._resolve_regime_coverage(controller, policy=policy)
        undercovered = sorted(
            coverage.items(),
            key=lambda item: (
                int(item[1]),
                target_regimes.index(item[0]) if item[0] in target_regimes else 999,
            ),
        )
        target_regime = str(undercovered[0][0] if undercovered else target_regimes[0])
        sampled_dates: list[str] = []
        if list(policy.get("dates") or []):
            sampled_dates = [
                normalize_date(str(item))
                for item in list(policy.get("dates") or [])
                if str(item or "").strip()
            ]
        while len(sampled_dates) < probe_count:
            sampled_dates.append(
                normalize_date(
                    controller.data_manager.random_cutoff_date(
                        min_date=min_date,
                        max_date=max_date or None,
                    )
                )
            )

        cache = dict(getattr(controller, "_regime_probe_cache", {}) or {})
        probe_results: list[dict[str, Any]] = []
        for sample_date in sampled_dates[:probe_count]:
            cached = dict(cache.get(sample_date) or {})
            if not cached:
                try:
                    preview = controller.training_governance_service.preview_governance(
                        controller,
                        cutoff_date=sample_date,
                        stock_count=getattr(config, "max_stocks", 50),
                        min_history_days=(
                            controller.experiment_min_history_days
                            or getattr(config, "min_history_days", 200)
                        ),
                        allowed_manager_ids=(
                            getattr(controller, "experiment_allowed_manager_ids", [])
                            or None
                        ),
                    )
                    cached = {
                        "cutoff_date": sample_date,
                        "regime": str(preview.get("regime") or "unknown"),
                        "confidence": float(
                            preview.get("regime_confidence")
                            or preview.get("confidence")
                            or 0.0
                        ),
                    }
                except Exception as exc:
                    cached = {
                        "cutoff_date": sample_date,
                        "regime": "unknown",
                        "confidence": 0.0,
                        "error": str(exc),
                    }
                cache[sample_date] = dict(cached)
            probe_results.append(dict(cached))
        controller._regime_probe_cache = dict(cache)

        target_candidates = [
            item
            for item in probe_results
            if str(item.get("regime") or "") == target_regime
        ]
        if target_candidates:
            selected_probe = max(
                target_candidates,
                key=lambda item: float(item.get("confidence") or 0.0),
            )
            return normalize_date(str(selected_probe.get("cutoff_date") or "")), {
                "mode": "regime_balanced",
                "target_regime": target_regime,
                "coverage": coverage,
                "probe_count": probe_count,
                "selected_by": "target_regime_match",
                "probes": probe_results,
            }

        fallback_mode = (
            str(policy.get("fallback_mode") or "random").strip().lower() or "random"
        )
        fallback_cutoff = self._resolve_fallback_cutoff(
            controller,
            cycle_id=cycle_id,
            min_date=min_date,
            max_date=max_date,
            fallback_mode=fallback_mode,
            policy=policy,
        )
        return fallback_cutoff, {
            "mode": "regime_balanced",
            "target_regime": target_regime,
            "coverage": coverage,
            "probe_count": probe_count,
            "selected_by": f"fallback:{fallback_mode}",
            "probes": probe_results,
        }

    def _resolve_cutoff(
        self,
        controller: Any,
        *,
        cycle_id: int,
    ) -> tuple[str, dict[str, Any]]:
        policy = dict(getattr(controller, "experiment_cutoff_policy", {}) or {})
        mode = str(policy.get("mode") or "random").strip().lower() or "random"
        min_date = str(getattr(controller, "experiment_min_date", None) or "20180101")
        max_date = str(getattr(controller, "experiment_max_date", None) or "")

        if mode == "fixed":
            fixed = str(
                policy.get("date") or policy.get("cutoff_date") or max_date or min_date
            )
            return normalize_date(fixed), {"mode": mode}

        if mode == "sequence":
            dates = [
                normalize_date(str(item))
                for item in list(policy.get("dates") or [])
                if str(item or "").strip()
            ]
            if dates:
                return dates[min(max(cycle_id - 1, 0), len(dates) - 1)], {
                    "mode": mode,
                    "dates": dates,
                }

        if mode == "rolling":
            anchor = normalize_date(str(policy.get("anchor_date") or min_date))
            step_days = max(
                1,
                int(
                    policy.get("step_days")
                    or getattr(controller, "experiment_simulation_days", 30)
                    or 30
                ),
            )
            candidate_dt = datetime.strptime(anchor, "%Y%m%d") + timedelta(
                days=step_days * max(cycle_id - 1, 0)
            )
            candidate = candidate_dt.strftime("%Y%m%d")
            if max_date:
                candidate = min(candidate, normalize_date(max_date))
            return candidate, {"mode": mode, "anchor_date": anchor, "step_days": step_days}

        if mode == "regime_balanced":
            return self._resolve_regime_balanced_cutoff(
                controller,
                cycle_id=cycle_id,
                min_date=min_date,
                max_date=max_date,
                policy=policy,
            )

        return normalize_date(
            controller.data_manager.random_cutoff_date(
                min_date=min_date,
                max_date=max_date or None,
            )
        ), {"mode": "random"}

    def prepare_cycle_context(self, controller: Any) -> TrainingCycleContext:
        cycle_id = int(controller.current_cycle_id) + 1
        if controller.experiment_seed is not None:
            seed_value = int(controller.experiment_seed) + cycle_id
            random.seed(seed_value)
            np.random.seed(seed_value % (2**32 - 1))

        resolved_cutoff, cutoff_policy_context = self._resolve_cutoff(
            controller,
            cycle_id=cycle_id,
        )
        controller.last_cutoff_policy_context = dict(cutoff_policy_context or {})
        cutoff_date = normalize_date(
            os.getenv("INVEST_FORCE_CUTOFF_DATE", "") or resolved_cutoff
        )
        requested_data_mode = str(
            getattr(
                controller,
                "requested_data_mode",
                getattr(controller.data_manager, "requested_mode", "live"),
            )
            or "live"
        )
        llm_mode = str(getattr(controller, "llm_mode", "live") or "live")
        return TrainingCycleContext(
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            requested_data_mode=requested_data_mode,
            llm_mode=llm_mode,
            cutoff_policy_context=dict(
                getattr(controller, "last_cutoff_policy_context", {}) or {}
            ),
        )

    def load_training_data(
        self,
        controller: Any,
        *,
        cutoff_date: str,
        requested_data_mode: str,
    ) -> TrainingDataLoadResult:
        min_history_days = max(
            30,
            int(
                controller.experiment_min_history_days
                or getattr(config, "min_history_days", 200)
            ),
        )
        diagnostics = self._resolve_diagnostics(
            controller,
            cutoff_date=cutoff_date,
            min_history_days=min_history_days,
        )
        stock_data = controller.data_manager.load_stock_data(
            cutoff_date,
            stock_count=config.max_stocks,
            min_history_days=min_history_days,
            include_future_days=max(
                30,
                int(
                    controller.experiment_simulation_days
                    or getattr(config, "simulation_days", 30)
                ),
            ),
        )
        resolution = dict(getattr(controller.data_manager, "last_resolution", {}) or {})
        effective_data_mode = str(
            resolution.get("effective_data_mode")
            or getattr(controller.data_manager, "last_source", "unknown")
            or "unknown"
        )
        degrade_reason = str(resolution.get("degrade_reason") or "")
        degraded = bool(resolution.get("degraded", False))
        return TrainingDataLoadResult(
            diagnostics=diagnostics,
            stock_data=stock_data,
            requested_data_mode=requested_data_mode,
            effective_data_mode=effective_data_mode,
            data_mode=effective_data_mode,
            degraded=degraded,
            degrade_reason=degrade_reason,
            min_history_days=min_history_days,
        )

    def _resolve_diagnostics(
        self,
        controller: Any,
        *,
        cutoff_date: str,
        min_history_days: int,
    ) -> dict[str, Any]:
        diagnostic_order = ["check_training_readiness", "diagnose_training_data"]
        if callable(
            getattr(controller.data_manager, "__dict__", {}).get(
                "diagnose_training_data"
            )
        ):
            diagnostic_order = ["diagnose_training_data", "check_training_readiness"]

        diagnostics: dict[str, Any] | None = None
        for method_name in diagnostic_order:
            method = getattr(controller.data_manager, method_name, None)
            if not callable(method):
                continue
            raw_diagnostics = method(
                cutoff_date=cutoff_date,
                stock_count=config.max_stocks,
                min_history_days=min_history_days,
            )
            if isinstance(raw_diagnostics, dict):
                diagnostics = dict(raw_diagnostics)
                break
        return diagnostics or {}


__all__ = [
    "TrainingCycleContext",
    "TrainingDataLoadResult",
    "TrainingCycleDataService",
]

logger = logging.getLogger(__name__)


class TrainingLLMRuntimeService:
    """Coordinates controller-wide LLM runtime settings."""

    @staticmethod
    def _append_component_llms(
        targets: list[Any],
        component: Any,
        *attribute_names: str,
    ) -> None:
        if component is None:
            return
        for attribute_name in attribute_names:
            llm = getattr(component, attribute_name, None)
            if llm is not None:
                targets.append(llm)

    @staticmethod
    def _apply_runtime_payload(llms: list[Any], *, payload: dict[str, Any]) -> None:
        timeout = payload.get("timeout")
        max_retries = payload.get("max_retries")
        dry_run = payload.get("dry_run")
        for llm in llms:
            if (
                timeout is not None or max_retries is not None
            ) and hasattr(llm, "apply_runtime_limits"):
                llm.apply_runtime_limits(
                    timeout=timeout,
                    max_retries=max_retries,
                )
            if dry_run is not None and hasattr(llm, "dry_run"):
                llm.dry_run = bool(dry_run)

    @staticmethod
    def _iter_unique_llms(controller: Any) -> list[Any]:
        targets = [getattr(controller, "llm_caller", None)]
        for agent in dict(getattr(controller, "agents", {}) or {}).values():
            llm = getattr(agent, "llm", None)
            if llm is not None:
                targets.append(llm)
        llm_optimizer = getattr(controller, "llm_optimizer", None)
        TrainingLLMRuntimeService._append_component_llms(targets, llm_optimizer, "llm")

        seen: set[int] = set()
        unique_targets: list[Any] = []
        for llm in targets:
            if llm is None or id(llm) in seen:
                continue
            seen.add(id(llm))
            unique_targets.append(llm)
        return unique_targets

    def apply_experiment_overrides(
        self,
        controller: Any,
        llm_spec: dict[str, Any] | None = None,
    ) -> None:
        payload = dict(llm_spec or {})
        self._apply_runtime_payload(self._iter_unique_llms(controller), payload=payload)

    def set_dry_run(self, controller: Any, enabled: bool = True) -> None:
        dry_run = bool(enabled)
        controller.llm_mode = "dry_run" if dry_run else "live"
        self._apply_runtime_payload(
            self._iter_unique_llms(controller),
            payload={"dry_run": dry_run},
        )


class TrainingLifecycleService:
    """Owns cycle completion bookkeeping and continuous-run lifecycle control."""

    @staticmethod
    def _refresh_leaderboards(controller: Any) -> None:
        refresh = getattr(
            getattr(controller, "training_persistence_service", None),
            "refresh_leaderboards",
            None,
        )
        if not callable(refresh):
            return
        try:
            refresh(controller)
        except Exception as exc:
            logger.warning(
                "Final leaderboard refresh failed: cycle_id=%s error=%s",
                int(getattr(controller, "current_cycle_id", 0) or 0),
                exc,
                exc_info=True,
            )
            event_emitter = getattr(controller, "_emit_runtime_event", None)
            if callable(event_emitter):
                event_emitter(
                    "warning",
                    {
                        "cycle_id": int(getattr(controller, "current_cycle_id", 0) or 0),
                        "severity": "warning",
                        "risk_level": "medium",
                        "message": "final leaderboard refresh failed",
                        "error": str(exc),
                    },
                )

    @staticmethod
    def _apply_success_target_metadata(
        report: dict[str, Any],
        *,
        controller: Any,
        successful_cycles_target: int | None,
    ) -> dict[str, Any]:
        payload = dict(report or {})
        if successful_cycles_target is None:
            return payload
        payload["successful_cycles_target"] = int(successful_cycles_target)
        cycle_history = list(session_cycle_history(controller) or [])
        payload["target_met"] = len(cycle_history) >= int(successful_cycles_target)
        return payload

    def finalize_cycle(
        self,
        controller: Any,
        *,
        cycle_result: Any,
        assessment_payload: dict[str, Any],
        cycle_id: int,
        cutoff_date: str,
        sim_result: Any,
        is_profit: bool,
        selected: list[str],
        trade_dicts: list[dict[str, Any]],
        review_applied: bool,
        selection_mode: str,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        research_feedback: dict[str, Any] | None,
    ) -> None:
        resolved_assessment_payload = dict(assessment_payload or {})
        cycle_history = session_cycle_history(controller)
        cycle_history.append(cycle_result)
        controller.current_cycle_id += 1
        from invest_evolution.application.training.observability import (
            SelfAssessmentSnapshot,
        )

        controller.training_persistence_service.record_self_assessment(
            controller,
            SelfAssessmentSnapshot,
            cycle_result,
            resolved_assessment_payload,
        )
        freeze_gate_evaluation = controller.freeze_gate_service.evaluate_freeze_gate(
            controller
        )
        portfolio_plan = dict(getattr(cycle_result, "portfolio_plan", {}) or {})
        subject_type = str(
            dict(getattr(cycle_result, "run_context", {}) or {}).get("subject_type")
            or (
                "manager_portfolio"
                if (
                    selection_mode == "manager_portfolio"
                    or portfolio_plan
                )
                else "single_manager"
            )
        )
        active_manager_ids = list(portfolio_plan.get("active_manager_ids") or [])
        governance_decision = normalize_governance_decision(
            dict(getattr(cycle_result, "governance_decision", {}) or {}),
            fallback=governance_from_controller(controller),
        )
        from invest_evolution.application.training.execution import (
            project_cycle_payload_manager_compatibility,
        )

        lifecycle_projection = project_cycle_payload_manager_compatibility(
            None,
            cycle_payload={
                "governance_decision": governance_decision,
                "dominant_manager_id": str(
                    getattr(cycle_result, "dominant_manager_id", "") or ""
                ),
                "manager_results": list(
                    getattr(cycle_result, "manager_results", []) or []
                ),
                "portfolio_plan": portfolio_plan,
                "execution_snapshot": dict(
                    getattr(cycle_result, "execution_snapshot", {}) or {}
                ),
                "execution_defaults": dict(
                    getattr(cycle_result, "execution_defaults", {}) or {}
                ),
            },
        )
        resolved_execution_defaults = execution_defaults_payload(
            governance_decision,
            portfolio_plan=portfolio_plan,
            manager_results=list(getattr(cycle_result, "manager_results", []) or []),
            execution_snapshot=dict(
                getattr(cycle_result, "execution_snapshot", {}) or {}
            ),
            fallback={
                **dict(getattr(cycle_result, "execution_defaults", {}) or {}),
                **dict(lifecycle_projection.execution_defaults or {}),
            },
        )
        resolved_dominant_manager_id = str(
            lifecycle_projection.dominant_manager_id
            or lifecycle_projection.manager_id
            or getattr(cycle_result, "dominant_manager_id", "")
            or ""
        )

        controller.last_cycle_meta = {
            "status": "ok",
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "return_pct": sim_result.return_pct,
            "requested_data_mode": requested_data_mode,
            "effective_data_mode": effective_data_mode,
            "llm_mode": llm_mode,
            "degraded": degraded,
            "degrade_reason": degrade_reason,
            "governance_decision": dict(governance_decision),
            "research_feedback": controller._research_feedback_brief(research_feedback),
            "research_feedback_optimization": dict(
                session_last_feedback_optimization(controller) or {}
            ),
            "freeze_gate_evaluation": dict(freeze_gate_evaluation or {}),
            "validation_status": str(
                dict(getattr(cycle_result, "validation_summary", {}) or {}).get(
                    "status"
                )
                or ""
            ),
            "judge_decision": str(
                dict(getattr(cycle_result, "judge_report", {}) or {}).get("decision")
                or ""
            ),
            "shadow_mode": bool(
                dict(getattr(cycle_result, "validation_report", {}) or {}).get(
                    "shadow_mode",
                    False,
                )
            ),
            "subject_type": subject_type,
            "dominant_manager_id": resolved_dominant_manager_id,
            "execution_defaults": resolved_execution_defaults,
            "active_manager_ids": active_manager_ids,
            "portfolio_selected_count": len(list(portfolio_plan.get("positions") or [])),
            "timestamp": datetime.now().isoformat(),
        }
        controller.training_persistence_service.save_cycle_result(controller, cycle_result)

        from invest_evolution.application.training.observability import emit_event

        event_emitter = getattr(controller, "_emit_runtime_event", emit_event)
        event_emitter(
            "cycle_complete",
            {
                "cycle_id": cycle_id,
                "cutoff_date": cutoff_date,
                "return_pct": sim_result.return_pct,
                "is_profit": bool(is_profit),
                "selected_count": len(selected),
                "selected_stocks": selected[:10],
                "trade_count": len(trade_dicts),
                "final_value": sim_result.final_value,
                "review_applied": review_applied,
                "selection_mode": selection_mode,
                "requested_data_mode": requested_data_mode,
                "effective_data_mode": effective_data_mode,
                "llm_mode": llm_mode,
                "degraded": degraded,
                "degrade_reason": degrade_reason,
                "governance_decision": dict(governance_decision),
                "subject_type": subject_type,
                "dominant_manager_id": resolved_dominant_manager_id,
                "execution_defaults": resolved_execution_defaults,
                "active_manager_ids": active_manager_ids,
                "portfolio_selected_count": len(list(portfolio_plan.get("positions") or [])),
                "timestamp": datetime.now().isoformat(),
            },
        )
        controller._emit_module_log(
            "cycle_complete",
            f"周期 #{cycle_id} 完成",
            (
                f"收益 {sim_result.return_pct:+.2f}% ，共 {len(list(portfolio_plan.get('positions') or []))} 只组合持仓"
                if subject_type == "manager_portfolio"
                else f"收益 {sim_result.return_pct:+.2f}% ，共 {len(selected)} 只选股"
            ),
            cycle_id=cycle_id,
            kind="cycle_complete",
            details={
                "selected_stocks": selected[:10],
                "trade_count": len(trade_dicts),
                "review_applied": review_applied,
                "requested_data_mode": requested_data_mode,
                "effective_data_mode": effective_data_mode,
                "llm_mode": llm_mode,
                "degraded": degraded,
                "degrade_reason": degrade_reason,
                "subject_type": subject_type,
                "active_manager_ids": active_manager_ids,
            },
            metrics={
                "return_pct": sim_result.return_pct,
                "selected_count": len(selected),
                "trade_count": len(trade_dicts),
            },
        )
        if controller.on_cycle_complete:
            controller.on_cycle_complete(cycle_result)

        logger.info(
            "\n周期 #%s 完成: 收益率 %.2f%%, %s",
            cycle_id,
            sim_result.return_pct,
            "盈利" if is_profit else "亏损",
        )

    def run_continuous(
        self,
        controller: Any,
        *,
        max_cycles: int = 100,
        successful_cycles_target: int | None = None,
    ) -> dict[str, Any]:
        normalized_target = (
            max(1, int(successful_cycles_target))
            if successful_cycles_target is not None
            else None
        )
        logger.info("\n%s", "#" * 60)
        if normalized_target is None:
            logger.info("开始持续训练 (最多 %s 个周期)", max_cycles)
        else:
            logger.info(
                "开始持续训练 (最多 %s 次尝试，目标成功周期 %s)",
                max_cycles,
                normalized_target,
            )
        logger.info("%s", "#" * 60)

        starting_attempts = int(getattr(controller, "total_cycle_attempts", 0) or 0)
        starting_skips = int(getattr(controller, "skipped_cycle_count", 0) or 0)
        attempt_index = 0
        skipped_in_run = 0
        while attempt_index < max_cycles:
            cycle_history = session_cycle_history(controller)
            if normalized_target is not None and len(cycle_history) >= normalized_target:
                break
            if controller.freeze_gate_service.should_freeze(controller):
                break
            successful_before = len(cycle_history)
            previous_research_feedback = dict(
                getattr(controller, "last_research_feedback", {}) or {}
            )
            result = controller.run_training_cycle()
            attempt_index += 1
            controller.total_cycle_attempts = starting_attempts + attempt_index

            successful_after = len(session_cycle_history(controller))
            if result is None and successful_after <= successful_before:
                skipped_in_run += 1
                controller.skipped_cycle_count = starting_skips + skipped_in_run
                if previous_research_feedback:
                    controller.last_research_feedback = previous_research_feedback

        freeze_gate_service = controller.freeze_gate_service
        if hasattr(freeze_gate_service, "build_final_report"):
            final_report = freeze_gate_service.build_final_report(controller)
        else:
            final_report = freeze_gate_service.generate_training_report(controller)
        final_report = self._apply_success_target_metadata(
            final_report,
            controller=controller,
            successful_cycles_target=normalized_target,
        )
        self._refresh_leaderboards(controller)
        return final_report
if TYPE_CHECKING:
    from invest_evolution.application.training.execution import (
        OutcomeStageContext,
        ReviewStageContext,
        SelectionStageContext,
        SimulationStageContext,
        TrainingExecutionService,
        ValidationStageContext,
    )

_EXECUTION_EXPORTS = {
    "OutcomeStageContext",
    "ReviewStageContext",
    "SelectionStageContext",
    "SimulationStageContext",
    "TrainingExecutionService",
    "ValidationStageContext",
}

def __getattr__(name: str):
    if name in _EXECUTION_EXPORTS:
        from invest_evolution.application.training import execution as _execution

        value = getattr(_execution, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "OutcomeStageContext",
    "ReviewStageContext",
    "SelectionStageContext",
    "SimulationStageContext",
    "TrainingCycleContext",
    "TrainingCycleDataService",
    "TrainingDataLoadResult",
    "TrainingExecutionService",
    "TrainingLifecycleService",
    "TrainingLLMRuntimeService",
    "ValidationStageContext",
]
