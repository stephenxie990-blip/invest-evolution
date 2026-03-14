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
from brain.presentation import BrainHumanReadablePresenter
from brain.planner_catalog import (
    build_config_overview_plan,
    build_data_focus_plan,
    build_model_analytics_plan,
    build_plugin_reload_plan,
    build_runtime_status_plan,
    build_strategy_plan,
    build_training_execution_plan,
    build_training_history_plan,
)
from brain.schema_contract import (
    MUTATING_DEFAULT_REASON_CODES,
    RISK_LEVEL_HIGH,
    RISK_LEVEL_LOW,
    RISK_LEVEL_MEDIUM,
    TRAINING_DEFAULT_REASON_CODES,
)
from brain.task_bus import build_bounded_entrypoint, build_bounded_orchestration, build_bounded_policy, build_mutating_task_bus, build_readonly_task_bus, build_protocol_response
from brain.tool_metadata import RUNTIME_OBSERVABILITY_TOOL_NAMES

logger = logging.getLogger(__name__)


def _dict_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


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
                "LLM is not configured. Check control-plane default bindings or provider api_key, "
                "or use explicit tool calls: "
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
                return "LLM is not configured. Check control-plane default bindings or provider api_key, or use explicit tool calls."
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
            return RISK_LEVEL_HIGH
        if any(self._is_mutating_tool(name) for name in names):
            return RISK_LEVEL_MEDIUM
        return RISK_LEVEL_LOW

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
        if any(name in RUNTIME_OBSERVABILITY_TOOL_NAMES for name in names):
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
                return build_training_execution_plan(rounds=rounds, mock=mock, user_goal=user_goal, limit=5)
            return build_training_history_plan(limit=5)
        if intent in {"config_management", "config_overview", "config_prompts", "runtime_paths"}:
            if intent == "config_overview":
                return build_config_overview_plan(config_focus=config_focus, writes_state=writes_state)
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
            return build_data_focus_plan(data_focus=data_focus, refresh=refresh, writes_state=writes_state)
        if intent == "stock_analysis":
            return [
                {"tool": "invest_stock_strategies", "args": {}},
                {"tool": "invest_ask_stock", "args": {"query": user_goal or "<stock>", "question": user_goal or "<question>", "strategy": strategy, "days": days}},
            ]
        if intent in {"runtime_observability", "runtime_status", "runtime_status_and_training", "runtime_diagnostics", "config_risk_diagnostics"}:
            primary_tool = "invest_deep_status" if any(token in str(user_goal or "") for token in ["深度", "slow", "deep"]) else "invest_quick_status"
            return build_runtime_status_plan(
                primary_tool=primary_tool,
                detail_mode="fast",
                summary_limit=100,
                event_limit=50,
                memory_limit=20,
            )
        if intent == "strategy_inventory":
            return build_strategy_plan("strategy_inventory")
        if intent == "model_analytics":
            return build_model_analytics_plan("model_analytics")
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
                *build_plugin_reload_plan(),
                {"tool": "invest_runtime_diagnostics", "args": {"event_limit": 50, "memory_limit": 20}},
            ]
        return [{"tool": name, "args": {}} for name in tool_names]

    @staticmethod
    def _payload_coverage(payload: dict[str, Any]) -> dict[str, Any] | None:
        direct = payload.get("coverage")
        if isinstance(direct, dict):
            return dict(direct)
        orchestration = dict(payload.get("orchestration") or {})
        coverage = orchestration.get("coverage")
        if isinstance(coverage, dict):
            return dict(coverage)
        return None

    @staticmethod
    def _payload_artifacts(payload: dict[str, Any], *, base: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(base)
        direct = payload.get("artifacts")
        if isinstance(direct, dict):
            artifacts.update(direct)
        training_lab = payload.get("training_lab")
        if isinstance(training_lab, dict):
            artifacts.setdefault("training_lab", training_lab)
        return artifacts

    def _build_task_bus_for_payload(
        self,
        *,
        payload: dict[str, Any],
        user_goal: str,
        intent: str,
        operation: str,
        mode: str,
        tool_names: list[str],
        writes_state: bool,
        risk_level: str,
        recommended_plan: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        reasons: list[str] | None = None,
        artifacts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = str(payload.get("status", "ok"))
        requires_confirmation = status == "confirmation_required"
        decision = "confirm" if requires_confirmation else "allow"
        normalized_artifacts = self._payload_artifacts(payload, base=dict(artifacts or {}))
        coverage = self._payload_coverage(payload)
        builder = build_mutating_task_bus if writes_state else build_readonly_task_bus
        kwargs = {
            "intent": intent,
            "operation": operation,
            "user_goal": user_goal,
            "mode": mode,
            "available_tools": self.tools.tool_names,
            "recommended_plan": list(recommended_plan),
            "tool_calls": list(tool_calls),
            "artifacts": normalized_artifacts,
            "coverage": coverage,
            "status": status,
        }
        if writes_state:
            kwargs.update(
                {
                    "risk_level": risk_level,
                    "decision": decision,
                    "requires_confirmation": requires_confirmation,
                    "reasons": list(reasons or MUTATING_DEFAULT_REASON_CODES),
                }
            )
        return builder(**kwargs)

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
        entrypoint = {
            "kind": "commander_tool_runtime",
            "resolver": f"BrainRuntime.{mode}",
            "mode": mode,
            "meeting_path": False,
            "tools": names,
            "intent": inferred_intent,
        }
        task_bus = _dict_payload(payload.get("task_bus"))
        if not task_bus:
            plan = self._recommended_plan_for_intent(intent=inferred_intent, tool_names=names, writes_state=writes_state, user_goal=user_goal)
            calls = list(tool_calls or self._tool_trace(names))
            task_bus = self._build_task_bus_for_payload(
                payload=payload,
                user_goal=user_goal,
                intent=inferred_intent,
                operation=mode,
                mode=mode,
                tool_names=names,
                writes_state=writes_state,
                risk_level=risk_level,
                recommended_plan=plan,
                tool_calls=calls,
                artifacts={"workspace": str(self.workspace), "tools": names, "mode": mode},
            )
        payload = build_protocol_response(
            payload=payload,
            entrypoint=entrypoint,
            task_bus=task_bus,
            default_message=str(payload.get("message") or payload.get("reply") or ""),
            default_reply=str(payload.get("message") or payload.get("reply") or ""),
        )
        payload = self._attach_human_readable_receipt(payload, intent=inferred_intent, operation=mode)
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
        risk_level: str = RISK_LEVEL_LOW,
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
        plan = list(recommended_plan or self._recommended_plan_for_intent(intent=intent, tool_names=tool_names, writes_state=writes_state, user_goal=user_goal))
        tool_calls = self._tool_trace(tool_names)
        payload = dict(payload)
        entrypoint = {
            "kind": "commander_builtin_intent",
            "resolver": "BrainRuntime._try_builtin_intent",
            "intent": intent,
            "operation": operation,
            "meeting_path": False,
        }
        task_bus = self._build_task_bus_for_payload(
            payload=payload,
            user_goal=user_goal,
            intent=intent,
            operation=operation,
            mode="builtin_intent",
            tool_names=tool_names,
            writes_state=writes_state,
            risk_level=risk_level,
            recommended_plan=plan,
            tool_calls=tool_calls,
            reasons=reasons,
            artifacts={
                "workspace": str(self.workspace),
                "intent": intent,
                "operation": operation,
            },
        )
        payload = build_protocol_response(
            payload=payload,
            entrypoint=entrypoint,
            task_bus=task_bus,
            default_message=str(payload.get("message") or payload.get("reply") or ""),
            default_reply=str(payload.get("message") or payload.get("reply") or ""),
        )
        payload = self._attach_human_readable_receipt(payload, intent=intent, operation=operation)
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _runtime_state_bullets(runtime_payload: dict[str, Any]) -> list[str]:
        state = str(runtime_payload.get("state") or "unknown")
        current_task = dict(runtime_payload.get("current_task") or {})
        last_task = dict(runtime_payload.get("last_task") or {})
        bullets = [f"运行状态：{state}"]
        if current_task.get("type"):
            bullets.append(f"当前任务：{current_task.get('type')}")
        if last_task.get("type"):
            bullets.append(f"最近完成：{last_task.get('type')} / {last_task.get('status', '')}".rstrip(" /"))
        return bullets

    @staticmethod
    def _training_lab_bullets(training_lab: dict[str, Any]) -> list[str]:
        if not training_lab:
            return []
        return [
            f"训练计划：{int(training_lab.get('plan_count', 0) or 0)}",
            f"训练运行：{int(training_lab.get('run_count', 0) or 0)}",
            f"训练评估：{int(training_lab.get('evaluation_count', 0) or 0)}",
        ]

    @staticmethod
    def _truncate_text(value: Any, *, limit: int = 120) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    @staticmethod
    def _is_internal_runtime_event(event_name: Any) -> bool:
        return str(event_name or "") in {"ask_started", "ask_finished", "task_started", "task_finished"}

    @staticmethod
    def _top_event_distribution(counts: dict[str, Any], *, limit: int = 3) -> str:
        ordered = sorted(
            ((str(name), int(value or 0)) for name, value in dict(counts or {}).items()),
            key=lambda item: (-item[1], item[0]),
        )
        return "，".join(f"{name}×{count}" for name, count in ordered[:limit])

    @staticmethod
    def _event_human_label(event_name: str) -> str:
        mapping = {
            "ask_started": "对话请求开始",
            "ask_finished": "对话请求完成",
            "task_started": "运行任务开始",
            "task_finished": "运行任务完成",
            "training_started": "训练开始",
            "training_finished": "训练完成",
            "routing_started": "模型路由开始",
            "regime_classified": "市场状态识别完成",
            "routing_decided": "模型路由完成",
            "model_switch_applied": "模型切换已执行",
            "model_switch_blocked": "模型切换被阻止",
            "cycle_start": "训练周期开始",
            "cycle_complete": "训练周期完成",
            "cycle_skipped": "训练周期被跳过",
            "agent_status": "Agent 状态更新",
            "agent_progress": "Agent 进度更新",
            "module_log": "模块日志更新",
            "meeting_speech": "会议发言更新",
            "data_download_triggered": "数据下载已触发",
            "runtime_paths_updated": "运行路径已更新",
            "evolution_config_updated": "训练配置已更新",
            "control_plane_updated": "控制面已更新",
            "agent_prompt_updated": "Agent Prompt 已更新",
        }
        return mapping.get(str(event_name or ""), str(event_name or "").replace("_", " "))

    @staticmethod
    def _event_detail_text(row: dict[str, Any]) -> str:
        payload = dict(row.get("payload") or {})
        event_name = str(row.get("event") or "")
        if event_name == "ask_started":
            channel = str(payload.get("channel") or "").strip()
            message_length = payload.get("message_length")
            details = []
            if channel:
                details.append(f"来源 {channel}")
            if message_length not in (None, ""):
                details.append(f"消息长度 {message_length}")
            if details:
                return "已接收对话请求，" + "，".join(details) + "。"
            return "已接收新的对话请求。"
        if event_name == "ask_finished":
            intent = str(payload.get("intent") or "").strip()
            status = str(payload.get("status") or "").strip()
            risk_level = str(payload.get("risk_level") or "").strip()
            details = []
            if intent:
                details.append(f"意图 {intent}")
            if status:
                details.append(f"状态 {status}")
            if risk_level:
                details.append(f"风险 {risk_level}")
            if details:
                return "对话处理结束，" + "，".join(details) + "。"
            return "对话处理结束。"
        if event_name == "task_started":
            task_type = str(payload.get("type") or "").strip()
            source = str(payload.get("source") or "").strip()
            if task_type and source:
                return f"开始执行 {task_type} 任务，来源 {source}。"
            if task_type:
                return f"开始执行 {task_type} 任务。"
        if event_name == "task_finished":
            task_type = str(payload.get("type") or "").strip()
            status = str(payload.get("status") or "").strip()
            if task_type and status:
                return f"{task_type} 任务已结束，状态 {status}。"
            if status:
                return f"运行任务已结束，状态 {status}。"
        if event_name == "routing_decided":
            regime = str(payload.get("regime") or "").strip()
            selected_model = str(payload.get("selected_model") or "").strip()
            current_model = str(payload.get("current_model") or "").strip()
            if regime and selected_model:
                if bool(payload.get("switch_applied")) and current_model and current_model != selected_model:
                    return f"识别为 {regime} 市场，主模型从 {current_model} 切换到 {selected_model}。"
                return f"识别为 {regime} 市场，当前建议主模型为 {selected_model}。"
        if event_name == "model_switch_applied":
            from_model = str(payload.get("from_model") or "").strip()
            to_model = str(payload.get("to_model") or "").strip()
            if from_model and to_model:
                return f"模型已从 {from_model} 切换到 {to_model}。"
        if event_name == "model_switch_blocked":
            hold_reason = str(payload.get("hold_reason") or "").strip()
            if hold_reason:
                return f"系统决定暂不切换模型，原因是：{hold_reason}"
            return "系统评估后决定继续保持当前模型。"
        if event_name == "cycle_start":
            cutoff_date = str(payload.get("cutoff_date") or "").strip()
            requested_mode = str(payload.get("requested_data_mode") or "").strip()
            llm_mode = str(payload.get("llm_mode") or "").strip()
            details = []
            if cutoff_date:
                details.append(f"截断日期 {cutoff_date}")
            if requested_mode:
                details.append(f"数据模式 {requested_mode}")
            if llm_mode:
                details.append(f"LLM 模式 {llm_mode}")
            if details:
                return "本轮训练已启动，" + "，".join(details) + "。"
        if event_name == "cycle_complete":
            cycle_id = payload.get("cycle_id")
            return_pct = payload.get("return_pct")
            if cycle_id is not None and return_pct not in (None, ""):
                return f"训练周期 #{cycle_id} 已完成，收益率约为 {return_pct}。"
            if cycle_id is not None:
                return f"训练周期 #{cycle_id} 已完成。"
        if event_name == "cycle_skipped":
            stage = str(payload.get("stage") or "").strip()
            reason = str(payload.get("reason") or "").strip()
            if stage and reason:
                return f"训练周期在 {stage} 阶段被跳过，原因是：{reason}"
            if reason:
                return f"训练周期被跳过，原因是：{reason}"
        if event_name == "agent_status":
            agent = str(payload.get("agent") or "").strip()
            status = str(payload.get("status") or "").strip()
            stage = str(payload.get("stage") or "").strip()
            progress_pct = payload.get("progress_pct")
            message = BrainRuntime._truncate_text(payload.get("message"), limit=80)
            parts = []
            if agent:
                parts.append(agent)
            if status:
                parts.append(status)
            if stage:
                parts.append(f"阶段 {stage}")
            if progress_pct not in (None, ""):
                parts.append(f"进度 {progress_pct}%")
            if message:
                parts.append(message)
            if parts:
                return "，".join(parts) + "。"
        if event_name == "module_log":
            module = str(payload.get("module") or "").strip()
            title = str(payload.get("title") or "").strip()
            message = BrainRuntime._truncate_text(payload.get("message"), limit=80)
            parts = [part for part in [module, title, message] if part]
            if parts:
                return " / ".join(parts) + "。"
        if event_name == "meeting_speech":
            speaker = str(payload.get("speaker") or "").strip()
            meeting = str(payload.get("meeting") or "").strip()
            speech = BrainRuntime._truncate_text(payload.get("speech"), limit=80)
            prefix = " / ".join(part for part in [meeting, speaker] if part)
            if prefix and speech:
                return f"{prefix}：{speech}"
        if event_name == "data_download_triggered":
            status = str(payload.get("status") or "").strip()
            message = BrainRuntime._truncate_text(payload.get("message"), limit=80)
            if status and message:
                return f"数据同步状态：{status}，{message}"
        if event_name in {"runtime_paths_updated", "evolution_config_updated", "control_plane_updated"}:
            updated = payload.get("updated")
            if isinstance(updated, list) and updated:
                return "更新字段：" + "，".join(str(item) for item in updated[:4])
        return ""

    @staticmethod
    def _event_broadcast_text(row: dict[str, Any]) -> str:
        event_name = str(row.get("event") or "").strip()
        if not event_name:
            return ""
        label = BrainRuntime._event_human_label(event_name)
        detail = BrainRuntime._event_detail_text(row)
        source = str(row.get("source") or "").strip()
        if detail:
            return f"{label}：{detail}"
        if source:
            return f"{label}（来源 {source}）"
        return label

    @staticmethod
    def _event_explanation_bullets(
        event_summary: dict[str, Any],
        *,
        recent_events: list[dict[str, Any]] | None = None,
    ) -> tuple[list[str], dict[str, Any], str]:
        summary = dict(event_summary or {})
        rows = list(recent_events or [])
        preferred_latest: dict[str, Any] = {}
        latest_internal: dict[str, Any] = {}
        for row in reversed(rows):
            event_name = str(row.get("event") or "")
            if not event_name:
                continue
            if not BrainRuntime._is_internal_runtime_event(event_name):
                preferred_latest = dict(row)
                break
            if not latest_internal:
                latest_internal = dict(row)
        latest = dict(preferred_latest or latest_internal or summary.get("latest") or {})
        counts = dict(summary.get("counts") or {})
        external_counts = {
            str(name): int(value or 0)
            for name, value in counts.items()
            if not BrainRuntime._is_internal_runtime_event(name)
        }
        bullets: list[str] = []
        latest_event: dict[str, Any] = {}
        explanation = ""
        if latest:
            event_name = str(latest.get("event") or "unknown")
            source = str(latest.get("source") or "").strip()
            detail_text = BrainRuntime._event_detail_text(latest)
            latest_event = {
                "event": event_name,
                "source": source,
                "ts": str(latest.get("ts") or ""),
                "kind": "internal" if BrainRuntime._is_internal_runtime_event(event_name) else "business",
                "label": BrainRuntime._event_human_label(event_name),
                "detail": detail_text,
                "broadcast_text": BrainRuntime._event_broadcast_text(latest),
            }
            if not BrainRuntime._is_internal_runtime_event(event_name):
                detail = f"最近业务事件：{event_name}（{BrainRuntime._event_human_label(event_name)}）"
                if source:
                    detail += f"（来源 {source}）"
                bullets.append(detail)
                if detail_text:
                    bullets.append("事件细节：" + detail_text)
        if external_counts:
            distribution = BrainRuntime._top_event_distribution(external_counts)
            bullets.append("业务事件分布：" + distribution)
            if preferred_latest:
                explanation = (
                    f"最近一次业务事件是 {latest_event['event']}"
                    + (f"（{latest_event.get('label')}）" if latest_event.get("label") else "")
                    + (f"（来源 {latest_event['source']}）" if latest_event.get("source") else "")
                    + "。"
                )
                if latest_event.get("detail"):
                    explanation += f" {latest_event['detail']}"
                if distribution:
                    explanation += f" 当前窗口内主要业务事件分布为：{distribution}。"
        elif counts:
            distribution = BrainRuntime._top_event_distribution(counts)
            bullets.append("交互事件分布：" + distribution)
            explanation = "当前窗口内主要记录的是交互与调度事件，尚未出现新的业务事件。"
            if distribution:
                explanation += f" 最近的事件分布为：{distribution}。"
        return bullets, latest_event, explanation

    @staticmethod
    def _event_timeline_items(
        recent_events: list[dict[str, Any]] | None,
        *,
        limit: int = 3,
    ) -> list[str]:
        rows = list(recent_events or [])
        business_items: list[str] = []
        internal_items: list[str] = []
        for row in reversed(rows):
            event_name = str(row.get("event") or "").strip()
            if not event_name:
                continue
            broadcast_text = BrainRuntime._event_broadcast_text(row)
            if not broadcast_text:
                continue
            target = internal_items if BrainRuntime._is_internal_runtime_event(event_name) else business_items
            if broadcast_text not in target:
                target.append(broadcast_text)
        selected = business_items or internal_items
        return selected[: max(1, int(limit or 3))]

    @staticmethod
    def _risk_explanations(
        diagnostics: list[Any],
        *,
        feedback: dict[str, Any],
        last_error: Any = "",
    ) -> list[str]:
        mapping = {
            "runtime_state=error": "运行态处于 error，建议优先检查最近失败任务和错误日志。",
            "data_quality_unhealthy": "数据健康异常，继续训练或问股前应先检查数据状态。",
            "last_run_degraded": "最近一次运行出现降级迹象，当前结果建议人工复核。",
        }
        items: list[str] = []
        for code in diagnostics[:3]:
            text = mapping.get(str(code), str(code).replace("_", " "))
            if text and text not in items:
                items.append(text)
        for reason_text in list(feedback.get("reason_texts") or []):
            text = str(reason_text or "").strip()
            if text and text not in items:
                items.append(text)
        error_text = BrainRuntime._truncate_text(last_error, limit=100)
        if error_text:
            items.append(f"最近错误：{error_text}")
        return items

    @staticmethod
    def _action_items(
        next_action: dict[str, Any],
        *,
        diagnostics: list[Any],
        latest_event: dict[str, Any] | None = None,
        status: str = "ok",
    ) -> list[str]:
        items: list[str] = []
        label = str(next_action.get("label") or "").strip()
        description = str(next_action.get("description") or "").strip()
        if label:
            items.append(f"{label}：{description}" if description else label)
        if bool(next_action.get("requires_confirmation")):
            items.append("如需继续执行，请直接用自然语言明确回复“确认执行”或补充确认参数。")
        diagnostic_codes = {str(item) for item in diagnostics}
        if "runtime_state=error" in diagnostic_codes:
            items.append("先恢复运行态，再继续训练、配置修改或问股请求。")
        if "data_quality_unhealthy" in diagnostic_codes:
            items.append("先执行数据状态检查或刷新，确认数据健康后再继续下游任务。")
        latest_event_name = str((latest_event or {}).get("event") or "")
        if latest_event_name == "training_finished":
            items.append("查看最近训练结果、排行榜和生成工件，确认是否需要继续迭代。")
        elif latest_event_name == "training_started":
            items.append("继续关注事件流和运行状态，等待训练完成后再查看结果。")
        if status == "ok" and not items:
            items.append("可以继续发起更具体的自然语言任务，例如训练、问股或配置诊断。")
        deduped: list[str] = []
        for item in items:
            if item and item not in deduped:
                deduped.append(item)
        return deduped

    @staticmethod
    def _risk_level_text(risk_level: str) -> str:
        mapping = {
            RISK_LEVEL_LOW: "低风险，可直接继续读取或查看结果。",
            RISK_LEVEL_MEDIUM: "中风险，建议先核对关键参数、数据状态或最近事件。",
            RISK_LEVEL_HIGH: "高风险，建议先确认操作范围与影响，再继续执行。",
        }
        return mapping.get(str(risk_level or ""), "")

    @staticmethod
    def _operation_nature_text(gate: dict[str, Any]) -> str:
        writes_state = bool(gate.get("writes_state"))
        if writes_state:
            return "本次属于写操作，可能会改动系统状态、配置或运行工件。"
        return "本次属于只读分析，不会改动系统状态。"

    @staticmethod
    def _confirmation_text(gate: dict[str, Any], *, status: str) -> str:
        confirmation = dict(gate.get("confirmation") or {})
        state = str(confirmation.get("state") or "")
        writes_state = bool(gate.get("writes_state"))
        requires_confirmation = bool(gate.get("requires_confirmation"))
        if requires_confirmation or state == "pending_confirmation" or status == "confirmation_required":
            return "当前仍需人工确认，系统不会直接执行写入动作。"
        if writes_state:
            return "当前写操作已确认或无需额外确认，可以按流程继续执行。"
        return "当前无需人工确认，可以直接继续查看或追问。"

    @staticmethod
    def _compose_human_readable_receipt(
        *,
        title: str,
        summary: str,
        operation: str,
        facts: list[str] | None = None,
        risks: list[str] | None = None,
        suggested_actions: list[str] | None = None,
        recommended_next_step: str = "",
        risk_level: str = "",
        latest_event: dict[str, Any] | None = None,
        event_explanation: str = "",
        event_timeline: list[str] | None = None,
        operation_nature: str = "",
        risk_summary: str = "",
        confirmation_summary: str = "",
    ) -> dict[str, Any]:
        fact_items = [str(item) for item in list(facts or []) if str(item or "").strip()]
        risk_items = [str(item) for item in list(risks or []) if str(item or "").strip()]
        action_items = [str(item) for item in list(suggested_actions or []) if str(item or "").strip()]
        timeline_items = [str(item) for item in list(event_timeline or []) if str(item or "").strip()]
        bullets = list(fact_items)
        posture_items = [str(item) for item in [operation_nature, risk_summary, confirmation_summary] if str(item or "").strip()]
        bullets.extend(posture_items)
        if event_explanation:
            bullets.append(f"事件解释：{event_explanation}")
        bullets.extend(f"最近事件：{item}" for item in timeline_items[:2])
        bullets.extend(f"关注项：{item}" for item in risk_items[:2])
        bullets.extend(f"建议动作：{item}" for item in action_items[:2])
        sections: list[dict[str, Any]] = [{"label": "结论", "text": summary}]
        if posture_items:
            sections.append({"label": "执行性质", "items": posture_items})
        if fact_items:
            sections.append({"label": "现状", "items": fact_items})
        if event_explanation:
            sections.append({"label": "事件解释", "text": event_explanation})
        if timeline_items:
            sections.append({"label": "最近事件", "items": timeline_items})
        if risk_items:
            sections.append({"label": "风险提示", "items": risk_items})
        if action_items:
            sections.append({"label": "建议动作", "items": action_items})
        receipt_lines = [f"结论：{summary}"]
        if operation_nature:
            receipt_lines.append(f"执行性质：{operation_nature}")
        if risk_summary:
            receipt_lines.append(f"风险等级：{risk_summary}")
        if confirmation_summary:
            receipt_lines.append(f"确认要求：{confirmation_summary}")
        if fact_items:
            receipt_lines.append("现状：" + "；".join(fact_items[:4]))
        if event_explanation:
            receipt_lines.append("事件解释：" + event_explanation)
        if timeline_items:
            receipt_lines.append("最近事件：" + "；".join(timeline_items[:2]))
        if risk_items:
            receipt_lines.append("风险提示：" + "；".join(risk_items[:2]))
        if action_items:
            receipt_lines.append("建议动作：" + "；".join(action_items[:2]))
        return {
            "title": title,
            "summary": summary,
            "bullets": bullets,
            "facts": fact_items,
            "risks": risk_items,
            "suggested_actions": action_items,
            "event_explanation": event_explanation,
            "event_timeline": timeline_items,
            "sections": sections,
            "receipt_text": "\n".join(receipt_lines),
            "recommended_next_step": recommended_next_step,
            "risk_level": risk_level,
            "latest_event": dict(latest_event or {}),
            "operation_nature": operation_nature,
            "risk_summary": risk_summary,
            "confirmation_summary": confirmation_summary,
            "operation": operation,
        }

    def _build_human_readable_receipt(
        self,
        payload: dict[str, Any],
        *,
        intent: str,
        operation: str,
    ) -> dict[str, Any]:
        return BrainHumanReadablePresenter.build_human_readable_receipt(
            payload,
            intent=intent,
            operation=operation,
        )

    def _attach_human_readable_receipt(
        self,
        payload: dict[str, Any],
        *,
        intent: str,
        operation: str,
    ) -> dict[str, Any]:
        return BrainHumanReadablePresenter.attach_human_readable_receipt(
            payload,
            intent=intent,
            operation=operation,
        )


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
        event_explanation_terms = [
            "发生了什么",
            "最近发生了什么",
            "解释最近发生了什么",
            "最近怎么了",
            "what happened",
            "recent activity",
        ]
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
        asks_diagnostics = (
            has_any(text, diagnostics_terms)
            or has_any(text, event_terms)
            or has_any(low, event_explanation_terms)
        )
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
                "entrypoint": build_bounded_entrypoint(
                    kind="commander_builtin_workflow",
                    resolver="BrainRuntime._try_builtin_intent",
                    intent="runtime_status_and_training",
                    operation="status_and_recent_training",
                    meeting_path=False,
                    agent_kind="bounded_runtime_agent",
                ),
                "orchestration": build_bounded_orchestration(
                    mode="builtin_bounded_readonly_workflow",
                    available_tools=["invest_quick_status", "invest_training_lab_summary"],
                    allowed_tools=["invest_quick_status", "invest_training_lab_summary"],
                    workflow=["runtime_scope_resolve", "quick_status_read", "training_lab_read", "finalize"],
                    phase_stats={"section_count": 2},
                    policy=build_bounded_policy(
                        source="commander_builtin_intent",
                        agent_kind="bounded_runtime_agent",
                        fixed_boundary=True,
                        fixed_workflow=True,
                        writes_state=False,
                        tool_catalog_scope="runtime_training_combo",
                    ),
                ),
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
                "entrypoint": build_bounded_entrypoint(
                    kind="commander_builtin_workflow",
                    resolver="BrainRuntime._try_builtin_intent",
                    intent="config_overview",
                    operation="config_overview",
                    meeting_path=False,
                    agent_kind="bounded_config_agent",
                ),
                "orchestration": build_bounded_orchestration(
                    mode="builtin_bounded_readonly_workflow",
                    available_tools=["invest_control_plane_get", "invest_evolution_config_get"],
                    allowed_tools=["invest_control_plane_get", "invest_evolution_config_get"],
                    workflow=["config_scope_resolve", "control_plane_read", "evolution_config_read", "finalize"],
                    phase_stats={"section_count": 2},
                    policy=build_bounded_policy(
                        source="commander_builtin_intent",
                        agent_kind="bounded_config_agent",
                        fixed_boundary=True,
                        fixed_workflow=True,
                        writes_state=False,
                        tool_catalog_scope="config_overview_combo",
                    ),
                ),
            }
            return self._wrap_builtin_payload(payload, user_goal=text, intent="config_overview", operation="config_overview", tool_names=["invest_control_plane_get", "invest_evolution_config_get"])

        if asks_training_exec:
            rounds_match = re.search(r"(\d+)\s*(轮|次)", text)
            rounds = int(rounds_match.group(1)) if rounds_match else 1
            mock = any(token in low for token in ["mock", "演示", "测试", "dry-run", "quick"])
            confirm = any(token in low for token in ["确认", "confirm"])
            payload = await run_json("invest_train", {"rounds": rounds, "mock": mock, "confirm": confirm})
            risk_level = RISK_LEVEL_LOW if mock else RISK_LEVEL_HIGH if rounds > 1 else RISK_LEVEL_MEDIUM
            return self._wrap_builtin_payload(
                payload,
                user_goal=text,
                intent="training_execution",
                operation="invest_train",
                tool_names=["invest_train"],
                writes_state=True,
                risk_level=risk_level,
                reasons=list(TRAINING_DEFAULT_REASON_CODES),
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
