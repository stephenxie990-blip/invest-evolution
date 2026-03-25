"""Bridge message normalization utilities for safer session handling."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any


def _normalized_token(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _normalized_optional_token(value: Any) -> str:
    return str(value or "").strip()


def derive_internal_session_key(
    *,
    channel: str,
    chat_id: str,
    external_conversation_id: str = "",
) -> str:
    base = f"{channel}:{chat_id}"
    suffix = _normalized_optional_token(external_conversation_id)
    return f"{base}:{suffix}" if suffix else base


@dataclass(frozen=True)
class InboundEnvelope:
    id: str
    channel: str
    chat_id: str
    session_key: str
    content: str
    ts_ms: int
    metadata: dict[str, Any]


def normalize_inbound_envelope(data: dict[str, Any]) -> InboundEnvelope:
    if not isinstance(data, dict):
        raise ValueError("bridge message must be a JSON object")
    channel = _normalized_token(data.get("channel"), fallback="file")
    chat_id = _normalized_token(data.get("chat_id"), fallback="default")
    metadata = dict(data.get("metadata") or {}) if isinstance(data.get("metadata"), dict) else {}
    external_conversation_id = _normalized_optional_token(
        data.get("external_conversation_id") or metadata.get("external_conversation_id")
    )
    internal_session_key = derive_internal_session_key(
        channel=channel,
        chat_id=chat_id,
        external_conversation_id=external_conversation_id,
    )
    provided_session_key = _normalized_optional_token(data.get("session_key"))
    if provided_session_key and provided_session_key != internal_session_key:
        metadata["ignored_external_session_key"] = provided_session_key
    if external_conversation_id:
        metadata["external_conversation_id"] = external_conversation_id
    return InboundEnvelope(
        id=str(data.get("id") or uuid.uuid4().hex[:12]),
        channel=channel,
        chat_id=chat_id,
        session_key=internal_session_key,
        content=str(data.get("content") or ""),
        ts_ms=int(data.get("ts_ms") or time.time() * 1000),
        metadata=metadata,
    )
