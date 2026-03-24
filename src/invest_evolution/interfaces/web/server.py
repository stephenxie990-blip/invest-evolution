"""Canonical Flask server and route registration surface."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import fcntl
import hmac
import inspect
import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from queue import Full
from typing import Any

from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

import invest_evolution.config as config_module
from invest_evolution.application.runtime_contracts import (
    RUNTIME_CONTRACT_PUBLIC_PATHS,
    load_runtime_contract_document,
)
from invest_evolution.common.environment import ensure_environment
from invest_evolution.config.control_plane import EvolutionConfigService
from invest_evolution.interfaces.web.contracts import (
    serve_runtime_contract_document as serve_interface_runtime_contract_document,
)
from invest_evolution.interfaces.web.presentation import (
    build_contract_payload_response as build_interface_contract_payload_response,
)
from invest_evolution.interfaces.web.presentation import (
    build_data_source_unavailable_response,
    build_json_error_response as _build_json_error_response,
    parse_value_or_400,
    respond_with_display as respond_with_interface_display,
)
from invest_evolution.interfaces.web.runtime import (
    DelegatingRuntimeFacade,
    InProcessRuntimeFacade,
    StateBackedRuntimeFacade,
    WebRuntimeEphemeralState,
    load_default_commander_runtime_types,
)

RuntimeRouteRegistrar = Callable[..., None]

logger = logging.getLogger(__name__)

_DEFAULT_WEB_RUNTIME_ASYNC_TIMEOUT_SEC = 600
_WEB_RUNTIME_ASYNC_BRIDGE_EXCEPTIONS = (
    RuntimeError,
    TimeoutError,
    OSError,
)
_WEB_RUNTIME_BOOTSTRAP_EXCEPTIONS = _WEB_RUNTIME_ASYNC_BRIDGE_EXCEPTIONS + (
    ValueError,
    TypeError,
    LookupError,
    ImportError,
)
_WEB_RUNTIME_LOOP_STOP_EXCEPTIONS = (
    RuntimeError,
    OSError,
)

ROUTE_REGISTRAR_PATHS = {
    "read": "invest_evolution.interfaces.web.routes:register_runtime_read_routes",
    "ops": "invest_evolution.interfaces.web.routes:register_runtime_ops_routes",
    "data": "invest_evolution.interfaces.web.routes:register_runtime_data_routes",
    "command": "invest_evolution.interfaces.web.routes:register_runtime_command_routes",
    "contracts": "invest_evolution.interfaces.web.routes:register_runtime_contract_routes",
}


def _load_registrar(path: str) -> RuntimeRouteRegistrar:
    module_path, function_name = path.split(":", maxsplit=1)
    module = import_module(module_path)
    return getattr(module, function_name)


def _call_registrar(
    registrar: RuntimeRouteRegistrar,
    app: Flask,
    **route_kwargs: Any,
) -> None:
    accepted_parameters = inspect.signature(registrar).parameters
    accepted_kwargs = {
        name: value
        for name, value in route_kwargs.items()
        if name in accepted_parameters
    }
    registrar(app, **accepted_kwargs)


def register_runtime_interface_routes(app: Flask, **route_kwargs: Any) -> None:
    for route_key in (
        "read",
        "ops",
        "data",
        "command",
        "contracts",
    ):
        registrar = _load_registrar(ROUTE_REGISTRAR_PATHS[route_key])
        _call_registrar(registrar, app, **route_kwargs)


# ---------------------------------------------------------------------------
# Async bridge — run async CommanderRuntime methods from sync Flask handlers
# ---------------------------------------------------------------------------

_loop: asyncio.AbstractEventLoop | None = None
_runtime: Any | None = None


def _bootstrap_config_int(name: str, *, default: int, minimum: int = 1) -> int:
    raw_value = getattr(config_module.config, name, default)
    try:
        value = default if raw_value in (None, "") else int(raw_value)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _bootstrap_config_float(
    name: str, *, default: float, minimum: float = 0.1
) -> float:
    raw_value = getattr(config_module.config, name, default)
    try:
        value = default if raw_value in (None, "") else float(raw_value)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


_EVENT_HISTORY_LIMIT = _bootstrap_config_int(
    "web_event_history_limit", default=200, minimum=1
)
_EVENT_BUFFER_LIMIT = _bootstrap_config_int(
    "web_event_buffer_limit", default=512, minimum=1
)
_EVENT_WAIT_TIMEOUT = _bootstrap_config_float(
    "web_event_wait_timeout_sec", default=15.0, minimum=0.1
)
_RATE_LIMIT_MAX_KEYS = _bootstrap_config_int(
    "web_rate_limit_max_keys", default=4096, minimum=1
)

_WEB_STATE = WebRuntimeEphemeralState(
    event_history_limit=_EVENT_HISTORY_LIMIT,
    event_buffer_limit=_EVENT_BUFFER_LIMIT,
)
_event_condition = _WEB_STATE.event_condition
_data_download_lock = _WEB_STATE.data_download_lock
_rate_limit_lock = _WEB_STATE.rate_limit_lock
_HEAVY_RATE_LIMIT_PATHS = {
    "/api/data/download",
}
_runtime_bootstrap_lock = threading.Lock()
_runtime_shutdown_registered = False
_event_dispatcher_thread: threading.Thread | None = None
_in_process_runtime_facade = InProcessRuntimeFacade(
    runtime_getter=lambda: _runtime,
    loop_getter=lambda: _loop,
)


def reset_ephemeral_web_state(*, reset_rate_limits: bool = True) -> None:
    """Test/support hook for clearing module-level request state."""
    _WEB_STATE.reset(reset_rate_limits=reset_rate_limits)
    if not reset_rate_limits:
        return
    try:
        _default_rate_limit_state_file().unlink(missing_ok=True)
    except OSError:
        logger.debug("Failed to clear shared rate limit state", exc_info=True)


def get_ephemeral_web_state() -> WebRuntimeEphemeralState:
    return _WEB_STATE


def set_runtime_facade_override(facade: Any | None) -> None:
    globals()["_runtime_facade"] = facade


def bind_embedded_runtime_context(
    *,
    runtime: Any | None,
    loop: asyncio.AbstractEventLoop | None,
) -> None:
    globals()["_runtime"] = runtime
    globals()["_loop"] = loop


def _project_root_path() -> Path:
    return Path(config_module.PROJECT_ROOT).expanduser().resolve()


def collect_data_status(detail_mode: str = "fast") -> dict[str, Any]:
    from invest_evolution.application.commander.status import (
        collect_data_status as _collect_data_status,
    )

    return _collect_data_status(detail_mode)


def collect_masked_config_payload() -> dict[str, Any]:
    return EvolutionConfigService(
        project_root=_project_root_path(),
        live_config=config_module.config,
    ).get_masked_payload()


def set_event_callback(callback: Any) -> None:
    from invest_evolution.application.train import (
        set_event_callback as _set_event_callback,
    )

    _set_event_callback(callback)


def _commander_classes() -> tuple[type[Any], type[Any]]:
    return load_default_commander_runtime_types()


def _default_runtime_state_file() -> Path:
    return _project_root_path() / "runtime" / "outputs" / "commander" / "state.json"


def _default_runtime_lock_file() -> Path:
    return _project_root_path() / "runtime" / "state" / "commander.lock"


def _default_training_lock_file() -> Path:
    return _project_root_path() / "runtime" / "state" / "training.lock"


def _default_data_download_lock_file() -> Path:
    return _project_root_path() / "runtime" / "state" / "web_data_download.lock"


def _default_rate_limit_state_file() -> Path:
    return _project_root_path() / "runtime" / "state" / "web_rate_limits.json"


def _default_runtime_events_path() -> Path:
    if _runtime is not None:
        return Path(_runtime.cfg.runtime_events_path)
    return _project_root_path() / "runtime" / "state" / "commander_events.jsonl"


def _select_runtime_facade():
    if _runtime_facade is not None:
        return _runtime_facade
    if _runtime is not None:
        return _in_process_runtime_facade
    return _state_backed_runtime_facade


_state_backed_runtime_facade = StateBackedRuntimeFacade(
    project_root_getter=_project_root_path,
    state_file_getter=_default_runtime_state_file,
    runtime_lock_file_getter=_default_runtime_lock_file,
    training_lock_file_getter=_default_training_lock_file,
    runtime_events_path_getter=_default_runtime_events_path,
    data_status_getter=collect_data_status,
    config_payload_getter=collect_masked_config_payload,
)
_runtime_facade = None
_route_runtime_facade = DelegatingRuntimeFacade(facade_getter=_select_runtime_facade)


def _event_sink(event_type: str, data: dict[str, Any]) -> None:
    """事件接收器：仅负责轻量入队，避免影响训练主流程。"""
    _ensure_event_dispatcher()
    try:
        _WEB_STATE.event_buffer.put_nowait(
            {
                "type": event_type,
                "data": dict(data),
            }
        )
    except Full:
        logger.warning("SSE event buffer full, dropping event: %s", event_type)


def _ensure_event_dispatcher() -> None:
    global _event_dispatcher_thread
    if _event_dispatcher_thread is not None and _event_dispatcher_thread.is_alive():
        return
    with _WEB_STATE.event_condition:
        if _event_dispatcher_thread is not None and _event_dispatcher_thread.is_alive():
            return
        t = threading.Thread(
            target=_run_event_dispatch_loop, name="web-sse-dispatcher", daemon=True
        )
        _event_dispatcher_thread = t
        _WEB_STATE.event_dispatcher_started = True
        t.start()


def _run_event_dispatch_loop() -> None:
    try:
        _event_dispatch_loop()
    except Exception:
        logger.exception("Web SSE dispatcher stopped unexpectedly")
    finally:
        with _WEB_STATE.event_condition:
            _WEB_STATE.event_dispatcher_started = False


def _event_dispatch_loop() -> None:
    while True:
        event = _WEB_STATE.event_buffer.get()
        with _WEB_STATE.event_condition:
            next_seq = _WEB_STATE.event_seq + 1
            _WEB_STATE.event_seq = next_seq
            _WEB_STATE.event_history.append(
                {
                    "id": next_seq,
                    "type": event["type"],
                    "data": event["data"],
                }
            )
            _WEB_STATE.event_condition.notify_all()


def _snapshot_events_since(last_id: int) -> tuple[list[dict[str, Any]], int]:
    with _WEB_STATE.event_condition:
        if not _WEB_STATE.event_history:
            return [], last_id
        oldest_id = _WEB_STATE.event_history[0]["id"]
        if last_id < oldest_id - 1:
            last_id = oldest_id - 1
        pending = [event for event in _WEB_STATE.event_history if event["id"] > last_id]
        if pending:
            last_id = pending[-1]["id"]
        return pending, last_id


def _start_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _run_async(coro: Any) -> Any:
    """Submit a coroutine to the background event loop and wait for result."""
    assert _loop is not None
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    timeout_sec = _read_web_runtime_async_timeout_sec()
    try:
        return future.result(timeout=timeout_sec)
    except TimeoutError as exc:
        future.cancel()
        raise RuntimeError(
            f"Commander runtime async bridge timed out after {timeout_sec} seconds"
        ) from exc


def _stop_loop_threadsafe(
    loop: asyncio.AbstractEventLoop,
    *,
    log_message: str,
    log_args: tuple[Any, ...],
) -> None:
    try:
        loop.call_soon_threadsafe(loop.stop)
    except _WEB_RUNTIME_LOOP_STOP_EXCEPTIONS:
        logger.debug(log_message, *log_args, exc_info=True)


_TRUE_VALUES = {"1", "true", "t", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "f", "no", "n", "off"}


def _parse_bool(value: Any, field_name: str) -> bool:
    """Parse common bool-like values from JSON payloads."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"{field_name} must be a boolean (or 0/1)")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    raise ValueError(f"{field_name} must be a boolean")


