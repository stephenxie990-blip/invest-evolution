"""Shared domain catalogs for commander bounded workflows."""

from __future__ import annotations

DOMAIN_TOOL_CATALOG: dict[str, tuple[str, ...]] = {
    "config": (
        "invest_control_plane_get",
        "invest_control_plane_update",
        "invest_evolution_config_get",
        "invest_evolution_config_update",
        "invest_runtime_paths_get",
        "invest_runtime_paths_update",
        "invest_agent_prompts_list",
        "invest_agent_prompts_update",
    ),
    "data": (
        "invest_data_status",
        "invest_data_download",
        "invest_data_capital_flow",
        "invest_data_dragon_tiger",
        "invest_data_intraday_60m",
    ),
    "training": (
        "invest_train",
        "invest_quick_test",
        "invest_training_plan_create",
        "invest_training_plan_list",
        "invest_training_plan_execute",
        "invest_training_runs_list",
        "invest_training_evaluations_list",
        "invest_training_lab_summary",
    ),
    "runtime": (
        "invest_quick_status",
        "invest_deep_status",
        "invest_events_tail",
        "invest_events_summary",
        "invest_runtime_diagnostics",
    ),
    "memory": (
        "invest_memory_search",
        "invest_memory_list",
        "invest_memory_get",
    ),
    "scheduler": (
        "invest_cron_add",
        "invest_cron_list",
        "invest_cron_remove",
    ),
    "analytics": (
        "invest_investment_models",
        "invest_leaderboard",
        "invest_allocator",
        "invest_model_routing_preview",
    ),
    "strategy": (
        "invest_list_strategies",
        "invest_reload_strategies",
        "invest_stock_strategies",
    ),
    "research": (
        "invest_research_cases",
        "invest_research_attributions",
        "invest_research_calibration",
    ),
    "plugin": (
        "invest_plugins_reload",
    ),
}


DOMAIN_AGENT_KIND: dict[str, str] = {
    "analytics": "bounded_analytics_agent",
    "config": "bounded_config_agent",
    "data": "bounded_data_agent",
    "memory": "bounded_memory_agent",
    "plugin": "bounded_plugin_agent",
    "research": "bounded_research_agent",
    "runtime": "bounded_runtime_agent",
    "scheduler": "bounded_scheduler_agent",
    "stock": "bounded_stock_agent",
    "strategy": "bounded_strategy_agent",
    "training": "bounded_training_agent",
}


def get_domain_tools(domain: str) -> list[str]:
    return list(DOMAIN_TOOL_CATALOG.get(str(domain or ""), ()))


def get_domain_agent_kind(domain: str, default: str = "bounded_runtime_agent") -> str:
    return DOMAIN_AGENT_KIND.get(str(domain or ""), default)
