"""Shared metadata for web UI shell routing and compatibility surfaces."""

from __future__ import annotations

from config.web_ui import DEFAULT_FRONTEND_CANARY_QUERY_PARAM

FRONTEND_CANARY_HEADER = "X-Invest-Frontend-Canary"

LEGACY_UI_ROUTE = "/legacy"
FRONTEND_APP_ROUTE = "/app"

SHELL_PUBLIC_PATHS = frozenset(
    {
        "/healthz",
        "/",
        LEGACY_UI_ROUTE,
        FRONTEND_APP_ROUTE,
    }
)

FRONTEND_CANARY_TRUE_VALUES = frozenset({"app", "new", "frontend"})
