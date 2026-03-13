"""Route group adapters for the Phase 6 interface layer."""

from app.interfaces.web.routes.command import register_runtime_command_routes
from app.interfaces.web.routes.data import register_runtime_data_routes
from app.interfaces.web.routes.ops import register_runtime_ops_routes
from app.interfaces.web.routes.read import register_runtime_read_routes

__all__ = [
    "register_runtime_read_routes",
    "register_runtime_ops_routes",
    "register_runtime_data_routes",
    "register_runtime_command_routes",
]
