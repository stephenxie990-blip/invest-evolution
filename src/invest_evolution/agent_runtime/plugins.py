"""Agent runtime plugins, template rendering, and bridge transport."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from .file_io import atomic_write_json, atomic_write_text
from .message_envelope import normalize_inbound_envelope
from .tools import BrainTool


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
                normalized = normalize_inbound_envelope(data)
                msg = BridgeMessage(
                    id=normalized.id,
                    channel=normalized.channel,
                    chat_id=normalized.chat_id,
                    session_key=normalized.session_key,
                    role="user",
                    content=normalized.content,
                    ts_ms=normalized.ts_ms,
                    metadata=normalized.metadata,
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
            atomic_write_text(sidecar, reason, encoding="utf-8")
        except Exception as exc:
            logger.exception("Failed to quarantine malformed bridge message %s: %s", path, exc)

    def emit(self, message: BridgeMessage) -> Path:
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        out = self.outbox_dir / f"{ts}_{message.id}.json"
        atomic_write_json(out, asdict(message))
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

logger = logging.getLogger(__name__)
_PLACEHOLDER_RE = re.compile(r"{{\s*(\w+)\s*}}")
_ALLOWED_PLACEHOLDERS = {"input", "context"}


def _sanitize_value(value: Any) -> str:
    text = str(value or "")
    escaped = json.dumps(text, ensure_ascii=False)
    if len(escaped) >= 2 and escaped[0] == '"' and escaped[-1] == '"':
        inner = escaped[1:-1]
        return inner.replace("{{", "{ {").replace("}}", "} }")
    return escaped.replace("{{", "{ {").replace("}}", "} }")


class TemplateRenderer:
    def __init__(self, template: str) -> None:
        self._validate_template(template)
        self.template = template

    @staticmethod
    def _validate_template(template: str) -> None:
        for match in _PLACEHOLDER_RE.finditer(template):
            key = match.group(1)
            if key not in _ALLOWED_PLACEHOLDERS:
                raise ValueError(f'unsupported placeholder "{{{{{key}}}}}"')

    def render(self, **kwargs: Any) -> str:
        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            return _sanitize_value(kwargs.get(key, ""))

        return _PLACEHOLDER_RE.sub(_replace, self.template)


class DeclarativePluginTool(BrainTool):
    def __init__(self, name: str, description: str, template: str):
        TemplateRenderer._validate_template(template)
        self._name = name
        self._description = description
        self._template = template

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "free text input"},
                "context": {"type": "string", "description": "optional context"},
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        renderer = TemplateRenderer(self._template)
        return renderer.render(**kwargs)


class PluginLoader:
    def __init__(self, plugin_dir: Path, create_dir: bool = True):
        self.plugin_dir = Path(plugin_dir)
        if create_dir:
            self.plugin_dir.mkdir(parents=True, exist_ok=True)

    def ensure_templates(self) -> None:
        sample = self.plugin_dir / "risk_note.json"
        if sample.exists():
            return
        sample.write_text(
            json.dumps(
                {
                    "name": "plugin_risk_note",
                    "description": "Generate a compact risk note for latest context.",
                    "template": "[RiskNote] context={{context}} input={{input}}",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def load_tools(self) -> list[BrainTool]:
        tools: list[BrainTool] = []
        for path in sorted(self.plugin_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Skipped invalid plugin definition %s: %s", path, exc)
                continue
            name = str(data.get("name") or "").strip()
            description = str(data.get("description") or "").strip()
            template = str(data.get("template") or "").strip()
            if not name or not template:
                continue
            try:
                tools.append(
                    DeclarativePluginTool(
                        name=name,
                        description=description or name,
                        template=template,
                    )
                )
            except ValueError as exc:
                logger.warning("Skipped declarative plugin %s: %s", path, exc)
                continue
        return tools
