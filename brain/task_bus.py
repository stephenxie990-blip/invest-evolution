from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from brain.schema_contract import (
    ARTIFACT_KINDS,
    ARTIFACT_TAXONOMY_SCHEMA_VERSION,
    CONFIRMATION_STATE_CONFIRMED_OR_NOT_REQUIRED,
    CONFIRMATION_STATE_NOT_APPLICABLE,
    CONFIRMATION_STATE_PENDING,
    COVERAGE_KIND_PLAN_EXECUTION,
    COVERAGE_KIND_WORKFLOW_PHASE,
    COVERAGE_SCHEMA_VERSION,
    MUTATING_DEFAULT_REASON_CODES,
    PLAN_SCHEMA_VERSION,
    READONLY_DEFAULT_REASON_CODES,
    REASON_CONFIRMATION_REQUIRED,
    REASON_INCOMPLETE_PARAMETER_COVERAGE,
    REASON_INCOMPLETE_PLAN_COVERAGE,
    RISK_LEVEL_HIGH,
    RISK_LEVEL_LOW,
    RISK_LEVEL_MEDIUM,
    TASK_BUS_SCHEMA_VERSION,
)


@dataclass
class TaskPlan:
    intent: str
    operation: str
    mode: str
    user_goal: str
    available_tools: list[str] = field(default_factory=list)
    recommended_plan: list[dict[str, Any]] = field(default_factory=list)
    plan_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "operation": self.operation,
            "mode": self.mode,
            "user_goal": self.user_goal,
            "available_tools": list(self.available_tools),
            "recommended_plan": list(self.recommended_plan),
            "plan_summary": dict(self.plan_summary),
        }


@dataclass
class TaskGate:
    decision: str
    risk_level: str
    writes_state: bool
    requires_confirmation: bool
    reasons: list[str] = field(default_factory=list)
    confirmation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "risk_level": self.risk_level,
            "writes_state": self.writes_state,
            "requires_confirmation": self.requires_confirmation,
            "reasons": list(self.reasons),
            "confirmation": dict(self.confirmation),
        }


@dataclass
class TaskAudit:
    status: str
    started_at: str
    completed_at: str
    tool_count: int
    used_tools: list[str] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    artifact_taxonomy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "tool_count": self.tool_count,
            "used_tools": list(self.used_tools),
            "artifacts": dict(self.artifacts),
            "coverage": dict(self.coverage),
            "artifact_taxonomy": dict(self.artifact_taxonomy),
        }




def _normalize_plan_step(step: dict[str, Any], index: int) -> dict[str, Any]:
    payload = dict(step or {})
    tool = str(payload.get("tool") or "").strip()
    args = dict(payload.get("args") or {})
    normalized = {
        "step_id": str(payload.get("step_id") or f"step_{index:02d}"),
        "tool": tool,
        "args": args,
    }
    if "thought" in payload and payload.get("thought") is not None:
        normalized["thought"] = str(payload.get("thought"))
    for key, value in payload.items():
        if key not in normalized:
            normalized[key] = value
    return normalized


def _normalize_recommended_plan(recommended_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_normalize_plan_step(dict(step or {}), index) for index, step in enumerate(list(recommended_plan or []), start=1)]


def _normalize_plan_tools(recommended_plan: list[dict[str, Any]]) -> list[str]:
    tools: list[str] = []
    for step in _normalize_recommended_plan(recommended_plan):
        tool = str(step.get("tool") or "").strip()
        if tool and tool not in tools:
            tools.append(tool)
    return tools


