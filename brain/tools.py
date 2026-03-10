"""Commander tool implementations based on local brain runtime abstractions."""

from __future__ import annotations

import json
from typing import Any

from .runtime import BrainTool


class InvestStatusTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_status"

    @property
    def description(self) -> str:
        return "Deprecated compatibility alias for `invest_quick_status` (fast snapshot path). Prefer `invest_quick_status`."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        return json.dumps(self.runtime.status(detail="fast"), ensure_ascii=False, indent=2)


class InvestQuickStatusTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_quick_status"

    @property
    def description(self) -> str:
        return "Get fast commander status using snapshot/cached data paths."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        return json.dumps(self.runtime.status(detail="fast"), ensure_ascii=False, indent=2)


class InvestDeepStatusTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_deep_status"

    @property
    def description(self) -> str:
        return "Get deep commander status with fresh data health recomputation."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        return json.dumps(self.runtime.status(detail="slow"), ensure_ascii=False, indent=2)


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
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        rounds = int(kwargs.get("rounds", 1))
        mock = bool(kwargs.get("mock", False))
        out = await self.runtime.train_once(rounds=rounds, mock=mock)
        return json.dumps(out, ensure_ascii=False, indent=2)


class InvestQuickTestTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_quick_test"

    @property
    def description(self) -> str:
        return "Run one mock training cycle as a health check."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        out = await self.runtime.train_once(rounds=1, mock=True)
        return json.dumps(out, ensure_ascii=False, indent=2)


class InvestListStrategiesTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_list_strategies"

    @property
    def description(self) -> str:
        return "List loaded strategy gene files (md/json/py)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "only_enabled": {"type": "boolean", "default": False}
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        only_enabled = bool(kwargs.get("only_enabled", False))
        genes = self.runtime.strategy_registry.list_genes(only_enabled=only_enabled)
        payload = {"count": len(genes), "items": [g.to_dict() for g in genes]}
        return json.dumps(payload, ensure_ascii=False, indent=2)


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
                "model_scope": {"type": "object", "default": {}},
                "optimization": {"type": "object", "default": {}},
                "llm": {"type": "object", "default": {}},
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        payload = self.runtime.create_training_plan(
            rounds=int(kwargs.get("rounds", 1)),
            mock=bool(kwargs.get("mock", False)),
            goal=str(kwargs.get("goal", "")),
            notes=str(kwargs.get("notes", "")),
            tags=list(kwargs.get("tags", []) or []),
            detail_mode=str(kwargs.get("detail_mode", "fast")),
            protocol=dict(kwargs.get("protocol") or {}),
            dataset=dict(kwargs.get("dataset") or {}),
            model_scope=dict(kwargs.get("model_scope") or {}),
            optimization=dict(kwargs.get("optimization") or {}),
            llm=dict(kwargs.get("llm") or {}),
        )
        return json.dumps(payload, ensure_ascii=False, indent=2)


class InvestTrainingPlanListTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_training_plan_list"

    @property
    def description(self) -> str:
        return "List recent training plans in the strategy lab."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        payload = self.runtime.list_training_plans(limit=int(kwargs.get("limit", 20)))
        return json.dumps(payload, ensure_ascii=False, indent=2)


class InvestTrainingPlanExecuteTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_training_plan_execute"

    @property
    def description(self) -> str:
        return "Execute a persisted training plan and generate run/evaluation artifacts."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
            },
            "required": ["plan_id"],
        }

    async def execute(self, **kwargs: Any) -> str:
        payload = await self.runtime.execute_training_plan(str(kwargs["plan_id"]))
        return json.dumps(payload, ensure_ascii=False, indent=2)


class InvestReloadStrategiesTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_reload_strategies"

    @property
    def description(self) -> str:
        return "Reload strategy genes from disk and refresh commander DNA prompt."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        payload = self.runtime.reload_strategies()
        return json.dumps(payload, ensure_ascii=False, indent=2)


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
        job = self.runtime.cron.add_job(
            name=kwargs["name"],
            message=kwargs["message"],
            every_sec=int(kwargs["every_sec"]),
            deliver=bool(kwargs.get("deliver", False)),
            channel=str(kwargs.get("channel", "cli")),
            to=str(kwargs.get("to", "commander")),
        )
        self.runtime._persist_state()
        return json.dumps({"status": "ok", "job": job.to_dict()}, ensure_ascii=False, indent=2)


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
        rows = [j.to_dict() for j in self.runtime.cron.list_jobs()]
        return json.dumps({"count": len(rows), "items": rows}, ensure_ascii=False, indent=2)


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
        return {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        }

    async def execute(self, **kwargs: Any) -> str:
        ok = self.runtime.cron.remove_job(str(kwargs["job_id"]))
        self.runtime._persist_state()
        return json.dumps({"status": "ok" if ok else "not_found", "job_id": kwargs["job_id"]}, ensure_ascii=False, indent=2)


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
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        query = str(kwargs.get("query", ""))
        limit = int(kwargs.get("limit", 20))
        rows = self.runtime.memory.search(query=query, limit=limit)
        return json.dumps({"count": len(rows), "items": rows}, ensure_ascii=False, indent=2)


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
        payload = self.runtime.reload_plugins()
        return json.dumps(payload, ensure_ascii=False, indent=2)


def build_commander_tools(runtime: Any) -> list[BrainTool]:
    return [
        InvestQuickStatusTool(runtime),
        InvestDeepStatusTool(runtime),
        InvestStatusTool(runtime),
        InvestTrainTool(runtime),
        InvestQuickTestTool(runtime),
        InvestTrainingPlanCreateTool(runtime),
        InvestTrainingPlanListTool(runtime),
        InvestTrainingPlanExecuteTool(runtime),
        InvestListStrategiesTool(runtime),
        InvestReloadStrategiesTool(runtime),
        InvestCronAddTool(runtime),
        InvestCronListTool(runtime),
        InvestCronRemoveTool(runtime),
        InvestMemorySearchTool(runtime),
        InvestPluginReloadTool(runtime),
    ]
