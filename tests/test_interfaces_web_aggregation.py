from pathlib import Path

from invest_evolution.interfaces.web import register_runtime_interface_routes
from invest_evolution.interfaces.web import routes, runtime, server

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src" / "invest_evolution"


def test_web_registry_and_route_modules_are_available():
    assert callable(register_runtime_interface_routes)
    assert callable(server.register_runtime_interface_routes)
    assert callable(routes.register_runtime_read_routes)
    assert callable(routes.register_runtime_ops_routes)
    assert callable(routes.register_runtime_data_routes)
    assert callable(routes.register_runtime_command_routes)
    assert callable(routes.register_runtime_contract_routes)
    assert server.ROUTE_REGISTRAR_PATHS["read"] == "invest_evolution.interfaces.web.routes:register_runtime_read_routes"
    assert server.ROUTE_REGISTRAR_PATHS["ops"] == "invest_evolution.interfaces.web.routes:register_runtime_ops_routes"
    assert server.ROUTE_REGISTRAR_PATHS["data"] == "invest_evolution.interfaces.web.routes:register_runtime_data_routes"
    assert server.ROUTE_REGISTRAR_PATHS["command"] == "invest_evolution.interfaces.web.routes:register_runtime_command_routes"
    assert server.ROUTE_REGISTRAR_PATHS["contracts"] == "invest_evolution.interfaces.web.routes:register_runtime_contract_routes"
    assert hasattr(runtime, "RuntimeFacade")
    assert hasattr(runtime, "WebRuntimeEphemeralState")


def test_web_wsgi_entrypoint_is_pure_import_surface():
    wsgi_source = (SRC_ROOT / "interfaces" / "web" / "wsgi.py").read_text(encoding="utf-8")
    assert "from invest_evolution.interfaces.web.server import app" in wsgi_source
    assert "bootstrap_embedded_runtime_if_enabled" not in wsgi_source


def test_web_package_init_keeps_server_surface_lazy():
    init_source = (SRC_ROOT / "interfaces" / "web" / "__init__.py").read_text(encoding="utf-8")

    header = "\n".join(init_source.splitlines()[:6])
    assert "from .server import" not in header
