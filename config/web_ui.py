"""Shared normalization and defaults for web UI shell configuration."""

from __future__ import annotations

import logging
from typing import Any


DEFAULT_WEB_UI_SHELL_MODE = "legacy"
WEB_UI_SHELL_MODES = frozenset({DEFAULT_WEB_UI_SHELL_MODE, "app"})
DEFAULT_FRONTEND_CANARY_QUERY_PARAM = "__frontend"


def normalize_web_ui_shell_mode(
    value: Any,
    *,
    strict: bool = False,
    logger: logging.Logger | None = None,
) -> str:
    normalized = str(value or DEFAULT_WEB_UI_SHELL_MODE).strip().lower() or DEFAULT_WEB_UI_SHELL_MODE
    if normalized in WEB_UI_SHELL_MODES:
        return normalized
    if strict:
        raise ValueError(
            f"web_ui_shell_mode must be one of: {', '.join(sorted(WEB_UI_SHELL_MODES))}"
        )
    if logger is not None:
        logger.warning("Invalid web_ui_shell_mode=%r, fallback to %s", value, DEFAULT_WEB_UI_SHELL_MODE)
    return DEFAULT_WEB_UI_SHELL_MODE


def normalize_frontend_canary_query_param(value: Any) -> str:
    return str(value or DEFAULT_FRONTEND_CANARY_QUERY_PARAM).strip() or DEFAULT_FRONTEND_CANARY_QUERY_PARAM
