from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from config import normalize_date
from invest.leaderboard import write_leaderboard
from invest.models import resolve_model_config_path
from invest.models.defaults import COMMON_PARAM_DEFAULTS
from app.training.experiment_protocol import ExperimentSpec
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


class TrainingLLMRuntimeService:
    """Coordinates controller-wide LLM runtime settings."""

    @staticmethod
    def _iter_unique_llms(controller: Any) -> list[Any]:
        targets = [getattr(controller, "llm_caller", None)]
        for agent in dict(getattr(controller, "agents", {}) or {}).values():
            llm = getattr(agent, "llm", None)
            if llm is not None:
                targets.append(llm)
        for component in (
            getattr(controller, "selection_meeting", None),
            getattr(controller, "review_meeting", None),
            getattr(controller, "llm_optimizer", None),
        ):
            llm = getattr(component, "llm", None)
            if llm is not None:
                targets.append(llm)

        seen: set[int] = set()
        unique_targets: list[Any] = []
        for llm in targets:
            if llm is None or id(llm) in seen:
                continue
            seen.add(id(llm))
            unique_targets.append(llm)
        return unique_targets

    def apply_experiment_overrides(
        self,
        controller: Any,
        llm_spec: Dict[str, Any] | None = None,
    ) -> None:
        payload = dict(llm_spec or {})
        timeout = payload.get("timeout")
        max_retries = payload.get("max_retries")
        dry_run = payload.get("dry_run")

        for llm in self._iter_unique_llms(controller):
            if hasattr(llm, "apply_runtime_limits"):
                llm.apply_runtime_limits(timeout=timeout, max_retries=max_retries)
            if dry_run is not None and hasattr(llm, "dry_run"):
                llm.dry_run = bool(dry_run)

    def set_dry_run(self, controller: Any, enabled: bool = True) -> None:
        dry_run = bool(enabled)
        controller.llm_mode = "dry_run" if dry_run else "live"
        for llm in self._iter_unique_llms(controller):
            if hasattr(llm, "dry_run"):
                llm.dry_run = dry_run


