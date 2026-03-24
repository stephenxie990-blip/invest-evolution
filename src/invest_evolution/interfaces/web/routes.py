"""Canonical web route registration and request handlers."""

from __future__ import annotations

import json
import os
import queue
import threading
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, Response, request, stream_with_context

import invest_evolution.config as config_module
from invest_evolution.application.config_surface import (
    ConfigSurfaceRouteSpec,
    ConfigSurfaceReadSpec,
    ConfigSurfaceUpdateSpec,
    ConfigSurfaceValidationError,
    build_config_surface_route_specs,
    build_config_surface_read_specs,
    build_config_surface_update_specs,
)
from invest_evolution.application.commander.ops import get_data_status_payload
from invest_evolution.application.commander.workflow import normalize_ask_reply_payload
from invest_evolution.market_data import DataSourceUnavailableError
from invest_evolution.market_data.manager import MarketDataGateway
from invest_evolution.interfaces.web.contracts import RUNTIME_CONTRACT_ROUTE_SPECS

from invest_evolution.interfaces.web.presentation import (
    ResponseValue,
    build_json_error_response,
    build_json_payload_response,
    build_json_status_error_response,
    build_not_found_response,
    display_limit_response_or_400,
    display_list_response_or_400,
    display_response_or_404,
    parse_bool_field_or_400,
    parse_detail_or_400,
    parse_int_field_or_400,
    parse_json_object_or_400,
    parse_query_bool_or_400,
    parse_required_str_field_or_400,
    parse_str_list_field_or_400,
    parse_value_or_400,
    parsed_request_response_or_400,
    read_object_field,
    read_str_field,
    is_route_error_response,
    runtime_display_response_or_400,
    runtime_items_response_or_400,
    runtime_or_fallback_display_response_or_400,
)
from invest_evolution.interfaces.web.runtime import RuntimeFacade

_WEB_ROUTE_INTERNAL_EXCEPTIONS = (
    RuntimeError,
    TypeError,
    KeyError,
    OSError,
    json.JSONDecodeError,
)
_WEB_ROUTE_WORKER_EXCEPTIONS = _WEB_ROUTE_INTERNAL_EXCEPTIONS + (
    DataSourceUnavailableError,
    ValueError,
)


def _resolve_config_surface_read_spec(surface: str) -> ConfigSurfaceReadSpec:
    spec = build_config_surface_read_specs(
        project_root=config_module.PROJECT_ROOT,
        live_config=config_module.config,
    ).get(surface)
    if spec is None:  # pragma: no cover - internal route wiring guard
        raise ValueError(f"unsupported config surface: {surface}")
    return spec


def _resolve_config_surface_update_spec(surface: str) -> ConfigSurfaceUpdateSpec:
    spec = build_config_surface_update_specs(
        project_root=config_module.PROJECT_ROOT,
        live_config=config_module.config,
    ).get(surface)
    if spec is None:  # pragma: no cover - internal route wiring guard
        raise ValueError(f"unsupported config surface update: {surface}")
    return spec


def _load_runtime_or_not_ready(
    *,
    runtime_facade: RuntimeFacade,
    runtime_not_ready_response: Callable[[], ResponseValue],
    require_loop: bool = False,
) -> Any:
    return runtime_facade.require_runtime(
        runtime_not_ready_response=runtime_not_ready_response,
        require_loop=require_loop,
    )


def _runtime_read_loader(
    *,
    runtime_facade: RuntimeFacade,
    runtime_not_ready_response: Callable[[], ResponseValue],
    require_loop: bool = False,
) -> Callable[[], Any]:
    def _load() -> Any:
        return _load_runtime_or_not_ready(
            runtime_facade=runtime_facade,
            runtime_not_ready_response=runtime_not_ready_response,
            require_loop=require_loop,
        )

    return _load


def _respond_runtime_display_read(
    *,
    runtime_facade: RuntimeFacade,
    runtime_not_ready_response: Callable[[], ResponseValue],
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
    fetch: Callable[[Any], Any],
) -> ResponseValue:
    return runtime_display_response_or_400(
        load_runtime=_runtime_read_loader(
            runtime_facade=runtime_facade,
            runtime_not_ready_response=runtime_not_ready_response,
        ),
        request_view_arg=request_view_arg,
        respond_with_display=respond_with_display,
        fetch=fetch,
    )


def _respond_runtime_items_read(
    *,
    runtime_facade: RuntimeFacade,
    runtime_not_ready_response: Callable[[], ResponseValue],
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
    fetch_items: Callable[[Any], list[dict[str, Any]]],
) -> ResponseValue:
    return runtime_items_response_or_400(
        load_runtime=_runtime_read_loader(
            runtime_facade=runtime_facade,
            runtime_not_ready_response=runtime_not_ready_response,
        ),
        request_view_arg=request_view_arg,
        respond_with_display=respond_with_display,
        fetch_items=fetch_items,
    )


