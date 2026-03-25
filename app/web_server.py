"""
投资进化系统 - API / Commander 服务器

Flask 应用，包装 CommanderRuntime 提供 REST API、事件流与自然语言对话入口。
启动方式：
    source .venv/bin/activate
    python web_server.py [--mock] [--port 8080]
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
from collections import deque
import config as config_module
import time
import hmac
import json
import logging
import os
from queue import Full, Queue
import threading
from typing import Any
import uuid

from flask import Flask, jsonify, request, Response, stream_with_context

from app.commander import CommanderConfig, CommanderRuntime
from app.interfaces.web.contracts import (
    build_runtime_contracts_payload as build_interface_runtime_contracts_payload,
    serve_runtime_contract_document as serve_interface_runtime_contract_document,
)
from app.interfaces.web import register_runtime_interface_routes
from app.interfaces.web.presentation import (
    jsonify_contract_payload as jsonify_interface_contract_payload,
    respond_with_display as respond_with_interface_display,
)
from app.runtime_contract_catalog import (
    RUNTIME_CONTRACT_PUBLIC_PATHS,
    load_runtime_contract_document,
)
from app.train import set_event_callback
from market_data import DataSourceUnavailableError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Async bridge — run async CommanderRuntime methods from sync Flask handlers
# ---------------------------------------------------------------------------

_loop: asyncio.AbstractEventLoop | None = None
_runtime: CommanderRuntime | None = None

# SSE 事件队列
_EVENT_HISTORY_LIMIT = 200
_EVENT_BUFFER_LIMIT = 512
_EVENT_WAIT_TIMEOUT = 15.0

_event_history: deque[dict[str, Any]] = deque(maxlen=_EVENT_HISTORY_LIMIT)
_event_buffer: Queue[dict[str, Any]] = Queue(maxsize=_EVENT_BUFFER_LIMIT)
_event_condition = threading.Condition()
_event_dispatcher_started = False
_event_seq = 0

_data_download_lock = threading.Lock()
_data_download_running = False

_rate_limit_lock = threading.Lock()
_rate_limit_events: dict[tuple[str, str, str], deque[float]] = {}
_HEAVY_RATE_LIMIT_PATHS = {
    "/api/train",
    "/api/data/download",
}
_runtime_bootstrap_lock = threading.Lock()
_runtime_shutdown_registered = False


def _event_sink(event_type: str, data: dict):
    """事件接收器：仅负责轻量入队，避免影响训练主流程。"""
    _ensure_event_dispatcher()
    try:
        _event_buffer.put_nowait({
            "type": event_type,
            "data": dict(data),
        })
    except Full:
        logger.warning("SSE event buffer full, dropping event: %s", event_type)


def _ensure_event_dispatcher() -> None:
    if _event_dispatcher_started:
        return
    with _event_condition:
        if _event_dispatcher_started:
            return
        t = threading.Thread(target=_event_dispatch_loop, name="web-sse-dispatcher", daemon=True)
        t.start()
        globals()["_event_dispatcher_started"] = True


def _event_dispatch_loop() -> None:
    while True:
        event = _event_buffer.get()
        with _event_condition:
            next_seq = _event_seq + 1
            globals()["_event_seq"] = next_seq
            _event_history.append({
                "id": next_seq,
                "type": event["type"],
                "data": event["data"],
            })
            _event_condition.notify_all()


def _snapshot_events_since(last_id: int) -> tuple[list[dict[str, Any]], int]:
    with _event_condition:
        if not _event_history:
            return [], last_id
        oldest_id = _event_history[0]["id"]
        if last_id < oldest_id - 1:
            last_id = oldest_id - 1
        pending = [event for event in _event_history if event["id"] > last_id]
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
    return future.result(timeout=600)


def _data_source_unavailable_response(exc: DataSourceUnavailableError):
    return jsonify(exc.to_dict()), 503


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


def _parse_int(value: Any, field_name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field_name} must be <= {maximum}")
    return parsed


def _jsonify_contract_payload(payload: Any, status_code: int = 200):
    return jsonify_interface_contract_payload(payload, status_code=status_code)


def _runtime_not_ready_response():
    return jsonify({
        "error": "Commander runtime is not initialized. Start the supported web entrypoint so runtime bootstrap can complete.",
    }), 503


def _parse_limit_arg(default: int = 20, maximum: int = 200) -> int:
    raw = request.args.get("limit", default)
    value = _parse_int(raw, "limit")
    return max(1, min(maximum, value))


def _parse_view_arg(value: Any, *, default: str = "json") -> str:
    view = str(value or default).strip().lower()
    if view not in {"json", "human"}:
        raise ValueError("view must be one of: json, human")
    return view


def _respond_with_display(payload: Any, *, status_code: int = 200, view: str = "json"):
    return respond_with_interface_display(payload, status_code=status_code, view=view)


def _build_runtime_contracts_payload():
    return jsonify(build_interface_runtime_contracts_payload())


def _serve_runtime_contract_document(document_id: str):
    return serve_interface_runtime_contract_document(
        document_id,
        logger=logger,
        load_document=load_runtime_contract_document,
    )


def _request_view_arg(*, default: str = "json") -> str:
    return _parse_view_arg(request.args.get("view", default), default=default)


def _removed_web_ui_response(path: str):
    payload = {
        "error": "web ui has been removed",
        "removed_path": path,
        "message": "请改用 /api/chat、/api/status、/api/events 或 commander CLI。",
        "entrypoints": {
            "chat": "/api/chat",
            "status": "/api/status",
            "events": "/api/events",
            "healthz": "/healthz",
        },
    }
    return jsonify(payload), 410


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
        except Exception:
            logger.debug("Failed to stop commander runtime cleanly during shutdown", exc_info=True)
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            logger.debug("Failed to stop web event loop cleanly during shutdown", exc_info=True)


def _register_runtime_shutdown() -> None:
    if _runtime_shutdown_registered:
        return
    atexit.register(shutdown_runtime_services)
    globals()["_runtime_shutdown_registered"] = True


def bootstrap_runtime_services(*, host: str, mock: bool = False, source: str = "cli") -> CommanderRuntime:
    with _runtime_bootstrap_lock:
        if _runtime is not None and _loop is not None:
            return _runtime

        if not _is_loopback_host(host):
            if not (_web_api_require_auth() and _web_api_token()):
                raise RuntimeError(
                    "Refusing to bind a non-loopback host without WEB_API_REQUIRE_AUTH=true and WEB_API_TOKEN configured."
                )

        if source == "wsgi":
            workers = _configured_gunicorn_workers()
            if workers != 1:
                raise RuntimeError(
                    "In-process Commander runtime only supports a single gunicorn worker. Set GUNICORN_WORKERS=1."
                )

        set_event_callback(_event_sink)

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
        except Exception:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                logger.debug("Failed to stop event loop after bootstrap error", exc_info=True)
            globals()["_runtime"] = None
            globals()["_loop"] = None
            raise

        _register_runtime_shutdown()
        return runtime


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
    return default

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)

_PUBLIC_API_PATHS = set(RUNTIME_CONTRACT_PUBLIC_PATHS)
_OPTIONALLY_PUBLIC_READ_PATHS = {
    "/api/status",
    "/api/lab/status/quick",
    "/api/lab/status/deep",
}


def _current_web_config():
    return config_module.config


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


def _configured_gunicorn_host() -> str:
    return _parse_bind_host(os.environ.get("GUNICORN_BIND", "0.0.0.0:8080"))


def _configured_worker_env() -> tuple[str, str]:
    for key in ("GUNICORN_WORKERS", "WEB_CONCURRENCY"):
        raw = str(os.environ.get(key, "") or "").strip()
        if raw:
            return key, raw
    return "GUNICORN_WORKERS", "1"


def _configured_gunicorn_workers() -> int:
    field_name, raw = _configured_worker_env()
    return max(1, _parse_int(raw, field_name, minimum=1))


def _web_api_token() -> str:
    return str(getattr(_current_web_config(), "web_api_token", "") or "").strip()


def _web_api_require_auth() -> bool:
    return bool(getattr(_current_web_config(), "web_api_require_auth", False))


def _web_api_public_read_enabled() -> bool:
    return bool(getattr(_current_web_config(), "web_api_public_read_enabled", False))


def _web_rate_limit_enabled() -> bool:
    return bool(getattr(_current_web_config(), "web_rate_limit_enabled", True))


def _web_rate_limit_window_sec() -> int:
    return max(1, int(getattr(_current_web_config(), "web_rate_limit_window_sec", 60) or 60))


def _web_rate_limit_read_max() -> int:
    return max(1, int(getattr(_current_web_config(), "web_rate_limit_read_max", 120) or 120))


def _web_rate_limit_write_max() -> int:
    return max(1, int(getattr(_current_web_config(), "web_rate_limit_write_max", 20) or 20))


def _web_rate_limit_heavy_max() -> int:
    return max(1, int(getattr(_current_web_config(), "web_rate_limit_heavy_max", 5) or 5))


def _extract_request_token() -> str:
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
    if not _web_api_require_auth():
        return False
    if request.method in {"GET", "HEAD", "OPTIONS"} and _web_api_public_read_enabled() and path in _OPTIONALLY_PUBLIC_READ_PATHS:
        return False
    return True


def _client_identifier() -> str:
    remote_addr = str(request.remote_addr or "").strip()
    if _is_loopback_host(remote_addr):
        real_ip = str(request.headers.get("X-Real-IP", "") or "").split(",", 1)[0].strip()
        if real_ip:
            return real_ip
    return remote_addr or "unknown"


def _rate_limit_bucket() -> tuple[str, int] | None:
    if not _web_rate_limit_enabled():
        return None
    path = str(request.path or "")
    if not path.startswith("/api/"):
        return None
    if path in _HEAVY_RATE_LIMIT_PATHS:
        return ("heavy", _web_rate_limit_heavy_max())
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        return ("write", _web_rate_limit_write_max())
    return ("read", _web_rate_limit_read_max())


def _consume_rate_limit() -> tuple[bool, int] | None:
    bucket = _rate_limit_bucket()
    if bucket is None:
        return None
    scope, max_requests = bucket
    key = (_client_identifier(), scope, str(request.path or ""))
    now = time.time()
    window_start = now - _web_rate_limit_window_sec()
    with _rate_limit_lock:
        queue = _rate_limit_events.setdefault(key, deque())
        while queue and queue[0] <= window_start:
            queue.popleft()
        if len(queue) >= max_requests:
            retry_after = max(1, int(queue[0] + _web_rate_limit_window_sec() - now))
            return False, retry_after
        queue.append(now)
    return True, 0


@app.before_request
def _enforce_api_auth():
    if not _request_requires_auth():
        return None
    expected_token = _web_api_token()
    if not expected_token:
        return jsonify({"error": "web api auth is enabled but token is not configured"}), 503
    provided_token = _extract_request_token()
    if not provided_token:
        return jsonify({"error": "authentication required"}), 401
    if not hmac.compare_digest(provided_token, expected_token):
        return jsonify({"error": "invalid authentication token"}), 403
    return None


@app.before_request
def _enforce_rate_limit():
    verdict = _consume_rate_limit()
    if verdict is None:
        return None
    allowed, retry_after = verdict
    if allowed:
        return None
    response = jsonify({
        "error": "rate limit exceeded",
        "retry_after_sec": retry_after,
        "window_sec": _web_rate_limit_window_sec(),
    })
    response.status_code = 429
    response.headers["Retry-After"] = str(retry_after)
    return response


def _status_snapshot(detail_mode: str) -> dict[str, Any] | tuple[Any, int]:
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    return runtime.status(detail=detail_mode)


def _status_response(*, detail_mode: str, route_mode: str | None = None):
    snapshot = _status_snapshot(detail_mode)
    if isinstance(snapshot, tuple):
        return snapshot
    try:
        view = _parse_view_arg(request.args.get("view", "json"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if route_mode is None:
        return _respond_with_display(snapshot, view=view)
    return _respond_with_display({"mode": route_mode, "snapshot": snapshot}, view=view)


register_runtime_interface_routes(
    app,
    get_runtime=lambda: _runtime,
    get_loop=lambda: _loop,
    parse_detail_mode=_parse_detail_mode,
    status_response=_status_response,
    runtime_not_ready_response=_runtime_not_ready_response,
    request_view_arg=_request_view_arg,
    parse_view_arg=_parse_view_arg,
    parse_limit_arg=_parse_limit_arg,
    parse_bool=_parse_bool,
    parse_int=_parse_int,
    respond_with_display=_respond_with_display,
    jsonify_contract_payload=_jsonify_contract_payload,
    build_contracts_payload=_build_runtime_contracts_payload,
    serve_contract_document=_serve_runtime_contract_document,
    data_source_unavailable_response=_data_source_unavailable_response,
    logger=logger,
    data_download_lock=_data_download_lock,
    get_data_download_running=lambda: _data_download_running,
    set_data_download_running=lambda value: globals().__setitem__("_data_download_running", bool(value)),
    thread_factory=lambda target: threading.Thread(target=target, daemon=True),
    normalize_chat_session_token=_normalize_chat_session_token,
    run_async=lambda coro: _run_async(coro),
)


@app.route("/")
def index():
    return jsonify({
        "service": "invest-api",
        "status": "ok",
        "message": "Web UI 已移除；请通过 API、SSE 或 commander CLI 与系统交互。",
        "entrypoints": {
            "chat": "/api/chat",
            "status": "/api/status",
            "events": "/api/events",
            "healthz": "/healthz",
        },
    })


@app.route("/legacy")
def legacy_index():
    return _removed_web_ui_response("/legacy")


@app.route("/app")
@app.route("/app/<path:asset_path>")
def frontend_app(asset_path: str = ""):
    return _removed_web_ui_response("/app" if not asset_path else f"/app/{asset_path}")

@app.route("/healthz")
def healthz():
    loop_running = bool(_loop is not None and _loop.is_running())
    return jsonify(
        {
            "status": "ok",
            "service": "invest-web",
            "runtime": {
                "initialized": _runtime is not None,
                "loop_running": loop_running,
                "event_buffer_size": _event_buffer.qsize(),
                "event_history_size": len(_event_history),
                "event_dispatcher_started": bool(_event_dispatcher_started),
            },
        }
    )


# ---- SSE (Server-Sent Events) ----

@app.route("/api/events")
def api_events():
    """SSE 实时事件流"""
    def generate():
        # 发送初始事件
        yield "event: connected\ndata: {\"status\":\"connected\"}\n\n"

        _, last_id = _snapshot_events_since(0)
        while True:
            with _event_condition:
                has_new_event = _event_condition.wait_for(
                    lambda: bool(_event_history) and _event_history[-1]["id"] > last_id,
                    timeout=_EVENT_WAIT_TIMEOUT,
                )
            if not has_new_event:
                yield ": keepalive\n\n"
                continue

            pending, last_id = _snapshot_events_since(last_id)
            for event in pending:
                yield (
                    f"id: {event['id']}\n"
                    f"event: {event['type']}\n"
                    f"data: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                )

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="投资进化系统 Web 前端")
    parser.add_argument("--port", type=int, default=8080, help="服务端口 (默认 8080)")
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址 (默认 127.0.0.1)")
    parser.add_argument("--mock", action="store_true", help="使用模拟数据 (无需真实行情)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    bootstrap_runtime_services(host=args.host, mock=args.mock, source="cli")
    if not _is_loopback_host(args.host):
        logger.warning(
            "Binding non-loopback host via Flask development server. Production should use gunicorn with a single worker."
        )

    print(f"""
╔══════════════════════════════════════════════════╗
║       投资进化系统 Web 前端已启动                   ║
║                                                  ║
║   🌐  http://{args.host}:{args.port}                    ║
║   📊  Mock 模式: {'✅ 已开启' if args.mock else '❌ 未开启'}                      ║
║                                                  ║
║   按 Ctrl+C 停止服务                               ║
╚══════════════════════════════════════════════════╝
""")

    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
