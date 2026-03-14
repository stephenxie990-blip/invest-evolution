from __future__ import annotations

from pathlib import Path
from typing import Any


def _latest_yaml_mutation_event(optimization_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    for event in reversed(list(optimization_events or [])):
        if str(event.get("stage") or "") == "yaml_mutation":
            return dict(event)
    return {}


def _candidate_meta_ref(candidate_config_ref: str) -> str:
    ref = str(candidate_config_ref or "").strip()
    if not ref:
        return ""
    return str(Path(ref).with_suffix(".json"))


def build_lineage_record(
    controller: Any,
    *,
    cycle_id: int,
    model_output: Any | None,
    run_context: dict[str, Any],
    optimization_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = dict(run_context or {})
    promotion_decision = dict(payload.get("promotion_decision") or {})
    candidate_config_ref = str(payload.get("candidate_config_ref") or "")
    active_config_ref = str(payload.get("active_config_ref") or "")
    mutation_event = _latest_yaml_mutation_event(optimization_events)

    if not candidate_config_ref:
        lineage_status = "active_only"
    elif bool(promotion_decision.get("applied_to_active", False)):
        lineage_status = "candidate_applied"
    else:
        lineage_status = "candidate_pending"

    return {
        "cycle_id": int(cycle_id),
        "basis_stage": str(payload.get("basis_stage") or "post_cycle_result"),
        "model_name": str(
            getattr(model_output, "model_name", "")
            or getattr(controller, "model_name", "")
            or ""
        ),
        "active_config_ref": active_config_ref,
        "candidate_config_ref": candidate_config_ref,
        "candidate_meta_ref": _candidate_meta_ref(candidate_config_ref),
        "lineage_status": lineage_status,
        "runtime_overrides": dict(payload.get("runtime_overrides") or {}),
        "fitness_source_cycles": list(payload.get("fitness_source_cycles") or []),
        "review_basis_window": dict(payload.get("review_basis_window") or {}),
        "mutation_trigger": str(mutation_event.get("trigger") or ""),
        "mutation_stage": str(mutation_event.get("stage") or ""),
        "mutation_notes": str(mutation_event.get("notes") or ""),
        "promotion_status": str(promotion_decision.get("status") or "not_evaluated"),
    }
