from __future__ import annotations

from typing import Any

from invest.foundation.metrics.benchmark import BenchmarkEvaluator
from invest.foundation.risk.controller import sanitize_risk_params
from invest.models.defaults import COMMON_BENCHMARK_DEFAULTS


class TrainingPolicyService:
    """Synchronizes runtime policies from the active investment model."""

    @staticmethod
    def policy_lookup(policy: dict[str, Any] | None, path: str, default: Any) -> Any:
        current: Any = dict(policy or {})
        for key in path.split('.'):
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return current

    def sanitize_runtime_param_adjustments(
        self,
        controller: Any,
        adjustments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = dict(adjustments or {})
        risk_like = {
            key: float(value)
            for key, value in normalized.items()
            if key in {"stop_loss_pct", "take_profit_pct", "position_size"} and value is not None
        }
        clean = sanitize_risk_params(risk_like, policy=controller.risk_policy)
        clamp_policy = dict(self.policy_lookup(controller.review_policy, "param_clamps", {}) or {})
        cash_bounds = dict(clamp_policy.get("cash_reserve") or {"min": 0.0, "max": 0.80})
        trailing_bounds = dict(clamp_policy.get("trailing_pct") or {"min": 0.03, "max": 0.20})
        if normalized.get("cash_reserve") is not None:
            clean["cash_reserve"] = max(
                float(cash_bounds.get("min", 0.0)),
                min(float(cash_bounds.get("max", 0.80)), float(normalized["cash_reserve"])),
            )
        if normalized.get("trailing_pct") is not None:
            clean["trailing_pct"] = max(
                float(trailing_bounds.get("min", 0.03)),
                min(float(trailing_bounds.get("max", 0.20)), float(normalized["trailing_pct"])),
            )
        return clean

    def sync_runtime_policy(self, controller: Any) -> None:
        if getattr(controller, "investment_model", None) is None:
            return

        config_params = controller.investment_model.config_section("params", {})
        merged_params = dict(controller.DEFAULT_PARAMS)
        merged_params.update(config_params or {})
        explicit_overrides = {
            key: value
            for key, value in (controller.current_params or {}).items()
            if key not in controller.DEFAULT_PARAMS or value != controller.DEFAULT_PARAMS.get(key)
        }
        merged_params.update(explicit_overrides)
        controller.current_params = merged_params
        controller.investment_model.update_runtime_overrides(controller.current_params)

        controller.execution_policy = controller.investment_model.config_section("execution", {}) or {}
        controller.risk_policy = controller.investment_model.config_section("risk_policy", {}) or {}
        controller.evaluation_policy = controller.investment_model.config_section("evaluation_policy", {}) or {}
        controller.review_policy = controller.investment_model.config_section("review_policy", {}) or {}
        controller.strategy_evaluator.set_policy(controller.evaluation_policy)
        controller.review_meeting_service.set_policy(controller.review_policy)

        benchmark_policy = controller.investment_model.config_section("benchmark", {}) or {}
        benchmark_criteria = dict(
            benchmark_policy.get("criteria")
            or COMMON_BENCHMARK_DEFAULTS.get("criteria")
            or {}
        )
        controller.benchmark_evaluator = BenchmarkEvaluator(
            risk_free_rate=float(
                benchmark_policy.get(
                    "risk_free_rate",
                    COMMON_BENCHMARK_DEFAULTS["risk_free_rate"],
                )
                or COMMON_BENCHMARK_DEFAULTS["risk_free_rate"]
            ),
            criteria=benchmark_criteria,
        )

        controller.train_policy = controller.investment_model.config_section("train", {}) or {}
        controller.freeze_total_cycles = int(
            controller.train_policy.get("freeze_total_cycles", controller.freeze_total_cycles)
            or controller.freeze_total_cycles
        )
        controller.freeze_profit_required = int(
            controller.train_policy.get("freeze_profit_required", controller.freeze_profit_required)
            or controller.freeze_profit_required
        )
        controller.max_losses_before_optimize = int(
            controller.train_policy.get(
                "max_losses_before_optimize",
                controller.max_losses_before_optimize,
            )
            or controller.max_losses_before_optimize
        )
        controller.freeze_gate_policy = dict(controller.train_policy.get("freeze_gate", {}) or {})
        controller.auto_apply_mutation = bool(controller.train_policy.get("auto_apply_mutation", False))
        controller.research_feedback_policy = dict(
            controller.train_policy.get("research_feedback", {}) or {}
        )
        controller.research_feedback_optimization_policy = dict(
            controller.research_feedback_policy.get("optimization", {}) or {}
        )
        controller.research_feedback_freeze_policy = dict(
            controller.research_feedback_policy.get("freeze_gate", {})
            or controller.freeze_gate_policy.get("research_feedback", {})
            or {}
        )
        if controller.research_feedback_freeze_policy and not controller.freeze_gate_policy.get(
            "research_feedback"
        ):
            controller.freeze_gate_policy["research_feedback"] = dict(
                controller.research_feedback_freeze_policy
            )

        agent_weights = controller.investment_model.config_section("agent_weights", {}) or {}
        if agent_weights:
            controller.selection_meeting_service.set_agent_weights({
                "trend_hunter": float(agent_weights.get("trend_hunter", 1.0) or 1.0),
                "contrarian": float(agent_weights.get("contrarian", 1.0) or 1.0),
            })
