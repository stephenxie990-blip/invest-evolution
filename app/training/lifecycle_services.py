from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.training.runtime_hooks import SelfAssessmentSnapshot, emit_event

logger = logging.getLogger(__name__)


class TrainingLifecycleService:
    """Owns cycle completion bookkeeping and continuous-run lifecycle control."""

    def finalize_cycle(
        self,
        controller: Any,
        *,
        cycle_result: Any,
        cycle_dict: dict[str, Any],
        cycle_id: int,
        cutoff_date: str,
        sim_result: Any,
        is_profit: bool,
        selected: list[str],
        trade_dicts: list[dict[str, Any]],
        review_applied: bool,
        selection_mode: str,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        research_feedback: dict[str, Any] | None,
    ) -> None:
        controller.cycle_history.append(cycle_result)
        controller.current_cycle_id += 1
        controller.training_persistence_service.record_self_assessment(
            controller,
            SelfAssessmentSnapshot,
            cycle_result,
            cycle_dict,
        )
        freeze_gate_evaluation = controller.freeze_gate_service.evaluate_freeze_gate(controller)

        controller.last_cycle_meta = {
            "status": "ok",
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "return_pct": sim_result.return_pct,
            "requested_data_mode": requested_data_mode,
            "effective_data_mode": effective_data_mode,
            "llm_mode": llm_mode,
            "degraded": degraded,
            "degrade_reason": degrade_reason,
            "routing_decision": dict(controller.last_routing_decision or {}),
            "research_feedback": controller._research_feedback_brief(research_feedback),
            "research_feedback_optimization": dict(controller.last_feedback_optimization or {}),
            "freeze_gate_evaluation": dict(freeze_gate_evaluation or {}),
            "timestamp": datetime.now().isoformat(),
        }
        controller.training_persistence_service.save_cycle_result(controller, cycle_result)

        event_emitter = getattr(controller, "_emit_runtime_event", emit_event)
        event_emitter(
            "cycle_complete",
            {
                "cycle_id": cycle_id,
                "cutoff_date": cutoff_date,
                "return_pct": sim_result.return_pct,
                "is_profit": bool(is_profit),
                "selected_count": len(selected),
                "selected_stocks": selected[:10],
                "trade_count": len(trade_dicts),
                "final_value": sim_result.final_value,
                "review_applied": review_applied,
                "selection_mode": selection_mode,
                "requested_data_mode": requested_data_mode,
                "effective_data_mode": effective_data_mode,
                "llm_mode": llm_mode,
                "degraded": degraded,
                "degrade_reason": degrade_reason,
                "model_name": controller.model_name,
                "routing_decision": dict(controller.last_routing_decision or {}),
                "timestamp": datetime.now().isoformat(),
            },
        )
        controller._emit_module_log(
            "cycle_complete",
            f"周期 #{cycle_id} 完成",
            f"收益 {sim_result.return_pct:+.2f}% ，共 {len(selected)} 只选股",
            cycle_id=cycle_id,
            kind="cycle_complete",
            details={
                "selected_stocks": selected[:10],
                "trade_count": len(trade_dicts),
                "review_applied": review_applied,
                "requested_data_mode": requested_data_mode,
                "effective_data_mode": effective_data_mode,
                "llm_mode": llm_mode,
                "degraded": degraded,
                "degrade_reason": degrade_reason,
            },
            metrics={
                "return_pct": sim_result.return_pct,
                "selected_count": len(selected),
                "trade_count": len(trade_dicts),
            },
        )
        if controller.on_cycle_complete:
            controller.on_cycle_complete(cycle_result)

        logger.info(
            "\n周期 #%s 完成: 收益率 %.2f%%, %s",
            cycle_id,
            sim_result.return_pct,
            "盈利" if is_profit else "亏损",
        )

    def run_continuous(self, controller: Any, *, max_cycles: int = 100) -> dict[str, Any]:
        logger.info(f"\n{'#'*60}")
        logger.info("开始持续训练 (最多 %s 个周期)", max_cycles)
        logger.info(f"{'#'*60}")

        for i in range(max_cycles):
            if controller.freeze_gate_service.should_freeze(controller):
                logger.info("🎉 达到固化条件！")
                if controller.stop_on_freeze:
                    return controller.freeze_gate_service.freeze_model(controller)
                logger.info("配置为继续训练，不因固化条件提前停止")

            controller.total_cycle_attempts += 1
            result = controller.run_training_cycle()
            if result is None:
                controller.skipped_cycle_count += 1
                logger.warning("周期 %s 执行失败，跳过", i + 1)
                continue

            profits = sum(1 for item in controller.cycle_history if item.is_profit)
            total = len(controller.cycle_history)
            logger.info(
                "进度: %s/%s | 盈利: %s/%s | 连续亏损: %s",
                i + 1,
                max_cycles,
                profits,
                total,
                controller.consecutive_losses,
            )

        return controller.freeze_gate_service.generate_training_report(controller)
