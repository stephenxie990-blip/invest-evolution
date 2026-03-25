"""Shared stock-analysis parsing and template-rendering helpers."""

from __future__ import annotations

import json
from typing import Any


def render_template_args(
    payload: dict[str, Any],
    *,
    query: str,
    days: int,
) -> dict[str, Any]:
    def render(value: Any) -> Any:
        if isinstance(value, str):
            return (
                value.replace("{{query}}", query)
                .replace("{{days}}", str(days))
                .replace("{{history_days}}", str(days))
                .replace("{{trend_days}}", str(max(120, days)))
            )
        if isinstance(value, dict):
            return {k: render(v) for k, v in value.items()}
        if isinstance(value, list):
            return [render(v) for v in value]
        return value

    rendered = render(payload)
    for key, value in list(rendered.items()):
        if isinstance(value, str) and value.isdigit():
            rendered[key] = int(value)
    return rendered


def parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("tool arguments must decode to a JSON object")
        return parsed
    raise ValueError("tool arguments must be a JSON object or JSON string")


class StockAnalysisParsingService:
    @staticmethod
    def render_template_args(
        payload: dict[str, Any],
        *,
        query: str,
        days: int,
    ) -> dict[str, Any]:
        return render_template_args(payload, query=query, days=days)

    @staticmethod
    def parse_tool_args(raw: Any) -> dict[str, Any]:
        return parse_tool_args(raw)


__all__ = [
    "StockAnalysisParsingService",
    "parse_tool_args",
    "render_template_args",
]
