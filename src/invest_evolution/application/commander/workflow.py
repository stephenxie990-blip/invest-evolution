"""Commander workflow orchestration, ask helpers, and mutating actions."""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np

from invest_evolution.agent_runtime.planner import (
    BOUNDED_WORKFLOW_SCHEMA_VERSION,
    COVERAGE_KIND_WORKFLOW_PHASE,
    COVERAGE_SCHEMA_VERSION,
    build_commander_bounded_workflow_plan,
    build_mutating_task_bus,
    build_readonly_task_bus,
)
from invest_evolution.agent_runtime.presentation import (
    build_bounded_entrypoint,
    build_bounded_orchestration,
    build_bounded_policy,
    build_bounded_response_context,
    build_protocol_response,
)
from invest_evolution.application.commander.status import (
    DomainResponseSpec,
    attach_projected_domain_response,
    attach_training_lab_paths,
    build_commander_promotion_summary,
    build_commander_training_evaluation_summary,
    build_training_memory_entry,
)
from invest_evolution.common.utils import safe_read_json_dict
from invest_evolution.market_data import DataSourceUnavailableError

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def _commander_runtime_module() -> Any:
    return import_module("invest_evolution.application.commander.runtime")


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


@dataclass(frozen=True)
class _BoundedWorkflowSpec:
    domain: str
    operation: str
    runtime_method: str
    runtime_tool: str
    agent_kind: str
    writes_state: bool
    available_tools: tuple[str, ...]
    workflow: tuple[str, ...]
    workspace: str
    recommended_plan: tuple[dict[str, Any], ...]
    phase_stats: dict[str, Any] | None = None
    extra_policy: dict[str, Any] | None = None


@dataclass(frozen=True)
class _TrainingExecutionContext:
    plan: dict[str, Any]
    rounds: int
    mock: bool
    plan_id: str
    record_training_lab_artifacts_impl: Any
    attach_training_lab_paths_impl: Any
    append_training_memory_impl: Any
    complete_runtime_task: Any
    idle_state: str
    busy_state: str
    error_state: str
    build_training_memory_entry: Any
    wrap_training_execution_payload: Any | None = None


