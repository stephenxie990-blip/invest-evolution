"""Commander application grouped by canonical contexts."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

GROUP_MODULE_PATHS = {
    "bootstrap": "invest_evolution.application.commander.bootstrap",
    "ops": "invest_evolution.application.commander.ops",
    "presentation": "invest_evolution.application.commander.presentation",
    "runtime": "invest_evolution.application.commander.runtime",
    "status": "invest_evolution.application.commander.status",
    "workflow": "invest_evolution.application.commander.workflow",
}


def load_group(name: str) -> ModuleType:
    return import_module(GROUP_MODULE_PATHS[name])


def __getattr__(name: str) -> ModuleType:
    if name in GROUP_MODULE_PATHS:
        module = load_group(name)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(GROUP_MODULE_PATHS))


__all__ = [*GROUP_MODULE_PATHS, "GROUP_MODULE_PATHS", "load_group"]
