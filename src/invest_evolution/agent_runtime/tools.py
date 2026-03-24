"""Agent runtime tool catalog and metadata."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger(__name__)


INVEST_QUICK_STATUS_TOOL_NAME = "invest_quick_status"
INVEST_DEEP_STATUS_TOOL_NAME = "invest_deep_status"
RUNTIME_OBSERVABILITY_TOOL_NAMES = frozenset(
    {
        INVEST_QUICK_STATUS_TOOL_NAME,
        INVEST_DEEP_STATUS_TOOL_NAME,
        "invest_events_tail",
        "invest_events_summary",
        "invest_runtime_diagnostics",
        "invest_training_lab_summary",
    }
)


class ToolArgumentParseError(ValueError):
    """Raised when tool-call arguments cannot be parsed into a JSON object."""


def parse_tool_args(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ToolArgumentParseError(str(exc)) from exc
        if not isinstance(parsed, dict):
            raise ToolArgumentParseError("tool arguments must decode to a JSON object")
        return dict(parsed)
    raise ToolArgumentParseError("tool arguments must be a JSON object or JSON string")


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
            logger.exception("Tool execution failed: %s", name)
            return f"Error executing {name}: {type(exc).__name__}: {exc}" + _hint

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


class InvestQuickStatusTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return INVEST_QUICK_STATUS_TOOL_NAME

    @property
    def description(self) -> str:
        return "Get fast commander status using snapshot/cached data paths."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.status(detail="fast"))


class InvestDeepStatusTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return INVEST_DEEP_STATUS_TOOL_NAME

    @property
    def description(self) -> str:
        return "Get deep commander status with fresh data health recomputation."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.status(detail="slow"))


class InvestTrainTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_train"

    @property
    def description(self) -> str:
        return "Run investment training cycles in-process (no subprocess)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "rounds": {"type": "integer", "minimum": 1, "maximum": 200, "default": 1},
                "mock": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        rounds = int(kwargs.get("rounds", 1))
        mock = bool(kwargs.get("mock", False))
        confirm = bool(kwargs.get("confirm", False))
        if rounds > 1 and not mock and not confirm:
            return _json(self.runtime.build_training_confirmation_required(rounds=rounds, mock=mock))
        out = await self.runtime.train_once(rounds=rounds, mock=mock)
        return _json(out)


class InvestQuickTestTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_quick_test"

    @property
    def description(self) -> str:
        return "Run one smoke/demo training cycle (mock data + dry-run LLM) as a health check."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        out = await self.runtime.train_once(rounds=1, mock=True)
        return _json(out)


class InvestListPlaybooksTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_list_playbooks"

    @property
    def description(self) -> str:
        return "List loaded commander playbook files (md/json/py)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"only_enabled": {"type": "boolean", "default": False}}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        only_enabled = bool(kwargs.get("only_enabled", False))
        playbooks = self.runtime.playbook_registry.list_playbooks(only_enabled=only_enabled)
        return _json({"count": len(playbooks), "items": [playbook.to_dict() for playbook in playbooks]})


class InvestTrainingPlanCreateTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_training_plan_create"

    @property
    def description(self) -> str:
        return "Create a training plan object for the strategy lab."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "rounds": {"type": "integer", "minimum": 1, "maximum": 200, "default": 1},
                "mock": {"type": "boolean", "default": False},
                "goal": {"type": "string", "default": ""},
                "notes": {"type": "string", "default": ""},
                "tags": {"type": "array", "items": {"type": "string"}},
                "detail_mode": {"type": "string", "enum": ["fast", "slow"], "default": "fast"},
                "protocol": {"type": "object", "default": {}},
                "dataset": {"type": "object", "default": {}},
                "manager_scope": {"type": "object", "default": {}},
                "optimization": {"type": "object", "default": {}},
                "llm": {"type": "object", "default": {}},
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        payload = self.runtime.create_training_plan(
            rounds=int(kwargs.get("rounds", 1)),
            mock=bool(kwargs.get("mock", False)),
            goal=str(kwargs.get("goal", "") or ""),
            notes=str(kwargs.get("notes", "") or ""),
            tags=list(kwargs.get("tags") or []),
            detail_mode=str(kwargs.get("detail_mode", "fast") or "fast"),
            protocol=dict(kwargs.get("protocol") or {}),
            dataset=dict(kwargs.get("dataset") or {}),
            manager_scope=dict(kwargs.get("manager_scope") or {}),
            optimization=dict(kwargs.get("optimization") or {}),
            llm=dict(kwargs.get("llm") or {}),
        )
        return _json(payload)


class InvestTrainingPlanListTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_training_plan_list"

    @property
    def description(self) -> str:
        return "List training plans from the strategy lab."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        payload = self.runtime.list_training_plans(limit=int(kwargs.get("limit", 20)))
        return _json(payload)


class InvestTrainingPlanExecuteTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_training_plan_execute"

    @property
    def description(self) -> str:
        return "Execute a training plan by id."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"plan_id": {"type": "string"}}, "required": ["plan_id"]}

    async def execute(self, **kwargs: Any) -> str:
        payload = await self.runtime.execute_training_plan(str(kwargs["plan_id"]))
        return _json(payload)


class InvestTrainingRunsListTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_training_runs_list"

    @property
    def description(self) -> str:
        return "List training run artifacts."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.list_training_runs(limit=int(kwargs.get("limit", 20))))


class InvestTrainingEvaluationsListTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_training_evaluations_list"

    @property
    def description(self) -> str:
        return "List training evaluation artifacts."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.list_training_evaluations(limit=int(kwargs.get("limit", 20))))


class InvestReloadPlaybooksTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_reload_playbooks"

    @property
    def description(self) -> str:
        return "Reload commander playbooks from disk and refresh commander prompts."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.reload_playbooks())


class InvestManagersTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_managers"
    @property
    def description(self) -> str:
        return "Get the manager roster, governance state, and execution defaults."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_managers())


class InvestLeaderboardTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_leaderboard"
    @property
    def description(self) -> str:
        return "Get current leaderboard snapshot."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_leaderboard())


class InvestAllocatorTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_allocator"
    @property
    def description(self) -> str:
        return "Preview allocator plan for a regime using leaderboard."
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "regime": {"type": "string", "default": "oscillation"},
                "top_n": {"type": "integer", "minimum": 1, "maximum": 4, "default": 3},
                "as_of_date": {"type": "string", "default": ""},
            },
            "required": [],
        }
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_allocator_preview(
            regime=str(kwargs.get("regime", "oscillation") or "oscillation"),
            top_n=int(kwargs.get("top_n", 3)),
            as_of_date=str(kwargs.get("as_of_date", "") or "") or None,
        ))


class InvestGovernancePreviewTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_governance_preview"
    @property
    def description(self) -> str:
        return "Preview governance decision for a cutoff date."
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "cutoff_date": {"type": "string", "default": ""},
                "stock_count": {"type": "integer", "minimum": 1},
                "min_history_days": {"type": "integer", "minimum": 1},
                "allowed_manager_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": [],
        }
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_governance_preview(
            cutoff_date=str(kwargs.get("cutoff_date", "") or "") or None,
            stock_count=int(kwargs["stock_count"]) if kwargs.get("stock_count") not in (None, "") else None,
            min_history_days=int(kwargs["min_history_days"]) if kwargs.get("min_history_days") not in (None, "") else None,
            allowed_manager_ids=list(kwargs.get("allowed_manager_ids") or []) or None,
        ))


class InvestCronAddTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_cron_add"
    @property
    def description(self) -> str:
        return "Add interval cron job to trigger commander tasks."
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "message": {"type": "string"},
                "every_sec": {"type": "integer", "minimum": 5, "maximum": 86400},
                "deliver": {"type": "boolean", "default": False},
                "channel": {"type": "string", "default": "cli"},
                "to": {"type": "string", "default": "commander"},
            },
            "required": ["name", "message", "every_sec"],
        }
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.add_cron_job(
            name=str(kwargs["name"]),
            message=str(kwargs["message"]),
            every_sec=int(kwargs["every_sec"]),
            deliver=bool(kwargs.get("deliver", False)),
            channel=str(kwargs.get("channel", "cli")),
            to=str(kwargs.get("to", "commander")),
        ))


class InvestCronListTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_cron_list"
    @property
    def description(self) -> str:
        return "List cron jobs."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.list_cron_jobs())


class InvestCronRemoveTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_cron_remove"
    @property
    def description(self) -> str:
        return "Remove cron job by id."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.remove_cron_job(str(kwargs["job_id"])))


class InvestMemorySearchTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_memory_search"
    @property
    def description(self) -> str:
        return "Search long-term memory records."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.list_memory(query=str(kwargs.get("query", "") or ""), limit=int(kwargs.get("limit", 20))))


class InvestMemoryListTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_memory_list"
    @property
    def description(self) -> str:
        return "List memory records with optional query."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"query": {"type": "string", "default": ""}, "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.list_memory(query=str(kwargs.get("query", "") or ""), limit=int(kwargs.get("limit", 20))))


class InvestMemoryGetTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_memory_get"
    @property
    def description(self) -> str:
        return "Get a memory record with expanded details by id."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"record_id": {"type": "string"}}, "required": ["record_id"]}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_memory_detail(str(kwargs["record_id"])))


class InvestConfigGetTool(BrainTool):
    tool_name = ""
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return self.tool_name
    @property
    def description(self) -> str:
        return "Get configuration payload."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}


class InvestConfigUpdateTool(BrainTool):
    tool_name = ""
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return self.tool_name
    @property
    def description(self) -> str:
        return "Update configuration payload."
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "patch": {"type": "object", "default": {}},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["patch"],
        }


class InvestControlPlaneGetTool(InvestConfigGetTool):
    tool_name = "invest_control_plane_get"
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_control_plane())


class InvestControlPlaneUpdateTool(InvestConfigUpdateTool):
    tool_name = "invest_control_plane_update"
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.update_control_plane(dict(kwargs.get("patch") or {}), confirm=bool(kwargs.get("confirm", False))))


class InvestRuntimePathsGetTool(InvestConfigGetTool):
    tool_name = "invest_runtime_paths_get"
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_runtime_paths())


class InvestRuntimePathsUpdateTool(InvestConfigUpdateTool):
    tool_name = "invest_runtime_paths_update"
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.update_runtime_paths(dict(kwargs.get("patch") or {}), confirm=bool(kwargs.get("confirm", False))))


class InvestEvolutionConfigGetTool(InvestConfigGetTool):
    tool_name = "invest_evolution_config_get"
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_evolution_config())


class InvestEvolutionConfigUpdateTool(InvestConfigUpdateTool):
    tool_name = "invest_evolution_config_update"
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.update_evolution_config(dict(kwargs.get("patch") or {}), confirm=bool(kwargs.get("confirm", False))))


class InvestAgentPromptsListTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_agent_prompts_list"
    @property
    def description(self) -> str:
        return "List editable agent prompts."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.list_agent_prompts())


class InvestAgentPromptsUpdateTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_agent_prompts_update"
    @property
    def description(self) -> str:
        return "Update one agent system prompt."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"name": {"type": "string"}, "system_prompt": {"type": "string"}}, "required": ["name", "system_prompt"]}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.update_agent_prompt(agent_name=str(kwargs["name"]), system_prompt=str(kwargs["system_prompt"])))


class InvestDataStatusTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_data_status"
    @property
    def description(self) -> str:
        return "Get data health and storage status."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"refresh": {"type": "boolean", "default": False}}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_data_status(refresh=bool(kwargs.get("refresh", False))))


class _CodesDateTool(BrainTool):
    tool_name = ""
    tool_desc = ""
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return self.tool_name
    @property
    def description(self) -> str:
        return self.tool_desc
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "codes": {"type": "array", "items": {"type": "string"}},
                "start_date": {"type": "string", "default": ""},
                "end_date": {"type": "string", "default": ""},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 200},
            },
            "required": [],
        }


class InvestDataCapitalFlowTool(_CodesDateTool):
    tool_name = "invest_data_capital_flow"
    tool_desc = "Query capital flow records from the local database."
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_capital_flow(codes=list(kwargs.get("codes") or []) or None, start_date=str(kwargs.get("start_date", "") or "") or None, end_date=str(kwargs.get("end_date", "") or "") or None, limit=int(kwargs.get("limit", 200))))


class InvestDataDragonTigerTool(_CodesDateTool):
    tool_name = "invest_data_dragon_tiger"
    tool_desc = "Query dragon-tiger event records from the local database."
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_dragon_tiger(codes=list(kwargs.get("codes") or []) or None, start_date=str(kwargs.get("start_date", "") or "") or None, end_date=str(kwargs.get("end_date", "") or "") or None, limit=int(kwargs.get("limit", 200))))


class InvestDataIntraday60mTool(_CodesDateTool):
    tool_name = "invest_data_intraday_60m"
    tool_desc = "Query 60-minute bars from the local database."
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_intraday_60m(codes=list(kwargs.get("codes") or []) or None, start_date=str(kwargs.get("start_date", "") or "") or None, end_date=str(kwargs.get("end_date", "") or "") or None, limit=int(kwargs.get("limit", 500))))


class InvestDataDownloadTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_data_download"
    @property
    def description(self) -> str:
        return "Trigger or inspect background data synchronization."
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "trigger"], "default": "status"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": [],
        }
    async def execute(self, **kwargs: Any) -> str:
        action = str(kwargs.get("action", "status") or "status").strip().lower()
        if action == "trigger":
            return _json(self.runtime.trigger_data_download(confirm=bool(kwargs.get("confirm", False))))
        return _json(self.runtime.get_data_download_status())


class InvestEventsTailTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_events_tail"
    @property
    def description(self) -> str:
        return "Get recent commander/runtime events."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_events_tail(limit=int(kwargs.get("limit", 50))))


class InvestEventsSummaryTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_events_summary"
    @property
    def description(self) -> str:
        return "Summarize recent commander/runtime events."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_events_summary(limit=int(kwargs.get("limit", 100))))


class InvestRuntimeDiagnosticsTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_runtime_diagnostics"
    @property
    def description(self) -> str:
        return "Build a unified runtime diagnostics report."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"event_limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}, "memory_limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_runtime_diagnostics(event_limit=int(kwargs.get("event_limit", 50)), memory_limit=int(kwargs.get("memory_limit", 20))))


class InvestTrainingLabSummaryTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_training_lab_summary"
    @property
    def description(self) -> str:
        return "Get summarized counts and latest artifacts for the training lab."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5}}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_training_lab_summary(limit=int(kwargs.get("limit", 5))))


class InvestPluginReloadTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_plugins_reload"
    @property
    def description(self) -> str:
        return "Reload plugin tools from plugins directory."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.reload_plugins())


class InvestResearchCasesTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_research_cases"
    @property
    def description(self) -> str:
        return "List research cases captured by the unified research engine."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}, "policy_id": {"type": "string", "default": ""}, "symbol": {"type": "string", "default": ""}, "as_of_date": {"type": "string", "default": ""}, "horizon": {"type": "string", "default": ""}}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.list_research_cases(limit=int(kwargs.get("limit", 20)), policy_id=str(kwargs.get("policy_id", "") or ""), symbol=str(kwargs.get("symbol", "") or ""), as_of_date=str(kwargs.get("as_of_date", "") or ""), horizon=str(kwargs.get("horizon", "") or "")))


class InvestResearchAttributionsTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_research_attributions"
    @property
    def description(self) -> str:
        return "List scored research attributions for replay and audit."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.list_research_attributions(limit=int(kwargs.get("limit", 20))))


class InvestResearchCalibrationTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_research_calibration"
    @property
    def description(self) -> str:
        return "Read calibration summary aggregated from research cases and attributions."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"policy_id": {"type": "string", "default": ""}}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.get_research_calibration(policy_id=str(kwargs.get("policy_id", "") or "")))


class InvestStockStrategiesTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_stock_strategies"
    @property
    def description(self) -> str:
        return "List available stock-analysis YAML strategies."
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.list_stock_strategies())


class InvestAskStockTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime
    @property
    def name(self) -> str:
        return "invest_ask_stock"
    @property
    def description(self) -> str:
        return "Analyze a stock using local data and a YAML stock-analysis strategy."
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {"type": "string", "default": ""},
                "query": {"type": "string", "description": "stock code or stock name"},
                "strategy": {"type": "string", "default": "chan_theory"},
                "days": {"type": "integer", "minimum": 30, "maximum": 500, "default": 60},
                "as_of_date": {"type": "string", "default": "", "description": "point-in-time cutoff date in YYYYMMDD or YYYY-MM-DD"},
            },
            "required": ["query"],
        }
    async def execute(self, **kwargs: Any) -> str:
        return _json(self.runtime.ask_stock(
            question=str(kwargs.get("question", "") or kwargs.get("query", "") or ""),
            query=str(kwargs["query"]),
            strategy=str(kwargs.get("strategy", "chan_theory") or "chan_theory"),
            days=int(kwargs.get("days", 60)),
            as_of_date=str(kwargs.get("as_of_date", "") or ""),
        ))


def build_commander_tools(runtime: Any) -> list[BrainTool]:
    return [
        InvestQuickStatusTool(runtime),
        InvestDeepStatusTool(runtime),
        InvestEventsSummaryTool(runtime),
        InvestEventsTailTool(runtime),
        InvestRuntimeDiagnosticsTool(runtime),
        InvestTrainTool(runtime),
        InvestQuickTestTool(runtime),
        InvestTrainingPlanCreateTool(runtime),
        InvestTrainingPlanListTool(runtime),
        InvestTrainingPlanExecuteTool(runtime),
        InvestTrainingRunsListTool(runtime),
        InvestTrainingEvaluationsListTool(runtime),
        InvestTrainingLabSummaryTool(runtime),
        InvestManagersTool(runtime),
        InvestLeaderboardTool(runtime),
        InvestAllocatorTool(runtime),
        InvestGovernancePreviewTool(runtime),
        InvestListPlaybooksTool(runtime),
        InvestReloadPlaybooksTool(runtime),
        InvestControlPlaneGetTool(runtime),
        InvestControlPlaneUpdateTool(runtime),
        InvestRuntimePathsGetTool(runtime),
        InvestRuntimePathsUpdateTool(runtime),
        InvestEvolutionConfigGetTool(runtime),
        InvestEvolutionConfigUpdateTool(runtime),
        InvestAgentPromptsListTool(runtime),
        InvestAgentPromptsUpdateTool(runtime),
        InvestDataStatusTool(runtime),
        InvestDataCapitalFlowTool(runtime),
        InvestDataDragonTigerTool(runtime),
        InvestDataIntraday60mTool(runtime),
        InvestDataDownloadTool(runtime),
        InvestCronAddTool(runtime),
        InvestCronListTool(runtime),
        InvestCronRemoveTool(runtime),
        InvestMemorySearchTool(runtime),
        InvestMemoryListTool(runtime),
        InvestMemoryGetTool(runtime),
        InvestResearchCasesTool(runtime),
        InvestResearchAttributionsTool(runtime),
        InvestResearchCalibrationTool(runtime),
        InvestStockStrategiesTool(runtime),
        InvestAskStockTool(runtime),
        InvestPluginReloadTool(runtime),
    ]
