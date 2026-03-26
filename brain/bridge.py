"""Channel bridge hub for commander (file channel first)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Awaitable, Callable, Optional


logger = logging.getLogger(__name__)


@dataclass
class BridgeMessage:
    id: str
    channel: str
    chat_id: str
    session_key: str
    role: str
    content: str
    ts_ms: int
    metadata: dict


class FileBridgeChannel:
    """Simple inbox/outbox file channel.

    Protocol:
    - drop json file into inbox_dir/*.json with fields: channel/chat_id/session_key/content/metadata
    - commander writes response json into outbox_dir/*.json
    """

    def __init__(self, inbox_dir: Path, outbox_dir: Path, create_dirs: bool = True):
        self.inbox_dir = Path(inbox_dir)
        self.outbox_dir = Path(outbox_dir)
        self.invalid_dir = self.inbox_dir / "_invalid"
        if create_dirs:
            self.inbox_dir.mkdir(parents=True, exist_ok=True)
            self.outbox_dir.mkdir(parents=True, exist_ok=True)

    def poll_inbox(self) -> list[BridgeMessage]:
        msgs: list[BridgeMessage] = []
        for path in sorted(self.inbox_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("bridge message must be a JSON object")
                msg = BridgeMessage(
                    id=str(data.get("id") or uuid.uuid4().hex[:12]),
                    channel=str(data.get("channel") or "file"),
                    chat_id=str(data.get("chat_id") or "default"),
                    session_key=str(data.get("session_key") or f"file:{data.get('chat_id') or 'default'}"),
                    role="user",
                    content=str(data.get("content") or ""),
                    ts_ms=int(data.get("ts_ms") or time.time() * 1000),
                    metadata=data.get("metadata") or {},
                )
                msgs.append(msg)
                path.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Failed to decode bridge message %s: %s", path, exc)
                self._quarantine(path, reason=str(exc))
        return msgs

    def _quarantine(self, path: Path, reason: str) -> None:
        try:
            self.invalid_dir.mkdir(parents=True, exist_ok=True)
            target = self.invalid_dir / path.name
            if target.exists():
                target = self.invalid_dir / f"{int(time.time() * 1000)}_{path.name}"
            path.rename(target)
            sidecar = target.with_suffix(target.suffix + ".error.txt")
            sidecar.write_text(reason, encoding="utf-8")
        except Exception as exc:
            logger.exception("Failed to quarantine malformed bridge message %s: %s", path, exc)

    def emit(self, message: BridgeMessage) -> Path:
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        out = self.outbox_dir / f"{ts}_{message.id}.json"
        out.write_text(json.dumps(asdict(message), ensure_ascii=False, indent=2), encoding="utf-8")
        return out


class BridgeHub:
    def __init__(
        self,
        inbox_dir: Path,
        outbox_dir: Path,
        on_message: Optional[Callable[[BridgeMessage], Awaitable[str]]] = None,
        poll_interval_sec: float = 1.0,
        enabled: bool = True,
    ):
        self.file_channel = FileBridgeChannel(inbox_dir=inbox_dir, outbox_dir=outbox_dir, create_dirs=False)
        self.on_message = on_message
        self.poll_interval_sec = max(0.2, float(poll_interval_sec))
        self.enabled = enabled
        self._task: asyncio.Task | None = None
        self._running = False
        self.processed = 0
        self.failed = 0

    async def start(self) -> None:
        if not self.enabled or self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "running": self._running,
            "processed": self.processed,
            "failed": self.failed,
            "inbox_dir": str(self.file_channel.inbox_dir),
            "outbox_dir": str(self.file_channel.outbox_dir),
        }

    async def _loop(self) -> None:
        try:
            while self._running:
                batch = self.file_channel.poll_inbox()
                if not batch:
                    await asyncio.sleep(self.poll_interval_sec)
                    continue
                for msg in batch:
                    try:
                        await self._handle(msg)
                    except Exception:
                        self.failed += 1
                        logger.exception("Unhandled bridge message failure for %s", msg.id)
        except asyncio.CancelledError:
            return

    async def _handle(self, msg: BridgeMessage) -> None:
        if not self.on_message:
            self.failed += 1
            return
        try:
            response = await self.on_message(msg)
            out = BridgeMessage(
                id=msg.id,
                channel=msg.channel,
                chat_id=msg.chat_id,
                session_key=msg.session_key,
                role="assistant",
                content=response or "",
                ts_ms=int(time.time() * 1000),
                metadata={"reply_to": msg.id},
            )
            self.file_channel.emit(out)
            self.processed += 1
        except Exception as exc:
            self.failed += 1
            err = BridgeMessage(
                id=msg.id,
                channel=msg.channel,
                chat_id=msg.chat_id,
                session_key=msg.session_key,
                role="assistant",
                content=f"Error: {exc}",
                ts_ms=int(time.time() * 1000),
                metadata={"reply_to": msg.id, "error": True},
            )
            try:
                self.file_channel.emit(err)
            except Exception:
                logger.exception("Failed to emit bridge error reply for %s", msg.id)
