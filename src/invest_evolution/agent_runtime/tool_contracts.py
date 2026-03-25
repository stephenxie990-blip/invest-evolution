"""Shared tool contracts for agent runtime internal API parity."""

from __future__ import annotations

from typing import Any


def agent_prompts_update_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "system_prompt": {"type": "string"},
        },
        "required": ["name", "system_prompt"],
        "additionalProperties": False,
    }
