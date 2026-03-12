from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


TASK_BUS_SCHEMA_VERSION = "task_bus.v2"
PLAN_SCHEMA_VERSION = "task_plan.v2"
COVERAGE_SCHEMA_VERSION = "task_coverage.v2"
ARTIFACT_TAXONOMY_SCHEMA_VERSION = "artifact_taxonomy.v2"


@dataclass
class TaskPlan:
    intent: str
    operation: str
    mode: str
    user_goal: str
    available_tools: list[str] = field(default_factory=list)
    recommended_plan: list[dict[str, Any]] = field(default_factory=list)
    plan_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "operation": self.operation,
            "mode": self.mode,
            "user_goal": self.user_goal,
            "available_tools": list(self.available_tools),
            "recommended_plan": list(self.recommended_plan),
            "plan_summary": dict(self.plan_summary),
        }


@dataclass
class TaskGate:
    decision: str
    risk_level: str
    writes_state: bool
    requires_confirmation: bool
    reasons: list[str] = field(default_factory=list)
    confirmation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "risk_level": self.risk_level,
            "writes_state": self.writes_state,
            "requires_confirmation": self.requires_confirmation,
            "reasons": list(self.reasons),
            "confirmation": dict(self.confirmation),
        }


@dataclass
class TaskAudit:
    status: str
    started_at: str
    completed_at: str
    tool_count: int
    used_tools: list[str] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    artifact_taxonomy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "tool_count": self.tool_count,
            "used_tools": list(self.used_tools),
            "artifacts": dict(self.artifacts),
            "coverage": dict(self.coverage),
            "artifact_taxonomy": dict(self.artifact_taxonomy),
        }




def _normalize_plan_step(step: dict[str, Any], index: int) -> dict[str, Any]:
    payload = dict(step or {})
    tool = str(payload.get("tool") or "").strip()
    args = dict(payload.get("args") or {})
    normalized = {
        "step_id": str(payload.get("step_id") or f"step_{index:02d}"),
        "tool": tool,
        "args": args,
    }
    if "thought" in payload and payload.get("thought") is not None:
        normalized["thought"] = str(payload.get("thought"))
    for key, value in payload.items():
        if key not in normalized:
            normalized[key] = value
    return normalized


def _normalize_recommended_plan(recommended_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_normalize_plan_step(dict(step or {}), index) for index, step in enumerate(list(recommended_plan or []), start=1)]


def _normalize_plan_tools(recommended_plan: list[dict[str, Any]]) -> list[str]:
    tools: list[str] = []
    for step in _normalize_recommended_plan(recommended_plan):
        tool = str(step.get("tool") or "").strip()
        if tool and tool not in tools:
            tools.append(tool)
    return tools


def _build_plan_summary(*, available_tools: list[str], recommended_plan: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_plan = _normalize_recommended_plan(recommended_plan)
    recommended_tools = _normalize_plan_tools(normalized_plan)
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "available_tool_count": len(list(available_tools or [])),
        "recommended_step_count": len(normalized_plan),
        "recommended_tool_count": len(recommended_tools),
        "recommended_tools": recommended_tools,
        "step_ids": [str(step.get("step_id") or "") for step in normalized_plan],
    }


def _default_coverage(*, recommended_plan: list[dict[str, Any]], tool_calls: list[dict[str, Any]], available_tools: list[str]) -> dict[str, Any]:
    recommended_steps = _normalize_recommended_plan(recommended_plan)
    used_tools = [str(item.get("action", {}).get("tool") or "") for item in list(tool_calls or []) if item.get("action")]
    used_tool_set = {tool for tool in used_tools if tool}
    recommended_tools = _normalize_plan_tools(recommended_steps)
    covered_tools = [tool for tool in recommended_tools if tool in used_tool_set]
    covered_step_ids = [str(step.get("step_id") or "") for step in recommended_steps if str(step.get("tool") or "") in used_tool_set]
    planned_step_coverage = 1.0 if not recommended_steps else round(len(covered_step_ids) / len(recommended_steps), 3)
    missing_steps = [step for step in recommended_steps if str(step.get("tool") or "") not in used_tool_set]
    return {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "coverage_kind": "plan_vs_execution",
        "recommended_step_count": len(recommended_steps),
        "executed_step_count": len(list(tool_calls or [])),
        "available_tool_count": len(list(available_tools or [])),
        "used_tool_count": len(used_tool_set),
        "recommended_tool_count": len(recommended_tools),
        "covered_recommended_tools": covered_tools,
        "covered_recommended_step_ids": covered_step_ids,
        "missing_planned_steps": missing_steps,
        "missing_planned_step_ids": [str(step.get("step_id") or "") for step in missing_steps],
        "planned_step_coverage": planned_step_coverage,
        "required_tool_coverage": 1.0 if not recommended_tools else round(len(covered_tools) / len(recommended_tools), 3),
    }