class TrainingExperimentService:
    """Applies experiment protocol, dataset, model-scope, and LLM overrides."""

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None or not str(value).strip():
            return None
        return int(value)

    @staticmethod
    def _optional_normalized_date(value: Any) -> str | None:
        if not value:
            return None
        return normalize_date(str(value))

    def configure_experiment(self, controller: Any, spec: Dict[str, Any] | None = None) -> None:
        normalized_spec = ExperimentSpec.from_payload(spec)
        payload = normalized_spec.to_payload()
        controller.experiment_protocol = payload
        controller.experiment_spec = payload
        protocol = dict(payload.get("protocol") or {})
        dataset = dict(payload.get("dataset") or {})
        model_scope = dict(payload.get("model_scope") or {})
        llm = dict(payload.get("llm") or {})

        seed = protocol.get("seed")
        date_range = dict(protocol.get("date_range") or {})
        controller.experiment_seed = self._optional_int(seed)
        controller.experiment_min_date = self._optional_normalized_date(
            date_range.get("min") or protocol.get("min_date")
        )
        controller.experiment_max_date = self._optional_normalized_date(
            date_range.get("max") or protocol.get("max_date")
        )
        controller.experiment_min_history_days = self._optional_int(dataset.get("min_history_days"))
        controller.experiment_simulation_days = self._optional_int(dataset.get("simulation_days"))
        controller.experiment_cutoff_policy = dict(
            protocol.get("cutoff_policy") or normalized_spec.cutoff_policy
        )
        controller.experiment_review_window = dict(protocol.get("review_window") or normalized_spec.review_window)
        controller.experiment_promotion_policy = dict(
            protocol.get("promotion_policy")
            or normalized_spec.promotion_policy
            or getattr(controller, "promotion_gate_policy", {})
        )

        allowed_models = model_scope.get("allowed_models") or []
        controller.experiment_allowed_models = [
            str(name) for name in allowed_models if str(name).strip()
        ]
        controller.experiment_llm = llm
        controller._apply_experiment_llm_overrides(llm)

        if model_scope.get("allocator_enabled") is not None:
            enabled = bool(model_scope.get("allocator_enabled"))
            controller.allocator_enabled = enabled
            controller.model_routing_enabled = enabled
        if model_scope.get("model_routing_enabled") is not None:
            controller.model_routing_enabled = bool(model_scope.get("model_routing_enabled"))
        if model_scope.get("routing_mode") is not None:
            controller.model_routing_mode = (
                str(model_scope.get("routing_mode") or "rule").strip().lower() or "rule"
            )
        if controller.experiment_allowed_models:
            controller.model_routing_allowed_models = list(controller.experiment_allowed_models)
        if model_scope.get("switch_cooldown_cycles") is not None:
            controller.model_switch_cooldown_cycles = int(
                model_scope.get("switch_cooldown_cycles") or 0
            )
        if model_scope.get("switch_min_confidence") is not None:
            controller.model_switch_min_confidence = float(
                model_scope.get("switch_min_confidence") or 0.0
            )
        if model_scope.get("switch_hysteresis_margin") is not None:
            controller.model_switch_hysteresis_margin = float(
                model_scope.get("switch_hysteresis_margin") or 0.0
            )
        if model_scope.get("agent_override_enabled") is not None:
            controller.model_routing_agent_override_enabled = bool(
                model_scope.get("agent_override_enabled")
            )
        if model_scope.get("agent_override_max_gap") is not None:
            controller.model_routing_agent_override_max_gap = float(
                model_scope.get("agent_override_max_gap") or 0.0
            )

        controller._refresh_model_routing_coordinator()
        if (
            controller.experiment_allowed_models
            and controller.model_name not in controller.experiment_allowed_models
        ):
            controller.model_name = controller.experiment_allowed_models[0]
            controller.model_config_path = str(resolve_model_config_path(controller.model_name))
            controller.current_params = {}
            controller._reload_investment_model(controller.model_config_path)


