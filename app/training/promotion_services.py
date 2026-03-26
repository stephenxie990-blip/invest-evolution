from __future__ import annotations

from pathlib import Path
from typing import Any

from invest.shared.model_governance import infer_deployment_stage, latest_candidate_build_event


def _candidate_meta_ref(candidate_config_ref: str) -> str:
    ref = str(candidate_config_ref or "").strip()
    if not ref:
        return ""
    return str(Path(ref).with_suffix(".json"))


def build_promotion_record(
    *,
    cycle_id: int,
    run_context: dict[str, Any],
    optimization_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = dict(run_context or {})
    decision = dict(payload.get("promotion_decision") or {})
    candidate_config_ref = str(payload.get("candidate_config_ref") or "")
    mutation_event = latest_candidate_build_event(optimization_events)
    applied_to_active = bool(decision.get("applied_to_active", False))
    discipline = dict(payload.get("promotion_discipline") or {})
    stage_info = infer_deployment_stage(
        run_context=payload,
        optimization_events=optimization_events,
    )
    deployment_stage = str(
        payload.get("deployment_stage")
        or discipline.get("deployment_stage")
        or stage_info.get("deployment_stage")
        or "active"
    )
    status = str(discipline.get("status") or decision.get("status") or "not_evaluated")

    if status == "candidate_expired" or status == "candidate_pruned":
        gate_status = "rejected"
    elif status == "override_expired":
        gate_status = "override_rejected"
    elif deployment_stage == "override":
        gate_status = "override_pending"
    elif not candidate_config_ref:
        gate_status = "not_applicable"
    elif applied_to_active:
        gate_status = "applied_to_active"
    else:
        gate_status = "awaiting_gate"

    return {
        "cycle_id": int(cycle_id),
        "basis_stage": str(payload.get("basis_stage") or "post_cycle_result"),
        "status": status,
        "source": str(decision.get("source") or ""),
        "reason": str(decision.get("reason") or ""),
        "applied_to_active": applied_to_active,
        "attempted": bool(candidate_config_ref or deployment_stage == "override"),
        "gate_status": gate_status,
        "deployment_stage": deployment_stage,
        "discipline": discipline,
        "active_config_ref": str(payload.get("active_config_ref") or ""),
        "active_version_id": str(payload.get("active_version_id") or ""),
        "active_runtime_fingerprint": str(payload.get("active_runtime_fingerprint") or ""),
        "candidate_config_ref": candidate_config_ref,
        "candidate_version_id": str(payload.get("candidate_version_id") or ""),
        "candidate_runtime_fingerprint": str(payload.get("candidate_runtime_fingerprint") or ""),
        "candidate_meta_ref": _candidate_meta_ref(candidate_config_ref),
        "policy": dict(decision.get("policy") or {}),
        "mutation_trigger": str(mutation_event.get("trigger") or ""),
        "mutation_stage": str(mutation_event.get("stage") or ""),
        "mutation_notes": str(mutation_event.get("notes") or ""),
    }
