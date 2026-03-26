from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.train import SelfLearningController
from app.training.reporting import build_training_audit_semantics, generate_training_report
from app.validation.phase0 import (
    _resolve_output_path,
    build_calibration_experiment_spec,
    load_controller_run_summary,
    load_cutoff_dates_from_run,
)
from config import normalize_date
from invest.models import resolve_model_config_path
from invest.shared.model_governance import (
    canonicalize_candidate_build_source,
    canonicalize_candidate_build_stage,
    latest_candidate_build_event,
    normalize_config_ref,
)


def _normalize_runtime_train_overrides(
    overrides: dict[str, Any] | None = None,
) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for key, value in dict(overrides or {}).items():
        if value is None or not str(value).strip():
            continue
        normalized[str(key)] = int(value)
    return normalized


def resolve_validation_cutoff_dates(
    *,
    cutoff_dates: list[str] | None = None,
    cutoff_source_run: str | Path | None = None,
    limit: int | None = None,
) -> list[str]:
    resolved = [str(item).strip() for item in list(cutoff_dates or []) if str(item).strip()]
    if not resolved and cutoff_source_run is not None:
        resolved = load_cutoff_dates_from_run(cutoff_source_run)
    if limit is not None:
        resolved = resolved[: max(0, int(limit))]
    if not resolved:
        raise ValueError("No cutoff dates resolved for prephase1 validation run")
    return resolved


