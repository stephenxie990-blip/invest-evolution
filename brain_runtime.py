"""
Local brain runtime (nanobot-like core) for the fused invest system.

This module intentionally lives inside src/ so the fused program no longer
relies on external nanobot package files at runtime.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from llm_gateway import LLMGateway, LLMGatewayError, LLMUnavailableError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool abstractions
# ---------------------------------------------------------------------------

class BrainTool(ABC):
    """Base class for brain tools."""

    _TYPE_MAP = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        pass

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        pass

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        pass

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            raise ValueError(f"Schema must be object type, got {schema.get('type')!r}")
        return self._validate(params, {**schema, "type": "object"}, "")

    def _validate(self, val: Any, schema: dict[str, Any], path: str) -> list[str]:
        t = schema.get("type")
        label = path or "parameter"
        errors: list[str] = []

        if t in self._TYPE_MAP and not isinstance(val, self._TYPE_MAP[t]):
            return [f"{label} should be {t}"]

        if "enum" in schema and val not in schema["enum"]:
            errors.append(f"{label} must be one of {schema['enum']}")

        if t in ("integer", "number"):
            if "minimum" in schema and val < schema["minimum"]:
                errors.append(f"{label} must be >= {schema['minimum']}")
            if "maximum" in schema and val > schema["maximum"]:
                errors.append(f"{label} must be <= {schema['maximum']}")

        if t == "object":
            props = schema.get("properties", {})
            for req in schema.get("required", []):
                if req not in val:
                    errors.append(f"missing required {path + '.' + req if path else req}")
            for key, item in val.items():
                if key in props:
                    errors.extend(self._validate(item, props[key], path + '.' + key if path else key))

        if t == "array" and "items" in schema:
            for i, item in enumerate(val):
                idx = f"{path}[{i}]" if path else f"[{i}]"
                errors.extend(self._validate(item, schema["items"], idx))

        return errors

    def to_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class BrainToolRegistry:
    """Runtime tool registry."""

    def __init__(self):
        self._tools: dict[str, BrainTool] = {}

    def register(self, tool: BrainTool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Optional[BrainTool]:
        return self._tools.get(name)

    def get_definitions(self) -> list[dict[str, Any]]:
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        _hint = "\n\n[Analyze the error above and try a different approach.]"
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

        try:
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + _hint
            result = await tool.execute(**params)
            if isinstance(result, str) and result.startswith("Error"):
                return result + _hint
            return result
        except Exception as exc:
            return f"Error executing {name}: {exc}" + _hint

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)


# ---------------------------------------------------------------------------
# Session and runtime
# ---------------------------------------------------------------------------

@dataclass
class BrainSession:
    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    updated_at: datetime = field(default_factory=datetime.now)


class BrainRuntime:
    """Lightweight local agent loop with tool-calling."""

    def __init__(
        self,
        workspace: Path,
        model: str,
        api_key: str = "",
        api_base: str = "",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        max_iterations: int = 20,
        memory_window: int = 120,
        system_prompt_provider: Optional[Callable[[], str]] = None,
    ):
        self.workspace = Path(workspace)
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_iterations = max_iterations
        self.memory_window = memory_window
        self.system_prompt_provider = system_prompt_provider

        self.tools = BrainToolRegistry()
        self.sessions: dict[str, BrainSession] = {}
        self.gateway = LLMGateway(
            model=self.model,
            api_key=self.api_key,
            api_base=self.api_base,
            timeout=120,
            max_retries=2,
        )

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    async def close(self) -> None:
        """Reserved for future resource cleanup."""
        return

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        session = self.sessions.setdefault(session_key, BrainSession(key=session_key))

        # Allow explicit tool execution without LLM: /tool <name> {json}
        explicit = await self._try_explicit_tool(content)
        if explicit is not None:
            self._append_turn(session, {"role": "user", "content": content}, {"role": "assistant", "content": explicit})
            return explicit

        if not self.gateway.available:
            fallback = (
                "LLM is not configured. Provide COMMANDER_API_KEY/LLM_API_KEY or use explicit tool calls: "
                "`/tool invest_status {}` / `/tool invest_train {\"rounds\":1,\"mock\":true}`"
            )
            self._append_turn(session, {"role": "user", "content": content}, {"role": "assistant", "content": fallback})
            return fallback

        messages = self._build_messages(session, content)
        result = await self._run_loop(messages, on_progress=on_progress)
        self._append_turn(session, {"role": "user", "content": content}, {"role": "assistant", "content": result})
        return result

    def _build_messages(self, session: BrainSession, content: str) -> list[dict[str, Any]]:
        system_prompt = self._system_prompt()
        history = session.messages[-self.memory_window:]
        return [
            {"role": "system", "content": system_prompt},
            *history,
            {
                "role": "user",
                "content": (
                    f"[Runtime]\nTime: {datetime.now().isoformat()}\n"
                    f"Workspace: {self.workspace}\n\n{content}"
                ),
            },
        ]

    def _system_prompt(self) -> str:
        if self.system_prompt_provider:
            return self.system_prompt_provider()
        return (
            "You are the fused investment commander. Use tools when needed. "
            "Respond concisely and avoid unsupported claims."
        )

    async def _run_loop(
        self,
        messages: list[dict[str, Any]],
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        final = ""

        for _ in range(max(1, self.max_iterations)):
            defs = self.tools.get_definitions()
            try:
                response = await self.gateway.acompletion_raw(
                    messages=self._sanitize_messages(messages),
                    temperature=self.temperature,
                    max_tokens=max(1, self.max_tokens),
                    tools=defs or None,
                    tool_choice="auto",
                )
            except LLMUnavailableError:
                return "LLM is not configured. Provide COMMANDER_API_KEY/LLM_API_KEY or use explicit tool calls."
            except LLMGatewayError as exc:
                logger.warning("brain runtime llm error: %s", exc)
                return "LLM request failed. Try explicit tool mode or retry later."
            choice = response.choices[0].message

            tool_calls = getattr(choice, "tool_calls", None) or []
            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": choice.content,
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
                )

                for tc in tool_calls:
                    args = self._parse_tool_args(tc.function.arguments)
                    if on_progress:
                        try:
                            await on_progress(f"tool: {tc.function.name}")
                        except Exception:
                            pass
                    result = await self.tools.execute(tc.function.name, args)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.function.name,
                            "content": result,
                        }
                    )
                continue

            final = (choice.content or "").strip()
            break

        if not final:
            final = "I could not produce a final response within iteration limits."
        return final

    @staticmethod
    def _sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        clean: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "assistant" and "tool_calls" in msg and content is None:
                content = ""
            clean_msg = {
                "role": role,
                "content": content,
            }
            if "tool_calls" in msg:
                clean_msg["tool_calls"] = msg["tool_calls"]
            if "tool_call_id" in msg:
                clean_msg["tool_call_id"] = msg["tool_call_id"]
            if "name" in msg:
                clean_msg["name"] = msg["name"]
            clean.append(clean_msg)
        return clean

    @staticmethod
    def _parse_tool_args(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return {}

    async def _try_explicit_tool(self, content: str) -> Optional[str]:
        stripped = content.strip()
        if not stripped.startswith("/tool "):
            return None

        # format: /tool <name> <json-args>
        parts = stripped.split(" ", 2)
        if len(parts) < 2:
            return "Error: Usage /tool <name> {json-args}"

        name = parts[1].strip()
        args = {}
        if len(parts) >= 3:
            raw = parts[2].strip()
            if raw:
                try:
                    args = json.loads(raw)
                except Exception as exc:
                    return f"Error: invalid json args: {exc}"
        return await self.tools.execute(name, args)

    def _append_turn(self, session: BrainSession, user_msg: dict[str, Any], assistant_msg: dict[str, Any]) -> None:
        session.messages.append(user_msg)
        session.messages.append(assistant_msg)
        if len(session.messages) > self.memory_window * 4:
            session.messages = session.messages[-self.memory_window * 4:]
        session.updated_at = datetime.now()
