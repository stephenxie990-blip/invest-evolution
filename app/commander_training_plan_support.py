"""Training plan loading and execution-args helpers for commander."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any


def load_training_plan_artifact(plan_path: Path, *, plan_id: str) -> tuple[Path, dict[str, Any]]:
    if not plan_path.exists():
        raise FileNotFoundError(f"training plan not found: {plan_id}")
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid training plan json: {plan_id}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"training plan must decode to an object: {plan_id}")
    return plan_path, payload


def build_experiment_spec_from_plan(plan: dict[str, Any]) -> tuple[dict[str, Any], int, bool]:
    spec = dict(plan.get("spec") or {})
    rounds = int(spec.get("rounds", 1) or 1)
    mock = bool(spec.get("mock", False))
    experiment_spec = {
        "spec": spec,
        "protocol": dict(plan.get("protocol") or {}),
        "dataset": dict(plan.get("dataset") or {}),
        "model_scope": dict(plan.get("model_scope") or {}),
        "optimization": dict(plan.get("optimization") or {}),
        "llm": dict(plan.get("llm") or {}),
    }
    return experiment_spec, rounds, mock


def build_run_cycles_kwargs(
    run_cycles_callable: Any,
    *,
    plan: dict[str, Any],
    rounds: int,
    mock: bool,
    experiment_spec: dict[str, Any],
) -> dict[str, Any]:
    run_cycles_kwargs = {
        "rounds": rounds,
        "force_mock": mock,
        "task_source": str(plan.get("source", "manual")),
    }
    try:
        run_cycles_signature = inspect.signature(run_cycles_callable)
        if "experiment_spec" in run_cycles_signature.parameters:
            run_cycles_kwargs["experiment_spec"] = experiment_spec
    except (TypeError, ValueError):
        run_cycles_kwargs["experiment_spec"] = experiment_spec
    return run_cycles_kwargs
