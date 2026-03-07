"""
投资进化系统 - Web 前端服务器

Flask 应用，包装 CommanderRuntime 提供 REST API。
启动方式：
    source .venv/bin/activate
    python web_server.py [--mock] [--port 8080]

浏览器打开：http://localhost:8080
"""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
import json
import logging
from queue import Full, Queue
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory, Response, stream_with_context

from app.commander import CommanderConfig, CommanderRuntime, _apply_runtime_path_overrides
from app.train import set_event_callback
from config.services import EvolutionConfigService, RuntimePathConfigService
from invest.meetings import MeetingRecorder

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
    return future.result(timeout=300)


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




def _runtime_not_ready_response():
    return jsonify({
        "error": "Commander runtime is not initialized. Start server with `python web_server.py`.",
    }), 503


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    static_folder=str(Path(__file__).parent.parent / "static"),
    static_url_path="/static",
)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---- Status ----

@app.route("/api/status")
def api_status():
    runtime = _runtime
    if runtime is None:
        return _runtime_not_ready_response()
    return jsonify(runtime.status())


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
            runtime.ask(message, session_key="web:chat", channel="web", chat_id="chat")
        )
        return jsonify({"reply": reply})
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
        mock = _parse_bool(data.get("mock", True), "mock")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        result = _run_async(runtime.train_once(rounds=rounds, mock=mock))
        return jsonify(result)
    except Exception as exc:
        logger.exception("Train error")
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
    return jsonify(result)


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
    return jsonify({"count": len(rows), "items": rows})


# ---- Agent Configs ----

@app.route("/api/agent_configs", methods=["GET"])
def api_agent_configs_list():
    from config import agent_config_registry
    return jsonify({
        "configs": agent_config_registry.list_configs()
    })

@app.route("/api/agent_configs", methods=["POST"])
def api_agent_configs_update():
    from config import agent_config_registry
    data = request.get_json(force=True) or {}
    agent_name = data.get("name")
    if not agent_name:
        return jsonify({"error": "name is required"}), 400
        
    current_cfg = agent_config_registry.get_config(agent_name)
    current_cfg["llm_model"] = data.get("llm_model", current_cfg.get("llm_model"))
    current_cfg["system_prompt"] = data.get("system_prompt", current_cfg.get("system_prompt"))
    
    ok = agent_config_registry.save_config(agent_name, current_cfg)
    return jsonify({"status": "ok" if ok else "error"})


# ---- Runtime Paths ----

@app.route("/api/runtime_paths", methods=["GET"])
def api_runtime_paths_get():
    import config as config_module

    service = RuntimePathConfigService(project_root=config_module.PROJECT_ROOT)
    payload = service.get_payload()
    if _runtime is not None:
        payload.update({
            "training_output_dir": str(_runtime.cfg.training_output_dir),
            "meeting_log_dir": str(_runtime.cfg.meeting_log_dir),
            "config_audit_log_path": str(_runtime.cfg.config_audit_log_path),
            "config_snapshot_dir": str(_runtime.cfg.config_snapshot_dir),
            "runtime_loaded": True,
        })
    else:
        payload["runtime_loaded"] = False
    return jsonify({"status": "ok", "config": payload})


@app.route("/api/runtime_paths", methods=["POST"])
def api_runtime_paths_update():
    import config as config_module

    data = request.get_json(force=True) or {}
    service = RuntimePathConfigService(project_root=config_module.PROJECT_ROOT)
    try:
        payload = service.apply_patch(data)
        if _runtime is not None:
            _sync_runtime_path_config(_runtime, payload["config"])
            payload["config"].update({
                "training_output_dir": str(_runtime.cfg.training_output_dir),
                "meeting_log_dir": str(_runtime.cfg.meeting_log_dir),
                "config_audit_log_path": str(_runtime.cfg.config_audit_log_path),
                "config_snapshot_dir": str(_runtime.cfg.config_snapshot_dir),
                "runtime_loaded": True,
            })
        else:
            payload["config"]["runtime_loaded"] = False
        return jsonify({"status": "ok", "updated": payload["updated"], "config": payload["config"]})
    except ValueError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Failed to update runtime path config")
        return jsonify({"status": "error", "error": str(exc)}), 500


# ---- Evolution Config (Models/Data) ----

@app.route("/api/evolution_config", methods=["GET"])
def api_evolution_config_get():
    import config as config_module

    service = EvolutionConfigService(project_root=config_module.PROJECT_ROOT, live_config=config_module.config)
    return jsonify({"status": "ok", "config": service.get_masked_payload()})


@app.route("/api/evolution_config", methods=["POST"])
def api_evolution_config_update():
    import config as config_module

    data = request.get_json(force=True) or {}
    service = EvolutionConfigService(project_root=config_module.PROJECT_ROOT, live_config=config_module.config)
    try:
        payload = service.apply_patch(data, source="web_api")
        return jsonify({"status": "ok", "updated": payload["updated"], "config": payload["config"]})
    except ValueError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Failed to update evolution config")
        return jsonify({"status": "error", "error": str(exc)}), 500


# ---- Data Management ----

@app.route("/api/data/status", methods=["GET"])
def api_data_status():
    from market_data.datasets import WebDatasetService

    status = WebDatasetService().get_status_summary()
    return jsonify(status)

@app.route("/api/data/download", methods=["POST"])
def api_data_download():
    def _do_download():
        from market_data.ingestion import DataIngestionService

        try:
            service = DataIngestionService()
            logger.info("开始后台同步股票主数据...")
            service.sync_security_master()
            logger.info("开始后台同步日线数据...")
            service.sync_daily_bars()
            logger.info("后台数据同步完成")
        except Exception as e:
            logger.exception(f"后台数据同步失败: {e}")

    t = threading.Thread(target=_do_download, daemon=True)
    t.start()
    return jsonify({"status": "started", "message": "后台同步已启动"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _loop, _runtime

    parser = argparse.ArgumentParser(description="投资进化系统 Web 前端")
    parser.add_argument("--port", type=int, default=8080, help="服务端口 (默认 8080)")
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址 (默认 127.0.0.1)")
    parser.add_argument("--mock", action="store_true", help="使用模拟数据 (无需真实行情)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Build commander runtime
    cfg = CommanderConfig.from_args(argparse.Namespace())
    if args.mock:
        cfg.mock_mode = True
    cfg.autopilot_enabled = False  # Web mode: manual trigger only
    cfg.heartbeat_enabled = False
    cfg.bridge_enabled = False

    # 设置训练事件回调
    set_event_callback(_event_sink)

    _runtime = CommanderRuntime(cfg)

    # Start async event loop in background thread
    _loop = asyncio.new_event_loop()
    t = threading.Thread(target=_start_event_loop, args=(_loop,), daemon=True)
    t.start()

    # Start cron service etc.
    _run_async(_runtime.start())

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
