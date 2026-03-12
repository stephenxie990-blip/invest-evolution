from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from invest.leaderboard import write_leaderboard
from invest.models.defaults import COMMON_PARAM_DEFAULTS
from app.training.reporting import (
    build_freeze_report,
    build_self_assessment_snapshot,
    evaluate_freeze_gate,
    evaluate_research_feedback_gate,
    generate_training_report,
    rolling_self_assessment,
    should_freeze as should_freeze_report,
)

logger = logging.getLogger(__name__)


class TrainingFeedbackService:
    @staticmethod
    def feedback_brief(plan: Dict[str, Any] | None = None, *, triggered: bool = False) -> Dict[str, Any]:
        payload = dict(plan or {})
        if not payload:
            return {}
        return {
            "triggered": bool(triggered),
            "trigger": str(payload.get("trigger") or "research_feedback"),
            "bias": str(payload.get("bias") or ""),
            "failed_horizons": list(payload.get("failed_horizons") or []),
            "failed_check_names": list(payload.get("failed_check_names") or []),
            "summary": str(payload.get("summary") or ""),
            "sample_count": int(payload.get("sample_count") or 0),
            "cooldown_cycles": int(payload.get("cooldown_cycles") or 0),
        }

    def load_research_feedback(self, controller: Any, *, cutoff_date: str, model_name: str, config_name: str) -> Dict[str, Any]:
        try:
            feedback = controller.research_case_store.build_training_feedback(
                model_name=model_name,
                config_name=config_name,
                as_of_date=cutoff_date,
                limit=200,
            )
        except Exception:
            logger.debug("research calibration feedback unavailable", exc_info=True)
            feedback = {}
        controller.last_research_feedback = dict(feedback or {})
        return controller.last_research_feedback

    def build_feedback_optimization_plan(self, controller: Any, feedback: Dict[str, Any] | None, *, cycle_id: int) -> Dict[str, Any]:
        payload = dict(feedback or {})
        evaluation = evaluate_research_feedback_gate(
            payload,
            policy=controller.research_feedback_optimization_policy,
            defaults={
                "min_sample_count": 5,
                "blocked_biases": ["tighten_risk", "recalibrate_probability"],
                "max_brier_like_direction_score": 0.28,
                "horizons": {
                    "default": {
                        "min_hit_rate": 0.45,
                        "max_invalidation_rate": 0.35,
                        "min_interval_hit_rate": 0.40,
                    }
                },
            },
        )
        if not evaluation.get("active") or evaluation.get("passed", True):
            return {}

        cooldown_cycles = int(controller.research_feedback_optimization_policy.get("cooldown_cycles", 3) or 3)
        if controller.last_feedback_optimization_cycle_id and cycle_id - controller.last_feedback_optimization_cycle_id < cooldown_cycles:
            return {}

        bias = str(evaluation.get("bias") or dict(payload.get("recommendation") or {}).get("bias") or "maintain")
        failed_checks = list(evaluation.get("failed_checks") or [])
        failed_horizons = sorted({str(item.get("horizon") or "").strip() for item in failed_checks if str(item.get("horizon") or "").strip()})
        fail_count = max(1, len(failed_checks))
        severity = min(3.0, 1.0 + 0.30 * max(0, fail_count - 1) + (0.35 if bias == "tighten_risk" else 0.20))

        current_position = float(controller.current_params.get("position_size", COMMON_PARAM_DEFAULTS["position_size"]) or COMMON_PARAM_DEFAULTS["position_size"])
        current_stop = float(controller.current_params.get("stop_loss_pct", COMMON_PARAM_DEFAULTS["stop_loss_pct"]) or COMMON_PARAM_DEFAULTS["stop_loss_pct"])
        current_take_profit = float(controller.current_params.get("take_profit_pct", COMMON_PARAM_DEFAULTS["take_profit_pct"]) or COMMON_PARAM_DEFAULTS["take_profit_pct"])
        current_cash = float(controller.current_params.get("cash_reserve", COMMON_PARAM_DEFAULTS["cash_reserve"]) or COMMON_PARAM_DEFAULTS["cash_reserve"])
        current_trailing = float(controller.current_params.get("trailing_pct", COMMON_PARAM_DEFAULTS["trailing_pct"]) or COMMON_PARAM_DEFAULTS["trailing_pct"])

        raw_adjustments: Dict[str, Any] = {
            "position_size": current_position * max(0.60, 1.0 - 0.10 * severity),
            "cash_reserve": current_cash + 0.04 + 0.02 * min(severity, 2.0),
        }
        suggestions = [
            f"ask校准在 {', '.join(failed_horizons) if failed_horizons else '多周期'} 上显示风险偏高，先自动收紧风险暴露",
        ]
        if bias == "tighten_risk":
            raw_adjustments["stop_loss_pct"] = current_stop * max(0.72, 1.0 - 0.08 * severity)
            raw_adjustments["trailing_pct"] = current_trailing * max(0.78, 1.0 - 0.05 * severity)
            suggestions.append("优先收紧止损、跟踪止盈与仓位")
        elif bias == "recalibrate_probability":
            raw_adjustments["take_profit_pct"] = current_take_profit * 0.95
            suggestions.append("优先下调仓位并收紧概率兑现预期")

        param_adjustments = controller._sanitize_runtime_param_adjustments(raw_adjustments)
        if not param_adjustments:
            return {}

        recommendation = dict(payload.get("recommendation") or {})
        summary = str(recommendation.get("summary") or "research feedback optimization")
        return {
            "trigger": "research_feedback",
            "bias": bias,
            "summary": summary,
            "sample_count": int(payload.get("sample_count") or 0),
            "recommendation": recommendation,
            "failed_horizons": failed_horizons,
            "failed_check_names": [str(item.get("name") or "") for item in failed_checks if str(item.get("name") or "")],
            "cooldown_cycles": cooldown_cycles,
            "evaluation": evaluation,
            "param_adjustments": param_adjustments,
            "scoring_adjustments": {},
            "suggestions": suggestions,
        }