def build_prephase1_validation_spec(
    *,
    model_name: str,
    cutoff_dates: list[str],
    min_history_days: int,
    simulation_days: int,
    dry_run_llm: bool = True,
    runtime_train_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = build_calibration_experiment_spec(
        model_name=model_name,
        cutoff_dates=cutoff_dates,
        min_history_days=min_history_days,
        simulation_days=simulation_days,
        dry_run_llm=dry_run_llm,
    )
    normalized_runtime_overrides = _normalize_runtime_train_overrides(runtime_train_overrides)
    if normalized_runtime_overrides:
        spec["optimization"] = {
            "runtime_train_overrides": normalized_runtime_overrides,
        }
    return spec


def extract_latest_candidate(cycles: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    candidate_index: dict[str, dict[str, Any]] = {}
    for cycle in list(cycles or []):
        cycle_payload = dict(cycle or {})
        cycle_id = int(cycle_payload.get("cycle_id") or 0)
        for event in list(cycle_payload.get("optimization_events") or []):
            payload = dict(event or {})
            if canonicalize_candidate_build_stage(payload.get("stage")) != "candidate_build":
                continue
            decision = dict(payload.get("decision") or {})
            config_path = str(
                decision.get("config_path")
                or decision.get("pending_candidate_ref")
                or ""
            ).strip()
            if not config_path:
                continue
            applied_change = dict(payload.get("applied_change") or {})
            candidate_index[config_path] = {
                "cycle_id": cycle_id,
                "candidate_version_id": str(decision.get("candidate_version_id") or ""),
                "candidate_runtime_fingerprint": str(
                    decision.get("candidate_runtime_fingerprint") or ""
                ),
                "proposal_refs": list(applied_change.get("proposal_refs") or []),
            }

    for cycle in reversed(list(cycles or [])):
        optimization_events = list(dict(cycle or {}).get("optimization_events") or [])
        event = latest_candidate_build_event(optimization_events)
        if not event:
            continue
        decision = dict(event.get("decision") or {})
        applied_change = dict(event.get("applied_change") or {})
        config_path = str(
            decision.get("config_path")
            or decision.get("pending_candidate_ref")
            or ""
        ).strip()
        if not config_path:
            continue
        indexed = dict(candidate_index.get(config_path) or {})
        return {
            "cycle_id": int(dict(cycle or {}).get("cycle_id") or 0),
            "config_path": config_path,
            "candidate_version_id": str(
                decision.get("candidate_version_id")
                or indexed.get("candidate_version_id")
                or ""
            ),
            "candidate_runtime_fingerprint": str(
                decision.get("candidate_runtime_fingerprint")
                or indexed.get("candidate_runtime_fingerprint")
                or ""
            ),
            "proposal_refs": list(
                applied_change.get("proposal_refs")
                or indexed.get("proposal_refs")
                or []
            ),
        }
    return {}


def extract_latest_proposal_gate(cycles: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    for cycle in reversed(list(cycles or [])):
        optimization_events = list(dict(cycle or {}).get("optimization_events") or [])
        for event in reversed(optimization_events):
            payload = dict(event or {})
            proposal_gate = dict(dict(payload.get("evidence") or {}).get("proposal_gate") or {})
            if not proposal_gate:
                continue
            return {
                "approved": bool(
                    dict(proposal_gate.get("proposal_summary") or {}).get("approved_proposal_count")
                ),
                "cycle_id": int(dict(cycle or {}).get("cycle_id") or 0),
                **proposal_gate,
            }
    return {}


_CANDIDATE_TERMINAL_STATUSES = {
    "candidate_applied",
    "candidate_pruned",
    "candidate_expired",
    "override_expired",
}

_LEGACY_STAGE_ALIASES = {
    "yaml_mutation": "candidate_build",
    "yaml_mutation_skipped": "candidate_build_skipped",
}

_LEGACY_SOURCE_ALIASES = {
    "runtime_yaml_mutation": "runtime_candidate_builder",
}


def _ensure_candidate_entry(
    candidates: dict[str, dict[str, Any]],
    *,
    config_ref: str,
    cycle_id: int = 0,
    cutoff_date: str = "",
    version_id: str = "",
    runtime_fingerprint: str = "",
    proposal_refs: list[str] | None = None,
) -> dict[str, Any]:
    normalized_ref = normalize_config_ref(config_ref)
    entry = candidates.setdefault(
        normalized_ref,
        {
            "candidate_config_ref": normalized_ref,
            "created_cycle_id": int(cycle_id or 0),
            "created_cutoff_date": str(cutoff_date or ""),
            "candidate_version_id": str(version_id or ""),
            "candidate_runtime_fingerprint": str(runtime_fingerprint or ""),
            "proposal_refs": list(proposal_refs or []),
            "path": [],
        },
    )
    if cycle_id and not int(entry.get("created_cycle_id") or 0):
        entry["created_cycle_id"] = int(cycle_id)
        entry["created_cutoff_date"] = str(cutoff_date or "")
    if version_id and not str(entry.get("candidate_version_id") or ""):
        entry["candidate_version_id"] = str(version_id)
    if runtime_fingerprint and not str(entry.get("candidate_runtime_fingerprint") or ""):
        entry["candidate_runtime_fingerprint"] = str(runtime_fingerprint)
    if proposal_refs:
        merged_refs = list(entry.get("proposal_refs") or [])
        for proposal_ref in proposal_refs:
            text = str(proposal_ref or "").strip()
            if text and text not in merged_refs:
                merged_refs.append(text)
        entry["proposal_refs"] = merged_refs
    return entry


def build_candidate_resolution_summary(
    cycles: list[dict[str, Any]] | None = None,
    *,
    target_candidate_ref: str | None = None,
) -> dict[str, Any]:
    sorted_cycles = sorted(
        [dict(item or {}) for item in list(cycles or [])],
        key=lambda payload: (
            int(payload.get("cycle_id") or 0),
            str(payload.get("cutoff_date") or ""),
        ),
    )
    target_ref = normalize_config_ref(target_candidate_ref) if target_candidate_ref else ""
    candidates: dict[str, dict[str, Any]] = {}

    for cycle in sorted_cycles:
        cycle_id = int(cycle.get("cycle_id") or 0)
        cutoff_date = str(cycle.get("cutoff_date") or "")
        for event in list(cycle.get("optimization_events") or []):
            payload = dict(event or {})
            if canonicalize_candidate_build_stage(payload.get("stage")) != "candidate_build":
                continue
            decision = dict(payload.get("decision") or {})
            applied_change = dict(payload.get("applied_change") or {})
            config_ref = normalize_config_ref(
                decision.get("config_path")
                or decision.get("pending_candidate_ref")
                or ""
            )
            if not config_ref:
                continue
            _ensure_candidate_entry(
                candidates,
                config_ref=config_ref,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                version_id=str(decision.get("candidate_version_id") or ""),
                runtime_fingerprint=str(decision.get("candidate_runtime_fingerprint") or ""),
                proposal_refs=list(applied_change.get("proposal_refs") or []),
            )

    if target_ref and target_ref not in candidates:
        for cycle in sorted_cycles:
            lineage = dict(cycle.get("lineage_record") or {})
            promotion = dict(cycle.get("promotion_record") or {})
            refs = [
                normalize_config_ref(lineage.get("candidate_config_ref") or ""),
                normalize_config_ref(promotion.get("candidate_config_ref") or ""),
                normalize_config_ref(lineage.get("active_config_ref") or ""),
            ]
            if target_ref in refs:
                _ensure_candidate_entry(
                    candidates,
                    config_ref=target_ref,
                    cycle_id=int(cycle.get("cycle_id") or 0),
                    cutoff_date=str(cycle.get("cutoff_date") or ""),
                )
                break

    for cycle in sorted_cycles:
        cycle_id = int(cycle.get("cycle_id") or 0)
        cutoff_date = str(cycle.get("cutoff_date") or "")
        lineage = dict(cycle.get("lineage_record") or {})
        promotion = dict(cycle.get("promotion_record") or {})
        discipline = dict(lineage.get("promotion_discipline") or promotion.get("discipline") or {})
        latest_event = latest_candidate_build_event(list(cycle.get("optimization_events") or []))
        latest_decision = dict(latest_event.get("decision") or {})
        event_refs = [
            normalize_config_ref(latest_decision.get("config_path") or ""),
            normalize_config_ref(latest_decision.get("pending_candidate_ref") or ""),
        ]
        for config_ref in (
            normalize_config_ref(lineage.get("candidate_config_ref") or ""),
            normalize_config_ref(promotion.get("candidate_config_ref") or ""),
            *event_refs,
        ):
            if not config_ref:
                continue
            entry = _ensure_candidate_entry(
                candidates,
                config_ref=config_ref,
                cycle_id=cycle_id,
                cutoff_date=cutoff_date,
                version_id=str(lineage.get("candidate_version_id") or promotion.get("candidate_version_id") or ""),
                runtime_fingerprint=str(
                    lineage.get("candidate_runtime_fingerprint")
                    or promotion.get("candidate_runtime_fingerprint")
                    or ""
                ),
            )
            step = {
                "cycle_id": cycle_id,
                "cutoff_date": cutoff_date,
                "lineage_status": str(lineage.get("lineage_status") or ""),
                "deployment_stage": str(
                    lineage.get("deployment_stage")
                    or promotion.get("deployment_stage")
                    or ""
                ),
                "promotion_gate_status": str(promotion.get("gate_status") or ""),
                "promotion_status": str(promotion.get("status") or ""),
                "discipline_status": str(discipline.get("status") or ""),
                "candidate_build_stage": str(latest_event.get("stage") or ""),
                "candidate_build_skip_reason": str(latest_decision.get("skip_reason") or ""),
                "candidate_build_pending_ref": normalize_config_ref(
                    latest_decision.get("pending_candidate_ref") or ""
                ),
                "resolution_reason": (
                    str(promotion.get("reason") or "")
                    or ",".join(str(item) for item in list(discipline.get("violations") or []))
                ),
            }
            path = list(entry.get("path") or [])
            if path and int(path[-1].get("cycle_id") or 0) == cycle_id:
                continue
            path.append(step)
            entry["path"] = path

    candidate_items: list[dict[str, Any]] = []
    resolution_status_counts: dict[str, int] = {}
    unresolved_refs: list[str] = []
    for config_ref in sorted(candidates):
        entry = dict(candidates.get(config_ref) or {})
        path = list(entry.get("path") or [])
        final_status = "candidate_pending"
        resolved_cycle_id = 0
        resolution_reason = ""
        for step in path:
            lineage_status = str(step.get("lineage_status") or "")
            gate_status = str(step.get("promotion_gate_status") or "")
            promotion_status = str(step.get("promotion_status") or "")
            discipline_status = str(step.get("discipline_status") or "")
            if lineage_status in _CANDIDATE_TERMINAL_STATUSES:
                final_status = lineage_status
                resolved_cycle_id = int(step.get("cycle_id") or 0)
                resolution_reason = str(step.get("resolution_reason") or "")
            elif gate_status == "applied_to_active" or promotion_status == "candidate_auto_applied":
                final_status = "candidate_applied"
                resolved_cycle_id = int(step.get("cycle_id") or 0)
                resolution_reason = str(step.get("resolution_reason") or "")
            elif discipline_status in _CANDIDATE_TERMINAL_STATUSES:
                final_status = discipline_status
                resolved_cycle_id = int(step.get("cycle_id") or 0)
                resolution_reason = str(step.get("resolution_reason") or "")
        resolved = final_status in _CANDIDATE_TERMINAL_STATUSES
        if not resolved:
            unresolved_refs.append(config_ref)
        resolution_status_counts[final_status] = resolution_status_counts.get(final_status, 0) + 1
        candidate_items.append(
            {
                **entry,
                "latest_observed_cycle_id": int(path[-1].get("cycle_id") or 0) if path else 0,
                "final_status": final_status,
                "resolved": resolved,
                "resolved_cycle_id": resolved_cycle_id,
                "resolution_reason": resolution_reason,
            }
        )

    focus_candidate = {}
    if target_ref:
        focus_candidate = dict(candidates.get(target_ref) or {})
        if focus_candidate:
            matching = next(
                (
                    item
                    for item in candidate_items
                    if str(item.get("candidate_config_ref") or "") == target_ref
                ),
                {},
            )
            focus_candidate = dict(matching or focus_candidate)

    return {
        "schema_version": "training.candidate_resolution_summary.v1",
        "candidate_count": len(candidate_items),
        "resolved_candidate_count": sum(1 for item in candidate_items if bool(item.get("resolved"))),
        "unresolved_candidate_count": sum(1 for item in candidate_items if not bool(item.get("resolved"))),
        "resolution_status_counts": resolution_status_counts,
        "unresolved_candidate_refs": unresolved_refs,
        "focus_candidate_ref": target_ref,
        "focus_candidate": focus_candidate,
        "candidates": candidate_items,
    }


def build_legacy_term_summary(
    cycles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    legacy_stage_counts: dict[str, int] = {}
    canonical_stage_counts: dict[str, int] = {}
    legacy_source_counts: dict[str, int] = {}
    canonical_source_counts: dict[str, int] = {}

    for cycle in list(cycles or []):
        for event in list(dict(cycle or {}).get("optimization_events") or []):
            payload = dict(event or {})
            raw_stage = str(payload.get("stage") or "").strip()
            canonical_stage = canonicalize_candidate_build_stage(raw_stage)
            if raw_stage:
                canonical_stage_counts[canonical_stage] = (
                    canonical_stage_counts.get(canonical_stage, 0) + 1
                )
                if raw_stage in _LEGACY_STAGE_ALIASES:
                    legacy_stage_counts[raw_stage] = legacy_stage_counts.get(raw_stage, 0) + 1

        for record in (
            dict(cycle.get("promotion_record") or {}),
            dict(cycle.get("lineage_record") or {}),
        ):
            for raw_source in (
                str(record.get("source") or "").strip(),
                str(record.get("mutation_source") or "").strip(),
            ):
                if not raw_source:
                    continue
                canonical_source = canonicalize_candidate_build_source(raw_source)
                canonical_source_counts[canonical_source] = (
                    canonical_source_counts.get(canonical_source, 0) + 1
                )
                if raw_source in _LEGACY_SOURCE_ALIASES:
                    legacy_source_counts[raw_source] = legacy_source_counts.get(raw_source, 0) + 1

    return {
        "legacy_terms_present": bool(legacy_stage_counts or legacy_source_counts),
        "legacy_stage_counts": legacy_stage_counts,
        "canonical_stage_counts": canonical_stage_counts,
        "legacy_source_counts": legacy_source_counts,
        "canonical_source_counts": canonical_source_counts,
        "canonicalization_map": {
            "stages": dict(_LEGACY_STAGE_ALIASES),
            "sources": dict(_LEGACY_SOURCE_ALIASES),
        },
    }


def _load_existing_validation_summary(run_dir: str | Path) -> dict[str, Any]:
    root = _resolve_output_path(run_dir)
    for filename in ("validation_summary.json", "normalized_validation_summary.json"):
        path = root / filename
        if not path.exists():
            continue
        try:
            return dict(json.loads(path.read_text(encoding="utf-8")) or {})
        except Exception:
            continue
    return {}


def _resolve_existing_validation_summary_path(run_dir: str | Path) -> str:
    root = _resolve_output_path(run_dir)
    for filename in ("validation_summary.json", "normalized_validation_summary.json"):
        path = root / filename
        if not path.exists():
            continue
        return str(path)
    return ""


def _infer_validation_metadata(
    cycles: list[dict[str, Any]] | None = None,
    *,
    existing_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cycle_items = [dict(item or {}) for item in list(cycles or [])]
    existing = dict(existing_summary or {})
    reference_cycle = {}
    for cycle in reversed(cycle_items):
        if cycle:
            reference_cycle = cycle
            break
    first_cycle = cycle_items[0] if cycle_items else {}
    experiment_spec = (
        dict(existing.get("experiment_spec") or {})
        or dict(reference_cycle.get("experiment_spec") or {})
        or dict(first_cycle.get("experiment_spec") or {})
    )
    runtime_train_overrides = dict(
        existing.get("runtime_train_overrides")
        or dict(experiment_spec.get("optimization") or {}).get("runtime_train_overrides")
        or {}
    )
    cutoff_dates = list(existing.get("cutoff_dates") or [])
    if not cutoff_dates:
        cutoff_dates = [
            str(dict(cycle).get("cutoff_date") or "").strip()
            for cycle in cycle_items
            if str(dict(cycle).get("cutoff_date") or "").strip()
        ]
    return {
        "model_name": str(
            existing.get("model_name")
            or reference_cycle.get("model_name")
            or first_cycle.get("model_name")
            or list(dict(experiment_spec.get("model_scope") or {}).get("allowed_models") or [""])[0]
            or ""
        ),
        "model_config_path": str(
            existing.get("model_config_path")
            or existing.get("config_path")
            or reference_cycle.get("config_path")
            or reference_cycle.get("config_name")
            or first_cycle.get("config_path")
            or first_cycle.get("config_name")
            or ""
        ),
        "llm_mode": str(
            existing.get("llm_mode")
            or reference_cycle.get("llm_mode")
            or first_cycle.get("llm_mode")
            or ("dry_run" if bool(dict(experiment_spec.get("llm") or {}).get("dry_run")) else "live")
            or "dry_run"
        ),
        "cutoff_dates": cutoff_dates,
        "experiment_spec": experiment_spec,
        "runtime_train_overrides": runtime_train_overrides,
    }


def _derive_report_from_cycles(
    cycles: list[dict[str, Any]] | None = None,
    *,
    aggregate_summary: dict[str, Any] | None = None,
    existing_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = dict(existing_summary or {})
    if dict(existing.get("report") or {}):
        return dict(existing.get("report") or {})
    cycle_items = [dict(item or {}) for item in list(cycles or [])]
    if not cycle_items:
        return generate_training_report(
            total_cycle_attempts=0,
            skipped_cycle_count=0,
            cycle_history=[],
            current_params={},
            is_frozen=False,
            self_assessment={},
            research_feedback={},
            freeze_gate_evaluation={},
        )
    summary = dict(aggregate_summary or {})
    last_cycle = dict(cycle_items[-1] or {})
    current_params = (
        dict(last_cycle.get("params") or {})
        or dict(dict(last_cycle.get("run_context") or {}).get("runtime_overrides") or {})
        or {}
    )
    self_assessment = dict(last_cycle.get("self_assessment") or {})
    research_feedback = dict(last_cycle.get("research_feedback") or {})
    is_frozen = bool(existing.get("freeze_applied", existing.get("is_frozen", False)))
    report_cycle_history = [SimpleNamespace(**item) for item in cycle_items]
    return generate_training_report(
        total_cycle_attempts=int(summary.get("cycle_count") or len(cycle_items)),
        skipped_cycle_count=int(summary.get("skipped_cycle_count") or 0),
        cycle_history=report_cycle_history,
        current_params=current_params,
        is_frozen=is_frozen,
        self_assessment=self_assessment,
        research_feedback=research_feedback,
        freeze_gate_evaluation=dict(existing.get("freeze_gate_evaluation") or {}),
    )


def build_normalized_validation_summary(
    *,
    run_dir: str | Path,
    output_dir: str | Path | None = None,
    loaded_summary: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
    existing_summary: dict[str, Any] | None = None,
    run_type: str = "prephase1_validation",
    target_candidate_ref: str | None = None,
    extra_sections: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_run_dir = _resolve_output_path(run_dir)
    artifact_dir = _resolve_output_path(output_dir or run_dir)
    loaded = dict(loaded_summary or {})
    cycles = [dict(item or {}) for item in list(loaded.get("cycles") or [])]
    existing = dict(existing_summary or {})
    metadata = _infer_validation_metadata(cycles, existing_summary=existing)
    latest_candidate = extract_latest_candidate(cycles)
    focus_candidate_ref = str(
        target_candidate_ref
        or existing.get("focus_candidate_ref")
        or latest_candidate.get("config_path")
        or ""
    )
    resolution_summary = build_candidate_resolution_summary(
        cycles,
        target_candidate_ref=focus_candidate_ref or None,
    )
    resolution_summary["candidate_resolution_summary_path"] = str(
        artifact_dir / "candidate_resolution_summary.json"
    )
    resolution_summary["run_dir"] = str(source_run_dir)
    legacy_term_summary = build_legacy_term_summary(cycles)
    audit_semantics = build_training_audit_semantics()
    aggregate_summary = dict(loaded.get("summary") or {})
    normalized_report = dict(
        report
        or _derive_report_from_cycles(
            cycles,
            aggregate_summary=aggregate_summary,
            existing_summary=existing,
        )
        or {}
    )
    summary = {
        "schema_version": "prephase1.validation_summary.v2",
        "terminology_version": str(audit_semantics.get("terminology_version") or ""),
        "run_type": str(run_type or "prephase1_validation"),
        "normalized_at": datetime.now().isoformat(),
        "source_run_dir": str(source_run_dir),
        "model_name": str(metadata.get("model_name") or ""),
        "model_config_path": str(metadata.get("model_config_path") or ""),
        "llm_mode": str(metadata.get("llm_mode") or "dry_run"),
        "cutoff_dates": list(metadata.get("cutoff_dates") or []),
        "output_dir": str(artifact_dir),
        "runtime_train_overrides": dict(metadata.get("runtime_train_overrides") or {}),
        "audit_semantics": audit_semantics,
        "report": normalized_report,
        "summary": aggregate_summary,
        "experiment_spec": dict(metadata.get("experiment_spec") or {}),
        "latest_candidate": latest_candidate,
        "latest_proposal_gate": extract_latest_proposal_gate(cycles),
        "candidate_resolution_summary": dict(resolution_summary),
        "legacy_term_summary": legacy_term_summary,
        "audit_summary": {
            "freeze_applied": bool(
                normalized_report.get("freeze_applied", normalized_report.get("is_frozen", False))
            ),
            "governance_metrics": dict(normalized_report.get("governance_metrics") or {}),
            "proposal_gate_summary": dict(normalized_report.get("proposal_gate_summary") or {}),
            "legacy_terms_present": bool(legacy_term_summary.get("legacy_terms_present")),
            "candidate_resolution": {
                "candidate_count": int(resolution_summary.get("candidate_count") or 0),
                "resolved_candidate_count": int(
                    resolution_summary.get("resolved_candidate_count") or 0
                ),
                "unresolved_candidate_count": int(
                    resolution_summary.get("unresolved_candidate_count") or 0
                ),
                "resolution_status_counts": dict(
                    resolution_summary.get("resolution_status_counts") or {}
                ),
            },
        },
    }
    if extra_sections:
        summary.update(dict(extra_sections or {}))
    summary["validation_summary_path"] = str(artifact_dir / "validation_summary.json")
    summary["normalized_validation_summary_path"] = str(
        artifact_dir / "normalized_validation_summary.json"
    )
    return summary


def persist_validation_summary(
    *,
    output_dir: str | Path,
    payload: dict[str, Any],
) -> Path:
    run_dir = _resolve_output_path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "validation_summary.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def persist_normalized_validation_summary(
    *,
    output_dir: str | Path,
    payload: dict[str, Any],
) -> Path:
    run_dir = _resolve_output_path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "normalized_validation_summary.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def persist_candidate_resolution_summary(
    *,
    output_dir: str | Path,
    payload: dict[str, Any],
) -> Path:
    run_dir = _resolve_output_path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "candidate_resolution_summary.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _persist_validation_artifacts(
    *,
    output_dir: str | Path,
    payload: dict[str, Any],
    write_validation_summary: bool = True,
) -> dict[str, Any]:
    artifact_dir = _resolve_output_path(output_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    persisted = dict(payload or {})
    persisted["output_dir"] = str(artifact_dir)
    persisted["validation_summary_path"] = (
        str(artifact_dir / "validation_summary.json") if write_validation_summary else ""
    )
    persisted["validation_summary_written"] = bool(write_validation_summary)
    persisted["normalized_validation_summary_path"] = str(
        artifact_dir / "normalized_validation_summary.json"
    )
    resolution_summary = dict(persisted.get("candidate_resolution_summary") or {})
    resolution_summary["candidate_resolution_summary_path"] = str(
        artifact_dir / "candidate_resolution_summary.json"
    )
    resolution_summary["run_dir"] = str(
        persisted.get("source_run_dir") or resolution_summary.get("run_dir") or artifact_dir
    )
    persist_candidate_resolution_summary(
        output_dir=artifact_dir,
        payload=resolution_summary,
    )
    persisted["candidate_resolution_summary"] = resolution_summary
    if write_validation_summary:
        persist_validation_summary(output_dir=artifact_dir, payload=persisted)
    persist_normalized_validation_summary(output_dir=artifact_dir, payload=persisted)
    return persisted


def _build_summary_overrides(
    *,
    model_name: str | None = None,
    model_config_path: str | None = None,
    llm_mode: str | None = None,
    cutoff_dates: list[str] | None = None,
    experiment_spec: dict[str, Any] | None = None,
    runtime_train_overrides: dict[str, Any] | None = None,
    extra_sections: dict[str, Any] | None = None,
) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if model_name:
        overrides["model_name"] = str(model_name)
    if model_config_path:
        overrides["model_config_path"] = str(model_config_path)
    if llm_mode:
        overrides["llm_mode"] = str(llm_mode)
    if cutoff_dates is not None:
        overrides["cutoff_dates"] = list(cutoff_dates)
    if experiment_spec is not None:
        overrides["experiment_spec"] = dict(experiment_spec)
    if runtime_train_overrides is not None:
        overrides["runtime_train_overrides"] = dict(runtime_train_overrides)
    if extra_sections:
        overrides.update(dict(extra_sections or {}))
    return overrides


def _prepare_validation_controller(
    *,
    model_name: str,
    config_path: str | Path | None,
    output_dir: str | Path,
    cutoff_dates: list[str] | None,
    cutoff_source_run: str | Path | None,
    cutoff_limit: int | None,
    min_history_days: int,
    simulation_days: int,
    dry_run_llm: bool,
    runtime_train_overrides: dict[str, Any] | None,
) -> tuple[Any, Path, dict[str, Any], list[str], dict[str, int]]:
    resolved_cutoff_dates = resolve_validation_cutoff_dates(
        cutoff_dates=cutoff_dates,
        cutoff_source_run=cutoff_source_run,
        limit=cutoff_limit,
    )
    normalized_runtime_overrides = _normalize_runtime_train_overrides(runtime_train_overrides)
    resolved_config_path = str(
        _resolve_output_path(config_path)
        if config_path is not None
        else resolve_model_config_path(model_name)
    )
    run_dir = _resolve_output_path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    controller = SelfLearningController(
        output_dir=str(run_dir),
        meeting_log_dir=str(run_dir / "meetings"),
        config_audit_log_path=str(run_dir / "config_audit.jsonl"),
        config_snapshot_dir=str(run_dir / "snapshots"),
    )
    controller.stop_on_freeze = False
    controller.model_name = str(model_name)
    controller.model_config_path = resolved_config_path
    controller.current_params = {}
    controller.training_routing_service.reload_investment_model(
        controller,
        controller.model_config_path,
    )

    experiment_spec = build_prephase1_validation_spec(
        model_name=model_name,
        cutoff_dates=resolved_cutoff_dates,
        min_history_days=min_history_days,
        simulation_days=simulation_days,
        dry_run_llm=dry_run_llm,
        runtime_train_overrides=normalized_runtime_overrides,
    )
    controller.configure_experiment(experiment_spec)
    if dry_run_llm:
        controller.set_llm_dry_run(True)
    return (
        controller,
        run_dir,
        experiment_spec,
        resolved_cutoff_dates,
        normalized_runtime_overrides,
    )


def _finalize_validation_summary(
    *,
    source_run_dir: str | Path,
    output_dir: str | Path,
    loaded_summary: dict[str, Any],
    report: dict[str, Any] | None,
    existing_summary: dict[str, Any] | None = None,
    run_type: str,
    target_candidate_ref: str | None = None,
    write_validation_summary: bool = True,
    model_name: str | None = None,
    model_config_path: str | None = None,
    llm_mode: str | None = None,
    cutoff_dates: list[str] | None = None,
    experiment_spec: dict[str, Any] | None = None,
    runtime_train_overrides: dict[str, Any] | None = None,
    extra_sections: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = build_normalized_validation_summary(
        run_dir=source_run_dir,
        output_dir=output_dir,
        loaded_summary=loaded_summary,
        report=report,
        existing_summary=existing_summary,
        run_type=run_type,
        target_candidate_ref=target_candidate_ref,
        extra_sections=_build_summary_overrides(
            model_name=model_name,
            model_config_path=model_config_path,
            llm_mode=llm_mode,
            cutoff_dates=cutoff_dates,
            experiment_spec=experiment_spec,
            runtime_train_overrides=runtime_train_overrides,
            extra_sections=extra_sections,
        ),
    )
    return _persist_validation_artifacts(
        output_dir=output_dir,
        payload=summary,
        write_validation_summary=write_validation_summary,
    )


def run_candidate_resolution_validation(
    *,
    run_dir: str | Path,
    output_dir: str | Path | None = None,
    target_candidate_ref: str | None = None,
) -> dict[str, Any]:
    loaded = load_controller_run_summary(run_dir)
    resolution_summary = build_candidate_resolution_summary(
        list(loaded.get("cycles") or []),
        target_candidate_ref=target_candidate_ref,
    )
    resolved_output_dir = _resolve_output_path(output_dir or run_dir)
    resolution_summary["run_dir"] = str(_resolve_output_path(run_dir))
    resolution_summary["candidate_resolution_summary_path"] = str(
        resolved_output_dir / "candidate_resolution_summary.json"
    )
    persist_candidate_resolution_summary(
        output_dir=resolved_output_dir,
        payload=resolution_summary,
    )
    return resolution_summary


def run_legacy_audit_backfill(
    *,
    run_dir: str | Path,
    output_dir: str | Path | None = None,
    target_candidate_ref: str | None = None,
) -> dict[str, Any]:
    source_run_dir = _resolve_output_path(run_dir)
    artifact_dir = _resolve_output_path(output_dir or run_dir)
    loaded = load_controller_run_summary(source_run_dir)
    existing_summary = _load_existing_validation_summary(source_run_dir)
    return _finalize_validation_summary(
        source_run_dir=source_run_dir,
        output_dir=artifact_dir,
        loaded_summary=loaded,
        report=dict(existing_summary.get("report") or {}),
        existing_summary=existing_summary,
        run_type="legacy_audit_backfill",
        target_candidate_ref=target_candidate_ref,
        write_validation_summary=False,
        extra_sections={
            "backfill": {
                "backfilled_from_run_dir": str(source_run_dir),
                "source_validation_summary_path": _resolve_existing_validation_summary_path(
                    source_run_dir
                ),
                "source_validation_summary_present": bool(existing_summary),
            }
        },
    )


def run_terminal_candidate_resolution_validation(
    *,
    model_name: str,
    config_path: str | Path | None = None,
    cutoff_dates: list[str] | None = None,
    cutoff_source_run: str | Path | None = None,
    cutoff_limit: int | None = None,
    followup_cutoff_dates: list[str] | None = None,
    max_followup_cycles: int | None = None,
    output_dir: str | Path,
    min_history_days: int,
    simulation_days: int,
    dry_run_llm: bool = True,
    runtime_train_overrides: dict[str, Any] | None = None,
    target_candidate_ref: str | None = None,
) -> dict[str, Any]:
    (
        controller,
        run_dir,
        experiment_spec,
        resolved_cutoff_dates,
        normalized_runtime_overrides,
    ) = _prepare_validation_controller(
        model_name=model_name,
        config_path=config_path,
        output_dir=output_dir,
        cutoff_dates=cutoff_dates,
        cutoff_source_run=cutoff_source_run,
        cutoff_limit=cutoff_limit,
        min_history_days=min_history_days,
        simulation_days=simulation_days,
        dry_run_llm=dry_run_llm,
        runtime_train_overrides=runtime_train_overrides,
    )
    report = controller.run_continuous(max_cycles=len(resolved_cutoff_dates))
    loaded = load_controller_run_summary(run_dir)
    latest_candidate = extract_latest_candidate(list(loaded.get("cycles") or []))
    focus_candidate_ref = normalize_config_ref(
        target_candidate_ref or latest_candidate.get("config_path") or ""
    )
    followup_dates = [
        normalize_date(str(item))
        for item in list(followup_cutoff_dates or [])
        if str(item or "").strip()
    ]
    followup_limit = len(followup_dates)
    if max_followup_cycles is not None:
        followup_limit = min(followup_limit, max(0, int(max_followup_cycles)))
    followup_history: list[dict[str, Any]] = []

    for followup_cutoff in followup_dates[:followup_limit]:
        if not focus_candidate_ref:
            break
        current_resolution = build_candidate_resolution_summary(
            list(loaded.get("cycles") or []),
            target_candidate_ref=focus_candidate_ref,
        )
        focus_candidate = dict(current_resolution.get("focus_candidate") or {})
        if bool(focus_candidate.get("resolved")):
            break
        policy = dict(getattr(controller, "experiment_cutoff_policy", {}) or {})
        policy_dates = [
            normalize_date(str(item))
            for item in list(policy.get("dates") or resolved_cutoff_dates)
            if str(item or "").strip()
        ]
        policy_dates.append(followup_cutoff)
        policy["mode"] = "sequence"
        policy["dates"] = policy_dates
        controller.experiment_cutoff_policy = policy
        report = controller.run_continuous(max_cycles=1)
        loaded = load_controller_run_summary(run_dir)
        updated_resolution = build_candidate_resolution_summary(
            list(loaded.get("cycles") or []),
            target_candidate_ref=focus_candidate_ref,
        )
        updated_focus = dict(updated_resolution.get("focus_candidate") or {})
        followup_history.append(
            {
                "cutoff_date": followup_cutoff,
                "resolved": bool(updated_focus.get("resolved")),
                "final_status": str(updated_focus.get("final_status") or ""),
                "resolved_cycle_id": int(updated_focus.get("resolved_cycle_id") or 0),
            }
        )
        if bool(updated_focus.get("resolved")):
            break

    final_resolution = build_candidate_resolution_summary(
        list(loaded.get("cycles") or []),
        target_candidate_ref=focus_candidate_ref or None,
    )
    final_focus_candidate = dict(final_resolution.get("focus_candidate") or {})
    return _finalize_validation_summary(
        source_run_dir=run_dir,
        output_dir=run_dir,
        loaded_summary=loaded,
        report=report,
        run_type="terminal_candidate_resolution_validation",
        target_candidate_ref=focus_candidate_ref or None,
        write_validation_summary=True,
        model_name=str(model_name),
        model_config_path=str(getattr(controller, "model_config_path", "") or ""),
        llm_mode=str(getattr(controller, "llm_mode", "dry_run") or "dry_run"),
        experiment_spec=experiment_spec,
        runtime_train_overrides=normalized_runtime_overrides,
        extra_sections={
            "terminal_resolution": {
                "target_candidate_ref": focus_candidate_ref,
                "initial_cycle_count": len(resolved_cutoff_dates),
                "followup_cycle_count": len(followup_history),
                "followup_cutoff_dates": followup_dates[:followup_limit],
                "followup_history": followup_history,
                "terminal_reached": bool(final_focus_candidate.get("resolved")),
                "terminal_status": str(
                    final_focus_candidate.get("final_status")
                    or ("no_candidate_generated" if not focus_candidate_ref else "candidate_pending")
                ),
                "resolved_cycle_id": int(final_focus_candidate.get("resolved_cycle_id") or 0),
            }
        },
    )


def run_terminal_candidate_resolution_from_existing_run(
    *,
    run_dir: str | Path,
    followup_cutoff_dates: list[str] | None = None,
    max_followup_cycles: int | None = None,
    output_dir: str | Path | None = None,
    target_candidate_ref: str | None = None,
) -> dict[str, Any]:
    source_run_dir = _resolve_output_path(run_dir)
    loaded = load_controller_run_summary(source_run_dir)
    existing_summary = _load_existing_validation_summary(source_run_dir)
    metadata = _infer_validation_metadata(
        list(loaded.get("cycles") or []),
        existing_summary=existing_summary,
    )
    model_name = str(metadata.get("model_name") or "").strip()
    if not model_name:
        raise ValueError(f"Unable to infer model_name from existing run: {source_run_dir}")
    experiment_spec = dict(metadata.get("experiment_spec") or {})
    dataset = dict(experiment_spec.get("dataset") or {})
    return run_terminal_candidate_resolution_validation(
        model_name=model_name,
        config_path=metadata.get("model_config_path") or None,
        cutoff_dates=list(metadata.get("cutoff_dates") or load_cutoff_dates_from_run(source_run_dir)),
        output_dir=output_dir or source_run_dir,
        min_history_days=int(dataset.get("min_history_days") or 200),
        simulation_days=int(dataset.get("simulation_days") or 30),
        dry_run_llm=str(metadata.get("llm_mode") or "dry_run") == "dry_run",
        runtime_train_overrides=dict(metadata.get("runtime_train_overrides") or {}),
        followup_cutoff_dates=followup_cutoff_dates,
        max_followup_cycles=max_followup_cycles,
        target_candidate_ref=target_candidate_ref,
    )


def run_prephase1_validation(
    *,
    model_name: str,
    config_path: str | Path | None = None,
    cutoff_dates: list[str] | None = None,
    cutoff_source_run: str | Path | None = None,
    cutoff_limit: int | None = None,
    output_dir: str | Path,
    min_history_days: int,
    simulation_days: int,
    dry_run_llm: bool = True,
    runtime_train_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    (
        controller,
        run_dir,
        experiment_spec,
        resolved_cutoff_dates,
        normalized_runtime_overrides,
    ) = _prepare_validation_controller(
        model_name=model_name,
        config_path=config_path,
        output_dir=output_dir,
        cutoff_dates=cutoff_dates,
        cutoff_source_run=cutoff_source_run,
        cutoff_limit=cutoff_limit,
        min_history_days=min_history_days,
        simulation_days=simulation_days,
        dry_run_llm=dry_run_llm,
        runtime_train_overrides=runtime_train_overrides,
    )
    report = controller.run_continuous(max_cycles=len(resolved_cutoff_dates))
    loaded = load_controller_run_summary(run_dir)
    return _finalize_validation_summary(
        source_run_dir=run_dir,
        output_dir=run_dir,
        loaded_summary=loaded,
        report=report,
        run_type="prephase1_validation",
        write_validation_summary=True,
        model_name=str(model_name),
        model_config_path=str(getattr(controller, "model_config_path", "") or ""),
        llm_mode=str(getattr(controller, "llm_mode", "dry_run") or "dry_run"),
        cutoff_dates=list(resolved_cutoff_dates),
        experiment_spec=experiment_spec,
        runtime_train_overrides=normalized_runtime_overrides,
    )


def _parse_runtime_override_args(items: list[str] | None = None) -> dict[str, int]:
    overrides: dict[str, int] = {}
    for item in list(items or []):
        text = str(item or "").strip()
        if not text:
            continue
        if "=" not in text:
            raise ValueError(f"Invalid runtime override: {text}")
        key, raw_value = text.split("=", 1)
        key = str(key).strip()
        raw_value = str(raw_value).strip()
        if not key or not raw_value:
            raise ValueError(f"Invalid runtime override: {text}")
        overrides[key] = int(raw_value)
    return overrides


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run standardized Pre-Phase1 validation with validation-mode isolation",
    )
    parser.add_argument("--model", required=True, help="Pinned model name")
    parser.add_argument("--config-path", default=None, help="Optional explicit model config path")
    parser.add_argument("--cutoff-source-run", default=None, help="Load cutoff dates from an existing run directory")
    parser.add_argument("--cutoff-date", action="append", default=None, help="Explicit cutoff date (repeatable)")
    parser.add_argument("--cutoff-limit", type=int, default=None, help="Optional cutoff-date limit after resolution")
    parser.add_argument("--output-dir", required=True, help="Validation output directory")
    parser.add_argument("--min-history-days", type=int, default=200, help="Minimum history days")
    parser.add_argument("--simulation-days", type=int, default=30, help="Simulation days")
    parser.add_argument(
        "--runtime-train-override",
        action="append",
        default=None,
        help="Runtime train override in key=value form, e.g. max_losses_before_optimize=1",
    )
    parser.add_argument(
        "--llm-mode",
        choices=("dry_run", "live"),
        default="dry_run",
        help="LLM mode for validation run",
    )

    args = parser.parse_args()
    summary = run_prephase1_validation(
        model_name=str(args.model),
        config_path=args.config_path,
        cutoff_dates=list(args.cutoff_date or []),
        cutoff_source_run=args.cutoff_source_run,
        cutoff_limit=args.cutoff_limit,
        output_dir=args.output_dir,
        min_history_days=int(args.min_history_days),
        simulation_days=int(args.simulation_days),
        dry_run_llm=str(args.llm_mode) == "dry_run",
        runtime_train_overrides=_parse_runtime_override_args(args.runtime_train_override),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