def _respond_parsed_runtime_or_fallback_read(
    *,
    parse_request: Callable[[], dict[str, Any] | ResponseValue],
    runtime_facade: RuntimeFacade,
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
    runtime_fetch: Callable[[Any, dict[str, Any]], Any],
    fallback_fetch: Callable[[dict[str, Any]], Any],
) -> ResponseValue:
    return parsed_request_response_or_400(
        parse_request=parse_request,
        respond=lambda parsed_request: _respond_runtime_or_fallback_read(
            runtime_facade=runtime_facade,
            request_view_arg=request_view_arg,
            respond_with_display=respond_with_display,
            runtime_fetch=lambda runtime: runtime_fetch(runtime, parsed_request),
            fallback_fetch=lambda: fallback_fetch(parsed_request),
        ),
    )


def _respond_runtime_operation(
    *,
    runtime_facade: RuntimeFacade,
    runtime_not_ready_response: Callable[[], ResponseValue],
    require_loop: bool = False,
    handle_runtime: Callable[[Any], ResponseValue] | None = None,
    parse_request: Callable[[], dict[str, Any] | ResponseValue] | None = None,
    handle_parsed_runtime: Callable[[Any, dict[str, Any]], ResponseValue] | None = None,
) -> ResponseValue:
    if parse_request is None:
        if handle_runtime is None:  # pragma: no cover - internal helper guard
            raise ValueError(
                "handle_runtime is required when parse_request is not provided"
            )
        return _with_runtime_handler(
            runtime_facade=runtime_facade,
            runtime_not_ready_response=runtime_not_ready_response,
            require_loop=require_loop,
            handler=handle_runtime,
        )
    if handle_parsed_runtime is None:  # pragma: no cover - internal helper guard
        raise ValueError(
            "handle_parsed_runtime is required when parse_request is provided"
        )
    return _with_runtime_handler(
        runtime_facade=runtime_facade,
        runtime_not_ready_response=runtime_not_ready_response,
        require_loop=require_loop,
        handler=lambda runtime: parsed_request_response_or_400(
            parse_request=parse_request,
            respond=lambda parsed_request: handle_parsed_runtime(
                runtime, parsed_request
            ),
        ),
    )


def _respond_runtime_or_fallback_read(
    *,
    runtime_facade: RuntimeFacade,
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
    runtime_fetch: Callable[[Any], Any],
    fallback_fetch: Callable[[], Any],
) -> ResponseValue:
    return runtime_or_fallback_display_response_or_400(
        get_runtime=runtime_facade.get_runtime,
        request_view_arg=request_view_arg,
        respond_with_display=respond_with_display,
        runtime_fetch=runtime_fetch,
        fallback_fetch=fallback_fetch,
    )


def _respond_training_lab_list_read(
    *,
    kind: str,
    runtime_facade: RuntimeFacade,
    request_view_arg: Callable[[], str],
    parse_limit_arg: Callable[..., int],
    respond_with_display: Callable[..., ResponseValue],
) -> ResponseValue:
    return display_list_response_or_400(
        request_view_arg=request_view_arg,
        parse_limit_arg=parse_limit_arg,
        respond_with_display=respond_with_display,
        fetch=lambda limit: runtime_facade.training_lab_list_snapshot(
            kind=kind,
            limit=limit,
        ),
    )


def _respond_training_lab_detail_read(
    *,
    kind: str,
    artifact_id: str,
    runtime_facade: RuntimeFacade,
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
) -> ResponseValue:
    return display_response_or_404(
        request_view_arg=request_view_arg,
        respond_with_display=respond_with_display,
        fetch=lambda: runtime_facade.training_lab_detail_snapshot(
            kind=kind,
            artifact_id=artifact_id,
        ),
    )