class FreezeGateService:
    def rolling_self_assessment(self, controller: Any, window: Optional[int] = None) -> Dict[str, Any]:
        return rolling_self_assessment(controller.assessment_history, controller.freeze_total_cycles, window=window)

    def evaluate_freeze_gate(self, controller: Any, rolling: Dict[str, Any] | None = None) -> Dict[str, Any]:
        active_rolling = dict(rolling or controller._rolling_self_assessment(controller.freeze_total_cycles) or {})
        evaluation = evaluate_freeze_gate(
            controller.cycle_history,
            controller.freeze_total_cycles,
            controller.freeze_profit_required,
            controller.freeze_gate_policy,
            active_rolling,
            research_feedback=controller.last_research_feedback,
        )
        controller.last_freeze_gate_evaluation = dict(evaluation or {})
        return controller.last_freeze_gate_evaluation

    def should_freeze(self, controller: Any) -> bool:
        rolling = controller._rolling_self_assessment(controller.freeze_total_cycles)
        controller.last_freeze_gate_evaluation = self.evaluate_freeze_gate(controller, rolling)
        return should_freeze_report(
            controller.cycle_history,
            controller.freeze_total_cycles,
            controller.freeze_profit_required,
            controller.freeze_gate_policy,
            rolling,
            research_feedback=controller.last_research_feedback,
        )

    def freeze_model(self, controller: Any) -> Dict[str, Any]:
        logger.info(f"\n{'='*50}\n🎉 模型固化！\n{'='*50}")

        rolling = controller._rolling_self_assessment(controller.freeze_total_cycles)
        report = build_freeze_report(
            controller.cycle_history,
            controller.current_params,
            controller.freeze_total_cycles,
            controller.freeze_profit_required,
            controller.freeze_gate_policy,
            rolling,
            research_feedback=controller.last_research_feedback,
        )
        controller.last_freeze_gate_evaluation = dict(report.get("freeze_gate_evaluation") or {})

        path = Path(controller.output_dir) / "model_frozen.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        logger.info("固化报告: %s", path)
        return report

    def generate_training_report(self, controller: Any) -> Dict[str, Any]:
        rolling = controller._rolling_self_assessment(controller.freeze_total_cycles)
        freeze_gate_evaluation = self.evaluate_freeze_gate(controller, rolling)
        return generate_training_report(
            controller.total_cycle_attempts,
            controller.skipped_cycle_count,
            controller.cycle_history,
            controller.current_params,
            bool(freeze_gate_evaluation.get("passed")),
            rolling,
            research_feedback=controller.last_research_feedback,
            freeze_gate_evaluation=freeze_gate_evaluation,
        )


