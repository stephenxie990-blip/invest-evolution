from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from invest.models.defaults import COMMON_PARAM_DEFAULTS


@dataclass(frozen=True)
class TrainingSelectionResult:
    model_output: Any
    regime_result: dict[str, Any]
    trading_plan: Any
    meeting_log: dict[str, Any]
    strategy_advice: dict[str, Any]
    selected: list[str]
    selected_data: dict[str, Any]
    selection_mode: str
    agent_used: bool


class TrainingSelectionService:
    """Owns model output extraction and selection-meeting orchestration."""

    def run_selection_stage(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
    ) -> TrainingSelectionResult | None:
        controller.investment_model.update_runtime_overrides(controller.current_params)
        model_output = controller.investment_model.process(stock_data, cutoff_date)
        regime_result = self._build_regime_result(controller, model_output)

        controller._emit_agent_status(
            "InvestmentModel",
            "completed",
            f"{controller.model_name} 已输出结构化信号与叙事上下文",
            cycle_id=cycle_id,
            stage="model_extraction",
            progress_pct=30,
            step=2,
            total_steps=6,
            details=model_output.to_dict(),
        )
        controller._emit_module_log(
            "model_extraction",
            "模型输出完成",
            model_output.agent_context.summary,
            cycle_id=cycle_id,
            kind="model_output",
            details={
                "model_name": model_output.model_name,
                "config_name": model_output.config_name,
                "selected_codes": model_output.signal_packet.selected_codes,
            },
            metrics={
                "signal_count": len(model_output.signal_packet.signals),
                "max_positions": model_output.signal_packet.max_positions,
            },
        )
        controller._emit_agent_status(
            "MarketRegime",
            "thinking",
            f"分析当前市场状态: {regime_result.get('regime', 'unknown')}",
            cycle_id=cycle_id,
            stage="market_regime",
            progress_pct=32,
            step=2,
            total_steps=6,
            thinking=controller._thinking_excerpt(model_output.agent_context.summary),
            details=regime_result,
        )
        controller._emit_module_log(
            "market_regime",
            "市场状态识别",
            f"当前市场状态: {regime_result.get('regime', 'unknown')}",
            cycle_id=cycle_id,
            kind="market_regime",
            details=model_output.agent_context.summary,
            metrics={
                "confidence": regime_result.get("confidence"),
                "suggested_exposure": regime_result.get("suggested_exposure"),
            },
        )

        meeting_data = controller.selection_meeting_service.run_with_model_output(model_output)
        trading_plan = meeting_data["trading_plan"]
        meeting_log = dict(meeting_data.get("meeting_log", {}) or {})
        strategy_advice = dict(meeting_data.get("strategy_advice", {}) or {})
        controller.meeting_recorder.save_selection(meeting_log, cycle_id)

        for hunter in meeting_log.get("hunters", []):
            picks = hunter.get("result", {}).get("picks", [])
            if picks:
                controller.agent_tracker.record_predictions(
                    cycle_id,
                    hunter.get("name", "unknown"),
                    picks,
                )
            controller._emit_meeting_speech(
                "selection",
                hunter.get("name", "unknown"),
                hunter.get("result", {}).get("overall_view")
                or hunter.get("result", {}).get("reasoning")
                or "已完成候选输出",
                cycle_id=cycle_id,
                role="hunter",
                picks=picks[:10],
                confidence=hunter.get("result", {}).get("confidence"),
            )

        selected = [position.code for position in trading_plan.positions]
        agent_used = bool(meeting_log.get("hunters"))
        selection_mode = "meeting" if selected else "meeting_empty"
        if selected and trading_plan.source and trading_plan.source != "llm":
            selection_mode = f"{trading_plan.source}_selection"

        if not selected:
            controller._mark_cycle_skipped(
                cycle_id,
                cutoff_date,
                stage="selection",
                reason="模型与会议未产出可交易标的",
            )
            return None

        controller._emit_agent_status(
            "SelectionMeeting",
            "completed",
            f"选股完成，共选中 {len(selected)} 只股票",
            cycle_id=cycle_id,
            stage="selection_meeting",
            progress_pct=58,
            step=2,
            total_steps=6,
            selected_stocks=selected[:10],
            details=meeting_log.get("selected", []),
        )
        controller._emit_module_log(
            "selection",
            "选股会议完成",
            f"最终选中 {len(selected)} 只股票",
            cycle_id=cycle_id,
            kind="selection_result",
            details=meeting_log.get("selected", selected)[:10],
            metrics={
                "selected_count": len(selected),
                "selection_mode": selection_mode,
            },
        )
        controller.agent_tracker.mark_selected(cycle_id, selected)

        selected_data = {
            code: stock_data[code]
            for code in selected
            if code in stock_data
        }
        if not selected_data:
            controller._mark_cycle_skipped(
                cycle_id,
                cutoff_date,
                stage="selection",
                reason="选股结果在数据集中不可用",
            )
            return None

        return TrainingSelectionResult(
            model_output=model_output,
            regime_result=regime_result,
            trading_plan=trading_plan,
            meeting_log=meeting_log,
            strategy_advice=strategy_advice,
            selected=selected,
            selected_data=selected_data,
            selection_mode=selection_mode,
            agent_used=agent_used,
        )

    def _build_regime_result(self, controller: Any, model_output: Any) -> dict[str, Any]:
        signal_packet = model_output.signal_packet
        agent_context = model_output.agent_context
        routing_snapshot = dict(getattr(controller, "last_routing_decision", {}) or {})
        return {
            "regime": routing_snapshot.get("regime") or signal_packet.regime,
            "confidence": float(
                routing_snapshot.get("regime_confidence")
                or agent_context.metadata.get("confidence", 0.72)
                or 0.72
            ),
            "reasoning": routing_snapshot.get("reasoning") or agent_context.summary,
            "suggested_exposure": max(0.0, min(1.0, 1.0 - float(signal_packet.cash_reserve))),
            "decision_source": routing_snapshot.get("decision_source", "model_output"),
            "params": {
                **dict(signal_packet.params or {}),
                "top_n": max(len(signal_packet.selected_codes), len(signal_packet.signals)),
                "max_positions": signal_packet.max_positions,
                "stop_loss_pct": signal_packet.params.get(
                    "stop_loss_pct",
                    controller.current_params.get(
                        "stop_loss_pct",
                        COMMON_PARAM_DEFAULTS["stop_loss_pct"],
                    ),
                ),
                "take_profit_pct": signal_packet.params.get(
                    "take_profit_pct",
                    controller.current_params.get(
                        "take_profit_pct",
                        COMMON_PARAM_DEFAULTS["take_profit_pct"],
                    ),
                ),
                "position_size": signal_packet.params.get(
                    "position_size",
                    controller.current_params.get(
                        "position_size",
                        COMMON_PARAM_DEFAULTS["position_size"],
                    ),
                ),
            },
        }