class TrainingFeedbackService:
    @staticmethod
    def research_feedback_summary(
        feedback: Dict[str, Any] | None = None,
        *,
        source: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        payload = dict(feedback or {})
        recommendation = dict(payload.get("recommendation") or {})
        t20 = dict(payload.get("horizons") or {}).get("T+20") or {}
        scope = dict(payload.get("scope") or {})
        return {
            "available": bool(payload),
            "source": dict(source or {}),
            "sample_count": int(payload.get("sample_count") or 0),
            "bias": str(recommendation.get("bias") or "unknown"),
            "summary": str(recommendation.get("summary") or ""),
            "brier_like_direction_score": payload.get("brier_like_direction_score"),
            "t20_hit_rate": t20.get("hit_rate"),
            "t20_invalidation_rate": t20.get("invalidation_rate"),
            "available_horizons": sorted((payload.get("horizons") or {}).keys()),
            "effective_scope": str(scope.get("effective_scope") or "overall"),
            "requested_regime": str(scope.get("requested_regime") or ""),
        }

    @staticmethod
    def research_feedback_brief(feedback: Dict[str, Any] | None = None) -> Dict[str, Any]:
        summary = TrainingFeedbackService.research_feedback_summary(feedback)
        return {
            "sample_count": int(summary.get("sample_count") or 0),
            "bias": str(summary.get("bias") or "unknown"),
            "brier_like_direction_score": summary.get("brier_like_direction_score"),
            "t20_hit_rate": summary.get("t20_hit_rate"),
        }

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

    def load_research_feedback(self, controller: Any, *, cutoff_date: str, model_name: str, config_name: str, regime: str = "") -> Dict[str, Any]:
        try:
            feedback = controller.research_case_store.build_training_feedback(
                model_name=model_name,
                config_name=config_name,
                as_of_date=cutoff_date,
                regime=regime,
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
        benchmark_window = min(10, max(3, int(getattr(controller, "freeze_total_cycles", 10) or 10)))
        rolling = controller.freeze_gate_service.rolling_self_assessment(
            controller,
            window=benchmark_window,
        )
        benchmark_required = float(
            controller.research_feedback_optimization_policy.get(
                "benchmark_pass_rate_gte",
                controller.freeze_gate_policy.get("benchmark_pass_rate_gte", 0.60),
            )
            or controller.freeze_gate_policy.get("benchmark_pass_rate_gte", 0.60)
            or 0.60
        )
        benchmark_pass_rate = float(rolling.get("benchmark_pass_rate", benchmark_required) or benchmark_required)
        benchmark_gap = max(0.0, benchmark_required - benchmark_pass_rate)
        severity = min(
            3.4,
            1.0
            + 0.30 * max(0, fail_count - 1)
            + (0.40 if bias == "tighten_risk" else 0.20)
            + min(0.90, benchmark_gap * 2.5),
        )

        current_position = float(controller.current_params.get("position_size", COMMON_PARAM_DEFAULTS["position_size"]) or COMMON_PARAM_DEFAULTS["position_size"])
        current_stop = float(controller.current_params.get("stop_loss_pct", COMMON_PARAM_DEFAULTS["stop_loss_pct"]) or COMMON_PARAM_DEFAULTS["stop_loss_pct"])
        current_take_profit = float(controller.current_params.get("take_profit_pct", COMMON_PARAM_DEFAULTS["take_profit_pct"]) or COMMON_PARAM_DEFAULTS["take_profit_pct"])
        current_cash = float(controller.current_params.get("cash_reserve", COMMON_PARAM_DEFAULTS["cash_reserve"]) or COMMON_PARAM_DEFAULTS["cash_reserve"])
        current_trailing = float(controller.current_params.get("trailing_pct", COMMON_PARAM_DEFAULTS["trailing_pct"]) or COMMON_PARAM_DEFAULTS["trailing_pct"])
        current_hold_days = int(
            controller.current_params.get("max_hold_days", COMMON_PARAM_DEFAULTS["max_hold_days"])
            or COMMON_PARAM_DEFAULTS["max_hold_days"]
        )
        current_signal_threshold = controller.current_params.get("signal_threshold")

        raw_adjustments: Dict[str, Any] = {
            "position_size": current_position * max(0.45, 1.0 - 0.16 * severity),
            "cash_reserve": current_cash + 0.05 + 0.03 * min(severity, 3.0),
            "max_hold_days": current_hold_days - max(4, int(round(3 * severity + benchmark_gap * 12))),
        }
        suggestions = [
            f"ask校准在 {', '.join(failed_horizons) if failed_horizons else '多周期'} 上显示风险偏高，先自动收紧风险暴露",
        ]
        if benchmark_gap > 0:
            suggestions.append(
                f"近窗 benchmark 通过率 {benchmark_pass_rate:.0%} 低于目标 {benchmark_required:.0%}，提高信号门槛并缩短持有周期"
            )
        if bias == "tighten_risk":
            raw_adjustments["stop_loss_pct"] = current_stop * max(0.55, 1.0 - 0.12 * severity)
            raw_adjustments["trailing_pct"] = current_trailing * max(0.60, 1.0 - 0.10 * severity)
            raw_adjustments["take_profit_pct"] = current_take_profit * max(0.82, 1.0 - 0.06 * severity)
            suggestions.append("优先收紧止损、跟踪止盈与仓位")
        elif bias == "recalibrate_probability":
            raw_adjustments["take_profit_pct"] = current_take_profit * max(0.85, 1.0 - 0.05 * severity)
            suggestions.append("优先下调仓位并收紧概率兑现预期")
        if current_signal_threshold is not None:
            raw_adjustments["signal_threshold"] = float(current_signal_threshold) + 0.015 + 0.02 * severity + 0.10 * benchmark_gap

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
            "severity": round(severity, 4),
            "benchmark_context": {
                "window": benchmark_window,
                "current_pass_rate": round(benchmark_pass_rate, 4),
                "required_pass_rate": round(benchmark_required, 4),
                "gap": round(benchmark_gap, 4),
            },
            "failed_horizons": failed_horizons,
            "failed_check_names": [str(item.get("name") or "") for item in failed_checks if str(item.get("name") or "")],
            "cooldown_cycles": cooldown_cycles,
            "evaluation": evaluation,
            "param_adjustments": param_adjustments,
            "scoring_adjustments": {},
            "suggestions": suggestions,
        }


class FreezeGateService:
    @staticmethod
    def _overridden_rolling_hook(controller: Any) -> Any | None:
        hook = getattr(controller, "_rolling_self_assessment", None)
        if not callable(hook):
            return None
        class_hook = getattr(type(controller), "_rolling_self_assessment", None)
        bound_func = getattr(hook, "__func__", None)
        if bound_func is class_hook:
            return None
        return hook

    def _resolve_rolling(self, controller: Any, rolling: Dict[str, Any] | None = None) -> Dict[str, Any]:
        hook = self._overridden_rolling_hook(controller)
        if hook is not None:
            return dict(hook(controller.freeze_total_cycles) or {})
        return dict(
            rolling
            or self.rolling_self_assessment(controller, window=controller.freeze_total_cycles)
            or {}
        )

    def rolling_self_assessment(self, controller: Any, window: Optional[int] = None) -> Dict[str, Any]:
        return rolling_self_assessment(controller.assessment_history, controller.freeze_total_cycles, window=window)

    def evaluate_freeze_gate(self, controller: Any, rolling: Dict[str, Any] | None = None) -> Dict[str, Any]:
        active_rolling = self._resolve_rolling(controller, rolling)
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
        rolling = self._resolve_rolling(controller)
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

        rolling = self._resolve_rolling(controller)
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
        rolling = self._resolve_rolling(controller)
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

    def refresh_leaderboards(self, controller: Any) -> None:
        run_root = Path(controller.output_dir)
        aggregate_root = run_root.parent
        leaderboard_policy = {
            "quality_gate_matrix": dict(getattr(controller, "quality_gate_matrix", {}) or {}),
            "train": {
                "promotion_gate": dict(getattr(controller, "promotion_gate_policy", {}) or {}),
                "freeze_gate": dict(getattr(controller, "freeze_gate_policy", {}) or {}),
                "quality_gate_matrix": dict(getattr(controller, "quality_gate_matrix", {}) or {}),
            },
        }
        write_leaderboard(
            run_root,
            run_root / "leaderboard.json",
            policy=leaderboard_policy,
        )
        if aggregate_root != run_root:
            write_leaderboard(
                aggregate_root,
                aggregate_root / "leaderboard.json",
                policy=leaderboard_policy,
            )

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
            "research_artifacts": _jsonable(dict(result.research_artifacts or {})),
            "ab_comparison": _jsonable(dict(result.ab_comparison or {})),
            "experiment_spec": _jsonable(dict(result.experiment_spec or {})),
            "execution_snapshot": _jsonable(dict(result.execution_snapshot or {})),
            "run_context": _jsonable(dict(result.run_context or {})),
            "promotion_record": _jsonable(dict(result.promotion_record or {})),
            "lineage_record": _jsonable(dict(result.lineage_record or {})),
            "review_decision": _jsonable(dict(result.review_decision or {})),
            "causal_diagnosis": _jsonable(dict(result.causal_diagnosis or {})),
            "similarity_summary": _jsonable(dict(result.similarity_summary or {})),
            "similar_results": _jsonable(list(result.similar_results or [])),
            "realism_metrics": _jsonable(dict(result.realism_metrics or {})),
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
        try:
            self.refresh_leaderboards(controller)
        except Exception:
            logger.debug("leaderboard update failed", exc_info=True)
