"""Agent runtime presentation, structured output, and transcript snapshots."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .planner import (
    RISK_LEVEL_HIGH,
    RISK_LEVEL_LOW,
    RISK_LEVEL_MEDIUM,
    build_artifact_taxonomy,
    build_bounded_workflow_protocol,
    build_gate_feedback,
    build_next_action,
    build_workflow_phase_coverage,
)


def _dict_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _list_payload(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return list(value)


def _validate_type(value: Any, expected: type | tuple[type, ...], *, label: str) -> list[str]:
    if isinstance(value, expected):
        return []
    expected_name = (
        ", ".join(item.__name__ for item in expected)
        if isinstance(expected, tuple)
        else expected.__name__
    )
    return [f"{label} should be {expected_name}"]


def _validation_errors(validator: Any, payload: dict[str, Any]) -> list[str]:
    if not callable(validator):
        return []
    result = validator(payload)
    return [str(item) for item in _list_payload(result) if str(item or "")]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return int(default)
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _brief_training_result(item: dict[str, Any]) -> dict[str, Any]:
    payload = _dict_payload(item)
    return {
        "cycle_id": payload.get("cycle_id"),
        "status": str(payload.get("status") or ""),
        "return_pct": payload.get("return_pct"),
        "benchmark_passed": bool(payload.get("benchmark_passed", False)),
        "promotion_record": _dict_payload(payload.get("promotion_record")),
        "lineage_record": _dict_payload(payload.get("lineage_record")),
    }


def _brief_training_plan_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = _dict_payload(item)
    return {
        "path": str(payload.get("path") or ""),
        "name": str(payload.get("name") or ""),
        "plan_id": str(payload.get("plan_id") or ""),
        "status": str(payload.get("status") or ""),
        "created_at": str(payload.get("created_at") or ""),
        "last_run_id": str(payload.get("last_run_id") or ""),
        "last_run_at": str(payload.get("last_run_at") or ""),
        "spec": _dict_payload(payload.get("spec")),
        "artifacts": _dict_payload(payload.get("artifacts")),
    }


def _brief_training_run_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = _dict_payload(item)
    run_payload = _dict_payload(payload.get("payload"))
    results = [dict(entry) for entry in _list_payload(run_payload.get("results")) if isinstance(entry, dict)]
    latest_result = _dict_payload(payload.get("latest_result")) or (dict(results[-1]) if results else {})
    return {
        "path": str(payload.get("path") or ""),
        "name": str(payload.get("name") or ""),
        "run_id": str(payload.get("run_id") or ""),
        "plan_id": str(payload.get("plan_id") or ""),
        "status": str(payload.get("status") or ""),
        "created_at": str(payload.get("created_at") or ""),
        "artifacts": _dict_payload(payload.get("artifacts")),
        "latest_result": _brief_training_result(latest_result),
    }


def _brief_training_evaluation_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = _dict_payload(item)
    assessment = _dict_payload(payload.get("assessment"))
    promotion = _dict_payload(payload.get("promotion"))
    return {
        "path": str(payload.get("path") or ""),
        "name": str(payload.get("name") or ""),
        "run_id": str(payload.get("run_id") or ""),
        "plan_id": str(payload.get("plan_id") or ""),
        "status": str(payload.get("status") or ""),
        "created_at": str(payload.get("created_at") or ""),
        "assessment": {
            "success_count": _safe_int(assessment.get("success_count"), 0),
            "no_data_count": _safe_int(assessment.get("no_data_count"), 0),
            "error_count": _safe_int(assessment.get("error_count"), 0),
            "avg_return_pct": assessment.get("avg_return_pct"),
            "benchmark_pass_rate": assessment.get("benchmark_pass_rate"),
            "latest_result": _dict_payload(assessment.get("latest_result")),
        },
        "promotion": {
            "verdict": str(promotion.get("verdict") or ""),
            "passed": bool(promotion.get("passed", False)),
            "research_feedback": _dict_payload(promotion.get("research_feedback")),
        },
        "governance_metrics": _dict_payload(payload.get("governance_metrics")),
        "realism_summary": _dict_payload(payload.get("realism_summary")),
    }


def _brief_agent_prompt_config(item: dict[str, Any]) -> dict[str, Any]:
    payload = _dict_payload(item)
    return {
        "name": str(payload.get("name") or ""),
        "role": str(payload.get("role") or payload.get("name") or ""),
        "system_prompt": str(payload.get("system_prompt") or ""),
    }


def read_runtime_governance_payload(payload: dict[str, Any]) -> dict[str, Any]:
    brain_payload = dict(payload.get("brain") or {})
    return dict(
        brain_payload.get("governance_metrics")
        or dict(payload.get("governance_metrics") or {}).get("runtime")
        or {}
    )


def project_runtime_governance_display_payload(payload: dict[str, Any]) -> dict[str, Any]:
    runtime_governance = read_runtime_governance_payload(payload)
    structured = _dict_payload(runtime_governance.get("structured_output"))
    guardrails = _dict_payload(runtime_governance.get("guardrails"))
    return {
        "available": bool(runtime_governance),
        "guardrail_blocks": _safe_int(guardrails.get("block_count"), 0),
        "validated_count": _safe_int(structured.get("validated_count"), 0),
        "repaired_count": _safe_int(structured.get("repaired_count"), 0),
        "fallback_count": _safe_int(structured.get("fallback_count"), 0),
        "reason_codes": [
            str(item)
            for item in _list_payload(guardrails.get("last_reason_codes"))
            if str(item or "").strip()
        ],
    }


def read_latest_training_result(payload: dict[str, Any]) -> dict[str, Any]:
    training_lab = dict(payload.get("training_lab") or {})
    run = dict(training_lab.get("run") or {})
    latest = dict(run.get("latest_result") or payload.get("latest_result") or {})
    if latest:
        return latest
    run_payload = dict(payload.get("payload") or {})
    results = [
        dict(item)
        for item in list(run_payload.get("results") or payload.get("results") or [])
        if isinstance(item, dict)
    ]
    return dict(results[-1]) if results else {}


NormalizePayloadFn = Callable[[dict[str, Any]], dict[str, Any]]
ValidatePayloadFn = Callable[[dict[str, Any]], list[str]]
FallbackPayloadFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
CoercionNotesFn = Callable[[dict[str, Any]], list[str]]
BriefItemFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class StructuredToolSpec:
    normalize: NormalizePayloadFn
    validate: ValidatePayloadFn
    fallback: FallbackPayloadFn
    coercions: CoercionNotesFn | None = None


def _resolve_fallback_status(
    raw: dict[str, Any],
    normalized: dict[str, Any],
    *,
    default: str,
) -> str:
    return str(raw.get("status") or normalized.get("status") or default)


def _fallback_list_items(
    normalized: dict[str, Any],
    *,
    field_name: str,
    projector: BriefItemFn | None = None,
) -> list[Any]:
    items = [item for item in _list_payload(normalized.get(field_name))]
    if projector is None:
        return items
    return [
        projector(item)
        for item in items
        if isinstance(item, dict)
    ]


def _build_fallback_transport_payload(
    *,
    raw: dict[str, Any],
    normalized: dict[str, Any],
    default_status: str = "ok",
    dict_fields: tuple[str, ...] = (),
    list_fields: tuple[str, ...] = (),
    bool_fields: tuple[str, ...] = (),
    string_fields: tuple[str, ...] = (),
    projected_list_fields: dict[str, BriefItemFn] | None = None,
    int_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": _resolve_fallback_status(
            raw,
            normalized,
            default=default_status,
        ),
    }
    projected_list_fields = dict(projected_list_fields or {})
    for field_name in dict_fields:
        payload[field_name] = _dict_payload(normalized.get(field_name))
    for field_name in list_fields:
        payload[field_name] = _fallback_list_items(
            normalized,
            field_name=field_name,
            projector=projected_list_fields.get(field_name),
        )
    for field_name in bool_fields:
        payload[field_name] = bool(normalized.get(field_name, False))
    for field_name in string_fields:
        payload[field_name] = str(normalized.get(field_name) or "")
    for field_name in int_fields:
        payload[field_name] = _safe_int(normalized.get(field_name), 0)
    return payload


def _build_counted_list_transport_payload(
    *,
    raw: dict[str, Any],
    normalized: dict[str, Any],
    item_field: str = "items",
    count_field: str = "count",
    projector: BriefItemFn | None = None,
) -> dict[str, Any]:
    items = _fallback_list_items(
        normalized,
        field_name=item_field,
        projector=projector,
    )
    payload = _build_fallback_transport_payload(
        raw=raw,
        normalized=normalized,
        list_fields=(item_field,),
        projected_list_fields={item_field: projector} if projector is not None else None,
        int_fields=(count_field,),
    )
    payload[count_field] = _safe_int(normalized.get(count_field), len(items))
    return payload


def _build_coercion_notes(
    raw: dict[str, Any],
    *,
    dict_fields: tuple[str, ...] = (),
    list_fields: tuple[str, ...] = (),
    int_fields: tuple[str, ...] = (),
    string_fields: tuple[str, ...] = (),
) -> list[str]:
    notes: list[str] = []
    for field_name in dict_fields:
        if field_name in raw and not isinstance(raw.get(field_name), dict):
            notes.append(f"{field_name}_coerced_to_dict")
    for field_name in list_fields:
        if field_name in raw and not isinstance(raw.get(field_name), list):
            notes.append(f"{field_name}_coerced_to_list")
    for field_name in int_fields:
        if field_name in raw and not isinstance(raw.get(field_name), int):
            notes.append(f"{field_name}_coerced_to_int")
    for field_name in string_fields:
        if field_name in raw and not isinstance(raw.get(field_name), str):
            notes.append(f"{field_name}_coerced_to_string")
    return notes


def _build_status_count_items_spec(item_brief: BriefItemFn) -> StructuredToolSpec:
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        items = [item_brief(item) for item in _list_payload(normalized.get("items")) if isinstance(item, dict)]
        normalized["status"] = str(payload.get("status") or "ok")
        normalized["count"] = _safe_int(payload.get("count"), len(items))
        normalized["items"] = items
        return normalized

    def _validate(payload: dict[str, Any]) -> list[str]:
        errors = _validate_type(payload.get("status"), str, label="status")
        errors.extend(_validate_type(payload.get("count"), int, label="count"))
        errors.extend(_validate_type(payload.get("items"), list, label="items"))
        return errors

    def _fallback(raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        return _build_counted_list_transport_payload(
            raw=raw,
            normalized=normalized,
            projector=item_brief,
        )

    def _coercions(raw: dict[str, Any]) -> list[str]:
        return _build_coercion_notes(
            raw,
            list_fields=("items",),
            int_fields=("count",),
        )

    return StructuredToolSpec(
        normalize=_normalize,
        validate=_validate,
        fallback=_fallback,
        coercions=_coercions,
    )


def _build_status_pending_updated_spec(
    *,
    body_key: str,
    alias_keys: tuple[str, ...] = (),
    include_restart_required: bool = False,
) -> StructuredToolSpec:
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["status"] = str(payload.get("status") or "ok")
        normalized["pending"] = _dict_payload(normalized.get("pending"))
        normalized["updated"] = _list_payload(normalized.get("updated"))
        body = _dict_payload(normalized.get(body_key))
        for alias in alias_keys:
            if body:
                break
            body = _dict_payload(normalized.get(alias))
        normalized[body_key] = body
        if include_restart_required:
            normalized["restart_required"] = bool(normalized.get("restart_required", False))
        return normalized

    def _validate(payload: dict[str, Any]) -> list[str]:
        errors = _validate_type(payload.get("status"), str, label="status")
        errors.extend(_validate_type(payload.get("pending"), dict, label="pending"))
        errors.extend(_validate_type(payload.get("updated"), list, label="updated"))
        errors.extend(_validate_type(payload.get(body_key), dict, label=body_key))
        if include_restart_required:
            errors.extend(_validate_type(payload.get("restart_required"), bool, label="restart_required"))
        return errors

    def _fallback(raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        payload = _build_fallback_transport_payload(
            raw=raw,
            normalized=normalized,
            dict_fields=("pending", body_key),
            list_fields=("updated",),
        )
        if include_restart_required:
            payload["restart_required"] = bool(normalized.get("restart_required", False))
        return payload

    def _coercions(raw: dict[str, Any]) -> list[str]:
        return _build_coercion_notes(
            raw,
            dict_fields=("pending", body_key),
            list_fields=("updated",),
        )

    return StructuredToolSpec(
        normalize=_normalize,
        validate=_validate,
        fallback=_fallback,
        coercions=_coercions,
    )


def _build_status_list_flags_spec(
    *,
    list_fields: tuple[str, ...],
    bool_fields: tuple[str, ...] = (),
) -> StructuredToolSpec:
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["status"] = str(payload.get("status") or "ok")
        for field_name in list_fields:
            normalized[field_name] = _list_payload(normalized.get(field_name))
        for field_name in bool_fields:
            normalized[field_name] = bool(normalized.get(field_name, False))
        return normalized

    def _validate(payload: dict[str, Any]) -> list[str]:
        errors = _validate_type(payload.get("status"), str, label="status")
        for field_name in list_fields:
            errors.extend(_validate_type(payload.get(field_name), list, label=field_name))
        for field_name in bool_fields:
            errors.extend(_validate_type(payload.get(field_name), bool, label=field_name))
        return errors

    def _fallback(raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        return _build_fallback_transport_payload(
            raw=raw,
            normalized=normalized,
            list_fields=list_fields,
            bool_fields=bool_fields,
        )

    def _coercions(raw: dict[str, Any]) -> list[str]:
        return _build_coercion_notes(raw, list_fields=list_fields)

    return StructuredToolSpec(
        normalize=_normalize,
        validate=_validate,
        fallback=_fallback,
        coercions=_coercions,
    )


def _build_status_body_spec(
    *,
    body_key: str,
    alias_keys: tuple[str, ...] = (),
    bool_fields: tuple[str, ...] = (),
    bool_fields_from_body: tuple[str, ...] = (),
    string_fields: tuple[str, ...] = (),
) -> StructuredToolSpec:
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        body = _dict_payload(normalized.get(body_key))
        for alias in alias_keys:
            if body:
                break
            body = _dict_payload(normalized.get(alias))
        normalized["status"] = str(payload.get("status") or "ok")
        normalized[body_key] = body
        for field_name in bool_fields:
            if field_name in bool_fields_from_body:
                normalized[field_name] = bool(body.get(field_name, normalized.get(field_name, False)))
            else:
                normalized[field_name] = bool(normalized.get(field_name, False))
        for field_name in string_fields:
            normalized[field_name] = str(normalized.get(field_name) or "")
        return normalized

    def _validate(payload: dict[str, Any]) -> list[str]:
        errors = _validate_type(payload.get("status"), str, label="status")
        errors.extend(_validate_type(payload.get(body_key), dict, label=body_key))
        for field_name in bool_fields:
            errors.extend(_validate_type(payload.get(field_name), bool, label=field_name))
        return errors

    def _fallback(raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        return _build_fallback_transport_payload(
            raw=raw,
            normalized=normalized,
            dict_fields=(body_key,),
            bool_fields=bool_fields,
            string_fields=string_fields,
        )

    return StructuredToolSpec(
        normalize=_normalize,
        validate=_validate,
        fallback=_fallback,
    )


def _build_status_configs_spec(
    *,
    field_name: str,
    alias_keys: tuple[str, ...] = (),
) -> StructuredToolSpec:
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        items = _list_payload(normalized.get(field_name))
        if not items:
            for alias in alias_keys:
                items = _list_payload(normalized.get(alias))
                if items:
                    break
        normalized["status"] = str(payload.get("status") or "ok")
        normalized[field_name] = [
            _brief_agent_prompt_config(item)
            for item in items
            if isinstance(item, dict)
        ]
        return normalized

    def _validate(payload: dict[str, Any]) -> list[str]:
        errors = _validate_type(payload.get("status"), str, label="status")
        errors.extend(_validate_type(payload.get(field_name), list, label=field_name))
        return errors

    def _fallback(raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        return _build_fallback_transport_payload(
            raw=raw,
            normalized=normalized,
            list_fields=(field_name,),
            projected_list_fields={field_name: _brief_agent_prompt_config},
        )

    return StructuredToolSpec(
        normalize=_normalize,
        validate=_validate,
        fallback=_fallback,
    )


def _build_training_plan_create_spec() -> StructuredToolSpec:
    dict_fields = (
        "spec",
        "protocol",
        "dataset",
        "manager_scope",
        "optimization",
        "guardrails",
        "llm",
        "objective",
        "artifacts",
    )

    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["status"] = str(normalized.get("status") or "planned")
        normalized["plan_id"] = str(normalized.get("plan_id") or "")
        for field_name in dict_fields:
            normalized[field_name] = _dict_payload(normalized.get(field_name))
        return normalized

    def _validate(payload: dict[str, Any]) -> list[str]:
        errors = _validate_type(payload.get("status"), str, label="status")
        errors.extend(_validate_type(payload.get("plan_id"), str, label="plan_id"))
        for field_name in dict_fields:
            errors.extend(_validate_type(payload.get(field_name), dict, label=field_name))
        return errors

    def _fallback(raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        return _build_fallback_transport_payload(
            raw=raw,
            normalized=normalized,
            default_status="planned",
            dict_fields=dict_fields,
            string_fields=("plan_id",),
        )

    def _coercions(raw: dict[str, Any]) -> list[str]:
        return _build_coercion_notes(
            raw,
            dict_fields=dict_fields,
            string_fields=("status", "plan_id"),
        )

    return StructuredToolSpec(
        normalize=_normalize,
        validate=_validate,
        fallback=_fallback,
        coercions=_coercions,
    )


def _build_training_plan_execute_spec() -> StructuredToolSpec:
    dict_fields = ("training_lab", "artifacts", "summary")

    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["status"] = str(payload.get("status") or "ok")
        normalized["plan_id"] = str(payload.get("plan_id") or "")
        normalized["run_id"] = str(payload.get("run_id") or "")
        for field_name in dict_fields:
            normalized[field_name] = _dict_payload(normalized.get(field_name))
        results = [dict(item) for item in _list_payload(normalized.get("results")) if isinstance(item, dict)]
        latest_result = dict(results[-1]) if results else {}
        normalized["results"] = results
        normalized["result_overview"] = {
            "result_count": len(results),
            "ok_result_count": sum(1 for item in results if str(item.get("status") or "") == "ok"),
            "latest_cycle_id": latest_result.get("cycle_id"),
            "latest_result_status": str(latest_result.get("status") or ""),
        }
        normalized["latest_result"] = _brief_training_result(latest_result)
        return normalized

    def _validate(payload: dict[str, Any]) -> list[str]:
        errors = _validate_type(payload.get("status"), str, label="status")
        errors.extend(_validate_type(payload.get("plan_id"), str, label="plan_id"))
        errors.extend(_validate_type(payload.get("run_id"), str, label="run_id"))
        errors.extend(_validate_type(payload.get("results"), list, label="results"))
        for field_name in (*dict_fields, "result_overview", "latest_result"):
            errors.extend(_validate_type(payload.get(field_name), dict, label=field_name))
        return errors

    def _fallback(raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        return _build_fallback_transport_payload(
            raw=raw,
            normalized=normalized,
            dict_fields=(*dict_fields, "result_overview", "latest_result"),
            list_fields=("results",),
            string_fields=("plan_id", "run_id"),
        )

    def _coercions(raw: dict[str, Any]) -> list[str]:
        return _build_coercion_notes(
            raw,
            dict_fields=dict_fields,
            list_fields=("results",),
            string_fields=("status", "plan_id", "run_id"),
        )

    return StructuredToolSpec(
        normalize=_normalize,
        validate=_validate,
        fallback=_fallback,
        coercions=_coercions,
    )


def _build_training_lab_summary_spec() -> StructuredToolSpec:
    count_fields = ("plan_count", "run_count", "evaluation_count")
    list_fields = (
        ("latest_plans", _brief_training_plan_item),
        ("latest_runs", _brief_training_run_item),
        ("latest_evaluations", _brief_training_evaluation_item),
    )
    dict_fields = ("latest_run_summary", "latest_evaluation_summary", "governance_summary")

    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["status"] = str(payload.get("status") or "ok")
        for field_name in count_fields:
            normalized[field_name] = _safe_int(payload.get(field_name), 0)
        for field_name, projector in list_fields:
            normalized[field_name] = [
                projector(item)
                for item in _list_payload(normalized.get(field_name))
                if isinstance(item, dict)
            ]
        latest_runs = list(normalized["latest_runs"])
        latest_evaluations = list(normalized["latest_evaluations"])
        normalized["latest_run_summary"] = _dict_payload(normalized.get("latest_run_summary")) or {
            "latest_result": _dict_payload(_dict_payload(latest_runs[0]).get("latest_result")) if latest_runs else {},
            "ops_panel": {},
        }
        normalized["latest_evaluation_summary"] = _dict_payload(normalized.get("latest_evaluation_summary")) or (
            dict(latest_evaluations[0]) if latest_evaluations else {}
        )
        normalized["governance_summary"] = _dict_payload(normalized.get("governance_summary"))
        return normalized

    def _validate(payload: dict[str, Any]) -> list[str]:
        errors = _validate_type(payload.get("status"), str, label="status")
        for field_name in count_fields:
            errors.extend(_validate_type(payload.get(field_name), int, label=field_name))
        for field_name, _ in list_fields:
            errors.extend(_validate_type(payload.get(field_name), list, label=field_name))
        for field_name in dict_fields:
            errors.extend(_validate_type(payload.get(field_name), dict, label=field_name))
        return errors

    def _fallback(raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        payload = _build_fallback_transport_payload(
            raw=raw,
            normalized=normalized,
            dict_fields=dict_fields,
            list_fields=tuple(field_name for field_name, _ in list_fields),
            projected_list_fields={field_name: projector for field_name, projector in list_fields},
            int_fields=count_fields,
        )
        return payload

    def _coercions(raw: dict[str, Any]) -> list[str]:
        return _build_coercion_notes(
            raw,
            dict_fields=dict_fields,
            list_fields=tuple(field_name for field_name, _ in list_fields),
        )

    return StructuredToolSpec(
        normalize=_normalize,
        validate=_validate,
        fallback=_fallback,
        coercions=_coercions,
    )


_STRUCTURED_TOOL_SPECS: dict[str, StructuredToolSpec] = {
    "invest_training_plan_create": _build_training_plan_create_spec(),
    "invest_training_plan_execute": _build_training_plan_execute_spec(),
    "invest_control_plane_update": _build_status_pending_updated_spec(
        body_key="control_plane",
        alias_keys=("config",),
        include_restart_required=True,
    ),
    "invest_runtime_paths_update": _build_status_pending_updated_spec(
        body_key="paths",
        alias_keys=("config",),
    ),
    "invest_evolution_config_update": _build_status_pending_updated_spec(body_key="config"),
    "invest_training_plan_list": _build_status_count_items_spec(_brief_training_plan_item),
    "invest_training_runs_list": _build_status_count_items_spec(_brief_training_run_item),
    "invest_training_evaluations_list": _build_status_count_items_spec(_brief_training_evaluation_item),
    "invest_training_lab_summary": _build_training_lab_summary_spec(),
    "invest_agent_prompts_list": _build_status_configs_spec(
        field_name="configs",
        alias_keys=("items",),
    ),
    "invest_agent_prompts_update": _build_status_list_flags_spec(
        list_fields=("updated",),
        bool_fields=("restart_required",),
    ),
    "invest_control_plane_get": _build_status_body_spec(
        body_key="control_plane",
        alias_keys=("config",),
        bool_fields=("restart_required",),
        string_fields=("config_path", "local_override_path", "audit_log_path", "snapshot_dir"),
    ),
    "invest_runtime_paths_get": _build_status_body_spec(
        body_key="paths",
        alias_keys=("config",),
    ),
    "invest_evolution_config_get": _build_status_body_spec(
        body_key="config",
        bool_fields=("restart_required",),
    ),
}


def _merge_response_context(
    *,
    payload: dict[str, Any],
    entrypoint: dict[str, Any] | None = None,
    protocol: dict[str, Any] | None = None,
    task_bus: dict[str, Any] | None = None,
    artifacts: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    artifact_taxonomy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = dict(payload or {})
    for key, extra in (
        ("entrypoint", entrypoint),
        ("protocol", protocol),
        ("artifacts", artifacts),
        ("coverage", coverage),
        ("artifact_taxonomy", artifact_taxonomy),
    ):
        if not isinstance(extra, dict):
            continue
        merged = dict(body.get(key) or {})
        merged.update(dict(extra))
        body[key] = merged
    if isinstance(task_bus, dict):
        existing_task_bus = body.get("task_bus")
        if isinstance(existing_task_bus, dict):
            merged_task_bus = dict(existing_task_bus)
            merged_task_bus.update(dict(task_bus))
            body["task_bus"] = merged_task_bus
        else:
            body["task_bus"] = dict(task_bus)
    return body


def _resolve_response_defaults(
    *,
    body: dict[str, Any],
    default_message: str = "",
    default_reply: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    task_bus = dict(body.get("task_bus") or {})
    fallback_message = str(
        body.get("message")
        or body.get("reply")
        or default_message
        or default_reply
        or ""
    )
    feedback = dict(
        body.get("feedback")
        or build_gate_feedback(
            task_bus=task_bus,
            default_message=fallback_message,
        )
    )
    next_action = dict(
        body.get("next_action")
        or build_next_action(task_bus=task_bus, feedback=feedback)
    )
    return feedback, next_action


def build_protocol_response(
    *,
    payload: dict[str, Any],
    entrypoint: dict[str, Any] | None = None,
    protocol: dict[str, Any] | None = None,
    task_bus: dict[str, Any] | None = None,
    artifacts: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    artifact_taxonomy: dict[str, Any] | None = None,
    default_message: str = "",
    default_reply: str = "",
) -> dict[str, Any]:
    body = _merge_response_context(
        payload=dict(payload or {}),
        entrypoint=entrypoint,
        protocol=protocol,
        task_bus=task_bus,
        artifacts=artifacts,
        coverage=coverage,
        artifact_taxonomy=artifact_taxonomy,
    )
    feedback, next_action = _resolve_response_defaults(
        body=body,
        default_message=default_message,
        default_reply=default_reply,
    )
    body["feedback"] = feedback
    body["next_action"] = next_action
    return build_response_envelope(
        payload=body,
        default_reply=str(default_reply or default_message or feedback.get("summary") or ""),
    )


def build_response_envelope(*, payload: dict[str, Any], default_reply: str = "") -> dict[str, Any]:
    body = dict(payload or {})
    feedback, next_action = _resolve_response_defaults(
        body=body,
        default_reply=default_reply,
    )
    task_bus = dict(body.get("task_bus") or {})
    message = str(
        body.get("message")
        or feedback.get("message")
        or body.get("reply")
        or default_reply
        or feedback.get("summary")
        or ""
    )
    reply = str(body.get("reply") or message)
    body["feedback"] = feedback
    body["next_action"] = next_action
    body["message"] = message
    body["reply"] = reply
    if "status" not in body:
        body["status"] = str(dict(task_bus.get("audit") or {}).get("status") or "ok")
    return body


def build_bounded_entrypoint(
    *,
    kind: str,
    meeting_path: bool = False,
    agent_kind: str | None = None,
    agent_system: str | None = None,
    domain: str | None = None,
    runtime_method: str | None = None,
    runtime_tool: str | None = None,
    resolver: str | None = None,
    service: str | None = None,
    intent: str | None = None,
    operation: str | None = None,
    standalone_agent: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": str(kind),
        "meeting_path": bool(meeting_path),
    }
    optional_values = {
        "agent_kind": agent_kind,
        "agent_system": agent_system,
        "domain": domain,
        "runtime_method": runtime_method,
        "runtime_tool": runtime_tool,
        "resolver": resolver,
        "service": service,
        "intent": intent,
        "operation": operation,
    }
    for key, value in optional_values.items():
        if value is None or value == "":
            continue
        payload[key] = value
    if standalone_agent is not None:
        payload["standalone_agent"] = bool(standalone_agent)
    if extra:
        payload.update(dict(extra))
    return payload


def build_bounded_policy(
    *,
    source: str,
    agent_kind: str,
    fixed_boundary: bool = True,
    fixed_workflow: bool = True,
    writes_state: bool | None = None,
    tool_catalog_scope: str | None = None,
    domain: str | None = None,
    runtime_tool: str | None = None,
    workflow_mode: str | None = None,
    react_enabled: bool | None = None,
    confirmation_gate: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": str(source),
        "agent_kind": str(agent_kind),
        "fixed_boundary": bool(fixed_boundary),
        "fixed_workflow": bool(fixed_workflow),
    }
    optional_values = {
        "writes_state": writes_state,
        "tool_catalog_scope": tool_catalog_scope,
        "domain": domain,
        "runtime_tool": runtime_tool,
        "workflow_mode": workflow_mode,
        "react_enabled": react_enabled,
        "confirmation_gate": confirmation_gate,
    }
    for key, value in optional_values.items():
        if value is None or value == "":
            continue
        payload[key] = value
    if extra:
        payload.update(dict(extra))
    return payload


def build_bounded_orchestration(
    *,
    mode: str,
    available_tools: list[str] | None = None,
    allowed_tools: list[str] | None = None,
    workflow: list[str] | None = None,
    phase_stats: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "mode": str(mode),
        "available_tools": list(available_tools or []),
        "allowed_tools": list(allowed_tools or available_tools or []),
        "workflow": list(workflow or []),
        "phase_stats": dict(phase_stats or {}),
        "policy": dict(policy or {}),
    }
    if extra:
        payload.update(dict(extra))
    return payload


def build_bounded_response_context(
    *,
    schema_version: str,
    domain: str,
    operation: str,
    artifacts: dict[str, Any] | None = None,
    workflow: list[str] | None = None,
    phase_stats: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_artifacts = dict(artifacts or {})
    normalized_workflow = list(workflow or [])
    normalized_phase_stats = dict(phase_stats or {})
    return {
        "protocol": build_bounded_workflow_protocol(
            schema_version=schema_version,
            domain=domain,
            operation=operation,
        ),
        "artifacts": normalized_artifacts,
        "coverage": build_workflow_phase_coverage(
            workflow=normalized_workflow,
            phase_stats=normalized_phase_stats,
            existing=coverage,
        ),
        "artifact_taxonomy": build_artifact_taxonomy(normalized_artifacts),
    }


class StructuredOutputAdapter:
    """Produces stable payloads with validation, one repair pass, and fallback metadata."""

    def normalize_payload(
        self,
        *,
        tool_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        raw = _dict_payload(payload)
        normalized = self._normalize_tool_payload(tool_name=tool_name, raw=raw)
        errors = self._validation_errors(tool_name=tool_name, payload=_dict_payload(normalized))
        status = "validated"
        repair_attempted = False
        coercions = self._coercion_notes(tool_name=tool_name, raw=raw)

        if errors:
            repair_attempted = True
            repaired = self._repair_payload(tool_name=tool_name, raw=raw, normalized=_dict_payload(normalized))
            repair_errors = self._validation_errors(tool_name=tool_name, payload=repaired)
            if not repair_errors:
                normalized = repaired
                errors = []
                status = "repaired"
            else:
                normalized = self._fallback_payload(tool_name=tool_name, raw=raw, normalized=_dict_payload(repaired))
                errors = self._validation_errors(tool_name=tool_name, payload=_dict_payload(normalized))
                status = "fallback" if not errors else "fallback_degraded"
        elif coercions:
            status = "repaired"
            repair_attempted = True

        final_payload = _dict_payload(normalized)
        final_payload["structured_output"] = {
            "enabled": True,
            "tool_name": str(tool_name or ""),
            "status": status,
            "repair_attempted": repair_attempted,
            "coercions": list(coercions),
            "validation_errors": list(errors),
        }
        return final_payload

    def _normalize_tool_payload(self, *, tool_name: str, raw: dict[str, Any]) -> dict[str, Any]:
        spec = _STRUCTURED_TOOL_SPECS.get(tool_name)
        if spec is not None:
            return spec.normalize(raw)
        normalizer = getattr(self, f"_normalize_{tool_name}", None)
        if callable(normalizer):
            normalized = normalizer(raw)
            return _dict_payload(normalized)
        return dict(raw)

    def _validation_errors(self, *, tool_name: str, payload: dict[str, Any]) -> list[str]:
        spec = _STRUCTURED_TOOL_SPECS.get(tool_name)
        if spec is not None:
            return spec.validate(payload)
        validator = getattr(self, f"_validate_{tool_name}", None)
        return _validation_errors(validator, payload)

    def _coercion_notes(self, *, tool_name: str, raw: dict[str, Any]) -> list[str]:
        spec = _STRUCTURED_TOOL_SPECS.get(tool_name)
        if spec is not None and callable(spec.coercions):
            return [str(item) for item in _list_payload(spec.coercions(raw)) if str(item or "")]
        inspector = getattr(self, f"_coercions_{tool_name}", None)
        if callable(inspector):
            return [str(item) for item in _list_payload(inspector(raw)) if str(item or "")]
        return []

    def _repair_payload(
        self,
        *,
        tool_name: str,
        raw: dict[str, Any],
        normalized: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = self._fallback_payload(tool_name=tool_name, raw=raw, normalized=normalized)
        merged = dict(fallback)
        merged.update(normalized)
        for key, value in fallback.items():
            if key not in merged or (
                isinstance(value, dict) and not isinstance(merged.get(key), dict)
            ):
                merged[key] = value
        return merged

    def _fallback_payload(
        self,
        *,
        tool_name: str,
        raw: dict[str, Any],
        normalized: dict[str, Any],
    ) -> dict[str, Any]:
        spec = _STRUCTURED_TOOL_SPECS.get(tool_name)
        if spec is not None:
            return _dict_payload(spec.fallback(raw, normalized))
        builder = getattr(self, f"_fallback_{tool_name}", None)
        if callable(builder):
            return _dict_payload(builder(raw=raw, normalized=normalized))
        return _dict_payload(normalized or raw)

    def _normalize_invest_ask_stock(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        identifiers = {
            "policy_id": str(normalized.get("policy_id") or ""),
            "research_case_id": str(normalized.get("research_case_id") or ""),
            "attribution_id": str(normalized.get("attribution_id") or ""),
        }
        security = _dict_payload(normalized.get("resolved_entities")).get("security")
        if not isinstance(security, dict):
            security = _dict_payload(normalized.get("resolved_security")) or _dict_payload(normalized.get("resolved"))
        normalized["request"] = {
            "question": str(normalized.get("question") or _dict_payload(normalized.get("request")).get("question") or ""),
            "query": str(normalized.get("query") or _dict_payload(normalized.get("request")).get("query") or ""),
            "normalized_query": str(normalized.get("normalized_query") or _dict_payload(normalized.get("request")).get("normalized_query") or ""),
            "requested_as_of_date": str(normalized.get("requested_as_of_date") or _dict_payload(normalized.get("request")).get("requested_as_of_date") or ""),
            "as_of_date": str(normalized.get("as_of_date") or _dict_payload(normalized.get("request")).get("as_of_date") or ""),
        }
        normalized["identifiers"] = {
            **{key: str(_dict_payload(normalized.get("identifiers")).get(key) or "") for key in identifiers.keys()},
            **{key: value for key, value in identifiers.items() if value},
        }
        normalized["resolved_entities"] = {"security": _dict_payload(security)}
        normalized["analysis"] = {
            "tool_results": _dict_payload(_dict_payload(normalized.get("analysis")).get("tool_results")),
            "result_sequence": _list_payload(_dict_payload(normalized.get("analysis")).get("result_sequence")),
            "derived_signals": _dict_payload(_dict_payload(normalized.get("analysis")).get("derived_signals")),
            "research_bridge": _dict_payload(_dict_payload(normalized.get("analysis")).get("research_bridge")),
        }
        normalized["research"] = {
            **_dict_payload(normalized.get("research")),
            "identifiers": _dict_payload(_dict_payload(normalized.get("research")).get("identifiers"))
            or dict(normalized["identifiers"]),
        }
        normalized["dashboard"] = _dict_payload(normalized.get("dashboard"))
        return normalized

    def _validate_invest_ask_stock(self, payload: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for key in ("request", "identifiers", "resolved_entities", "analysis", "research", "dashboard"):
            errors.extend(_validate_type(payload.get(key), dict, label=key))
        errors.extend(_validate_type(_dict_payload(payload.get("resolved_entities")).get("security"), dict, label="resolved_entities.security"))
        return errors

    def _fallback_invest_ask_stock(self, *, raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        return {
            "request": _dict_payload(normalized.get("request")),
            "identifiers": _dict_payload(normalized.get("identifiers")),
            "resolved_entities": {"security": _dict_payload(_dict_payload(normalized.get("resolved_entities")).get("security"))},
            "analysis": _dict_payload(normalized.get("analysis")),
            "research": _dict_payload(normalized.get("research")),
            "dashboard": _dict_payload(normalized.get("dashboard")),
            "status": str(raw.get("status") or normalized.get("status") or "ok"),
        }

__all__ = ["StructuredOutputAdapter"]



_DEFAULT_POLICY_KEYS = (
    "fixed_boundary",
    "fixed_workflow",
    "writes_state",
    "confirmation_gate",
    "tool_catalog_scope",
    "workflow_mode",
)


_DEFAULT_TOP_LEVEL_KEYS = ("status", "detail_mode", "intent", "pending")


def _nested_get(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def build_entrypoint_snapshot(payload: dict[str, Any], *, include_service: bool = True) -> dict[str, Any]:
    snapshot = {
        "agent_kind": _nested_get(payload, "entrypoint", "agent_kind"),
        "domain": _nested_get(payload, "entrypoint", "domain"),
        "runtime_tool": _nested_get(payload, "entrypoint", "runtime_tool"),
    }
    if include_service:
        snapshot["service"] = _nested_get(payload, "entrypoint", "service")
    return snapshot


def build_orchestration_snapshot(
    payload: dict[str, Any],
    *,
    policy_keys: tuple[str, ...] = _DEFAULT_POLICY_KEYS,
    include_step_count: bool = True,
    include_phase_stats: bool = True,
) -> dict[str, Any]:
    snapshot = {
        "workflow": _nested_get(payload, "orchestration", "workflow"),
        "mode": _nested_get(payload, "orchestration", "mode"),
        "policy": {key: _nested_get(payload, "orchestration", "policy", key) for key in policy_keys},
    }
    if include_step_count:
        snapshot["step_count"] = _nested_get(payload, "orchestration", "step_count")
    if include_phase_stats:
        snapshot["phase_stats"] = _nested_get(payload, "orchestration", "phase_stats")
    return snapshot


def build_task_bus_snapshot(
    payload: dict[str, Any],
    *,
    include_recommended_args: bool = False,
    include_coverage: bool = False,
    include_gate_decision: bool = False,
    include_tool_count: bool = False,
) -> dict[str, Any]:
    task_bus = dict(payload.get("task_bus") or {})
    plan = list(_nested_get(payload, "task_bus", "planner", "recommended_plan") or [])
    snapshot = {
        "schema_version": task_bus.get("schema_version"),
        "intent": _nested_get(payload, "task_bus", "planner", "intent"),
        "operation": _nested_get(payload, "task_bus", "planner", "operation"),
        "mode": _nested_get(payload, "task_bus", "planner", "mode"),
        "recommended_tools": _nested_get(payload, "task_bus", "planner", "plan_summary", "recommended_tools"),
        "used_tools": _nested_get(payload, "task_bus", "audit", "used_tools"),
        "requires_confirmation": _nested_get(payload, "task_bus", "gate", "requires_confirmation"),
        "confirmation_state": _nested_get(payload, "task_bus", "gate", "confirmation", "state"),
    }
    if include_recommended_args:
        snapshot["recommended_args"] = [dict(step).get("args") for step in plan]
    if include_tool_count:
        snapshot["tool_count"] = _nested_get(payload, "task_bus", "audit", "tool_count")
    if include_coverage:
        for key in (
            "planned_step_coverage",
            "parameterized_step_count",
            "covered_parameterized_step_ids",
            "missing_parameterized_step_ids",
            "parameter_coverage",
        ):
            snapshot[key] = _nested_get(payload, "task_bus", "audit", "coverage", key)
    if include_gate_decision:
        for key in ("decision", "risk_level", "writes_state"):
            snapshot[key] = _nested_get(payload, "task_bus", "gate", key)
    return snapshot


def build_feedback_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    return {"summary": _nested_get(payload, "feedback", "summary")}


def build_next_action_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": _nested_get(payload, "next_action", "kind"),
        "requires_confirmation": _nested_get(payload, "next_action", "requires_confirmation"),
    }


def build_transcript_snapshot(
    payload: dict[str, Any],
    *,
    top_level_keys: tuple[str, ...] = _DEFAULT_TOP_LEVEL_KEYS,
    include_strategy: bool = False,
    include_resolved: bool = False,
    include_feedback: bool = True,
    include_next_action: bool = True,
    include_protocol: bool = True,
    include_recommended_args: bool = False,
    include_task_bus_coverage: bool = False,
    include_gate_decision: bool = False,
    include_tool_count: bool = False,
    include_orchestration_step_count: bool = True,
    include_orchestration_phase_stats: bool = True,
    include_entrypoint_service: bool = True,
    orchestration_policy_keys: tuple[str, ...] = _DEFAULT_POLICY_KEYS,
) -> dict[str, Any]:
    snapshot = {
        "entrypoint": build_entrypoint_snapshot(payload, include_service=include_entrypoint_service),
        "orchestration": build_orchestration_snapshot(
            payload,
            policy_keys=orchestration_policy_keys,
            include_step_count=include_orchestration_step_count,
            include_phase_stats=include_orchestration_phase_stats,
        ),
        "task_bus": build_task_bus_snapshot(
            payload,
            include_recommended_args=include_recommended_args,
            include_coverage=include_task_bus_coverage,
            include_gate_decision=include_gate_decision,
            include_tool_count=include_tool_count,
        ),
    }
    if include_protocol:
        protocol = payload.get("protocol")
        if protocol is not None:
            snapshot["protocol"] = protocol
    if include_feedback:
        feedback = build_feedback_snapshot(payload)
        if feedback is not None:
            snapshot["feedback"] = feedback
    if include_next_action:
        next_action = build_next_action_snapshot(payload)
        if next_action is not None:
            snapshot["next_action"] = next_action
    for key in top_level_keys:
        value = payload.get(key)
        if value is not None:
            snapshot[key] = value
    if include_strategy and "strategy" in payload:
        snapshot["strategy"] = {
            "name": _nested_get(payload, "strategy", "name"),
            "required_tools": _nested_get(payload, "strategy", "required_tools"),
            "analysis_steps": _nested_get(payload, "strategy", "analysis_steps"),
        }
    if include_resolved and "resolved" in payload:
        snapshot["resolved"] = {
            "code": _nested_get(payload, "resolved", "code"),
            "name": _nested_get(payload, "resolved", "name"),
        }
    return snapshot


def build_contract_transcript_snapshots() -> dict[str, Any]:
    runtime_status_payload = {
        "status": "ok",
        "detail_mode": "fast",
        "entrypoint": {
            "agent_kind": "bounded_runtime_agent",
            "domain": "runtime",
            "runtime_tool": "invest_quick_status",
            "service": None,
        },
        "orchestration": {
            "workflow": ["runtime_scope_resolve", "status_read", "finalize"],
            "mode": "bounded_readonly_workflow",
            "step_count": None,
            "phase_stats": {"detail_mode": "fast", "event_count": 12},
            "policy": {
                "fixed_boundary": True,
                "fixed_workflow": True,
                "writes_state": False,
                "confirmation_gate": None,
                "tool_catalog_scope": "runtime_domain",
                "workflow_mode": None,
            },
        },
        "task_bus": {
            "schema_version": "task_bus.v2",
            "planner": {
                "intent": "runtime_status",
                "operation": "status",
                "mode": "commander_runtime_method",
                "recommended_plan": [],
                "plan_summary": {"recommended_tools": ["invest_quick_status", "invest_events_summary", "invest_runtime_diagnostics"]},
            },
            "gate": {"requires_confirmation": False, "confirmation": {"state": "not_applicable"}},
            "audit": {"used_tools": ["invest_quick_status"]},
        },
        "protocol": {
            "schema_version": "bounded_workflow.v2",
            "task_bus_schema_version": "task_bus.v2",
            "plan_schema_version": "task_plan.v2",
            "coverage_schema_version": "task_coverage.v2",
            "artifact_taxonomy_schema_version": "artifact_taxonomy.v2",
            "domain": "runtime",
            "operation": "status",
        },
        "feedback": {"summary": "当前任务已完成，计划与参数覆盖满足预期。"},
        "next_action": {"kind": "continue", "requires_confirmation": False},
    }
    ask_stock_payload = {
        "status": "ok",
        "entrypoint": {
            "agent_kind": "bounded_stock_agent",
            "domain": "stock",
            "runtime_tool": "invest_ask_stock",
            "service": "StockAnalysisService",
        },
        "orchestration": {
            "workflow": ["yaml_strategy_loaded", "yaml_plan_execute", "finalize"],
            "mode": "yaml_react_like",
            "step_count": 5,
            "phase_stats": {"llm_react_steps": 0, "yaml_planned_steps": 5, "total_steps": 5},
            "policy": {
                "fixed_boundary": True,
                "fixed_workflow": True,
                "writes_state": None,
                "confirmation_gate": None,
                "tool_catalog_scope": "strategy_restricted",
                "workflow_mode": "llm_react_with_yaml_gap_fill",
            },
        },
        "task_bus": {
            "schema_version": "task_bus.v2",
            "planner": {
                "intent": "stock_analysis",
                "operation": "ask_stock",
                "mode": "yaml_react_like",
                "recommended_plan": [],
                "plan_summary": {"recommended_tools": ["get_daily_history", "get_indicator_snapshot", "analyze_support_resistance", "get_capital_flow", "get_realtime_quote"]},
            },
            "gate": {"requires_confirmation": False, "confirmation": {"state": "not_applicable"}},
            "audit": {"used_tools": ["get_daily_history", "get_indicator_snapshot", "analyze_support_resistance", "get_capital_flow", "get_realtime_quote"]},
        },
        "protocol": {
            "schema_version": "bounded_workflow.v2",
            "task_bus_schema_version": "task_bus.v2",
            "plan_schema_version": "task_plan.v2",
            "coverage_schema_version": "task_coverage.v2",
            "artifact_taxonomy_schema_version": "artifact_taxonomy.v2",
            "domain": "stock",
            "operation": "ask_stock",
        },
        "feedback": {"summary": "当前任务已完成，计划与参数覆盖满足预期。"},
        "next_action": {"kind": "continue", "requires_confirmation": False},
        "strategy": {
            "name": "chan_theory",
            "required_tools": ["get_daily_history", "get_indicator_snapshot", "analyze_support_resistance", "get_capital_flow", "get_realtime_quote"],
            "analysis_steps": ["获取近60日日线", "识别指标状态", "判断支撑阻力", "观察资金确认", "结合最新价格输出结论"],
        },
        "resolved": {"code": "sh.600001", "name": "FooBank"},
    }
    mutating_payload = {
        "status": "confirmation_required",
        "pending": {"patch": {"training_output_dir": "/tmp/train"}},
        "entrypoint": {
            "agent_kind": "bounded_config_agent",
            "domain": "config",
            "runtime_tool": "invest_runtime_paths_update",
            "service": None,
        },
        "orchestration": {
            "workflow": ["config_scope_resolve", "gate_confirmation", "finalize"],
            "mode": "bounded_mutating_workflow",
            "phase_stats": {"pending_key_count": 1, "requires_confirmation": True},
            "policy": {
                "fixed_boundary": True,
                "fixed_workflow": True,
                "writes_state": True,
                "confirmation_gate": True,
                "tool_catalog_scope": "config_domain",
                "workflow_mode": None,
            },
        },
        "task_bus": {
            "schema_version": "task_bus.v2",
            "planner": {
                "intent": "config_runtime_paths_update",
                "operation": "update_runtime_paths",
                "mode": "commander_runtime_method",
                "recommended_plan": [{"args": {}}, {"args": {"confirm": False}}],
                "plan_summary": {"recommended_tools": ["invest_runtime_paths_get", "invest_runtime_paths_update"]},
            },
            "gate": {
                "requires_confirmation": True,
                "decision": "confirm",
                "risk_level": "high",
                "writes_state": True,
                "confirmation": {"state": "pending_confirmation"},
            },
            "audit": {
                "used_tools": [],
                "tool_count": 0,
                "coverage": {
                    "planned_step_coverage": 0.0,
                    "parameterized_step_count": 1,
                    "covered_parameterized_step_ids": [],
                    "missing_parameterized_step_ids": ["step_02"],
                    "parameter_coverage": 0.0,
                },
            },
        },
        "protocol": {
            "schema_version": "bounded_workflow.v2",
            "task_bus_schema_version": "task_bus.v2",
            "plan_schema_version": "task_plan.v2",
            "coverage_schema_version": "task_coverage.v2",
            "artifact_taxonomy_schema_version": "artifact_taxonomy.v2",
            "domain": "config",
            "operation": "update_runtime_paths",
        },
        "feedback": {"summary": "当前任务仍需人工确认后才能视为审计闭环完成。"},
        "next_action": {"kind": "confirm", "requires_confirmation": True},
    }
    builtin_runtime_payload = {
        "status": "ok",
        "entrypoint": {
            "agent_kind": "bounded_runtime_agent",
            "domain": None,
            "runtime_tool": None,
            "service": None,
        },
        "orchestration": {
            "workflow": ["runtime_scope_resolve", "quick_status_read", "training_lab_read", "finalize"],
            "mode": "builtin_bounded_readonly_workflow",
            "step_count": None,
            "phase_stats": {"section_count": 2},
            "policy": {
                "fixed_boundary": True,
                "fixed_workflow": True,
                "writes_state": False,
                "confirmation_gate": None,
                "tool_catalog_scope": "runtime_training_combo",
                "workflow_mode": None,
            },
        },
        "task_bus": {
            "schema_version": "task_bus.v2",
            "planner": {
                "intent": "runtime_status_and_training",
                "operation": "status_and_recent_training",
                "mode": "builtin_intent",
                "recommended_plan": [],
                "plan_summary": {"recommended_tools": ["invest_quick_status", "invest_training_lab_summary"]},
            },
            "gate": {"requires_confirmation": False, "confirmation": {"state": "not_applicable"}},
            "audit": {"used_tools": ["invest_quick_status", "invest_training_lab_summary"]},
        },
        "protocol": None,
        "feedback": {"summary": "当前任务已完成，计划与参数覆盖满足预期。"},
        "next_action": {"kind": "continue", "requires_confirmation": False},
    }
    return {
        "schema_version": "transcript_snapshots.v1",
        "examples": {
            "runtime_status": build_transcript_snapshot(runtime_status_payload),
            "ask_stock": build_transcript_snapshot(ask_stock_payload, include_strategy=True, include_resolved=True),
            "mutating_confirmation": build_transcript_snapshot(
                mutating_payload,
                top_level_keys=("status", "pending"),
                include_recommended_args=True,
                include_task_bus_coverage=True,
                include_gate_decision=True,
                include_tool_count=True,
                include_orchestration_step_count=False,
                include_entrypoint_service=False,
                orchestration_policy_keys=("writes_state", "confirmation_gate", "fixed_boundary", "fixed_workflow"),
            ),
            "runtime_builtin_combo": build_transcript_snapshot(builtin_runtime_payload),
        },
    }


class BrainHumanReadablePresenter:
    """Builds human-facing summaries while keeping runtime orchestration thin."""

    @staticmethod
    def truncate_text(value: Any, *, limit: int = 120) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    @staticmethod
    def runtime_state_bullets(runtime_payload: dict[str, Any]) -> list[str]:
        state = str(runtime_payload.get("state") or "unknown")
        current_task = dict(runtime_payload.get("current_task") or {})
        last_task = dict(runtime_payload.get("last_task") or {})
        bullets = [f"运行状态：{state}"]
        if current_task.get("type"):
            bullets.append(f"当前任务：{current_task.get('type')}")
        if last_task.get("type"):
            bullets.append(
                f"最近完成：{last_task.get('type')} / {last_task.get('status', '')}".rstrip(" /")
            )
        return bullets

    @staticmethod
    def training_lab_bullets(training_lab: dict[str, Any]) -> list[str]:
        if not training_lab:
            return []
        bullets = [
            f"训练计划：{int(training_lab.get('plan_count', 0) or 0)}",
            f"训练运行：{int(training_lab.get('run_count', 0) or 0)}",
            f"训练评估：{int(training_lab.get('evaluation_count', 0) or 0)}",
        ]
        governance_summary = dict(training_lab.get("governance_summary") or {})
        governance_metrics = dict(governance_summary.get("governance_metrics") or {})
        if governance_metrics:
            bullets.append(
                f"候选待发布：{int(governance_metrics.get('candidate_pending_count', 0) or 0)}"
            )
            bullets.append(
                f"配置漂移率：{float(governance_metrics.get('active_candidate_drift_rate', 0.0) or 0.0):.2%}"
            )
        return bullets

    @staticmethod
    def runtime_governance_bullets(payload: dict[str, Any]) -> list[str]:
        governance_display = project_runtime_governance_display_payload(payload)
        if not governance_display.get("available", False):
            return []
        return [
            f"guardrail 阻断：{int(governance_display.get('guardrail_blocks', 0) or 0)}",
            f"结构化 fallback：{int(governance_display.get('fallback_count', 0) or 0)}",
        ]

    @staticmethod
    def latest_training_result_summary(payload: dict[str, Any]) -> dict[str, Any]:
        return read_latest_training_result(payload)

    @staticmethod
    def is_internal_runtime_event(event_name: Any) -> bool:
        return str(event_name or "") in {
            "ask_started",
            "ask_finished",
            "task_started",
            "task_finished",
        }

    @staticmethod
    def top_event_distribution(counts: dict[str, Any], *, limit: int = 3) -> str:
        ordered = sorted(
            ((str(name), int(value or 0)) for name, value in dict(counts or {}).items()),
            key=lambda item: (-item[1], item[0]),
        )
        return "，".join(f"{name}×{count}" for name, count in ordered[:limit])

    @staticmethod
    def event_human_label(event_name: str) -> str:
        mapping = {
            "ask_started": "对话请求开始",
            "ask_finished": "对话请求完成",
            "task_started": "运行任务开始",
            "task_finished": "运行任务完成",
            "training_started": "训练开始",
            "training_finished": "训练完成",
            "governance_started": "组合治理开始",
            "manager_activation_decided": "组合治理决策完成",
            "governance_applied": "组合治理已应用",
            "governance_blocked": "组合治理被阻断",
            "regime_classified": "市场状态识别完成",
            "cycle_start": "训练周期开始",
            "cycle_complete": "训练周期完成",
            "cycle_skipped": "训练周期被跳过",
            "agent_status": "Agent 状态更新",
            "agent_progress": "Agent 进度更新",
            "module_log": "模块日志更新",
            "meeting_speech": "会议发言更新",
            "data_download_triggered": "数据下载已触发",
            "runtime_paths_updated": "运行路径已更新",
            "evolution_config_updated": "训练配置已更新",
            "control_plane_updated": "控制面已更新",
            "agent_prompt_updated": "Agent Prompt 已更新",
        }
        return mapping.get(str(event_name or ""), str(event_name or "").replace("_", " "))

    @classmethod
    def event_detail_text(cls, row: dict[str, Any]) -> str:
        payload = dict(row.get("payload") or {})
        event_name = str(row.get("event") or "")
        if event_name == "ask_started":
            channel = str(payload.get("channel") or "").strip()
            message_length = payload.get("message_length")
            details = []
            if channel:
                details.append(f"来源 {channel}")
            if message_length not in (None, ""):
                details.append(f"消息长度 {message_length}")
            if details:
                return "已接收对话请求，" + "，".join(details) + "。"
            return "已接收新的对话请求。"
        if event_name == "ask_finished":
            intent = str(payload.get("intent") or "").strip()
            status = str(payload.get("status") or "").strip()
            risk_level = str(payload.get("risk_level") or "").strip()
            details = []
            if intent:
                details.append(f"意图 {intent}")
            if status:
                details.append(f"状态 {status}")
            if risk_level:
                details.append(f"风险 {risk_level}")
            if details:
                return "对话处理结束，" + "，".join(details) + "。"
            return "对话处理结束。"
        if event_name == "task_started":
            task_type = str(payload.get("type") or "").strip()
            source = str(payload.get("source") or "").strip()
            if task_type and source:
                return f"开始执行 {task_type} 任务，来源 {source}。"
            if task_type:
                return f"开始执行 {task_type} 任务。"
        if event_name == "task_finished":
            task_type = str(payload.get("type") or "").strip()
            status = str(payload.get("status") or "").strip()
            if task_type and status:
                return f"{task_type} 任务已结束，状态 {status}。"
            if status:
                return f"运行任务已结束，状态 {status}。"
        if event_name == "manager_activation_decided":
            regime = str(payload.get("regime") or "").strip()
            dominant_manager_id = str(payload.get("dominant_manager_id") or "").strip()
            active_manager_ids = [
                str(item).strip()
                for item in list(payload.get("active_manager_ids") or [])
                if str(item).strip()
            ]
            if not active_manager_ids and dominant_manager_id:
                active_manager_ids = [dominant_manager_id]
            manager_budget_weights = {
                str(key): float(value)
                for key, value in dict(payload.get("manager_budget_weights") or {}).items()
                if str(key).strip()
            }
            details = []
            if regime:
                details.append(f"识别为 {regime} 市场")
            if active_manager_ids:
                details.append(f"激活经理 {', '.join(active_manager_ids)}")
            if dominant_manager_id:
                details.append(f"主导经理 {dominant_manager_id}")
            if manager_budget_weights:
                details.append(
                    "预算分配 "
                    + " / ".join(f"{key}:{value:.2f}" for key, value in manager_budget_weights.items())
                )
            if details:
                return "，".join(details) + "。"
        if event_name == "governance_applied":
            dominant_manager_id = str(
                payload.get("dominant_manager_id")
                or ""
            ).strip()
            active_manager_ids = [
                str(item).strip()
                for item in list(payload.get("active_manager_ids") or [])
                if str(item).strip()
            ]
            if not active_manager_ids and dominant_manager_id:
                active_manager_ids = [dominant_manager_id]
            if active_manager_ids and dominant_manager_id:
                return f"治理已应用，激活经理 {', '.join(active_manager_ids)}，主导经理 {dominant_manager_id}。"
            if active_manager_ids:
                return f"治理已应用，激活经理 {', '.join(active_manager_ids)}。"
        if event_name == "governance_blocked":
            hold_reason = str(payload.get("hold_reason") or "").strip()
            if hold_reason:
                return f"治理调整被阻断，原因是：{hold_reason}"
            return "治理调整被 guardrail 阻断。"
        if event_name == "cycle_start":
            cutoff_date = str(payload.get("cutoff_date") or "").strip()
            requested_mode = str(payload.get("requested_data_mode") or "").strip()
            llm_mode = str(payload.get("llm_mode") or "").strip()
            details = []
            if cutoff_date:
                details.append(f"截断日期 {cutoff_date}")
            if requested_mode:
                details.append(f"数据模式 {requested_mode}")
            if llm_mode:
                details.append(f"LLM 模式 {llm_mode}")
            if details:
                return "本轮训练已启动，" + "，".join(details) + "。"
        if event_name == "cycle_complete":
            cycle_id = payload.get("cycle_id")
            return_pct = payload.get("return_pct")
            if cycle_id is not None and return_pct not in (None, ""):
                return f"训练周期 #{cycle_id} 已完成，收益率约为 {return_pct}。"
            if cycle_id is not None:
                return f"训练周期 #{cycle_id} 已完成。"
        if event_name == "cycle_skipped":
            stage = str(payload.get("stage") or "").strip()
            reason = str(payload.get("reason") or "").strip()
            if stage and reason:
                return f"训练周期在 {stage} 阶段被跳过，原因是：{reason}"
            if reason:
                return f"训练周期被跳过，原因是：{reason}"
        if event_name == "agent_status":
            agent = str(payload.get("agent") or "").strip()
            status = str(payload.get("status") or "").strip()
            stage = str(payload.get("stage") or "").strip()
            progress_pct = payload.get("progress_pct")
            message = cls.truncate_text(payload.get("message"), limit=80)
            parts = []
            if agent:
                parts.append(agent)
            if status:
                parts.append(status)
            if stage:
                parts.append(f"阶段 {stage}")
            if progress_pct not in (None, ""):
                parts.append(f"进度 {progress_pct}%")
            if message:
                parts.append(message)
            if parts:
                return "，".join(parts) + "。"
        if event_name == "module_log":
            module = str(payload.get("module") or "").strip()
            title = str(payload.get("title") or "").strip()
            message = cls.truncate_text(payload.get("message"), limit=80)
            parts = [part for part in [module, title, message] if part]
            if parts:
                return " / ".join(parts) + "。"
        if event_name == "meeting_speech":
            speaker = str(payload.get("speaker") or "").strip()
            meeting = str(payload.get("meeting") or "").strip()
            speech = cls.truncate_text(payload.get("speech"), limit=80)
            prefix = " / ".join(part for part in [meeting, speaker] if part)
            if prefix and speech:
                return f"{prefix}：{speech}"
        if event_name == "data_download_triggered":
            status = str(payload.get("status") or "").strip()
            message = cls.truncate_text(payload.get("message"), limit=80)
            if status and message:
                return f"数据同步状态：{status}，{message}"
        if event_name in {
            "runtime_paths_updated",
            "evolution_config_updated",
            "control_plane_updated",
        }:
            updated = payload.get("updated")
            if isinstance(updated, list) and updated:
                return "更新字段：" + "，".join(str(item) for item in updated[:4])
        return ""

    @classmethod
    def event_broadcast_text(cls, row: dict[str, Any]) -> str:
        event_name = str(row.get("event") or "").strip()
        if not event_name:
            return ""
        label = cls.event_human_label(event_name)
        detail = cls.event_detail_text(row)
        source = str(row.get("source") or "").strip()
        if detail:
            return f"{label}：{detail}"
        if source:
            return f"{label}（来源 {source}）"
        return label

    @classmethod
    def event_explanation_bullets(
        cls,
        event_summary: dict[str, Any],
        *,
        recent_events: list[dict[str, Any]] | None = None,
    ) -> tuple[list[str], dict[str, Any], str]:
        summary = dict(event_summary or {})
        rows = list(recent_events or [])
        preferred_latest: dict[str, Any] = {}
        latest_internal: dict[str, Any] = {}
        for row in reversed(rows):
            event_name = str(row.get("event") or "")
            if not event_name:
                continue
            if not cls.is_internal_runtime_event(event_name):
                preferred_latest = dict(row)
                break
            if not latest_internal:
                latest_internal = dict(row)
        latest = dict(preferred_latest or latest_internal or summary.get("latest") or {})
        counts = dict(summary.get("counts") or {})
        external_counts = {
            str(name): int(value or 0)
            for name, value in counts.items()
            if not cls.is_internal_runtime_event(name)
        }
        bullets: list[str] = []
        latest_event: dict[str, Any] = {}
        explanation = ""
        if latest:
            event_name = str(latest.get("event") or "unknown")
            source = str(latest.get("source") or "").strip()
            detail_text = cls.event_detail_text(latest)
            latest_event = {
                "event": event_name,
                "source": source,
                "ts": str(latest.get("ts") or ""),
                "kind": "internal" if cls.is_internal_runtime_event(event_name) else "business",
                "label": cls.event_human_label(event_name),
                "detail": detail_text,
                "broadcast_text": cls.event_broadcast_text(latest),
            }
            if not cls.is_internal_runtime_event(event_name):
                detail = f"最近业务事件：{event_name}（{cls.event_human_label(event_name)}）"
                if source:
                    detail += f"（来源 {source}）"
                bullets.append(detail)
                if detail_text:
                    bullets.append("事件细节：" + detail_text)
        if external_counts:
            distribution = cls.top_event_distribution(external_counts)
            bullets.append("业务事件分布：" + distribution)
            if preferred_latest:
                explanation = (
                    f"最近一次业务事件是 {latest_event['event']}"
                    + (
                        f"（{latest_event.get('label')}）"
                        if latest_event.get("label")
                        else ""
                    )
                    + (
                        f"（来源 {latest_event['source']}）"
                        if latest_event.get("source")
                        else ""
                    )
                    + "。"
                )
                if latest_event.get("detail"):
                    explanation += f" {latest_event['detail']}"
                if distribution:
                    explanation += f" 当前窗口内主要业务事件分布为：{distribution}。"
        elif counts:
            distribution = cls.top_event_distribution(counts)
            bullets.append("交互事件分布：" + distribution)
            explanation = "当前窗口内主要记录的是交互与调度事件，尚未出现新的业务事件。"
            if distribution:
                explanation += f" 最近的事件分布为：{distribution}。"
        return bullets, latest_event, explanation

    @classmethod
    def event_timeline_items(
        cls,
        recent_events: list[dict[str, Any]] | None,
        *,
        limit: int = 3,
    ) -> list[str]:
        rows = list(recent_events or [])
        business_items: list[str] = []
        internal_items: list[str] = []
        for row in reversed(rows):
            event_name = str(row.get("event") or "").strip()
            if not event_name:
                continue
            broadcast_text = cls.event_broadcast_text(row)
            if not broadcast_text:
                continue
            target = (
                internal_items if cls.is_internal_runtime_event(event_name) else business_items
            )
            if broadcast_text not in target:
                target.append(broadcast_text)
        selected = business_items or internal_items
        return selected[: max(1, int(limit or 3))]

    @staticmethod
    def risk_explanations(
        diagnostics: list[Any],
        *,
        feedback: dict[str, Any],
        last_error: Any = "",
    ) -> list[str]:
        mapping = {
            "runtime_state=error": "运行态处于 error，建议优先检查最近失败任务和错误日志。",
            "data_quality_unhealthy": "数据健康异常，继续训练或问股前应先检查数据状态。",
            "last_run_degraded": "最近一次运行出现降级迹象，当前结果建议人工复核。",
        }
        items: list[str] = []
        for code in diagnostics[:3]:
            text = mapping.get(str(code), str(code).replace("_", " "))
            if text and text not in items:
                items.append(text)
        for reason_text in list(feedback.get("reason_texts") or []):
            text = str(reason_text or "").strip()
            if text and text not in items:
                items.append(text)
        error_text = BrainHumanReadablePresenter.truncate_text(last_error, limit=100)
        if error_text:
            items.append(f"最近错误：{error_text}")
        return items

    @staticmethod
    def action_items(
        next_action: dict[str, Any],
        *,
        diagnostics: list[Any],
        latest_event: dict[str, Any] | None = None,
        status: str = "ok",
    ) -> list[str]:
        items: list[str] = []
        label = str(next_action.get("label") or "").strip()
        description = str(next_action.get("description") or "").strip()
        if label:
            items.append(f"{label}：{description}" if description else label)
        if bool(next_action.get("requires_confirmation")):
            items.append("如需继续执行，请直接用自然语言明确回复“确认执行”或补充确认参数。")
        diagnostic_codes = {str(item) for item in diagnostics}
        if "runtime_state=error" in diagnostic_codes:
            items.append("先恢复运行态，再继续训练、配置修改或问股请求。")
        if "data_quality_unhealthy" in diagnostic_codes:
            items.append("先执行数据状态检查或刷新，确认数据健康后再继续下游任务。")
        latest_event_name = str((latest_event or {}).get("event") or "")
        if latest_event_name == "training_finished":
            items.append("查看最近训练结果、排行榜和生成工件，确认是否需要继续迭代。")
        elif latest_event_name == "training_started":
            items.append("继续关注事件流和运行状态，等待训练完成后再查看结果。")
        if status == "ok" and not items:
            items.append("可以继续发起更具体的自然语言任务，例如训练、问股或配置诊断。")
        deduped: list[str] = []
        for item in items:
            if item and item not in deduped:
                deduped.append(item)
        return deduped

    @staticmethod
    def risk_level_text(risk_level: str) -> str:
        mapping = {
            RISK_LEVEL_LOW: "低风险，可直接继续读取或查看结果。",
            RISK_LEVEL_MEDIUM: "中风险，建议先核对关键参数、数据状态或最近事件。",
            RISK_LEVEL_HIGH: "高风险，建议先确认操作范围与影响，再继续执行。",
        }
        return mapping.get(str(risk_level or ""), "")

    @staticmethod
    def operation_nature_text(gate: dict[str, Any]) -> str:
        writes_state = bool(gate.get("writes_state"))
        if writes_state:
            return "本次属于写操作，可能会改动系统状态、配置或运行工件。"
        return "本次属于只读分析，不会改动系统状态。"

    @staticmethod
    def confirmation_text(gate: dict[str, Any], *, status: str) -> str:
        confirmation = dict(gate.get("confirmation") or {})
        state = str(confirmation.get("state") or "")
        writes_state = bool(gate.get("writes_state"))
        requires_confirmation = bool(gate.get("requires_confirmation"))
        if requires_confirmation or state == "pending_confirmation" or status == "confirmation_required":
            return "当前仍需人工确认，系统不会直接执行写入动作。"
        if writes_state:
            return "当前写操作已确认或无需额外确认，可以按流程继续执行。"
        return "当前无需人工确认，可以直接继续查看或追问。"

    @staticmethod
    def compose_human_readable_receipt(
        *,
        title: str,
        summary: str,
        operation: str,
        facts: list[str] | None = None,
        risks: list[str] | None = None,
        suggested_actions: list[str] | None = None,
        recommended_next_step: str = "",
        risk_level: str = "",
        latest_event: dict[str, Any] | None = None,
        event_explanation: str = "",
        event_timeline: list[str] | None = None,
        operation_nature: str = "",
        risk_summary: str = "",
        confirmation_summary: str = "",
    ) -> dict[str, Any]:
        fact_items = [str(item) for item in list(facts or []) if str(item or "").strip()]
        risk_items = [str(item) for item in list(risks or []) if str(item or "").strip()]
        action_items = [
            str(item) for item in list(suggested_actions or []) if str(item or "").strip()
        ]
        timeline_items = [
            str(item) for item in list(event_timeline or []) if str(item or "").strip()
        ]
        bullets = list(fact_items)
        posture_items = [
            str(item)
            for item in [operation_nature, risk_summary, confirmation_summary]
            if str(item or "").strip()
        ]
        bullets.extend(posture_items)
        if event_explanation:
            bullets.append(f"事件解释：{event_explanation}")
        bullets.extend(f"最近事件：{item}" for item in timeline_items[:2])
        bullets.extend(f"关注项：{item}" for item in risk_items[:2])
        bullets.extend(f"建议动作：{item}" for item in action_items[:2])
        sections: list[dict[str, Any]] = [{"label": "结论", "text": summary}]
        if posture_items:
            sections.append({"label": "执行性质", "items": posture_items})
        if fact_items:
            sections.append({"label": "现状", "items": fact_items})
        if event_explanation:
            sections.append({"label": "事件解释", "text": event_explanation})
        if timeline_items:
            sections.append({"label": "最近事件", "items": timeline_items})
        if risk_items:
            sections.append({"label": "风险提示", "items": risk_items})
        if action_items:
            sections.append({"label": "建议动作", "items": action_items})
        receipt_lines = [f"结论：{summary}"]
        if operation_nature:
            receipt_lines.append(f"执行性质：{operation_nature}")
        if risk_summary:
            receipt_lines.append(f"风险等级：{risk_summary}")
        if confirmation_summary:
            receipt_lines.append(f"确认要求：{confirmation_summary}")
        if fact_items:
            receipt_lines.append("现状：" + "；".join(fact_items[:6]))
        if event_explanation:
            receipt_lines.append("事件解释：" + event_explanation)
        if timeline_items:
            receipt_lines.append("最近事件：" + "；".join(timeline_items[:2]))
        if risk_items:
            receipt_lines.append("风险提示：" + "；".join(risk_items[:2]))
        if action_items:
            receipt_lines.append("建议动作：" + "；".join(action_items[:2]))
        return {
            "title": title,
            "summary": summary,
            "bullets": bullets,
            "facts": fact_items,
            "risks": risk_items,
            "suggested_actions": action_items,
            "event_explanation": event_explanation,
            "event_timeline": timeline_items,
            "sections": sections,
            "receipt_text": "\n".join(receipt_lines),
            "recommended_next_step": recommended_next_step,
            "risk_level": risk_level,
            "latest_event": dict(latest_event or {}),
            "operation_nature": operation_nature,
            "risk_summary": risk_summary,
            "confirmation_summary": confirmation_summary,
            "operation": operation,
        }

    @classmethod
    def build_human_readable_receipt(
        cls,
        payload: dict[str, Any],
        *,
        intent: str,
        operation: str,
    ) -> dict[str, Any]:
        feedback = dict(payload.get("feedback") or {})
        next_action = dict(payload.get("next_action") or {})
        status = str(payload.get("status") or "ok")
        task_bus = dict(payload.get("task_bus") or {})
        gate = dict(task_bus.get("gate") or {})
        risk_level = str(gate.get("risk_level") or "")
        operation_nature = cls.operation_nature_text(gate)
        risk_summary = cls.risk_level_text(risk_level)
        confirmation_summary = cls.confirmation_text(gate, status=status)

        if intent in {
            "runtime_status",
            "runtime_diagnostics",
            "runtime_status_and_training",
            "config_risk_diagnostics",
        }:
            quick_status = dict(payload.get("quick_status") or {})
            runtime_payload = dict(payload.get("runtime") or quick_status.get("runtime") or {})
            plugins = dict(payload.get("plugins") or quick_status.get("plugins") or {})
            event_summary = dict(
                payload.get("event_summary")
                or payload.get("events")
                or quick_status.get("events")
                or {}
            )
            recent_events = list(payload.get("recent_events") or payload.get("items") or [])
            training_lab = dict(
                payload.get("training_lab") or quick_status.get("training_lab") or {}
            )
            diagnostics = list(payload.get("diagnostics") or [])
            facts = []
            facts.extend(cls.runtime_state_bullets(runtime_payload))
            if plugins:
                facts.append(f"插件数：{int(plugins.get('count', 0) or 0)}")
            if event_summary:
                facts.append(f"事件数：{int(event_summary.get('count', 0) or 0)}")
            event_bullets, latest_event, event_explanation = cls.event_explanation_bullets(
                event_summary,
                recent_events=recent_events,
            )
            event_timeline = cls.event_timeline_items(recent_events)
            facts.extend(event_bullets)
            facts.extend(cls.training_lab_bullets(training_lab))
            facts.extend(cls.runtime_governance_bullets(payload))
            risks = cls.risk_explanations(
                diagnostics,
                feedback=feedback,
                last_error=payload.get("last_error") or "",
            )
            actions = cls.action_items(
                next_action,
                diagnostics=diagnostics,
                latest_event=latest_event,
                status=status,
            )
            summary = str(feedback.get("summary") or "已生成运行时摘要。")
            if status == "ok" and not diagnostics:
                summary = "系统可用，已返回运行状态、事件与训练摘要。"
            elif status == "ok" and diagnostics:
                summary = f"系统仍可用，但有 {len(risks)} 项需要优先关注。"
            return cls.compose_human_readable_receipt(
                title="系统运行摘要",
                summary=summary,
                operation=operation,
                facts=facts,
                risks=risks,
                suggested_actions=actions,
                recommended_next_step=str(next_action.get("label") or ""),
                risk_level=risk_level,
                latest_event=latest_event,
                event_explanation=event_explanation,
                event_timeline=event_timeline,
                operation_nature=operation_nature,
                risk_summary=risk_summary,
                confirmation_summary=confirmation_summary,
            )

        if intent in {"training_lab_summary", "training_execution"}:
            training_lab = dict(payload.get("training_lab") or payload)
            facts = cls.training_lab_bullets(training_lab)
            facts.extend(cls.runtime_governance_bullets(payload))
            latest_result = cls.latest_training_result_summary(payload)
            if latest_result:
                cycle_id = latest_result.get("cycle_id")
                if cycle_id is not None:
                    facts.append(f"最新训练周期：{int(cycle_id)}")
                if latest_result.get("return_pct") is not None:
                    facts.append(f"最新收益：{float(latest_result.get('return_pct') or 0.0):+.2f}%")
                promotion_record = dict(latest_result.get("promotion_record") or {})
                if promotion_record:
                    facts.append(
                        "晋升状态："
                        + str(promotion_record.get("status") or "unknown")
                        + " / "
                        + str(promotion_record.get("gate_status") or "unknown")
                    )
                lineage_record = dict(latest_result.get("lineage_record") or {})
                if lineage_record:
                    facts.append("lineage：" + str(lineage_record.get("lineage_status") or "unknown"))
            risks = cls.risk_explanations([], feedback=feedback)
            actions = cls.action_items(next_action, diagnostics=[], status=status)
            return cls.compose_human_readable_receipt(
                title="训练实验室摘要",
                summary=str(feedback.get("summary") or "已返回训练实验室状态。"),
                operation=operation,
                facts=facts,
                risks=risks,
                suggested_actions=actions,
                recommended_next_step=str(next_action.get("label") or ""),
                risk_level=risk_level,
                event_explanation="",
                operation_nature=operation_nature,
                risk_summary=risk_summary,
                confirmation_summary=confirmation_summary,
            )

        if intent.startswith("config_") or intent in {"runtime_paths", "config_overview"}:
            control_plane = dict(payload.get("control_plane") or {})
            evolution_config = dict(payload.get("evolution_config") or {})
            facts: list[str] = []
            if control_plane:
                provider = str(
                    control_plane.get("provider") or control_plane.get("default_provider") or ""
                )
                if provider:
                    facts.append(f"控制面 Provider：{provider}")
            if evolution_config:
                manager_id = str(evolution_config.get("default_manager_id") or "")
                if manager_id:
                    facts.append(f"默认经理：{manager_id}")
            return cls.compose_human_readable_receipt(
                title="配置摘要",
                summary=str(feedback.get("summary") or "已返回配置与控制面信息。"),
                operation=operation,
                facts=facts,
                risks=cls.risk_explanations([], feedback=feedback),
                suggested_actions=cls.action_items(next_action, diagnostics=[], status=status),
                recommended_next_step=str(next_action.get("label") or ""),
                risk_level=risk_level,
                event_explanation="",
                operation_nature=operation_nature,
                risk_summary=risk_summary,
                confirmation_summary=confirmation_summary,
            )

        return cls.compose_human_readable_receipt(
            title="执行摘要",
            summary=str(
                feedback.get("summary") or payload.get("message") or payload.get("reply") or ""
            ),
            operation=operation,
            facts=[],
            risks=cls.risk_explanations([], feedback=feedback),
            suggested_actions=cls.action_items(next_action, diagnostics=[], status=status),
            recommended_next_step=str(next_action.get("label") or ""),
            risk_level=risk_level,
            event_explanation="",
            operation_nature=operation_nature,
            risk_summary=risk_summary,
            confirmation_summary=confirmation_summary,
        )

    @classmethod
    def attach_human_readable_receipt(
        cls,
        payload: dict[str, Any],
        *,
        intent: str,
        operation: str,
    ) -> dict[str, Any]:
        enriched = dict(payload or {})
        enriched["human_readable"] = cls.build_human_readable_receipt(
            enriched,
            intent=intent,
            operation=operation,
        )
        return enriched
