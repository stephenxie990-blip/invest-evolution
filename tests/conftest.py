import sys
from importlib import import_module
from pathlib import Path

import pytest


SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
if str(SRC_ROOT) not in sys.path:
    # Keep src-package imports available during the migration window.
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture(autouse=True)
def _reset_web_rate_limit_events(monkeypatch):
    # Keep module-level web state behind a single reset hook so tests do not
    # reach into internal globals directly.
    web_server = None
    for module_name in ("invest_evolution.interfaces.web.server", "web_server"):
        try:
            web_server = import_module(module_name)
            break
        except Exception:
            continue
    if web_server is not None and hasattr(web_server, "reset_ephemeral_web_state"):
        web_server.reset_ephemeral_web_state()
