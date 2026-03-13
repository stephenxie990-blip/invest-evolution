"""Shared recommended-plan templates for commander and brain runtimes."""

from __future__ import annotations

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
    if operation == "get_investment_models":
        return [
            {"tool": "invest_investment_models", "args": {}},
            {"tool": "invest_model_routing_preview", "args": {}},
        ]
    if operation == "get_leaderboard":
        return [
            {"tool": "invest_leaderboard", "args": {}},
            {"tool": "invest_investment_models", "args": {}},
        ]
    if operation == "get_allocator_preview":
        return [
            {"tool": "invest_allocator", "args": {}},
            {"tool": "invest_leaderboard", "args": {}},
            {"tool": "invest_model_routing_preview", "args": {}},
        ]
    if operation == "get_model_routing_preview":
        return [
            {"tool": "invest_model_routing_preview", "args": {}},
            {"tool": "invest_investment_models", "args": {}},
        ]
    return [
        {"tool": "invest_investment_models", "args": {}},
        {"tool": "invest_leaderboard", "args": {}},
        {"tool": "invest_model_routing_preview", "args": {}},
    ]


def build_strategy_plan(operation: str) -> list[dict[str, Any]]:
    if operation == "reload_strategies":
        return [
            {"tool": "invest_list_strategies", "args": {"only_enabled": False}},
            {"tool": "invest_reload_strategies", "args": {}},
            {"tool": "invest_stock_strategies", "args": {}},
        ]
    if operation == "strategy_inventory":
        return [
            {"tool": "invest_list_strategies", "args": {"only_enabled": False}},
            {"tool": "invest_stock_strategies", "args": {}},
        ]
    return [
        {"tool": "invest_stock_strategies", "args": {}},
        {"tool": "invest_list_strategies", "args": {"only_enabled": False}},
    ]


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
            "get_investment_models",
            "get_leaderboard",
            "get_allocator_preview",
            "get_model_routing_preview",
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

    if domain == "strategy":
        if operation in {"list_stock_strategies", "reload_strategies"}:
            return build_strategy_plan(operation)

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