def _parse_int(
    value: Any,
    field_name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field_name} must be <= {maximum}")
    return parsed


def _build_runtime_not_ready_response():
    return _build_json_error_response(
        (
            "Commander runtime backend is not available. "
            "Start the dedicated runtime service or enable explicit embedded runtime bootstrap."
        ),
        503,
    )


def _parse_request_limit_arg(default: int = 20, maximum: int = 200) -> int:
    raw = request.args.get("limit", default)
    value = _parse_int(raw, "limit")
    return max(1, min(maximum, value))


def _parse_view_arg(value: Any, *, default: str = "json") -> str:
    view = str(value or default).strip().lower()
    if view not in {"json", "human"}:
        raise ValueError("view must be one of: json, human")
    return view


def _serve_runtime_contract_document_response(document_id: str):
    return serve_interface_runtime_contract_document(
        document_id,
        logger=logger,
        load_document=load_runtime_contract_document,
    )


def _read_request_view_arg(*, default: str = "json") -> str:
    return _parse_view_arg(request.args.get("view", default), default=default)


def _parse_request_view_or_400(*, default: str = "json") -> str | Any:
    return parse_value_or_400(lambda: _read_request_view_arg(default=default))


def _read_runtime_event_rows_since(
    path: Path, offset: int
) -> tuple[list[dict[str, Any]], int]:
    start_offset = offset
    try:
        if not path.exists():
            return [], 0
        size = path.stat().st_size
        if size < offset:
            logger.debug(
                "Runtime event stream truncated; resetting offset: path=%s requested_offset=%s size=%s",
                path,
                offset,
                size,
            )
            offset = 0
        with path.open("r", encoding="utf-8") as handle:
            handle.seek(offset)
            chunk = handle.read()
            next_offset = handle.tell()
    except OSError:
        logger.debug(
            "Failed to read runtime event stream: path=%s offset=%s",
            path,
            offset,
            exc_info=True,
        )
        return [], offset

    rows: list[dict[str, Any]] = []
    invalid_json_rows = 0
    invalid_payload_rows = 0
    for raw_line in chunk.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            invalid_json_rows += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            invalid_payload_rows += 1
    if invalid_json_rows or invalid_payload_rows:
        logger.warning(
            "Skipped invalid runtime event row(s) while reading SSE event stream: path=%s start_offset=%s end_offset=%s invalid_json_rows=%s invalid_payload_rows=%s",
            path,
            start_offset,
            next_offset,
            invalid_json_rows,
            invalid_payload_rows,
        )
    return rows, next_offset


