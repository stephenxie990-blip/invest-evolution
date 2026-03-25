"""Shared stock-analysis LLM prompt and tool-presentation helpers."""

from __future__ import annotations

import json
from typing import Any, Callable


def stock_tool_definitions(
    definitions_by_name: dict[str, dict[str, Any]],
    *,
    allowed_tools: list[str] | None = None,
) -> list[dict[str, Any]]:
    catalog = [dict(item) for item in definitions_by_name.values()]
    if not allowed_tools:
        return catalog
    allowed = {
        str(name or "").strip() for name in allowed_tools if str(name or "").strip()
    }
    return [
        item
        for item in catalog
        if str(dict(item.get("function") or {}).get("name") or "") in allowed
    ]


def default_thought(
    tool_name: str,
    *,
    normalize_tool_name: Callable[[str], str],
    catalog_by_name: dict[str, dict[str, Any]],
) -> str:
    metadata = catalog_by_name.get(normalize_tool_name(tool_name), {})
    return str(metadata.get("thought") or f"调用 {tool_name} 获取下一步分析所需信息。")


def build_llm_assistant_tool_message(
    *,
    choice: Any,
    tool_calls: list[Any],
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": getattr(choice, "content", "") or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ],
    }


def build_llm_tool_result_message(
    *,
    tool_call_id: str,
    tool_name: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": tool_name,
        "content": json.dumps(result, ensure_ascii=False),
    }


def stock_system_prompt() -> str:
    return (
        "You are a bounded stock-analysis planning agent. "
        "The tool catalog is restricted by the strategy YAML and defines your hard boundary. "
        "Use the provided tools to gather evidence before concluding. "
        "Prefer the strategy's required tools, stay close to the YAML workflow, and stop once the evidence is sufficient. "
        "If you already have enough evidence, return a short final reasoning summary. "
        "Do not invent market data or tool results."
    )


def build_stock_user_prompt(
    *,
    question: str,
    query: str,
    security: dict[str, Any],
    strategy: Any,
    days: int,
) -> str:
    return (
        f"User question: {question}\n"
        f"Target security: {security.get('name') or ''} ({query})\n"
        f"Strategy: {strategy.display_name} / {strategy.name}\n"
        f"Description: {strategy.description}\n"
        f"Required tools: {', '.join(strategy.required_tools)}\n"
        f"Analysis steps: {' -> '.join(strategy.analysis_steps)}\n"
        f"Core rules: {'; '.join(strategy.core_rules)}\n"
        f"Entry conditions: {'; '.join(strategy.entry_conditions)}\n"
        f"Scoring rules: {json.dumps(strategy.scoring, ensure_ascii=False)}\n"
        f"Recommended lookback days: {days}\n"
        f"Suggested planner prompt: {strategy.planner_prompt}\n"
        "First decide the next most useful tool call."
    )


class StockAnalysisPromptService:
    def __init__(
        self,
        *,
        normalize_tool_name: Callable[[str], str],
        catalog_by_name_provider: Callable[[], dict[str, dict[str, Any]]],
        definitions_by_name_provider: Callable[[], dict[str, dict[str, Any]]],
    ) -> None:
        self.normalize_tool_name = normalize_tool_name
        self.catalog_by_name_provider = catalog_by_name_provider
        self.definitions_by_name_provider = definitions_by_name_provider

    def default_thought(self, tool_name: str) -> str:
        return default_thought(
            tool_name,
            normalize_tool_name=self.normalize_tool_name,
            catalog_by_name=self.catalog_by_name_provider(),
        )

    def stock_tool_definitions(
        self,
        allowed_tools: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return stock_tool_definitions(
            self.definitions_by_name_provider(),
            allowed_tools=allowed_tools,
        )


__all__ = [
    "StockAnalysisPromptService",
    "build_llm_assistant_tool_message",
    "build_llm_tool_result_message",
    "build_stock_user_prompt",
    "default_thought",
    "stock_system_prompt",
    "stock_tool_definitions",
]
