from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import normalize_date
from invest.shared.model_governance import (
    canonicalize_candidate_build_source,
    evaluate_promotion_discipline,
    infer_deployment_stage,
    latest_candidate_build_event,
    latest_open_candidate_record,
    normalize_freeze_gate_policy,
    normalize_proposal_gate_policy,
    normalize_promotion_gate_policy,
    resolve_model_governance_matrix,
)
from app.training.runtime_discipline import (
    resolve_active_runtime_params,
    resolve_effective_runtime_params,
)
from app.training.versioning import resolve_active_runtime_identity


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


def _normalize_allowed_models(value: Any) -> list[str]:
    return [str(item).strip() for item in list(value or []) if str(item).strip()]


def _normalize_experiment_mode(value: Any) -> str:
    normalized = str(value or "standard").strip().lower() or "standard"
    if normalized not in {"standard", "validation"}:
        return "standard"
    return normalized


def _normalize_regime_targets(value: Any) -> list[str]:
    allowed = {"bull", "bear", "oscillation"}
    targets: list[str] = []
    for item in list(value or []):
        normalized = str(item or "").strip().lower()
        if normalized in allowed and normalized not in targets:
            targets.append(normalized)
    return targets


def _normalize_universe_sampling_mode(value: Any) -> str:
    normalized = str(value or "ranked").strip().lower() or "ranked"
    if normalized not in {"ranked", "random", "stratified_random"}:
        return "ranked"
    return normalized


def _normalize_universe_stratify_by(value: Any) -> str:
    normalized = str(value or "board").strip().lower() or "board"
    if normalized not in {"board", "industry"}:
        return "board"
    return normalized


def _normalize_config_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    looks_like_path = path.is_absolute() or path.suffix.lower() in {".yaml", ".yml", ".json"} or any(
        separator in text for separator in ("/", "\\")
    )
    if not looks_like_path:
        return text
    try:
        return str(path.resolve(strict=False))
    except Exception:
        return text


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
        "step_days": max(1, _optional_int(payload.get("step_days") or payload.get("window_days")) or 30),
        "dates": dates,
    }
    if mode == "sequence":
        normalized["sampling_seeds"] = [
            _optional_int(item)
            for item in list(payload.get("sampling_seeds") or [])
        ]
    if mode == "regime_balanced":
        fallback_mode = str(payload.get("fallback_mode") or "random").strip().lower() or "random"
        if fallback_mode not in {"random", "rolling", "sequence", "fixed"}:
            fallback_mode = "random"
        normalized.update(
            {
                "probe_count": max(3, _optional_int(payload.get("probe_count")) or 9),
                "probe_mode": str(payload.get("probe_mode") or "routing_regime").strip().lower()
                or "routing_regime",
                "min_regime_samples": max(0, _optional_int(payload.get("min_regime_samples")) or 0),
                "target_regimes": _normalize_regime_targets(payload.get("target_regimes") or []),
                "fallback_mode": fallback_mode,
            }
        )
    return normalized


