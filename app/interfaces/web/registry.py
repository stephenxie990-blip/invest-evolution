"""Phase 6 web registry that routes through the interface layer."""

from __future__ import annotations

import inspect
from typing import Any, Callable

from flask import Flask

from app.interfaces.web.routes.command import register_runtime_command_routes
from app.interfaces.web.routes.data import register_runtime_data_routes
from app.interfaces.web.routes.ops import register_runtime_ops_routes
from app.interfaces.web.routes.read import register_runtime_read_routes

RouteRegistrar = Callable[..., None]


def _call_registrar(registrar: RouteRegistrar, app: Flask, **route_kwargs: Any) -> None:
    accepted_parameters = inspect.signature(registrar).parameters
    accepted_kwargs = {
        name: value
        for name, value in route_kwargs.items()
        if name in accepted_parameters
    }
    registrar(app, **accepted_kwargs)


def register_runtime_interface_routes(app: Flask, **route_kwargs: Any) -> None:
    for registrar in (
        register_runtime_read_routes,
        register_runtime_ops_routes,
        register_runtime_data_routes,
        register_runtime_command_routes,
    ):
        _call_registrar(registrar, app, **route_kwargs)
