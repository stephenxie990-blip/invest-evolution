"""Governance package root."""

from __future__ import annotations

from . import engine, planning
from .engine import (
    GovernanceCoordinator,
    ModelAllocator,
    RegimeClassifier,
    build_allocation_plan,
    build_leaderboard,
    build_leaderboard_payload,
    collect_cycle_records,
    write_leaderboard,
)
from .planning import (
    PlanAssemblyService,
    PortfolioAssembler,
    PortfolioAssemblyConfig,
    RiskCheckService,
)

PACKAGE_MODULES = {
    "engine": engine,
    "planning": planning,
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
    *PACKAGE_MODULES,
    "GovernanceCoordinator",
    "ModelAllocator",
    "RegimeClassifier",
    "build_allocation_plan",
    "build_leaderboard",
    "build_leaderboard_payload",
    "collect_cycle_records",
    "write_leaderboard",
    "PlanAssemblyService",
    "PortfolioAssembler",
    "PortfolioAssemblyConfig",
    "RiskCheckService",
    "PACKAGE_MODULES",
    "load_package",
]
