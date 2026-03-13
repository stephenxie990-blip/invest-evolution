from __future__ import annotations

from typing import Any


class TrainingOutcomeService:
    """Builds cycle audit metadata and training result payloads."""

    def build_audit_tags(
        self,
        controller: Any,
        *,
        data_mode: str,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        selection_mode: str,
        agent_used: bool,
        llm_used: bool,
        benchmark_passed: bool,
        review_applied: bool,
        regime_result: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "data_mode": data_mode,
            "requested_data_mode": requested_data_mode,
            "effective_data_mode": effective_data_mode,
            "llm_mode": llm_mode,
            "degraded": degraded,
            "degrade_reason": degrade_reason,
            "selection_mode": selection_mode,
            "meeting_fallback": False,
            "agent_used": agent_used,
            "llm_used": llm_used,
            "mock_data_used": data_mode == "mock",
            "benchmark_passed": benchmark_passed,
            "review_applied": review_applied,
            "routing_enabled": controller.model_routing_enabled,
            "routing_mode": controller.model_routing_mode,
            "routing_model": (controller.last_routing_decision or {}).get(
                "selected_model",
                controller.model_name,
            ),
            "routing_regime": (controller.last_routing_decision or {}).get(
                "regime",
                regime_result.get("regime", "unknown"),
            ),
        }

    def build_cycle_result(
        self,
        controller: Any,
        *,
        result_factory: Any,
        cycle_id: int,
        cutoff_date: str,
        selected: list[str],
        sim_result: Any,
        is_profit: bool,
        trade_dicts: list[dict[str, Any]],
        data_mode: str,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        selection_mode: str,
        agent_used: bool,
        llm_used: bool,
        benchmark_passed: bool,
        cycle_dict: dict[str, Any],
        review_applied: bool,
        config_snapshot_path: str,
        optimization_events: list[dict[str, Any]],
        audit_tags: dict[str, Any],
        model_output: Any | None,
        research_feedback: dict[str, Any] | None,
    ) -> Any:
        return result_factory(
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            selected_stocks=selected,
            initial_capital=sim_result.initial_capital,
            final_value=sim_result.final_value,
            return_pct=sim_result.return_pct,
            is_profit=is_profit,
            trade_history=trade_dicts,
            params=dict(controller.current_params),
            analysis=cycle_dict.get("analysis", "") or "",
            data_mode=data_mode,
            requested_data_mode=requested_data_mode,
            effective_data_mode=effective_data_mode,
            llm_mode=llm_mode,
            degraded=degraded,
            degrade_reason=degrade_reason,
            selection_mode=selection_mode,
            agent_used=agent_used,
            llm_used=llm_used,
            benchmark_passed=benchmark_passed,
            strategy_scores=dict(cycle_dict.get("strategy_scores") or {}),
            review_applied=review_applied,
            config_snapshot_path=config_snapshot_path,
            optimization_events=optimization_events,
            audit_tags=audit_tags,
            model_name=(
                getattr(model_output, "model_name", controller.model_name)
                if model_output is not None
                else controller.model_name
            ),
            config_name=(
                getattr(model_output, "config_name", controller.model_config_path)
                if model_output is not None
                else controller.model_config_path
            ),
            routing_decision=dict(controller.last_routing_decision or {}),
            research_feedback=dict(research_feedback or {}),
        )
