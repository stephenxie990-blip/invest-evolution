"""Shared runtime status helpers for commander runtime flows."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.commander_support.observability import read_event_rows, summarize_event_rows
from app.commander_support.workflow import jsonable


def normalize_status_detail(detail: str) -> str:
    detail_mode = str(detail or "fast").strip().lower()
    return detail_mode if detail_mode in {"fast", "slow"} else "fast"


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


def collect_training_lab_status(
    *,
    lab_counts: dict[str, Any],
    include_recent: bool,
    list_training_plans: Any,
    list_training_runs: Any,
    list_training_evaluations: Any,
) -> dict[str, Any]:
    latest_plans: list[dict[str, Any]] = []
    latest_runs: list[dict[str, Any]] = []
    latest_evaluations: list[dict[str, Any]] = []
    if include_recent:
        latest_plans = list(list_training_plans(limit=3).get("items", []))
        latest_runs = list(list_training_runs(limit=3).get("items", []))
        latest_evaluations = list(list_training_evaluations(limit=3).get("items", []))
    return build_training_lab_status(
        lab_counts=lab_counts,
        latest_plans=latest_plans,
        latest_runs=latest_runs,
        latest_evaluations=latest_evaluations,
        include_recent=include_recent,
    )


def build_events_tail_payload(runtime_events_path: Path, *, limit: int) -> dict[str, Any]:
    rows = read_event_rows(runtime_events_path, limit=limit)
    return {"count": len(rows), "items": rows}


def build_events_summary_payload(
    runtime_events_path: Path,
    *,
    limit: int,
    ok_status: str,
) -> dict[str, Any]:
    rows = read_event_rows(runtime_events_path, limit=limit)
    return {"status": ok_status, "summary": summarize_event_rows(rows), "items": rows}


def build_training_lab_summary_payload(
    *,
    lab_counts: dict[str, Any],
    list_training_plans: Any,
    list_training_runs: Any,
    list_training_evaluations: Any,
    limit: int,
    ok_status: str,
) -> dict[str, Any]:
    return {
        "status": ok_status,
        **dict(lab_counts or {}),
        "latest_plans": list_training_plans(limit=limit).get("items", []),
        "latest_runs": list_training_runs(limit=limit).get("items", []),
        "latest_evaluations": list_training_evaluations(limit=limit).get("items", []),
    }


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


def build_commander_status_payload(
    runtime: Any,
    *,
    detail_mode: str,
    event_rows: list[dict[str, Any]] | None = None,
    include_recent_training_lab: bool = True,
) -> dict[str, Any]:
    runtime_state, current_task, last_task = runtime._snapshot_runtime_fields()
    strategy_items = [gene.to_dict() for gene in runtime.strategy_registry.genes]
    training_lab_status = collect_training_lab_status(
        lab_counts=runtime._lab_counts(),
        include_recent=include_recent_training_lab,
        list_training_plans=runtime.list_training_plans,
        list_training_runs=runtime.list_training_runs,
        list_training_evaluations=runtime.list_training_evaluations,
    )
    return build_runtime_status_payload(
        detail_mode=detail_mode,
        instance_id=runtime.instance_id,
        workspace=str(runtime.cfg.workspace),
        strategy_dir=str(runtime.cfg.strategy_dir),
        model=runtime.cfg.model,
        autopilot_enabled=runtime.cfg.autopilot_enabled,
        heartbeat_enabled=runtime.cfg.heartbeat_enabled,
        training_interval_sec=runtime.cfg.training_interval_sec,
        heartbeat_interval_sec=runtime.cfg.heartbeat_interval_sec,
        runtime_state=runtime_state,
        started=runtime._started,
        current_task=current_task,
        last_task=last_task,
        runtime_lock_file=runtime.cfg.runtime_lock_file,
        runtime_lock_active=runtime.cfg.runtime_lock_file.exists(),
        training_lock_file=runtime.cfg.training_lock_file,
        training_lock_active=runtime.cfg.training_lock_file.exists(),
        brain_tool_count=len(runtime.brain.tools),
        brain_session_count=runtime.brain.session_count,
        cron_status=runtime.cron.status(),
        body_snapshot=runtime.body.snapshot(),
        memory_stats=runtime.memory.stats(),
        bridge_status=runtime.bridge.status(),
        plugin_tool_names=runtime._plugin_tool_names,
        strategies=strategy_items,
        enabled_strategy_count=len(runtime.strategy_registry.list_genes(only_enabled=True)),
        config_payload=runtime.config_service.get_masked_payload(),
        data_status=runtime._collect_data_status(detail_mode),
        event_rows=list(event_rows or []),
        training_lab_status=training_lab_status,
    )


def build_commander_status_snapshot(
    runtime: Any,
    *,
    detail: str,
    event_limit: int = 20,
    include_recent_training_lab: bool = True,
) -> dict[str, Any]:
    detail_mode = normalize_status_detail(detail)
    event_rows = read_event_rows(runtime.cfg.runtime_events_path, limit=event_limit)
    payload = build_commander_status_payload(
        runtime,
        detail_mode=detail_mode,
        event_rows=event_rows,
        include_recent_training_lab=include_recent_training_lab,
    )
    return {
        "detail_mode": detail_mode,
        "event_rows": event_rows,
        "payload": payload,
    }


def build_persisted_status_payload(runtime: Any) -> dict[str, Any]:
    return build_commander_status_payload(
        runtime,
        detail_mode=normalize_status_detail("fast"),
        event_rows=[],
        include_recent_training_lab=False,
    )
