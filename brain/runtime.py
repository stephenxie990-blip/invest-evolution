"""Local brain runtime for the fused invest system."""

from __future__ import annotations

import json
import textwrap
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from app.llm_gateway import LLMGateway, LLMGatewayError, LLMUnavailableError
from brain.task_bus import build_mutating_task_bus, build_readonly_task_bus

logger = logging.getLogger(__name__)


class ToolArgumentParseError(ValueError):
    """Raised when tool-call arguments cannot be parsed into a JSON object."""


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
            explicit = self._wrap_tool_response(
                explicit,
                user_goal=content,
                tool_names=[self._extract_explicit_tool_name(content)],
                mode="explicit_tool",
            )
            self._append_turn(session, {"role": "user", "content": content}, {"role": "assistant", "content": explicit})
            return explicit

        builtin = await self._try_builtin_intent(content)
        if builtin is not None:
            self._append_turn(session, {"role": "user", "content": content}, {"role": "assistant", "content": builtin})
            return builtin

        if not self.gateway.available:
            fallback = (
                "LLM is not configured. Provide COMMANDER_API_KEY/LLM_API_KEY or use explicit tool calls: "
                "`/tool invest_quick_status {}` / `/tool invest_train {\"rounds\":1,\"mock\":true}`"
            )
            self._append_turn(session, {"role": "user", "content": content}, {"role": "assistant", "content": fallback})
            return fallback

        messages = self._build_messages(session, content)
        result = await self._run_loop(messages, user_goal=content, on_progress=on_progress)
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
        return textwrap.dedent(
            """\
            You are the Investment Evolution runtime agent.
            Your job is to help the user inspect status, strategies, memory, and training through registered tools.

            Operating rules:
            1. Ground every factual statement in either the user message, prior tool outputs, or runtime metadata in this chat.
            2. Never invent tool results, file contents, market facts, config values, or training outcomes.
            3. When a tool is needed, call the single most relevant tool first and pass a valid JSON object as arguments.
            4. If a request can be answered from existing context, answer directly without unnecessary tool calls.
            5. If arguments are uncertain or a tool is unsuitable, say so explicitly instead of guessing.

            Response rules:
            - Be concise, operational, and audit-friendly.
            - Distinguish facts, risks, and next actions when helpful.
            - For state-changing actions, rely on tool outputs rather than promises or speculation.
            - Do not emit fake function calls, placeholder JSON, or unsupported claims.
            """
        )

    async def _run_loop(
        self,
        messages: list[dict[str, Any]],
        user_goal: str = "",
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        final = ""
        tool_trace: list[dict[str, Any]] = []

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
                    if on_progress:
                        try:
                            await on_progress(f"tool: {tc.function.name}")
                        except Exception:
                            pass
                    args: dict[str, Any] = {}
                    try:
                        args = self._parse_tool_args(tc.function.arguments)
                    except ToolArgumentParseError as exc:
                        result = f"Error: invalid tool arguments for {tc.function.name}: {exc}"
                    else:
                        result = await self.tools.execute(tc.function.name, args)
                    tool_trace.append({"action": {"tool": tc.function.name, "args": args}})
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
        return self._wrap_tool_response(final, user_goal=user_goal, tool_names=[str(item.get("action", {}).get("tool") or "") for item in tool_trace], mode="llm_tool_loop", tool_calls=tool_trace)

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
        if raw is None:
            return {}
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw:
                return {}
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ToolArgumentParseError(str(exc)) from exc
            if not isinstance(parsed, dict):
                raise ToolArgumentParseError("tool arguments must decode to a JSON object")
            return parsed
        raise ToolArgumentParseError("tool arguments must be a JSON object or JSON string")

    async def _try_explicit_tool(self, content: str) -> Optional[str]:
        stripped = content.strip()
        if not stripped.startswith("/tool "):
            return None

        # format: /tool <name> <json-args>
        parts = stripped.split(" ", 2)
        if len(parts) < 2:
            return "Error: Usage /tool <name> {json-args}"

        name = parts[1].strip()
        args: dict[str, Any] = {}
        if len(parts) >= 3:
            raw = parts[2].strip()
            if raw:
                try:
                    args = self._parse_tool_args(raw)
                except ToolArgumentParseError as exc:
                    return f"Error: invalid tool arguments for {name}: {exc}"
        return await self.tools.execute(name, args)

    @staticmethod
    def _tool_trace(tool_names: list[str]) -> list[dict[str, Any]]:
        return [{"action": {"tool": name, "args": {}}} for name in tool_names if name]

    @staticmethod
    def _extract_explicit_tool_name(content: str) -> str:
        stripped = str(content or "").strip()
        if not stripped.startswith("/tool "):
            return ""
        parts = stripped.split(" ", 2)
        return parts[1].strip() if len(parts) >= 2 else ""

    @staticmethod
    def _try_parse_json_object(raw: Any) -> dict[str, Any] | None:
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        if not text or not text.startswith("{"):
            return None
        try:
            parsed = json.loads(text)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _is_mutating_tool(name: str) -> bool:
        tool = str(name or "")
        if tool in {
            "invest_train",
            "invest_data_download",
            "invest_plugins_reload",
            "invest_cron_add",
            "invest_cron_remove",
            "invest_control_plane_update",
            "invest_runtime_paths_update",
            "invest_evolution_config_update",
            "invest_training_plan_create",
            "invest_training_plan_execute",
            "invest_agent_prompts_update",
        }:
            return True
        return tool.endswith("_update")

    def _risk_level_for_tools(self, tool_names: list[str]) -> str:
        names = {str(name or "") for name in tool_names}
        if any(name in {"invest_train", "invest_control_plane_update", "invest_runtime_paths_update", "invest_evolution_config_update"} for name in names):
            return "high"
        if any(self._is_mutating_tool(name) for name in names):
            return "medium"
        return "low"

    @staticmethod
    def _intent_for_tools(tool_names: list[str]) -> str:
        names = {str(name or "") for name in tool_names if str(name or "")}
        if "invest_ask_stock" in names:
            return "stock_analysis"
        if any(name.startswith("invest_data_") for name in names):
            return "data_operations"
        if any(name in {
            "invest_train",
            "invest_quick_test",
            "invest_training_plan_create",
            "invest_training_plan_list",
            "invest_training_plan_execute",
            "invest_training_runs_list",
            "invest_training_evaluations_list",
            "invest_training_lab_summary",
        } for name in names):
            return "training_execution"
        if any(name in {
            "invest_control_plane_get",
            "invest_control_plane_update",
            "invest_runtime_paths_get",
            "invest_runtime_paths_update",
            "invest_evolution_config_get",
            "invest_evolution_config_update",
            "invest_agent_prompts_list",
            "invest_agent_prompts_update",
        } for name in names):
            return "config_management"
        if any(name in {"invest_quick_status", "invest_deep_status", "invest_status", "invest_events_tail", "invest_events_summary", "invest_runtime_diagnostics"} for name in names):
            return "runtime_observability"
        if any(name in {"invest_list_strategies", "invest_reload_strategies", "invest_stock_strategies"} for name in names):
            return "strategy_inventory"
        if any(name in {"invest_leaderboard", "invest_investment_models", "invest_allocator", "invest_model_routing_preview"} for name in names):
            return "model_analytics"
        if any(name in {"invest_memory_search", "invest_memory_list", "invest_memory_get"} for name in names):
            return "memory_lookup"
        if any(name in {"invest_cron_add", "invest_cron_list", "invest_cron_remove"} for name in names):
            return "scheduler_management"
        if "invest_plugins_reload" in names:
            return "plugin_management"
        return "runtime_tooling"

    @staticmethod
    def _extract_rounds_from_goal(user_goal: str, default: int = 1) -> int:
        text = str(user_goal or "")
        match = re.search(r"(\d+)\s*(轮|次)", text)
        return max(1, int(match.group(1))) if match else max(1, int(default or 1))

    @staticmethod
    def _infer_mock_from_goal(user_goal: str) -> bool:
        low = str(user_goal or "").lower()
        return any(token in low for token in ["mock", "演示", "测试", "dry-run", "quick", "快速测试"])

    @staticmethod
    def _infer_refresh_from_goal(user_goal: str) -> bool:
        low = str(user_goal or "").lower()
        return any(token in low for token in ["refresh", "刷新", "重新检查", "重算"])

    @staticmethod
    def _infer_stock_strategy_from_goal(user_goal: str) -> str:
        low = str(user_goal or "").lower()
        if any(token in low for token in ["趋势跟随", "trend following", "趋势策略"]):
            return "trend_following"
        return "chan_theory"

    @staticmethod
    def _infer_days_from_goal(user_goal: str, default: int = 60) -> int:
        text = str(user_goal or "")
        match = re.search(r"(\d{2,4})\s*(?:个)?(?:交易)?(?:日|天)", text)
        if not match:
            return max(30, int(default or 60))
        return max(30, min(500, int(match.group(1))))

    @staticmethod
    def _infer_data_focus_from_goal(user_goal: str) -> str:
        low = str(user_goal or "").lower()
        if any(token in low for token in ["资金流", "capital flow"]):
            return "capital_flow"
        if any(token in low for token in ["龙虎榜", "dragon tiger"]):
            return "dragon_tiger"
        if any(token in low for token in ["60m", "60分钟", "60 分钟", "分时", "intraday"]):
            return "intraday_60m"
        if any(token in low for token in ["下载", "同步", "拉取", "download", "sync"]):
            return "download"
        return "status"

    @staticmethod
    def _infer_config_focus_from_goal(user_goal: str) -> str:
        low = str(user_goal or "").lower()
        if any(token in low for token in ["prompt", "提示词", "agent prompt", "角色提示"]):
            return "prompts"
        if any(token in low for token in ["路径", "workspace", "输出目录", "runtime path"]):
            return "paths"
        if any(token in low for token in ["控制面", "control plane", "模型绑定", "llm 绑定", "绑定"]):
            return "control_plane"
        return "evolution"

    def _recommended_plan_for_intent(
        self,
        *,
        intent: str,
        tool_names: list[str],
        writes_state: bool,
        user_goal: str,
    ) -> list[dict[str, Any]]:
        rounds = self._extract_rounds_from_goal(user_goal, default=1)
        mock = self._infer_mock_from_goal(user_goal)
        refresh = self._infer_refresh_from_goal(user_goal)
        strategy = self._infer_stock_strategy_from_goal(user_goal)
        days = self._infer_days_from_goal(user_goal, default=60)
        data_focus = self._infer_data_focus_from_goal(user_goal)
        config_focus = self._infer_config_focus_from_goal(user_goal)

        if intent in {"training_execution", "training_lab_summary"}:
            if writes_state:
                return [
                    {"tool": "invest_quick_test", "args": {}},
                    {"tool": "invest_training_plan_create", "args": {"rounds": rounds, "mock": mock, "goal": user_goal or "training request"}},
                    {"tool": "invest_training_plan_execute", "args": {"plan_id": "<created_plan_id>"}},
                    {"tool": "invest_training_evaluations_list", "args": {"limit": 5}},
                    {"tool": "invest_training_lab_summary", "args": {"limit": 5}},
                ]
            return [
                {"tool": "invest_training_runs_list", "args": {"limit": 5}},
                {"tool": "invest_training_evaluations_list", "args": {"limit": 5}},
                {"tool": "invest_training_lab_summary", "args": {"limit": 5}},
            ]
        if intent in {"config_management", "config_overview", "config_prompts", "runtime_paths"}:
            if intent == "config_overview":
                primary_tool = {
                    "prompts": "invest_agent_prompts_list",
                    "paths": "invest_runtime_paths_get",
                    "control_plane": "invest_control_plane_get",
                }.get(config_focus, "invest_evolution_config_get")
                base_plan = [
                    {"tool": primary_tool, "args": {}},
                    {"tool": "invest_control_plane_get", "args": {}},
                    {"tool": "invest_evolution_config_get", "args": {}},
                ]
                if config_focus not in {"prompts", "paths", "control_plane"}:
                    base_plan.append({"tool": "invest_runtime_paths_get", "args": {}})
                deduped_plan: list[dict[str, Any]] = []
                seen_tools: set[str] = set()
                for item in base_plan:
                    tool_name = str(item.get("tool") or "")
                    if tool_name and tool_name not in seen_tools:
                        deduped_plan.append(item)
                        seen_tools.add(tool_name)
                if writes_state:
                    if config_focus == "prompts":
                        deduped_plan.append({"tool": "invest_agent_prompts_update", "args": {"name": "<agent>", "system_prompt": "<prompt>"}})
                    elif config_focus == "paths":
                        deduped_plan.append({"tool": "invest_runtime_paths_update", "args": {"patch": {"<path_key>": "<new_path>"}, "confirm": False}})
                    elif config_focus == "control_plane":
                        deduped_plan.append({"tool": "invest_control_plane_update", "args": {"patch": {"<section>": "<value>"}, "confirm": False}})
                    else:
                        deduped_plan.append({"tool": "invest_evolution_config_update", "args": {"patch": {"<param>": "<value>"}, "confirm": False}})
                    deduped_plan.append({"tool": "invest_runtime_diagnostics", "args": {"event_limit": 50, "memory_limit": 20}})
                return deduped_plan
            if config_focus == "prompts":
                plan = [{"tool": "invest_agent_prompts_list", "args": {}}]
                if writes_state:
                    plan.append({"tool": "invest_agent_prompts_update", "args": {"name": "<agent>", "system_prompt": "<prompt>"}})
                return plan
            if config_focus == "paths":
                plan = [{"tool": "invest_runtime_paths_get", "args": {}}]
                if writes_state:
                    plan.extend([
                        {"tool": "invest_runtime_paths_update", "args": {"patch": {"<path_key>": "<new_path>"}, "confirm": False}},
                        {"tool": "invest_runtime_diagnostics", "args": {"event_limit": 50, "memory_limit": 20}},
                    ])
                return plan
            if config_focus == "control_plane":
                plan = [{"tool": "invest_control_plane_get", "args": {}}]
                if writes_state:
                    plan.extend([
                        {"tool": "invest_control_plane_update", "args": {"patch": {"<section>": "<value>"}, "confirm": False}},
                        {"tool": "invest_runtime_diagnostics", "args": {"event_limit": 50, "memory_limit": 20}},
                    ])
                return plan
            plan = [
                {"tool": "invest_evolution_config_get", "args": {}},
                {"tool": "invest_control_plane_get", "args": {}},
                {"tool": "invest_runtime_paths_get", "args": {}},
            ]
            if writes_state:
                plan.extend([
                    {"tool": "invest_evolution_config_update", "args": {"patch": {"<param>": "<value>"}, "confirm": False}},
                    {"tool": "invest_runtime_diagnostics", "args": {"event_limit": 50, "memory_limit": 20}},
                ])
            return plan
        if intent in {"data_operations", "data_status"}:
            if data_focus == "capital_flow":
                return [
                    {"tool": "invest_data_status", "args": {"refresh": refresh}},
                    {"tool": "invest_data_capital_flow", "args": {"limit": 200}},
                ]
            if data_focus == "dragon_tiger":
                return [
                    {"tool": "invest_data_status", "args": {"refresh": refresh}},
                    {"tool": "invest_data_dragon_tiger", "args": {"limit": 200}},
                ]
            if data_focus == "intraday_60m":
                return [
                    {"tool": "invest_data_status", "args": {"refresh": refresh}},
                    {"tool": "invest_data_intraday_60m", "args": {"limit": 500}},
                ]
            plan = [
                {"tool": "invest_data_status", "args": {"refresh": refresh}},
                {"tool": "invest_data_download", "args": {"action": "status"}},
            ]
            if writes_state or data_focus == "download":
                plan.extend([
                    {"tool": "invest_data_download", "args": {"action": "trigger", "confirm": False}},
                    {"tool": "invest_data_status", "args": {"refresh": True}},
                ])
            return plan
        if intent == "stock_analysis":
            return [
                {"tool": "invest_stock_strategies", "args": {}},
                {"tool": "invest_ask_stock", "args": {"query": user_goal or "<stock>", "question": user_goal or "<question>", "strategy": strategy, "days": days}},
            ]
        if intent in {"runtime_observability", "runtime_status", "runtime_status_and_training", "runtime_diagnostics", "config_risk_diagnostics"}:
            plan = [{"tool": "invest_quick_status", "args": {}}]
            if any(token in str(user_goal or "") for token in ["深度", "slow", "deep"]):
                plan[0] = {"tool": "invest_deep_status", "args": {}}
            plan.extend([
                {"tool": "invest_events_summary", "args": {"limit": 100}},
                {"tool": "invest_runtime_diagnostics", "args": {"event_limit": 50, "memory_limit": 20}},
            ])
            return plan
        if intent == "strategy_inventory":
            return [
                {"tool": "invest_list_strategies", "args": {"only_enabled": False}},
                {"tool": "invest_stock_strategies", "args": {}},
            ]
        if intent == "model_analytics":
            return [
                {"tool": "invest_investment_models", "args": {}},
                {"tool": "invest_leaderboard", "args": {}},
                {"tool": "invest_model_routing_preview", "args": {}},
            ]
        if intent == "memory_lookup":
            return [
                {"tool": "invest_memory_search", "args": {"query": user_goal or "", "limit": 10}},
                {"tool": "invest_memory_list", "args": {"query": user_goal or "", "limit": 10}},
            ]
        if intent == "scheduler_management":
            plan = [{"tool": "invest_cron_list", "args": {}}]
            if writes_state:
                plan.append({"tool": "invest_cron_add", "args": {"message": user_goal or "<job>", "cron": "0 * * * *"}})
            return plan
        if intent == "plugin_management":
            return [
                {"tool": "invest_plugins_reload", "args": {}},
                {"tool": "invest_runtime_diagnostics", "args": {"event_limit": 50, "memory_limit": 20}},
            ]
        return [{"tool": name, "args": {}} for name in tool_names]

    def _wrap_tool_response(
        self,
        result: Any,
        *,
        user_goal: str,
        tool_names: list[str],
        mode: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> str:
        names = [str(name or "") for name in tool_names if str(name or "")]
        if not names or not any(name.startswith("invest_") for name in names):
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
        payload = self._try_parse_json_object(result) or {}
        status = str(payload.get("status", "ok")) if payload else "ok"
        writes_state = any(self._is_mutating_tool(name) for name in names)
        risk_level = self._risk_level_for_tools(names)
        if not payload:
            payload = {"status": status, "reply": str(result)}
        elif "reply" not in payload and not payload.get("message") and isinstance(result, str) and not result.strip().startswith("{"):
            payload["reply"] = str(result)
        inferred_intent = self._intent_for_tools(names)
        payload.setdefault("entrypoint", {
            "kind": "commander_tool_runtime",
            "resolver": f"BrainRuntime.{mode}",
            "mode": mode,
            "meeting_path": False,
            "tools": names,
            "intent": inferred_intent,
        })
        if "task_bus" not in payload:
            plan = self._recommended_plan_for_intent(intent=inferred_intent, tool_names=names, writes_state=writes_state, user_goal=user_goal)
            calls = list(tool_calls or self._tool_trace(names))
            artifacts = {"workspace": str(self.workspace), "tools": names, "mode": mode}
            if writes_state:
                payload["task_bus"] = build_mutating_task_bus(
                    intent=inferred_intent,
                    operation=mode,
                    user_goal=user_goal,
                    mode=mode,
                    available_tools=self.tools.tool_names,
                    recommended_plan=plan,
                    tool_calls=calls,
                    artifacts=artifacts,
                    status=status,
                    risk_level=risk_level,
                    decision="confirm" if status == "confirmation_required" else "allow",
                    requires_confirmation=status == "confirmation_required",
                    reasons=["state_changing_request", "tool_grounded_execution"],
                )
            else:
                payload["task_bus"] = build_readonly_task_bus(
                    intent=inferred_intent,
                    operation=mode,
                    user_goal=user_goal,
                    mode=mode,
                    available_tools=self.tools.tool_names,
                    recommended_plan=plan,
                    tool_calls=calls,
                    artifacts=artifacts,
                    status=status,
                )
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _wrap_builtin_payload(
        self,
        payload: Any,
        *,
        user_goal: str,
        intent: str,
        operation: str,
        tool_names: list[str],
        writes_state: bool = False,
        risk_level: str = "low",
        recommended_plan: list[dict[str, Any]] | None = None,
        reasons: list[str] | None = None,
    ) -> str:
        if not isinstance(payload, dict):
            return json.dumps({
                "status": "ok",
                "content": payload,
                "entrypoint": {
                    "kind": "commander_builtin_intent",
                    "resolver": "BrainRuntime._try_builtin_intent",
                    "intent": intent,
                    "operation": operation,
                },
            }, ensure_ascii=False, indent=2)
        if "task_bus" in payload:
            return json.dumps(payload, ensure_ascii=False, indent=2)
        status = str(payload.get("status", "ok"))
        requires_confirmation = status == "confirmation_required"
        decision = "confirm" if requires_confirmation else "allow"
        plan = list(recommended_plan or self._recommended_plan_for_intent(intent=intent, tool_names=tool_names, writes_state=writes_state, user_goal=user_goal))
        tool_calls = self._tool_trace(tool_names)
        artifacts = {
            "workspace": str(self.workspace),
            "intent": intent,
            "operation": operation,
        }
        if writes_state:
            task_bus = build_mutating_task_bus(
                intent=intent,
                operation=operation,
                user_goal=user_goal,
                mode="builtin_intent",
                available_tools=self.tools.tool_names,
                recommended_plan=plan,
                tool_calls=tool_calls,
                artifacts=artifacts,
                status=status,
                risk_level=risk_level,
                decision=decision,
                requires_confirmation=requires_confirmation,
                reasons=list(reasons or ["state_changing_request", "tool_grounded_execution"]),
            )
        else:
            task_bus = build_readonly_task_bus(
                intent=intent,
                operation=operation,
                user_goal=user_goal,
                mode="builtin_intent",
                available_tools=self.tools.tool_names,
                recommended_plan=plan,
                tool_calls=tool_calls,
                artifacts=artifacts,
                status=status,
            )
        payload = dict(payload)
        payload.setdefault("entrypoint", {
            "kind": "commander_builtin_intent",
            "resolver": "BrainRuntime._try_builtin_intent",
            "intent": intent,
            "operation": operation,
            "meeting_path": False,
        })
        payload["task_bus"] = task_bus
        return json.dumps(payload, ensure_ascii=False, indent=2)


    async def _try_builtin_intent(self, content: str) -> Optional[str]:
        text = str(content or "").strip()
        if not text:
            return None
        low = text.lower()
        names = set(self.tools._tools.keys())  # pylint: disable=protected-access

        def has(name: str) -> bool:
            return name in names

        async def run(name: str, args: dict[str, Any] | None = None) -> Optional[str]:
            if not has(name):
                return None
            return await self.tools.execute(name, args or {})

        async def run_json(name: str, args: dict[str, Any] | None = None) -> Any:
            result = await run(name, args)
            if result is None:
                return None
            try:
                return json.loads(result)
            except Exception:
                return result

        def has_any(haystack: str, terms: list[str]) -> bool:
            return any(term in haystack for term in terms)

        data_terms = ["数据", "行情", "日线", "资金流", "龙虎榜", "data"]
        data_status_terms = ["数据状态", "数据健康", "data status", "data health", "刷新数据"]
        diagnostics_terms = ["诊断", "diagnostics", "runtime", "运行诊断", "运行信息", "日志", "log"]
        event_terms = ["事件", "events", "最近事件", "事件摘要"]
        training_lab_terms = ["训练实验室", "training lab", "最近训练", "训练记录", "训练结果", "实验记录", "run list", "eval"]
        training_exec_terms = ["训练", "跑一轮", "开始训练", "执行训练", "run training", "train once", "train"]
        status_terms = ["系统状态", "系统概览", "状态", "status", "系统情况"]
        leaderboard_terms = ["排行榜", "榜单", "leaderboard"]
        strategy_terms = ["策略列表", "列出策略", "有哪些策略", "strategy list"]
        quick_test_terms = ["快速测试", "健康检查", "quick test", "smoke"]
        config_terms = ["配置", "config", "设置"]
        control_terms = ["控制面", "control plane", "模型绑定", "llm 绑定", "绑定"]
        path_terms = ["路径", "runtime path", "输出目录", "workspace"]
        prompt_terms = ["prompt", "提示词", "agent prompt", "agent prompts", "角色提示"]
        stock_explicit_terms = ["问股", "股票", "个股", "stock"]
        stock_strategy_terms = ["缠论", "均线", "macd", "rsi", "趋势", "筹码"]
        stock_verbs = ["分析", "看看", "看下", "看一下", "研究"]
        conflict_terms = data_terms + status_terms + diagnostics_terms + event_terms + training_lab_terms + training_exec_terms + leaderboard_terms + strategy_terms + config_terms + control_terms + path_terms + prompt_terms
        stock_code_like = bool(re.search(r"(?i)\b(?:sh|sz)\.?\d{6}\b|\b\d{6}\b", text))
        asks_recent_training = has_any(text, training_lab_terms)
        asks_training_exec = has_any(low, training_exec_terms) and not asks_recent_training
        asks_status = has_any(text, status_terms)
        asks_data_status = has_any(text, data_status_terms) or (has_any(text, data_terms) and has_any(text, ["状态", "健康", "刷新", "refresh", "诊断", "check"]))
        asks_diagnostics = has_any(text, diagnostics_terms) or has_any(text, event_terms)
        asks_config = has_any(low, config_terms) or has_any(low, control_terms) or has_any(low, path_terms) or has_any(low, prompt_terms)
        asks_stock = (
            has_any(low, stock_explicit_terms)
            or stock_code_like
            or (has_any(text, stock_strategy_terms) and has_any(text, stock_verbs))
            or (
                not has_any(text, conflict_terms)
                and any(text.startswith(prefix) for prefix in ["看看", "看下", "看一下", "分析", "分析一下", "研究", "研究一下"])
            )
        )

        if has_any(text, ["深度状态", "慢状态", "deep status"]):
            payload = await run_json("invest_deep_status")
            return self._wrap_builtin_payload(payload, user_goal=text, intent="runtime_status", operation="invest_deep_status", tool_names=["invest_deep_status"])

        if asks_data_status:
            payload = await run_json("invest_data_status", {"refresh": any(token in low for token in ["refresh", "刷新", "slow"])})
            return self._wrap_builtin_payload(payload, user_goal=text, intent="data_status", operation="invest_data_status", tool_names=["invest_data_status"])

        if asks_status and asks_recent_training:
            quick = await run_json("invest_quick_status")
            lab = await run_json("invest_training_lab_summary")
            payload = {
                "status": "ok",
                "intent": "status_and_recent_training",
                "quick_status": quick,
                "training_lab": lab,
            }
            return self._wrap_builtin_payload(payload, user_goal=text, intent="runtime_status_and_training", operation="status_and_recent_training", tool_names=["invest_quick_status", "invest_training_lab_summary"])

        if asks_diagnostics:
            payload = await run_json("invest_runtime_diagnostics")
            return self._wrap_builtin_payload(payload, user_goal=text, intent="runtime_diagnostics", operation="invest_runtime_diagnostics", tool_names=["invest_runtime_diagnostics"])

        if asks_recent_training:
            payload = await run_json("invest_training_lab_summary")
            return self._wrap_builtin_payload(payload, user_goal=text, intent="training_lab_summary", operation="invest_training_lab_summary", tool_names=["invest_training_lab_summary"])

        if has_any(text, leaderboard_terms):
            payload = await run_json("invest_leaderboard")
            return self._wrap_builtin_payload(payload, user_goal=text, intent="leaderboard", operation="invest_leaderboard", tool_names=["invest_leaderboard"])

        if has_any(text, strategy_terms):
            payload = await run_json("invest_list_strategies")
            return self._wrap_builtin_payload(payload, user_goal=text, intent="strategy_inventory", operation="invest_list_strategies", tool_names=["invest_list_strategies"])

        if has_any(text, quick_test_terms):
            payload = await run_json("invest_quick_test")
            return self._wrap_builtin_payload(payload, user_goal=text, intent="runtime_quick_test", operation="invest_quick_test", tool_names=["invest_quick_test"])

        if asks_config:
            if has_any(low, prompt_terms):
                payload = await run_json("invest_agent_prompts_list")
                return self._wrap_builtin_payload(payload, user_goal=text, intent="config_prompts", operation="invest_agent_prompts_list", tool_names=["invest_agent_prompts_list"])
            if has_any(low, path_terms):
                payload = await run_json("invest_runtime_paths_get")
                return self._wrap_builtin_payload(payload, user_goal=text, intent="runtime_paths", operation="invest_runtime_paths_get", tool_names=["invest_runtime_paths_get"])
            if any(token in text for token in ["有没有问题", "有问题", "异常", "风险"]):
                payload = await run_json("invest_runtime_diagnostics")
                return self._wrap_builtin_payload(payload, user_goal=text, intent="config_risk_diagnostics", operation="invest_runtime_diagnostics", tool_names=["invest_runtime_diagnostics"])
            control_plane = await run_json("invest_control_plane_get")
            evolution_config = await run_json("invest_evolution_config_get")
            payload = {
                "status": "ok",
                "intent": "config_overview",
                "control_plane": control_plane,
                "evolution_config": evolution_config,
            }
            return self._wrap_builtin_payload(payload, user_goal=text, intent="config_overview", operation="config_overview", tool_names=["invest_control_plane_get", "invest_evolution_config_get"])

        if asks_training_exec:
            rounds_match = re.search(r"(\d+)\s*(轮|次)", text)
            rounds = int(rounds_match.group(1)) if rounds_match else 1
            mock = any(token in low for token in ["mock", "演示", "测试", "dry-run", "quick"])
            confirm = any(token in low for token in ["确认", "confirm"])
            payload = await run_json("invest_train", {"rounds": rounds, "mock": mock, "confirm": confirm})
            risk_level = "low" if mock else "high" if rounds > 1 else "medium"
            return self._wrap_builtin_payload(
                payload,
                user_goal=text,
                intent="training_execution",
                operation="invest_train",
                tool_names=["invest_train"],
                writes_state=True,
                risk_level=risk_level,
                reasons=["training_changes_runtime_state", "tool_grounded_execution"],
            )

        if asks_status:
            payload = await run_json("invest_quick_status")
            return self._wrap_builtin_payload(payload, user_goal=text, intent="runtime_status", operation="invest_quick_status", tool_names=["invest_quick_status"])

        if asks_stock:
            result = await run("invest_ask_stock", {"query": text, "question": text})
            if result and not result.startswith("Error executing invest_ask_stock"):
                return result
            if self.gateway.available:
                return None
            return result
        return None

    def _append_turn(self, session: BrainSession, user_msg: dict[str, Any], assistant_msg: dict[str, Any]) -> None:
        session.messages.append(user_msg)
        session.messages.append(assistant_msg)
        if len(session.messages) > self.memory_window * 4:
            session.messages = session.messages[-self.memory_window * 4:]
        session.updated_at = datetime.now()
