"""Shared interface-layer response helpers for web runtime routes."""

from __future__ import annotations

from typing import Any

from flask import Response, jsonify

from app.commander_support.presentation import build_human_display


def contract_payload_root(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if isinstance(payload.get("protocol"), dict) or isinstance(payload.get("task_bus"), dict):
            return payload
        snapshot = payload.get("snapshot")
        if isinstance(snapshot, dict) and (
            isinstance(snapshot.get("protocol"), dict)
            or isinstance(snapshot.get("task_bus"), dict)
        ):
            return snapshot
    return None


def jsonify_contract_payload(payload: Any, *, status_code: int = 200):
    response = jsonify(payload)
    response.status_code = int(status_code)
    root = contract_payload_root(payload)
    if not root:
        return response

    protocol = dict(root.get("protocol") or {})
    task_bus = dict(root.get("task_bus") or {})
    coverage = dict(root.get("coverage") or {})
    artifact_taxonomy = dict(root.get("artifact_taxonomy") or {})

    if protocol.get("schema_version"):
        response.headers["X-Bounded-Workflow-Schema"] = str(protocol.get("schema_version"))
    if protocol.get("task_bus_schema_version"):
        response.headers["X-Task-Bus-Schema"] = str(protocol.get("task_bus_schema_version"))
    elif task_bus.get("schema_version"):
        response.headers["X-Task-Bus-Schema"] = str(task_bus.get("schema_version"))
    if coverage.get("schema_version"):
        response.headers["X-Coverage-Schema"] = str(coverage.get("schema_version"))
    if artifact_taxonomy.get("schema_version"):
        response.headers["X-Artifact-Taxonomy-Schema"] = str(
            artifact_taxonomy.get("schema_version")
        )
    if protocol.get("domain"):
        response.headers["X-Commander-Domain"] = str(protocol.get("domain"))
    if protocol.get("operation"):
        response.headers["X-Commander-Operation"] = str(protocol.get("operation"))
    return response


def attach_display_payload(payload: Any) -> dict[str, Any]:
    body: dict[str, Any] = (
        dict(payload or {}) if isinstance(payload, dict) else {"reply": str(payload)}
    )
    display = build_human_display(body)
    body.setdefault(
        "human_reply",
        str(display.get("text") or body.get("reply") or body.get("message") or ""),
    )
    body.setdefault(
        "display",
        {
            "available": bool(display.get("available")),
            "title": str(display.get("title") or ""),
            "summary": str(display.get("summary") or ""),
            "text": str(display.get("text") or ""),
            "sections": list(display.get("sections") or []),
            "suggested_actions": list(display.get("suggested_actions") or []),
            "recommended_next_step": str(display.get("recommended_next_step") or ""),
            "risk_level": str(display.get("risk_level") or ""),
            "synthesized": bool(display.get("synthesized")),
        },
    )
    return body


def respond_with_display(payload: Any, *, status_code: int = 200, view: str = "json"):
    enriched = attach_display_payload(payload)
    if view == "human":
        return Response(
            str(enriched.get("human_reply") or ""),
            status=int(status_code),
            mimetype="text/plain; charset=utf-8",
        )
    return jsonify_contract_payload(enriched, status_code=status_code)
