from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.runtime_artifact_reader import safe_read_json, safe_read_jsonl, safe_read_text


def append_event_row(path: Path, event: str, payload: dict[str, Any], *, source: str = "runtime") -> dict[str, Any]:
    row = {
        "id": f"evt-{int(datetime.now().timestamp() * 1000)}",
        "ts": datetime.now().isoformat(),
        "event": str(event),
        "source": str(source),
        "payload": payload or {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def read_event_rows(path: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows[-max(1, int(limit)):]


def summarize_event_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    latest = rows[-1] if rows else None
    for row in rows:
        key = str(row.get("event") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return {
        "count": len(rows),
        "counts": counts,
        "latest": latest,
        "window_start": rows[0].get("ts") if rows else "",
        "window_end": rows[-1].get("ts") if rows else "",
    }


def memory_brief_row(row: dict[str, Any]) -> dict[str, Any]:
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
        if code:
            codes.append(code)
    return codes


def _diff_params(current: Any, previous: Any) -> dict[str, Any]:
    current_dict = dict(current or {}) if isinstance(current, dict) else {}
    previous_dict = dict(previous or {}) if isinstance(previous, dict) else {}
    all_keys = sorted(set(current_dict) | set(previous_dict))
    changed = []
    for key in all_keys:
        if current_dict.get(key) != previous_dict.get(key):
            changed.append({"key": key, "current": current_dict.get(key), "previous": previous_dict.get(key)})
    return {"changed_count": len(changed), "items": changed}


def _build_strategy_compare(runtime: Any, row: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    if runtime is None:
        return {"has_previous": False}
    rows = runtime.memory.search(query="", limit=200)
    current_id = str(row.get("id") or "")
    previous_row = None
    for candidate in reversed(rows):
        if str(candidate.get("id") or "") == current_id:
            continue
        meta = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
        if meta.get("training_run"):
            previous_row = candidate
            break
    if previous_row is None:
        return {"has_previous": False}
    current_result = dict((list(metadata.get("results") or []) or [{}])[-1] or {})
    previous_meta = previous_row.get("metadata") if isinstance(previous_row.get("metadata"), dict) else {}
    previous_result = dict((list(previous_meta.get("results") or []) or [{}])[-1] or {})
    current_selected = _normalize_stock_codes(current_result.get("selected_stocks"))
    previous_selected = _normalize_stock_codes(previous_result.get("selected_stocks"))
    current_return = _as_float(current_result.get("return_pct"))
    previous_return = _as_float(previous_result.get("return_pct"))
    current_selected_count = int(current_result.get("selected_count") or len(current_selected))
    previous_selected_count = int(previous_result.get("selected_count") or len(previous_selected))
    current_trade_count = int(current_result.get("trade_count") or 0)
    previous_trade_count = int(previous_result.get("trade_count") or 0)
    current_opt_count = int(current_result.get("optimization_event_count") or len(current_result.get("optimization_events") or []))
    previous_opt_count = int(previous_result.get("optimization_event_count") or len(previous_result.get("optimization_events") or []))
    return {
        "has_previous": True,
        "previous_record": memory_brief_row(previous_row),
        "current_cycle_id": current_result.get("cycle_id"),
        "previous_cycle_id": previous_result.get("cycle_id"),
        "metrics": {
            "return_pct": {"current": current_return, "previous": previous_return, "delta": (current_return - previous_return) if current_return is not None and previous_return is not None else None},
            "selected_count": {"current": current_selected_count, "previous": previous_selected_count, "delta": current_selected_count - previous_selected_count},
            "trade_count": {"current": current_trade_count, "previous": previous_trade_count, "delta": current_trade_count - previous_trade_count},
            "optimization_event_count": {"current": current_opt_count, "previous": previous_opt_count, "delta": current_opt_count - previous_opt_count},
        },
        "flags": {
            "selection_mode": {"current": current_result.get("selection_mode"), "previous": previous_result.get("selection_mode"), "changed": current_result.get("selection_mode") != previous_result.get("selection_mode")},
            "review_applied": {"current": bool(current_result.get("review_applied", False)), "previous": bool(previous_result.get("review_applied", False)), "changed": bool(current_result.get("review_applied", False)) != bool(previous_result.get("review_applied", False))},
            "benchmark_passed": {"current": bool(current_result.get("benchmark_passed", False)), "previous": bool(previous_result.get("benchmark_passed", False)), "changed": bool(current_result.get("benchmark_passed", False)) != bool(previous_result.get("benchmark_passed", False))},
        },
        "selected_stocks": {"current": current_selected, "previous": previous_selected, "added": [code for code in current_selected if code not in previous_selected], "removed": [code for code in previous_selected if code not in current_selected], "kept": [code for code in current_selected if code in previous_selected]},
        "params": _diff_params(current_result.get("params"), previous_result.get("params")),
    }


def build_memory_detail(runtime: Any, row: dict[str, Any]) -> dict[str, Any]:
    item = memory_brief_row(row)
    metadata: dict[str, Any] = (
        dict(item.get("metadata") or {})
        if isinstance(item.get("metadata"), dict)
        else {}
    )
    results = list(metadata.get("results") or [])
    detailed_results = []
    optimization_cache: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        cycle = dict(result or {})
        artifacts = cycle.get("artifacts") if isinstance(cycle.get("artifacts"), dict) else {}
        cycle_id = cycle.get("cycle_id")
        cycle_result = safe_read_json(runtime, artifacts.get("cycle_result_path", "")) if artifacts else None
        selection_meeting = safe_read_json(runtime, artifacts.get("selection_meeting_json_path", "")) if artifacts else None
        review_meeting = safe_read_json(runtime, artifacts.get("review_meeting_json_path", "")) if artifacts else None
        config_snapshot = safe_read_json(runtime, cycle.get("config_snapshot_path", "")) if cycle.get("config_snapshot_path") else None
        optimization_path = artifacts.get("optimization_events_path", "") if artifacts else ""
        if optimization_path:
            optimization_cache.setdefault(optimization_path, safe_read_jsonl(runtime, optimization_path))
        optimization_events = optimization_cache.get(optimization_path, [])
        detailed_results.append({
            **cycle,
            "cycle_result": cycle_result,
            "selection_meeting": selection_meeting,
            "selection_meeting_markdown": safe_read_text(runtime, artifacts.get("selection_meeting_markdown_path", "")) if artifacts else "",
            "review_meeting": review_meeting,
            "review_meeting_markdown": safe_read_text(runtime, artifacts.get("review_meeting_markdown_path", "")) if artifacts else "",
            "config_snapshot": config_snapshot,
            "optimization_events": [evt for evt in optimization_events if cycle_id is None or evt.get("cycle_id") in (None, cycle_id)],
        })
    return {
        "item": item,
        "details": {
            "summary": metadata.get("summary") or {},
            "runtime_summary": metadata.get("runtime_summary") or {},
            "results": detailed_results,
            "compare": _build_strategy_compare(runtime, row, metadata),
        },
    }


def build_runtime_diagnostics(runtime: Any, *, event_limit: int = 50, memory_limit: int = 20) -> dict[str, Any]:
    status = runtime.status(detail="fast")
    rows = read_event_rows(runtime.cfg.runtime_events_path, limit=event_limit)
    event_summary = summarize_event_rows(rows)
    memory_rows = runtime.memory.search(query="", limit=memory_limit)
    recent_training = [memory_brief_row(row) for row in memory_rows if (row.get("metadata") or {}).get("training_run")][-5:]
    diagnostics: list[str] = []
    runtime_state = status.get("runtime", {}).get("state")
    if runtime_state == "error":
        diagnostics.append("runtime_state=error")
    data = status.get("data") or {}
    quality = data.get("quality") if isinstance(data, dict) else None
    if isinstance(quality, dict) and not bool(quality.get("healthy", True)):
        diagnostics.append("data_quality_unhealthy")
    body = status.get("body") or {}
    last_result = body.get("last_result") if isinstance(body, dict) else None
    if isinstance(last_result, dict) and bool(last_result.get("degraded", False)):
        diagnostics.append("last_run_degraded")
    last_error = body.get("last_error") if isinstance(body, dict) else ""
    return {
        "status": "ok",
        "runtime": {"state": runtime_state, "current_task": status.get("runtime", {}).get("current_task"), "last_task": status.get("runtime", {}).get("last_task")},
        "event_summary": event_summary,
        "recent_events": rows,
        "recent_training_memory": recent_training,
        "last_error": last_error,
        "diagnostics": diagnostics,
        "restart_required": False,
    }
