"""Shared recommended-plan templates for commander and brain runtimes."""

from __future__ import annotations

from typing import Any


def _normalized_int(value: Any, default: int) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return int(default)


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
