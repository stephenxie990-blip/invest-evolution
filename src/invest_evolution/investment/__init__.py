"""Investment domain package."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

PACKAGE_MODULES = {
    'agents': 'invest_evolution.investment.agents',
    'contracts': 'invest_evolution.investment.contracts',
    'evolution': 'invest_evolution.investment.evolution',
    'factors': 'invest_evolution.investment.factors',
    'foundation': 'invest_evolution.investment.foundation',
    'governance': 'invest_evolution.investment.governance',
    'managers': 'invest_evolution.investment.managers',
    'research': 'invest_evolution.investment.research',
    'runtimes': 'invest_evolution.investment.runtimes',
    'shared': 'invest_evolution.investment.shared',
}


def load_package(name: str) -> ModuleType:
    return import_module(PACKAGE_MODULES[name])


def __getattr__(name: str) -> ModuleType:
    if name in PACKAGE_MODULES:
        module = load_package(name)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(PACKAGE_MODULES))


__all__ = [*PACKAGE_MODULES, 'PACKAGE_MODULES', 'load_package']