def _normalize_chat_session_token(value: Any, *, field_name: str, prefix: str) -> str:
    token = str(value or "").strip()
    if not token:
        return f"{prefix}:{uuid.uuid4().hex}"
    if len(token) > 120:
        raise ValueError(f"{field_name} must be <= 120 characters")
    return token


def shutdown_runtime_services() -> None:
    runtime = _runtime
    loop = _loop
    globals()["_runtime"] = None
    globals()["_loop"] = None
    if runtime is None:
        return
    if loop is not None:
        try:
            future = asyncio.run_coroutine_threadsafe(runtime.stop(), loop)
            future.result(timeout=30)
        except _WEB_RUNTIME_ASYNC_BRIDGE_EXCEPTIONS:
            logger.debug(
                "Failed to stop commander runtime cleanly during shutdown: runtime_type=%s loop_type=%s",
                type(runtime).__name__,
                type(loop).__name__,
                exc_info=True,
            )
        _stop_loop_threadsafe(
            loop,
            log_message="Failed to stop web event loop cleanly during shutdown: runtime_type=%s loop_type=%s",
            log_args=(type(runtime).__name__, type(loop).__name__),
        )


def _register_runtime_shutdown() -> None:
    if _runtime_shutdown_registered:
        return
    atexit.register(shutdown_runtime_services)
    globals()["_runtime_shutdown_registered"] = True