def _build_bounded_workflow_response(
    payload: Any,
    *,
    spec: _BoundedWorkflowSpec,
) -> dict[str, Any]:
    available_tools = list(spec.available_tools)
    normalized_workflow = list(spec.workflow)
    normalized_phase_stats = jsonable(dict(spec.phase_stats or {}))
    recommended_plan = list(spec.recommended_plan)
    body = (
        dict(payload)
        if isinstance(payload, dict)
        else {"status": "ok", "content": payload}
    )
    body.setdefault(
        "entrypoint",
        build_bounded_entrypoint(
            kind="commander_bounded_workflow",
            domain=spec.domain,
            runtime_method=spec.runtime_method,
            runtime_tool=spec.runtime_tool,
            meeting_path=False,
            agent_kind=spec.agent_kind,
            agent_system="commander_bounded_workflows",
        ),
    )
    orchestration = dict(body.get("orchestration") or {})
    policy = dict(orchestration.get("policy") or {})
    policy.update(
        build_bounded_policy(
            source="commander_runtime",
            domain=spec.domain,
            agent_kind=spec.agent_kind,
            runtime_tool=spec.runtime_tool,
            fixed_boundary=True,
            fixed_workflow=True,
            writes_state=spec.writes_state,
            tool_catalog_scope=f"{spec.domain}_domain",
            extra=jsonable(dict(spec.extra_policy or {})) if spec.extra_policy else None,
        )
    )
    orchestration = build_bounded_orchestration(
        mode=str(
            orchestration.get("mode")
            or (
                "bounded_mutating_workflow"
                if spec.writes_state
                else "bounded_readonly_workflow"
            )
        ),
        available_tools=available_tools,
        allowed_tools=list(orchestration.get("allowed_tools") or available_tools),
        workflow=normalized_workflow,
        phase_stats=normalized_phase_stats,
        policy=policy,
        extra={
            key: value
            for key, value in orchestration.items()
            if key
            not in {
                "mode",
                "available_tools",
                "allowed_tools",
                "workflow",
                "phase_stats",
                "policy",
            }
        },
    )
    artifacts = {
        "workspace": spec.workspace,
        "runtime_tool": spec.runtime_tool,
        "runtime_method": spec.runtime_method,
        "domain": spec.domain,
        "operation": spec.operation,
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
        domain=spec.domain,
        operation=spec.operation,
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
                domain=spec.domain,
                operation=spec.operation,
                runtime_tool=spec.runtime_tool,
                writes_state=spec.writes_state,
                available_tools=available_tools,
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
    if spec.writes_state:
        body["orchestration"]["policy"]["confirmation_gate"] = (
            gate_requires_confirmation
            or body["orchestration"]["policy"].get("confirmation_gate")
        )
    return jsonable(body)


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
    return _build_bounded_workflow_response(
        payload,
        spec=_BoundedWorkflowSpec(
            domain=domain,
            operation=operation,
            runtime_method=runtime_method,
            runtime_tool=runtime_tool,
            agent_kind=agent_kind,
            writes_state=writes_state,
            available_tools=tuple(available_tools),
            workflow=tuple(workflow),
            workspace=str(workspace),
            recommended_plan=tuple(recommended_plan),
            phase_stats=dict(phase_stats or {}),
            extra_policy=dict(extra_policy or {}) if extra_policy else None,
        ),
    )

DomainAgentKindResolver = Callable[[str], str]
DomainToolsResolver = Callable[[str], list[str]]


def runtime_method_label(operation: str, runtime_method: str | None = None) -> str:
    return str(runtime_method or f"CommanderRuntime.{operation}")


def build_domain_workflow(domain: str, phase: str, *extra_phases: str) -> list[str]:
    return [f"{domain}_scope_resolve", phase, *extra_phases, "finalize"]


def _recommended_plan_for_bounded_workflow(
    *,
    domain: str,
    operation: str,
    runtime_tool: str,
    phase_stats: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return build_commander_bounded_workflow_plan(
        domain=domain,
        operation=operation,
        runtime_tool=runtime_tool,
        phase_stats=phase_stats,
        payload=payload,
    )


def _resolve_bounded_workflow_spec(
    *,
    payload: Any,
    domain: str,
    operation: str,
    runtime_method: str | None,
    runtime_tool: str,
    agent_kind: str | None,
    writes_state: bool,
    available_tools: list[str] | None,
    workflow: list[str],
    phase_stats: dict[str, Any] | None,
    extra_policy: dict[str, Any] | None,
    workspace: str,
    domain_agent_kind_resolver: DomainAgentKindResolver,
    domain_tools_resolver: DomainToolsResolver,
) -> _BoundedWorkflowSpec:
    resolved_agent_kind = str(agent_kind or domain_agent_kind_resolver(domain))
    resolved_available_tools = tuple(available_tools or domain_tools_resolver(domain))
    resolved_runtime_method = runtime_method_label(operation, runtime_method)
    recommended_plan = tuple(
        _recommended_plan_for_bounded_workflow(
            domain=domain,
            operation=operation,
            runtime_tool=runtime_tool,
            phase_stats=phase_stats,
            payload=dict(payload) if isinstance(payload, dict) else None,
        )
    )
    return _BoundedWorkflowSpec(
        domain=domain,
        operation=operation,
        runtime_method=resolved_runtime_method,
        runtime_tool=runtime_tool,
        agent_kind=resolved_agent_kind,
        writes_state=writes_state,
        available_tools=resolved_available_tools,
        workflow=tuple(workflow),
        workspace=str(workspace),
        recommended_plan=recommended_plan,
        phase_stats=dict(phase_stats or {}),
        extra_policy=dict(extra_policy or {}) if extra_policy else None,
    )


def attach_bounded_workflow(
    *,
    payload: Any,
    domain: str,
    operation: str,
    runtime_method: str | None = None,
    runtime_tool: str,
    agent_kind: str | None = None,
    writes_state: bool,
    available_tools: list[str] | None = None,
    workflow: list[str],
    phase_stats: dict[str, Any] | None = None,
    extra_policy: dict[str, Any] | None = None,
    workspace: str,
    domain_agent_kind_resolver: DomainAgentKindResolver,
    domain_tools_resolver: DomainToolsResolver,
) -> dict[str, Any]:
    spec = _resolve_bounded_workflow_spec(
        payload=payload,
        domain=domain,
        operation=operation,
        runtime_method=runtime_method,
        runtime_tool=runtime_tool,
        agent_kind=agent_kind,
        writes_state=writes_state,
        available_tools=available_tools,
        workflow=workflow,
        phase_stats=phase_stats,
        extra_policy=extra_policy,
        workspace=workspace,
        domain_agent_kind_resolver=domain_agent_kind_resolver,
        domain_tools_resolver=domain_tools_resolver,
    )
    return _build_bounded_workflow_response(payload, spec=spec)


def _attach_workflow_variant(
    *,
    payload: Any,
    domain: str,
    operation: str,
    runtime_method: str | None = None,
    runtime_tool: str,
    agent_kind: str | None = None,
    available_tools: list[str] | None = None,
    workflow: list[str],
    phase_stats: dict[str, Any] | None = None,
    extra_policy: dict[str, Any] | None = None,
    workspace: str,
    writes_state: bool,
    domain_agent_kind_resolver: DomainAgentKindResolver,
    domain_tools_resolver: DomainToolsResolver,
) -> dict[str, Any]:
    return attach_bounded_workflow(
        payload=payload,
        domain=domain,
        operation=operation,
        runtime_method=runtime_method,
        runtime_tool=runtime_tool,
        agent_kind=agent_kind,
        writes_state=writes_state,
        available_tools=available_tools,
        workflow=workflow,
        phase_stats=phase_stats,
        extra_policy=extra_policy,
        workspace=workspace,
        domain_agent_kind_resolver=domain_agent_kind_resolver,
        domain_tools_resolver=domain_tools_resolver,
    )


def attach_readonly_workflow(
    *,
    payload: Any,
    domain: str,
    operation: str,
    runtime_method: str | None = None,
    runtime_tool: str,
    agent_kind: str | None = None,
    available_tools: list[str] | None = None,
    workflow: list[str],
    phase_stats: dict[str, Any] | None = None,
    extra_policy: dict[str, Any] | None = None,
    workspace: str,
    domain_agent_kind_resolver: DomainAgentKindResolver,
    domain_tools_resolver: DomainToolsResolver,
) -> dict[str, Any]:
    return _attach_workflow_variant(
        payload=payload,
        domain=domain,
        operation=operation,
        runtime_method=runtime_method,
        runtime_tool=runtime_tool,
        agent_kind=agent_kind,
        available_tools=available_tools,
        workflow=workflow,
        phase_stats=phase_stats,
        extra_policy=extra_policy,
        workspace=workspace,
        writes_state=False,
        domain_agent_kind_resolver=domain_agent_kind_resolver,
        domain_tools_resolver=domain_tools_resolver,
    )


def attach_mutating_workflow(
    *,
    payload: Any,
    domain: str,
    operation: str,
    runtime_method: str | None = None,
    runtime_tool: str,
    agent_kind: str | None = None,
    available_tools: list[str] | None = None,
    workflow: list[str],
    phase_stats: dict[str, Any] | None = None,
    extra_policy: dict[str, Any] | None = None,
    workspace: str,
    domain_agent_kind_resolver: DomainAgentKindResolver,
    domain_tools_resolver: DomainToolsResolver,
) -> dict[str, Any]:
    return _attach_workflow_variant(
        payload=payload,
        domain=domain,
        operation=operation,
        runtime_method=runtime_method,
        runtime_tool=runtime_tool,
        agent_kind=agent_kind,
        available_tools=available_tools,
        workflow=workflow,
        phase_stats=phase_stats,
        extra_policy=extra_policy,
        workspace=workspace,
        writes_state=True,
        domain_agent_kind_resolver=domain_agent_kind_resolver,
        domain_tools_resolver=domain_tools_resolver,
    )


def _attach_domain_workflow_variant(
    *,
    payload: Any,
    domain: str,
    operation: str,
    runtime_method: str | None = None,
    runtime_tool: str,
    agent_kind: str | None = None,
    available_tools: list[str] | None = None,
    phase: str,
    extra_phases: tuple[str, ...] = (),
    phase_stats: dict[str, Any] | None = None,
    extra_policy: dict[str, Any] | None = None,
    workspace: str,
    writes_state: bool,
    domain_agent_kind_resolver: DomainAgentKindResolver,
    domain_tools_resolver: DomainToolsResolver,
) -> dict[str, Any]:
    return _attach_workflow_variant(
        payload=payload,
        domain=domain,
        operation=operation,
        runtime_method=runtime_method,
        runtime_tool=runtime_tool,
        agent_kind=agent_kind,
        available_tools=available_tools,
        workflow=build_domain_workflow(domain, phase, *extra_phases),
        phase_stats=phase_stats,
        extra_policy=extra_policy,
        workspace=workspace,
        writes_state=writes_state,
        domain_agent_kind_resolver=domain_agent_kind_resolver,
        domain_tools_resolver=domain_tools_resolver,
    )


def attach_domain_readonly_workflow(
    *,
    payload: Any,
    domain: str,
    operation: str,
    runtime_method: str | None = None,
    runtime_tool: str,
    agent_kind: str | None = None,
    available_tools: list[str] | None = None,
    phase: str,
    extra_phases: tuple[str, ...] = (),
    phase_stats: dict[str, Any] | None = None,
    extra_policy: dict[str, Any] | None = None,
    workspace: str,
    domain_agent_kind_resolver: DomainAgentKindResolver,
    domain_tools_resolver: DomainToolsResolver,
) -> dict[str, Any]:
    return _attach_domain_workflow_variant(
        payload=payload,
        domain=domain,
        operation=operation,
        runtime_method=runtime_method,
        runtime_tool=runtime_tool,
        agent_kind=agent_kind,
        available_tools=available_tools,
        phase=phase,
        extra_phases=extra_phases,
        phase_stats=phase_stats,
        extra_policy=extra_policy,
        workspace=workspace,
        writes_state=False,
        domain_agent_kind_resolver=domain_agent_kind_resolver,
        domain_tools_resolver=domain_tools_resolver,
    )


def attach_domain_mutating_workflow(
    *,
    payload: Any,
    domain: str,
    operation: str,
    runtime_method: str | None = None,
    runtime_tool: str,
    agent_kind: str | None = None,
    available_tools: list[str] | None = None,
    phase: str,
    extra_phases: tuple[str, ...] = (),
    phase_stats: dict[str, Any] | None = None,
    extra_policy: dict[str, Any] | None = None,
    workspace: str,
    domain_agent_kind_resolver: DomainAgentKindResolver,
    domain_tools_resolver: DomainToolsResolver,
) -> dict[str, Any]:
    return _attach_domain_workflow_variant(
        payload=payload,
        domain=domain,
        operation=operation,
        runtime_method=runtime_method,
        runtime_tool=runtime_tool,
        agent_kind=agent_kind,
        available_tools=available_tools,
        phase=phase,
        extra_phases=extra_phases,
        phase_stats=phase_stats,
        extra_policy=extra_policy,
        workspace=workspace,
        writes_state=True,
        domain_agent_kind_resolver=domain_agent_kind_resolver,
        domain_tools_resolver=domain_tools_resolver,
    )


def load_leaderboard_snapshot(training_output_dir: str | Path) -> dict[str, Any]:
    path = Path(training_output_dir).parent / "leaderboard.json"
    if not path.exists():
        return {}
    try:
        return safe_read_json_dict(path)
    except (OSError, UnicodeDecodeError, ValueError):
        return {}


def record_training_lab_artifacts(
    *,
    training_lab: Any,
    build_training_evaluation_summary: Any,
    new_run_id: Any,
    plan: dict[str, Any],
    payload: dict[str, Any],
    status: str,
    error: str = "",
) -> dict[str, Any]:
    run_id = new_run_id()
    eval_payload = build_training_evaluation_summary(payload, plan=plan, run_id=run_id, error=error)
    return training_lab.record_training_lab_artifacts(
        payload=payload,
        plan=plan,
        status=status,
        eval_payload=eval_payload,
        run_id=run_id,
        error=error,
    )


def append_training_memory(
    memory: Any,
    payload: dict[str, Any],
    *,
    rounds: int,
    mock: bool,
    status: str,
    error: str = "",
    build_training_memory_entry: Any,
) -> None:
    entry = build_training_memory_entry(
        payload,
        rounds=rounds,
        mock=mock,
        status=status,
        error=error,
    )
    memory.append(
        kind="training_run",
        session_key="runtime:train",
        content=str(entry.get("content") or ""),
        metadata=dict(entry.get("metadata") or {}),
    )


def _build_training_execution_context(
    *,
    plan: dict[str, Any],
    rounds: int,
    mock: bool,
    plan_id: str,
    record_training_lab_artifacts_impl: Any,
    attach_training_lab_paths_impl: Any,
    append_training_memory_impl: Any,
    complete_runtime_task: Any,
    idle_state: str,
    busy_state: str,
    error_state: str,
    build_training_memory_entry: Any,
    wrap_training_execution_payload: Any | None = None,
) -> _TrainingExecutionContext:
    return _TrainingExecutionContext(
        plan=plan,
        rounds=rounds,
        mock=mock,
        plan_id=plan_id,
        record_training_lab_artifacts_impl=record_training_lab_artifacts_impl,
        attach_training_lab_paths_impl=attach_training_lab_paths_impl,
        append_training_memory_impl=append_training_memory_impl,
        complete_runtime_task=complete_runtime_task,
        idle_state=idle_state,
        busy_state=busy_state,
        error_state=error_state,
        build_training_memory_entry=build_training_memory_entry,
        wrap_training_execution_payload=wrap_training_execution_payload,
    )


def _resolve_training_completion_state(
    *,
    status: str,
    context: _TrainingExecutionContext,
) -> str:
    if status == context.error_state:
        return context.error_state
    if status == context.busy_state:
        return context.busy_state
    return context.idle_state


def _record_training_execution_side_effects(
    *,
    context: _TrainingExecutionContext,
    payload: dict[str, Any],
    status: str,
    error: str = "",
) -> None:
    lab = context.record_training_lab_artifacts_impl(
        plan=context.plan,
        payload=payload,
        status=status,
        error=error,
    )
    context.attach_training_lab_paths_impl(payload, lab)
    context.append_training_memory_impl(
        payload,
        rounds=context.rounds,
        mock=context.mock,
        status=status,
        error=error,
        build_training_memory_entry=context.build_training_memory_entry,
    )


def _finalize_training_execution_from_context(
    *,
    context: _TrainingExecutionContext,
    payload: dict[str, Any],
    status: str,
    error: str = "",
    wrap_payload: bool = True,
) -> dict[str, Any]:
    _record_training_execution_side_effects(
        context=context,
        payload=payload,
        status=status,
        error=error,
    )
    context.complete_runtime_task(
        state=_resolve_training_completion_state(status=status, context=context),
        status=status,
        rounds=context.rounds,
        mock=context.mock,
        plan_id=context.plan_id,
    )
    if not wrap_payload or context.wrap_training_execution_payload is None:
        return payload
    return context.wrap_training_execution_payload(
        payload,
        plan_id=str(context.plan_id),
        rounds=context.rounds,
        mock=context.mock,
    )


def mark_training_plan_running(
    *,
    plan: dict[str, Any],
    plan_path: Path,
    write_json_artifact: Any,
    begin_task: Any,
    set_runtime_state: Any,
    memory: Any,
    rounds: int,
    mock: bool,
    plan_id: str,
    training_state: str,
) -> None:
    plan["status"] = "running"
    plan["started_at"] = datetime.now().isoformat()
    write_json_artifact(plan_path, plan)
    begin_task(
        "train_plan",
        str(plan.get("source", "manual")),
        rounds=rounds,
        mock=mock,
        plan_id=plan_id,
    )
    set_runtime_state(training_state)
    memory.append_audit(
        "train_requested",
        "runtime:train",
        {"rounds": rounds, "mock": mock, "plan_id": plan_id},
    )


def finalize_training_execution(
    *,
    plan: dict[str, Any],
    payload: dict[str, Any],
    status: str,
    rounds: int,
    mock: bool,
    plan_id: str,
    record_training_lab_artifacts_impl: Any,
    attach_training_lab_paths_impl: Any,
    append_training_memory_impl: Any,
    complete_runtime_task: Any,
    idle_state: str,
    busy_state: str,
    error_state: str,
    build_training_memory_entry: Any,
    error: str = "",
    wrap_training_execution_payload: Any | None = None,
) -> dict[str, Any]:
    context = _build_training_execution_context(
        plan=plan,
        rounds=rounds,
        mock=mock,
        plan_id=plan_id,
        record_training_lab_artifacts_impl=record_training_lab_artifacts_impl,
        attach_training_lab_paths_impl=attach_training_lab_paths_impl,
        append_training_memory_impl=append_training_memory_impl,
        complete_runtime_task=complete_runtime_task,
        idle_state=idle_state,
        busy_state=busy_state,
        error_state=error_state,
        build_training_memory_entry=build_training_memory_entry,
        wrap_training_execution_payload=wrap_training_execution_payload,
    )
    return _finalize_training_execution_from_context(
        context=context,
        payload=payload,
        status=status,
        error=error,
        wrap_payload=wrap_training_execution_payload is not None,
    )


async def execute_training_plan_flow(
    *,
    plan_path: Path,
    plan: dict[str, Any],
    experiment_spec: dict[str, Any],
    rounds: int,
    mock: bool,
    plan_id: str,
    body: Any,
    body_snapshot: Any,
    build_run_cycles_kwargs: Any,
    write_json_artifact: Any,
    begin_task: Any,
    set_runtime_state: Any,
    memory: Any,
    record_training_lab_artifacts_impl: Any,
    attach_training_lab_paths_impl: Any,
    append_training_memory_impl: Any,
    complete_runtime_task: Any,
    wrap_training_execution_payload: Any,
    build_training_memory_entry: Any,
    ok_status: str,
    busy_state: str,
    idle_state: str,
    training_state: str,
    error_state: str,
) -> dict[str, Any]:
    context = _build_training_execution_context(
        plan=plan,
        rounds=rounds,
        mock=mock,
        plan_id=plan_id,
        record_training_lab_artifacts_impl=record_training_lab_artifacts_impl,
        attach_training_lab_paths_impl=attach_training_lab_paths_impl,
        append_training_memory_impl=append_training_memory_impl,
        complete_runtime_task=complete_runtime_task,
        idle_state=idle_state,
        busy_state=busy_state,
        error_state=error_state,
        build_training_memory_entry=build_training_memory_entry,
        wrap_training_execution_payload=wrap_training_execution_payload,
    )
    mark_training_plan_running(
        plan=plan,
        plan_path=plan_path,
        write_json_artifact=write_json_artifact,
        begin_task=begin_task,
        set_runtime_state=set_runtime_state,
        memory=memory,
        rounds=rounds,
        mock=mock,
        plan_id=plan_id,
        training_state=training_state,
    )
    try:
        run_cycles_kwargs = build_run_cycles_kwargs(
            plan=plan,
            rounds=rounds,
            mock=mock,
            experiment_spec=experiment_spec,
        )
        payload = await body.run_cycles(**run_cycles_kwargs)
        if data_error := body._extract_data_source_error(payload):
            raise DataSourceUnavailableError.from_payload(data_error)
        status = str(payload.get("status", ok_status))
        return _finalize_training_execution_from_context(
            context=context,
            payload=payload,
            status=status,
        )
    except Exception as exc:
        logger.exception(
            "Training plan execution failed: plan_id=%s rounds=%s mock=%s",
            plan_id,
            rounds,
            mock,
        )
        error_payload = {"results": [], "summary": body_snapshot()}
        _finalize_training_execution_from_context(
            context=context,
            payload=error_payload,
            status=error_state,
            error=str(exc),
            wrap_payload=False,
        )
        raise


def load_training_plan_artifact(plan_path: Path, *, plan_id: str) -> tuple[Path, dict[str, Any]]:
    if not plan_path.exists():
        raise FileNotFoundError(f"training plan not found: {plan_id}")
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid training plan json: {plan_id}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"training plan must decode to an object: {plan_id}")
    return plan_path, payload


def build_experiment_spec_from_plan(plan: dict[str, Any]) -> tuple[dict[str, Any], int, bool]:
    spec = dict(plan.get("spec") or {})
    rounds = int(spec.get("rounds", 1) or 1)
    mock = bool(spec.get("mock", False))
    experiment_spec = {
        "spec": spec,
        "protocol": dict(plan.get("protocol") or {}),
        "dataset": dict(plan.get("dataset") or {}),
        "manager_scope": dict(plan.get("manager_scope") or {}),
        "optimization": dict(plan.get("optimization") or {}),
        "llm": dict(plan.get("llm") or {}),
    }
    return experiment_spec, rounds, mock


def build_run_cycles_kwargs(
    run_cycles_callable: Any,
    *,
    plan: dict[str, Any],
    rounds: int,
    mock: bool,
    experiment_spec: dict[str, Any],
    request_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_cycles_kwargs = {
        "rounds": rounds,
        "force_mock": mock,
        "task_source": str(plan.get("source", "manual")),
    }
    try:
        run_cycles_signature = inspect.signature(run_cycles_callable)
        if "experiment_spec" in run_cycles_signature.parameters:
            run_cycles_kwargs["experiment_spec"] = experiment_spec
        for key in ("session_key", "chat_id", "request_id", "channel"):
            if key in run_cycles_signature.parameters:
                run_cycles_kwargs[key] = str((request_context or {}).get(key) or "")
    except (TypeError, ValueError):
        run_cycles_kwargs["experiment_spec"] = experiment_spec
        for key in ("session_key", "chat_id", "request_id", "channel"):
            run_cycles_kwargs[key] = str((request_context or {}).get(key) or "")
    return run_cycles_kwargs


def parse_ask_response_payload(response: Any) -> dict[str, Any]:
    try:
        payload = json.loads(response) if isinstance(response, str) else dict(response or {})
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def normalize_ask_reply_payload(
    response: Any,
    *,
    session_key: str = "",
    chat_id: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    payload = parse_ask_response_payload(response)
    if not payload:
        payload = {"reply": str(response or "")}
    payload.setdefault("reply", str(payload.get("message") or response or ""))
    payload.setdefault("message", str(payload.get("reply") or ""))
    if session_key:
        payload.setdefault("session_key", session_key)
    if chat_id:
        payload.setdefault("chat_id", chat_id)
    if request_id:
        payload.setdefault("request_id", request_id)
    return payload


def extract_ask_result_metadata(response: Any) -> dict[str, Any]:
    payload = parse_ask_response_payload(response)
    if not payload:
        return {}

    protocol = dict(payload.get("protocol") or {})
    entrypoint = dict(payload.get("entrypoint") or {})
    next_action = dict(payload.get("next_action") or {})
    task_bus = dict(payload.get("task_bus") or {})
    gate = dict(task_bus.get("gate") or {})
    confirmation = dict(gate.get("confirmation") or {})
    metadata: dict[str, Any] = {}

    status = str(payload.get("status") or "").strip()
    if status:
        metadata["status"] = status
    if protocol.get("domain"):
        metadata["domain"] = str(protocol["domain"])
    if protocol.get("operation"):
        metadata["operation"] = str(protocol["operation"])
    if protocol.get("schema_version"):
        metadata["protocol_schema_version"] = str(protocol["schema_version"])
    if entrypoint.get("kind"):
        metadata["entrypoint_kind"] = str(entrypoint["kind"])
    if entrypoint.get("intent"):
        metadata["intent"] = str(entrypoint["intent"])
    if next_action.get("kind"):
        metadata["next_action_kind"] = str(next_action["kind"])
    if gate.get("risk_level"):
        metadata["risk_level"] = str(gate["risk_level"])
    if "requires_confirmation" in gate:
        metadata["requires_confirmation"] = bool(gate.get("requires_confirmation"))
    if confirmation.get("state"):
        metadata["confirmation_state"] = str(confirmation["state"])
    return metadata


def append_session_message(
    memory: Any,
    *,
    kind: str,
    session_key: str,
    content: str,
    channel: str,
    chat_id: str,
    request_id: str = "",
) -> None:
    memory.append(
        kind=kind,
        session_key=session_key,
        content=content,
        metadata={
            "channel": channel,
            "chat_id": chat_id,
            **({"request_id": request_id} if str(request_id or "").strip() else {}),
        },
    )


def record_runtime_ask_activity(
    *,
    memory: Any,
    append_runtime_event: Callable[[str, dict[str, Any]], Any],
    event: str,
    session_key: str,
    channel: str,
    chat_id: str,
    request_id: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    payload = {
        "session_key": session_key,
        "channel": channel,
        "chat_id": chat_id,
        **({"request_id": request_id} if str(request_id or "").strip() else {}),
        **dict(extra or {}),
    }
    memory.append_audit(event, session_key, payload)
    append_runtime_event(event, payload)


async def execute_runtime_ask(
    *,
    message: str,
    session_key: str,
    channel: str,
    chat_id: str,
    request_id: str,
    ensure_runtime_storage: Callable[[], None],
    begin_task: Callable[..., None],
    memory: Any,
    record_ask_activity: Callable[..., None],
    process_direct: Callable[..., Awaitable[str]],
    complete_runtime_task: Callable[..., None],
    status_ok: str,
    status_error: str,
    event_ask_started: str,
    event_ask_finished: str,
) -> str:
    ensure_runtime_storage()
    begin_task("ask", channel, session_key=session_key, chat_id=chat_id)
    append_session_message(
        memory,
        kind="user",
        session_key=session_key,
        content=message,
        channel=channel,
        chat_id=chat_id,
        request_id=request_id,
    )
    record_ask_activity(
        event_ask_started,
        session_key=session_key,
        channel=channel,
        chat_id=chat_id,
        request_id=request_id,
        extra={"message_length": len(message)},
    )
    try:
        response = await process_direct(message, session_key=session_key)
        append_session_message(
            memory,
            kind="assistant",
            session_key=session_key,
            content=response or "",
            channel=channel,
            chat_id=chat_id,
            request_id=request_id,
        )
        ask_result = extract_ask_result_metadata(response)
        record_ask_activity(
            event_ask_finished,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            request_id=request_id,
            extra={"message_length": len(message), **ask_result},
        )
        complete_runtime_task(status=status_ok)
        return response
    except Exception:
        logger.exception(
            "Runtime ask failed: session_key=%s channel=%s chat_id=%s request_id=%s message_length=%s",
            session_key,
            channel,
            chat_id,
            request_id,
            len(message),
        )
        complete_runtime_task(status=status_error)
        raise


def reload_playbooks_response(
    runtime: Any,
    *,
    ensure_runtime_storage: Callable[[], None],
    begin_task: Callable[..., None],
    set_runtime_state: Callable[[str], None],
    write_commander_identity: Callable[[], None],
    complete_runtime_task: Callable[..., None],
    attach_domain_mutating_workflow: Callable[..., dict[str, Any]],
    reloading_state: str,
    idle_state: str,
    ok_status: str,
) -> dict[str, Any]:
    ensure_runtime_storage()
    begin_task("reload_playbooks", "direct")
    set_runtime_state(reloading_state)
    runtime.playbook_registry.ensure_default_playbooks()
    playbooks = runtime.playbook_registry.reload()
    write_commander_identity()
    complete_runtime_task(state=idle_state, status=ok_status, playbook_count=len(playbooks))
    return attach_projected_domain_response(
        {
            "status": ok_status,
            "count": len(playbooks),
            "items": [playbook.to_dict() for playbook in playbooks],
        },
        spec=DomainResponseSpec(
            domain="playbook",
            operation="reload_playbooks",
            runtime_tool="invest_reload_playbooks",
            phase="playbook_reload",
            phase_stats={"playbook_count": len(playbooks)},
        ),
        attach_workflow=attach_domain_mutating_workflow,
    )


def add_cron_job_response(
    runtime: Any,
    *,
    name: str,
    message: str,
    every_sec: int,
    deliver: bool,
    channel: str,
    to: str,
    persist_state: Callable[[], None],
    attach_domain_mutating_workflow: Callable[..., dict[str, Any]],
    ok_status: str,
) -> dict[str, Any]:
    job = runtime.cron.add_job(
        name=name,
        message=message,
        every_sec=int(every_sec),
        deliver=bool(deliver),
        channel=str(channel),
        to=str(to),
    )
    persist_state()
    return attach_projected_domain_response(
        {"status": ok_status, "job": job.to_dict()},
        spec=DomainResponseSpec(
            domain="scheduler",
            operation="add_cron_job",
            runtime_tool="invest_cron_add",
            phase="cron_add",
            phase_stats={
                "job_id": getattr(job, "id", ""),
                "every_sec": int(every_sec),
            },
        ),
        attach_workflow=attach_domain_mutating_workflow,
    )


def list_cron_jobs_response(
    runtime: Any,
    *,
    attach_domain_readonly_workflow: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    rows = [job.to_dict() for job in runtime.cron.list_jobs()]
    return attach_projected_domain_response(
        {"count": len(rows), "items": rows},
        spec=DomainResponseSpec(
            domain="scheduler",
            operation="list_cron_jobs",
            runtime_tool="invest_cron_list",
            phase="cron_list",
            phase_stats={"count": len(rows)},
        ),
        attach_workflow=attach_domain_readonly_workflow,
    )


def remove_cron_job_response(
    runtime: Any,
    *,
    job_id: str,
    persist_state: Callable[[], None],
    attach_domain_mutating_workflow: Callable[..., dict[str, Any]],
    ok_status: str,
    not_found_status: str,
) -> dict[str, Any]:
    removed = runtime.cron.remove_job(str(job_id))
    persist_state()
    return attach_projected_domain_response(
        {"status": ok_status if removed else not_found_status, "job_id": str(job_id)},
        spec=DomainResponseSpec(
            domain="scheduler",
            operation="remove_cron_job",
            runtime_tool="invest_cron_remove",
            phase="cron_remove",
            phase_stats={"job_id": str(job_id), "removed": bool(removed)},
        ),
        attach_workflow=attach_domain_mutating_workflow,
    )


def reload_plugins_response(
    runtime: Any,
    *,
    ensure_runtime_storage: Callable[[], None],
    load_plugins: Callable[..., dict[str, Any]],
    attach_domain_mutating_workflow: Callable[..., dict[str, Any]],
    ok_status: str,
) -> dict[str, Any]:
    ensure_runtime_storage()
    payload = load_plugins(persist=True)
    return attach_projected_domain_response(
        {"status": ok_status, **payload},
        spec=DomainResponseSpec(
            domain="plugin",
            operation="reload_plugins",
            runtime_tool="invest_plugins_reload",
            phase="plugin_reload",
            phase_stats={"plugin_count": int(payload.get("count", 0) or 0)},
        ),
        attach_workflow=attach_domain_mutating_workflow,
    )


def record_ask_activity(runtime: Any, event: str, **kwargs: Any) -> None:
    record_runtime_ask_activity(
        memory=runtime.memory,
        append_runtime_event=lambda event_name, payload: runtime._append_runtime_event(
            event_name,
            payload,
            source="brain",
        ),
        event=event,
        **kwargs,
    )


async def start_runtime(runtime: Any) -> None:
    await _commander_runtime_module().start_runtime_flow(
        is_started=runtime._started,
        logger=logger,
        ensure_runtime_storage=runtime._ensure_runtime_storage,
        begin_task=runtime._begin_task,
        set_runtime_state=runtime._set_runtime_state,
        acquire_runtime_lock=runtime._acquire_runtime_lock,
        ensure_default_playbooks=runtime.playbook_registry.ensure_default_playbooks,
        reload_playbooks=lambda: runtime.playbook_registry.reload(),
        load_plugins=runtime._load_plugins,
        write_commander_identity=runtime._write_commander_identity,
        start_background_services=lambda: _commander_runtime_module().start_runtime_background_services(
            cron=runtime.cron,
            heartbeat=runtime.heartbeat,
            bridge=runtime.bridge,
            heartbeat_enabled=runtime.cfg.heartbeat_enabled,
            bridge_enabled=runtime.cfg.bridge_enabled,
            drain_notifications=runtime._drain_notifications,
            autopilot_enabled=runtime.cfg.autopilot_enabled,
            autopilot_loop=runtime.body.autopilot_loop,
            training_interval_sec=runtime.cfg.training_interval_sec,
        ),
        mark_started=runtime._set_started_flag,
        set_background_tasks=runtime._set_background_tasks,
        complete_runtime_task=runtime._complete_runtime_task,
        end_task=runtime._end_task,
        release_runtime_lock=runtime._release_runtime_lock,
        persist_state=runtime._persist_state,
        starting_state="starting",
        idle_state="idle",
        error_state="error",
        ok_status="ok",
    )


async def stop_runtime(runtime: Any) -> None:
    await _commander_runtime_module().stop_runtime_flow(
        is_started=runtime._started,
        begin_task=runtime._begin_task,
        set_runtime_state=runtime._set_runtime_state,
        stop_background_services=lambda: _commander_runtime_module().stop_runtime_background_services(
            body=runtime.body,
            autopilot_task=runtime._autopilot_task,
            notify_task=runtime._notify_task,
            bridge=runtime.bridge,
            heartbeat=runtime.heartbeat,
            cron=runtime.cron,
            brain=runtime.brain,
        ),
        mark_started=runtime._set_started_flag,
        release_runtime_lock=runtime._release_runtime_lock,
        complete_runtime_task=runtime._complete_runtime_task,
        stopping_state="stopping",
        stopped_state="stopped",
        ok_status="ok",
    )


async def ask_runtime(
    runtime: Any,
    message: str,
    *,
    session_key: str = "commander:direct",
    channel: str = "cli",
    chat_id: str = "direct",
    request_id: str = "",
) -> str:
    resolved_request_id = str(request_id or runtime.new_request_id()).strip()
    with runtime._request_event_context(
        session_key=session_key,
        chat_id=chat_id,
        request_id=resolved_request_id,
        channel=channel,
    ):
        return await execute_runtime_ask(
            message=message,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            request_id=resolved_request_id,
            ensure_runtime_storage=runtime._ensure_runtime_storage,
            begin_task=runtime._begin_task,
            memory=runtime.memory,
            record_ask_activity=runtime._record_ask_activity,
            process_direct=runtime.brain.process_direct,
            complete_runtime_task=runtime._complete_runtime_task,
            status_ok="ok",
            status_error="error",
            event_ask_started="ask_started",
            event_ask_finished="ask_finished",
        )


def create_training_plan(runtime: Any, **kwargs: Any) -> dict[str, Any]:
    runtime._ensure_runtime_storage()
    plan = runtime._create_training_plan_payload(**kwargs)
    runtime._write_json_artifact(runtime._training_plan_path(plan["plan_id"]), plan)
    runtime._persist_state()
    return plan


def load_training_plan_artifact_for_runtime(runtime: Any, plan_id: str) -> tuple[Path, dict[str, Any]]:
    return load_training_plan_artifact(
        runtime._training_plan_path(str(plan_id)),
        plan_id=str(plan_id),
    )


def load_leaderboard_snapshot_for_runtime(runtime: Any) -> dict[str, Any]:
    return load_leaderboard_snapshot(runtime.cfg.training_output_dir)


def build_promotion_summary_for_runtime(
    runtime: Any,
    *,
    plan: dict[str, Any],
    ok_results: list[dict[str, Any]],
    avg_return_pct: float | None,
    avg_strategy_score: float | None,
    benchmark_pass_rate: float,
) -> dict[str, Any]:
    board = load_leaderboard_snapshot_for_runtime(runtime)
    return build_commander_promotion_summary(
        plan=plan,
        ok_results=ok_results,
        avg_return_pct=avg_return_pct,
        avg_strategy_score=avg_strategy_score,
        benchmark_pass_rate=benchmark_pass_rate,
        leaderboard_entries=list(board.get("entries") or []),
    )


def build_training_evaluation_summary_for_runtime(
    runtime: Any,
    payload: dict[str, Any],
    *,
    plan: dict[str, Any],
    run_id: str,
    error: str = "",
) -> dict[str, Any]:
    board = load_leaderboard_snapshot_for_runtime(runtime)
    return build_commander_training_evaluation_summary(
        payload,
        plan=plan,
        run_id=run_id,
        error=error,
        run_path=str(runtime._training_run_path(run_id)),
        evaluation_path=str(runtime._training_eval_path(run_id)),
        leaderboard_entries=list(board.get("entries") or []),
    )


def record_training_lab_artifacts_for_runtime(
    runtime: Any,
    *,
    plan: dict[str, Any],
    payload: dict[str, Any],
    status: str,
    error: str = "",
) -> dict[str, Any]:
    return record_training_lab_artifacts(
        training_lab=runtime.training_lab,
        build_training_evaluation_summary=lambda body, *, plan, run_id, error="": (
            build_training_evaluation_summary_for_runtime(
                runtime,
                body,
                plan=plan,
                run_id=run_id,
                error=error,
            )
        ),
        new_run_id=runtime._new_training_run_id,
        plan=plan,
        payload=payload,
        status=status,
        error=error,
    )


def append_training_memory_for_runtime(
    runtime: Any,
    payload: dict[str, Any],
    *,
    rounds: int,
    mock: bool,
    status: str,
    error: str = "",
    build_training_memory_entry_impl: Any = build_training_memory_entry,
) -> None:
    append_training_memory(
        runtime.memory,
        payload,
        rounds=rounds,
        mock=mock,
        status=status,
        error=error,
        build_training_memory_entry=build_training_memory_entry_impl,
    )


def build_run_cycles_kwargs_for_runtime(
    runtime: Any,
    *,
    plan: dict[str, Any],
    rounds: int,
    mock: bool,
    experiment_spec: dict[str, Any],
) -> dict[str, Any]:
    return build_run_cycles_kwargs(
        runtime.body.run_cycles,
        plan=plan,
        rounds=rounds,
        mock=mock,
        experiment_spec=experiment_spec,
        request_context=runtime._current_request_event_context(),
    )


def attach_training_lab_paths_for_runtime(payload: dict[str, Any], lab: dict[str, Any]) -> None:
    attach_training_lab_paths(payload, lab)


def _build_training_execution_phase_stats(
    payload: dict[str, Any],
    *,
    plan_id: str,
    rounds: int,
    mock: bool,
) -> dict[str, Any]:
    result_count = len(list(payload.get("results") or []))
    total_cycles = dict(payload.get("summary") or {}).get("total_cycles")
    return {
        "plan_id": str(plan_id),
        "rounds": int(rounds),
        "mock": bool(mock),
        "result_count": int(result_count),
        "total_cycles": total_cycles,
    }


def _build_training_execution_response_spec(
    payload: dict[str, Any],
    *,
    plan_id: str,
    rounds: int,
    mock: bool,
) -> DomainResponseSpec:
    return DomainResponseSpec(
        domain="training",
        operation="execute_training_plan",
        runtime_tool="invest_training_plan_execute",
        phase="training_plan_load",
        extra_phases=("training_cycles_execute", "training_artifacts_record"),
        phase_stats=_build_training_execution_phase_stats(
            payload,
            plan_id=plan_id,
            rounds=rounds,
            mock=mock,
        ),
    )


def wrap_training_execution_payload_for_runtime(
    runtime: Any,
    payload: dict[str, Any],
    *,
    plan_id: str,
    rounds: int,
    mock: bool,
) -> dict[str, Any]:
    return attach_projected_domain_response(
        payload,
        spec=_build_training_execution_response_spec(
            payload,
            plan_id=plan_id,
            rounds=rounds,
            mock=mock,
        ),
        attach_workflow=runtime._attach_domain_mutating_workflow,
    )


async def train_once(runtime: Any, **kwargs: Any) -> dict[str, Any]:
    rounds = int(kwargs.pop("rounds", 1))
    mock = bool(kwargs.pop("mock", False))
    plan = create_training_plan(
        runtime,
        rounds=rounds,
        mock=mock,
        goal="direct training run",
        notes="auto-generated from invest_train",
        tags=["direct", "auto"],
        source="direct",
        auto_generated=True,
    )
    return await execute_training_plan(runtime, plan["plan_id"], rounds=rounds, mock=mock, **kwargs)


async def execute_training_plan(
    runtime: Any,
    plan_id: str,
    *,
    session_key: str = "",
    chat_id: str = "",
    request_id: str = "",
    channel: str = "",
    rounds: int | None = None,
    mock: bool | None = None,
) -> dict[str, Any]:
    runtime._ensure_runtime_storage()
    plan_path, plan = runtime._load_training_plan_artifact(str(plan_id))
    experiment_spec, resolved_rounds, resolved_mock = runtime._build_experiment_spec_from_plan(plan)
    if rounds is not None:
        resolved_rounds = int(rounds)
    if mock is not None:
        resolved_mock = bool(mock)
    resolved_request_id = str(
        request_id
        or runtime._current_request_event_context().get("request_id")
        or runtime.new_request_id()
    ).strip()
    resolved_session_key = str(
        session_key
        or runtime._current_request_event_context().get("session_key")
        or f"train:{plan_id}"
    ).strip()
    resolved_chat_id = str(
        chat_id
        or runtime._current_request_event_context().get("chat_id")
        or str(plan_id)
    ).strip()
    resolved_channel = str(
        channel
        or runtime._current_request_event_context().get("channel")
        or "runtime"
    ).strip()
    with runtime._request_event_context(
        session_key=resolved_session_key,
        chat_id=resolved_chat_id,
        request_id=resolved_request_id,
        channel=resolved_channel,
    ):
        return await execute_training_plan_flow(
            plan_path=plan_path,
            plan=plan,
            experiment_spec=experiment_spec,
            rounds=resolved_rounds,
            mock=resolved_mock,
            plan_id=str(plan_id),
            body=runtime.body,
            body_snapshot=runtime.body.snapshot,
            build_run_cycles_kwargs=runtime._build_run_cycles_kwargs,
            write_json_artifact=runtime._write_json_artifact,
            begin_task=runtime._begin_task,
            set_runtime_state=runtime._set_runtime_state,
            memory=runtime.memory,
            record_training_lab_artifacts_impl=runtime._record_training_lab_artifacts,
            attach_training_lab_paths_impl=runtime._attach_training_lab_paths,
            append_training_memory_impl=runtime._append_training_memory,
            complete_runtime_task=runtime._complete_runtime_task,
            wrap_training_execution_payload=runtime._wrap_training_execution_payload,
            build_training_memory_entry=build_training_memory_entry,
            ok_status="ok",
            busy_state="busy",
            idle_state="idle",
            training_state="training",
            error_state="error",
        )


def reload_playbooks(runtime: Any) -> dict[str, Any]:
    return reload_playbooks_response(
        runtime,
        ensure_runtime_storage=runtime._ensure_runtime_storage,
        begin_task=runtime._begin_task,
        set_runtime_state=runtime._set_runtime_state,
        write_commander_identity=runtime._write_commander_identity,
        complete_runtime_task=runtime._complete_runtime_task,
        attach_domain_mutating_workflow=runtime._attach_domain_mutating_workflow,
        reloading_state="reloading_playbooks",
        idle_state="idle",
        ok_status="ok",
    )


def add_cron_job(runtime: Any, **kwargs: Any) -> dict[str, Any]:
    return add_cron_job_response(
        runtime,
        persist_state=runtime._persist_state,
        attach_domain_mutating_workflow=runtime._attach_domain_mutating_workflow,
        ok_status="ok",
        **kwargs,
    )


def list_cron_jobs(runtime: Any) -> dict[str, Any]:
    return list_cron_jobs_response(
        runtime,
        attach_domain_readonly_workflow=runtime._attach_domain_readonly_workflow,
    )


def remove_cron_job(runtime: Any, job_id: str) -> dict[str, Any]:
    return remove_cron_job_response(
        runtime,
        job_id=job_id,
        persist_state=runtime._persist_state,
        attach_domain_mutating_workflow=runtime._attach_domain_mutating_workflow,
        ok_status="ok",
        not_found_status="not_found",
    )


def reload_plugins(runtime: Any) -> dict[str, Any]:
    return reload_plugins_response(
        runtime,
        ensure_runtime_storage=runtime._ensure_runtime_storage,
        load_plugins=runtime._load_plugins,
        attach_domain_mutating_workflow=runtime._attach_domain_mutating_workflow,
        ok_status="ok",
    )


async def serve_forever(runtime: Any, interactive: bool = False) -> None:
    await _commander_runtime_module().serve_forever_loop(
        start_runtime=runtime.start,
        ask_runtime=runtime.ask,
        interactive=interactive,
    )
