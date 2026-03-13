"""Shared runtime status helpers for commander runtime flows."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.commander_observability import summarize_event_rows
from app.commander_workflow_support import jsonable


def collect_data_status(detail_mode: str) -> dict[str, Any]:
    try:
        from market_data.datasets import WebDatasetService

        return WebDatasetService().get_status_summary(refresh=(detail_mode == "slow"))
    except Exception as exc:
        return {"status": "error", "error": str(exc), "detail_mode": detail_mode}


def build_training_lab_status(
    *,
    lab_counts: dict[str, Any],
    latest_plans: list[dict[str, Any]] | None,
    latest_runs: list[dict[str, Any]] | None,
    latest_evaluations: list[dict[str, Any]] | None,
    include_recent: bool,
) -> dict[str, Any]:
    payload = {**dict(lab_counts or {})}
    if include_recent:
        payload.update(
            {
                "latest_plans": list(latest_plans or []),
                "latest_runs": list(latest_runs or []),
                "latest_evaluations": list(latest_evaluations or []),
            }
        )
    else:
        payload.update({"latest_plans": [], "latest_runs": [], "latest_evaluations": []})
    return payload


def build_runtime_status_payload(
    *,
    detail_mode: str,
    instance_id: str,
    workspace: str,
    strategy_dir: str,
    model: str,
    autopilot_enabled: bool,
    heartbeat_enabled: bool,
    training_interval_sec: int,
    heartbeat_interval_sec: int,
    runtime_state: str,
    started: str,
    current_task: dict[str, Any] | None,
    last_task: dict[str, Any] | None,
    runtime_lock_file: Path,
    runtime_lock_active: bool,
    training_lock_file: Path,
    training_lock_active: bool,
    brain_tool_count: int,
    brain_session_count: int,
    cron_status: dict[str, Any],
    body_snapshot: dict[str, Any],
    memory_stats: dict[str, Any],
    bridge_status: dict[str, Any],
    plugin_tool_names: set[str] | list[str],
    strategies: list[dict[str, Any]],
    enabled_strategy_count: int,
    config_payload: dict[str, Any],
    data_status: dict[str, Any],
    event_rows: list[dict[str, Any]],
    training_lab_status: dict[str, Any],
) -> dict[str, Any]:
    rows = list(event_rows or [])
    return jsonable(
        {
            "ts": datetime.now().isoformat(),
            "detail_mode": detail_mode,
            "instance_id": instance_id,
            "workspace": workspace,
            "strategy_dir": strategy_dir,
            "model": model,
            "autopilot_enabled": autopilot_enabled,
            "heartbeat_enabled": heartbeat_enabled,
            "training_interval_sec": training_interval_sec,
            "heartbeat_interval_sec": heartbeat_interval_sec,
            "runtime": {
                "state": runtime_state,
                "started": started,
                "current_task": current_task,
                "last_task": last_task,
                "runtime_lock_file": str(runtime_lock_file),
                "runtime_lock_active": runtime_lock_active,
                "training_lock_file": str(training_lock_file),
                "training_lock_active": training_lock_active,
            },
            "brain": {
                "tool_count": brain_tool_count,
                "session_count": brain_session_count,
                "cron": cron_status,
            },
            "body": body_snapshot,
            "memory": memory_stats,
            "bridge": bridge_status,
            "plugins": {
                "count": len(list(plugin_tool_names or [])),
                "items": sorted(str(item) for item in list(plugin_tool_names or [])),
            },
            "strategies": {
                "total": len(list(strategies or [])),
                "enabled": enabled_strategy_count,
                "items": list(strategies or []),
            },
            "config": config_payload,
            "data": data_status,
            "events": summarize_event_rows(rows),
            "training_lab": training_lab_status,
        }
    )
