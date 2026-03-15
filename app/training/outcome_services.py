from __future__ import annotations

import math
from typing import Any

from app.training.experiment_protocol import build_cycle_run_context, build_execution_snapshot
from app.training.lineage_services import build_lineage_record
from app.training.promotion_services import build_promotion_record


class TrainingOutcomeService:
    """Builds cycle audit metadata and training result payloads."""

    @staticmethod
    def _finite_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def build_realism_metrics(
        *,
        trade_dicts: list[dict[str, Any]],
        selection_mode: str,
        optimization_events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        trades = [dict(item) for item in list(trade_dicts or []) if isinstance(item, dict)]
        trade_amounts = [
            amount
            for amount in (
                TrainingOutcomeService._finite_float(item.get("amount"))
                for item in trades
            )
            if amount is not None
        ]
        avg_trade_amount = (
            sum(trade_amounts) / len(trade_amounts)
            if trade_amounts
            else 0.0
        )
        turnover_values = [
            turnover_rate
            for turnover_rate in (
                TrainingOutcomeService._finite_float(item.get("turnover_rate"))
                for item in trades
            )
            if turnover_rate is not None
        ]
        holding_days = [
            int(item.get("holding_days", 0) or 0)
            for item in trades
            if int(item.get("holding_days", 0) or 0) > 0
        ]
        source_counts: dict[str, int] = {}
        exit_trigger_counts: dict[str, int] = {}
        for item in trades:
            source = str(item.get("source") or "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1
            trigger = str(item.get("exit_trigger") or "")
            if trigger:
                exit_trigger_counts[trigger] = exit_trigger_counts.get(trigger, 0) + 1
        total_trades = len(trades) or 1
        return {
            "trade_record_count": len(trades),
            "selection_mode": str(selection_mode or ""),
            "optimization_event_count": len(list(optimization_events or [])),
            "avg_trade_amount": round(avg_trade_amount, 2),
            "avg_turnover_rate": round(
                (sum(turnover_values) / len(turnover_values)) if turnover_values else 0.0,
                4,
            ),
            "high_turnover_trade_count": sum(1 for value in turnover_values if value >= 10.0),
            "avg_holding_days": round(
                (sum(holding_days) / len(holding_days)) if holding_days else 0.0,
                2,
            ),
            "source_mix": {
                key: round(value / total_trades, 4)
                for key, value in sorted(source_counts.items())
            },
            "exit_trigger_mix": {
                key: round(value / total_trades, 4)
                for key, value in sorted(exit_trigger_counts.items())
            },
        }

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
        research_artifacts: dict[str, Any] | None = None,
        ab_comparison: dict[str, Any] | None = None,
    ) -> Any:
        experiment_spec = dict(getattr(controller, "experiment_spec", {}) or {})
        execution_snapshot = dict(
            cycle_dict.get("execution_snapshot")
            or build_execution_snapshot(
                controller,
                cycle_id=cycle_id,
                model_output=model_output,
                selection_mode=selection_mode,
                benchmark_passed=benchmark_passed,
                basis_stage="persistence_fallback",
            )
        )
        run_context = build_cycle_run_context(
            controller,
            cycle_id=cycle_id,
            model_output=model_output,
            optimization_events=optimization_events,
            execution_snapshot=execution_snapshot,
        )
        promotion_record = build_promotion_record(
            cycle_id=cycle_id,
            run_context=run_context,
            optimization_events=optimization_events,
        )
        lineage_record = build_lineage_record(
            controller,
            cycle_id=cycle_id,
            model_output=model_output,
            run_context=run_context,
            optimization_events=optimization_events,
        )
        realism_metrics = self.build_realism_metrics(
            trade_dicts=trade_dicts,
            selection_mode=selection_mode,
            optimization_events=optimization_events,
        )
        return result_factory(
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            selected_stocks=selected,
            initial_capital=sim_result.initial_capital,
            final_value=sim_result.final_value,
            return_pct=sim_result.return_pct,
            is_profit=is_profit,
            trade_history=trade_dicts,
            params=dict(execution_snapshot.get("runtime_overrides") or {}),
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
            model_name=str(
                execution_snapshot.get("model_name")
                or (getattr(model_output, "model_name", controller.model_name) if model_output is not None else controller.model_name)
            ),
            config_name=str(
                execution_snapshot.get("active_config_ref")
                or (getattr(model_output, "config_name", controller.model_config_path) if model_output is not None else controller.model_config_path)
            ),
            routing_decision=dict(execution_snapshot.get("routing_decision") or controller.last_routing_decision or {}),
            research_feedback=dict(research_feedback or {}),
            research_artifacts=dict(research_artifacts or {}),
            ab_comparison=dict(ab_comparison or {}),
            experiment_spec=experiment_spec,
            execution_snapshot=execution_snapshot,
            run_context=run_context,
            promotion_record=promotion_record,
            lineage_record=lineage_record,
            review_decision=dict(cycle_dict.get("review_decision") or {}),
            causal_diagnosis=dict(cycle_dict.get("causal_diagnosis") or {}),
            similarity_summary=dict(cycle_dict.get("similarity_summary") or {}),
            similar_results=[dict(item) for item in list(cycle_dict.get("similar_results") or [])],
            realism_metrics=realism_metrics,
        )