def register_runtime_read_routes(
    app: Flask,
    *,
    runtime_facade: RuntimeFacade,
    parse_detail_mode: Callable[..., str],
    status_response: Callable[..., ResponseValue],
    runtime_not_ready_response: Callable[[], ResponseValue],
    request_view_arg: Callable[[], str],
    parse_limit_arg: Callable[..., int],
    respond_with_display: Callable[..., ResponseValue],
) -> None:
    @app.route("/api/status")
    def api_status():
        detail_mode = parse_detail_or_400(
            parse_detail_mode,
            raw_value=request.args.get("detail", "fast"),
        )
        if not isinstance(detail_mode, str):
            return detail_mode
        return status_response(detail_mode=detail_mode)

    @app.route("/api/events/summary")
    def api_events_summary():
        return display_limit_response_or_400(
            request_view_arg=request_view_arg,
            parse_limit_arg=parse_limit_arg,
            respond_with_display=respond_with_display,
            fetch=lambda limit: runtime_facade.events_summary_snapshot(
                limit=limit,
                ok_status="ok",
            ),
            default_limit=50,
            maximum_limit=200,
        )

    @app.route("/api/lab/training/plans")
    def api_training_plan_list():
        return _respond_training_lab_list_read(
            kind="plan",
            runtime_facade=runtime_facade,
            request_view_arg=request_view_arg,
            parse_limit_arg=parse_limit_arg,
            respond_with_display=respond_with_display,
        )

    @app.route("/api/lab/training/plans/<plan_id>")
    def api_training_plan_get(plan_id: str):
        return _respond_training_lab_detail_read(
            kind="plan",
            artifact_id=plan_id,
            runtime_facade=runtime_facade,
            request_view_arg=request_view_arg,
            respond_with_display=respond_with_display,
        )

    @app.route("/api/lab/training/runs")
    def api_training_run_list():
        return _respond_training_lab_list_read(
            kind="run",
            runtime_facade=runtime_facade,
            request_view_arg=request_view_arg,
            parse_limit_arg=parse_limit_arg,
            respond_with_display=respond_with_display,
        )

    @app.route("/api/lab/training/runs/<run_id>")
    def api_training_run_get(run_id: str):
        return _respond_training_lab_detail_read(
            kind="run",
            artifact_id=run_id,
            runtime_facade=runtime_facade,
            request_view_arg=request_view_arg,
            respond_with_display=respond_with_display,
        )

    @app.route("/api/lab/training/evaluations")
    def api_training_evaluation_list():
        return _respond_training_lab_list_read(
            kind="evaluation",
            runtime_facade=runtime_facade,
            request_view_arg=request_view_arg,
            parse_limit_arg=parse_limit_arg,
            respond_with_display=respond_with_display,
        )

    @app.route("/api/lab/training/evaluations/<run_id>")
    def api_training_evaluation_get(run_id: str):
        return _respond_training_lab_detail_read(
            kind="evaluation",
            artifact_id=run_id,
            runtime_facade=runtime_facade,
            request_view_arg=request_view_arg,
            respond_with_display=respond_with_display,
        )


def _parse_data_download_request_or_400(
    *,
    parse_bool: Callable[[Any, str], bool],
) -> dict[str, bool] | ResponseValue:
    data = parse_json_object_or_400(silent=True)
    if not isinstance(data, dict):
        return data
    confirm = parse_bool_field_or_400(data, "confirm", parse_bool, default=False)
    if not isinstance(confirm, bool):
        return confirm
    return {"confirm": confirm}


def _build_data_download_status_payload(*, status: str, message: str) -> dict[str, str]:
    return {"status": status, "message": message}


def _respond_runtime_data_download_or_400(
    *,
    runtime: Any,
    parse_bool: Callable[[Any, str], bool],
    build_contract_payload_response: Callable[..., ResponseValue],
) -> ResponseValue:
    return parsed_request_response_or_400(
        parse_request=lambda: _parse_data_download_request_or_400(
            parse_bool=parse_bool
        ),
        respond=lambda parsed_request: build_contract_payload_response(
            runtime.trigger_data_download(confirm=parsed_request["confirm"])
        ),
    )


def _respond_fallback_data_download(
    *,
    parse_bool: Callable[[Any, str], bool],
    build_json_payload_response: Callable[..., ResponseValue],
    lock_file_path: Path,
    logger: Any,
    data_download_lock: Any,
    get_data_download_running: Callable[[], bool],
    set_data_download_running: Callable[[bool], None],
    thread_factory: Callable[[Callable[[], None]], Any],
) -> ResponseValue:
    parsed_request = _parse_data_download_request_or_400(parse_bool=parse_bool)
    if not isinstance(parsed_request, dict):
        return parsed_request
    if not parsed_request["confirm"]:
        return build_json_error_response(
            "confirm=true is required when live runtime is unavailable",
            400,
        )

    def _release_lock_file() -> None:
        try:
            lock_file_path.unlink(missing_ok=True)
        except OSError:
            logger.debug(
                "Failed to remove fallback data download lock: path=%s",
                lock_file_path,
                exc_info=True,
            )

    def _lock_file_is_active() -> bool:
        if not lock_file_path.exists():
            return False
        try:
            payload = json.loads(lock_file_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return True
        if not isinstance(payload, dict):
            return True
        try:
            pid = int(payload.get("pid") or 0)
        except (TypeError, ValueError):
            return True
        if pid <= 0:
            return True
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            _release_lock_file()
            return False

    def _acquire_lock_file() -> bool:
        lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        if _lock_file_is_active():
            return False
        try:
            fd = os.open(
                lock_file_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "started_at": datetime.now().isoformat(),
                    },
                    ensure_ascii=False,
                )
            )
        return True

    def _do_download() -> None:
        try:
            MarketDataGateway().sync_background_full_refresh()
        except _WEB_ROUTE_WORKER_EXCEPTIONS:
            logger.exception(
                "后台数据同步失败: source=/api/data/download mode=fallback_background_worker"
            )
        finally:
            with data_download_lock:
                set_data_download_running(False)
            _release_lock_file()

    with data_download_lock:
        if get_data_download_running() or _lock_file_is_active():
            return build_json_payload_response(
                _build_data_download_status_payload(
                    status="running",
                    message="后台同步已在运行",
                )
            )
        if not _acquire_lock_file():
            return build_json_payload_response(
                _build_data_download_status_payload(
                    status="running",
                    message="后台同步已在运行",
                )
            )
        set_data_download_running(True)

    thread = thread_factory(_do_download)
    try:
        thread.start()
    except RuntimeError:
        with data_download_lock:
            set_data_download_running(False)
        _release_lock_file()
        raise
    return build_json_payload_response(
        _build_data_download_status_payload(
            status="started",
            message="后台同步已启动",
        )
    )