def bootstrap_runtime_services(
    *, host: str, mock: bool = False, source: str = "cli"
) -> Any:
    with _runtime_bootstrap_lock:
        if _runtime is not None and _loop is not None:
            return _runtime

        if not _is_loopback_host(host):
            if not (_is_web_api_auth_required() and _read_web_api_token()):
                raise RuntimeError(
                    "Refusing to bind a non-loopback host without WEB_API_REQUIRE_AUTH=true and WEB_API_TOKEN configured."
                )

        ensure_environment(
            required_modules=["pandas"]
            if mock
            else ["pandas", "requests", "rank_bm25"],
            require_project_python=False,
            validate_requests_stack=not mock,
            component="web embedded runtime",
        )
        set_event_callback(_event_sink)

        CommanderConfig, CommanderRuntime = _commander_classes()
        cfg = CommanderConfig.from_args(argparse.Namespace())
        if mock:
            cfg.mock_mode = True
        cfg.autopilot_enabled = False
        cfg.heartbeat_enabled = False
        cfg.bridge_enabled = False

        runtime = CommanderRuntime(cfg)
        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(
            target=_start_event_loop,
            args=(loop,),
            name=f"web-event-loop:{source}",
            daemon=True,
        )

        globals()["_runtime"] = runtime
        globals()["_loop"] = loop
        loop_thread.start()
        try:
            _run_async(runtime.start())
        except _WEB_RUNTIME_BOOTSTRAP_EXCEPTIONS:
            logger.exception(
                "Commander runtime bootstrap failed: host=%s mock=%s source=%s runtime_type=%s loop_thread=%s",
                host,
                mock,
                source,
                type(runtime).__name__,
                loop_thread.name,
            )
            _stop_loop_threadsafe(
                loop,
                log_message="Failed to stop event loop after bootstrap error: host=%s mock=%s source=%s loop_type=%s",
                log_args=(host, mock, source, type(loop).__name__),
            )
            globals()["_runtime"] = None
            globals()["_loop"] = None
            raise

        _register_runtime_shutdown()
        return runtime


def bootstrap_embedded_runtime_if_enabled(
    *,
    host: str,
    mock: bool = False,
    source: str = "gunicorn",
) -> Any | None:
    if not _is_embedded_runtime_enabled():
        logger.info(
            "Embedded Commander runtime disabled for source=%s; serving stateless web/API only.",
            source,
        )
        return None

    workers = _read_configured_gunicorn_workers()
    if workers != 1:
        raise RuntimeError(
            "Explicit embedded runtime mode requires GUNICORN_WORKERS=1. "
            "Disable WEB_EMBEDDED_RUNTIME_ENABLED for stateless multi-worker web deployments."
        )
    return bootstrap_runtime_services(host=host, mock=mock, source=source)


def _parse_detail_mode(
    value: Any,
    *,
    default: str = "fast",
    field_name: str = "detail",
    strict: bool = False,
) -> str:
    detail_mode = str(value or default).strip().lower() or default
    if detail_mode in {"fast", "slow"}:
        return detail_mode
    if strict:
        raise ValueError(f"{field_name} must be one of: fast, slow")
    logger.debug(
        "Invalid detail mode; using default: field_name=%s raw_value=%r default=%s",
        field_name,
        value,
        default,
    )
    return default


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)

_PUBLIC_API_PATHS = set(RUNTIME_CONTRACT_PUBLIC_PATHS)
_OPTIONALLY_PUBLIC_READ_PATHS = {
    "/api/status",
}


def _request_trace_id() -> str:
    existing = str(request.environ.get("invest.trace_id") or "").strip()
    if existing:
        return existing
    header_value = str(
        request.headers.get("X-Trace-Id")
        or request.headers.get("X-Request-Id")
        or ""
    ).strip()
    trace_id = header_value or f"web:{uuid.uuid4().hex[:16]}"
    request.environ["invest.trace_id"] = trace_id
    return trace_id


def _read_current_web_config():
    return config_module.config


def _is_heavy_rate_limit_path(path: str) -> bool:
    normalized = str(path or "")
    if normalized in _HEAVY_RATE_LIMIT_PATHS:
        return True
    return normalized.startswith("/api/lab/training/plans/") and normalized.endswith(
        "/execute"
    )


def _read_config_value(name: str, default: Any) -> Any:
    return getattr(_read_current_web_config(), name, default)


def _read_config_string(name: str, *, default: str = "") -> str:
    return str(_read_config_value(name, default) or "").strip()


