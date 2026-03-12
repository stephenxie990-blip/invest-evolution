"""Shared metadata for commander runtime observability tool names."""

from __future__ import annotations

INVEST_QUICK_STATUS_TOOL_NAME = "invest_quick_status"
INVEST_DEEP_STATUS_TOOL_NAME = "invest_deep_status"

RUNTIME_OBSERVABILITY_TOOL_NAMES = frozenset(
    {
        INVEST_QUICK_STATUS_TOOL_NAME,
        INVEST_DEEP_STATUS_TOOL_NAME,
        "invest_events_tail",
        "invest_events_summary",
        "invest_runtime_diagnostics",
    }
)
