"""Shared tool-response builder helpers for stock analysis tools."""

from __future__ import annotations

from typing import Any


def build_tool_response(
    *,
    status: str,
    query: str,
    code: str,
    security: dict[str, Any] | None = None,
    **payload: Any,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "status": status,
        "query": query,
        "code": code,
    }
    if security is not None:
        response["security"] = security
    response.update(payload)
    return response


def build_tool_common_payload(
    *,
    summary: str,
    next_actions: list[str],
    artifacts: dict[str, Any] | None = None,
    observation_summary: str = "",
    metrics: dict[str, Any] | None = None,
    **payload: Any,
) -> dict[str, Any]:
    common_payload: dict[str, Any] = {
        "summary": summary,
        "next_actions": next_actions,
        "artifacts": dict(artifacts or {}),
    }
    if observation_summary:
        common_payload["observation_summary"] = observation_summary
    if metrics is not None:
        common_payload["metrics"] = metrics
    common_payload.update(payload)
    return common_payload


def build_tool_unavailable_response(
    *,
    status: str,
    query: str,
    code: str,
    summary: str,
    next_actions: list[str],
    artifacts: dict[str, Any] | None = None,
    security: dict[str, Any] | None = None,
    **payload: Any,
) -> dict[str, Any]:
    return build_tool_response(
        status=status,
        query=query,
        code=code,
        security=security,
        **build_tool_common_payload(
            summary=summary,
            next_actions=next_actions,
            artifacts=artifacts,
            **payload,
        ),
    )


def build_tool_analysis_response(
    *,
    query: str,
    code: str,
    security: dict[str, Any] | None,
    summary: str,
    next_actions: list[str],
    artifacts: dict[str, Any] | None = None,
    observation_summary: str = "",
    **payload: Any,
) -> dict[str, Any]:
    return build_tool_response(
        status="ok",
        query=query,
        code=code,
        security=security,
        **build_tool_common_payload(
            summary=summary,
            next_actions=next_actions,
            artifacts=artifacts,
            observation_summary=observation_summary,
            **payload,
        ),
    )


def build_tool_records_response(
    *,
    query: str,
    code: str,
    security: dict[str, Any] | None,
    records_key: str,
    records: list[dict[str, Any]],
    summary: str,
    next_actions: list[str],
    artifacts: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    **payload: Any,
) -> dict[str, Any]:
    records_payload = {
        records_key: records,
        "count": int(len(records)),
        **payload,
    }
    return build_tool_response(
        status="ok",
        query=query,
        code=code,
        security=security,
        **build_tool_common_payload(
            summary=summary,
            next_actions=next_actions,
            artifacts=artifacts,
            metrics=metrics,
            **records_payload,
        ),
    )
