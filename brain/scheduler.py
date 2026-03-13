"""
Local scheduler primitives (cron + heartbeat) for fused commander runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)
MILLISECONDS_PER_SECOND = 1000


def _now_ms() -> int:
    return int(time.time() * MILLISECONDS_PER_SECOND)


@dataclass
class CronJob:
    id: str
    name: str
    message: str
    every_sec: int
    enabled: bool = True
    deliver: bool = False
    channel: str = "cli"
    to: str = "commander"
    next_run_at_ms: int = 0
    last_run_at_ms: int = 0
    last_status: str = ""
    last_error: str = ""
    created_at_ms: int = field(default_factory=_now_ms)
    updated_at_ms: int = field(default_factory=_now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CronService:
    """Simple interval cron service for local runtime."""

    def __init__(self, store_path: Path):
        self.store_path = Path(store_path)
        self.jobs: list[CronJob] = []
        self.on_job: Optional[Callable[[CronJob], Awaitable[Optional[str]]]] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._load()
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Cron service started with %s jobs", len(self.jobs))

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self._save()

    def status(self) -> dict[str, Any]:
        next_wake = None
        enabled_jobs = [job for job in self.jobs if job.enabled]
        if enabled_jobs:
            next_wake = min(job.next_run_at_ms for job in enabled_jobs)
        return {
            "enabled": self._running,
            "jobs": len(enabled_jobs),
            "next_wake_at_ms": next_wake,
        }

    def list_jobs(self) -> list[CronJob]:
        return list(self.jobs)

    def add_job(
        self,
        name: str,
        message: str,
        every_sec: int,
        deliver: bool = False,
        channel: str = "cli",
        to: str = "commander",
    ) -> CronJob:
        now = _now_ms()
        job = CronJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            message=message,
            every_sec=max(1, int(every_sec)),
            deliver=deliver,
            channel=channel,
            to=to,
            next_run_at_ms=now + max(1, int(every_sec)) * MILLISECONDS_PER_SECOND,
        )
        self.jobs.append(job)
        self._save()
        return job

    def remove_job(self, job_id: str) -> bool:
        before = len(self.jobs)
        self.jobs = [job for job in self.jobs if job.id != job_id]
        changed = len(self.jobs) < before
        if changed:
            self._save()
        return changed

    async def _run_loop(self) -> None:
        try:
            while self._running:
                now = _now_ms()
                for job in self.jobs:
                    if not job.enabled:
                        continue
                    if job.next_run_at_ms and now >= job.next_run_at_ms:
                        await self._execute(job)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            return

    async def _execute(self, job: CronJob) -> None:
        now = _now_ms()
        job.last_run_at_ms = now
        job.updated_at_ms = now

        try:
            if self.on_job:
                await self.on_job(job)
            job.last_status = "ok"
            job.last_error = ""
        except Exception as exc:
            job.last_status = "error"
            job.last_error = str(exc)
            logger.exception("Cron job failed: %s", job.id)

        job.next_run_at_ms = _now_ms() + max(1, int(job.every_sec)) * MILLISECONDS_PER_SECOND
        self._save()

    def _load(self) -> None:
        if not self.store_path.exists():
            self.jobs = []
            return

        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._quarantine_corrupt_store(exc)
            self.jobs = []
            return

        raw_jobs = data.get("jobs", []) if isinstance(data, dict) else []
        jobs: list[CronJob] = []
        invalid_jobs = 0
        for item in raw_jobs:
            try:
                job = CronJob(
                    id=item["id"],
                    name=item["name"],
                    message=item.get("message", ""),
                    every_sec=max(1, int(item.get("every_sec", 3600))),
                    enabled=bool(item.get("enabled", True)),
                    deliver=bool(item.get("deliver", False)),
                    channel=item.get("channel", "cli"),
                    to=item.get("to", "commander"),
                    next_run_at_ms=int(item.get("next_run_at_ms") or (_now_ms() + 3600 * MILLISECONDS_PER_SECOND)),
                    last_run_at_ms=int(item.get("last_run_at_ms", 0)),
                    last_status=item.get("last_status", ""),
                    last_error=item.get("last_error", ""),
                    created_at_ms=int(item.get("created_at_ms", _now_ms())),
                    updated_at_ms=int(item.get("updated_at_ms", _now_ms())),
                )
                jobs.append(job)
            except (KeyError, TypeError, ValueError) as exc:
                invalid_jobs += 1
                logger.warning("Skipping invalid cron job payload from %s: %s", self.store_path, exc)
                continue
        if invalid_jobs:
            logger.warning("Skipped %s invalid cron jobs while loading %s", invalid_jobs, self.store_path)
        self.jobs = jobs

    def _quarantine_corrupt_store(self, exc: Exception) -> None:
        logger.error("Failed to load cron store %s: %s", self.store_path, exc)
        try:
            quarantine_path = self.store_path.with_name(
                f"{self.store_path.stem}.corrupt.{int(time.time())}{self.store_path.suffix}"
            )
            self.store_path.rename(quarantine_path)
            logger.warning("Moved corrupt cron store to %s", quarantine_path)
        except OSError as move_exc:
            logger.warning("Failed to quarantine corrupt cron store %s: %s", self.store_path, move_exc)

    def _save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "jobs": [job.to_dict() for job in self.jobs],
        }
        self.store_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class HeartbeatService:
    """Simple periodic heartbeat executor."""

    def __init__(
        self,
        workspace: Path,
        on_execute: Optional[Callable[[str], Awaitable[str]]] = None,
        on_notify: Optional[Callable[[str], Awaitable[None]]] = None,
        interval_s: int = 1800,
        enabled: bool = True,
    ):
        self.workspace = Path(workspace)
        self.on_execute = on_execute
        self.on_notify = on_notify
        self.interval_s = max(1, int(interval_s))
        self.enabled = enabled

        self._running = False
        self._task: Optional[asyncio.Task] = None

    @property
    def heartbeat_file(self) -> Path:
        return self.workspace / "HEARTBEAT.md"

    async def start(self) -> None:
        if not self.enabled:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Heartbeat started, interval=%ss", self.interval_s)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
        except asyncio.CancelledError:
            return

    async def _tick(self) -> None:
        if not self.heartbeat_file.exists():
            return

        try:
            content = self.heartbeat_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read heartbeat file %s: %s", self.heartbeat_file, exc)
            return

        tasks = self._extract_tasks(content)
        if not tasks:
            return

        if not self.on_execute:
            return

        try:
            result = await self.on_execute(tasks)
            if result and self.on_notify:
                await self.on_notify(result)
        except Exception:
            logger.exception("Heartbeat task execution failed")

    @staticmethod
    def _extract_tasks(content: str) -> str:
        lines = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            lines.append(stripped)
        return "\n".join(lines).strip()