def _read_config_bool(name: str, *, default: bool = False) -> bool:
    raw_value = _read_config_value(name, default)
    if raw_value in (None, ""):
        return default
    try:
        return _parse_bool(raw_value, name)
    except ValueError:
        logger.warning(
            "Invalid web config boolean; using default: field=%s raw_value=%r default=%s",
            name,
            raw_value,
            default,
        )
        return default


def _read_config_int_with_minimum(
    name: str,
    *,
    default: int,
    minimum: int = 1,
    warning_scope: str = "web config",
) -> int:
    raw_value = _read_config_value(name, default)
    value = default if raw_value in (None, "") else int(raw_value)
    if value < minimum:
        logger.warning(
            "Invalid %s; clamping to minimum: field=%s raw_value=%r minimum=%s",
            warning_scope,
            name,
            raw_value,
            minimum,
        )
        return minimum
    return value


def _read_env_string(name: str, *, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _read_env_int(name: str, *, default: int, minimum: int | None = None) -> int:
    raw_value = _read_env_string(name, default=str(default))
    value = default if raw_value == "" else _parse_int(raw_value, name, minimum=minimum)
    return value


def _read_env_bool(name: str, *, default: bool = False) -> bool:
    raw_value = _read_env_string(name, default="")
    if not raw_value:
        return default
    normalized = raw_value.lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise RuntimeError(f"{name} must be a boolean when configured.")


def _is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1", "[::1]"}


def _parse_bind_host(bind: str) -> str:
    token = str(bind or "").split(",", 1)[0].strip()
    if not token:
        return "127.0.0.1"
    if token.startswith("unix:"):
        return "localhost"
    if token.startswith("[") and "]" in token:
        return token[1:].split("]", 1)[0]
    if token.count(":") == 1:
        return token.rsplit(":", 1)[0]
    return token


def _read_configured_gunicorn_host() -> str:
    return _parse_bind_host(_read_env_string("GUNICORN_BIND", default="127.0.0.1:8080"))


def _read_configured_gunicorn_workers() -> int:
    return _read_env_int("GUNICORN_WORKERS", default=2, minimum=1)


def _is_embedded_runtime_enabled() -> bool:
    return _read_env_bool("WEB_EMBEDDED_RUNTIME_ENABLED", default=False)


def _read_web_api_token() -> str:
    return _read_config_string("web_api_token", default="")


def _is_web_api_auth_required() -> bool:
    return _read_config_bool("web_api_require_auth", default=False)


def _is_web_api_public_read_enabled() -> bool:
    return _read_config_bool("web_api_public_read_enabled", default=False)


def _is_web_rate_limit_enabled() -> bool:
    return _read_config_bool("web_rate_limit_enabled", default=True)


def _read_web_rate_limit_window_sec() -> int:
    return _read_config_int_with_minimum(
        "web_rate_limit_window_sec",
        default=60,
        minimum=1,
        warning_scope="web rate limit config",
    )


def _read_web_rate_limit_read_max() -> int:
    return _read_config_int_with_minimum(
        "web_rate_limit_read_max",
        default=120,
        minimum=1,
        warning_scope="web rate limit config",
    )


def _read_web_rate_limit_write_max() -> int:
    return _read_config_int_with_minimum(
        "web_rate_limit_write_max",
        default=20,
        minimum=1,
        warning_scope="web rate limit config",
    )


def _read_web_rate_limit_heavy_max() -> int:
    return _read_config_int_with_minimum(
        "web_rate_limit_heavy_max",
        default=5,
        minimum=1,
        warning_scope="web rate limit config",
    )


def _read_web_rate_limit_max_keys() -> int:
    return _read_config_int_with_minimum(
        "web_rate_limit_max_keys",
        default=_RATE_LIMIT_MAX_KEYS,
        minimum=1,
        warning_scope="web rate limit config",
    )


def _is_shared_rate_limit_state_enabled() -> bool:
    raw_workers = _read_env_string("GUNICORN_WORKERS", default="")
    if not raw_workers:
        return False
    return _read_configured_gunicorn_workers() > 1


def _serialize_rate_limit_key(key: tuple[str, str, str]) -> str:
    return json.dumps(list(key), ensure_ascii=False, separators=(",", ":"))


def _consume_rate_limit_from_shared_state(
    *,
    key: tuple[str, str, str],
    max_requests: int,
    now: float,
    window_start: float,
    window_sec: int,
) -> tuple[bool, int]:
    state_file = _default_rate_limit_state_file()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    serialized_key = _serialize_rate_limit_key(key)
    with state_file.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            raw_payload = handle.read().strip()
            payload = json.loads(raw_payload) if raw_payload else {}
            events_payload = (
                payload.get("events", {}) if isinstance(payload, dict) else {}
            )
            events_by_key = (
                dict(events_payload) if isinstance(events_payload, dict) else {}
            )
            stale_keys = [
                item_key
                for item_key, queue in events_by_key.items()
                if not isinstance(queue, list) or not queue
            ]
            for item_key in stale_keys:
                events_by_key.pop(item_key, None)
            queue = [
                float(item)
                for item in list(events_by_key.get(serialized_key) or [])
                if isinstance(item, (int, float)) and float(item) > window_start
            ]
            if len(queue) >= max_requests:
                retry_after = max(1, int(queue[0] + window_sec - now))
                return False, retry_after
            queue.append(now)
            events_by_key[serialized_key] = queue
            for item_key, item_queue in list(events_by_key.items()):
                if item_key == serialized_key:
                    continue
                normalized_queue = [
                    float(item)
                    for item in list(item_queue or [])
                    if isinstance(item, (int, float)) and float(item) > window_start
                ]
                if normalized_queue:
                    events_by_key[item_key] = normalized_queue
                else:
                    events_by_key.pop(item_key, None)
            overflow = len(events_by_key) - _read_web_rate_limit_max_keys()
            if overflow > 0:
                ranked = sorted(
                    events_by_key.items(),
                    key=lambda item: item[1][-1] if item[1] else 0.0,
                )
                for item_key, _ in ranked[:overflow]:
                    events_by_key.pop(item_key, None)
            handle.seek(0)
            handle.truncate()
            json.dump({"events": events_by_key}, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return True, 0


def _read_web_event_wait_timeout_sec() -> float:
    raw_value = _read_config_value("web_event_wait_timeout_sec", _EVENT_WAIT_TIMEOUT)
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid web event config; fallback to default: field=%s raw_value=%r default=%s",
            "web_event_wait_timeout_sec",
            raw_value,
            _EVENT_WAIT_TIMEOUT,
        )
        return _EVENT_WAIT_TIMEOUT
    if value < 0.1:
        logger.warning(
            "Invalid web event config; clamping to minimum: field=%s raw_value=%r minimum=%s",
            "web_event_wait_timeout_sec",
            raw_value,
            0.1,
        )
        return 0.1
    return value


def _read_web_runtime_async_timeout_sec() -> int:
    return _read_config_int_with_minimum(
        "web_runtime_async_timeout_sec",
        default=_DEFAULT_WEB_RUNTIME_ASYNC_TIMEOUT_SEC,
        minimum=1,
        warning_scope="web runtime bridge config",
    )


_LEGACY_PRIVATE_HELPER_ALIASES: dict[str, str] = {
    "_configured_gunicorn_host": "_read_configured_gunicorn_host",
    "_configured_gunicorn_workers": "_read_configured_gunicorn_workers",
    "_embedded_runtime_enabled": "_is_embedded_runtime_enabled",
    "_web_api_token": "_read_web_api_token",
    "_web_api_require_auth": "_is_web_api_auth_required",
    "_web_api_public_read_enabled": "_is_web_api_public_read_enabled",
    "_web_rate_limit_enabled": "_is_web_rate_limit_enabled",
    "_web_rate_limit_window_sec": "_read_web_rate_limit_window_sec",
    "_web_rate_limit_read_max": "_read_web_rate_limit_read_max",
    "_web_rate_limit_write_max": "_read_web_rate_limit_write_max",
    "_web_rate_limit_heavy_max": "_read_web_rate_limit_heavy_max",
    "_web_rate_limit_max_keys": "_read_web_rate_limit_max_keys",
    "_web_event_wait_timeout_sec": "_read_web_event_wait_timeout_sec",
    "_web_runtime_async_timeout_sec": "_read_web_runtime_async_timeout_sec",
}


def _configured_gunicorn_host() -> str:
    return _read_configured_gunicorn_host()


def _configured_gunicorn_workers() -> int:
    return _read_configured_gunicorn_workers()


def _embedded_runtime_enabled() -> bool:
    return _is_embedded_runtime_enabled()


def _web_api_token() -> str:
    return _read_web_api_token()


def _web_api_require_auth() -> bool:
    return _is_web_api_auth_required()


def _web_api_public_read_enabled() -> bool:
    return _is_web_api_public_read_enabled()


def _web_rate_limit_enabled() -> bool:
    return _is_web_rate_limit_enabled()


def _web_rate_limit_window_sec() -> int:
    return _read_web_rate_limit_window_sec()


def _web_rate_limit_read_max() -> int:
    return _read_web_rate_limit_read_max()


def _web_rate_limit_write_max() -> int:
    return _read_web_rate_limit_write_max()


def _web_rate_limit_heavy_max() -> int:
    return _read_web_rate_limit_heavy_max()


def _web_rate_limit_max_keys() -> int:
    return _read_web_rate_limit_max_keys()


def _web_event_wait_timeout_sec() -> float:
    return _read_web_event_wait_timeout_sec()


def _web_runtime_async_timeout_sec() -> int:
    return _read_web_runtime_async_timeout_sec()


def __getattr__(name: str) -> Any:
    # Keep old private helper names stable for tests and thin wrappers that still
    # access the pre-refactor surface after importing a partially-updated module.
    target_name = _LEGACY_PRIVATE_HELPER_ALIASES.get(name)
    if target_name:
        return globals()[target_name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _read_request_token() -> str:
    auth_header = str(request.headers.get("Authorization", "") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return str(request.headers.get("X-Invest-Token", "") or "").strip()


def _request_requires_auth() -> bool:
    path = str(request.path or "")
    if not path.startswith("/api/"):
        return False
    if path in _PUBLIC_API_PATHS:
        return False
    if (
        request.method in {"GET", "HEAD", "OPTIONS"}
        and path in _OPTIONALLY_PUBLIC_READ_PATHS
        and (_is_web_api_public_read_enabled() or not _is_web_api_auth_required())
    ):
        return False
    if not _is_loopback_host(_read_client_identifier()):
        return True
    return _is_web_api_auth_required()


def _read_client_identifier() -> str:
    remote_addr = str(request.remote_addr or "").strip()
    if _is_loopback_host(remote_addr):
        real_ip = (
            str(request.headers.get("X-Real-IP", "") or "").split(",", 1)[0].strip()
        )
        if real_ip:
            return real_ip
    return remote_addr or "unknown"


def _build_rate_limit_bucket() -> tuple[str, int] | None:
    if not _is_web_rate_limit_enabled():
        return None
    path = str(request.path or "")
    if not path.startswith("/api/"):
        return None
    if _is_heavy_rate_limit_path(path):
        return ("heavy", _read_web_rate_limit_heavy_max())
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        return ("write", _read_web_rate_limit_write_max())
    return ("read", _read_web_rate_limit_read_max())


def _read_rate_limit_route_scope() -> str:
    matched_rule = getattr(request, "url_rule", None)
    if matched_rule is not None:
        normalized = str(getattr(matched_rule, "rule", "") or "").strip()
        if normalized.startswith("/api/"):
            return normalized
    return str(request.path or "")


def _consume_rate_limit() -> tuple[bool, int] | None:
    bucket = _build_rate_limit_bucket()
    if bucket is None:
        return None
    scope, max_requests = bucket
    key = (_read_client_identifier(), scope, _read_rate_limit_route_scope())
    now = time.time()
    window_sec = _read_web_rate_limit_window_sec()
    window_start = now - window_sec
    if _is_shared_rate_limit_state_enabled():
        return _consume_rate_limit_from_shared_state(
            key=key,
            max_requests=max_requests,
            now=now,
            window_start=window_start,
            window_sec=window_sec,
        )
    with _rate_limit_lock:
        _WEB_STATE.compact_rate_limit_events(
            window_start=window_start,
            max_keys=_read_web_rate_limit_max_keys(),
        )
        queue = _WEB_STATE.rate_limit_events.setdefault(key, deque())
        while queue and queue[0] <= window_start:
            queue.popleft()
        if len(queue) >= max_requests:
            retry_after = max(
                1,
                int(queue[0] + _read_web_rate_limit_window_sec() - now),
            )
            return False, retry_after
        queue.append(now)
    return True, 0


@app.before_request
def _enforce_api_auth():
    if not _request_requires_auth():
        return None
    expected_token = _read_web_api_token()
    if not expected_token:
        return _build_json_error_response(
            "web api auth is enabled but token is not configured",
            503,
        )
    provided_token = _read_request_token()
    if not provided_token:
        return _build_json_error_response("authentication required", 401)
    if not hmac.compare_digest(provided_token, expected_token):
        return _build_json_error_response("invalid authentication token", 403)
    return None


@app.after_request
def _attach_trace_headers(response):
    response.headers.setdefault("X-Trace-Id", _request_trace_id())
    return response


@app.errorhandler(Exception)
def _handle_unexpected_web_exception(exc: Exception):
    trace_id = _request_trace_id()
    if isinstance(exc, HTTPException):
        if not str(request.path or "").startswith("/api/"):
            return exc
        return _build_json_error_response(
            str(getattr(exc, "description", exc)),
            int(exc.code or 500),
            status="error",
            error_code=f"WEB_HTTP_{int(exc.code or 500)}",
            trace_id=trace_id,
        )
    logger.exception(
        "Unhandled web route error: method=%s path=%s trace_id=%s",
        request.method,
        request.path,
        trace_id,
    )
    return _build_json_error_response(
        "internal server error",
        500,
        status="error",
        error_code="WEB_UNHANDLED",
        trace_id=trace_id,
        retryable=False,
    )


@app.before_request
def _enforce_rate_limit():
    verdict = _consume_rate_limit()
    if verdict is None:
        return None
    allowed, retry_after = verdict
    if allowed:
        return None
    response = _build_json_error_response(
        "rate limit exceeded",
        429,
        retry_after_sec=retry_after,
        window_sec=_read_web_rate_limit_window_sec(),
    )
    response.headers["Retry-After"] = str(retry_after)
    return response


def _status_snapshot(detail_mode: str) -> dict[str, Any] | tuple[Any, int]:
    return _select_runtime_facade().status_snapshot(
        detail_mode=detail_mode,
        runtime_not_ready_response=_build_runtime_not_ready_response,
    )


def _status_response(*, detail_mode: str, route_mode: str | None = None):
    snapshot = _status_snapshot(detail_mode)
    if isinstance(snapshot, tuple):
        return snapshot
    view = _parse_request_view_or_400(default="json")
    if not isinstance(view, str):
        return view
    if route_mode is None:
        return respond_with_interface_display(snapshot, view=view)
    route_payload = dict(snapshot)
    route_payload["mode"] = route_mode
    return respond_with_interface_display(route_payload, view=view)


register_runtime_interface_routes(
    app,
    runtime_facade=_route_runtime_facade,
    parse_detail_mode=_parse_detail_mode,
    status_response=_status_response,
    runtime_not_ready_response=_build_runtime_not_ready_response,
    request_view_arg=_read_request_view_arg,
    parse_view_arg=_parse_view_arg,
    parse_limit_arg=_parse_request_limit_arg,
    parse_bool=_parse_bool,
    parse_int=_parse_int,
    respond_with_display=respond_with_interface_display,
    build_contract_payload_response=build_interface_contract_payload_response,
    serve_contract_document=_serve_runtime_contract_document_response,
    build_data_source_unavailable_response=build_data_source_unavailable_response,
    logger=logger,
    data_download_lock_file_getter=_default_data_download_lock_file,
    data_download_lock=_data_download_lock,
    get_data_download_running=lambda: _WEB_STATE.data_download_running,
    set_data_download_running=lambda value: setattr(
        _WEB_STATE, "data_download_running", bool(value)
    ),
    thread_factory=lambda target: threading.Thread(target=target, daemon=True),
    normalize_chat_session_token=_normalize_chat_session_token,
    run_async=lambda coro: _run_async(coro),
)


@app.route("/")
def index():
    return jsonify(
        {
            "service": "invest-api",
            "status": "ok",
            "message": "Web 仅提供无状态 API/SSE；人类主入口请优先使用 commander CLI。",
            "human_entrypoint": "invest-commander",
            "batch_entrypoint": "invest-train",
            "entrypoints": {
                "chat": "/api/chat",
                "status": "/api/status",
                "events": "/api/events",
                "healthz": "/healthz",
                "contracts": "/api/contracts/runtime-v2",
            },
        }
    )


@app.route("/healthz")
def healthz():
    return jsonify(
        _select_runtime_facade().build_health_payload(
            event_buffer_size=_WEB_STATE.event_buffer.qsize(),
            event_history_size=len(_WEB_STATE.event_history),
            event_dispatcher_started=bool(_WEB_STATE.event_dispatcher_started),
        )
    )


def create_app() -> Flask:
    return app


# ---- SSE (Server-Sent Events) ----


@app.route("/api/events")
def api_events():
    """SSE runtime event stream backed by runtime/state event artifacts."""

    def generate():
        yield 'event: connected\ndata: {"status":"connected"}\n\n'
        path = _default_runtime_events_path()
        offset = 0
        while True:
            rows, offset = _read_runtime_event_rows_since(path, offset)
            if not rows:
                time.sleep(_read_web_event_wait_timeout_sec())
                yield ": keepalive\n\n"
                continue
            for event in rows:
                event_name = str(event.get("event") or "runtime_event")
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    payload = dict(event)
                yield (
                    f"id: {event.get('id', '')}\n"
                    f"event: {event_name}\n"
                    f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                )

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="投资进化系统无状态 Web/API 入口")
    parser.add_argument("--port", type=int, default=8080, help="服务端口 (默认 8080)")
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址 (默认 127.0.0.1)")
    parser.add_argument(
        "--mock", action="store_true", help="使用模拟数据 (无需真实行情)"
    )
    parser.add_argument(
        "--embedded-runtime",
        action="store_true",
        help="显式启用进程内 Commander runtime（仅 compat/dev 模式）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    if args.embedded_runtime:
        bootstrap_runtime_services(host=args.host, mock=args.mock, source="cli")
    else:
        bootstrap_embedded_runtime_if_enabled(
            host=args.host, mock=args.mock, source="cli"
        )
    if not _is_loopback_host(args.host):
        logger.warning(
            "Binding non-loopback host via Flask development server. Production should use the split topology "
            "or an explicitly enabled embedded-runtime deployment."
        )

    print(f"""
╔══════════════════════════════════════════════════╗
║     投资进化系统无状态 Web/API 已启动                ║
║                                                  ║
║   🌐  http://{args.host}:{args.port}                    ║
║   📊  Mock 模式: {"✅ 已开启" if args.mock else "❌ 未开启"}                      ║
║   🔌  Embedded runtime: {"✅ 已开启" if args.embedded_runtime else "❌ 关闭"}            ║
║   🧭  人类主入口: invest-commander                 ║
║                                                  ║
║   按 Ctrl+C 停止服务                               ║
╚══════════════════════════════════════════════════╝
""")

    app.run(host=args.host, port=args.port, debug=False, threaded=True)


__all__ = [
    "ROUTE_REGISTRAR_PATHS",
    "bind_embedded_runtime_context",
    "bootstrap_embedded_runtime_if_enabled",
    "bootstrap_runtime_services",
    "create_app",
    "get_ephemeral_web_state",
    "register_runtime_interface_routes",
    "reset_ephemeral_web_state",
    "set_runtime_facade_override",
]


if __name__ == "__main__":
    main()
