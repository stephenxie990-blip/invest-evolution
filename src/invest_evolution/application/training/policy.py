"""Merged training module: policy.py."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from invest_evolution.application.training.controller import (
    session_current_params,
    session_default_manager_config_ref,
    session_default_manager_id,
    session_last_governance_decision,
    set_session_current_params,
    set_session_default_manager,
    set_session_last_governance_decision,
    set_session_manager_budget_weights,
)
from invest_evolution.config import (
    EFFECTIVE_RUNTIME_MODE,
    OUTPUT_DIR,
    RUNTIME_CONTRACT_VERSION,
    config,
    normalize_date,
    normalize_manager_active_ids,
    normalize_manager_budget_weights,
)
from invest_evolution.investment.foundation.metrics import BenchmarkEvaluator
from invest_evolution.investment.foundation.risk import sanitize_risk_params
from invest_evolution.investment.governance import (
    GovernanceCoordinator,
    write_leaderboard,
)
from invest_evolution.investment.managers.registry import (
    canonical_manager_config_ref as canonical_registry_manager_config_ref,
    looks_like_manager_config_ref,
    normalize_manager_config_ref,
)
from invest_evolution.investment.runtimes import create_manager_runtime
from invest_evolution.investment.runtimes.catalog import COMMON_BENCHMARK_DEFAULTS
from invest_evolution.investment.shared.policy import (
    deep_merge,
    normalize_freeze_gate_policy,
    normalize_promotion_gate_policy,
    resolve_governance_matrix,
)

logger = logging.getLogger(__name__)

_DEFAULT_RUNTIME_MANAGER_ID = "momentum"
_DEFAULT_RUNTIME_MANAGER_CONFIG_REF = (
    "src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml"
)


def normalize_manager_id(value: Any, *, default: str = "momentum") -> str:
    manager_id = str(value or "").strip()
    return manager_id or default


def resolve_manager_config_ref(
    manager_id: Any,
    manager_config_ref: Any = None,
) -> str:
    manager_ref = str(manager_config_ref or "").strip()
    if manager_ref and looks_like_manager_config_ref(manager_ref):
        return manager_ref
    return canonical_registry_manager_config_ref(
        normalize_manager_id(manager_id),
        manager_config_ref,
    )


def build_manager_runtime(
    *,
    manager_id: Any,
    manager_config_ref: Any = None,
    runtime_overrides: dict[str, Any] | None = None,
) -> Any:
    resolved_manager_id = normalize_manager_id(manager_id)
    resolved_manager_config_ref = resolve_manager_config_ref(
        resolved_manager_id,
        manager_config_ref,
    )
    runtime = create_manager_runtime(
        resolved_manager_id,
        runtime_config_ref=resolved_manager_config_ref,
        runtime_overrides=dict(runtime_overrides or {}),
    )
    setattr(runtime, "manager_id", resolved_manager_id)
    setattr(runtime, "manager_config_ref", resolved_manager_config_ref)
    return runtime


def controller_default_manager_id(controller: Any, *, default: str = "momentum") -> str:
    return normalize_manager_id(session_default_manager_id(controller), default=default)


def controller_default_manager_config_ref(controller: Any) -> str:
    return resolve_manager_config_ref(
        controller_default_manager_id(controller),
        session_default_manager_config_ref(controller),
    )


def _owner_config_value(owner: Any | None, key: str, default: Any) -> Any:
    if owner is None:
        return default
    return getattr(owner, key, default)


def _config_projection_value(owner: Any | None, key: str, default: Any) -> Any:
    return getattr(config, key, _owner_config_value(owner, key, default))


def _coalesced_projection_value(owner: Any | None, key: str, default: Any) -> Any:
    owner_value = _owner_config_value(owner, key, default)
    return _config_projection_value(owner, key, owner_value) or owner_value or default


def _bool_projection_value(owner: Any | None, key: str, default: bool = False) -> bool:
    return bool(_config_projection_value(owner, key, default))


def _int_projection_value(owner: Any | None, key: str, default: int) -> int:
    return int(_coalesced_projection_value(owner, key, default))


def _float_projection_value(owner: Any | None, key: str, default: float) -> float:
    return float(_coalesced_projection_value(owner, key, default))


def _string_projection_value(owner: Any | None, key: str, default: str) -> str:
    return str(_coalesced_projection_value(owner, key, default))


def _manager_ids_projection_value(owner: Any | None, key: str) -> list[str]:
    return normalize_manager_active_ids(_config_projection_value(owner, key, []))


def _budget_weights_projection_value(owner: Any | None) -> dict[str, float]:
    return normalize_manager_budget_weights(
        _config_projection_value(owner, "manager_budget_weights", {})
    )


def _default_runtime_manager_projection(owner: Any | None) -> dict[str, str]:
    owner_manager_id = (
        controller_default_manager_id(owner)
        if owner is not None
        else _DEFAULT_RUNTIME_MANAGER_ID
    )
    owner_manager_config_ref = (
        controller_default_manager_config_ref(owner)
        if owner is not None
        else _DEFAULT_RUNTIME_MANAGER_CONFIG_REF
    )
    return {
        "default_manager_id": str(
            getattr(config, "default_manager_id", owner_manager_id) or owner_manager_id
        ),
        "default_manager_config_ref": str(
            getattr(config, "default_manager_config_ref", owner_manager_config_ref)
            or owner_manager_config_ref
        ),
    }


def _apply_runtime_projection(controller: Any, projection: dict[str, Any]) -> None:
    for key, value in projection.items():
        if key in {"default_manager_id", "default_manager_config_ref"}:
            continue
        setattr(controller, key, value)


def _manager_runtime_selection_changed(
    *,
    previous_manager_id: str,
    previous_manager_config_ref: str,
    next_manager_id: str,
    next_manager_config_ref: str,
) -> bool:
    return (
        previous_manager_id != next_manager_id
        or previous_manager_config_ref != next_manager_config_ref
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return int(text)


def _optional_normalized_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return normalize_date(text)


def _normalize_allowed_manager_ids(value: Any) -> list[str]:
    return [str(item).strip() for item in list(value or []) if str(item).strip()]


def _normalize_regime_targets(value: Any) -> list[str]:
    allowed = {"bull", "bear", "oscillation"}
    targets: list[str] = []
    for item in list(value or []):
        normalized = str(item or "").strip().lower()
        if normalized in allowed and normalized not in targets:
            targets.append(normalized)
    return targets


def _normalize_config_ref(value: Any) -> str:
    return normalize_manager_config_ref(value)


def _canonical_manager_config_ref_for_manager(
    manager_id: str,
    manager_config_ref: Any,
) -> str:
    return canonical_registry_manager_config_ref(
        manager_id,
        manager_config_ref,
    )


def _finite_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_benchmark_criteria(
    value: Any,
    *,
    default: Any = None,
) -> dict[str, float]:
    normalized: dict[str, float] = {}
    if isinstance(default, dict):
        for key, item in default.items():
            if isinstance(key, str) and key.strip():
                parsed = _finite_float(item)
                if parsed is not None:
                    normalized[key] = float(parsed)
    if not isinstance(value, dict):
        return normalized
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            continue
        parsed = _finite_float(item)
        if parsed is None:
            continue
        normalized[key] = float(parsed)
    return normalized


def _normalize_benchmark_risk_free_rate(value: Any, *, default: Any) -> float:
    parsed = _finite_float(value)
    if parsed is not None:
        return float(parsed)
    fallback = _finite_float(default)
    if fallback is not None:
        return float(fallback)
    return 0.03


def normalize_review_window(value: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(value or {})
    mode = str(payload.get("mode") or "single_cycle").strip().lower() or "single_cycle"
    if mode not in {"single_cycle", "rolling"}:
        mode = "single_cycle"
    size = _optional_int(payload.get("size") or payload.get("window")) or 1
    if mode == "single_cycle":
        size = 1
    return {
        "mode": mode,
        "size": max(1, size),
    }


def normalize_cutoff_policy(value: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(value or {})
    mode = str(payload.get("mode") or "random").strip().lower() or "random"
    if mode not in {"random", "fixed", "rolling", "sequence", "regime_balanced"}:
        mode = "random"
    dates = [
        normalize_date(str(item))
        for item in list(payload.get("dates") or [])
        if str(item or "").strip()
    ]
    anchor_date = str(payload.get("anchor_date") or "").strip()
    fixed_date = str(payload.get("date") or payload.get("cutoff_date") or "").strip()
    normalized = {
        "mode": mode,
        "date": normalize_date(fixed_date) if fixed_date else "",
        "anchor_date": normalize_date(anchor_date) if anchor_date else "",
        "step_days": max(
            1,
            _optional_int(payload.get("step_days") or payload.get("window_days")) or 30,
        ),
        "dates": dates,
    }
    if mode == "regime_balanced":
        fallback_mode = (
            str(payload.get("fallback_mode") or "random").strip().lower() or "random"
        )
        if fallback_mode not in {"random", "rolling", "sequence", "fixed"}:
            fallback_mode = "random"
        normalized.update(
            {
                "probe_count": max(3, _optional_int(payload.get("probe_count")) or 9),
                "min_regime_samples": max(
                    0, _optional_int(payload.get("min_regime_samples")) or 0
                ),
                "target_regimes": _normalize_regime_targets(
                    payload.get("target_regimes") or []
                ),
                "fallback_mode": fallback_mode,
            }
        )
    return normalized


@dataclass(frozen=True)
class ExperimentSpec:
    payload: dict[str, Any] = field(default_factory=dict)
    seed: int | None = None
    llm_mode: str = "live"
    review_window: dict[str, Any] = field(
        default_factory=lambda: {"mode": "single_cycle", "size": 1}
    )
    cutoff_policy: dict[str, Any] = field(
        default_factory=lambda: {
            "mode": "random",
            "date": "",
            "anchor_date": "",
            "step_days": 30,
            "dates": [],
        }
    )
    promotion_policy: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None = None) -> "ExperimentSpec":
        raw = dict(payload or {})
        spec = dict(raw.get("spec") or {})
        protocol = dict(raw.get("protocol") or {})
        dataset = dict(raw.get("dataset") or {})
        manager_scope = dict(raw.get("manager_scope") or {})
        optimization = dict(raw.get("optimization") or {})
        llm = dict(raw.get("llm") or {})

        date_range = dict(protocol.get("date_range") or {})
        normalized_seed = _optional_int(protocol.get("seed"))
        normalized_review_window = normalize_review_window(
            dict(protocol.get("review_window") or {})
        )
        normalized_cutoff_policy = normalize_cutoff_policy(
            dict(protocol.get("cutoff_policy") or {})
        )
        normalized_promotion_policy = dict(
            protocol.get("promotion_policy") or optimization.get("promotion_gate") or {}
        )
        llm_mode = (
            "dry_run"
            if bool(llm.get("dry_run"))
            else str(llm.get("mode") or "live").strip() or "live"
        )

        normalized_payload = {
            "spec": deepcopy(spec),
            "protocol": {
                **protocol,
                "seed": normalized_seed,
                "date_range": {
                    "min": _optional_normalized_date(
                        date_range.get("min") or protocol.get("min_date")
                    ),
                    "max": _optional_normalized_date(
                        date_range.get("max") or protocol.get("max_date")
                    ),
                },
                "review_window": normalized_review_window,
                "cutoff_policy": normalized_cutoff_policy,
                "promotion_policy": deepcopy(normalized_promotion_policy),
            },
            "dataset": {
                **dataset,
                "min_history_days": _optional_int(dataset.get("min_history_days")),
                "simulation_days": _optional_int(dataset.get("simulation_days")),
            },
            "manager_scope": {
                **manager_scope,
                "allowed_manager_ids": _normalize_allowed_manager_ids(
                    manager_scope.get("allowed_manager_ids") or []
                ),
            },
            "optimization": deepcopy(optimization),
            "llm": {
                **llm,
                "mode": llm_mode,
            },
        }
        return cls(
            payload=normalized_payload,
            seed=normalized_seed,
            llm_mode=llm_mode,
            review_window=normalized_review_window,
            cutoff_policy=normalized_cutoff_policy,
            promotion_policy=normalized_promotion_policy,
        )

    def to_payload(self) -> dict[str, Any]:
        return deepcopy(self.payload)


class TrainingPolicyService:
    """Synchronizes runtime policies from the active manager runtime."""

    @staticmethod
    def policy_lookup(policy: dict[str, Any] | None, path: str, default: Any) -> Any:
        current: Any = dict(policy or {})
        for key in path.split("."):
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return current

    def sanitize_runtime_param_adjustments(
        self,
        controller: Any,
        adjustments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = dict(adjustments or {})
        risk_like = {
            key: float(value)
            for key, value in normalized.items()
            if key in {"stop_loss_pct", "take_profit_pct", "position_size"}
            and value is not None
        }
        clean = sanitize_risk_params(risk_like, policy=controller.risk_policy)
        clamp_policy = dict(
            self.policy_lookup(controller.review_policy, "param_clamps", {}) or {}
        )
        cash_bounds = dict(
            clamp_policy.get("cash_reserve") or {"min": 0.0, "max": 0.80}
        )
        trailing_bounds = dict(
            clamp_policy.get("trailing_pct") or {"min": 0.03, "max": 0.20}
        )
        max_hold_bounds = dict(
            clamp_policy.get("max_hold_days") or {"min": 5, "max": 60}
        )
        signal_threshold_bounds = dict(
            clamp_policy.get("signal_threshold") or {"min": 0.30, "max": 0.95}
        )
        if normalized.get("cash_reserve") is not None:
            clean["cash_reserve"] = max(
                float(cash_bounds.get("min", 0.0)),
                min(
                    float(cash_bounds.get("max", 0.80)),
                    float(normalized["cash_reserve"]),
                ),
            )
        if normalized.get("trailing_pct") is not None:
            clean["trailing_pct"] = max(
                float(trailing_bounds.get("min", 0.03)),
                min(
                    float(trailing_bounds.get("max", 0.20)),
                    float(normalized["trailing_pct"]),
                ),
            )
        if normalized.get("max_hold_days") is not None:
            clean["max_hold_days"] = int(
                max(
                    int(max_hold_bounds.get("min", 5)),
                    min(
                        int(max_hold_bounds.get("max", 60)),
                        int(round(float(normalized["max_hold_days"]))),
                    ),
                )
            )
        if normalized.get("signal_threshold") is not None:
            clean["signal_threshold"] = round(
                max(
                    float(signal_threshold_bounds.get("min", 0.30)),
                    min(
                        float(signal_threshold_bounds.get("max", 0.95)),
                        float(normalized["signal_threshold"]),
                    ),
                ),
                4,
            )
        return clean

    def sync_runtime_policy(self, controller: Any) -> None:
        if getattr(controller, "manager_runtime", None) is None:
            return

        manager_runtime = controller.manager_runtime
        config_params = manager_runtime.config_section("params", {})
        merged_params = dict(controller.DEFAULT_PARAMS)
        merged_params.update(config_params or {})
        current_params = session_current_params(controller)
        explicit_overrides = {
            key: value
            for key, value in current_params.items()
            if key not in controller.DEFAULT_PARAMS
            or value != controller.DEFAULT_PARAMS.get(key)
        }
        merged_params.update(explicit_overrides)
        manager_runtime.update_runtime_overrides(
            set_session_current_params(controller, merged_params)
        )

        controller.execution_policy = (
            manager_runtime.config_section("execution", {}) or {}
        )
        controller.risk_policy = manager_runtime.config_section("risk_policy", {}) or {}
        controller.evaluation_policy = (
            manager_runtime.config_section("evaluation_policy", {}) or {}
        )
        controller.review_policy = (
            manager_runtime.config_section("review_policy", {}) or {}
        )
        controller.strategy_evaluator.set_policy(controller.evaluation_policy)

        raw_benchmark_policy = manager_runtime.config_section("benchmark", {})
        benchmark_policy = (
            raw_benchmark_policy if isinstance(raw_benchmark_policy, dict) else {}
        )
        default_criteria = COMMON_BENCHMARK_DEFAULTS.get("criteria")
        benchmark_criteria = _normalize_benchmark_criteria(
            benchmark_policy.get("criteria"),
            default=default_criteria,
        )
        controller.benchmark_evaluator = BenchmarkEvaluator(
            risk_free_rate=_normalize_benchmark_risk_free_rate(
                benchmark_policy.get("risk_free_rate"),
                default=COMMON_BENCHMARK_DEFAULTS.get("risk_free_rate", 0.03),
            ),
            criteria=benchmark_criteria,
        )

        controller.train_policy = manager_runtime.config_section("train", {}) or {}
        controller.freeze_total_cycles = int(
            controller.train_policy.get(
                "freeze_total_cycles", controller.freeze_total_cycles
            )
            or controller.freeze_total_cycles
        )
        controller.freeze_profit_required = int(
            controller.train_policy.get(
                "freeze_profit_required", controller.freeze_profit_required
            )
            or controller.freeze_profit_required
        )
        controller.max_losses_before_optimize = int(
            controller.train_policy.get(
                "max_losses_before_optimize",
                controller.max_losses_before_optimize,
            )
            or controller.max_losses_before_optimize
        )
        controller.freeze_gate_policy = normalize_freeze_gate_policy(
            dict(controller.train_policy.get("freeze_gate") or {})
        )
        controller.promotion_gate_policy = normalize_promotion_gate_policy(
            dict(controller.train_policy.get("promotion_gate") or {})
        )
        controller.quality_gate_matrix = resolve_governance_matrix(
            dict(controller.train_policy.get("quality_gate_matrix") or {})
        )
        if not dict(getattr(controller, "experiment_promotion_policy", {}) or {}):
            controller.experiment_promotion_policy = dict(
                controller.promotion_gate_policy
            )
        controller.auto_apply_mutation = bool(
            controller.train_policy.get("auto_apply_mutation", False)
        )
        controller.research_feedback_policy = dict(
            controller.train_policy.get("research_feedback", {}) or {}
        )
        controller.research_feedback_optimization_policy = dict(
            controller.research_feedback_policy.get("optimization", {}) or {}
        )
        controller.research_feedback_freeze_policy = dict(
            controller.research_feedback_policy.get("freeze_gate", {})
            or controller.freeze_gate_policy.get("research_feedback", {})
            or {}
        )
        if controller.research_feedback_freeze_policy:
            controller.freeze_gate_policy["research_feedback"] = deep_merge(
                dict(controller.freeze_gate_policy.get("research_feedback", {}) or {}),
                controller.research_feedback_freeze_policy,
            )

        agent_weights = manager_runtime.config_section("agent_weights", {}) or {}
        if agent_weights:
            controller.selection_agent_weights = {
                "trend_hunter": float(agent_weights.get("trend_hunter", 1.0) or 1.0),
                "contrarian": float(agent_weights.get("contrarian", 1.0) or 1.0),
            }


def runtime_config_projection_from_live_config(
    current: Any | None = None,
) -> dict[str, Any]:
    owner = current
    projection = _default_runtime_manager_projection(owner)
    allocator_enabled = _bool_projection_value(owner, "allocator_enabled")
    return {
        **projection,
        "allocator_enabled": allocator_enabled,
        "allocator_top_n": _int_projection_value(owner, "allocator_top_n", 3),
        "manager_arch_enabled": _bool_projection_value(owner, "manager_arch_enabled"),
        "manager_shadow_mode": _bool_projection_value(owner, "manager_shadow_mode"),
        "manager_allocator_enabled": _bool_projection_value(
            owner, "manager_allocator_enabled"
        ),
        "portfolio_assembly_enabled": _bool_projection_value(
            owner, "portfolio_assembly_enabled"
        ),
        "dual_review_enabled": _bool_projection_value(owner, "dual_review_enabled"),
        "manager_persistence_enabled": _bool_projection_value(
            owner, "manager_persistence_enabled"
        ),
        "manager_active_ids": _manager_ids_projection_value(
            owner, "manager_active_ids"
        ),
        "manager_budget_weights": _budget_weights_projection_value(owner),
        "governance_enabled": bool(
            _bool_projection_value(owner, "governance_enabled") or allocator_enabled
        ),
        "governance_mode": _string_projection_value(owner, "governance_mode", "rule")
        .strip()
        .lower(),
        "governance_allowed_manager_ids": _manager_ids_projection_value(
            owner,
            "governance_allowed_manager_ids",
        ),
        "governance_cooldown_cycles": _int_projection_value(
            owner,
            "governance_cooldown_cycles",
            2,
        ),
        "governance_min_confidence": _float_projection_value(
            owner,
            "governance_min_confidence",
            0.60,
        ),
        "governance_hysteresis_margin": _float_projection_value(
            owner,
            "governance_hysteresis_margin",
            0.08,
        ),
        "governance_agent_override_enabled": _bool_projection_value(
            owner,
            "governance_agent_override_enabled",
        ),
        "governance_agent_override_max_gap": _float_projection_value(
            owner,
            "governance_agent_override_max_gap",
            0.18,
        ),
        "governance_policy": dict(
            _config_projection_value(owner, "governance_policy", {}) or {}
        ),
        "effective_runtime_mode": EFFECTIVE_RUNTIME_MODE,
        "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
    }


class TrainingGovernanceService:
    """Coordinates leaderboard refresh and governance decisions."""

    @staticmethod
    def _resolve_shadow_mode(owner: Any | None) -> bool:
        payload = dict(getattr(owner, "experiment_protocol", {}) or {})
        if not payload:
            payload = dict(getattr(owner, "experiment_spec", {}) or {})
        if "shadow_mode" in payload:
            return bool(payload.get("shadow_mode"))
        protocol = dict(payload.get("protocol") or {})
        if "shadow_mode" in protocol:
            return bool(protocol.get("shadow_mode"))
        if owner is not None and hasattr(owner, "manager_shadow_mode"):
            return bool(getattr(owner, "manager_shadow_mode"))
        return bool(getattr(config, "manager_shadow_mode", False))

    def sync_runtime_from_config(self, controller: Any) -> None:
        previous_manager_id = controller_default_manager_id(controller)
        previous_manager_config_ref = controller_default_manager_config_ref(controller)
        projection = runtime_config_projection_from_live_config(controller)
        next_manager_id = str(
            projection["default_manager_id"]
            or controller_default_manager_id(controller)
        )
        next_manager_config_ref = str(
            projection["default_manager_config_ref"]
            or controller_default_manager_config_ref(controller)
        )
        set_session_default_manager(
            controller,
            manager_id=next_manager_id,
            manager_config_ref=next_manager_config_ref,
        )
        _apply_runtime_projection(controller, projection)
        self.refresh_governance_coordinator(controller)
        if _manager_runtime_selection_changed(
            previous_manager_id=previous_manager_id,
            previous_manager_config_ref=previous_manager_config_ref,
            next_manager_id=next_manager_id,
            next_manager_config_ref=next_manager_config_ref,
        ):
            set_session_current_params(controller, {})
            self.reload_manager_runtime(controller, next_manager_config_ref)

    def reload_manager_runtime(
        self, controller: Any, runtime_config_ref: str | None = None
    ) -> None:
        if runtime_config_ref:
            set_session_default_manager(
                controller,
                manager_id=controller_default_manager_id(controller),
                manager_config_ref=str(runtime_config_ref),
            )
        controller.manager_runtime = build_manager_runtime(
            manager_id=controller_default_manager_id(controller),
            manager_config_ref=controller_default_manager_config_ref(controller),
            runtime_overrides=session_current_params(controller),
        )
        controller._sync_runtime_policy_from_manager_runtime()

    def build_governance_coordinator(self, owner: Any) -> GovernanceCoordinator:
        return GovernanceCoordinator(
            governance_policy=dict(getattr(owner, "governance_policy", {}) or {}),
            min_confidence=float(
                getattr(owner, "governance_min_confidence", 0.60) or 0.60
            ),
            cooldown_cycles=int(getattr(owner, "governance_cooldown_cycles", 2) or 2),
            hysteresis_margin=float(
                getattr(owner, "governance_hysteresis_margin", 0.08) or 0.08
            ),
            agent_override_max_gap=float(
                getattr(owner, "governance_agent_override_max_gap", 0.18) or 0.18
            ),
        )

    def refresh_governance_coordinator(self, owner: Any) -> GovernanceCoordinator:
        coordinator = self.build_governance_coordinator(owner)
        owner.governance_coordinator = coordinator
        return coordinator

    def prepare_leaderboard(
        self, *, output_dir: str | Path | None, safe: bool = False
    ) -> Path:
        leaderboard_root = Path(output_dir or OUTPUT_DIR)
        if leaderboard_root.name == "training":
            leaderboard_root = leaderboard_root.parent
        leaderboard_root.mkdir(parents=True, exist_ok=True)
        leaderboard_path = leaderboard_root / "leaderboard.json"
        if safe:
            try:
                write_leaderboard(leaderboard_root, leaderboard_path)
            except Exception as exc:
                logger.warning(
                    "Leaderboard refresh failed in safe mode: path=%s error=%s",
                    leaderboard_path,
                    exc,
                    exc_info=True,
                )
        else:
            write_leaderboard(leaderboard_root, leaderboard_path)
        return leaderboard_path

    def preview_governance(
        self,
        controller: Any,
        *,
        cutoff_date: str | None = None,
        stock_count: int | None = None,
        min_history_days: int | None = None,
        allowed_manager_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        preview_cutoff = normalize_date(
            cutoff_date or controller.data_manager.random_cutoff_date()
        )
        preview_stock_count = max(
            1, int(stock_count or getattr(config, "max_stocks", 50) or 50)
        )
        preview_min_history = max(
            30, int(min_history_days or getattr(config, "min_history_days", 200) or 200)
        )
        stock_data = controller.data_manager.load_stock_data(
            cutoff_date=preview_cutoff,
            stock_count=preview_stock_count,
            min_history_days=preview_min_history,
        )
        decision = self.decide_governance(
            controller,
            stock_data=stock_data,
            cutoff_date=preview_cutoff,
            current_manager_id=str(
                dominant_manager_id(governance_from_controller(controller))
                or controller_default_manager_id(controller)
                or "momentum"
            ),
            data_manager=controller.data_manager,
            output_dir=getattr(controller, "output_dir", OUTPUT_DIR),
            allowed_manager_ids=allowed_manager_ids,
            current_cycle_id=int(getattr(controller, "current_cycle_id", 0) or 0) + 1,
        )
        return decision.to_dict()

    def apply_governance(
        self,
        controller: Any,
        *,
        stock_data: dict[str, Any],
        cutoff_date: str,
        cycle_id: int,
        event_emitter: Any,
    ) -> None:
        if not controller.governance_enabled or controller.governance_mode == "off":
            return
        controller._emit_agent_status(
            "Governance",
            "running",
            "正在评估市场状态并更新经理激活与预算分配...",
            cycle_id=cycle_id,
            stage="governance",
            progress_pct=22,
            step=2,
            total_steps=6,
        )
        event_emitter(
            "governance_started",
            {
                **controller._event_context(cycle_id),
                "dominant_manager_id": (
                    dominant_manager_id(governance_from_controller(controller))
                    or controller_default_manager_id(controller)
                ),
                "governance_mode": controller.governance_mode,
            },
        )
        decision = self.decide_governance(
            controller,
            stock_data=stock_data,
            cutoff_date=cutoff_date,
            current_manager_id=(
                dominant_manager_id(governance_from_controller(controller))
                or controller_default_manager_id(controller)
            ),
            data_manager=controller.data_manager,
            output_dir=controller.output_dir,
            allowed_manager_ids=controller.experiment_allowed_manager_ids
            or controller.governance_allowed_manager_ids,
            current_cycle_id=cycle_id,
        )
        governance_payload = normalize_governance_decision(decision.to_dict())

        # Default to multi-manager semantics unless the controller explicitly disables them.
        manager_arch_enabled = bool(getattr(controller, "manager_arch_enabled", True))
        dominant_manager = dominant_manager_id(governance_payload)

        # If multi-manager architecture is disabled, clamp the governance payload itself
        # to a single canonical subject. This prevents "single_manager" runs from
        # silently executing portfolio mixing due to governance defaults.
        if not manager_arch_enabled:
            single = dominant_manager or controller_default_manager_id(controller)
            governance_payload = {
                **dict(governance_payload),
                "active_manager_ids": [single] if single else [],
                "manager_budget_weights": ({single: 1.0} if single else {}),
                "dominant_manager_id": single,
                "metadata": {
                    **dict(governance_payload.get("metadata") or {}),
                    "subject_type": "single_manager",
                    "clamped": True,
                    "clamped_reason": "manager_arch_disabled",
                },
            }
            dominant_manager = single

        # Persist the normalized (and possibly clamped) governance decision.
        set_session_last_governance_decision(controller, governance_payload)
        controller.governance_history.append(dict(governance_payload))
        controller.last_allocation_plan = dict(decision.allocation_plan or {})
        active_manager_ids = list(governance_payload.get("active_manager_ids") or [])
        raw_manager_budget_weights = governance_payload.get("manager_budget_weights")
        manager_budget_weights = (
            dict(raw_manager_budget_weights)
            if isinstance(raw_manager_budget_weights, dict)
            else {}
        )
        controller.manager_active_ids = active_manager_ids
        normalized_budget_weights = {
            str(key): float(value) for key, value in manager_budget_weights.items()
        }
        set_session_manager_budget_weights(controller, normalized_budget_weights)
        controller.portfolio_assembly_enabled = bool(manager_arch_enabled)
        event_emitter(
            "regime_classified",
            {
                **controller._event_context(cycle_id),
                "regime": decision.regime,
                "confidence": decision.regime_confidence,
                "source": decision.regime_source,
                "reasoning": (decision.evidence.get("rule_result") or {}).get(
                    "reasoning"
                )
                or decision.reasoning,
            },
        )
        event_emitter(
            "manager_activation_decided",
            {
                **controller._event_context(cycle_id),
                "regime": decision.regime,
                "regime_confidence": decision.regime_confidence,
                "decision_confidence": decision.decision_confidence,
                "decision_source": decision.decision_source,
                "active_manager_ids": list(active_manager_ids),
                "manager_budget_weights": dict(manager_budget_weights),
                "dominant_manager_id": dominant_manager,
                "cash_reserve_hint": decision.cash_reserve_hint,
                "portfolio_constraints": dict(
                    getattr(decision, "portfolio_constraints", {}) or {}
                ),
                "reasoning": decision.reasoning,
                "guardrail_checks": decision.guardrail_checks,
            },
        )
        historical = (
            dict(getattr(decision, "metadata", {}) or {}).get("historical") or {}
        )
        if bool(historical.get("guardrail_hold", False)):
            event_emitter(
                "governance_blocked",
                {
                    **controller._event_context(cycle_id),
                    "dominant_manager_id": dominant_manager,
                    "active_manager_ids": list(active_manager_ids),
                    "manager_budget_weights": dict(manager_budget_weights),
                    "hold_reason": str(historical.get("guardrail_hold_reason") or ""),
                    "reasoning": decision.reasoning,
                },
            )
        else:
            event_emitter(
                "governance_applied",
                {
                    **controller._event_context(cycle_id),
                    "dominant_manager_id": dominant_manager,
                    "active_manager_ids": list(active_manager_ids),
                    "manager_budget_weights": dict(manager_budget_weights),
                    "cash_reserve_hint": decision.cash_reserve_hint,
                    "reasoning": decision.reasoning,
                },
            )
        controller._emit_agent_status(
            "Governance",
            "completed",
            f"治理层识别 {decision.regime} 市场，激活 {len(active_manager_ids)} 个经理",
            cycle_id=cycle_id,
            stage="governance",
            progress_pct=24,
            step=2,
            total_steps=6,
            details=session_last_governance_decision(controller),
            thinking=controller._thinking_excerpt(decision.reasoning),
        )
        controller._emit_module_log(
            "governance",
            "组合治理完成",
            decision.reasoning,
            cycle_id=cycle_id,
            kind="governance_decision",
            details=session_last_governance_decision(controller),
            metrics={
                "active_manager_count": len(active_manager_ids),
                "cash_reserve_hint": decision.cash_reserve_hint,
                "decision_confidence": decision.decision_confidence,
            },
        )

    def decide_governance(
        self,
        owner: Any | None,
        *,
        stock_data: dict[str, Any],
        cutoff_date: str,
        current_manager_id: str,
        data_manager: Any,
        output_dir: str | Path | None,
        allowed_manager_ids: list[str] | None = None,
        current_cycle_id: int | None = None,
        safe_leaderboard_refresh: bool = False,
    ) -> Any:
        governance_enabled = bool(
            getattr(
                owner, "governance_enabled", getattr(config, "governance_enabled", True)
            )
        )
        governance_mode = (
            str(
                getattr(
                    owner, "governance_mode", getattr(config, "governance_mode", "rule")
                )
                or "rule"
            )
            .strip()
            .lower()
        )
        allocator_top_n = int(
            getattr(owner, "allocator_top_n", getattr(config, "allocator_top_n", 3))
            or 3
        )
        coordinator = (
            getattr(owner, "governance_coordinator", None)
            if owner is not None
            else None
        )
        if coordinator is None:
            coordinator = self.build_governance_coordinator(owner or config)
            if owner is not None:
                owner.governance_coordinator = coordinator
        agents = dict(getattr(owner, "agents", {}) or {}) if owner is not None else {}
        selector_enabled = bool(
            getattr(
                owner,
                "governance_agent_override_enabled",
                getattr(config, "governance_agent_override_enabled", False),
            )
        )
        decision = coordinator.decide(
            stock_data=stock_data,
            cutoff_date=cutoff_date,
            current_manager_id=current_manager_id,
            leaderboard_path=self.prepare_leaderboard(
                output_dir=output_dir,
                safe=(safe_leaderboard_refresh or owner is None),
            ),
            allocator_top_n=allocator_top_n,
            allowed_manager_ids=self._resolve_allowed_manager_ids(
                owner, allowed_manager_ids
            )
            or None,
            governance_mode=governance_mode if governance_enabled else "off",
            regime_agent=agents.get("market_regime")
            if governance_mode in {"hybrid", "agent"}
            else None,
            selector_agent=(
                agents.get("governance_selector")
                if selector_enabled and governance_mode in {"hybrid", "agent"}
                else None
            ),
            previous_decision=governance_from_controller(owner),
            current_cycle_id=current_cycle_id,
            last_governance_change_cycle_id=getattr(
                owner, "last_governance_change_cycle_id", None
            ),
            data_manager=data_manager,
            shadow_mode=self._resolve_shadow_mode(owner),
        )
        return decision

    def _resolve_allowed_manager_ids(
        self, owner: Any | None, override: list[str] | None
    ) -> list[str]:
        candidates = (
            override
            or getattr(owner, "experiment_allowed_manager_ids", None)
            or getattr(owner, "governance_allowed_manager_ids", None)
            or getattr(config, "governance_allowed_manager_ids", None)
            or []
        )
        return [str(item).strip() for item in candidates if str(item).strip()]


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return default


def _item_field(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def normalize_governance_decision(
    payload: dict[str, Any] | None = None,
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    governance = dict(payload or {})
    if governance:
        return governance

    routing = dict(fallback or {})
    if not routing:
        return {}

    dominant = str(routing.get("dominant_manager_id") or "").strip()
    active_manager_ids = [
        str(item).strip()
        for item in list(
            routing.get("active_manager_ids") or ([dominant] if dominant else [])
        )
        if str(item).strip()
    ]
    manager_budget_weights = {
        str(key): float(value)
        for key, value in dict(
            routing.get("manager_budget_weights")
            or ({dominant: 1.0} if dominant else {})
        ).items()
        if str(key).strip()
    }
    if not dominant and active_manager_ids:
        dominant = active_manager_ids[0]

    metadata = dict(routing.get("metadata") or {})
    metadata.setdefault("compatibility_source", "historical_governance_fallback")

    return {
        "as_of_date": str(routing.get("as_of_date") or ""),
        "regime": str(routing.get("regime") or "unknown"),
        "regime_confidence": _to_float(
            routing.get("regime_confidence", routing.get("confidence", 0.0))
        ),
        "decision_confidence": _to_float(
            routing.get("decision_confidence", routing.get("confidence", 0.0))
        ),
        "active_manager_ids": active_manager_ids,
        "manager_budget_weights": manager_budget_weights,
        "dominant_manager_id": dominant,
        "cash_reserve_hint": _to_float(routing.get("cash_reserve_hint", 0.0)),
        "portfolio_constraints": dict(routing.get("portfolio_constraints") or {}),
        "decision_source": str(routing.get("decision_source") or "compatibility"),
        "regime_source": str(
            routing.get("regime_source") or routing.get("source") or "compatibility"
        ),
        "reasoning": str(routing.get("reasoning") or ""),
        "evidence": dict(routing.get("evidence") or {}),
        "agent_advice": dict(routing.get("agent_advice") or {}),
        "allocation_plan": dict(routing.get("allocation_plan") or {}),
        "guardrail_checks": [
            dict(item) for item in list(routing.get("guardrail_checks") or [])
        ],
        "metadata": metadata,
    }


def governance_from_controller(controller: Any) -> dict[str, Any]:
    return normalize_governance_decision(
        dict(session_last_governance_decision(controller) or {})
    )


def governance_from_item(item: Any) -> dict[str, Any]:
    return normalize_governance_decision(
        dict(_item_field(item, "governance_decision", {}) or {})
    )


def governance_regime(
    governance_decision: dict[str, Any] | None = None,
    *,
    fallback: dict[str, Any] | None = None,
    default: str = "unknown",
) -> str:
    governance = normalize_governance_decision(governance_decision, fallback=fallback)
    regime = str(governance.get("regime") or default).strip()
    return regime or default


def dominant_manager_id(
    governance_decision: dict[str, Any] | None = None,
    *,
    fallback: dict[str, Any] | None = None,
) -> str:
    governance = normalize_governance_decision(governance_decision, fallback=fallback)
    dominant = str(governance.get("dominant_manager_id") or "").strip()
    if dominant:
        return dominant
    active_manager_ids = list(governance.get("active_manager_ids") or [])
    return str(active_manager_ids[0]).strip() if active_manager_ids else ""


def dominant_manager_config_ref(
    governance_decision: dict[str, Any] | None = None,
    *,
    portfolio_plan: dict[str, Any] | None = None,
    manager_results: list[dict[str, Any]] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    fallback: dict[str, Any] | None = None,
) -> str:
    governance = normalize_governance_decision(governance_decision, fallback=fallback)
    dominant = dominant_manager_id(governance, fallback=fallback)
    portfolio = dict(portfolio_plan or {})
    snapshot = dict(execution_snapshot or {})
    fallback_payload = dict(fallback or {})

    allocation_plan = dict(governance.get("allocation_plan") or {})
    selected_manager_config_refs = dict(
        allocation_plan.get("selected_manager_config_refs") or {}
    )
    if dominant:
        manager_config_ref = str(
            selected_manager_config_refs.get(dominant) or ""
        ).strip()
        if manager_config_ref:
            return manager_config_ref

    manager_config_ref = str(
        snapshot.get("manager_config_ref")
        or snapshot.get("active_runtime_config_ref")
        or fallback_payload.get("default_manager_config_ref")
        or ""
    ).strip()
    if manager_config_ref:
        return manager_config_ref

    for item in list(manager_results or []):
        payload = dict(item or {})
        if str(payload.get("manager_id") or "").strip() != dominant:
            continue
        plan = dict(payload.get("plan") or {})
        manager_config_ref = str(
            plan.get("source_manager_config_ref")
            or payload.get("manager_config_ref")
            or payload.get("runtime_config_ref")
            or dict(payload.get("metadata") or {}).get("manager_config_ref")
            or ""
        ).strip()
        if manager_config_ref:
            return manager_config_ref

    portfolio_meta = dict(portfolio.get("metadata") or {})
    portfolio_selected_refs = dict(
        portfolio_meta.get("selected_manager_config_refs") or {}
    )
    if dominant:
        manager_config_ref = str(portfolio_selected_refs.get(dominant) or "").strip()
        if manager_config_ref:
            return manager_config_ref

    return str(
        dict(governance.get("metadata") or {}).get("dominant_manager_config") or ""
    ).strip()


def execution_defaults_payload(
    governance_decision: dict[str, Any] | None = None,
    *,
    portfolio_plan: dict[str, Any] | None = None,
    manager_results: list[dict[str, Any]] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    fallback: dict[str, Any] | None = None,
) -> dict[str, str]:
    fallback_payload = dict(fallback or {})
    manager_id = str(
        dominant_manager_id(governance_decision, fallback=fallback_payload)
        or fallback_payload.get("default_manager_id")
        or ""
    ).strip()
    manager_config_ref = dominant_manager_config_ref(
        governance_decision,
        portfolio_plan=portfolio_plan,
        manager_results=manager_results,
        execution_snapshot=execution_snapshot,
        fallback=fallback_payload,
    )
    return {
        "default_manager_id": manager_id,
        "default_manager_config_ref": manager_config_ref,
    }


@dataclass(frozen=True)
class TrainingScopeResolution:
    governance_decision: dict[str, Any]
    dominant_manager_id: str
    manager_config_ref: str
    active_runtime_config_ref: str
    execution_defaults: dict[str, str]
    subject_type: str


def resolve_training_scope(
    *,
    controller: Any | None = None,
    governance_decision: dict[str, Any] | None = None,
    portfolio_plan: dict[str, Any] | None = None,
    manager_results: list[dict[str, Any]] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    dominant_manager_id_hint: str = "",
    fallback: dict[str, Any] | None = None,
) -> TrainingScopeResolution:
    fallback_payload = dict(fallback or {})
    if controller is not None:
        fallback_payload.setdefault(
            "default_manager_id", controller_default_manager_id(controller)
        )
        fallback_payload.setdefault(
            "default_manager_config_ref",
            controller_default_manager_config_ref(controller),
        )
    resolved_governance = normalize_governance_decision(
        dict(governance_decision or {}),
        fallback=fallback_payload,
    )
    snapshot = dict(execution_snapshot or {})
    active_runtime_config_ref = str(
        snapshot.get("active_runtime_config_ref")
        or fallback_payload.get("default_manager_config_ref")
        or ""
    ).strip()
    resolved_fallback = dict(fallback_payload)
    if dominant_manager_id_hint:
        resolved_fallback.setdefault(
            "dominant_manager_id", str(dominant_manager_id_hint)
        )
    if active_runtime_config_ref:
        resolved_fallback.setdefault(
            "default_manager_config_ref", active_runtime_config_ref
        )
    resolved_dominant_manager_id = str(
        dominant_manager_id(
            resolved_governance,
            fallback=resolved_fallback,
        )
        or dominant_manager_id_hint
        or fallback_payload.get("default_manager_id")
        or ""
    ).strip()
    resolved_manager_config_ref = dominant_manager_config_ref(
        resolved_governance,
        portfolio_plan=portfolio_plan,
        manager_results=manager_results,
        execution_snapshot={
            **snapshot,
            "active_runtime_config_ref": active_runtime_config_ref,
        },
        fallback={
            **resolved_fallback,
            "default_manager_id": resolved_dominant_manager_id
            or fallback_payload.get("default_manager_id")
            or "",
            "default_manager_config_ref": active_runtime_config_ref
            or fallback_payload.get("default_manager_config_ref")
            or "",
        },
    )
    resolved_execution_defaults = execution_defaults_payload(
        resolved_governance,
        portfolio_plan=portfolio_plan,
        manager_results=manager_results,
        execution_snapshot={
            **snapshot,
            "active_runtime_config_ref": active_runtime_config_ref,
            "manager_config_ref": resolved_manager_config_ref,
        },
        fallback={
            **resolved_fallback,
            "default_manager_id": resolved_dominant_manager_id
            or fallback_payload.get("default_manager_id")
            or "",
            "default_manager_config_ref": resolved_manager_config_ref
            or active_runtime_config_ref
            or fallback_payload.get("default_manager_config_ref")
            or "",
        },
    )

    # Enforce a single canonical subject identity: dominant manager id must map to the
    # active/default/runtime config ref used for the cycle.
    canonical_manager_config_ref = _canonical_manager_config_ref_for_manager(
        resolved_dominant_manager_id,
        active_runtime_config_ref or resolved_manager_config_ref,
    )
    resolved_manager_config_ref = canonical_manager_config_ref
    active_runtime_config_ref = canonical_manager_config_ref
    resolved_execution_defaults = {
        **dict(resolved_execution_defaults or {}),
        "default_manager_id": resolved_dominant_manager_id,
        "default_manager_config_ref": canonical_manager_config_ref,
    }
    return TrainingScopeResolution(
        governance_decision=resolved_governance,
        dominant_manager_id=resolved_dominant_manager_id,
        manager_config_ref=resolved_manager_config_ref,
        active_runtime_config_ref=active_runtime_config_ref,
        execution_defaults=resolved_execution_defaults,
        subject_type="manager_portfolio"
        if dict(portfolio_plan or {})
        else "single_manager",
    )


class TrainingExperimentService:
    """Applies experiment protocol, dataset, manager-scope, and LLM overrides."""

    def configure_experiment(
        self, controller: Any, spec: Dict[str, Any] | None = None
    ) -> None:
        normalized_spec = ExperimentSpec.from_payload(spec)
        payload = normalized_spec.to_payload()
        projection = build_experiment_runtime_projection(
            controller,
            normalized_spec=normalized_spec,
            payload=payload,
        )
        apply_experiment_runtime_projection(controller, projection)


@dataclass(frozen=True)
class ExperimentRuntimeProjection:
    payload: dict[str, Any]
    seed: int | None
    min_date: str | None
    max_date: str | None
    min_history_days: int | None
    simulation_days: int | None
    cutoff_policy: dict[str, Any]
    review_window: dict[str, Any]
    promotion_policy: dict[str, Any]
    allowed_manager_ids: list[str]
    llm: dict[str, Any]
    allocator_enabled: bool | None
    governance_enabled: bool | None
    governance_mode: str | None
    governance_cooldown_cycles: int | None
    governance_min_confidence: float | None
    governance_hysteresis_margin: float | None
    governance_agent_override_enabled: bool | None
    governance_agent_override_max_gap: float | None


def reload_manager_runtime_boundary(
    controller: Any,
    runtime_config_ref: str,
) -> None:
    reload_runtime = getattr(controller, "_reload_manager_runtime", None)
    if callable(reload_runtime):
        reload_runtime(runtime_config_ref)
        return

    routing_service = getattr(controller, "training_governance_service", None)
    if routing_service is not None and hasattr(
        routing_service, "reload_manager_runtime"
    ):
        routing_service.reload_manager_runtime(controller, runtime_config_ref)


def enforce_allowed_manager_scope_boundary(
    controller: Any,
    *,
    allowed_manager_ids: list[str] | None = None,
) -> None:
    allowed_ids = [
        str(manager_id).strip()
        for manager_id in list(
            allowed_manager_ids
            if allowed_manager_ids is not None
            else getattr(controller, "experiment_allowed_manager_ids", [])
        )
        if str(manager_id).strip()
    ]
    if not allowed_ids:
        return
    if controller_default_manager_id(controller) in allowed_ids:
        return

    next_manager_id = allowed_ids[0]
    next_manager_config_ref = str(resolve_manager_config_ref(next_manager_id))
    set_session_default_manager(
        controller,
        manager_id=next_manager_id,
        manager_config_ref=next_manager_config_ref,
    )
    set_session_current_params(controller, {})
    reload_manager_runtime_boundary(controller, next_manager_config_ref)


def build_experiment_runtime_projection(
    controller: Any,
    *,
    normalized_spec: Any,
    payload: dict[str, Any],
) -> ExperimentRuntimeProjection:
    protocol = dict(payload.get("protocol") or {})
    dataset = dict(payload.get("dataset") or {})
    manager_scope = dict(payload.get("manager_scope") or {})
    llm = dict(payload.get("llm") or {})
    date_range = dict(protocol.get("date_range") or {})
    return ExperimentRuntimeProjection(
        payload=dict(payload or {}),
        seed=getattr(normalized_spec, "seed", None),
        min_date=str(date_range.get("min") or "") or None,
        max_date=str(date_range.get("max") or "") or None,
        min_history_days=dataset.get("min_history_days"),
        simulation_days=dataset.get("simulation_days"),
        cutoff_policy=dict(
            protocol.get("cutoff_policy")
            or getattr(normalized_spec, "cutoff_policy", {})
            or {}
        ),
        review_window=dict(
            protocol.get("review_window")
            or getattr(normalized_spec, "review_window", {})
            or {}
        ),
        promotion_policy=dict(
            protocol.get("promotion_policy")
            or getattr(normalized_spec, "promotion_policy", {})
            or getattr(controller, "promotion_gate_policy", {})
            or {}
        ),
        allowed_manager_ids=[
            str(name)
            for name in list(manager_scope.get("allowed_manager_ids") or [])
            if str(name).strip()
        ],
        llm=dict(llm or {}),
        allocator_enabled=(
            bool(manager_scope.get("allocator_enabled"))
            if manager_scope.get("allocator_enabled") is not None
            else None
        ),
        governance_enabled=(
            bool(manager_scope.get("governance_enabled"))
            if manager_scope.get("governance_enabled") is not None
            else None
        ),
        governance_mode=(
            str(manager_scope.get("governance_mode") or "rule").strip().lower()
            or "rule"
            if manager_scope.get("governance_mode") is not None
            else None
        ),
        governance_cooldown_cycles=(
            int(manager_scope.get("governance_cooldown_cycles") or 0)
            if manager_scope.get("governance_cooldown_cycles") is not None
            else None
        ),
        governance_min_confidence=(
            float(manager_scope.get("governance_min_confidence") or 0.0)
            if manager_scope.get("governance_min_confidence") is not None
            else None
        ),
        governance_hysteresis_margin=(
            float(manager_scope.get("governance_hysteresis_margin") or 0.0)
            if manager_scope.get("governance_hysteresis_margin") is not None
            else None
        ),
        governance_agent_override_enabled=(
            bool(manager_scope.get("governance_agent_override_enabled"))
            if manager_scope.get("governance_agent_override_enabled") is not None
            else None
        ),
        governance_agent_override_max_gap=(
            float(manager_scope.get("governance_agent_override_max_gap") or 0.0)
            if manager_scope.get("governance_agent_override_max_gap") is not None
            else None
        ),
    )


def apply_experiment_runtime_projection(
    controller: Any,
    projection: ExperimentRuntimeProjection,
) -> None:
    controller.experiment_protocol = dict(projection.payload or {})
    controller.experiment_spec = dict(projection.payload or {})
    controller.experiment_seed = projection.seed
    controller.experiment_min_date = projection.min_date
    controller.experiment_max_date = projection.max_date
    controller.experiment_min_history_days = projection.min_history_days
    controller.experiment_simulation_days = projection.simulation_days
    controller.experiment_cutoff_policy = dict(projection.cutoff_policy or {})
    controller.experiment_review_window = dict(projection.review_window or {})
    controller.experiment_promotion_policy = dict(projection.promotion_policy or {})
    controller.experiment_allowed_manager_ids = list(
        projection.allowed_manager_ids or []
    )
    controller.experiment_llm = dict(projection.llm or {})
    controller._apply_experiment_llm_overrides(dict(projection.llm or {}))

    if projection.allocator_enabled is not None:
        controller.allocator_enabled = bool(projection.allocator_enabled)
        controller.governance_enabled = bool(projection.allocator_enabled)
    if projection.governance_enabled is not None:
        controller.governance_enabled = bool(projection.governance_enabled)
    if projection.governance_mode is not None:
        controller.governance_mode = str(projection.governance_mode or "rule")
    if projection.allowed_manager_ids:
        controller.governance_allowed_manager_ids = list(projection.allowed_manager_ids)
    if projection.governance_cooldown_cycles is not None:
        controller.governance_cooldown_cycles = int(
            projection.governance_cooldown_cycles
        )
    if projection.governance_min_confidence is not None:
        controller.governance_min_confidence = float(
            projection.governance_min_confidence
        )
    if projection.governance_hysteresis_margin is not None:
        controller.governance_hysteresis_margin = float(
            projection.governance_hysteresis_margin
        )
    if projection.governance_agent_override_enabled is not None:
        controller.governance_agent_override_enabled = bool(
            projection.governance_agent_override_enabled
        )
    if projection.governance_agent_override_max_gap is not None:
        controller.governance_agent_override_max_gap = float(
            projection.governance_agent_override_max_gap
        )

    controller._refresh_governance_coordinator()
    enforce_allowed_manager_scope_boundary(
        controller,
        allowed_manager_ids=projection.allowed_manager_ids,
    )