def normalize_universe_policy(value: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(value or {})
    return {
        "mode": _normalize_universe_sampling_mode(payload.get("mode")),
        "stratify_by": _normalize_universe_stratify_by(payload.get("stratify_by")),
    }


def build_standard_training_experiment_spec(
    *,
    seed: int = 7,
    min_history_days: int,
    simulation_days: int,
    stock_count: int,
    min_date: str | None = None,
    max_date: str | None = None,
    cutoff_policy: dict[str, Any] | None = None,
    universe_policy: dict[str, Any] | None = None,
    allowed_models: list[str] | None = None,
    dry_run_llm: bool = False,
) -> dict[str, Any]:
    return {
        "protocol": {
            "seed": int(seed),
            "date_range": {
                "min": _optional_normalized_date(min_date),
                "max": _optional_normalized_date(max_date),
            },
            "review_window": {"mode": "single_cycle", "size": 1},
            "cutoff_policy": normalize_cutoff_policy(
                cutoff_policy or {"mode": "random"}
            ),
        },
        "dataset": {
            "min_history_days": int(min_history_days),
            "simulation_days": int(simulation_days),
            "stock_count": int(stock_count),
            "universe_policy": normalize_universe_policy(
                universe_policy
                or {"mode": "stratified_random", "stratify_by": "board"}
            ),
        },
        "model_scope": {
            "experiment_mode": "standard",
            "allowed_models": _normalize_allowed_models(allowed_models or []),
        },
        "llm": {
            "dry_run": bool(dry_run_llm),
        },
    }


def build_review_basis_window(
    controller: Any,
    *,
    cycle_id: int,
    review_window: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_review_window(review_window)
    size = max(1, int(normalized.get("size") or 1))
    if str(normalized.get("mode") or "single_cycle") == "single_cycle":
        return {
            "mode": "single_cycle",
            "size": 1,
            "cycle_ids": [int(cycle_id)],
            "current_cycle_id": int(cycle_id),
        }
    previous_cycle_ids = [
        int(getattr(item, "cycle_id"))
        for item in list(getattr(controller, "cycle_history", []) or [])
        if getattr(item, "cycle_id", None) is not None
    ]
    basis_cycle_ids = (previous_cycle_ids[-max(0, size - 1) :] + [int(cycle_id)])[-size:]
    return {
        "mode": str(normalized.get("mode") or "single_cycle"),
        "size": size,
        "cycle_ids": basis_cycle_ids,
        "current_cycle_id": int(cycle_id),
    }


def _fitness_source_cycles(controller: Any, optimization_events: list[dict[str, Any]] | None = None) -> list[int]:
    if not latest_candidate_build_event(optimization_events):
        return []
    return [
        int(getattr(item, "cycle_id"))
        for item in list(getattr(controller, "cycle_history", []) or [])[-10:]
        if getattr(item, "cycle_id", None) is not None
    ]

def _promotion_decision(
    *,
    controller: Any,
    active_config_ref: str,
    candidate_config_ref: str,
    auto_applied: bool,
    carried_forward: bool = False,
    mutation_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = dict(getattr(controller, "experiment_promotion_policy", {}) or {})
    if not candidate_config_ref:
        return {
            "status": "not_evaluated",
            "source": "controller_cycle",
            "reason": "no_candidate_config_generated",
            "applied_to_active": False,
            "active_config_ref": active_config_ref,
            "candidate_config_ref": "",
            "policy": policy,
        }

    if carried_forward and not bool((mutation_event or {}).get("stage")):
        return {
            "status": "candidate_pending",
            "source": "controller_cycle",
            "reason": "existing pending candidate carried forward",
            "applied_to_active": False,
            "active_config_ref": active_config_ref,
            "candidate_config_ref": candidate_config_ref,
            "policy": policy,
        }

    status = "candidate_auto_applied" if auto_applied else "candidate_generated"
    reason = (
        "candidate config auto-applied to runtime"
        if auto_applied
        else "candidate config generated; active config unchanged"
    )
    return {
        "status": status,
        "source": canonicalize_candidate_build_source("runtime_candidate_builder"),
        "reason": str((mutation_event or {}).get("notes") or reason),
        "applied_to_active": bool(auto_applied),
        "active_config_ref": active_config_ref,
        "candidate_config_ref": candidate_config_ref,
        "policy": policy,
    }


@dataclass(frozen=True)
class ExperimentSpec:
    payload: dict[str, Any] = field(default_factory=dict)
    seed: int | None = None
    llm_mode: str = "live"
    review_window: dict[str, Any] = field(default_factory=lambda: {"mode": "single_cycle", "size": 1})
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
        model_scope = dict(raw.get("model_scope") or {})
        optimization = dict(raw.get("optimization") or {})
        llm = dict(raw.get("llm") or {})
        experiment_mode = _normalize_experiment_mode(
            model_scope.get("experiment_mode") or model_scope.get("mode")
        )

        date_range = dict(protocol.get("date_range") or {})
        normalized_seed = _optional_int(protocol.get("seed"))
        normalized_review_window = normalize_review_window(dict(protocol.get("review_window") or {}))
        normalized_cutoff_policy = normalize_cutoff_policy(dict(protocol.get("cutoff_policy") or {}))
        normalized_promotion_policy = dict(
            protocol.get("promotion_policy")
            or optimization.get("promotion_gate")
            or {}
        )
        llm_mode = "dry_run" if bool(llm.get("dry_run")) else str(llm.get("mode") or "live").strip() or "live"

        normalized_payload = {
            "spec": deepcopy(spec),
            "protocol": {
                **protocol,
                "seed": normalized_seed,
                "date_range": {
                    "min": _optional_normalized_date(date_range.get("min") or protocol.get("min_date")),
                    "max": _optional_normalized_date(date_range.get("max") or protocol.get("max_date")),
                },
                "review_window": normalized_review_window,
                "cutoff_policy": normalized_cutoff_policy,
                "promotion_policy": deepcopy(normalized_promotion_policy),
            },
            "dataset": {
                **dataset,
                "min_history_days": _optional_int(dataset.get("min_history_days")),
                "simulation_days": _optional_int(dataset.get("simulation_days")),
                "stock_count": _optional_int(dataset.get("stock_count")),
                "universe_policy": normalize_universe_policy(
                    dict(dataset.get("universe_policy") or {})
                ),
            },
            "model_scope": {
                **model_scope,
                "experiment_mode": experiment_mode,
                "allowed_models": _normalize_allowed_models(model_scope.get("allowed_models") or []),
            },
            "optimization": deepcopy(optimization),
            "llm": {
                **llm,
                "mode": llm_mode,
            },
        }
        if experiment_mode == "validation":
            normalized_payload["model_scope"]["allocator_enabled"] = False
            normalized_payload["model_scope"]["model_routing_enabled"] = False
            normalized_payload["model_scope"]["routing_mode"] = "off"
            normalized_payload["model_scope"]["agent_override_enabled"] = False
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


def build_cycle_run_context(
    controller: Any,
    *,
    cycle_id: int,
    model_output: Any | None,
    optimization_events: list[dict[str, Any]] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    evaluation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = dict(execution_snapshot or {})
    evaluation = dict(evaluation_context or {})
    mutation_event = latest_candidate_build_event(optimization_events)
    mutation_decision = dict(mutation_event.get("decision") or {})
    candidate_config_ref = _normalize_config_ref(
        mutation_decision.get("config_path")
        or mutation_decision.get("pending_candidate_ref")
        or ""
    )
    candidate_version_id = str(mutation_decision.get("candidate_version_id") or "")
    candidate_runtime_fingerprint = str(
        mutation_decision.get("candidate_runtime_fingerprint") or ""
    )
    auto_applied = bool(mutation_decision.get("auto_applied", False))
    carried_forward_candidate = False
    if not candidate_config_ref and not auto_applied:
        pending_candidate = latest_open_candidate_record(
            list(getattr(controller, "cycle_history", []) or [])
        )
        candidate_config_ref = _normalize_config_ref(
            pending_candidate.get("candidate_config_ref") or ""
        )
        candidate_version_id = str(
            candidate_version_id or pending_candidate.get("candidate_version_id") or ""
        )
        candidate_runtime_fingerprint = str(
            candidate_runtime_fingerprint
            or pending_candidate.get("candidate_runtime_fingerprint")
            or ""
        )
        carried_forward_candidate = bool(candidate_config_ref)
    snapshot_active_version_id = str(snapshot.get("active_version_id") or "")
    snapshot_active_runtime_fingerprint = str(
        snapshot.get("runtime_fingerprint")
        or snapshot.get("active_runtime_fingerprint")
        or ""
    )
    active_config_ref = _normalize_config_ref(
        (
            candidate_config_ref
            if auto_applied and candidate_config_ref
            else snapshot.get("active_config_ref")
        )
        or getattr(controller, "model_config_path", "")
        or getattr(model_output, "config_name", "")
        or ""
    )
    active_version_id = (
        candidate_version_id
        if auto_applied and candidate_version_id
        else snapshot_active_version_id
    )
    active_runtime_fingerprint = (
        candidate_runtime_fingerprint
        if auto_applied and candidate_runtime_fingerprint
        else snapshot_active_runtime_fingerprint
    )
    review_window = dict(getattr(controller, "experiment_review_window", {}) or {})
    runtime_overrides = deepcopy(
        dict(snapshot.get("runtime_overrides") or resolve_active_runtime_params(controller))
    )
    if not active_version_id or not active_runtime_fingerprint:
        active_identity = resolve_active_runtime_identity(
            controller,
            model_name=str(
                getattr(model_output, "model_name", "")
                or getattr(controller, "model_name", "")
                or ""
            ),
            config_ref=active_config_ref,
            runtime_params=runtime_overrides,
        )
        active_version_id = str(active_version_id or active_identity.get("version_id") or "")
        active_runtime_fingerprint = str(
            active_runtime_fingerprint
            or active_identity.get("runtime_fingerprint")
            or ""
        )

    context = {
        "basis_stage": str(snapshot.get("basis_stage") or "post_cycle_result"),
        "strategy_family": str(
            getattr(controller, "strategy_family", "")
            or getattr(controller, "model_name", "")
            or ""
        ),
        "active_config_ref": active_config_ref,
        "active_version_id": str(active_version_id or ""),
        "active_runtime_fingerprint": str(active_runtime_fingerprint or ""),
        "candidate_config_ref": candidate_config_ref,
        "candidate_version_id": candidate_version_id,
        "candidate_runtime_fingerprint": candidate_runtime_fingerprint,
        "proposal_bundle_id": str(mutation_decision.get("proposal_bundle_id") or ""),
        "proposal_bundle_path": str(mutation_decision.get("proposal_bundle_path") or ""),
        "runtime_overrides": runtime_overrides,
        "review_basis_window": build_review_basis_window(
            controller,
            cycle_id=int(cycle_id),
            review_window=review_window,
        ),
        "fitness_source_cycles": _fitness_source_cycles(
            controller,
            optimization_events=optimization_events,
        ),
        "ab_comparison": deepcopy(dict(evaluation.get("ab_comparison") or {})),
        "research_feedback": deepcopy(dict(evaluation.get("research_feedback") or {})),
        "promotion_decision": _promotion_decision(
            controller=controller,
            active_config_ref=active_config_ref,
            candidate_config_ref=candidate_config_ref,
            auto_applied=auto_applied,
            carried_forward=carried_forward_candidate,
            mutation_event=mutation_event,
        ),
    }
    discipline = evaluate_promotion_discipline(
        run_context=context,
        cycle_history=list(getattr(controller, "cycle_history", []) or []),
        policy=dict((getattr(controller, "quality_gate_matrix", {}) or {}).get("promotion") or {}),
        optimization_events=optimization_events,
    )
    context["deployment_stage"] = str(discipline.get("deployment_stage") or "active")
    context["promotion_discipline"] = discipline
    context["quality_gate_matrix"] = resolve_model_governance_matrix(
        dict(getattr(controller, "quality_gate_matrix", {}) or {}),
        strategy_family=getattr(controller, "strategy_family", ""),
    )
    context["resolved_train_policy"] = {
        "promotion_gate": normalize_promotion_gate_policy(
            dict(getattr(controller, "promotion_gate_policy", {}) or {})
        ),
        "proposal_gate": normalize_proposal_gate_policy(
            dict(getattr(controller, "proposal_gate_policy", {}) or {})
        ),
        "freeze_gate": normalize_freeze_gate_policy(
            dict(getattr(controller, "freeze_gate_policy", {}) or {})
        ),
        "quality_gate_matrix": dict(context["quality_gate_matrix"]),
    }
    context["governance_stage"] = infer_deployment_stage(
        run_context=context,
        optimization_events=optimization_events,
    )
    return context


def build_execution_snapshot(
    controller: Any,
    *,
    cycle_id: int,
    model_output: Any | None,
    selection_mode: str = "",
    benchmark_passed: bool = False,
    return_pct: float | None = None,
    is_profit: bool | None = None,
    basis_stage: str = "pre_optimization",
) -> dict[str, Any]:
    active_config_ref = _normalize_config_ref(
        getattr(controller, "model_config_path", "")
        or getattr(model_output, "config_name", "")
        or ""
    )
    model_name = str(
        getattr(model_output, "model_name", "")
        or getattr(controller, "model_name", "")
        or ""
    )
    base_runtime_overrides = deepcopy(resolve_active_runtime_params(controller))
    runtime_overrides = deepcopy(resolve_effective_runtime_params(controller))
    runtime_identity = resolve_active_runtime_identity(
        controller,
        model_name=model_name,
        config_ref=active_config_ref,
        runtime_params=runtime_overrides,
    )
    inferred_profit = False
    if is_profit is not None:
        inferred_profit = bool(is_profit)
    else:
        try:
            inferred_profit = float(return_pct or 0.0) > 0.0
        except (TypeError, ValueError):
            inferred_profit = False
    return {
        "basis_stage": str(basis_stage or "pre_optimization"),
        "cycle_id": int(cycle_id),
        "model_name": model_name,
        "active_config_ref": active_config_ref,
        "active_version_id": str(runtime_identity.get("version_id") or ""),
        "runtime_fingerprint": str(runtime_identity.get("runtime_fingerprint") or ""),
        "runtime_overrides": runtime_overrides,
        "base_runtime_overrides": base_runtime_overrides,
        "regime_runtime_profile": deepcopy(
            dict(getattr(controller, "current_cycle_regime_profile", {}) or {})
        ),
        "selection_intercepts": deepcopy(
            dict(getattr(controller, "current_cycle_selection_intercepts", {}) or {})
        ),
        "routing_decision": deepcopy(dict(getattr(controller, "last_routing_decision", {}) or {})),
        "selection_mode": str(selection_mode or ""),
        "benchmark_passed": bool(benchmark_passed),
        "return_pct": return_pct,
        "is_profit": inferred_profit,
    }
