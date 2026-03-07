"""Commander tool implementations based on local brain runtime abstractions."""

from __future__ import annotations

import json
from typing import Any

from brain_runtime import BrainTool


class InvestStatusTool(BrainTool):
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "invest_status"

    @property
    def description(self) -> str:
        return "Get unified commander status (brain/body/strategies)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        return json.dumps(self.runtime.status(), ensure_ascii=False, indent=2)


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
        InvestStatusTool(runtime),
        InvestTrainTool(runtime),
        InvestQuickTestTool(runtime),
        InvestListStrategiesTool(runtime),
        InvestReloadStrategiesTool(runtime),
        InvestCronAddTool(runtime),
        InvestCronListTool(runtime),
        InvestCronRemoveTool(runtime),
        InvestMemorySearchTool(runtime),
        InvestPluginReloadTool(runtime),
    ]
