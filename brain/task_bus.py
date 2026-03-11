from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TaskPlan:
    intent: str
    operation: str
    mode: str
    user_goal: str
    available_tools: list[str] = field(default_factory=list)
    recommended_plan: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "operation": self.operation,
            "mode": self.mode,
            "user_goal": self.user_goal,
            "available_tools": list(self.available_tools),
            "recommended_plan": list(self.recommended_plan),
        }


@dataclass
class TaskGate:
    decision: str
    risk_level: str
    writes_state: bool
    requires_confirmation: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "risk_level": self.risk_level,
            "writes_state": self.writes_state,
            "requires_confirmation": self.requires_confirmation,
            "reasons": list(self.reasons),
        }


@dataclass
class TaskAudit:
    status: str
    started_at: str
    completed_at: str
    tool_count: int
    used_tools: list[str] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "tool_count": self.tool_count,
            "used_tools": list(self.used_tools),
            "artifacts": dict(self.artifacts),
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
    status: str = "ok",
    writes_state: bool = False,
    risk_level: str = "low",
    decision: str = "allow",
    requires_confirmation: bool = False,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    started = datetime.now().isoformat()
    completed = datetime.now().isoformat()
    planner = TaskPlan(
        intent=intent,
        operation=operation,
        mode=mode,
        user_goal=user_goal,
        available_tools=available_tools,
        recommended_plan=recommended_plan,
    )
    gate = TaskGate(
        decision=decision,
        risk_level=risk_level,
        writes_state=writes_state,
        requires_confirmation=requires_confirmation,
        reasons=list(reasons or []),
    )
    audit = TaskAudit(
        status=status,
        started_at=started,
        completed_at=completed,
        tool_count=len(tool_calls),
        used_tools=[str(item.get("action", {}).get("tool") or "") for item in tool_calls if item.get("action")],
        artifacts=dict(artifacts or {}),
    )
    return {
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
        status=status,
        writes_state=True,
        risk_level=risk_level,
        decision=decision,
        requires_confirmation=requires_confirmation,
        reasons=list(reasons or ["state_changing_request", "tool_grounded_execution"]),
    )


__all__ = [
    "build_mutating_task_bus",
    "build_readonly_task_bus",
    "build_task_bus",
    "TaskAudit",
    "TaskGate",
    "TaskPlan",
]
