from __future__ import annotations

import logging
from typing import Any

from invest.contracts import EvalReport
from app.training.runtime_discipline import record_learning_proposal

logger = logging.getLogger(__name__)


class TrainingReviewService:
    """Owns review-phase report building and decision application."""

    def build_eval_report(
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
    ) -> EvalReport:
        return EvalReport(
            cycle_id=cycle_id,
            as_of_date=cutoff_date,
            return_pct=sim_result.return_pct,
            total_pnl=sim_result.total_pnl,
            total_trades=sim_result.total_trades,
            win_rate=sim_result.win_rate,
            regime=regime_result.get("regime", "unknown"),
            is_profit=bool(sim_result.return_pct > 0),
            selected_codes=list(selected),
            benchmark_passed=bool(cycle_dict.get("benchmark_passed", False)),
            benchmark_strict_passed=bool(cycle_dict.get("benchmark_strict_passed", False)),
            sharpe_ratio=float(cycle_dict.get("sharpe_ratio", 0.0) or 0.0),
            max_drawdown=float(cycle_dict.get("max_drawdown", 0.0) or 0.0),
            excess_return=float(cycle_dict.get("excess_return", 0.0) or 0.0),
            data_mode=data_mode,
            selection_mode=selection_mode,
            agent_used=bool(agent_used),
            llm_used=bool(llm_used),
            metadata={
                "model_name": getattr(model_output, "model_name", controller.model_name)
                if model_output is not None
                else controller.model_name,
                "config_name": getattr(model_output, "config_name", controller.model_config_path)
                if model_output is not None
                else controller.model_config_path,
                "trade_count": len(trade_dicts),
                "requested_data_mode": requested_data_mode,
                "effective_data_mode": effective_data_mode,
                "llm_mode": llm_mode,
                "degraded": degraded,
                "degrade_reason": degrade_reason,
                "research_feedback": dict(research_feedback or {}),
            },
        )

    def apply_review_decision(
        self,
        controller: Any,
        *,
        cycle_id: int,
        review_decision: dict[str, Any],
        review_event: Any,
    ) -> bool:
        review_applied = False
        proposal_refs: list[str] = []
        if review_decision.get("param_adjustments"):
            sanitize = getattr(controller, "_sanitize_runtime_param_adjustments", None)
            param_adjustments = (
                sanitize(review_decision["param_adjustments"])
                if callable(sanitize)
                else dict(review_decision["param_adjustments"])
            )
            proposal = record_learning_proposal(
                controller,
                source="review.param_adjustment",
                patch=param_adjustments,
                target_scope="candidate",
                rationale=str(review_decision.get("reasoning") or ""),
                evidence={
                    "cycle_id": int(cycle_id),
                    "strategy_suggestions": list(review_decision.get("strategy_suggestions", [])),
                },
                metadata={"proposal_kind": "param_adjustment"},
                cycle_id=cycle_id,
            )
            proposal_refs.append(str(proposal["proposal_id"]))
            review_applied = True
            review_event.applied_change.update(
                {"queued_param_adjustments": dict(param_adjustments)}
            )
            logger.info("根据复盘记录参数提案: %s", param_adjustments)
            controller._emit_agent_status(
                "ReviewMeeting",
                "completed",
                f"参数提案已记录: {list(param_adjustments.keys())}",
                cycle_id=cycle_id,
                stage="review_meeting",
                progress_pct=96,
                step=4,
                total_steps=6,
                details=review_decision,
                adjustments=param_adjustments,
            )

        if review_decision.get("agent_weight_adjustments"):
            proposal = record_learning_proposal(
                controller,
                source="review.agent_weight_adjustment",
                patch=dict(review_decision["agent_weight_adjustments"]),
                target_scope="candidate",
                rationale=str(review_decision.get("reasoning") or ""),
                evidence={"cycle_id": int(cycle_id)},
                metadata={"proposal_kind": "agent_weight_adjustment"},
                cycle_id=cycle_id,
            )
            proposal_refs.append(str(proposal["proposal_id"]))
            review_applied = True
            review_event.applied_change.update(
                {
                    "queued_agent_weight_adjustments": dict(
                        review_decision["agent_weight_adjustments"]
                    )
                }
            )

        if proposal_refs:
            review_event.applied_change["proposal_refs"] = proposal_refs

        return review_applied
