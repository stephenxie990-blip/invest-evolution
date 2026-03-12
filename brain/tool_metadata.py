"""Shared metadata for commander tool names and compatibility aliases."""

from __future__ import annotations


INVEST_STATUS_TOOL_NAME = "invest_status"
INVEST_QUICK_STATUS_TOOL_NAME = "invest_quick_status"
INVEST_DEEP_STATUS_TOOL_NAME = "invest_deep_status"

INVEST_STATUS_ALIAS_DESCRIPTION = (
    f"Deprecated compatibility alias for `{INVEST_QUICK_STATUS_TOOL_NAME}` "
    f"(fast snapshot path). Prefer `{INVEST_QUICK_STATUS_TOOL_NAME}`."
)

RUNTIME_OBSERVABILITY_TOOL_NAMES = frozenset(
    {
        INVEST_QUICK_STATUS_TOOL_NAME,
        INVEST_DEEP_STATUS_TOOL_NAME,
        INVEST_STATUS_TOOL_NAME,
        "invest_events_tail",
        "invest_events_summary",
        "invest_runtime_diagnostics",
    }
)
