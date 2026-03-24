"""Interface-layer entrypoints."""

from __future__ import annotations

from importlib import import_module

PACKAGE_MODULES = {
    "web": "invest_evolution.interfaces.web",
}


def __getattr__(name: str):
    if name in PACKAGE_MODULES:
        module = import_module(PACKAGE_MODULES[name])
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(PACKAGE_MODULES))


__all__ = ["web"]
