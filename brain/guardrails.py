"""Semantic pre-execution guardrails for mutating runtime tools."""

from __future__ import annotations

from typing import Any


def _dict_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _list_payload(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return list(value)


def _find_placeholder_paths(value: Any, *, prefix: str = "") -> list[str]:
    matches: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            matches.extend(_find_placeholder_paths(item, prefix=path))
        return matches
    if isinstance(value, list):
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            matches.extend(_find_placeholder_paths(item, prefix=path))
        return matches
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("<") and text.endswith(">"):
            return [prefix or "value"]
    return matches


def _flatten_leaf_paths(value: Any, *, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_flatten_leaf_paths(item, prefix=path))
        return paths
    if isinstance(value, list):
        paths: list[str] = []
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            paths.extend(_flatten_leaf_paths(item, prefix=path))
        return paths
    return [prefix or "value"]


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


class RuntimeGuardrails:
    _PATCH_TOOLS = {
        "invest_control_plane_update",
        "invest_runtime_paths_update",
        "invest_evolution_config_update",
    }
    _MUTATING_TOOLS = _PATCH_TOOLS | {
        "invest_training_plan_create",
        "invest_training_plan_execute",
        "invest_data_download",
        "invest_train",
    }
    _PATCH_SCOPE_RULES = {
        "invest_control_plane_update": {
            "forbidden_fragments": (
                "training_output_dir",
                "runtime_paths",
                "workspace",
                "output_dir",
                "simulation_days",
                "stop_loss_pct",
                "take_profit_pct",
            ),
            "reason_code": "cross_scope_patch",
            "message": "Guardrail blocked a control plane patch that belongs to runtime paths or evolution config scope.",
        },
        "invest_runtime_paths_update": {
            "forbidden_fragments": (
                "llm",
                "bindings",
                "provider",
                "api_key",
                "model_routing",
                "investment_model",
                "stop_loss_pct",
            ),
            "reason_code": "cross_scope_patch",
            "message": "Guardrail blocked a runtime paths patch that belongs to control plane or evolution config scope.",
        },
        "invest_evolution_config_update": {
            "forbidden_fragments": (
                "llm",
                "bindings",
                "provider",
                "api_key",
                "training_output_dir",
                "workspace",
                "bridge_inbox",
                "bridge_outbox",
            ),
            "reason_code": "cross_scope_patch",
            "message": "Guardrail blocked an evolution config patch that belongs to control plane or runtime paths scope.",
        },
    }

    def evaluate(self, *, tool_name: str, params: dict[str, Any]) -> dict[str, Any] | None:
        name = str(tool_name or "")
        payload = _dict_payload(params)
        placeholder_paths = _find_placeholder_paths(payload)
        if name in self._MUTATING_TOOLS and placeholder_paths:
            return self._blocked_payload(
                tool_name=name,
                reason_codes=["placeholder_arguments"],
                message="Guardrail blocked placeholder arguments in a mutating tool call.",
                details={"paths": placeholder_paths},
            )

        if name in self._PATCH_TOOLS and not _dict_payload(payload.get("patch")):
            return self._blocked_payload(
                tool_name=name,
                reason_codes=["empty_patch"],
                message="Guardrail blocked an empty patch for a high-risk config update.",
                details={"required": ["patch"]},
            )

        plan_id = str(payload.get("plan_id") or "").strip()
        if name == "invest_training_plan_execute" and not plan_id:
            return self._blocked_payload(
                tool_name=name,
                reason_codes=["missing_plan_id"],
                message="Guardrail blocked training plan execution without a concrete plan_id.",
                details={"required": ["plan_id"]},
            )

        patch_violation = self._evaluate_patch_scope(tool_name=name, payload=payload)
        if patch_violation is not None:
            return patch_violation

        plan_violation = self._evaluate_training_plan_create(payload=payload) if name == "invest_training_plan_create" else None
        if plan_violation is not None:
            return plan_violation
        return None

    def _evaluate_patch_scope(self, *, tool_name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        rule = self._PATCH_SCOPE_RULES.get(tool_name)
        patch = _dict_payload(payload.get("patch"))
        if not rule or not patch:
            return None
        leaf_paths = _flatten_leaf_paths(patch)
        forbidden = tuple(str(item) for item in rule.get("forbidden_fragments") or ())
        offending = [
            path for path in leaf_paths
            if any(fragment in path for fragment in forbidden)
        ]
        if not offending:
            return None
        return self._blocked_payload(
            tool_name=tool_name,
            reason_codes=[str(rule.get("reason_code") or "cross_scope_patch")],
            message=str(rule.get("message") or "Guardrail blocked a cross-scope patch."),
            details={"paths": offending},
        )

    def _evaluate_training_plan_create(self, *, payload: dict[str, Any]) -> dict[str, Any] | None:
        rounds = _safe_int(payload.get("rounds"))
        protocol = _dict_payload(payload.get("protocol"))
        dataset = _dict_payload(payload.get("dataset"))
        llm = _dict_payload(payload.get("llm"))

        min_history_days = _safe_int(dataset.get("min_history_days"))
        simulation_days = _safe_int(dataset.get("simulation_days"))
        if (
            min_history_days is not None
            and simulation_days is not None
            and min_history_days < simulation_days
        ):
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["history_window_too_short"],
                message="Guardrail blocked a training plan whose min_history_days is shorter than simulation_days.",
                details={
                    "min_history_days": min_history_days,
                    "simulation_days": simulation_days,
                },
            )

        review_window = _dict_payload(protocol.get("review_window"))
        review_mode = str(review_window.get("mode") or "").strip().lower()
        if review_mode and review_mode not in {"single_cycle", "rolling"}:
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["invalid_review_window_mode"],
                message="Guardrail blocked a training plan with an unsupported review_window.mode.",
                details={"review_window_mode": review_mode},
            )

        review_size = _safe_int(review_window.get("size") or review_window.get("window"))
        if review_mode == "single_cycle" and review_size not in (None, 1):
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["single_cycle_window_size_conflict"],
                message="Guardrail blocked a single_cycle review window whose size is not 1.",
                details={"review_window_mode": review_mode, "review_window_size": review_size},
            )
        if rounds is not None and review_size is not None and review_size > max(1, rounds):
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["review_window_exceeds_rounds"],
                message="Guardrail blocked a training plan whose review window exceeds total rounds.",
                details={"rounds": rounds, "review_window_size": review_size},
            )

        cutoff_policy = _dict_payload(protocol.get("cutoff_policy"))
        cutoff_mode = str(cutoff_policy.get("mode") or "").strip().lower()
        if cutoff_mode and cutoff_mode not in {"random", "fixed", "rolling", "sequence", "regime_balanced"}:
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["invalid_cutoff_policy_mode"],
                message="Guardrail blocked a training plan with an unsupported cutoff_policy.mode.",
                details={"cutoff_policy_mode": cutoff_mode},
            )

        llm_mode = str(llm.get("mode") or "").strip().lower()
        dry_run = bool(llm.get("dry_run", False))
        if dry_run and llm_mode == "live":
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["conflicting_llm_mode"],
                message="Guardrail blocked a training plan with conflicting llm.dry_run=true and llm.mode=live.",
                details={"llm": {"mode": llm_mode, "dry_run": dry_run}},
            )

        promotion_gate = _dict_payload(_dict_payload(payload.get("optimization")).get("promotion_gate"))
        min_samples = _safe_int(promotion_gate.get("min_samples"))
        if rounds is not None and min_samples is not None and min_samples > max(1, rounds):
            return self._blocked_payload(
                tool_name="invest_training_plan_create",
                reason_codes=["promotion_gate_exceeds_rounds"],
                message="Guardrail blocked a training plan whose promotion gate min_samples exceeds total rounds.",
                details={"rounds": rounds, "min_samples": min_samples},
            )
        return None

    @staticmethod
    def _blocked_payload(
        *,
        tool_name: str,
        reason_codes: list[str],
        message: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "status": "guardrail_blocked",
            "message": message,
            "guardrails": {
                "decision": "block",
                "tool_name": tool_name,
                "reason_codes": list(reason_codes),
                "details": dict(details),
            },
        }


__all__ = ["RuntimeGuardrails"]