def register_runtime_data_routes(
    app: Flask,
    *,
    runtime_facade: RuntimeFacade,
    runtime_not_ready_response: Callable[[], ResponseValue],
    parse_limit_arg: Callable[..., int],
    parse_bool: Callable[[Any, str], bool],
    build_contract_payload_response: Callable[..., ResponseValue],
    build_data_source_unavailable_response: Callable[
        [DataSourceUnavailableError],
        ResponseValue,
    ],
    logger: Any,
    data_download_lock_file_getter: Callable[[], Path],
    data_download_lock: Any,
    get_data_download_running: Callable[[], bool],
    set_data_download_running: Callable[[bool], None],
    thread_factory: Callable[[Callable[[], None]], Any],
) -> None:
    @app.route("/api/data/download", methods=["POST"])
    def api_data_download():
        runtime = runtime_facade.get_runtime()
        if runtime is not None:
            return _respond_runtime_data_download_or_400(
                runtime=runtime,
                parse_bool=parse_bool,
                build_contract_payload_response=build_contract_payload_response,
            )
        return _respond_fallback_data_download(
            parse_bool=parse_bool,
            build_json_payload_response=build_json_payload_response,
            lock_file_path=data_download_lock_file_getter(),
            logger=logger,
            data_download_lock=data_download_lock,
            get_data_download_running=get_data_download_running,
            set_data_download_running=set_data_download_running,
            thread_factory=thread_factory,
        )


def _parse_chat_request_context_or_400(
    data: dict[str, Any],
    *,
    normalize_chat_session_token: Callable[..., str],
) -> dict[str, str] | ResponseValue:
    session_key = parse_value_or_400(
        lambda: normalize_chat_session_token(
            data.get("session_key"),
            field_name="session_key",
            prefix="api:chat",
        )
    )
    if not isinstance(session_key, str):
        return session_key
    chat_id = parse_value_or_400(
        lambda: normalize_chat_session_token(
            data.get("chat_id"),
            field_name="chat_id",
            prefix="chat",
        )
    )
    if not isinstance(chat_id, str):
        return chat_id
    request_id = parse_value_or_400(
        lambda: normalize_chat_session_token(
            data.get("request_id"),
            field_name="request_id",
            prefix="req",
        )
    )
    if not isinstance(request_id, str):
        return request_id
    return {
        "session_key": session_key,
        "chat_id": chat_id,
        "request_id": request_id,
    }


def _parse_view_arg_or_400(
    parse_view_arg: Callable[..., str],
    value: Any,
) -> str | ResponseValue:
    parsed = parse_value_or_400(lambda: parse_view_arg(value))
    return parsed


def _parse_chat_request_or_400(
    *,
    parse_view_arg: Callable[..., str],
    normalize_chat_session_token: Callable[..., str],
) -> dict[str, str] | ResponseValue:
    data = parse_json_object_or_400(force=True)
    if not isinstance(data, dict):
        return data
    message = parse_required_str_field_or_400(data, "message")
    if not isinstance(message, str):
        return message
    view = _parse_view_arg_or_400(
        parse_view_arg,
        data.get("view", request.args.get("view", "json")),
    )
    if not isinstance(view, str):
        return view
    parsed_context = _parse_chat_request_context_or_400(
        data,
        normalize_chat_session_token=normalize_chat_session_token,
    )
    if not isinstance(parsed_context, dict):
        return parsed_context
    return {
        "message": message,
        "view": view,
        **parsed_context,
    }


def _parse_training_plan_request_or_400(
    *,
    parse_bool: Callable[[Any, str], bool],
    parse_detail_mode: Callable[..., str],
) -> dict[str, Any] | ResponseValue:
    data = parse_json_object_or_400(force=True)
    if not isinstance(data, dict):
        return data
    rounds = parse_int_field_or_400(data, "rounds", default=1, minimum=1, maximum=100)
    if not isinstance(rounds, int):
        return rounds
    mock = parse_bool_field_or_400(data, "mock", parse_bool, default=False)
    if not isinstance(mock, bool):
        return mock
    detail_mode = parse_detail_or_400(
        parse_detail_mode,
        raw_value=data.get("detail_mode", "fast"),
        field_name="detail_mode",
    )
    if not isinstance(detail_mode, str):
        return detail_mode
    tags = parse_str_list_field_or_400(data, "tags", default=[])
    if not isinstance(tags, list):
        return tags
    return {
        "rounds": rounds,
        "mock": mock,
        "goal": read_str_field(data, "goal"),
        "notes": read_str_field(data, "notes"),
        "tags": tags,
        "detail_mode": detail_mode,
        "protocol": read_object_field(data, "protocol"),
        "dataset": read_object_field(data, "dataset"),
        "manager_scope": read_object_field(data, "manager_scope"),
        "optimization": read_object_field(data, "optimization"),
    }


