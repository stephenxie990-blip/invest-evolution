from __future__ import annotations

from typing import Any


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
        snapshot["protocol"] = payload.get("protocol")
    if include_feedback:
        snapshot["feedback"] = build_feedback_snapshot(payload)
    if include_next_action:
        snapshot["next_action"] = build_next_action_snapshot(payload)
    for key in top_level_keys:
        if key in payload:
            snapshot[key] = payload.get(key)
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
