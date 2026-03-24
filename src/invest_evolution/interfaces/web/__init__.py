"""Web interface package for Invest Evolution."""

from __future__ import annotations

from typing import Any

__all__ = ["register_runtime_interface_routes"]


def register_runtime_interface_routes(*args: Any, **kwargs: Any) -> Any:
    from .server import register_runtime_interface_routes as _register_runtime_interface_routes

    return _register_runtime_interface_routes(*args, **kwargs)