def _respond_chat_request(
    *,
    runtime: Any,
    parsed_request: dict[str, str],
    run_async: Callable[[Any], Any],
    respond_with_display: Callable[..., ResponseValue],
    logger: Any,
) -> ResponseValue:
    try:
        reply = run_async(
            runtime.ask(
                parsed_request["message"],
                session_key=parsed_request["session_key"],
                channel="api",
                chat_id=parsed_request["chat_id"],
                request_id=parsed_request["request_id"],
            )
        )
        payload = normalize_ask_reply_payload(
            reply,
            session_key=parsed_request["session_key"],
            chat_id=parsed_request["chat_id"],
            request_id=parsed_request["request_id"],
        )
        return respond_with_display(payload, view=parsed_request["view"])
    except ValueError as exc:
        return build_json_error_response(str(exc), 400)
    except _WEB_ROUTE_INTERNAL_EXCEPTIONS as exc:
        logger.exception(
            "Chat route failed: session_key=%s chat_id=%s request_id=%s view=%s message_length=%s",
            parsed_request["session_key"],
            parsed_request["chat_id"],
            parsed_request["request_id"],
            parsed_request["view"],
            len(parsed_request["message"]),
        )
        return build_json_error_response(str(exc), 500)


def _respond_training_plan_execution(
    *,
    runtime: Any,
    plan_id: str,
    run_async: Callable[[Any], Any],
    build_contract_payload_response: Callable[..., ResponseValue],
    build_data_source_unavailable_response: Callable[
        [DataSourceUnavailableError],
        ResponseValue,
    ],
    logger: Any,
) -> ResponseValue:
    try:
        payload = run_async(runtime.execute_training_plan(plan_id))
        return build_contract_payload_response(payload)
    except FileNotFoundError as exc:
        return build_not_found_response(exc)
    except DataSourceUnavailableError as exc:
        logger.warning(
            "Training plan execution data source unavailable: plan_id=%s error=%s",
            plan_id,
            exc,
        )
        return build_data_source_unavailable_response(exc)
    except _WEB_ROUTE_INTERNAL_EXCEPTIONS as exc:
        logger.exception("Training plan execution route failed: plan_id=%s", plan_id)
        return build_json_error_response(str(exc), 500)


def _respond_training_plan_create(
    *,
    runtime: Any,
    parsed_request: dict[str, Any],
    build_contract_payload_response: Callable[..., ResponseValue],
) -> ResponseValue:
    plan = runtime.create_training_plan(
        rounds=parsed_request["rounds"],
        mock=parsed_request["mock"],
        goal=parsed_request["goal"],
        notes=parsed_request["notes"],
        tags=parsed_request["tags"],
        detail_mode=parsed_request["detail_mode"],
        protocol=parsed_request["protocol"],
        dataset=parsed_request["dataset"],
        manager_scope=parsed_request["manager_scope"],
        optimization=parsed_request["optimization"],
        source="api",
    )
    return build_contract_payload_response(plan, status_code=201)


def _build_sse_event_chunk(event_name: str, payload: Any) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_sse_keepalive_chunk() -> str:
    return ": keepalive\n\n"


def _start_chat_stream_worker(
    *,
    runtime: Any,
    message: str,
    session_key: str,
    chat_id: str,
    request_id: str,
    run_async: Callable[[Any], Any],
    logger: Any,
) -> tuple[dict[str, Any], threading.Event]:
    result_holder: dict[str, Any] = {}
    completed = threading.Event()

    def run_request() -> None:
        try:
            result_holder["reply"] = run_async(
                runtime.ask(
                    message,
                    session_key=session_key,
                    channel="api",
                    chat_id=chat_id,
                    request_id=request_id,
                )
            )
        except _WEB_ROUTE_WORKER_EXCEPTIONS as exc:
            logger.exception(
                "Chat stream route failed: session_key=%s chat_id=%s request_id=%s message_length=%s",
                session_key,
                chat_id,
                request_id,
                len(message),
            )
            result_holder["error"] = str(exc)
        finally:
            completed.set()

    worker = threading.Thread(
        target=run_request,
        name=f"chat-stream-{request_id}",
        daemon=True,
    )
    worker.start()
    return result_holder, completed


