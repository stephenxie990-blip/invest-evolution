"""Shared workflow assembly helpers for commander bounded responses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from brain.schema_contract import (
    BOUNDED_WORKFLOW_SCHEMA_VERSION,
    COVERAGE_KIND_WORKFLOW_PHASE,
    COVERAGE_SCHEMA_VERSION,
)
from brain.task_bus import (
    build_bounded_entrypoint,
    build_bounded_orchestration,
    build_bounded_policy,
    build_bounded_response_context,
    build_mutating_task_bus,
    build_protocol_response,
    build_readonly_task_bus,
)


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    return value


def build_workflow_coverage(
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
        "workflow_step_coverage": 1.0,
        "phase_stat_key_count": len(dict(phase_stats or {})),
    }
    if existing:
        coverage.update(dict(existing))
    coverage.setdefault("schema_version", COVERAGE_SCHEMA_VERSION)
    coverage.setdefault("coverage_kind", COVERAGE_KIND_WORKFLOW_PHASE)
    return coverage


def intent_for_bounded_workflow(*, domain: str, operation: str) -> str:
    if operation == "status":
        return f"{domain}_status"
    if operation == "execute_training_plan":
        return "training_plan_execution"
    if operation.startswith("get_"):
        suffix = operation[4:]
        if suffix.startswith(f"{domain}_"):
            suffix = suffix[len(domain) + 1 :]
        return f"{domain}_{suffix}"
    if operation.startswith("update_"):
        suffix = operation[7:]
        return f"{domain}_{suffix}_update"
    if operation.startswith("trigger_"):
        return operation
    return operation


def actual_tool_call_args(
    *,
    runtime_tool: str,
    writes_state: bool,
    recommended_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    matching_steps = [
        step
        for step in list(recommended_plan or [])
        if str(step.get("tool") or "") == runtime_tool
    ]
    if not matching_steps:
        return {}
    selected = matching_steps[-1] if writes_state else matching_steps[0]
    return dict(selected.get("args") or {})


def build_bounded_workflow_task_bus(
    *,
    payload: dict[str, Any],
    domain: str,
    operation: str,
    runtime_tool: str,
    writes_state: bool,
    available_tools: list[str],
    artifacts: dict[str, Any],
    recommended_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    status = str(payload.get("status", "ok") or "ok")
    requires_confirmation = status == "confirmation_required"
    decision = "confirm" if requires_confirmation else "allow"
    tool_calls = [] if requires_confirmation else [
        {
            "action": {
                "tool": runtime_tool,
                "args": actual_tool_call_args(
                    runtime_tool=runtime_tool,
                    writes_state=writes_state,
                    recommended_plan=recommended_plan,
                ),
            }
        }
    ]
    builder = build_mutating_task_bus if writes_state else build_readonly_task_bus
    kwargs = {
        "intent": intent_for_bounded_workflow(domain=domain, operation=operation),
        "operation": operation,
        "user_goal": f"{domain}:{operation}",
        "mode": "commander_runtime_method",
        "available_tools": list(available_tools),
        "recommended_plan": recommended_plan,
        "tool_calls": tool_calls,
        "artifacts": dict(artifacts),
        "status": status,
    }
    if writes_state:
        kwargs.update(
            {
                "risk_level": "high" if requires_confirmation else "medium",
                "decision": decision,
                "requires_confirmation": requires_confirmation,
            }
        )
    return builder(**kwargs)


def attach_bounded_workflow_response(
    *,
    payload: Any,
    domain: str,
    operation: str,
    runtime_method: str,
    runtime_tool: str,
    agent_kind: str,
    writes_state: bool,
    available_tools: list[str],
    workflow: list[str],
    workspace: str,
    recommended_plan: list[dict[str, Any]],
    phase_stats: dict[str, Any] | None = None,
    extra_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = dict(payload) if isinstance(payload, dict) else {"status": "ok", "content": payload}
    body.setdefault(
        "entrypoint",
        build_bounded_entrypoint(
            kind="commander_bounded_workflow",
            domain=domain,
            runtime_method=runtime_method,
            runtime_tool=runtime_tool,
            meeting_path=False,
            agent_kind=agent_kind,
            agent_system="commander_bounded_workflows",
        ),
    )
    normalized_workflow = list(workflow)
    normalized_phase_stats = jsonable(dict(phase_stats or {}))
    orchestration = dict(body.get("orchestration") or {})
    policy = dict(orchestration.get("policy") or {})
    policy.update(
        build_bounded_policy(
            source="commander_runtime",
            domain=domain,
            agent_kind=agent_kind,
            runtime_tool=runtime_tool,
            fixed_boundary=True,
            fixed_workflow=True,
            writes_state=writes_state,
            tool_catalog_scope=f"{domain}_domain",
            extra=jsonable(dict(extra_policy or {})) if extra_policy else None,
        )
    )
    orchestration = build_bounded_orchestration(
        mode=str(
            orchestration.get("mode")
            or ("bounded_mutating_workflow" if writes_state else "bounded_readonly_workflow")
        ),
        available_tools=list(available_tools),
        allowed_tools=list(orchestration.get("allowed_tools") or available_tools),
        workflow=normalized_workflow,
        phase_stats=normalized_phase_stats,
        policy=policy,
        extra={
            key: value
            for key, value in orchestration.items()
            if key not in {"mode", "available_tools", "allowed_tools", "workflow", "phase_stats", "policy"}
        },
    )
    artifacts = {
        "workspace": workspace,
        "runtime_tool": runtime_tool,
        "runtime_method": runtime_method,
        "domain": domain,
        "operation": operation,
    }
    if isinstance(body.get("artifacts"), dict):
        artifacts.update(dict(body.get("artifacts") or {}))
    coverage = build_workflow_coverage(
        workflow=normalized_workflow,
        phase_stats=normalized_phase_stats,
        existing=body.get("coverage") if isinstance(body.get("coverage"), dict) else None,
    )
    body["orchestration"] = orchestration
    bounded_context = build_bounded_response_context(
        schema_version=BOUNDED_WORKFLOW_SCHEMA_VERSION,
        domain=domain,
        operation=operation,
        artifacts=artifacts,
        workflow=normalized_workflow,
        phase_stats=normalized_phase_stats,
        coverage=coverage,
    )
    task_bus = dict(body.get("task_bus") or {})
    if not task_bus:
        task_bus = jsonable(
            build_bounded_workflow_task_bus(
                payload=body,
                domain=domain,
                operation=operation,
                runtime_tool=runtime_tool,
                writes_state=writes_state,
                available_tools=list(available_tools),
                artifacts=artifacts,
                recommended_plan=recommended_plan,
            )
        )
    gate_requires_confirmation = bool(
        dict(task_bus.get("gate") or {}).get("requires_confirmation")
    )
    body = jsonable(
        build_protocol_response(
            payload=body,
            protocol=bounded_context["protocol"],
            task_bus=task_bus,
            artifacts=bounded_context["artifacts"],
            coverage=bounded_context["coverage"],
            artifact_taxonomy=bounded_context["artifact_taxonomy"],
            default_message=str(body.get("message") or ""),
            default_reply=str(body.get("message") or ""),
        )
    )
    if writes_state:
        body["orchestration"]["policy"]["confirmation_gate"] = (
            gate_requires_confirmation
            or body["orchestration"]["policy"].get("confirmation_gate")
        )
    return jsonable(body)
