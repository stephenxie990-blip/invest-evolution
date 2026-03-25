"""Route registration for runtime ops, config, and memory endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import config as config_module
from flask import Flask, jsonify, request

from app.commander_support.services import (
    ConfigSurfaceValidationError,
    get_control_plane_payload,
    get_data_status_payload,
    get_evolution_config_payload,
    get_runtime_paths_payload,
    list_agent_prompts_payload,
    update_agent_prompt_payload,
    update_control_plane_payload,
    update_evolution_config_payload,
    update_runtime_paths_payload,
)
from app.runtime_artifact_reader import safe_read_json, safe_read_jsonl, safe_read_text


ResponseValue = Any
RuntimeGetter = Callable[[], Any]


def _as_object_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _as_object_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return list(value)


def _is_error_response(value: Any) -> bool:
    return isinstance(value, tuple)


def _runtime_or_not_ready(
    *,
    get_runtime: RuntimeGetter,
    runtime_not_ready_response: Callable[[], ResponseValue],
) -> Any:
    runtime = get_runtime()
    if runtime is None:
        return runtime_not_ready_response()
    return runtime


def _parse_view_or_400(request_view_arg: Callable[[], str]) -> str | ResponseValue:
    try:
        return request_view_arg()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


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
            changed.append(
                {
                    "key": key,
                    "current": current_map.get(key),
                    "previous": previous_map.get(key),
                }
            )
    return {
        "changed": changed,
        "added": added,
        "removed": removed,
        "changed_count": len(changed) + len(added) + len(removed),
    }


def _build_strategy_compare(runtime: Any, row: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
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
    current_opt_count = int(
        current_result.get("optimization_event_count") or len(current_result.get("optimization_events") or [])
    )
    previous_opt_count = int(
        previous_result.get("optimization_event_count") or len(previous_result.get("optimization_events") or [])
    )

    return {
        "has_previous": True,
        "previous_record": _memory_brief_row(previous_row),
        "current_cycle_id": current_result.get("cycle_id"),
        "previous_cycle_id": previous_result.get("cycle_id"),
        "metrics": {
            "return_pct": {
                "current": current_return,
                "previous": previous_return,
                "delta": (
                    current_return - previous_return
                    if current_return is not None and previous_return is not None
                    else None
                ),
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
                "changed": bool(current_result.get("review_applied", False))
                != bool(previous_result.get("review_applied", False)),
            },
            "benchmark_passed": {
                "current": bool(current_result.get("benchmark_passed", False)),
                "previous": bool(previous_result.get("benchmark_passed", False)),
                "changed": bool(current_result.get("benchmark_passed", False))
                != bool(previous_result.get("benchmark_passed", False)),
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


def _build_memory_detail(runtime: Any, row: dict[str, Any]) -> dict[str, Any]:
    item = _memory_brief_row(row)
    metadata = _as_object_dict(item.get("metadata"))
    results = _as_object_list(metadata.get("results"))
    detailed_results = []
    optimization_cache: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        cycle = dict(result or {})
        artifacts = cycle.get("artifacts") if isinstance(cycle.get("artifacts"), dict) else {}
        cycle_id = cycle.get("cycle_id")
        cycle_result = safe_read_json(runtime, artifacts.get("cycle_result_path", "")) if artifacts else None
        selection_meeting = safe_read_json(runtime, artifacts.get("selection_meeting_json_path", "")) if artifacts else None
        review_meeting = safe_read_json(runtime, artifacts.get("review_meeting_json_path", "")) if artifacts else None
        config_snapshot = (
            safe_read_json(runtime, cycle.get("config_snapshot_path", ""))
            if cycle.get("config_snapshot_path")
            else None
        )
        optimization_path = artifacts.get("optimization_events_path", "") if artifacts else ""
        if optimization_path:
            optimization_cache.setdefault(optimization_path, safe_read_jsonl(runtime, optimization_path))
        optimization_events = optimization_cache.get(optimization_path, [])
        detailed_results.append(
            {
                **cycle,
                "cycle_result": cycle_result,
                "selection_meeting": selection_meeting,
                "selection_meeting_markdown": (
                    safe_read_text(runtime, artifacts.get("selection_meeting_markdown_path", ""))
                    if artifacts
                    else ""
                ),
                "review_meeting": review_meeting,
                "review_meeting_markdown": (
                    safe_read_text(runtime, artifacts.get("review_meeting_markdown_path", "")) if artifacts else ""
                ),
                "config_snapshot": config_snapshot,
                "optimization_events": [
                    evt for evt in optimization_events if cycle_id is None or evt.get("cycle_id") in (None, cycle_id)
                ],
            }
        )
    return {
        "item": item,
        "details": {
            "summary": _as_object_dict(metadata.get("summary")),
            "runtime_summary": _as_object_dict(metadata.get("runtime_summary")),
            "results": detailed_results,
            "compare": _build_strategy_compare(runtime, row, metadata),
        },
    }


def register_runtime_ops_routes(
    app: Flask,
    *,
    get_runtime: RuntimeGetter,
    runtime_not_ready_response: Callable[[], ResponseValue],
    request_view_arg: Callable[[], str],
    parse_view_arg: Callable[..., str],
    parse_bool: Callable[[Any, str], bool],
    parse_int: Callable[..., int],
    respond_with_display: Callable[..., ResponseValue],
    jsonify_contract_payload: Callable[..., ResponseValue],
) -> None:
    @app.route("/api/allocator")
    def api_allocator():
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
        )
        if _is_error_response(runtime):
            return runtime
        view = _parse_view_or_400(request_view_arg)
        if _is_error_response(view):
            return view
        regime = str(request.args.get("regime", "oscillation") or "oscillation").strip().lower()
        try:
            top_n = parse_int(request.args.get("top_n", 3), "top_n", minimum=1, maximum=4)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return respond_with_display(
            runtime.get_allocator_preview(
                regime=regime,
                top_n=top_n,
                as_of_date=datetime.now().strftime("%Y%m%d"),
            ),
            view=str(view),
        )

    @app.route("/api/strategies")
    def api_strategies():
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
        )
        if _is_error_response(runtime):
            return runtime
        view = _parse_view_or_400(request_view_arg)
        if _is_error_response(view):
            return view
        genes = runtime.strategy_registry.list_genes()
        return respond_with_display(
            {"count": len(genes), "items": [gene.to_dict() for gene in genes]},
            view=str(view),
        )

    @app.route("/api/strategies/reload", methods=["POST"])
    def api_strategies_reload():
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
        )
        if _is_error_response(runtime):
            return runtime
        return jsonify_contract_payload(runtime.reload_strategies())

    @app.route("/api/cron")
    def api_cron_list():
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
        )
        if _is_error_response(runtime):
            return runtime
        view = _parse_view_or_400(request_view_arg)
        if _is_error_response(view):
            return view
        rows = [job.to_dict() for job in runtime.cron.list_jobs()]
        return respond_with_display({"count": len(rows), "items": rows}, view=str(view))

    @app.route("/api/cron", methods=["POST"])
    def api_cron_add():
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
        )
        if _is_error_response(runtime):
            return runtime
        data = request.get_json(force=True) or {}
        name = str(data.get("name", "")).strip()
        message = str(data.get("message", "")).strip()
        try:
            every_sec = max(1, int(data.get("every_sec", 3600)))
        except (TypeError, ValueError):
            return jsonify({"error": "every_sec must be an integer"}), 400
        try:
            deliver = parse_bool(data.get("deliver", False), "deliver")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not name or not message:
            return jsonify({"error": "name and message are required"}), 400
        job = runtime.cron.add_job(
            name=name,
            message=message,
            every_sec=every_sec,
            deliver=deliver,
            channel=str(data.get("channel", "web")),
            to=str(data.get("to", "commander")),
        )
        runtime._persist_state()
        return jsonify({"status": "ok", "job": job.to_dict()})

    @app.route("/api/cron/<job_id>", methods=["DELETE"])
    def api_cron_remove(job_id: str):
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
        )
        if _is_error_response(runtime):
            return runtime
        ok = runtime.cron.remove_job(job_id)
        runtime._persist_state()
        return jsonify({"status": "ok" if ok else "not_found", "job_id": job_id})

    @app.route("/api/memory")
    def api_memory():
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
        )
        if _is_error_response(runtime):
            return runtime
        view = _parse_view_or_400(request_view_arg)
        if _is_error_response(view):
            return view
        query = request.args.get("q", "")
        try:
            limit = min(200, max(1, int(request.args.get("limit", 20))))
        except (TypeError, ValueError):
            return jsonify({"error": "limit must be an integer"}), 400
        rows = runtime.memory.search(query=query, limit=limit)
        items = [_memory_brief_row(row) for row in rows]
        return respond_with_display({"count": len(items), "items": items}, view=str(view))

    @app.route("/api/memory/<record_id>")
    def api_memory_detail(record_id: str):
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
        )
        if _is_error_response(runtime):
            return runtime
        view = _parse_view_or_400(request_view_arg)
        if _is_error_response(view):
            return view
        row = runtime.memory.get(record_id)
        if row is None:
            return jsonify({"error": "memory record not found"}), 404
        return respond_with_display(_build_memory_detail(runtime, row), view=str(view))

    @app.route("/api/agent_prompts", methods=["GET"])
    def api_agent_prompts_list():
        view = _parse_view_or_400(request_view_arg)
        if _is_error_response(view):
            return view
        runtime = get_runtime()
        if runtime is not None:
            return respond_with_display(runtime.list_agent_prompts(), view=str(view))

        return respond_with_display(
            list_agent_prompts_payload(project_root=config_module.PROJECT_ROOT),
            view=str(view),
        )

    @app.route("/api/agent_prompts", methods=["POST"])
    def api_agent_prompts_update():
        data = _as_object_dict(request.get_json(force=True) or {})
        agent_name = str(data.get("name", "") or "").strip()
        if not agent_name:
            return jsonify({"error": "name is required"}), 400
        if "system_prompt" not in data:
            return jsonify({"error": "system_prompt is required"}), 400
        supported_fields = {"name", "system_prompt"}
        unsupported = sorted(str(key) for key in data.keys() if str(key) not in supported_fields)
        if unsupported:
            if unsupported == ["llm_model"]:
                return jsonify(
                    {
                        "error": "llm_model is not editable on /api/agent_prompts; use /api/control_plane for model binding"
                    }
                ), 400
            return jsonify(
                {
                    "error": f"unsupported fields for /api/agent_prompts: {', '.join(unsupported)}",
                    "invalid_keys": unsupported,
                }
            ), 400
        try:
            runtime = get_runtime()
            if runtime is not None:
                return jsonify_contract_payload(
                    runtime.update_agent_prompt(
                        agent_name=agent_name,
                        system_prompt=str(data.get("system_prompt", "") or ""),
                    )
                )

            return jsonify(
                update_agent_prompt_payload(
                    agent_name=agent_name,
                    system_prompt=str(data.get("system_prompt", "") or ""),
                    project_root=config_module.PROJECT_ROOT,
                )
            )
        except ConfigSurfaceValidationError as exc:
            payload: dict[str, Any] = {"status": "error", "error": str(exc)}
            if exc.invalid_keys:
                payload["invalid_keys"] = list(exc.invalid_keys)
            return jsonify(payload), 400
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.route("/api/runtime_paths", methods=["GET"])
    def api_runtime_paths_get():
        view = _parse_view_or_400(request_view_arg)
        if _is_error_response(view):
            return view
        runtime = get_runtime()
        if runtime is not None:
            return respond_with_display(runtime.get_runtime_paths(), view=str(view))

        return respond_with_display(
            get_runtime_paths_payload(None, project_root=config_module.PROJECT_ROOT),
            view=str(view),
        )

    @app.route("/api/runtime_paths", methods=["POST"])
    def api_runtime_paths_update():
        data = request.get_json(force=True) or {}
        try:
            runtime = get_runtime()
            if runtime is not None:
                return jsonify_contract_payload(runtime.update_runtime_paths(data, confirm=True))

            return jsonify(
                update_runtime_paths_payload(
                    patch=data,
                    runtime=None,
                    project_root=config_module.PROJECT_ROOT,
                    sync_runtime=None,
                )
            )
        except ValueError as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.route("/api/evolution_config", methods=["GET"])
    def api_evolution_config_get():
        view = _parse_view_or_400(request_view_arg)
        if _is_error_response(view):
            return view
        runtime = get_runtime()
        if runtime is not None:
            return respond_with_display(runtime.get_evolution_config(), view=str(view))

        return respond_with_display(
            get_evolution_config_payload(
                project_root=config_module.PROJECT_ROOT,
                live_config=config_module.config,
            ),
            view=str(view),
        )

    @app.route("/api/evolution_config", methods=["POST"])
    def api_evolution_config_update():
        data = request.get_json(force=True) or {}
        forbidden_keys = {"llm_fast_model", "llm_deep_model", "llm_api_base", "llm_api_key"}
        invalid_keys = sorted(key for key in forbidden_keys if key in data)
        if invalid_keys:
            return (
                jsonify(
                    {
                        "status": "error",
                        "error": "LLM 配置已迁移到 /api/control_plane；/api/evolution_config 仅保留训练参数",
                        "migrate_to": "/api/control_plane",
                        "invalid_keys": invalid_keys,
                    }
                ),
                400,
            )
        try:
            runtime = get_runtime()
            if runtime is not None:
                return jsonify_contract_payload(runtime.update_evolution_config(data, confirm=True))

            return jsonify(
                update_evolution_config_payload(
                    patch=data,
                    project_root=config_module.PROJECT_ROOT,
                    live_config=config_module.config,
                )
            )
        except ValueError as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.route("/api/control_plane", methods=["GET"])
    def api_control_plane_get():
        view = _parse_view_or_400(request_view_arg)
        if _is_error_response(view):
            return view
        runtime = get_runtime()
        if runtime is not None:
            return respond_with_display(runtime.get_control_plane(), view=str(view))

        return respond_with_display(
            get_control_plane_payload(project_root=config_module.PROJECT_ROOT),
            view=str(view),
        )

    @app.route("/api/control_plane", methods=["POST"])
    def api_control_plane_update():
        data = request.get_json(force=True) or {}
        try:
            runtime = get_runtime()
            if runtime is not None:
                return jsonify_contract_payload(runtime.update_control_plane(data, confirm=True))

            return jsonify(
                update_control_plane_payload(
                    patch=data,
                    project_root=config_module.PROJECT_ROOT,
                )
            )
        except ValueError as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.route("/api/data/status", methods=["GET"])
    def api_data_status():
        view = _parse_view_or_400(request_view_arg)
        if _is_error_response(view):
            return view
        try:
            refresh = parse_bool(request.args.get("refresh", False), "refresh")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        detail_mode = "slow" if refresh else "fast"
        runtime = get_runtime()
        if runtime is not None:
            return respond_with_display(runtime.get_data_status(refresh=refresh), view=str(view))

        return respond_with_display(
            {
                **get_data_status_payload(refresh=refresh),
                "detail_mode": detail_mode,
            },
            view=str(view),
        )
