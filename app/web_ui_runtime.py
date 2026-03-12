"""Pure helpers for web UI shell rollout and compatibility decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.web_ui_metadata import (
    DEFAULT_FRONTEND_CANARY_QUERY_PARAM,
    FRONTEND_APP_ROUTE,
    FRONTEND_CANARY_HEADER,
    FRONTEND_CANARY_TRUE_VALUES,
    SHELL_PUBLIC_PATHS,
)
from config.web_ui import normalize_frontend_canary_query_param, normalize_web_ui_shell_mode


_TRUE_VALUES = frozenset({"1", "true", "t", "yes", "y", "on"})

LEGACY_SHELL_TARGET = "legacy"
FRONTEND_APP_SHELL_TARGET = "frontend_app"


@dataclass(frozen=True)
class WebUIShellSettings:
    shell_mode: str
    frontend_canary_enabled: bool
    frontend_canary_query_param: str

    @classmethod
    def from_config(cls, cfg: Any) -> "WebUIShellSettings":
        return cls(
            shell_mode=normalize_web_ui_shell_mode(getattr(cfg, "web_ui_shell_mode", None)),
            frontend_canary_enabled=bool(getattr(cfg, "frontend_canary_enabled", False)),
            frontend_canary_query_param=normalize_frontend_canary_query_param(
                getattr(
                    cfg,
                    "frontend_canary_query_param",
                    DEFAULT_FRONTEND_CANARY_QUERY_PARAM,
                )
            ),
        )


def is_shell_public_path(path: str) -> bool:
    normalized = str(path or "")
    if normalized in SHELL_PUBLIC_PATHS:
        return True
    return normalized.startswith("/static/") or normalized.startswith(f"{FRONTEND_APP_ROUTE}/")


def request_prefers_frontend_app(
    settings: WebUIShellSettings,
    *,
    query_args: Mapping[str, Any],
    headers: Mapping[str, Any],
) -> bool:
    if settings.shell_mode == "app":
        return True
    if not settings.frontend_canary_enabled:
        return False
    query_value = str(query_args.get(settings.frontend_canary_query_param, "") or "").strip().lower()
    if query_value in (_TRUE_VALUES | FRONTEND_CANARY_TRUE_VALUES):
        return True
    header_value = str(headers.get(FRONTEND_CANARY_HEADER, "") or "").strip().lower()
    return header_value in (_TRUE_VALUES | FRONTEND_CANARY_TRUE_VALUES)


def resolve_root_shell_target(
    settings: WebUIShellSettings,
    *,
    frontend_dist_available: bool,
    query_args: Mapping[str, Any],
    headers: Mapping[str, Any],
) -> str:
    if frontend_dist_available and request_prefers_frontend_app(
        settings,
        query_args=query_args,
        headers=headers,
    ):
        return FRONTEND_APP_SHELL_TARGET
    return LEGACY_SHELL_TARGET


def normalize_frontend_asset_path(asset_path: str = "") -> str:
    return str(asset_path or "").strip()