def _normalize_coverage(*, coverage: dict[str, Any] | None, recommended_plan: list[dict[str, Any]], tool_calls: list[dict[str, Any]], available_tools: list[str]) -> dict[str, Any]:
    base = _default_coverage(
        recommended_plan=recommended_plan,
        tool_calls=tool_calls,
        available_tools=available_tools,
    )
    if not coverage:
        return base
    merged = {**base, **dict(coverage)}
    merged.setdefault("schema_version", COVERAGE_SCHEMA_VERSION)
    merged.setdefault("coverage_kind", "plan_vs_execution")
    merged.setdefault("recommended_step_count", len(_normalize_recommended_plan(recommended_plan)))
    merged.setdefault("executed_step_count", len(list(tool_calls or [])))
    merged.setdefault("available_tool_count", len(list(available_tools or [])))
    merged.setdefault("used_tool_count", len({str(item.get("action", {}).get("tool") or "") for item in list(tool_calls or []) if item.get("action")}))
    merged.setdefault("recommended_tool_count", len(_normalize_plan_tools(recommended_plan)))
    merged.setdefault("planned_step_coverage", base.get("planned_step_coverage", 1.0))
    merged.setdefault("required_tool_coverage", base.get("required_tool_coverage", 1.0))
    merged.setdefault("missing_planned_steps", base.get("missing_planned_steps", []))
    merged["missing_planned_steps"] = _normalize_recommended_plan(list(merged.get("missing_planned_steps") or []))
    merged.setdefault("missing_planned_step_ids", [str(step.get("step_id") or "") for step in list(merged.get("missing_planned_steps") or [])])
    merged.setdefault("covered_recommended_step_ids", base.get("covered_recommended_step_ids", []))
    return merged


def _confirmation_state(*, writes_state: bool, requires_confirmation: bool, decision: str) -> str:
    if requires_confirmation:
        return "pending_confirmation"
    if writes_state:
        return "confirmed_or_not_required" if decision == "allow" else str(decision or "pending")
    return "not_applicable"


def _build_confirmation_summary(*, writes_state: bool, requires_confirmation: bool, decision: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "required": bool(requires_confirmation),
        "decision": str(decision),
        "state": _confirmation_state(writes_state=writes_state, requires_confirmation=requires_confirmation, decision=decision),
        "reason_codes": list(reasons or []),
    }


def _classify_artifact_value(value: Any) -> str:
    if isinstance(value, str):
        lower = value.lower()
        if "/" in value or chr(92) in value or lower.endswith((".json", ".jsonl", ".md", ".csv", ".txt", ".log", ".yaml", ".yml")):
            return "path"
        if lower.endswith(("_id", "id")):
            return "id"
        return "scalar"
    if isinstance(value, (int, float, bool)) or value is None:
        return "scalar"
    if isinstance(value, list):
        return "collection"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _build_artifact_taxonomy(artifacts: dict[str, Any]) -> dict[str, Any]:
    items = dict(artifacts or {})
    kinds = {key: _classify_artifact_value(value) for key, value in items.items()}
    return {
        "schema_version": ARTIFACT_TAXONOMY_SCHEMA_VERSION,
        "count": len(items),
        "keys": sorted(items.keys()),
        "kinds": kinds,
        "path_keys": sorted([key for key, kind in kinds.items() if kind == "path"]),
        "object_keys": sorted([key for key, kind in kinds.items() if kind == "object"]),
        "collection_keys": sorted([key for key, kind in kinds.items() if kind == "collection"]),
        "known_kinds": ["collection", "id", "object", "path", "scalar", "unknown"],
    }


