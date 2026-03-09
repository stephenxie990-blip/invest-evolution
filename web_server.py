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
import json
import logging
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from commander import CommanderConfig, CommanderRuntime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Async bridge — run async CommanderRuntime methods from sync Flask handlers
# ---------------------------------------------------------------------------

_loop: asyncio.AbstractEventLoop | None = None
_runtime: CommanderRuntime | None = None


def _start_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _run_async(coro: Any) -> Any:
    """Submit a coroutine to the background event loop and wait for result."""
    assert _loop is not None
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=300)


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    static_folder=str(Path(__file__).parent / "static"),
    static_url_path="/static",
)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---- Status ----

@app.route("/api/status")
def api_status():
    return jsonify(_runtime.status())


# ---- Chat ----

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True) or {}
    message = str(data.get("message", "")).strip()
    if not message:
        return jsonify({"error": "message is required"}), 400
    try:
        reply = _run_async(
            _runtime.ask(message, session_key="web:chat", channel="web", chat_id="chat")
        )
        return jsonify({"reply": reply})
    except Exception as exc:
        logger.exception("Chat error")
        return jsonify({"error": str(exc)}), 500


# ---- Train ----

@app.route("/api/train", methods=["POST"])
def api_train():
    data = request.get_json(force=True) or {}
    rounds = max(1, min(100, int(data.get("rounds", 1))))
    mock = bool(data.get("mock", True))
    try:
        result = _run_async(_runtime.train_once(rounds=rounds, mock=mock))
        return jsonify(result)
    except Exception as exc:
        logger.exception("Train error")
        return jsonify({"error": str(exc)}), 500


# ---- Strategies ----

@app.route("/api/strategies")
def api_strategies():
    genes = _runtime.strategy_registry.list_genes()
    return jsonify({
        "count": len(genes),
        "items": [g.to_dict() for g in genes],
    })


@app.route("/api/strategies/reload", methods=["POST"])
def api_strategies_reload():
    result = _runtime.reload_strategies()
    return jsonify(result)


# ---- Cron ----

@app.route("/api/cron")
def api_cron_list():
    rows = [j.to_dict() for j in _runtime.cron.list_jobs()]
    return jsonify({"count": len(rows), "items": rows})


@app.route("/api/cron", methods=["POST"])
def api_cron_add():
    data = request.get_json(force=True) or {}
    name = str(data.get("name", "")).strip()
    message = str(data.get("message", "")).strip()
    every_sec = int(data.get("every_sec", 3600))
    if not name or not message:
        return jsonify({"error": "name and message are required"}), 400
    job = _runtime.cron.add_job(
        name=name, message=message, every_sec=every_sec,
        deliver=bool(data.get("deliver", False)),
        channel=str(data.get("channel", "web")),
        to=str(data.get("to", "commander")),
    )
    _runtime._persist_state()
    return jsonify({"status": "ok", "job": job.to_dict()})


@app.route("/api/cron/<job_id>", methods=["DELETE"])
def api_cron_remove(job_id: str):
    ok = _runtime.cron.remove_job(job_id)
    _runtime._persist_state()
    return jsonify({"status": "ok" if ok else "not_found", "job_id": job_id})


# ---- Memory ----

@app.route("/api/memory")
def api_memory():
    query = request.args.get("q", "")
    limit = min(200, max(1, int(request.args.get("limit", 20))))
    rows = _runtime.memory.search(query=query, limit=limit)
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


# ---- Data Management ----

@app.route("/api/data/status", methods=["GET"])
def api_data_status():
    from data import DataCache
    status = DataCache().get_status_summary()
    return jsonify(status)

@app.route("/api/data/download", methods=["POST"])
def api_data_download():
    def _do_download():
        from data import DataCache
        try:
            logger.info("开始后台下载股票基本信息...")
            DataCache().download_stock_info()
            logger.info("开始后台下载日K线...")
            # 默认下载最近的数据
            DataCache().download_daily_kline()
            logger.info("后台数据下载完成")
        except Exception as e:
            logger.exception(f"后台数据下载失败: {e}")

    t = threading.Thread(target=_do_download, daemon=True)
    t.start()
    return jsonify({"status": "started", "message": "后台下载已启动"})


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
    cfg = CommanderConfig()
    if args.mock:
        cfg.mock_mode = True
    cfg.autopilot_enabled = False  # Web mode: manual trigger only
    cfg.heartbeat_enabled = False
    cfg.bridge_enabled = False

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
