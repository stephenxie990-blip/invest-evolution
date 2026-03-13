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