def _yield_chat_stream_runtime_event_chunks(
    *,
    event_queue: Any,
    completed: threading.Event,
) -> Iterator[str]:
    while True:
        try:
            event_payload = event_queue.get(timeout=0.5)
            yield _build_sse_event_chunk("runtime_event", event_payload)
            continue
        except queue.Empty:
            if completed.is_set():
                break
            yield _build_sse_keepalive_chunk()
    while True:
        try:
            event_payload = event_queue.get_nowait()
        except queue.Empty:
            break
        yield _build_sse_event_chunk("runtime_event", event_payload)


def _respond_chat_stream(
    *,
    runtime: Any,
    parsed_request: dict[str, str],
    run_async: Callable[[Any], Any],
    logger: Any,
) -> ResponseValue:
    subscription_id, event_queue = runtime.subscribe_event_stream(
        session_key=parsed_request["session_key"],
        chat_id=parsed_request["chat_id"],
        request_id=parsed_request["request_id"],
    )
    result_holder, completed = _start_chat_stream_worker(
        runtime=runtime,
        message=parsed_request["message"],
        session_key=parsed_request["session_key"],
        chat_id=parsed_request["chat_id"],
        request_id=parsed_request["request_id"],
        run_async=run_async,
        logger=logger,
    )

    def generate() -> Iterator[str]:
        try:
            yield _build_sse_event_chunk(
                "connected",
                {
                    "status": "connected",
                    "session_key": parsed_request["session_key"],
                    "chat_id": parsed_request["chat_id"],
                    "request_id": parsed_request["request_id"],
                },
            )
            yield from _yield_chat_stream_runtime_event_chunks(
                event_queue=event_queue,
                completed=completed,
            )

            summary_payload = runtime.build_stream_summary_packet(subscription_id)
            yield _build_sse_event_chunk("summary", summary_payload)

            if "error" in result_holder:
                yield _build_sse_event_chunk(
                    "error",
                    {
                        "error": result_holder["error"],
                        "request_id": parsed_request["request_id"],
                    },
                )
                return

            payload = normalize_ask_reply_payload(
                result_holder.get("reply"),
                session_key=parsed_request["session_key"],
                chat_id=parsed_request["chat_id"],
                request_id=parsed_request["request_id"],
            )
            payload = runtime.merge_stream_summary_into_reply_payload(
                payload,
                summary_payload,
            )
            yield _build_sse_event_chunk("reply", payload)
            yield _build_sse_event_chunk(
                "done",
                {
                    "status": "completed",
                    "request_id": parsed_request["request_id"],
                },
            )
        finally:
            runtime.unsubscribe_event_stream(subscription_id)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def register_runtime_command_routes(
    app: Flask,
    *,
    runtime_facade: RuntimeFacade,
    runtime_not_ready_response: Callable[[], ResponseValue],
    parse_view_arg: Callable[..., str],
    parse_bool: Callable[[Any, str], bool],
    parse_detail_mode: Callable[..., str],
    normalize_chat_session_token: Callable[..., str],
    respond_with_display: Callable[..., ResponseValue],
    build_contract_payload_response: Callable[..., ResponseValue],
    run_async: Callable[[Any], Any],
    build_data_source_unavailable_response: Callable[
        [DataSourceUnavailableError],
        ResponseValue,
    ],
    logger: Any,
) -> None:
    @app.route("/api/chat", methods=["POST"])
    def api_chat():
        return _respond_runtime_operation(
            runtime_facade=runtime_facade,
            runtime_not_ready_response=runtime_not_ready_response,
            require_loop=True,
            parse_request=lambda: _parse_chat_request_or_400(
                parse_view_arg=parse_view_arg,
                normalize_chat_session_token=normalize_chat_session_token,
            ),
            handle_parsed_runtime=lambda runtime, parsed_request: _respond_chat_request(
                runtime=runtime,
                parsed_request=parsed_request,
                run_async=run_async,
                respond_with_display=respond_with_display,
                logger=logger,
            ),
        )

    @app.route("/api/chat/stream", methods=["POST"])
    def api_chat_stream():
        return _respond_runtime_operation(
            runtime_facade=runtime_facade,
            runtime_not_ready_response=runtime_not_ready_response,
            require_loop=True,
            parse_request=lambda: _parse_chat_request_or_400(
                parse_view_arg=parse_view_arg,
                normalize_chat_session_token=normalize_chat_session_token,
            ),
            handle_parsed_runtime=lambda runtime, parsed_request: _respond_chat_stream(
                runtime=runtime,
                parsed_request=parsed_request,
                run_async=run_async,
                logger=logger,
            ),
        )

    @app.route("/api/lab/training/plans", methods=["POST"])
    def api_training_plan_create():
        return _respond_runtime_operation(
            runtime_facade=runtime_facade,
            runtime_not_ready_response=runtime_not_ready_response,
            parse_request=lambda: _parse_training_plan_request_or_400(
                parse_bool=parse_bool,
                parse_detail_mode=parse_detail_mode,
            ),
            handle_parsed_runtime=lambda runtime, parsed_request: (
                _respond_training_plan_create(
                    runtime=runtime,
                    parsed_request=parsed_request,
                    build_contract_payload_response=build_contract_payload_response,
                )
            ),
        )

    @app.route("/api/lab/training/plans/<plan_id>/execute", methods=["POST"])
    def api_training_plan_execute(plan_id: str):
        return _respond_runtime_operation(
            runtime_facade=runtime_facade,
            runtime_not_ready_response=runtime_not_ready_response,
            require_loop=True,
            handle_runtime=lambda runtime: _respond_training_plan_execution(
                runtime=runtime,
                plan_id=plan_id,
                run_async=run_async,
                build_contract_payload_response=build_contract_payload_response,
                build_data_source_unavailable_response=build_data_source_unavailable_response,
                logger=logger,
            ),
        )


