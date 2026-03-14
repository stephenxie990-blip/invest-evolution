"""Route registration for command-style runtime endpoints."""

from __future__ import annotations

import json
import queue
import threading
from typing import Any, Callable

from flask import Flask, Response, jsonify, request, stream_with_context

from market_data import DataSourceUnavailableError


ResponseValue = Any
RuntimeGetter = Callable[[], Any]
LoopGetter = Callable[[], Any]


def _runtime_or_not_ready(
    *,
    get_runtime: RuntimeGetter,
    get_loop: LoopGetter,
    runtime_not_ready_response: Callable[[], ResponseValue],
    require_loop: bool,
) -> Any:
    runtime = get_runtime()
    if runtime is None:
        return runtime_not_ready_response()
    if require_loop and get_loop() is None:
        return runtime_not_ready_response()
    return runtime


def register_runtime_command_routes(
    app: Flask,
    *,
    get_runtime: RuntimeGetter,
    get_loop: LoopGetter,
    runtime_not_ready_response: Callable[[], ResponseValue],
    parse_view_arg: Callable[..., str],
    parse_bool: Callable[[Any, str], bool],
    parse_detail_mode: Callable[..., str],
    normalize_chat_session_token: Callable[..., str],
    respond_with_display: Callable[..., ResponseValue],
    jsonify_contract_payload: Callable[..., ResponseValue],
    run_async: Callable[[Any], Any],
    data_source_unavailable_response: Callable[[DataSourceUnavailableError], ResponseValue],
    logger: Any,
) -> None:
    @app.route("/api/chat", methods=["POST"])
    def api_chat():
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            get_loop=get_loop,
            runtime_not_ready_response=runtime_not_ready_response,
            require_loop=True,
        )
        if isinstance(runtime, tuple):
            return runtime

        data = request.get_json(force=True) or {}
        message = str(data.get("message", "")).strip()
        if not message:
            return jsonify({"error": "message is required"}), 400
        try:
            view = parse_view_arg(data.get("view", request.args.get("view", "json")))
            session_key = normalize_chat_session_token(
                data.get("session_key"),
                field_name="session_key",
                prefix="api:chat",
            )
            chat_id = normalize_chat_session_token(
                data.get("chat_id"),
                field_name="chat_id",
                prefix="chat",
            )
            request_id = normalize_chat_session_token(
                data.get("request_id"),
                field_name="request_id",
                prefix="req",
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            reply = run_async(
                runtime.ask(
                    message,
                    session_key=session_key,
                    channel="api",
                    chat_id=chat_id,
                    request_id=request_id,
                )
            )
            try:
                payload = json.loads(reply) if isinstance(reply, str) else dict(reply or {})
            except Exception:
                payload = {"reply": str(reply)}
            if not isinstance(payload, dict):
                payload = {"reply": str(reply)}
            payload.setdefault("reply", str(payload.get("message") or reply))
            payload.setdefault("message", str(payload.get("reply") or ""))
            payload.setdefault("session_key", session_key)
            payload.setdefault("chat_id", chat_id)
            payload.setdefault("request_id", request_id)
            return respond_with_display(payload, view=view)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            logger.exception("Chat error")
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/chat/stream", methods=["POST"])
    def api_chat_stream():
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            get_loop=get_loop,
            runtime_not_ready_response=runtime_not_ready_response,
            require_loop=True,
        )
        if isinstance(runtime, tuple):
            return runtime

        data = request.get_json(force=True) or {}
        message = str(data.get("message", "")).strip()
        if not message:
            return jsonify({"error": "message is required"}), 400
        try:
            session_key = normalize_chat_session_token(
                data.get("session_key"),
                field_name="session_key",
                prefix="api:chat",
            )
            chat_id = normalize_chat_session_token(
                data.get("chat_id"),
                field_name="chat_id",
                prefix="chat",
            )
            request_id = normalize_chat_session_token(
                data.get("request_id"),
                field_name="request_id",
                prefix="req",
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        subscription_id, event_queue = runtime.subscribe_event_stream(
            session_key=session_key,
            chat_id=chat_id,
            request_id=request_id,
        )
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
            except Exception as exc:
                result_holder["error"] = str(exc)
            finally:
                completed.set()

        worker = threading.Thread(target=run_request, name=f"chat-stream-{request_id}", daemon=True)
        worker.start()

        def generate():
            try:
                yield (
                    "event: connected\n"
                    f"data: {json.dumps({'status': 'connected', 'session_key': session_key, 'chat_id': chat_id, 'request_id': request_id}, ensure_ascii=False)}\n\n"
                )
                while True:
                    try:
                        event_payload = event_queue.get(timeout=0.5)
                        yield f"event: runtime_event\ndata: {json.dumps(event_payload, ensure_ascii=False)}\n\n"
                        continue
                    except queue.Empty:
                        if completed.is_set():
                            break
                        yield ": keepalive\n\n"
                while True:
                    try:
                        event_payload = event_queue.get_nowait()
                    except queue.Empty:
                        break
                    yield f"event: runtime_event\ndata: {json.dumps(event_payload, ensure_ascii=False)}\n\n"

                summary_payload = runtime.build_stream_summary_packet(subscription_id)
                yield f"event: summary\ndata: {json.dumps(summary_payload, ensure_ascii=False)}\n\n"

                if "error" in result_holder:
                    yield f"event: error\ndata: {json.dumps({'error': result_holder['error'], 'request_id': request_id}, ensure_ascii=False)}\n\n"
                    return

                reply = result_holder.get("reply")
                try:
                    payload = json.loads(reply) if isinstance(reply, str) else dict(reply or {})
                except Exception:
                    payload = {"reply": str(reply)}
                if not isinstance(payload, dict):
                    payload = {"reply": str(reply)}
                payload.setdefault("reply", str(payload.get("message") or reply or ""))
                payload.setdefault("message", str(payload.get("reply") or ""))
                payload.setdefault("session_key", session_key)
                payload.setdefault("chat_id", chat_id)
                payload.setdefault("request_id", request_id)
                payload = runtime.merge_stream_summary_into_reply_payload(payload, summary_payload)
                yield f"event: reply\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                yield f"event: done\ndata: {json.dumps({'status': 'completed', 'request_id': request_id}, ensure_ascii=False)}\n\n"
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

    @app.route("/api/train", methods=["POST"])
    def api_train():
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            get_loop=get_loop,
            runtime_not_ready_response=runtime_not_ready_response,
            require_loop=True,
        )
        if isinstance(runtime, tuple):
            return runtime

        data = request.get_json(force=True) or {}
        try:
            view = parse_view_arg(data.get("view", request.args.get("view", "json")))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            rounds = max(1, min(100, int(data.get("rounds", 1))))
        except (TypeError, ValueError):
            return jsonify({"error": "rounds must be an integer"}), 400
        try:
            mock = parse_bool(data.get("mock", False), "mock")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            result = run_async(runtime.train_once(rounds=rounds, mock=mock))
            return respond_with_display(result, view=view)
        except DataSourceUnavailableError as exc:
            logger.warning("Train data source unavailable: %s", exc)
            return data_source_unavailable_response(exc)
        except Exception as exc:
            logger.exception("Train error")
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/lab/training/plans", methods=["POST"])
    def api_training_plan_create():
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            get_loop=get_loop,
            runtime_not_ready_response=runtime_not_ready_response,
            require_loop=False,
        )
        if isinstance(runtime, tuple):
            return runtime

        data = request.get_json(force=True) or {}
        try:
            rounds = max(1, min(100, int(data.get("rounds", 1))))
        except (TypeError, ValueError):
            return jsonify({"error": "rounds must be an integer"}), 400
        try:
            mock = parse_bool(data.get("mock", False), "mock")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            detail_mode = parse_detail_mode(
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
        return jsonify_contract_payload(plan, 201)

    @app.route("/api/lab/training/plans/<plan_id>/execute", methods=["POST"])
    def api_training_plan_execute(plan_id: str):
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            get_loop=get_loop,
            runtime_not_ready_response=runtime_not_ready_response,
            require_loop=True,
        )
        if isinstance(runtime, tuple):
            return runtime
        try:
            payload = run_async(runtime.execute_training_plan(plan_id))
            return jsonify_contract_payload(payload)
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except DataSourceUnavailableError as exc:
            logger.warning("Training plan execution data source unavailable: %s", exc)
            return data_source_unavailable_response(exc)
        except Exception as exc:
            logger.exception("Training plan execution error")
            return jsonify({"error": str(exc)}), 500
