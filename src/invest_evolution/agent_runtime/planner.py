"""Agent runtime planner catalog and bounded workflow plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _normalized_int(value: Any, default: int) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return int(default)


def _planner_args(
    *,
    phase_stats: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    keys: list[str],
) -> dict[str, Any]:
    source = dict(phase_stats or {})
    if isinstance(payload, dict):
        source.update({key: value for key, value in payload.items() if key not in source})
    return {
        key: source[key]
        for key in keys
        if key in source and source[key] is not None and source[key] != ""
    }


def build_runtime_status_plan(
    *,
    primary_tool: str,
    detail_mode: str = "fast",
    summary_limit: int = 100,
    event_limit: int = 50,
    memory_limit: int = 20,
) -> list[dict[str, Any]]:
    return [
        {"tool": primary_tool, "args": {"detail": detail_mode}} if primary_tool == "invest_quick_status" else {"tool": primary_tool, "args": {}},
        {"tool": "invest_events_summary", "args": {"limit": _normalized_int(summary_limit, 100)}},
        {
            "tool": "invest_runtime_diagnostics",
            "args": {
                "event_limit": _normalized_int(event_limit, 50),
                "memory_limit": _normalized_int(memory_limit, 20),
            },
        },
    ]


def build_runtime_events_tail_plan(*, limit: int = 50, summary_limit: int = 100) -> list[dict[str, Any]]:
    return [
        {"tool": "invest_events_tail", "args": {"limit": _normalized_int(limit, 50)}},
        {"tool": "invest_events_summary", "args": {"limit": _normalized_int(summary_limit, 100)}},
    ]


def build_runtime_events_summary_plan(
    *,
    summary_limit: int = 100,
    event_limit: int = 50,
    memory_limit: int = 20,
) -> list[dict[str, Any]]:
    return [
        {"tool": "invest_events_summary", "args": {"limit": _normalized_int(summary_limit, 100)}},
        {
            "tool": "invest_runtime_diagnostics",
            "args": {
                "event_limit": _normalized_int(event_limit, 50),
                "memory_limit": _normalized_int(memory_limit, 20),
            },
        },
    ]


def build_runtime_diagnostics_plan(
    *,
    summary_limit: int = 100,
    event_limit: int = 50,
    memory_limit: int = 20,
) -> list[dict[str, Any]]:
    return [
        {
            "tool": "invest_runtime_diagnostics",
            "args": {
                "event_limit": _normalized_int(event_limit, 50),
                "memory_limit": _normalized_int(memory_limit, 20),
            },
        },
        {"tool": "invest_events_summary", "args": {"limit": _normalized_int(summary_limit, 100)}},
    ]


def build_training_lab_summary_plan(*, limit: int = 5) -> list[dict[str, Any]]:
    normalized_limit = _normalized_int(limit, 5)
    return [
        {"tool": "invest_training_lab_summary", "args": {"limit": normalized_limit}},
        {"tool": "invest_training_runs_list", "args": {"limit": normalized_limit}},
        {"tool": "invest_training_evaluations_list", "args": {"limit": normalized_limit}},
    ]


def build_training_history_plan(*, limit: int = 5) -> list[dict[str, Any]]:
    normalized_limit = _normalized_int(limit, 5)
    return [
        {"tool": "invest_training_runs_list", "args": {"limit": normalized_limit}},
        {"tool": "invest_training_evaluations_list", "args": {"limit": normalized_limit}},
        {"tool": "invest_training_lab_summary", "args": {"limit": normalized_limit}},
    ]


def build_training_execution_plan(*, rounds: int, mock: bool, user_goal: str, limit: int = 5) -> list[dict[str, Any]]:
    normalized_limit = _normalized_int(limit, 5)
    return [
        {"tool": "invest_quick_test", "args": {}},
        {"tool": "invest_training_plan_create", "args": {"rounds": _normalized_int(rounds, 1), "mock": bool(mock), "goal": user_goal or "training request"}},
        {"tool": "invest_training_plan_execute", "args": {"plan_id": "<created_plan_id>"}},
        {"tool": "invest_training_evaluations_list", "args": {"limit": normalized_limit}},
        {"tool": "invest_training_lab_summary", "args": {"limit": normalized_limit}},
    ]


def build_training_plan_execution_plan(
    *,
    plan_id: str | None = None,
    rounds: int | None = None,
    mock: bool | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    normalized_limit = _normalized_int(limit, 5)
    execute_args: dict[str, Any] = {}
    if plan_id:
        execute_args["plan_id"] = str(plan_id)
    if rounds is not None:
        execute_args["rounds"] = _normalized_int(rounds, 1)
    if mock is not None:
        execute_args["mock"] = bool(mock)
    return [
        {"tool": "invest_training_plan_execute", "args": execute_args},
        {"tool": "invest_training_evaluations_list", "args": {"limit": normalized_limit}},
        {"tool": "invest_training_lab_summary", "args": {"limit": normalized_limit}},
    ]


def build_model_analytics_plan(operation: str) -> list[dict[str, Any]]:
    if operation == "get_managers":
        return [
            {"tool": "invest_managers", "args": {}},
            {"tool": "invest_governance_preview", "args": {}},
        ]
    if operation == "get_leaderboard":
        return [
            {"tool": "invest_leaderboard", "args": {}},
            {"tool": "invest_managers", "args": {}},
        ]
    if operation == "get_allocator_preview":
        return [
            {"tool": "invest_allocator", "args": {}},
            {"tool": "invest_leaderboard", "args": {}},
            {"tool": "invest_governance_preview", "args": {}},
        ]
    if operation == "get_governance_preview":
        return [
            {"tool": "invest_governance_preview", "args": {}},
            {"tool": "invest_managers", "args": {}},
        ]
    return [
        {"tool": "invest_managers", "args": {}},
        {"tool": "invest_leaderboard", "args": {}},
        {"tool": "invest_governance_preview", "args": {}},
    ]


def build_playbook_plan(operation: str) -> list[dict[str, Any]]:
    if operation == "reload_playbooks":
        return [
            {"tool": "invest_list_playbooks", "args": {"only_enabled": False}},
            {"tool": "invest_reload_playbooks", "args": {}},
        ]
    if operation == "playbook_inventory":
        return [
            {"tool": "invest_list_playbooks", "args": {"only_enabled": False}},
        ]
    return [{"tool": "invest_list_playbooks", "args": {"only_enabled": False}}]


def build_plugin_reload_plan() -> list[dict[str, Any]]:
    return [{"tool": "invest_plugins_reload", "args": {}}]


def build_config_overview_plan(*, config_focus: str, writes_state: bool) -> list[dict[str, Any]]:
    primary_tool = {
        "prompts": "invest_agent_prompts_list",
        "paths": "invest_runtime_paths_get",
        "control_plane": "invest_control_plane_get",
    }.get(config_focus, "invest_evolution_config_get")
    base_plan = [
        {"tool": primary_tool, "args": {}},
        {"tool": "invest_control_plane_get", "args": {}},
        {"tool": "invest_evolution_config_get", "args": {}},
    ]
    if config_focus not in {"prompts", "paths", "control_plane"}:
        base_plan.append({"tool": "invest_runtime_paths_get", "args": {}})

    deduped_plan: list[dict[str, Any]] = []
    seen_tools: set[str] = set()
    for item in base_plan:
        tool_name = str(item.get("tool") or "")
        if tool_name and tool_name not in seen_tools:
            deduped_plan.append(item)
            seen_tools.add(tool_name)

    if writes_state:
        if config_focus == "prompts":
            deduped_plan.append({"tool": "invest_agent_prompts_update", "args": {"name": "<agent>", "system_prompt": "<prompt>"}})
        elif config_focus == "paths":
            deduped_plan.append({"tool": "invest_runtime_paths_update", "args": {"patch": {"<path_key>": "<new_path>"}, "confirm": False}})
        elif config_focus == "control_plane":
            deduped_plan.append({"tool": "invest_control_plane_update", "args": {"patch": {"<section>": "<value>"}, "confirm": False}})
        else:
            deduped_plan.append({"tool": "invest_evolution_config_update", "args": {"patch": {"<param>": "<value>"}, "confirm": False}})
        deduped_plan.append({"tool": "invest_runtime_diagnostics", "args": {"event_limit": 50, "memory_limit": 20}})

    return deduped_plan


def build_data_focus_plan(*, data_focus: str, refresh: bool, writes_state: bool) -> list[dict[str, Any]]:
    if data_focus == "capital_flow":
        return [
            {"tool": "invest_data_status", "args": {"refresh": bool(refresh)}},
            {"tool": "invest_data_capital_flow", "args": {"limit": 200}},
        ]
    if data_focus == "dragon_tiger":
        return [
            {"tool": "invest_data_status", "args": {"refresh": bool(refresh)}},
            {"tool": "invest_data_dragon_tiger", "args": {"limit": 200}},
        ]
    if data_focus == "intraday_60m":
        return [
            {"tool": "invest_data_status", "args": {"refresh": bool(refresh)}},
            {"tool": "invest_data_intraday_60m", "args": {"limit": 500}},
        ]
    plan = [
        {"tool": "invest_data_status", "args": {"refresh": bool(refresh)}},
        {"tool": "invest_data_download", "args": {"action": "status"}},
    ]
    if writes_state or data_focus == "download":
        plan.extend([
            {"tool": "invest_data_download", "args": {"action": "trigger", "confirm": False}},
            {"tool": "invest_data_status", "args": {"refresh": True}},
        ])
    return plan


def build_commander_bounded_workflow_plan(
    *,
    domain: str,
    operation: str,
    runtime_tool: str,
    phase_stats: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    phase_stats = dict(phase_stats or {})

    if domain == "runtime":
        if operation == "status":
            return build_runtime_status_plan(
                primary_tool=runtime_tool,
                detail_mode=str(phase_stats.get("detail_mode", "fast") or "fast"),
                summary_limit=int(phase_stats.get("limit", 100) or 100),
                event_limit=int(phase_stats.get("event_limit", 50) or 50),
                memory_limit=int(phase_stats.get("memory_limit", 20) or 20),
            )
        if operation == "get_events_tail":
            return build_runtime_events_tail_plan(
                limit=int(phase_stats.get("limit", 50) or 50),
                summary_limit=int(phase_stats.get("limit", 100) or 100),
            )
        if operation == "get_events_summary":
            return build_runtime_events_summary_plan(
                summary_limit=int(phase_stats.get("limit", 100) or 100),
                event_limit=int(phase_stats.get("event_limit", 50) or 50),
                memory_limit=int(phase_stats.get("memory_limit", 20) or 20),
            )
        if operation == "get_runtime_diagnostics":
            return build_runtime_diagnostics_plan(
                summary_limit=int(phase_stats.get("limit", 100) or 100),
                event_limit=int(phase_stats.get("event_limit", 50) or 50),
                memory_limit=int(phase_stats.get("memory_limit", 20) or 20),
            )

    if domain == "config":
        if operation == "get_control_plane":
            return [
                {"tool": "invest_control_plane_get", "args": {}},
                {"tool": "invest_evolution_config_get", "args": {}},
            ]
        if operation == "get_evolution_config":
            return [
                {"tool": "invest_evolution_config_get", "args": {}},
                {"tool": "invest_control_plane_get", "args": {}},
                {"tool": "invest_runtime_paths_get", "args": {}},
            ]
        if operation == "get_runtime_paths":
            return [
                {"tool": "invest_runtime_paths_get", "args": {}},
                {"tool": "invest_evolution_config_get", "args": {}},
            ]
        if operation == "list_agent_prompts":
            return [{"tool": "invest_agent_prompts_list", "args": {}}]
        if operation == "update_agent_prompt":
            return [
                {"tool": "invest_agent_prompts_list", "args": {}},
                {
                    "tool": "invest_agent_prompts_update",
                    "args": _planner_args(
                        phase_stats=phase_stats,
                        payload=payload,
                        keys=["agent_name"],
                    ),
                },
            ]
        if operation == "update_runtime_paths":
            return [
                {"tool": "invest_runtime_paths_get", "args": {}},
                {
                    "tool": "invest_runtime_paths_update",
                    "args": {"confirm": bool(phase_stats.get("confirmed", False))},
                },
            ]
        if operation == "update_evolution_config":
            return [
                {"tool": "invest_evolution_config_get", "args": {}},
                {
                    "tool": "invest_evolution_config_update",
                    "args": {"confirm": bool(phase_stats.get("confirmed", False))},
                },
                {"tool": "invest_control_plane_get", "args": {}},
            ]
        if operation == "update_control_plane":
            return [
                {"tool": "invest_control_plane_get", "args": {}},
                {
                    "tool": "invest_control_plane_update",
                    "args": {"confirm": bool(phase_stats.get("confirmed", False))},
                },
                {"tool": "invest_evolution_config_get", "args": {}},
            ]

    if domain == "data":
        if operation == "get_data_status":
            return [
                {
                    "tool": "invest_data_status",
                    "args": {"refresh": bool(phase_stats.get("requested_refresh", False))},
                },
                {
                    "tool": "invest_data_download",
                    "args": {
                        "action": "status",
                        **_planner_args(
                            phase_stats=phase_stats,
                            payload=payload,
                            keys=["job_status"],
                        ),
                    },
                },
            ]
        if operation == "get_capital_flow":
            return [
                {"tool": "invest_data_status", "args": {"refresh": False}},
                {
                    "tool": "invest_data_capital_flow",
                    "args": {"limit": int(phase_stats.get("limit", 200) or 200)},
                },
            ]
        if operation == "get_dragon_tiger":
            return [
                {"tool": "invest_data_status", "args": {"refresh": False}},
                {
                    "tool": "invest_data_dragon_tiger",
                    "args": {"limit": int(phase_stats.get("limit", 200) or 200)},
                },
            ]
        if operation == "get_intraday_60m":
            return [
                {"tool": "invest_data_status", "args": {"refresh": False}},
                {
                    "tool": "invest_data_intraday_60m",
                    "args": {"limit": int(phase_stats.get("limit", 500) or 500)},
                },
            ]
        if operation == "get_data_download_status":
            return [
                {
                    "tool": "invest_data_download",
                    "args": {
                        "action": "status",
                        **_planner_args(
                            phase_stats=phase_stats,
                            payload=payload,
                            keys=["job_status"],
                        ),
                    },
                },
                {"tool": "invest_data_status", "args": {"refresh": False}},
            ]
        if operation == "trigger_data_download":
            return [
                {
                    "tool": "invest_data_download",
                    "args": {
                        "action": "status",
                        **_planner_args(
                            phase_stats=phase_stats,
                            payload=payload,
                            keys=["job_status"],
                        ),
                    },
                },
                {
                    "tool": "invest_data_download",
                    "args": {"action": "trigger", "confirm": bool(phase_stats.get("confirmed", False))},
                },
                {"tool": "invest_data_status", "args": {"refresh": True}},
            ]

    if domain == "memory":
        if operation == "list_memory":
            return [
                {
                    "tool": "invest_memory_search",
                    "args": {
                        "query": str(phase_stats.get("query", "") or ""),
                        "limit": int(phase_stats.get("limit", 20) or 20),
                    },
                },
                {
                    "tool": "invest_memory_list",
                    "args": {"limit": int(phase_stats.get("limit", 20) or 20)},
                },
            ]
        if operation == "get_memory_detail":
            return [
                {
                    "tool": "invest_memory_list",
                    "args": {"limit": int(phase_stats.get("limit", 20) or 20)},
                },
                {
                    "tool": "invest_memory_get",
                    "args": _planner_args(
                        phase_stats=phase_stats,
                        payload=payload,
                        keys=["record_id"],
                    ),
                },
            ]

    if domain == "scheduler":
        if operation == "add_cron_job":
            return [
                {"tool": "invest_cron_list", "args": {}},
                {
                    "tool": "invest_cron_add",
                    "args": _planner_args(
                        phase_stats=phase_stats,
                        payload=payload,
                        keys=["job_id", "every_sec"],
                    ),
                },
            ]
        if operation == "list_cron_jobs":
            return [{"tool": "invest_cron_list", "args": {}}]
        if operation == "remove_cron_job":
            return [
                {"tool": "invest_cron_list", "args": {}},
                {
                    "tool": "invest_cron_remove",
                    "args": _planner_args(
                        phase_stats=phase_stats,
                        payload=payload,
                        keys=["job_id"],
                    ),
                },
            ]

    if domain == "analytics":
        if operation in {
            "get_managers",
            "get_leaderboard",
            "get_allocator_preview",
            "get_governance_preview",
        }:
            return build_model_analytics_plan(operation)

    if domain == "training":
        if operation == "get_training_lab_summary":
            return build_training_lab_summary_plan(limit=int(phase_stats.get("limit", 5) or 5))
        if operation == "execute_training_plan":
            execute_args = _planner_args(
                phase_stats=phase_stats,
                payload=payload,
                keys=["plan_id", "rounds", "mock"],
            )
            return build_training_plan_execution_plan(
                plan_id=str(execute_args.get("plan_id") or "") or None,
                rounds=execute_args.get("rounds"),
                mock=execute_args.get("mock"),
                limit=int(phase_stats.get("limit", 5) or 5),
            )

    if domain == "playbook":
        if operation in {"playbook_inventory", "reload_playbooks"}:
            return build_playbook_plan(operation)

    if domain == "strategy":
        if operation == "list_stock_strategies":
            return [{"tool": "invest_stock_strategies", "args": {}}]

    if domain == "research":
        if operation == "list_research_cases":
            return [
                {
                    "tool": "invest_research_cases",
                    "args": _planner_args(
                        phase_stats=phase_stats,
                        payload=payload,
                        keys=["limit", "policy_id", "symbol", "as_of_date", "horizon"],
                    ),
                },
                {
                    "tool": "invest_research_calibration",
                    "args": _planner_args(
                        phase_stats=phase_stats,
                        payload=payload,
                        keys=["policy_id"],
                    ),
                },
            ]
        if operation == "list_research_attributions":
            return [
                {
                    "tool": "invest_research_attributions",
                    "args": _planner_args(
                        phase_stats=phase_stats,
                        payload=payload,
                        keys=["limit"],
                    ),
                },
                {
                    "tool": "invest_research_cases",
                    "args": {"limit": int(phase_stats.get("limit", 20) or 20)},
                },
            ]
        if operation == "get_research_calibration":
            return [
                {
                    "tool": "invest_research_calibration",
                    "args": _planner_args(
                        phase_stats=phase_stats,
                        payload=payload,
                        keys=["policy_id"],
                    ),
                },
                {
                    "tool": "invest_research_cases",
                    "args": {
                        "limit": int(phase_stats.get("limit", 20) or 20),
                        **_planner_args(
                            phase_stats=phase_stats,
                            payload=payload,
                            keys=["policy_id"],
                        ),
                    },
                },
            ]

    if domain == "plugin" and operation == "reload_plugins":
        return build_plugin_reload_plan()

    return [{"tool": runtime_tool, "args": {}}]


TASK_BUS_SCHEMA_VERSION = "task_bus.v2"
PLAN_SCHEMA_VERSION = "task_plan.v2"
COVERAGE_SCHEMA_VERSION = "task_coverage.v2"
ARTIFACT_TAXONOMY_SCHEMA_VERSION = "artifact_taxonomy.v2"
BOUNDED_WORKFLOW_SCHEMA_VERSION = "bounded_workflow.v2"

COVERAGE_KIND_PLAN_EXECUTION = "plan_vs_execution"
COVERAGE_KIND_WORKFLOW_PHASE = "workflow_phase_completion"

ARTIFACT_KIND_PATH = "path"
ARTIFACT_KIND_OBJECT = "object"
ARTIFACT_KIND_COLLECTION = "collection"
ARTIFACT_KIND_SCALAR = "scalar"
ARTIFACT_KIND_ID = "id"
ARTIFACT_KIND_UNKNOWN = "unknown"
ARTIFACT_KINDS = [
    ARTIFACT_KIND_COLLECTION,
    ARTIFACT_KIND_ID,
    ARTIFACT_KIND_OBJECT,
    ARTIFACT_KIND_PATH,
    ARTIFACT_KIND_SCALAR,
    ARTIFACT_KIND_UNKNOWN,
]

CONFIRMATION_STATE_PENDING = "pending_confirmation"
CONFIRMATION_STATE_CONFIRMED_OR_NOT_REQUIRED = "confirmed_or_not_required"
CONFIRMATION_STATE_NOT_APPLICABLE = "not_applicable"
CONFIRMATION_STATES = [
    CONFIRMATION_STATE_PENDING,
    CONFIRMATION_STATE_CONFIRMED_OR_NOT_REQUIRED,
    CONFIRMATION_STATE_NOT_APPLICABLE,
]

RISK_LEVEL_LOW = "low"
RISK_LEVEL_MEDIUM = "medium"
RISK_LEVEL_HIGH = "high"
RISK_LEVELS = [RISK_LEVEL_LOW, RISK_LEVEL_MEDIUM, RISK_LEVEL_HIGH]

REASON_READ_ONLY_ANALYSIS = "read_only_analysis"
REASON_TOOL_GROUNDED_EXECUTION = "tool_grounded_execution"
REASON_STATE_CHANGING_REQUEST = "state_changing_request"
REASON_TRAINING_CHANGES_RUNTIME_STATE = "training_changes_runtime_state"
REASON_CONFIRMATION_REQUIRED = "confirmation_required"
REASON_INCOMPLETE_PLAN_COVERAGE = "incomplete_plan_coverage"
REASON_INCOMPLETE_PARAMETER_COVERAGE = "incomplete_parameter_coverage"
REASON_CODES = [
    REASON_CONFIRMATION_REQUIRED,
    REASON_INCOMPLETE_PARAMETER_COVERAGE,
    REASON_INCOMPLETE_PLAN_COVERAGE,
    REASON_READ_ONLY_ANALYSIS,
    REASON_STATE_CHANGING_REQUEST,
    REASON_TOOL_GROUNDED_EXECUTION,
    REASON_TRAINING_CHANGES_RUNTIME_STATE,
]
READONLY_DEFAULT_REASON_CODES = [REASON_READ_ONLY_ANALYSIS, REASON_TOOL_GROUNDED_EXECUTION]
MUTATING_DEFAULT_REASON_CODES = [REASON_STATE_CHANGING_REQUEST, REASON_TOOL_GROUNDED_EXECUTION]
TRAINING_DEFAULT_REASON_CODES = [REASON_TRAINING_CHANGES_RUNTIME_STATE, REASON_TOOL_GROUNDED_EXECUTION]

TASK_BUS_TOP_LEVEL_KEYS = ["schema_version", "planner", "gate", "audit"]
TASK_PLAN_KEYS = ["intent", "operation", "mode", "user_goal", "available_tools", "recommended_plan", "plan_summary"]
TASK_PLAN_SUMMARY_KEYS = ["schema_version", "available_tool_count", "recommended_step_count", "recommended_tool_count", "recommended_tools", "step_ids"]
TASK_GATE_KEYS = ["decision", "risk_level", "writes_state", "requires_confirmation", "reasons", "confirmation"]
TASK_CONFIRMATION_KEYS = ["required", "decision", "state", "reason_codes"]
TASK_AUDIT_KEYS = ["status", "started_at", "completed_at", "tool_count", "used_tools", "artifacts", "coverage", "artifact_taxonomy"]
FEEDBACK_COVERAGE_KEYS = ["planned_step_coverage", "parameter_coverage"]
FEEDBACK_KEYS = ["message", "summary", "reason_codes", "reason_texts", "requires_confirmation", "decision", "coverage"]
NEXT_ACTION_KEYS = ["kind", "label", "description", "requires_confirmation", "suggested_params"]
RESPONSE_ENVELOPE_KEYS = ["status", "reply", "message", "feedback", "next_action", "task_bus"]
TASK_COVERAGE_KEYS = [
    "schema_version",
    "coverage_kind",
    "recommended_step_count",
    "executed_step_count",
    "available_tool_count",
    "used_tool_count",
    "recommended_tool_count",
    "covered_recommended_tools",
    "covered_recommended_step_ids",
    "missing_planned_steps",
    "missing_planned_step_ids",
    "planned_step_coverage",
    "required_tool_coverage",
    "parameterized_step_count",
    "covered_parameterized_step_ids",
    "missing_parameterized_step_ids",
    "parameter_coverage",
]
ARTIFACT_TAXONOMY_KEYS = ["schema_version", "count", "keys", "kinds", "path_keys", "object_keys", "collection_keys", "known_kinds"]
PLAN_STEP_KEYS = ["step_id", "tool", "args"]

BOUNDED_WORKFLOW_TOP_LEVEL_KEYS = ["entrypoint", "orchestration", "protocol", "artifacts", "coverage", "artifact_taxonomy", "feedback", "next_action"]
BOUNDED_PROTOCOL_KEYS = [
    "schema_version",
    "task_bus_schema_version",
    "plan_schema_version",
    "coverage_schema_version",
    "artifact_taxonomy_schema_version",
    "domain",
    "operation",
]
BOUNDED_COVERAGE_KEYS = [
    "schema_version",
    "coverage_kind",
    "workflow_step_count",
    "completed_workflow_step_count",
    "workflow_step_coverage",
    "phase_stat_key_count",
]


def task_bus_contract() -> dict[str, Any]:
    return {
        "schema_version": TASK_BUS_SCHEMA_VERSION,
        "top_level_keys": list(TASK_BUS_TOP_LEVEL_KEYS),
        "planner": {
            "keys": list(TASK_PLAN_KEYS),
            "step_required_keys": list(PLAN_STEP_KEYS),
            "summary_keys": list(TASK_PLAN_SUMMARY_KEYS),
            "summary_schema_version": PLAN_SCHEMA_VERSION,
        },
        "gate": {
            "keys": list(TASK_GATE_KEYS),
            "confirmation_keys": list(TASK_CONFIRMATION_KEYS),
            "confirmation_states": list(CONFIRMATION_STATES),
            "risk_levels": list(RISK_LEVELS),
            "reason_codes": list(REASON_CODES),
            "readonly_default_reason_codes": list(READONLY_DEFAULT_REASON_CODES),
            "mutating_default_reason_codes": list(MUTATING_DEFAULT_REASON_CODES),
            "training_default_reason_codes": list(TRAINING_DEFAULT_REASON_CODES),
        },
        "audit": {
            "keys": list(TASK_AUDIT_KEYS),
            "coverage_keys": list(TASK_COVERAGE_KEYS),
            "coverage_schema_version": COVERAGE_SCHEMA_VERSION,
            "coverage_kinds": [COVERAGE_KIND_PLAN_EXECUTION, COVERAGE_KIND_WORKFLOW_PHASE],
            "artifact_taxonomy_keys": list(ARTIFACT_TAXONOMY_KEYS),
            "artifact_taxonomy_schema_version": ARTIFACT_TAXONOMY_SCHEMA_VERSION,
            "artifact_kinds": list(ARTIFACT_KINDS),
        },
        "feedback": {
            "keys": list(FEEDBACK_KEYS),
            "coverage_keys": list(FEEDBACK_COVERAGE_KEYS),
        },
        "next_action": {
            "keys": list(NEXT_ACTION_KEYS),
        },
        "response_envelope": {
            "keys": list(RESPONSE_ENVELOPE_KEYS),
        },
    }


def bounded_workflow_contract() -> dict[str, Any]:
    return {
        "schema_version": BOUNDED_WORKFLOW_SCHEMA_VERSION,
        "top_level_keys": list(BOUNDED_WORKFLOW_TOP_LEVEL_KEYS),
        "protocol_keys": list(BOUNDED_PROTOCOL_KEYS),
        "protocol_versions": {
            "task_bus": TASK_BUS_SCHEMA_VERSION,
            "plan": PLAN_SCHEMA_VERSION,
            "coverage": COVERAGE_SCHEMA_VERSION,
            "artifact_taxonomy": ARTIFACT_TAXONOMY_SCHEMA_VERSION,
        },
        "coverage_keys": list(BOUNDED_COVERAGE_KEYS),
        "coverage_kind": COVERAGE_KIND_WORKFLOW_PHASE,
        "artifact_taxonomy_keys": list(ARTIFACT_TAXONOMY_KEYS),
        "artifact_kinds": list(ARTIFACT_KINDS),
        "feedback": {
            "keys": list(FEEDBACK_KEYS),
            "coverage_keys": list(FEEDBACK_COVERAGE_KEYS),
        },
        "next_action": {
            "keys": list(NEXT_ACTION_KEYS),
        },
        "response_envelope": {
            "keys": list(RESPONSE_ENVELOPE_KEYS),
        },
    }


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


@dataclass(frozen=True)
class _PlanExecutionProjection:
    normalized_plan: list[dict[str, Any]]
    recommended_tools: list[str]
    used_tools: list[str]
    used_tool_set: set[str]
    covered_tools: list[str]
    covered_step_ids: list[str]
    missing_steps: list[dict[str, Any]]
    parameter_coverage: dict[str, Any]


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


def _parameter_coverage_from_steps(
    recommended_steps: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
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


def _parameter_coverage(recommended_plan: list[dict[str, Any]], tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    recommended_steps = _normalize_recommended_plan(recommended_plan)
    return _parameter_coverage_from_steps(recommended_steps, tool_calls)


def _build_plan_execution_projection(
    *,
    recommended_plan: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
) -> _PlanExecutionProjection:
    normalized_plan = _normalize_recommended_plan(recommended_plan)
    recommended_tools = _normalize_plan_tools(normalized_plan)
    used_tools = [
        str(item.get("action", {}).get("tool") or "")
        for item in list(tool_calls or [])
        if item.get("action")
    ]
    used_tool_set = {tool for tool in used_tools if tool}
    covered_tools = [tool for tool in recommended_tools if tool in used_tool_set]
    covered_step_ids = [
        str(step.get("step_id") or "")
        for step in normalized_plan
        if str(step.get("tool") or "") in used_tool_set
    ]
    missing_steps = [
        step for step in normalized_plan if str(step.get("tool") or "") not in used_tool_set
    ]
    parameter_coverage = _parameter_coverage_from_steps(normalized_plan, tool_calls)
    return _PlanExecutionProjection(
        normalized_plan=normalized_plan,
        recommended_tools=recommended_tools,
        used_tools=used_tools,
        used_tool_set=used_tool_set,
        covered_tools=covered_tools,
        covered_step_ids=covered_step_ids,
        missing_steps=missing_steps,
        parameter_coverage=parameter_coverage,
    )


def _build_plan_summary(
    *,
    available_tools: list[str],
    projection: _PlanExecutionProjection,
) -> dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "available_tool_count": len(list(available_tools or [])),
        "recommended_step_count": len(projection.normalized_plan),
        "recommended_tool_count": len(projection.recommended_tools),
        "recommended_tools": list(projection.recommended_tools),
        "step_ids": [str(step.get("step_id") or "") for step in projection.normalized_plan],
    }


def _default_coverage(
    *,
    projection: _PlanExecutionProjection,
    tool_calls: list[dict[str, Any]],
    available_tools: list[str],
) -> dict[str, Any]:
    planned_step_coverage = (
        1.0
        if not projection.normalized_plan
        else round(len(projection.covered_step_ids) / len(projection.normalized_plan), 3)
    )
    return {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "coverage_kind": COVERAGE_KIND_PLAN_EXECUTION,
        "recommended_step_count": len(projection.normalized_plan),
        "executed_step_count": len(list(tool_calls or [])),
        "available_tool_count": len(list(available_tools or [])),
        "used_tool_count": len(projection.used_tool_set),
        "recommended_tool_count": len(projection.recommended_tools),
        "covered_recommended_tools": list(projection.covered_tools),
        "covered_recommended_step_ids": list(projection.covered_step_ids),
        "missing_planned_steps": list(projection.missing_steps),
        "missing_planned_step_ids": [
            str(step.get("step_id") or "") for step in projection.missing_steps
        ],
        "planned_step_coverage": planned_step_coverage,
        "required_tool_coverage": (
            1.0
            if not projection.recommended_tools
            else round(
                len(projection.covered_tools) / len(projection.recommended_tools),
                3,
            )
        ),
        "parameterized_step_count": projection.parameter_coverage["parameterized_step_count"],
        "covered_parameterized_step_ids": projection.parameter_coverage["covered_parameterized_step_ids"],
        "missing_parameterized_step_ids": projection.parameter_coverage["missing_parameterized_step_ids"],
        "parameter_coverage": projection.parameter_coverage["parameter_coverage"],
    }


def _normalize_coverage(
    *,
    coverage: dict[str, Any] | None,
    projection: _PlanExecutionProjection,
    tool_calls: list[dict[str, Any]],
    available_tools: list[str],
) -> dict[str, Any]:
    base = _default_coverage(
        projection=projection,
        tool_calls=tool_calls,
        available_tools=available_tools,
    )
    if not coverage:
        return base
    merged = {**base, **dict(coverage)}
    merged.setdefault("schema_version", COVERAGE_SCHEMA_VERSION)
    merged.setdefault("coverage_kind", COVERAGE_KIND_PLAN_EXECUTION)
    merged.setdefault("recommended_step_count", len(projection.normalized_plan))
    merged.setdefault("executed_step_count", len(list(tool_calls or [])))
    merged.setdefault("available_tool_count", len(list(available_tools or [])))
    merged.setdefault("used_tool_count", len(projection.used_tool_set))
    merged.setdefault("recommended_tool_count", len(projection.recommended_tools))
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
        "read_only_analysis": "本次是只读分析，不会改动系统状态",
        "tool_grounded_execution": "结果来自实际工具执行，不是自由编造",
        REASON_CONFIRMATION_REQUIRED: "当前操作仍需要人工确认",
        REASON_INCOMPLETE_PLAN_COVERAGE: "推荐计划尚未被完整覆盖",
        REASON_INCOMPLETE_PARAMETER_COVERAGE: "关键参数执行尚未完整对齐推荐计划",
    }
    return mapping.get(str(reason_code or ""), str(reason_code or "").replace("_", " "))


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
    projection = _build_plan_execution_projection(
        recommended_plan=recommended_plan,
        tool_calls=tool_calls,
    )
    normalized_artifacts = dict(artifacts or {})
    normalized_coverage = _normalize_coverage(
        coverage=coverage,
        projection=projection,
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
        recommended_plan=list(projection.normalized_plan),
        plan_summary=_build_plan_summary(
            available_tools=available_tools,
            projection=projection,
        ),
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
        used_tools=list(projection.used_tools),
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
    "ARTIFACT_KINDS",
    "ARTIFACT_KIND_COLLECTION",
    "ARTIFACT_KIND_ID",
    "ARTIFACT_KIND_OBJECT",
    "ARTIFACT_KIND_PATH",
    "ARTIFACT_KIND_SCALAR",
    "ARTIFACT_KIND_UNKNOWN",
    "ARTIFACT_TAXONOMY_KEYS",
    "ARTIFACT_TAXONOMY_SCHEMA_VERSION",
    "BOUNDED_COVERAGE_KEYS",
    "BOUNDED_PROTOCOL_KEYS",
    "BOUNDED_WORKFLOW_SCHEMA_VERSION",
    "BOUNDED_WORKFLOW_TOP_LEVEL_KEYS",
    "CONFIRMATION_STATES",
    "CONFIRMATION_STATE_CONFIRMED_OR_NOT_REQUIRED",
    "CONFIRMATION_STATE_NOT_APPLICABLE",
    "CONFIRMATION_STATE_PENDING",
    "COVERAGE_KIND_PLAN_EXECUTION",
    "COVERAGE_KIND_WORKFLOW_PHASE",
    "COVERAGE_SCHEMA_VERSION",
    "FEEDBACK_COVERAGE_KEYS",
    "FEEDBACK_KEYS",
    "MUTATING_DEFAULT_REASON_CODES",
    "NEXT_ACTION_KEYS",
    "PLAN_SCHEMA_VERSION",
    "PLAN_STEP_KEYS",
    "READONLY_DEFAULT_REASON_CODES",
    "REASON_CODES",
    "REASON_CONFIRMATION_REQUIRED",
    "REASON_INCOMPLETE_PARAMETER_COVERAGE",
    "REASON_INCOMPLETE_PLAN_COVERAGE",
    "REASON_READ_ONLY_ANALYSIS",
    "REASON_STATE_CHANGING_REQUEST",
    "REASON_TOOL_GROUNDED_EXECUTION",
    "REASON_TRAINING_CHANGES_RUNTIME_STATE",
    "RESPONSE_ENVELOPE_KEYS",
    "RISK_LEVEL_HIGH",
    "RISK_LEVEL_LOW",
    "RISK_LEVEL_MEDIUM",
    "RISK_LEVELS",
    "TASK_AUDIT_KEYS",
    "TASK_BUS_SCHEMA_VERSION",
    "TASK_BUS_TOP_LEVEL_KEYS",
    "TASK_CONFIRMATION_KEYS",
    "TASK_COVERAGE_KEYS",
    "TASK_GATE_KEYS",
    "TASK_PLAN_KEYS",
    "TASK_PLAN_SUMMARY_KEYS",
    "TRAINING_DEFAULT_REASON_CODES",
    "TaskAudit",
    "TaskGate",
    "TaskPlan",
    "bounded_workflow_contract",
    "build_artifact_taxonomy",
    "build_bounded_workflow_protocol",
    "build_gate_feedback",
    "build_mutating_task_bus",
    "build_next_action",
    "build_readonly_task_bus",
    "build_task_bus",
    "build_workflow_phase_coverage",
    "task_bus_contract",
]