def _args_subset_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(key in actual and _args_subset_match(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) > len(actual):
            return False
        return all(_args_subset_match(value, actual[index]) for index, value in enumerate(expected))
    return expected == actual


def _parameter_coverage(recommended_plan: list[dict[str, Any]], tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    recommended_steps = _normalize_recommended_plan(recommended_plan)
    parameterized_steps = [step for step in recommended_steps if dict(step.get("args") or {})]
    matched_step_ids: list[str] = []
    for step in parameterized_steps:
        step_tool = str(step.get("tool") or "")
        expected_args = dict(step.get("args") or {})
        for item in list(tool_calls or []):
            action = dict(item.get("action") or {})
            if str(action.get("tool") or "") != step_tool:
                continue
            actual_args = dict(action.get("args") or {})
            if _args_subset_match(expected_args, actual_args):
                matched_step_ids.append(str(step.get("step_id") or ""))
                break
    missing_step_ids = [str(step.get("step_id") or "") for step in parameterized_steps if str(step.get("step_id") or "") not in matched_step_ids]
    coverage = 1.0 if not parameterized_steps else round(len(matched_step_ids) / len(parameterized_steps), 3)
    return {
        "parameterized_step_count": len(parameterized_steps),
        "covered_parameterized_step_ids": matched_step_ids,
        "missing_parameterized_step_ids": missing_step_ids,
        "parameter_coverage": coverage,
    }


def _build_plan_summary(*, available_tools: list[str], recommended_plan: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_plan = _normalize_recommended_plan(recommended_plan)
    recommended_tools = _normalize_plan_tools(normalized_plan)
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "available_tool_count": len(list(available_tools or [])),
        "recommended_step_count": len(normalized_plan),
        "recommended_tool_count": len(recommended_tools),
        "recommended_tools": recommended_tools,
        "step_ids": [str(step.get("step_id") or "") for step in normalized_plan],
    }


def _default_coverage(*, recommended_plan: list[dict[str, Any]], tool_calls: list[dict[str, Any]], available_tools: list[str]) -> dict[str, Any]:
    recommended_steps = _normalize_recommended_plan(recommended_plan)
    used_tools = [str(item.get("action", {}).get("tool") or "") for item in list(tool_calls or []) if item.get("action")]
    used_tool_set = {tool for tool in used_tools if tool}
    recommended_tools = _normalize_plan_tools(recommended_steps)
    covered_tools = [tool for tool in recommended_tools if tool in used_tool_set]
    covered_step_ids = [str(step.get("step_id") or "") for step in recommended_steps if str(step.get("tool") or "") in used_tool_set]
    planned_step_coverage = 1.0 if not recommended_steps else round(len(covered_step_ids) / len(recommended_steps), 3)
    missing_steps = [step for step in recommended_steps if str(step.get("tool") or "") not in used_tool_set]
    parameter_coverage = _parameter_coverage(recommended_steps, tool_calls)
    return {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "coverage_kind": COVERAGE_KIND_PLAN_EXECUTION,
        "recommended_step_count": len(recommended_steps),
        "executed_step_count": len(list(tool_calls or [])),
        "available_tool_count": len(list(available_tools or [])),
        "used_tool_count": len(used_tool_set),
        "recommended_tool_count": len(recommended_tools),
        "covered_recommended_tools": covered_tools,
        "covered_recommended_step_ids": covered_step_ids,
        "missing_planned_steps": missing_steps,
        "missing_planned_step_ids": [str(step.get("step_id") or "") for step in missing_steps],
        "planned_step_coverage": planned_step_coverage,
        "required_tool_coverage": 1.0 if not recommended_tools else round(len(covered_tools) / len(recommended_tools), 3),
        "parameterized_step_count": parameter_coverage["parameterized_step_count"],
        "covered_parameterized_step_ids": parameter_coverage["covered_parameterized_step_ids"],
        "missing_parameterized_step_ids": parameter_coverage["missing_parameterized_step_ids"],
        "parameter_coverage": parameter_coverage["parameter_coverage"],
    }


def _normalize_coverage(*, coverage: dict[str, Any] | None, recommended_plan: list[dict[str, Any]], tool_calls: list[dict[str, Any]], available_tools: list[str]) -> dict[str, Any]:
    base = _default_coverage(
        recommended_plan=recommended_plan,
        tool_calls=tool_calls,
        available_tools=available_tools,
    )
    if not coverage:
        return base
    merged = {**base, **dict(coverage)}
    merged.setdefault("schema_version", COVERAGE_SCHEMA_VERSION)
    merged.setdefault("coverage_kind", COVERAGE_KIND_PLAN_EXECUTION)
    merged.setdefault("recommended_step_count", len(_normalize_recommended_plan(recommended_plan)))
    merged.setdefault("executed_step_count", len(list(tool_calls or [])))
    merged.setdefault("available_tool_count", len(list(available_tools or [])))
    merged.setdefault("used_tool_count", len({str(item.get("action", {}).get("tool") or "") for item in list(tool_calls or []) if item.get("action")}))
    merged.setdefault("recommended_tool_count", len(_normalize_plan_tools(recommended_plan)))
    merged.setdefault("planned_step_coverage", base.get("planned_step_coverage", 1.0))
    merged.setdefault("required_tool_coverage", base.get("required_tool_coverage", 1.0))
    merged.setdefault("parameterized_step_count", base.get("parameterized_step_count", 0))
    merged.setdefault("covered_parameterized_step_ids", base.get("covered_parameterized_step_ids", []))
    merged.setdefault("missing_parameterized_step_ids", base.get("missing_parameterized_step_ids", []))
    merged.setdefault("parameter_coverage", base.get("parameter_coverage", 1.0))
    merged.setdefault("missing_planned_steps", base.get("missing_planned_steps", []))
    merged["missing_planned_steps"] = _normalize_recommended_plan(list(merged.get("missing_planned_steps") or []))
    merged.setdefault("missing_planned_step_ids", [str(step.get("step_id") or "") for step in list(merged.get("missing_planned_steps") or [])])
    merged.setdefault("covered_recommended_step_ids", base.get("covered_recommended_step_ids", []))
    return merged


def _confirmation_state(*, writes_state: bool, requires_confirmation: bool, decision: str) -> str:
    if requires_confirmation:
        return CONFIRMATION_STATE_PENDING
    if writes_state:
        return CONFIRMATION_STATE_CONFIRMED_OR_NOT_REQUIRED if decision == "allow" else str(decision or "pending")
    return CONFIRMATION_STATE_NOT_APPLICABLE


def _build_confirmation_summary(*, writes_state: bool, requires_confirmation: bool, decision: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "required": bool(requires_confirmation),
        "decision": str(decision),
        "state": _confirmation_state(writes_state=writes_state, requires_confirmation=requires_confirmation, decision=decision),
        "reason_codes": list(reasons or []),
    }


def _reason_human_text(reason_code: str) -> str:
    mapping = {
        REASON_CONFIRMATION_REQUIRED: "当前操作仍需要人工确认",
        REASON_INCOMPLETE_PLAN_COVERAGE: "推荐计划尚未被完整覆盖",
        REASON_INCOMPLETE_PARAMETER_COVERAGE: "关键参数执行尚未完整对齐推荐计划",
    }
    return mapping.get(str(reason_code or ""), str(reason_code or "").replace("_", " "))


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
    task_bus_payload = dict(body.get("task_bus") or {})
    fallback_message = str(body.get("message") or body.get("reply") or default_message or default_reply or "")
    feedback = dict(body.get("feedback") or build_gate_feedback(task_bus=task_bus_payload, default_message=fallback_message))
    next_action = dict(body.get("next_action") or build_next_action(task_bus=task_bus_payload, feedback=feedback))
    body["feedback"] = feedback
    body["next_action"] = next_action
    return build_response_envelope(payload=body, default_reply=str(default_reply or default_message or feedback.get("summary") or ""))


def build_response_envelope(*, payload: dict[str, Any], default_reply: str = "") -> dict[str, Any]:
    body = dict(payload or {})
    task_bus = dict(body.get("task_bus") or {})
    feedback = dict(body.get("feedback") or build_gate_feedback(task_bus=task_bus, default_message=str(body.get("message") or body.get("reply") or default_reply or "")))
    next_action = dict(body.get("next_action") or build_next_action(task_bus=task_bus, feedback=feedback))
    message = str(body.get("message") or feedback.get("message") or body.get("reply") or default_reply or feedback.get("summary") or "")
    reply = str(body.get("reply") or message)
    body["feedback"] = feedback
    body["next_action"] = next_action
    body["message"] = message
    body["reply"] = reply
    if "status" not in body:
        body["status"] = str(dict(task_bus.get("audit") or {}).get("status") or "ok")
    return body


def build_next_action(*, task_bus: dict[str, Any], feedback: dict[str, Any] | None = None) -> dict[str, Any]:
    gate = dict(task_bus.get("gate") or {})
    planner = dict(task_bus.get("planner") or {})
    audit = dict(task_bus.get("audit") or {})
    feedback_payload = dict(feedback or {})
    requires_confirmation = bool(gate.get("requires_confirmation"))
    reason_codes = [str(item) for item in list(feedback_payload.get("reason_codes") or gate.get("reasons") or []) if str(item or "")]
    plan = list(planner.get("recommended_plan") or [])
    suggested_params = dict((plan[0] or {}).get("args") or {}) if plan else {}
    status = str(audit.get("status") or "")
    artifacts = dict(audit.get("artifacts") or {})
    has_path_artifact = any(isinstance(value, str) and ("/" in value or chr(92) in value) for value in artifacts.values())

    if requires_confirmation:
        return {
            "kind": "confirm",
            "label": "补充确认后重试",
            "description": "当前任务需要人工确认，建议按推荐参数补充 confirm=true 后重试。",
            "requires_confirmation": True,
            "suggested_params": suggested_params,
        }
    if status in {"error", "failed", "partial_failure", "insufficient_data"}:
        return {
            "kind": "rerun",
            "label": "调整参数后重试",
            "description": "当前任务未稳定完成，建议检查输入与环境后重新执行。",
            "requires_confirmation": False,
            "suggested_params": suggested_params,
        }
    if "incomplete_plan_coverage" in reason_codes or "incomplete_parameter_coverage" in reason_codes:
        return {
            "kind": "review",
            "label": "补充证据或复核结果",
            "description": "推荐计划或参数覆盖不完整，建议补充证据或人工复核后再使用结果。",
            "requires_confirmation": False,
            "suggested_params": suggested_params,
        }
    if has_path_artifact:
        return {
            "kind": "inspect_artifact",
            "label": "查看生成产物",
            "description": "当前任务已产出可检查的文件或工件，建议先查看产物再决定后续动作。",
            "requires_confirmation": False,
            "suggested_params": suggested_params,
        }
    return {
        "kind": "continue",
        "label": "可继续下一步",
        "description": "当前结果满足协议要求，可继续后续分析或执行下一任务。",
        "requires_confirmation": False,
        "suggested_params": suggested_params,
    }


def build_gate_feedback(*, task_bus: dict[str, Any], default_message: str = "") -> dict[str, Any]:
    gate = dict(task_bus.get("gate") or {})
    audit = dict(task_bus.get("audit") or {})
    coverage = dict(audit.get("coverage") or {})
    reasons = [str(item) for item in list(gate.get("reasons") or gate.get("confirmation", {}).get("reason_codes") or []) if str(item or "")]
    human_reasons = [_reason_human_text(code) for code in reasons]
    planned_step_coverage = _coverage_float(coverage, "planned_step_coverage", 1.0)
    parameter_coverage = _coverage_float(coverage, "parameter_coverage", 1.0)
    requires_confirmation = bool(gate.get("requires_confirmation"))
    writes_state = bool(gate.get("writes_state"))

    if requires_confirmation and writes_state:
        summary = "当前任务仍需人工确认后才能视为审计闭环完成。"
    elif writes_state and (planned_step_coverage < 1.0 or parameter_coverage < 1.0):
        summary = "任务已执行，但审计计划覆盖不足，仍需人工复核。"
    elif planned_step_coverage < 1.0 or parameter_coverage < 1.0:
        summary = "分析已完成，但证据覆盖仍不完整，结论应谨慎使用。"
    else:
        summary = "当前任务已完成，计划与参数覆盖满足预期。"

    message = str(default_message or "").strip()
    if human_reasons:
        reason_text = "；".join(human_reasons)
        if message:
            message = f"{message} 原因：{reason_text}。"
        else:
            message = f"{summary} 原因：{reason_text}。"
    elif not message:
        message = summary

    return {
        "message": message,
        "summary": summary,
        "reason_codes": reasons,
        "reason_texts": human_reasons,
        "requires_confirmation": requires_confirmation,
        "decision": str(gate.get("decision") or "allow"),
        "coverage": {
            "planned_step_coverage": planned_step_coverage,
            "parameter_coverage": parameter_coverage,
        },
    }


def _add_reason(reasons: list[str], code: str) -> list[str]:
    if code and code not in reasons:
        reasons.append(code)
    return reasons


def _coverage_float(coverage: dict[str, Any], key: str, default: float = 1.0) -> float:
    try:
        return float(coverage.get(key, default))
    except (TypeError, ValueError, AttributeError):
        return float(default)


def _derive_gate_policy(
    *,
    writes_state: bool,
    risk_level: str,
    decision: str,
    requires_confirmation: bool,
    reasons: list[str],
    coverage: dict[str, Any],
) -> tuple[str, str, bool, list[str]]:
    normalized_reasons = list(reasons or [])
    planned_step_coverage = _coverage_float(coverage, "planned_step_coverage", 1.0)
    parameter_coverage = _coverage_float(coverage, "parameter_coverage", 1.0)
    has_plan_gap = planned_step_coverage < 1.0
    has_parameter_gap = parameter_coverage < 1.0

    if requires_confirmation:
        _add_reason(normalized_reasons, REASON_CONFIRMATION_REQUIRED)
    if has_plan_gap:
        _add_reason(normalized_reasons, REASON_INCOMPLETE_PLAN_COVERAGE)
    if has_parameter_gap:
        _add_reason(normalized_reasons, REASON_INCOMPLETE_PARAMETER_COVERAGE)

    derived_requires_confirmation = bool(requires_confirmation)
    derived_risk_level = str(risk_level or (RISK_LEVEL_MEDIUM if writes_state else RISK_LEVEL_LOW))
    derived_decision = str(decision or "allow")

    if writes_state and (has_plan_gap or has_parameter_gap):
        derived_requires_confirmation = True
        derived_decision = "confirm"
        derived_risk_level = RISK_LEVEL_HIGH
    elif writes_state and derived_requires_confirmation:
        derived_decision = "confirm"
        derived_risk_level = RISK_LEVEL_HIGH

    return derived_decision, derived_risk_level, derived_requires_confirmation, normalized_reasons


def _classify_artifact_value(value: Any) -> str:
    if isinstance(value, str):
        lower = value.lower()
        if "/" in value or chr(92) in value or lower.endswith((".json", ".jsonl", ".md", ".csv", ".txt", ".log", ".yaml", ".yml")):
            return "path"
        if lower.endswith(("_id", "id")):
            return "id"
        return "scalar"
    if isinstance(value, (int, float, bool)) or value is None:
        return "scalar"
    if isinstance(value, list):
        return "collection"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def build_artifact_taxonomy(artifacts: dict[str, Any] | None = None) -> dict[str, Any]:
    return _build_artifact_taxonomy(dict(artifacts or {}))


def build_workflow_phase_coverage(
    *,
    workflow: list[str],
    phase_stats: dict[str, Any] | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    coverage = {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "coverage_kind": COVERAGE_KIND_WORKFLOW_PHASE,
        "workflow_step_count": len(list(workflow or [])),
        "completed_workflow_step_count": len(list(workflow or [])),
        "workflow_step_coverage": 1.0 if workflow else 1.0,
        "phase_stat_key_count": len(dict(phase_stats or {})),
    }
    if existing:
        coverage.update(dict(existing))
    coverage.setdefault("schema_version", COVERAGE_SCHEMA_VERSION)
    coverage.setdefault("coverage_kind", COVERAGE_KIND_WORKFLOW_PHASE)
    return coverage


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


def build_bounded_workflow_protocol(*, schema_version: str, domain: str, operation: str) -> dict[str, Any]:
    return {
        "schema_version": str(schema_version),
        "task_bus_schema_version": TASK_BUS_SCHEMA_VERSION,
        "plan_schema_version": PLAN_SCHEMA_VERSION,
        "coverage_schema_version": COVERAGE_SCHEMA_VERSION,
        "artifact_taxonomy_schema_version": ARTIFACT_TAXONOMY_SCHEMA_VERSION,
        "domain": str(domain),
        "operation": str(operation),
    }


def _build_artifact_taxonomy(artifacts: dict[str, Any]) -> dict[str, Any]:
    items = dict(artifacts or {})
    kinds = {key: _classify_artifact_value(value) for key, value in items.items()}
    return {
        "schema_version": ARTIFACT_TAXONOMY_SCHEMA_VERSION,
        "count": len(items),
        "keys": sorted(items.keys()),
        "kinds": kinds,
        "path_keys": sorted([key for key, kind in kinds.items() if kind == "path"]),
        "object_keys": sorted([key for key, kind in kinds.items() if kind == "object"]),
        "collection_keys": sorted([key for key, kind in kinds.items() if kind == "collection"]),
        "known_kinds": list(ARTIFACT_KINDS),
    }


def build_task_bus(
    *,
    intent: str,
    operation: str,
    user_goal: str,
    mode: str,
    available_tools: list[str],
    recommended_plan: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    artifacts: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    status: str = "ok",
    writes_state: bool = False,
    risk_level: str = RISK_LEVEL_LOW,
    decision: str = "allow",
    requires_confirmation: bool = False,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    started = datetime.now().isoformat()
    completed = datetime.now().isoformat()
    normalized_reasons = list(reasons or [])
    normalized_plan = _normalize_recommended_plan(recommended_plan)
    normalized_artifacts = dict(artifacts or {})
    normalized_coverage = _normalize_coverage(
        coverage=coverage,
        recommended_plan=normalized_plan,
        tool_calls=tool_calls,
        available_tools=available_tools,
    )
    decision, risk_level, requires_confirmation, normalized_reasons = _derive_gate_policy(
        writes_state=writes_state,
        risk_level=risk_level,
        decision=decision,
        requires_confirmation=requires_confirmation,
        reasons=normalized_reasons,
        coverage=normalized_coverage,
    )
    planner = TaskPlan(
        intent=intent,
        operation=operation,
        mode=mode,
        user_goal=user_goal,
        available_tools=available_tools,
        recommended_plan=normalized_plan,
        plan_summary=_build_plan_summary(available_tools=available_tools, recommended_plan=normalized_plan),
    )
    gate = TaskGate(
        decision=decision,
        risk_level=risk_level,
        writes_state=writes_state,
        requires_confirmation=requires_confirmation,
        reasons=normalized_reasons,
        confirmation=_build_confirmation_summary(
            writes_state=writes_state,
            requires_confirmation=requires_confirmation,
            decision=decision,
            reasons=normalized_reasons,
        ),
    )
    audit = TaskAudit(
        status=status,
        started_at=started,
        completed_at=completed,
        tool_count=len(tool_calls),
        used_tools=[str(item.get("action", {}).get("tool") or "") for item in tool_calls if item.get("action")],
        artifacts=normalized_artifacts,
        coverage=normalized_coverage,
        artifact_taxonomy=_build_artifact_taxonomy(normalized_artifacts),
    )
    return {
        "schema_version": TASK_BUS_SCHEMA_VERSION,
        "planner": planner.to_dict(),
        "gate": gate.to_dict(),
        "audit": audit.to_dict(),
    }


def build_readonly_task_bus(
    *,
    intent: str,
    operation: str,
    user_goal: str,
    mode: str,
    available_tools: list[str],
    recommended_plan: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    artifacts: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    status: str = "ok",
) -> dict[str, Any]:
    return build_task_bus(
        intent=intent,
        operation=operation,
        user_goal=user_goal,
        mode=mode,
        available_tools=available_tools,
        recommended_plan=recommended_plan,
        tool_calls=tool_calls,
        artifacts=artifacts,
        coverage=coverage,
        status=status,
        writes_state=False,
        risk_level=RISK_LEVEL_LOW,
        decision="allow",
        requires_confirmation=False,
        reasons=list(READONLY_DEFAULT_REASON_CODES),
    )


def build_mutating_task_bus(
    *,
    intent: str,
    operation: str,
    user_goal: str,
    mode: str,
    available_tools: list[str],
    recommended_plan: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    artifacts: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    status: str = "ok",
    risk_level: str = RISK_LEVEL_MEDIUM,
    decision: str = "allow",
    requires_confirmation: bool = False,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    return build_task_bus(
        intent=intent,
        operation=operation,
        user_goal=user_goal,
        mode=mode,
        available_tools=available_tools,
        recommended_plan=recommended_plan,
        tool_calls=tool_calls,
        artifacts=artifacts,
        coverage=coverage,
        status=status,
        writes_state=True,
        risk_level=risk_level,
        decision=decision,
        requires_confirmation=requires_confirmation,
        reasons=list(reasons or MUTATING_DEFAULT_REASON_CODES),
    )


__all__ = [
    "ARTIFACT_TAXONOMY_SCHEMA_VERSION",
    "COVERAGE_SCHEMA_VERSION",
    "PLAN_SCHEMA_VERSION",
    "TASK_BUS_SCHEMA_VERSION",
    "build_gate_feedback",
    "build_next_action",
    "build_mutating_task_bus",
    "build_readonly_task_bus",
    "build_task_bus",
    "TaskAudit",
    "TaskGate",
    "TaskPlan",
]
