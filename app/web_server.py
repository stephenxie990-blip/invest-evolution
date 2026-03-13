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
from datetime import datetime
import time
import hmac
import json
import logging
import os
from queue import Full, Queue
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, Response, stream_with_context

from app.commander import CommanderConfig, CommanderRuntime, _apply_runtime_path_overrides
from app.runtime_contract_catalog import (
    RUNTIME_CONTRACT_DOCUMENTS_BY_ID,
    RUNTIME_CONTRACT_PUBLIC_PATHS,
    build_runtime_contract_catalog_items,
    load_runtime_contract_document,
)
from app.runtime_artifact_reader import resolve_runtime_artifact_path, safe_read_json, safe_read_jsonl, safe_read_text
from invest.allocator import build_allocation_plan
from invest.leaderboard import write_leaderboard
from invest.models import list_models
from app.train import set_event_callback
from config.services import EvolutionConfigService, RuntimePathConfigService
from invest.meetings import MeetingRecorder
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
_rate_limit_events: dict[tuple[str, str], deque[float]] = {}
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
    global _event_dispatcher_started
    if _event_dispatcher_started:
        return
    with _event_condition:
        if _event_dispatcher_started:
            return
        t = threading.Thread(target=_event_dispatch_loop, name="web-sse-dispatcher", daemon=True)
        t.start()
        _event_dispatcher_started = True


