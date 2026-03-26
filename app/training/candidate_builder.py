from __future__ import annotations

import logging
from typing import Any

from app.training.proposal_governance import evaluate_candidate_proposal_gate
from app.training.proposal_store import (
    persist_cycle_proposal_bundle,
    update_cycle_proposal_bundle,
)
from app.training.suggestion_tracking import apply_proposal_outcome
from app.training.versioning import build_candidate_identity
from invest.shared.model_governance import (
    build_optimization_event_lineage,
    latest_open_candidate_record,
    normalize_config_ref,
)

logger = logging.getLogger(__name__)


def _latest_open_candidate_ref(controller: Any) -> str:
    return normalize_config_ref(
        dict(latest_open_candidate_record(list(getattr(controller, "cycle_history", []) or []))).get(
            "candidate_config_ref"
        )
        or ""
    )


def _proposal_block_reason_map(gate_result: dict[str, Any]) -> dict[str, list[str]]:
    blocked: dict[str, list[str]] = {}
    for proposal in list(gate_result.get("blocked_proposals") or []):
        proposal_id = str(dict(proposal or {}).get("proposal_id") or "")
        if proposal_id:
            blocked[proposal_id] = [
                str(reason).strip()
                for reason in list(dict(proposal or {}).get("block_reasons") or [])
                if str(reason).strip()
            ]
    return blocked


def _refresh_bundle_tracking(
    controller: Any,
    *,
    cycle_id: int,
    bundle: dict[str, Any],
    gate_result: dict[str, Any],
    decision_stage: str,
    decision_reason: str,
    candidate_config_ref: str = "",
    candidate_version_id: str = "",
    pending_candidate_ref: str = "",
) -> dict[str, Any]:
    bundle_path = str(bundle.get("bundle_path") or "")
    if not bundle_path:
        return dict(bundle)

    approved_refs = {
        str(item).strip()
        for item in list(dict(gate_result.get("proposal_summary") or {}).get("approved_proposal_refs") or [])
        if str(item).strip()
    }
    blocked_reason_map = _proposal_block_reason_map(gate_result)
    updated_proposals: list[dict[str, Any]] = []

    for proposal in list(bundle.get("proposals") or []):
        payload = dict(proposal or {})
        if str(payload.get("target_scope") or "candidate") != "candidate":
            updated_proposals.append(payload)
            continue

        proposal_id = str(payload.get("proposal_id") or "")
        if proposal_id in blocked_reason_map:
            updated_proposals.append(
                apply_proposal_outcome(
                    payload,
                    adoption_status="rejected_by_proposal_gate",
                    decision_cycle_id=int(cycle_id),
                    decision_stage=decision_stage,
                    decision_reason=decision_reason,
                    proposal_bundle_id=str(bundle.get("proposal_bundle_id") or ""),
                    block_reasons=blocked_reason_map.get(proposal_id) or [],
                )
            )
            continue

        if proposal_id in approved_refs:
            if pending_candidate_ref:
                adoption_status = "deferred_pending_candidate"
            elif candidate_config_ref:
                adoption_status = "adopted_to_candidate"
            else:
                adoption_status = "queued"
            updated_proposals.append(
                apply_proposal_outcome(
                    payload,
                    adoption_status=adoption_status,
                    decision_cycle_id=int(cycle_id),
                    decision_stage=decision_stage,
                    decision_reason=decision_reason,
                    candidate_config_ref=candidate_config_ref,
                    candidate_version_id=candidate_version_id,
                    pending_candidate_ref=pending_candidate_ref,
                    proposal_bundle_id=str(bundle.get("proposal_bundle_id") or ""),
                )
            )
            continue

        updated_proposals.append(payload)

    updated_bundle = update_cycle_proposal_bundle(
        controller,
        bundle_path=bundle_path,
        proposals=updated_proposals,
    )
    bundle.clear()
    bundle.update(updated_bundle)
    return updated_bundle


