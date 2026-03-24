"""Manager registry package root."""

from __future__ import annotations

from . import registry
from .registry import (
    ManagerAgent,
    ManagerExecutionArtifacts,
    ManagerRegistry,
    RuntimeBackedManager,
    build_default_manager_registry,
    resolve_manager_config_ref,
)

PACKAGE_MODULES = {
    "registry": registry,
}


def load_package(name: str):
    return PACKAGE_MODULES[name]


def __getattr__(name: str):
    if name in PACKAGE_MODULES:
        return PACKAGE_MODULES[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(PACKAGE_MODULES))


__all__ = [
    "registry",
    "ManagerAgent",
    "ManagerExecutionArtifacts",
    "ManagerRegistry",
    "RuntimeBackedManager",
    "build_default_manager_registry",
    "resolve_manager_config_ref",
]