def _event_dispatch_loop() -> None:
    global _event_seq
    while True:
        event = _event_buffer.get()
        with _event_condition:
            _event_seq += 1
            _event_history.append({
                "id": _event_seq,
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


def _sync_runtime_path_config(runtime: CommanderRuntime, payload: dict[str, Any]) -> None:
    import config as config_module

    _apply_runtime_path_overrides(runtime.cfg, payload)
    controller = runtime.body.controller
    controller.output_dir = Path(runtime.cfg.training_output_dir)
    controller.output_dir.mkdir(parents=True, exist_ok=True)
    controller.meeting_recorder = MeetingRecorder(base_dir=str(runtime.cfg.meeting_log_dir))
    controller.config_service = EvolutionConfigService(
        project_root=config_module.PROJECT_ROOT,
        live_config=config_module.config,
        audit_log_path=Path(runtime.cfg.config_audit_log_path),
        snapshot_dir=Path(runtime.cfg.config_snapshot_dir),
    )




def _contract_payload_root(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if isinstance(payload.get("protocol"), dict) or isinstance(payload.get("task_bus"), dict):
            return payload
        snapshot = payload.get("snapshot")
        if isinstance(snapshot, dict) and (isinstance(snapshot.get("protocol"), dict) or isinstance(snapshot.get("task_bus"), dict)):
            return snapshot
    return None


def _jsonify_contract_payload(payload: Any, status_code: int = 200):
    response = jsonify(payload)
    response.status_code = int(status_code)
    root = _contract_payload_root(payload)
    if not root:
        return response

    protocol = dict(root.get("protocol") or {})
    task_bus = dict(root.get("task_bus") or {})
    coverage = dict(root.get("coverage") or {})
    artifact_taxonomy = dict(root.get("artifact_taxonomy") or {})

    if protocol.get("schema_version"):
        response.headers["X-Bounded-Workflow-Schema"] = str(protocol.get("schema_version"))
    if protocol.get("task_bus_schema_version"):
        response.headers["X-Task-Bus-Schema"] = str(protocol.get("task_bus_schema_version"))
    elif task_bus.get("schema_version"):
        response.headers["X-Task-Bus-Schema"] = str(task_bus.get("schema_version"))
    if coverage.get("schema_version"):
        response.headers["X-Coverage-Schema"] = str(coverage.get("schema_version"))
    if artifact_taxonomy.get("schema_version"):
        response.headers["X-Artifact-Taxonomy-Schema"] = str(artifact_taxonomy.get("schema_version"))
    if protocol.get("domain"):
        response.headers["X-Commander-Domain"] = str(protocol.get("domain"))
    if protocol.get("operation"):
        response.headers["X-Commander-Operation"] = str(protocol.get("operation"))
    return response


def _runtime_not_ready_response():
    return jsonify({
        "error": "Commander runtime is not initialized. Start the supported web entrypoint so runtime bootstrap can complete.",
    }), 503


def _parse_limit_arg(default: int = 20, maximum: int = 200) -> int:
    raw = request.args.get("limit", default)
    value = _parse_int(raw, "limit")
    return max(1, min(maximum, value))


def shutdown_runtime_services() -> None:
    global _loop, _runtime

    runtime = _runtime
    loop = _loop
    _runtime = None
    _loop = None
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
    global _runtime_shutdown_registered
    if _runtime_shutdown_registered:
        return
    atexit.register(shutdown_runtime_services)
    _runtime_shutdown_registered = True


def bootstrap_runtime_services(*, host: str, mock: bool = False, source: str = "cli") -> CommanderRuntime:
    global _loop, _runtime

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

        _runtime = runtime
        _loop = loop
        loop_thread.start()
        try:
            _run_async(runtime.start())
        except Exception:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                logger.debug("Failed to stop event loop after bootstrap error", exc_info=True)
            _runtime = None
            _loop = None
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
    import config as config_module

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


def _configured_gunicorn_workers() -> int:
    raw = str(os.environ.get("GUNICORN_WORKERS", "1") or "1").strip()
    return max(1, _parse_int(raw, "GUNICORN_WORKERS", minimum=1))


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


def _resolve_runtime_artifact_path(path_str: str) -> Path | None:
    return resolve_runtime_artifact_path(_runtime, path_str)


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


def _status_snapshot(detail_mode: str) -> dict[str, Any] | tuple[Any, int]:
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    return runtime.status(detail=detail_mode)


def _status_response(*, detail_mode: str, route_mode: str | None = None):
    snapshot = _status_snapshot(detail_mode)
    if isinstance(snapshot, tuple):
        return snapshot
    if route_mode is None:
        return _jsonify_contract_payload(snapshot)
    return _jsonify_contract_payload({"mode": route_mode, "snapshot": snapshot})


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


# ---- Contracts ----

@app.route("/api/contracts")
def api_contracts():
    items = build_runtime_contract_catalog_items()
    return jsonify({"count": len(items), "items": items})


def _serve_runtime_contract_document(document_id: str):
    document = RUNTIME_CONTRACT_DOCUMENTS_BY_ID[document_id]
    try:
        return jsonify(load_runtime_contract_document(document))
    except FileNotFoundError:
        return jsonify({"error": document.not_found_error}), 404
    except Exception as exc:
        logger.exception(document.load_error_log)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/contracts/runtime-v1")
def api_contract_runtime_v1():
    return _serve_runtime_contract_document("runtime-v1")


@app.route("/api/contracts/runtime-v1/schema")
def api_contract_runtime_v1_schema():
    return _serve_runtime_contract_document("runtime-v1-schema")


@app.route("/api/contracts/runtime-v1/openapi")
def api_contract_runtime_v1_openapi():
    return _serve_runtime_contract_document("runtime-v1-openapi")


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "service": "invest-web"})


# ---- Status ----

@app.route("/api/status")
def api_status():
    detail_mode = _parse_detail_mode(request.args.get("detail", "fast"))
    return _status_response(detail_mode=detail_mode)


@app.route("/api/lab/status/quick")
def api_lab_status_quick():
    return _status_response(detail_mode="fast", route_mode="quick")


@app.route("/api/lab/status/deep")
def api_lab_status_deep():
    return _status_response(detail_mode="slow", route_mode="deep")


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


# ---- Chat ----

@app.route("/api/chat", methods=["POST"])
def api_chat():
    runtime = _runtime
    if runtime is None or _loop is None:
        return _runtime_not_ready_response()

    data = request.get_json(force=True) or {}
    message = str(data.get("message", "")).strip()
    if not message:
        return jsonify({"error": "message is required"}), 400
    try:
        reply = _run_async(
            runtime.ask(message, session_key="api:chat", channel="api", chat_id="chat")
        )
        try:
            payload = json.loads(reply) if isinstance(reply, str) else dict(reply or {})
        except Exception:
            payload = {"reply": str(reply)}
        if not isinstance(payload, dict):
            payload = {"reply": str(reply)}
        payload.setdefault("reply", str(payload.get("message") or reply))
        payload.setdefault("message", str(payload.get("reply") or ""))
        return jsonify(payload)
    except Exception as exc:
        logger.exception("Chat error")
        return jsonify({"error": str(exc)}), 500


# ---- Train ----

@app.route("/api/train", methods=["POST"])
def api_train():
    runtime = _runtime
    if runtime is None or _loop is None:
        return _runtime_not_ready_response()

    data = request.get_json(force=True) or {}
    try:
        rounds = max(1, min(100, int(data.get("rounds", 1))))
    except (TypeError, ValueError):
        return jsonify({"error": "rounds must be an integer"}), 400
    try:
        mock = _parse_bool(data.get("mock", False), "mock")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        result = _run_async(runtime.train_once(rounds=rounds, mock=mock))
        return _jsonify_contract_payload(result)
    except DataSourceUnavailableError as exc:
        logger.warning("Train data source unavailable: %s", exc)
        return _data_source_unavailable_response(exc)
    except Exception as exc:
        logger.exception("Train error")
        return jsonify({"error": str(exc)}), 500


# ---- Training Lab ----

@app.route("/api/lab/training/plans", methods=["POST"])
def api_training_plan_create():
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()

    data = request.get_json(force=True) or {}
    try:
        rounds = max(1, min(100, int(data.get("rounds", 1))))
    except (TypeError, ValueError):
        return jsonify({"error": "rounds must be an integer"}), 400
    try:
        mock = _parse_bool(data.get("mock", False), "mock")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        detail_mode = _parse_detail_mode(
            data.get("detail_mode", "fast"),
            field_name="detail_mode",
            strict=True,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    raw_tags = data.get("tags", [])
    if isinstance(raw_tags, str):
        tags = [part.strip() for part in raw_tags.split(",") if part.strip()]
    elif isinstance(raw_tags, list):
        tags = [str(part).strip() for part in raw_tags if str(part).strip()]
    else:
        return jsonify({"error": "tags must be a list of strings or a comma-separated string"}), 400

    plan = runtime.create_training_plan(
        rounds=rounds,
        mock=mock,
        goal=str(data.get("goal", "") or ""),
        notes=str(data.get("notes", "") or ""),
        tags=tags,
        detail_mode=detail_mode,
        protocol=data.get("protocol") if isinstance(data.get("protocol"), dict) else None,
        dataset=data.get("dataset") if isinstance(data.get("dataset"), dict) else None,
        model_scope=data.get("model_scope") if isinstance(data.get("model_scope"), dict) else None,
        optimization=data.get("optimization") if isinstance(data.get("optimization"), dict) else None,
        source="api",
    )
    return _jsonify_contract_payload(plan, 201)


@app.route("/api/lab/training/plans")
def api_training_plan_list():
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    try:
        limit = _parse_limit_arg()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return _jsonify_contract_payload(runtime.list_training_plans(limit=limit))


@app.route("/api/lab/training/plans/<plan_id>")
def api_training_plan_get(plan_id: str):
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    try:
        return _jsonify_contract_payload(runtime.get_training_plan(plan_id))
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/api/lab/training/plans/<plan_id>/execute", methods=["POST"])
def api_training_plan_execute(plan_id: str):
    runtime = _runtime
    if runtime is None or _loop is None:
        return _runtime_not_ready_response()
    try:
        payload = _run_async(runtime.execute_training_plan(plan_id))
        return _jsonify_contract_payload(payload)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except DataSourceUnavailableError as exc:
        logger.warning("Training plan execution data source unavailable: %s", exc)
        return _data_source_unavailable_response(exc)
    except Exception as exc:
        logger.exception("Training plan execution error")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/lab/training/runs")
def api_training_run_list():
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    try:
        limit = _parse_limit_arg()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return _jsonify_contract_payload(runtime.list_training_runs(limit=limit))


@app.route("/api/lab/training/runs/<run_id>")
def api_training_run_get(run_id: str):
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    try:
        return _jsonify_contract_payload(runtime.get_training_run(run_id))
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/api/lab/training/evaluations")
def api_training_evaluation_list():
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    try:
        limit = _parse_limit_arg()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return _jsonify_contract_payload(runtime.list_training_evaluations(limit=limit))


@app.route("/api/lab/training/evaluations/<run_id>")
def api_training_evaluation_get(run_id: str):
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    try:
        return _jsonify_contract_payload(runtime.get_training_evaluation(run_id))
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404


# ---- Investment Models ----

@app.route("/api/investment-models")
def api_investment_models():
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    return _jsonify_contract_payload(runtime.get_investment_models())


@app.route("/api/leaderboard")
def api_leaderboard():
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    return _jsonify_contract_payload(runtime.get_leaderboard())


@app.route("/api/allocator")
def api_allocator():
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    regime = str(request.args.get("regime", "oscillation") or "oscillation").strip().lower()
    try:
        top_n = _parse_int(request.args.get("top_n", 3), "top_n", minimum=1, maximum=4)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return _jsonify_contract_payload(runtime.get_allocator_preview(
        regime=regime,
        top_n=top_n,
        as_of_date=datetime.now().strftime("%Y%m%d"),
    ))


@app.route("/api/model-routing/preview")
def api_model_routing_preview():
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    controller = runtime.body.controller
    cutoff_date = str(request.args.get("cutoff_date", "") or "").strip() or None
    try:
        stock_count = int(request.args.get("stock_count", 0) or 0) or None
    except (TypeError, ValueError):
        return jsonify({"error": "stock_count must be an integer"}), 400
    try:
        min_history_days = int(request.args.get("min_history_days", 0) or 0) or None
    except (TypeError, ValueError):
        return jsonify({"error": "min_history_days must be an integer"}), 400
    allowed_models = request.args.getlist("allowed_models")
    if not allowed_models:
        raw_allowed = str(request.args.get("allowed_models", "") or "").strip()
        if raw_allowed:
            allowed_models = [part.strip() for part in raw_allowed.split(",") if part.strip()]
    try:
        payload = runtime.get_model_routing_preview(
            cutoff_date=cutoff_date,
            stock_count=stock_count,
            min_history_days=min_history_days,
            allowed_models=allowed_models or None,
        )
        return _jsonify_contract_payload(payload)
    except DataSourceUnavailableError as exc:
        logger.warning("Model routing preview data source unavailable: %s", exc)
        return _data_source_unavailable_response(exc)
    except Exception as exc:
        logger.exception("Model routing preview error")
        return jsonify({"error": str(exc)}), 500


# ---- Strategies ----

@app.route("/api/strategies")
def api_strategies():
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    genes = runtime.strategy_registry.list_genes()
    return jsonify({
        "count": len(genes),
        "items": [g.to_dict() for g in genes],
    })


@app.route("/api/strategies/reload", methods=["POST"])
def api_strategies_reload():
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    result = runtime.reload_strategies()
    return _jsonify_contract_payload(result)


# ---- Cron ----

@app.route("/api/cron")
def api_cron_list():
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    rows = [j.to_dict() for j in runtime.cron.list_jobs()]
    return jsonify({"count": len(rows), "items": rows})


@app.route("/api/cron", methods=["POST"])
def api_cron_add():
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()

    data = request.get_json(force=True) or {}
    name = str(data.get("name", "")).strip()
    message = str(data.get("message", "")).strip()
    try:
        every_sec = int(data.get("every_sec", 3600))
    except (TypeError, ValueError):
        return jsonify({"error": "every_sec must be an integer"}), 400
    try:
        deliver = _parse_bool(data.get("deliver", False), "deliver")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not name or not message:
        return jsonify({"error": "name and message are required"}), 400
    job = runtime.cron.add_job(
        name=name, message=message, every_sec=every_sec,
        deliver=deliver,
        channel=str(data.get("channel", "web")),
        to=str(data.get("to", "commander")),
    )
    runtime._persist_state()
    return jsonify({"status": "ok", "job": job.to_dict()})


@app.route("/api/cron/<job_id>", methods=["DELETE"])
def api_cron_remove(job_id: str):
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    ok = runtime.cron.remove_job(job_id)
    runtime._persist_state()
    return jsonify({"status": "ok" if ok else "not_found", "job_id": job_id})


# ---- Memory ----

def _memory_brief_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row or {})
    ts_ms = item.get("ts_ms")
    if ts_ms:
        try:
            item["ts"] = datetime.fromtimestamp(int(ts_ms) / 1000).isoformat()
        except Exception:
            item["ts"] = ""
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    if metadata:
        item["summary"] = metadata.get("summary")
        item["training_run"] = bool(metadata.get("training_run"))
    return item


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_stock_codes(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    codes: list[str] = []
    for item in values:
        code = ""
        if isinstance(item, str):
            code = item.strip()
        elif isinstance(item, dict):
            code = str(item.get("code") or item.get("ts_code") or "").strip()
        if code and code not in codes:
            codes.append(code)
    return codes


def _primary_training_result(metadata: dict[str, Any]) -> dict[str, Any]:
    results = list(metadata.get("results") or [])
    if not results:
        return {}
    ok_results = [dict(item or {}) for item in results if str((item or {}).get("status") or "ok") == "ok"]
    if ok_results:
        return ok_results[-1]
    return dict(results[-1] or {})


def _diff_params(current: Any, previous: Any) -> dict[str, Any]:
    current_map = current if isinstance(current, dict) else {}
    previous_map = previous if isinstance(previous, dict) else {}
    changed: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for key in sorted(set(current_map) | set(previous_map)):
        has_current = key in current_map
        has_previous = key in previous_map
        if has_current and not has_previous:
            added.append({"key": key, "current": current_map.get(key)})
        elif has_previous and not has_current:
            removed.append({"key": key, "previous": previous_map.get(key)})
        elif current_map.get(key) != previous_map.get(key):
            changed.append({
                "key": key,
                "current": current_map.get(key),
                "previous": previous_map.get(key),
            })
    return {
        "changed": changed,
        "added": added,
        "removed": removed,
        "changed_count": len(changed) + len(added) + len(removed),
    }


def _build_strategy_compare(runtime: CommanderRuntime | None, row: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    if runtime is None:
        return {"has_previous": False}
    try:
        training_rows = runtime.memory.recent(limit=runtime.memory.max_records, kind="training_run")
    except Exception:
        training_rows = []
    current_id = str(row.get("id") or "")
    previous_row = None
    for index, candidate in enumerate(training_rows):
        if str(candidate.get("id") or "") == current_id:
            if index > 0:
                previous_row = training_rows[index - 1]
            break
    if previous_row is None:
        return {"has_previous": False}

    previous_metadata = previous_row.get("metadata") if isinstance(previous_row.get("metadata"), dict) else {}
    current_result = _primary_training_result(metadata)
    previous_result = _primary_training_result(previous_metadata)

    current_selected = _normalize_stock_codes(current_result.get("selected_stocks"))
    previous_selected = _normalize_stock_codes(previous_result.get("selected_stocks"))
    current_selected_count = int(current_result.get("selected_count") or len(current_selected))
    previous_selected_count = int(previous_result.get("selected_count") or len(previous_selected))

    current_return = _as_float(current_result.get("return_pct"))
    previous_return = _as_float(previous_result.get("return_pct"))
    current_trade_count = int(current_result.get("trade_count") or 0)
    previous_trade_count = int(previous_result.get("trade_count") or 0)
    current_opt_count = int(current_result.get("optimization_event_count") or len(current_result.get("optimization_events") or []))
    previous_opt_count = int(previous_result.get("optimization_event_count") or len(previous_result.get("optimization_events") or []))

    return {
        "has_previous": True,
        "previous_record": _memory_brief_row(previous_row),
        "current_cycle_id": current_result.get("cycle_id"),
        "previous_cycle_id": previous_result.get("cycle_id"),
        "metrics": {
            "return_pct": {
                "current": current_return,
                "previous": previous_return,
                "delta": (current_return - previous_return) if current_return is not None and previous_return is not None else None,
            },
            "selected_count": {
                "current": current_selected_count,
                "previous": previous_selected_count,
                "delta": current_selected_count - previous_selected_count,
            },
            "trade_count": {
                "current": current_trade_count,
                "previous": previous_trade_count,
                "delta": current_trade_count - previous_trade_count,
            },
            "optimization_event_count": {
                "current": current_opt_count,
                "previous": previous_opt_count,
                "delta": current_opt_count - previous_opt_count,
            },
        },
        "flags": {
            "selection_mode": {
                "current": current_result.get("selection_mode"),
                "previous": previous_result.get("selection_mode"),
                "changed": current_result.get("selection_mode") != previous_result.get("selection_mode"),
            },
            "review_applied": {
                "current": bool(current_result.get("review_applied", False)),
                "previous": bool(previous_result.get("review_applied", False)),
                "changed": bool(current_result.get("review_applied", False)) != bool(previous_result.get("review_applied", False)),
            },
            "benchmark_passed": {
                "current": bool(current_result.get("benchmark_passed", False)),
                "previous": bool(previous_result.get("benchmark_passed", False)),
                "changed": bool(current_result.get("benchmark_passed", False)) != bool(previous_result.get("benchmark_passed", False)),
            },
        },
        "selected_stocks": {
            "current": current_selected,
            "previous": previous_selected,
            "added": [code for code in current_selected if code not in previous_selected],
            "removed": [code for code in previous_selected if code not in current_selected],
            "kept": [code for code in current_selected if code in previous_selected],
        },
        "params": _diff_params(current_result.get("params"), previous_result.get("params")),
    }


def _build_memory_detail(row: dict[str, Any]) -> dict[str, Any]:
    item = _memory_brief_row(row)
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    results = list(metadata.get("results") or [])
    detailed_results = []
    optimization_cache: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        cycle = dict(result or {})
        artifacts = cycle.get("artifacts") if isinstance(cycle.get("artifacts"), dict) else {}
        cycle_id = cycle.get("cycle_id")
        cycle_result = safe_read_json(_runtime, artifacts.get("cycle_result_path", "")) if artifacts else None
        selection_meeting = safe_read_json(_runtime, artifacts.get("selection_meeting_json_path", "")) if artifacts else None
        review_meeting = safe_read_json(_runtime, artifacts.get("review_meeting_json_path", "")) if artifacts else None
        config_snapshot = safe_read_json(_runtime, cycle.get("config_snapshot_path", "")) if cycle.get("config_snapshot_path") else None
        optimization_path = artifacts.get("optimization_events_path", "") if artifacts else ""
        if optimization_path:
            optimization_cache.setdefault(optimization_path, safe_read_jsonl(_runtime, optimization_path))
        optimization_events = optimization_cache.get(optimization_path, [])
        detailed_results.append({
            **cycle,
            "cycle_result": cycle_result,
            "selection_meeting": selection_meeting,
            "selection_meeting_markdown": safe_read_text(_runtime, artifacts.get("selection_meeting_markdown_path", "")) if artifacts else "",
            "review_meeting": review_meeting,
            "review_meeting_markdown": safe_read_text(_runtime, artifacts.get("review_meeting_markdown_path", "")) if artifacts else "",
            "config_snapshot": config_snapshot,
            "optimization_events": [evt for evt in optimization_events if cycle_id is None or evt.get("cycle_id") in (None, cycle_id)],
        })
    return {
        "item": item,
        "details": {
            "summary": metadata.get("summary") or {},
            "runtime_summary": metadata.get("runtime_summary") or {},
            "results": detailed_results,
            "compare": _build_strategy_compare(_runtime, row, metadata),
        },
    }

@app.route("/api/memory")
def api_memory():
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()

    query = request.args.get("q", "")
    try:
        limit = min(200, max(1, int(request.args.get("limit", 20))))
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400
    rows = runtime.memory.search(query=query, limit=limit)
    items = [_memory_brief_row(row) for row in rows]
    return jsonify({"count": len(items), "items": items})


@app.route("/api/memory/<record_id>")
def api_memory_detail(record_id: str):
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    row = runtime.memory.get(record_id)
    if row is None:
        return jsonify({"error": "memory record not found"}), 404
    return jsonify(_build_memory_detail(row))


# ---- Agent Prompts ----

@app.route("/api/agent_prompts", methods=["GET"])
def api_agent_prompts_list():
    runtime = _runtime
    if runtime is not None:
        return _jsonify_contract_payload(runtime.list_agent_prompts())
    import config as config_module
    from app.commander_services import list_agent_prompts_payload
    return jsonify(list_agent_prompts_payload(project_root=config_module.PROJECT_ROOT))


@app.route("/api/agent_prompts", methods=["POST"])
def api_agent_prompts_update():
    data = request.get_json(force=True) or {}
    agent_name = str(data.get("name", "") or "").strip()
    if not agent_name:
        return jsonify({"error": "name is required"}), 400
    if "system_prompt" not in data:
        return jsonify({"error": "system_prompt is required"}), 400
    try:
        runtime = _runtime
        if runtime is not None:
            return _jsonify_contract_payload(runtime.update_agent_prompt(agent_name=agent_name, system_prompt=str(data.get("system_prompt", "") or "")))
        import config as config_module
        from app.commander_services import update_agent_prompt_payload
        return jsonify(update_agent_prompt_payload(agent_name=agent_name, system_prompt=str(data.get("system_prompt", "") or ""), project_root=config_module.PROJECT_ROOT))
    except Exception as exc:
        logger.exception("Failed to update agent prompt")
        return jsonify({"status": "error", "error": str(exc)}), 500


# ---- Runtime Paths ----

@app.route("/api/runtime_paths", methods=["GET"])
def api_runtime_paths_get():
    runtime = _runtime
    if runtime is not None:
        return _jsonify_contract_payload(runtime.get_runtime_paths())
    import config as config_module
    from app.commander_services import get_runtime_paths_payload
    return jsonify(get_runtime_paths_payload(None, project_root=config_module.PROJECT_ROOT))


@app.route("/api/runtime_paths", methods=["POST"])
def api_runtime_paths_update():
    data = request.get_json(force=True) or {}
    try:
        runtime = _runtime
        if runtime is not None:
            return _jsonify_contract_payload(runtime.update_runtime_paths(data, confirm=True))
        import config as config_module
        from app.commander_services import update_runtime_paths_payload
        return jsonify(update_runtime_paths_payload(patch=data, runtime=None, project_root=config_module.PROJECT_ROOT, sync_runtime=None))
    except ValueError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Failed to update runtime path config")
        return jsonify({"status": "error", "error": str(exc)}), 500


# ---- Evolution Config (Models/Data) ----

@app.route("/api/evolution_config", methods=["GET"])
def api_evolution_config_get():
    runtime = _runtime
    if runtime is not None:
        return _jsonify_contract_payload(runtime.get_evolution_config())
    import config as config_module
    from app.commander_services import get_evolution_config_payload
    return jsonify(get_evolution_config_payload(project_root=config_module.PROJECT_ROOT, live_config=config_module.config))


@app.route("/api/evolution_config", methods=["POST"])
def api_evolution_config_update():
    data = request.get_json(force=True) or {}
    forbidden_keys = {"llm_fast_model", "llm_deep_model", "llm_api_base", "llm_api_key"}
    touched = sorted(key for key in forbidden_keys if key in data)
    if touched:
        return jsonify({
            "status": "error",
            "error": "LLM 配置已迁移到 /api/control_plane；/api/evolution_config 仅保留训练参数",
            "migrate_to": "/api/control_plane",
            "invalid_keys": touched,
        }), 400
    try:
        runtime = _runtime
        if runtime is not None:
            return _jsonify_contract_payload(runtime.update_evolution_config(data, confirm=True))
        import config as config_module
        from app.commander_services import update_evolution_config_payload
        return jsonify(update_evolution_config_payload(patch=data, project_root=config_module.PROJECT_ROOT, live_config=config_module.config, source="web_api"))
    except ValueError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Failed to update evolution config")
        return jsonify({"status": "error", "error": str(exc)}), 500


@app.route("/api/control_plane", methods=["GET"])
def api_control_plane_get():
    runtime = _runtime
    if runtime is not None:
        return _jsonify_contract_payload(runtime.get_control_plane())
    import config as config_module
    from app.commander_services import get_control_plane_payload
    return jsonify(get_control_plane_payload(project_root=config_module.PROJECT_ROOT))


@app.route("/api/control_plane", methods=["POST"])
def api_control_plane_update():
    data = request.get_json(force=True) or {}
    try:
        runtime = _runtime
        if runtime is not None:
            return _jsonify_contract_payload(runtime.update_control_plane(data, confirm=True))
        import config as config_module
        from app.commander_services import update_control_plane_payload
        return jsonify(update_control_plane_payload(patch=data, project_root=config_module.PROJECT_ROOT, source="web_api"))
    except ValueError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Failed to update control plane config")
        return jsonify({"status": "error", "error": str(exc)}), 500


# ---- Data Management ----

@app.route("/api/data/status", methods=["GET"])
def api_data_status():
    try:
        refresh = _parse_bool(request.args.get("refresh", False), "refresh")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    runtime = _runtime
    if runtime is not None:
        return _jsonify_contract_payload(runtime.get_data_status(refresh=refresh))
    from app.commander_services import get_data_status_payload
    return jsonify(get_data_status_payload(refresh=refresh))

@app.route("/api/data/capital_flow", methods=["GET"])
def api_data_capital_flow():
    codes_param = str(request.args.get("codes", "") or "").strip()
    codes = [item.strip() for item in codes_param.split(",") if item.strip()] or None
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    limit = _parse_limit_arg(default=200, maximum=5000)
    runtime = _runtime
    if runtime is not None:
        return _jsonify_contract_payload(runtime.get_capital_flow(codes=codes, start_date=start_date, end_date=end_date, limit=limit))
    from app.commander_services import get_capital_flow_payload
    return jsonify(get_capital_flow_payload(codes=codes, start_date=start_date, end_date=end_date, limit=limit))


@app.route("/api/data/dragon_tiger", methods=["GET"])
def api_data_dragon_tiger():
    codes_param = str(request.args.get("codes", "") or "").strip()
    codes = [item.strip() for item in codes_param.split(",") if item.strip()] or None
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    limit = _parse_limit_arg(default=200, maximum=5000)
    runtime = _runtime
    if runtime is not None:
        return _jsonify_contract_payload(runtime.get_dragon_tiger(codes=codes, start_date=start_date, end_date=end_date, limit=limit))
    from app.commander_services import get_dragon_tiger_payload
    return jsonify(get_dragon_tiger_payload(codes=codes, start_date=start_date, end_date=end_date, limit=limit))


@app.route("/api/data/intraday_60m", methods=["GET"])
def api_data_intraday_60m():
    codes_param = str(request.args.get("codes", "") or "").strip()
    codes = [item.strip() for item in codes_param.split(",") if item.strip()] or None
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    limit = _parse_limit_arg(default=500, maximum=10000)
    runtime = _runtime
    if runtime is not None:
        return _jsonify_contract_payload(runtime.get_intraday_60m(codes=codes, start_date=start_date, end_date=end_date, limit=limit))
    from app.commander_services import get_intraday_60m_payload
    return jsonify(get_intraday_60m_payload(codes=codes, start_date=start_date, end_date=end_date, limit=limit))


@app.route("/api/data/download", methods=["POST"])
def api_data_download():
    global _data_download_running

    runtime = _runtime
    if runtime is not None:
        data = request.get_json(silent=True) or {}
        try:
            confirm = _parse_bool(data.get("confirm", False), "confirm")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return _jsonify_contract_payload(runtime.trigger_data_download(confirm=confirm))

    def _do_download():
        global _data_download_running
        from market_data.gateway import MarketDataGateway

        try:
            MarketDataGateway().sync_background_full_refresh()
        except Exception as exc:
            logger.exception("后台数据同步失败: %s", exc)
        finally:
            with _data_download_lock:
                _data_download_running = False

    with _data_download_lock:
        if _data_download_running:
            return jsonify({"status": "running", "message": "后台同步已在运行"})
        _data_download_running = True

    t = threading.Thread(target=_do_download, daemon=True)
    try:
        t.start()
    except Exception:
        with _data_download_lock:
            _data_download_running = False
        raise
    return jsonify({"status": "started", "message": "后台同步已启动"})


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
