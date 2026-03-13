"""Lifecycle support helpers for commander runtime orchestration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable


def ensure_runtime_storage(
    *,
    directories: Iterable[Path],
    training_lab: Any,
    memory: Any,
) -> None:
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
    training_lab.ensure_storage()
    memory.ensure_storage()


def setup_cron_callback(
    *,
    cron: Any,
    ask: Callable[..., Awaitable[str]],
    notifications: asyncio.Queue[str],
) -> None:
    async def on_cron_job(job: Any) -> str | None:
        response = await ask(job.message, session_key=f"cron:{job.id}")
        if job.deliver:
            notify = f"[cron][{job.channel}:{job.to}] {response or ''}"
            await notifications.put(notify)
        return response

    cron.on_job = on_cron_job


async def drain_runtime_notifications(
    notifications: asyncio.Queue[str],
    *,
    logger: Any,
) -> None:
    while True:
        try:
            msg = await asyncio.wait_for(notifications.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            return

        preview = msg.strip() if isinstance(msg, str) else str(msg)
        if preview:
            logger.info("%s", preview[:300])


def persist_runtime_state(state_file: Path, *, payload: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_persisted_runtime_state(state_file: Path, *, logger: Any) -> dict[str, Any] | None:
    if not state_file.exists():
        return None
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to restore persisted commander state from %s", state_file, exc_info=True)
        return None
    if not isinstance(payload, dict):
        logger.warning("Persisted commander state must be a JSON object: %s", state_file)
        return None
    return payload


def write_commander_identity_artifacts(
    workspace: Path,
    *,
    strategy_dir: str,
    quick_status_tool_name: str,
    strategy_summary: str,
    build_soul: Callable[..., str],
    build_heartbeat: Callable[[], str],
) -> None:
    soul_file = workspace / "SOUL.md"
    heartbeat_file = workspace / "HEARTBEAT.md"

    soul = build_soul(
        strategy_dir=strategy_dir,
        quick_status_tool_name=quick_status_tool_name,
        strategy_summary=strategy_summary,
    )
    soul_file.write_text(soul, encoding="utf-8")

    if not heartbeat_file.exists():
        heartbeat_file.write_text(build_heartbeat(), encoding="utf-8")


async def start_runtime_background_services(
    *,
    cron: Any,
    heartbeat: Any,
    bridge: Any,
    heartbeat_enabled: bool,
    bridge_enabled: bool,
    drain_notifications: Callable[[], Awaitable[None]],
    autopilot_enabled: bool,
    autopilot_loop: Callable[[int], Awaitable[None]],
    training_interval_sec: int,
) -> tuple[asyncio.Task[None], asyncio.Task[None] | None]:
    await cron.start()
    if heartbeat_enabled:
        await heartbeat.start()
    if bridge_enabled:
        await bridge.start()

    notify_task = asyncio.create_task(drain_notifications())
    autopilot_task: asyncio.Task[None] | None = None
    if autopilot_enabled:
        autopilot_task = asyncio.create_task(autopilot_loop(training_interval_sec))
    return notify_task, autopilot_task


async def stop_runtime_background_services(
    *,
    body: Any,
    autopilot_task: asyncio.Task[None] | None,
    notify_task: asyncio.Task[None] | None,
    bridge: Any,
    heartbeat: Any,
    cron: Any,
    brain: Any,
) -> tuple[None, None]:
    body.stop()
    if autopilot_task:
        autopilot_task.cancel()
        await asyncio.gather(autopilot_task, return_exceptions=True)
    if notify_task:
        notify_task.cancel()
        await asyncio.gather(notify_task, return_exceptions=True)

    bridge.stop()
    heartbeat.stop()
    cron.stop()
    await brain.close()
    return None, None