class TrainingPersistenceService:
    def record_self_assessment(self, controller: Any, snapshot_factory: Any, cycle_result: Any, cycle_dict: Dict[str, Any]) -> None:
        snapshot = build_self_assessment_snapshot(snapshot_factory, cycle_result, cycle_dict)
        controller.assessment_history.append(snapshot)

    def save_cycle_result(self, controller: Any, result: Any) -> None:
        path = Path(controller.output_dir) / f"cycle_{result.cycle_id}.json"

        def _bool(value: Any) -> bool:
            return bool(value)

        def _jsonable(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: _jsonable(item) for key, item in value.items()}
            if isinstance(value, list):
                return [_jsonable(item) for item in value]
            if isinstance(value, tuple):
                return [_jsonable(item) for item in value]
            if isinstance(value, np.generic):
                return value.item()
            return value

        scoring_changed_keys: list[str] = []
        scoring_mutation_count = 0
        for event in result.optimization_events:
            applied = dict(event.get("applied_change") or {})
            scoring = dict(applied.get("scoring") or {})
            if scoring:
                scoring_mutation_count += 1
                for section_name, section_values in scoring.items():
                    if isinstance(section_values, dict):
                        for key in section_values.keys():
                            scoring_changed_keys.append(f"{section_name}.{key}")

        data = {
            "cycle_id": result.cycle_id,
            "cutoff_date": result.cutoff_date,
            "selected_stocks": result.selected_stocks,
            "initial_capital": result.initial_capital,
            "final_value": result.final_value,
            "return_pct": result.return_pct,
            "is_profit": _bool(result.is_profit),
            "params": result.params,
            "trade_count": len(result.trade_history),
            "trades": _jsonable(result.trade_history),
            "analysis": result.analysis,
            "data_mode": result.data_mode,
            "requested_data_mode": result.requested_data_mode,
            "effective_data_mode": result.effective_data_mode,
            "llm_mode": result.llm_mode,
            "degraded": _bool(result.degraded),
            "degrade_reason": result.degrade_reason,
            "selection_mode": result.selection_mode,
            "agent_used": _bool(result.agent_used),
            "llm_used": _bool(result.llm_used),
            "benchmark_passed": _bool(result.benchmark_passed),
            "strategy_scores": _jsonable(dict(result.strategy_scores or {})),
            "review_applied": _bool(result.review_applied),
            "config_snapshot_path": result.config_snapshot_path,
            "optimization_events": _jsonable(result.optimization_events),
            "audit_tags": _jsonable({key: _bool(value) if isinstance(value, (bool, np.bool_)) else value for key, value in result.audit_tags.items()}),
            "model_name": result.model_name,
            "config_name": result.config_name,
            "routing_decision": _jsonable(dict(result.routing_decision or {})),
            "allocation_plan": _jsonable((result.routing_decision or {}).get("allocation_plan") or getattr(controller, "last_allocation_plan", {}) or {}),
            "research_feedback": _jsonable(dict(result.research_feedback or {})),
            "scoring_mutation_count": scoring_mutation_count,
            "scoring_changed_keys": sorted(set(scoring_changed_keys)),
        }
        snapshot = next((item for item in controller.assessment_history if item.cycle_id == result.cycle_id), None)
        if snapshot:
            data["self_assessment"] = {
                "regime": snapshot.regime,
                "plan_source": snapshot.plan_source,
                "sharpe_ratio": snapshot.sharpe_ratio,
                "max_drawdown": snapshot.max_drawdown,
                "excess_return": snapshot.excess_return,
                "benchmark_passed": _bool(snapshot.benchmark_passed),
            }
        if result.strategy_scores:
            data.setdefault("self_assessment", {})
            data["self_assessment"].update(
                {
                    "signal_accuracy": float(result.strategy_scores.get("signal_accuracy", 0.0) or 0.0),
                    "timing_score": float(result.strategy_scores.get("timing_score", 0.0) or 0.0),
                    "risk_control_score": float(result.strategy_scores.get("risk_control_score", 0.0) or 0.0),
                    "overall_score": float(result.strategy_scores.get("overall_score", 0.0) or 0.0),
                }
            )
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        leaderboard_root = Path(controller.output_dir).parent
        try:
            write_leaderboard(leaderboard_root)
        except Exception:
            logger.debug("leaderboard update failed", exc_info=True)
