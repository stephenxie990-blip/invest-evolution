"""Readonly runtime query helpers for commander entry methods."""

from __future__ import annotations

from typing import Any

from app.commander_observability import build_runtime_diagnostics
from app.commander_status_support import (
    build_commander_status_snapshot,
    build_events_summary_payload,
    build_events_tail_payload,
    build_training_lab_summary_payload,
)


def get_events_tail_response(
    runtime: Any,
    *,
    limit: int,
    attach_domain_readonly_workflow: Any,
) -> dict[str, Any]:
    return attach_domain_readonly_workflow(
        build_events_tail_payload(runtime.cfg.runtime_events_path, limit=limit),
        domain="runtime",
        operation="get_events_tail",
        runtime_tool="invest_events_tail",
        phase="events_tail_read",
        phase_stats={"limit": int(limit)},
    )


def get_events_summary_response(
    runtime: Any,
    *,
    limit: int,
    ok_status: str,
    attach_domain_readonly_workflow: Any,
) -> dict[str, Any]:
    return attach_domain_readonly_workflow(
        build_events_summary_payload(
            runtime.cfg.runtime_events_path,
            limit=limit,
            ok_status=ok_status,
        ),
        domain="runtime",
        operation="get_events_summary",
        runtime_tool="invest_events_summary",
        phase="events_summary_read",
        phase_stats={"limit": int(limit)},
    )


def get_runtime_diagnostics_response(
    runtime: Any,
    *,
    event_limit: int,
    memory_limit: int,
    attach_domain_readonly_workflow: Any,
) -> dict[str, Any]:
    return attach_domain_readonly_workflow(
        build_runtime_diagnostics(runtime, event_limit=event_limit, memory_limit=memory_limit),
        domain="runtime",
        operation="get_runtime_diagnostics",
        runtime_tool="invest_runtime_diagnostics",
        phase="diagnostics_build",
        phase_stats={"event_limit": int(event_limit), "memory_limit": int(memory_limit)},
    )


def get_training_lab_summary_response(
    runtime: Any,
    *,
    limit: int,
    ok_status: str,
    attach_domain_readonly_workflow: Any,
) -> dict[str, Any]:
    payload = build_training_lab_summary_payload(
        lab_counts=runtime._lab_counts(),
        list_training_plans=runtime.list_training_plans,
        list_training_runs=runtime.list_training_runs,
        list_training_evaluations=runtime.list_training_evaluations,
        limit=limit,
        ok_status=ok_status,
    )
    return attach_domain_readonly_workflow(
        payload,
        domain="training",
        operation="get_training_lab_summary",
        runtime_tool="invest_training_lab_summary",
        phase="lab_summary_read",
        phase_stats={
            "limit": int(limit),
            "plan_count": int(payload.get("plan_count", 0) or 0),
            "run_count": int(payload.get("run_count", 0) or 0),
            "evaluation_count": int(payload.get("evaluation_count", 0) or 0),
        },
    )


def get_status_response(
    runtime: Any,
    *,
    detail: str,
    attach_domain_readonly_workflow: Any,
) -> dict[str, Any]:
    snapshot = build_commander_status_snapshot(
        runtime,
        detail=detail,
        event_limit=20,
        include_recent_training_lab=True,
    )
    detail_mode = str(snapshot["detail_mode"])
    event_rows = list(snapshot["event_rows"])
    return attach_domain_readonly_workflow(
        dict(snapshot["payload"]),
        domain="runtime",
        operation="status",
        runtime_tool="invest_deep_status" if detail_mode == "slow" else "invest_quick_status",
        phase="status_refresh" if detail_mode == "slow" else "status_read",
        phase_stats={"detail_mode": detail_mode, "event_count": len(event_rows)},
    )