def build_cycle_candidate_from_proposals(
    controller: Any,
    *,
    cycle_id: int,
    proposal_bundle: dict[str, Any] | None = None,
    event_factory: Any,
    trigger_reason: str = "cycle_review_completed",
) -> Any | None:
    bundle = dict(
        proposal_bundle
        or persist_cycle_proposal_bundle(
            controller,
            cycle_id=cycle_id,
        )
    )
    active_config_ref = normalize_config_ref(
        bundle.get("active_config_ref")
        or getattr(controller, "model_config_path", "")
        or ""
    )
    model_name = str(bundle.get("model_name") or getattr(controller, "model_name", "") or "")
    fitness_source_cycles = [
        int(getattr(item, "cycle_id"))
        for item in list(getattr(controller, "cycle_history", []) or [])[-10:]
        if getattr(item, "cycle_id", None) is not None
    ]
    gate_result = evaluate_candidate_proposal_gate(
        controller,
        cycle_id=cycle_id,
        proposal_bundle=bundle,
    )
    allowed_adjustments = dict(gate_result.get("filtered_adjustments") or {})
    param_adjustments = dict(allowed_adjustments.get("params") or {})
    scoring_adjustments = dict(allowed_adjustments.get("scoring") or {})
    agent_weight_adjustments = dict(allowed_adjustments.get("agent_weights") or {})
    proposal_refs = list(allowed_adjustments.get("proposal_refs") or [])
    runtime_override_keys = sorted(
        {
            *(str(key) for key in param_adjustments.keys()),
            *(str(key) for key in scoring_adjustments.keys()),
            *(str(key) for key in agent_weight_adjustments.keys()),
        }
    )

    if not (param_adjustments or scoring_adjustments or agent_weight_adjustments):
        blocked_adjustments = dict(gate_result.get("blocked_adjustments") or {})
        if not (
            dict(blocked_adjustments.get("params") or {})
            or dict(blocked_adjustments.get("scoring") or {})
            or dict(blocked_adjustments.get("agent_weights") or {})
        ):
            return None
        event = event_factory(
            cycle_id=int(cycle_id),
            trigger=trigger_reason,
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
            lineage=build_optimization_event_lineage(
                cycle_id=int(cycle_id),
                model_name=model_name,
                active_config_ref=active_config_ref,
                candidate_config_ref="",
                promotion_status="proposal_rejected",
                deployment_stage="active",
                review_basis_window={},
                fitness_source_cycles=fitness_source_cycles,
                runtime_override_keys=[],
            ),
            evidence={
                "proposal_bundle_id": str(bundle.get("proposal_bundle_id") or ""),
                "proposal_bundle_path": str(bundle.get("bundle_path") or ""),
                "proposal_source_summary": dict(
                    dict(gate_result.get("proposal_summary") or {}).get("requested_source_summary") or {}
                ),
                "proposal_gate": gate_result,
            },
            notes="all candidate changes blocked by proposal governance gate",
        )
        updated_bundle = _refresh_bundle_tracking(
            controller,
            cycle_id=int(cycle_id),
            bundle=bundle,
            gate_result=gate_result,
            decision_stage="candidate_build_skipped",
            decision_reason="proposal_governance_rejected",
        )
        event.evidence["suggestion_tracking_summary"] = dict(
            updated_bundle.get("suggestion_tracking_summary") or {}
        )
        return event

    pending_candidate_ref = _latest_open_candidate_ref(controller)
    if pending_candidate_ref and not bool(getattr(controller, "auto_apply_mutation", False)):
        event = event_factory(
            cycle_id=int(cycle_id),
            trigger=trigger_reason,
            stage="candidate_build_skipped",
            decision={
                "skipped": True,
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
            lineage=build_optimization_event_lineage(
                cycle_id=int(cycle_id),
                model_name=model_name,
                active_config_ref=active_config_ref,
                candidate_config_ref=pending_candidate_ref,
                promotion_status="candidate_generated",
                deployment_stage="candidate",
                review_basis_window={},
                fitness_source_cycles=fitness_source_cycles,
                runtime_override_keys=runtime_override_keys,
            ),
            evidence={
                "skip_reason": "pending_candidate_unresolved",
                "proposal_bundle_id": str(bundle.get("proposal_bundle_id") or ""),
                "proposal_bundle_path": str(bundle.get("bundle_path") or ""),
                "proposal_source_summary": dict(
                    dict(gate_result.get("proposal_summary") or {}).get("requested_source_summary") or {}
                ),
                "proposal_gate": gate_result,
            },
            notes="existing pending candidate reused; skip generating another candidate config",
        )
        updated_bundle = _refresh_bundle_tracking(
            controller,
            cycle_id=int(cycle_id),
            bundle=bundle,
            gate_result=gate_result,
            decision_stage="candidate_build_skipped",
            decision_reason="pending_candidate_unresolved",
            pending_candidate_ref=pending_candidate_ref,
        )
        event.evidence["suggestion_tracking_summary"] = dict(
            updated_bundle.get("suggestion_tracking_summary") or {}
        )
        return event

    mutation = controller.model_mutator.mutate(
        active_config_ref or getattr(controller, "model_config_path", ""),
        param_adjustments=param_adjustments or None,
        scoring_adjustments=scoring_adjustments or None,
        agent_weight_adjustments=agent_weight_adjustments or None,
        narrative_adjustments={"last_trigger": trigger_reason},
        generation_label=f"cycle_{int(cycle_id):04d}",
        parent_meta={
            "cycle_id": int(cycle_id),
            "trigger": trigger_reason,
            "proposal_bundle_id": str(bundle.get("proposal_bundle_id") or ""),
            "proposal_refs": proposal_refs,
            "baseline_config_ref": str(
                dict(gate_result.get("baseline") or {}).get("config_ref") or active_config_ref
            ),
        },
    )
    auto_applied = bool(getattr(controller, "auto_apply_mutation", False))
    if auto_applied:
        reload_model = getattr(controller, "_reload_investment_model", None)
        if callable(reload_model):
            reload_model(mutation["config_path"])
    candidate_identity = build_candidate_identity(
        config_ref=str(mutation["config_path"]),
        config_payload=dict(mutation.get("config") or {}),
        model_name=model_name,
    )
    event = event_factory(
        cycle_id=int(cycle_id),
        trigger=trigger_reason,
        stage="candidate_build",
        decision={
            "config_path": mutation["config_path"],
            "meta_path": mutation.get("meta_path"),
            "auto_applied": auto_applied,
            "proposal_bundle_id": str(bundle.get("proposal_bundle_id") or ""),
            "proposal_bundle_path": str(bundle.get("bundle_path") or ""),
            "candidate_version_id": str(candidate_identity.get("version_id") or ""),
            "candidate_runtime_fingerprint": str(candidate_identity.get("runtime_fingerprint") or ""),
        },
        applied_change={
            "params": param_adjustments,
            "scoring": scoring_adjustments,
            "agent_weights": agent_weight_adjustments,
            "proposal_refs": proposal_refs,
            "proposal_count": len(proposal_refs),
        },
        lineage=build_optimization_event_lineage(
            cycle_id=int(cycle_id),
            model_name=model_name,
            active_config_ref=active_config_ref,
            candidate_config_ref=str(mutation["config_path"]),
            promotion_status="candidate_auto_applied" if auto_applied else "candidate_generated",
            deployment_stage="active" if auto_applied else "candidate",
            review_basis_window={},
            fitness_source_cycles=fitness_source_cycles,
            runtime_override_keys=runtime_override_keys,
        ),
        evidence={
            "mutation_meta": dict(mutation.get("meta") or {}),
            "auto_applied": auto_applied,
            "proposal_bundle_id": str(bundle.get("proposal_bundle_id") or ""),
            "proposal_bundle_path": str(bundle.get("bundle_path") or ""),
            "proposal_source_summary": dict(
                dict(gate_result.get("proposal_summary") or {}).get("requested_source_summary") or {}
            ),
            "proposal_gate": gate_result,
        },
        notes="active model config mutated" if auto_applied else "candidate model config generated; active config unchanged",
    )
    updated_bundle = _refresh_bundle_tracking(
        controller,
        cycle_id=int(cycle_id),
        bundle=bundle,
        gate_result=gate_result,
        decision_stage="candidate_build",
        decision_reason="candidate_generated" if not auto_applied else "candidate_auto_applied",
        candidate_config_ref=str(mutation["config_path"]),
        candidate_version_id=str(candidate_identity.get("version_id") or ""),
    )
    event.evidence["suggestion_tracking_summary"] = dict(
        updated_bundle.get("suggestion_tracking_summary") or {}
    )
    controller._append_optimization_event(event)
    controller._emit_module_log(
        "optimization",
        "模型配置已变异",
        (
            f"新的模型配置已生成并已接管 active：{mutation['config_path']}"
            if auto_applied
            else f"新的候选模型配置已生成（未自动接管 active）：{mutation['config_path']}"
        ),
        cycle_id=cycle_id,
        kind="candidate_build",
        details=dict(event.evidence or {}),
        metrics={"adjustment_count": len(runtime_override_keys)},
    )
    return event