def build_task_bus(
    *,
    intent: str,
    operation: str,
    user_goal: str,
    mode: str,
    available_tools: list[str],
    recommended_plan: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    artifacts: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    status: str = "ok",
    writes_state: bool = False,
    risk_level: str = "low",
    decision: str = "allow",
    requires_confirmation: bool = False,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    started = datetime.now().isoformat()
    completed = datetime.now().isoformat()
    normalized_reasons = list(reasons or [])
    normalized_plan = _normalize_recommended_plan(recommended_plan)
    normalized_artifacts = dict(artifacts or {})
    normalized_coverage = _normalize_coverage(
        coverage=coverage,
        recommended_plan=normalized_plan,
        tool_calls=tool_calls,
        available_tools=available_tools,
    )
    planner = TaskPlan(
        intent=intent,
        operation=operation,
        mode=mode,
        user_goal=user_goal,
        available_tools=available_tools,
        recommended_plan=normalized_plan,
        plan_summary=_build_plan_summary(available_tools=available_tools, recommended_plan=normalized_plan),
    )
    gate = TaskGate(
        decision=decision,
        risk_level=risk_level,
        writes_state=writes_state,
        requires_confirmation=requires_confirmation,
        reasons=normalized_reasons,
        confirmation=_build_confirmation_summary(
            writes_state=writes_state,
            requires_confirmation=requires_confirmation,
            decision=decision,
            reasons=normalized_reasons,
        ),
    )
    audit = TaskAudit(
        status=status,
        started_at=started,
        completed_at=completed,
        tool_count=len(tool_calls),
        used_tools=[str(item.get("action", {}).get("tool") or "") for item in tool_calls if item.get("action")],
        artifacts=normalized_artifacts,
        coverage=normalized_coverage,
        artifact_taxonomy=_build_artifact_taxonomy(normalized_artifacts),
    )
    return {
        "schema_version": TASK_BUS_SCHEMA_VERSION,
        "planner": planner.to_dict(),
        "gate": gate.to_dict(),
        "audit": audit.to_dict(),
    }


def build_readonly_task_bus(
    *,
    intent: str,
    operation: str,
    user_goal: str,
    mode: str,
    available_tools: list[str],
    recommended_plan: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    artifacts: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    status: str = "ok",
) -> dict[str, Any]:
    return build_task_bus(
        intent=intent,
        operation=operation,
        user_goal=user_goal,
        mode=mode,
        available_tools=available_tools,
        recommended_plan=recommended_plan,
        tool_calls=tool_calls,
        artifacts=artifacts,
        coverage=coverage,
        status=status,
        writes_state=False,
        risk_level="low",
        decision="allow",
        requires_confirmation=False,
        reasons=["read_only_analysis", "tool_grounded_execution"],
    )


def build_mutating_task_bus(
    *,
    intent: str,
    operation: str,
    user_goal: str,
    mode: str,
    available_tools: list[str],
    recommended_plan: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    artifacts: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    status: str = "ok",
    risk_level: str = "medium",
    decision: str = "allow",
    requires_confirmation: bool = False,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    return build_task_bus(
        intent=intent,
        operation=operation,
        user_goal=user_goal,
        mode=mode,
        available_tools=available_tools,
        recommended_plan=recommended_plan,
        tool_calls=tool_calls,
        artifacts=artifacts,
        coverage=coverage,
        status=status,
        writes_state=True,
        risk_level=risk_level,
        decision=decision,
        requires_confirmation=requires_confirmation,
        reasons=list(reasons or ["state_changing_request", "tool_grounded_execution"]),
    )


__all__ = [
    "ARTIFACT_TAXONOMY_SCHEMA_VERSION",
    "COVERAGE_SCHEMA_VERSION",
    "PLAN_SCHEMA_VERSION",
    "TASK_BUS_SCHEMA_VERSION",
    "build_mutating_task_bus",
    "build_readonly_task_bus",
    "build_task_bus",
    "TaskAudit",
    "TaskGate",
    "TaskPlan",
]
