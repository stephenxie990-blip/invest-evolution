"""Application-layer facade for the unified commander runtime."""

from __future__ import annotations

from typing import Any

from app.commander import CommanderRuntime


class CommanderRuntimeFacade(CommanderRuntime):
    """Thin application-layer facade over the legacy commander runtime."""


def build_commander_runtime(*args: Any, **kwargs: Any) -> CommanderRuntimeFacade:
    return CommanderRuntimeFacade(*args, **kwargs)
