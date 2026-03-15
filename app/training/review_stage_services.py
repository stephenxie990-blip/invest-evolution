from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.training.experiment_protocol import build_review_basis_window
from app.training.review_protocol import build_review_input
from invest.shared.model_governance import build_optimization_event_lineage, normalize_config_ref


@dataclass(frozen=True)
class TrainingReviewStageResult:
    eval_report: Any
    review_decision: dict[str, Any]
    review_applied: bool
    review_event: Any


class TrainingReviewStageService:
    """Owns review-stage orchestration around eval reports and review decisions."""

    def run_review_stage(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        sim_result: Any,
        regime_result: dict[str, Any],
        selected: list[str],
        cycle_dict: dict[str, Any],
        trade_dicts: list[dict[str, Any]],
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        data_mode: str,
        selection_mode: str,
        agent_used: bool,
        llm_used: bool,
        model_output: Any | None,
        research_feedback: dict[str, Any] | None,
        optimization_event_factory: Any,
    ) -> TrainingReviewStageResult:
        controller._emit_agent_status(
            "ReviewMeeting",
            "running",
            "复盘会议自省中...",
            cycle_id=cycle_id,
            stage="review_meeting",
            progress_pct=84,
            step=4,
            total_steps=6,
        )
        controller._emit_module_log(
            "review",
            "进入复盘会议",
            "开始汇总交易表现与策略偏差",
            cycle_id=cycle_id,
            kind="phase_start",
        )
        eval_report = controller.training_review_service.build_eval_report(
            controller,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            sim_result=sim_result,
            regime_result=regime_result,
            selected=selected,
            cycle_dict=cycle_dict,
            trade_dicts=trade_dicts,
            requested_data_mode=requested_data_mode,
            effective_data_mode=effective_data_mode,
            llm_mode=llm_mode,
            degraded=degraded,
            degrade_reason=degrade_reason,
            data_mode=data_mode,
            selection_mode=selection_mode,
            agent_used=agent_used,
            llm_used=llm_used,
            model_output=model_output,
            research_feedback=research_feedback,
        )
        controller.cycle_records.append(cycle_dict)
        agent_accuracy = controller.agent_tracker.compute_accuracy(last_n_cycles=20)
        review_input = build_review_input(
            controller,
            cycle_id=cycle_id,
            eval_report=eval_report,
        )
        review_decision = controller.review_meeting_service.run_with_eval_report(
            eval_report,
            agent_accuracy=agent_accuracy,
            current_params=controller.current_params,
            recent_results=review_input["recent_results"],
            review_basis_window=review_input["review_basis_window"],
            similar_results=review_input["similar_results"],
            similarity_summary=review_input["similarity_summary"],
            causal_diagnosis=review_input["causal_diagnosis"],
        )
        review_facts = getattr(controller.review_meeting, "last_facts", None) or cycle_dict
        controller.meeting_recorder.save_review(review_decision, review_facts, cycle_id)

        review_event = optimization_event_factory(
            cycle_id=cycle_id,
            trigger="review_meeting",
            stage="review_decision",
            decision={
                "strategy_suggestions": review_decision.get("strategy_suggestions", []),
                "param_adjustments": review_decision.get("param_adjustments", {}),
                "agent_weight_adjustments": review_decision.get("agent_weight_adjustments", {}),
            },
            applied_change={},
            lineage=build_optimization_event_lineage(
                cycle_id=cycle_id,
                model_name=str(
                    getattr(model_output, "model_name", "")
                    or getattr(controller, "model_name", "")
                    or ""
                ),
                active_config_ref=normalize_config_ref(
                    getattr(model_output, "config_name", "")
                    or getattr(controller, "model_config_path", "")
                    or ""
                ),
                candidate_config_ref="",
                promotion_status="not_evaluated",
                deployment_stage="active",
                review_basis_window=build_review_basis_window(
                    controller,
                    cycle_id=cycle_id,
                    review_window=dict(getattr(controller, "experiment_review_window", {}) or {}),
                ),
                fitness_source_cycles=[],
                runtime_override_keys=[],
            ),
            evidence={
                "return_pct": float(getattr(eval_report, "return_pct", 0.0) or 0.0),
                "benchmark_passed": bool(getattr(eval_report, "benchmark_passed", False)),
                "strategy_suggestion_count": len(review_decision.get("strategy_suggestions", [])),
                "param_adjustment_count": len(review_decision.get("param_adjustments", {})),
                "agent_weight_adjustment_count": len(review_decision.get("agent_weight_adjustments", {})),
            },
            notes=review_decision.get("reasoning", ""),
        )
        review_applied = controller.training_review_service.apply_review_decision(
            controller,
            cycle_id=cycle_id,
            review_decision=review_decision,
            review_event=review_event,
        )
        review_event.lineage = build_optimization_event_lineage(
            cycle_id=cycle_id,
            model_name=str(
                getattr(model_output, "model_name", "")
                or getattr(controller, "model_name", "")
                or ""
            ),
            active_config_ref=normalize_config_ref(
                getattr(model_output, "config_name", "")
                or getattr(controller, "model_config_path", "")
                or ""
            ),
            candidate_config_ref="",
            promotion_status="override_pending" if review_applied else "not_evaluated",
            deployment_stage="override" if review_applied else "active",
            review_basis_window=build_review_basis_window(
                controller,
                cycle_id=cycle_id,
                review_window=dict(getattr(controller, "experiment_review_window", {}) or {}),
            ),
            fitness_source_cycles=[],
            runtime_override_keys=sorted(
                {
                    *(
                        str(key)
                        for key in dict(
                            getattr(review_event, "applied_change", {}).get("params") or {}
                        ).keys()
                    ),
                    *(
                        str(key)
                        for key in dict(
                            getattr(review_event, "applied_change", {}).get("agent_weights") or {}
                        ).keys()
                    ),
                }
            ),
        )
        cycle_dict["review_applied"] = review_applied
        append_event = getattr(controller, "_append_optimization_event", None)
        if callable(append_event):
            append_event(review_event)
        controller._emit_module_log(
            "review",
            "复盘会议结论",
            review_decision.get("reasoning", "复盘完成"),
            cycle_id=cycle_id,
            kind="review_decision",
            details={
                "strategy_suggestions": review_decision.get("strategy_suggestions", []),
                "param_adjustments": review_decision.get("param_adjustments", {}),
                "agent_weight_adjustments": review_decision.get("agent_weight_adjustments", {}),
            },
            metrics={
                "review_applied": review_applied,
                "suggestion_count": len(review_decision.get("strategy_suggestions", [])),
            },
        )
        return TrainingReviewStageResult(
            eval_report=eval_report,
            review_decision=review_decision,
            review_applied=review_applied,
            review_event=review_event,
        )
