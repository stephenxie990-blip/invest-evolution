"""Route registration for data query and download endpoints."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from flask import Flask, jsonify, request

from app.commander_support.services import (
    get_capital_flow_payload,
    get_dragon_tiger_payload,
    get_intraday_60m_payload,
)
from market_data import DataSourceUnavailableError
from market_data.gateway import MarketDataGateway

ResponseValue = Any
RuntimeGetter = Callable[[], Any]


def _runtime_or_not_ready(
    *,
    get_runtime: RuntimeGetter,
    runtime_not_ready_response: Callable[[], ResponseValue],
) -> Any:
    runtime = get_runtime()
    if runtime is None:
        return runtime_not_ready_response()
    return runtime


def _parse_codes_arg() -> list[str] | None:
    codes_param = str(request.args.get("codes", "") or "").strip()
    return [item.strip() for item in codes_param.split(",") if item.strip()] or None


def register_runtime_data_routes(
    app: Flask,
    *,
    get_runtime: RuntimeGetter,
    runtime_not_ready_response: Callable[[], ResponseValue],
    parse_limit_arg: Callable[..., int],
    parse_bool: Callable[[Any, str], bool],
    jsonify_contract_payload: Callable[..., ResponseValue],
    data_source_unavailable_response: Callable[[DataSourceUnavailableError], ResponseValue],
    logger: Any,
    data_download_lock_file_getter: Callable[[], Path],
    data_download_lock: Any,
    get_data_download_running: Callable[[], bool],
    set_data_download_running: Callable[[bool], None],
    thread_factory: Callable[[Callable[[], None]], Any],
) -> None:
    @app.route("/api/model-routing/preview")
    def api_model_routing_preview():
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
        )
        if isinstance(runtime, tuple):
            return runtime
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
            return jsonify_contract_payload(payload)
        except DataSourceUnavailableError as exc:
            logger.warning("Model routing preview data source unavailable: %s", exc)
            return data_source_unavailable_response(exc)
        except Exception as exc:
            logger.exception("Model routing preview error")
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/data/capital_flow", methods=["GET"])
    def api_data_capital_flow():
        codes = _parse_codes_arg()
        start_date = request.args.get("start")
        end_date = request.args.get("end")
        limit = parse_limit_arg(default=200, maximum=5000)
        runtime = get_runtime()
        if runtime is not None:
            return jsonify_contract_payload(
                runtime.get_capital_flow(
                    codes=codes,
                    start_date=start_date,
                    end_date=end_date,
                    limit=limit,
                )
            )
        return jsonify(
            get_capital_flow_payload(
                codes=codes,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )
        )

    @app.route("/api/data/dragon_tiger", methods=["GET"])
    def api_data_dragon_tiger():
        codes = _parse_codes_arg()
        start_date = request.args.get("start")
        end_date = request.args.get("end")
        limit = parse_limit_arg(default=200, maximum=5000)
        runtime = get_runtime()
        if runtime is not None:
            return jsonify_contract_payload(
                runtime.get_dragon_tiger(
                    codes=codes,
                    start_date=start_date,
                    end_date=end_date,
                    limit=limit,
                )
            )
        return jsonify(
            get_dragon_tiger_payload(
                codes=codes,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )
        )

    @app.route("/api/data/intraday_60m", methods=["GET"])
    def api_data_intraday_60m():
        codes = _parse_codes_arg()
        start_date = request.args.get("start")
        end_date = request.args.get("end")
        limit = parse_limit_arg(default=500, maximum=10000)
        runtime = get_runtime()
        if runtime is not None:
            return jsonify_contract_payload(
                runtime.get_intraday_60m(
                    codes=codes,
                    start_date=start_date,
                    end_date=end_date,
                    limit=limit,
                )
            )
        return jsonify(
            get_intraday_60m_payload(
                codes=codes,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )
        )

    @app.route("/api/data/download", methods=["POST"])
    def api_data_download():
        runtime = get_runtime()
        data = request.get_json(silent=True) or {}
        try:
            confirm = parse_bool(data.get("confirm", False), "confirm")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        if runtime is not None:
            return jsonify_contract_payload(runtime.trigger_data_download(confirm=confirm))

        if not confirm:
            return jsonify({"error": "confirm=true is required when live runtime is unavailable"}), 400

        lock_file_path = data_download_lock_file_getter()

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

        def _do_download():
            try:
                MarketDataGateway().sync_background_full_refresh()
            except Exception as exc:
                logger.exception("后台数据同步失败: %s", exc)
            finally:
                with data_download_lock:
                    set_data_download_running(False)
                _release_lock_file()

        with data_download_lock:
            if get_data_download_running() or _lock_file_is_active():
                return jsonify({"status": "running", "message": "后台同步已在运行"})
            if not _acquire_lock_file():
                return jsonify({"status": "running", "message": "后台同步已在运行"})
            set_data_download_running(True)

        thread = thread_factory(_do_download)
        try:
            thread.start()
        except Exception:
            with data_download_lock:
                set_data_download_running(False)
            _release_lock_file()
            raise
        return jsonify({"status": "started", "message": "后台同步已启动"})