def _as_object_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _as_object_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return list(value)


def _payload_keys(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    return sorted(str(key) for key in value)


def _with_runtime_handler(
    *,
    runtime_facade: RuntimeFacade,
    runtime_not_ready_response: Callable[[], ResponseValue],
    handler: Callable[[Any], ResponseValue],
    require_loop: bool = False,
) -> ResponseValue:
    runtime = _load_runtime_or_not_ready(
        runtime_facade=runtime_facade,
        runtime_not_ready_response=runtime_not_ready_response,
        require_loop=require_loop,
    )
    if is_route_error_response(runtime):
        return runtime
    return handler(runtime)


def _parse_agent_prompt_update_request_or_400() -> dict[str, Any] | ResponseValue:
    data = parse_json_object_or_400(force=True)
    if not isinstance(data, dict):
        return data
    agent_name = parse_required_str_field_or_400(data, "name")
    if not isinstance(agent_name, str):
        return agent_name
    unexpected_keys = sorted(set(data.keys()) - {"name", "system_prompt"})
    if unexpected_keys:
        if unexpected_keys == ["llm_model"]:
            return build_json_error_response(
                "llm_model is not editable on /api/agent_prompts; use /api/control_plane for model binding",
                400,
            )
        return build_json_error_response(
            f"unsupported fields for agent_prompts: {', '.join(unexpected_keys)}",
            400,
        )
    if "system_prompt" not in data:
        return build_json_error_response("system_prompt is required", 400)
    return {
        "data": data,
        "name": agent_name,
        "system_prompt": read_str_field(data, "system_prompt"),
    }


def _parse_patch_request_or_400() -> dict[str, Any] | ResponseValue:
    return parse_json_object_or_400(force=True)


def _parse_data_status_request_or_400(
    *,
    parse_bool: Callable[[Any, str], bool],
) -> dict[str, bool] | ResponseValue:
    refresh = parse_query_bool_or_400("refresh", parse_bool, default=False)
    if not isinstance(refresh, bool):
        return refresh
    return {"refresh": refresh}


def _execute_config_update(
    *,
    runtime_facade: RuntimeFacade,
    build_contract_payload_response: Callable[..., ResponseValue],
    runtime_update: Callable[[Any], Any],
    fallback_update: Callable[[], dict[str, Any]],
    logger: Any,
    error_label: str,
    request_payload: Any = None,
) -> ResponseValue:
    runtime = None
    payload_keys = _payload_keys(request_payload)
    try:
        runtime = runtime_facade.get_runtime()
        if runtime is not None:
            return build_contract_payload_response(runtime_update(runtime))
        return build_contract_payload_response(fallback_update())
    except ValueError as exc:
        return build_json_status_error_response(str(exc), 400)
    except _WEB_ROUTE_INTERNAL_EXCEPTIONS as exc:
        logger.exception(
            "%s error: runtime_loaded=%s payload_keys=%s",
            error_label,
            runtime is not None,
            payload_keys,
        )
        return build_json_status_error_response(str(exc), 500)


def _respond_config_surface_read(
    *,
    surface: str,
    runtime_facade: RuntimeFacade,
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
) -> ResponseValue:
    spec = _resolve_config_surface_read_spec(surface)

    return _respond_runtime_or_fallback_read(
        runtime_facade=runtime_facade,
        request_view_arg=request_view_arg,
        respond_with_display=respond_with_display,
        runtime_fetch=spec.runtime_fetch,
        fallback_fetch=spec.fallback_fetch,
    )


def _respond_config_surface_update(
    *,
    surface: str,
    parsed_request: dict[str, Any],
    runtime_facade: RuntimeFacade,
    build_contract_payload_response: Callable[..., ResponseValue],
    logger: Any,
) -> ResponseValue:
    spec = _resolve_config_surface_update_spec(surface)
    if spec.validate_payload is not None:
        try:
            spec.validate_payload(parsed_request)
        except ConfigSurfaceValidationError as exc:
            extra = (
                {"invalid_keys": list(exc.invalid_keys)}
                if exc.invalid_keys
                else {}
            )
            return build_json_status_error_response(str(exc), 400, **extra)
    return _execute_config_update(
        runtime_facade=runtime_facade,
        build_contract_payload_response=build_contract_payload_response,
        runtime_update=lambda runtime: spec.runtime_update(runtime, parsed_request),
        fallback_update=lambda: spec.fallback_update(parsed_request),
        logger=logger,
        error_label=spec.error_label,
        request_payload=spec.request_payload(parsed_request),
    )


def _respond_parsed_config_surface_update(
    *,
    surface: str,
    parse_request: Callable[[], dict[str, Any] | ResponseValue],
    runtime_facade: RuntimeFacade,
    build_contract_payload_response: Callable[..., ResponseValue],
    logger: Any,
) -> ResponseValue:
    return parsed_request_response_or_400(
        parse_request=parse_request,
        respond=lambda parsed_request: _respond_config_surface_update(
            surface=surface,
            parsed_request=parsed_request,
            runtime_facade=runtime_facade,
            build_contract_payload_response=build_contract_payload_response,
            logger=logger,
        ),
    )


def _resolve_config_surface_update_parser(
    route_spec: ConfigSurfaceRouteSpec,
) -> Callable[[], dict[str, Any] | ResponseValue]:
    if route_spec.update_request_kind == "agent_prompt":
        return _parse_agent_prompt_update_request_or_400
    if route_spec.update_request_kind == "patch_object":
        return _parse_patch_request_or_400
    raise ValueError(
        f"unsupported config surface request kind: {route_spec.update_request_kind}"
    )


def _make_config_surface_read_view(
    *,
    route_spec: ConfigSurfaceRouteSpec,
    runtime_facade: RuntimeFacade,
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
) -> Callable[[], ResponseValue]:
    def _view() -> ResponseValue:
        return _respond_config_surface_read(
            surface=route_spec.surface,
            runtime_facade=runtime_facade,
            request_view_arg=request_view_arg,
            respond_with_display=respond_with_display,
        )

    return _view


def _make_config_surface_update_view(
    *,
    route_spec: ConfigSurfaceRouteSpec,
    runtime_facade: RuntimeFacade,
    build_contract_payload_response: Callable[..., ResponseValue],
    logger: Any,
) -> Callable[[], ResponseValue]:
    parse_request = _resolve_config_surface_update_parser(route_spec)

    def _view() -> ResponseValue:
        return _respond_parsed_config_surface_update(
            surface=route_spec.surface,
            parse_request=parse_request,
            runtime_facade=runtime_facade,
            build_contract_payload_response=build_contract_payload_response,
            logger=logger,
        )

    return _view


def register_runtime_ops_routes(
    app: Flask,
    *,
    runtime_facade: RuntimeFacade,
    runtime_not_ready_response: Callable[[], ResponseValue],
    request_view_arg: Callable[[], str],
    parse_bool: Callable[[Any, str], bool],
    parse_int: Callable[..., int],
    respond_with_display: Callable[..., ResponseValue],
    build_contract_payload_response: Callable[..., ResponseValue],
    logger: Any,
) -> None:
    for route_spec in build_config_surface_route_specs():
        app.add_url_rule(
            route_spec.path,
            endpoint=f"api_{route_spec.surface}_get",
            view_func=_make_config_surface_read_view(
                route_spec=route_spec,
                runtime_facade=runtime_facade,
                request_view_arg=request_view_arg,
                respond_with_display=respond_with_display,
            ),
            methods=["GET"],
        )
        app.add_url_rule(
            route_spec.path,
            endpoint=f"api_{route_spec.surface}_update",
            view_func=_make_config_surface_update_view(
                route_spec=route_spec,
                runtime_facade=runtime_facade,
                build_contract_payload_response=build_contract_payload_response,
                logger=logger,
            ),
            methods=["POST"],
        )

    @app.route("/api/data/status", methods=["GET"])
    def api_data_status():
        return _respond_parsed_runtime_or_fallback_read(
            parse_request=lambda: _parse_data_status_request_or_400(
                parse_bool=parse_bool
            ),
            runtime_facade=runtime_facade,
            request_view_arg=request_view_arg,
            respond_with_display=respond_with_display,
            runtime_fetch=lambda runtime, parsed_request: runtime.get_data_status(
                refresh=parsed_request["refresh"]
            ),
            fallback_fetch=lambda parsed_request: {
                **get_data_status_payload(refresh=parsed_request["refresh"]),
                "detail_mode": "slow" if parsed_request["refresh"] else "fast",
            },
        )


def register_runtime_contract_routes(
    app: Flask,
    *,
    serve_contract_document: Callable[[str], ResponseValue],
) -> None:
    def _make_contract_view(document_id: str) -> Callable[[], ResponseValue]:
        def _view() -> ResponseValue:
            return serve_contract_document(document_id)

        return _view

    for route_path, endpoint_name, document_id in RUNTIME_CONTRACT_ROUTE_SPECS:
        app.add_url_rule(
            route_path,
            endpoint=endpoint_name,
            view_func=_make_contract_view(document_id),
            methods=["GET"],
        )
