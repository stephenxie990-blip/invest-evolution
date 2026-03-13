"""Ask-flow helpers for commander runtime interactions."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable


def extract_ask_result_metadata(response: Any) -> dict[str, Any]:
    try:
        payload = json.loads(response) if isinstance(response, str) else dict(response or {})
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}

    protocol = dict(payload.get("protocol") or {})
    entrypoint = dict(payload.get("entrypoint") or {})
    next_action = dict(payload.get("next_action") or {})
    metadata: dict[str, Any] = {}

    status = str(payload.get("status") or "").strip()
    if status:
        metadata["status"] = status
    if protocol.get("domain"):
        metadata["domain"] = str(protocol["domain"])
    if protocol.get("operation"):
        metadata["operation"] = str(protocol["operation"])
    if protocol.get("schema_version"):
        metadata["protocol_schema_version"] = str(protocol["schema_version"])
    if entrypoint.get("kind"):
        metadata["entrypoint_kind"] = str(entrypoint["kind"])
    if entrypoint.get("intent"):
        metadata["intent"] = str(entrypoint["intent"])
    if next_action.get("kind"):
        metadata["next_action_kind"] = str(next_action["kind"])
    return metadata


def append_session_message(
    memory: Any,
    *,
    kind: str,
    session_key: str,
    content: str,
    channel: str,
    chat_id: str,
) -> None:
    memory.append(
        kind=kind,
        session_key=session_key,
        content=content,
        metadata={"channel": channel, "chat_id": chat_id},
    )


def record_runtime_ask_activity(
    *,
    memory: Any,
    append_runtime_event: Callable[[str, dict[str, Any]], Any],
    event: str,
    session_key: str,
    channel: str,
    chat_id: str,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = {"session_key": session_key, "channel": channel, "chat_id": chat_id, **dict(extra or {})}
    memory.append_audit(event, session_key, payload)
    append_runtime_event(event, payload)


async def execute_runtime_ask(
    *,
    message: str,
    session_key: str,
    channel: str,
    chat_id: str,
    ensure_runtime_storage: Callable[[], None],
    begin_task: Callable[..., None],
    memory: Any,
    record_ask_activity: Callable[..., None],
    process_direct: Callable[..., Awaitable[str]],
    complete_runtime_task: Callable[..., None],
    status_ok: str,
    status_error: str,
    event_ask_started: str,
    event_ask_finished: str,
) -> str:
    ensure_runtime_storage()
    begin_task("ask", channel, session_key=session_key, chat_id=chat_id)
    append_session_message(
        memory,
        kind="user",
        session_key=session_key,
        content=message,
        channel=channel,
        chat_id=chat_id,
    )
    record_ask_activity(
        event_ask_started,
        session_key=session_key,
        channel=channel,
        chat_id=chat_id,
        extra={"message_length": len(message)},
    )
    try:
        response = await process_direct(message, session_key=session_key)
        append_session_message(
            memory,
            kind="assistant",
            session_key=session_key,
            content=response or "",
            channel=channel,
            chat_id=chat_id,
        )
        ask_result = extract_ask_result_metadata(response)
        record_ask_activity(
            event_ask_finished,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            extra={"message_length": len(message), **ask_result},
        )
        complete_runtime_task(status=status_ok)
        return response
    except Exception:
        complete_runtime_task(status=status_error)
        raise
