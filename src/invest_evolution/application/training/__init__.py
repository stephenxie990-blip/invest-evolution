"""Training orchestration grouped by canonical contexts."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

GROUP_MODULE_PATHS = {
    "bootstrap": "invest_evolution.application.training.bootstrap",
    "controller": "invest_evolution.application.training.controller",
    "execution": "invest_evolution.application.training.execution",
    "observability": "invest_evolution.application.training.observability",
    "persistence": "invest_evolution.application.training.persistence",
    "policy": "invest_evolution.application.training.policy",
    "research": "invest_evolution.application.training.research",
    "review": "invest_evolution.application.training.review",
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
