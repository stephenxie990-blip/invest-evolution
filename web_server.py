import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio as asyncio
    import threading as threading

    from app.commander import CommanderConfig  # noqa: F401
    from app.commander import CommanderRuntime  # noqa: F401
    from app.web_server import _ensure_event_dispatcher  # noqa: F401
    from app.web_server import _event_condition  # noqa: F401
    from app.web_server import _event_history  # noqa: F401
    from app.web_server import _event_seq  # noqa: F401
    from app.web_server import _event_sink  # noqa: F401
    from app.web_server import _loop  # noqa: F401
    from app.web_server import _rate_limit_events  # noqa: F401
    from app.web_server import _runtime  # noqa: F401
    from app.web_server import _runtime_shutdown_registered  # noqa: F401
    from app.web_server import _snapshot_events_since  # noqa: F401
    from app.web_server import app  # noqa: F401
    from app.web_server import bootstrap_runtime_services  # noqa: F401
    from app.web_server import main  # noqa: F401
    from app.web_server import shutdown_runtime_services  # noqa: F401

_impl = import_module("app.web_server")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
