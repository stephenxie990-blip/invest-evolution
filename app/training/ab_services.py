from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from config import config
from invest.foundation.engine.simulator import SimulatedTrader
from invest.models import create_investment_model
from invest.models.defaults import COMMON_EXECUTION_DEFAULTS, COMMON_PARAM_DEFAULTS

logger = logging.getLogger(__name__)


def _selection_overlap_ratio(left: list[str], right: list[str]) -> float | None:
    left_set = {str(item).strip() for item in list(left or []) if str(item).strip()}
    right_set = {str(item).strip() for item in list(right or []) if str(item).strip()}
    union = left_set | right_set
    if not union:
        return None
    return round(len(left_set & right_set) / len(union), 4)


class TrainingABService:
    """Runs side-effect-free candidate-vs-active comparative training."""

    def _build_trader(
        self,
        controller: Any,
        *,
        model: Any,
        selected_data: dict[str, Any],
        trading_plan: Any,
    ) -> SimulatedTrader:
        runtime_owner = SimpleNamespace(
            execution_policy={
                **dict(getattr(controller, "execution_policy", {}) or {}),
                "initial_capital": float(
                    model.execution_param(
                        "initial_capital",
                        getattr(config, "initial_capital", COMMON_EXECUTION_DEFAULTS["initial_capital"]),
                    )
                    or getattr(config, "initial_capital", COMMON_EXECUTION_DEFAULTS["initial_capital"])
                ),
                "commission_rate": float(
                    model.execution_param(
                        "commission_rate",
                        COMMON_EXECUTION_DEFAULTS["commission_rate"],
                    )
                    or COMMON_EXECUTION_DEFAULTS["commission_rate"]
                ),
                "stamp_tax_rate": float(
                    model.execution_param(
                        "stamp_tax_rate",
                        COMMON_EXECUTION_DEFAULTS["stamp_tax_rate"],
                    )
                    or COMMON_EXECUTION_DEFAULTS["stamp_tax_rate"]
                ),
                "slippage_rate": float(
                    model.execution_param(
                        "slippage_rate",
                        COMMON_EXECUTION_DEFAULTS["slippage_rate"],
                    )
                    or COMMON_EXECUTION_DEFAULTS["slippage_rate"]
                ),
            },
            current_params={
                **dict(getattr(controller, "current_params", {}) or {}),
                "position_size": float(
                    model.param(
                        "position_size",
                        COMMON_PARAM_DEFAULTS["position_size"],
                    )
                    or COMMON_PARAM_DEFAULTS["position_size"]
                ),
            },
            risk_policy={
                **dict(getattr(controller, "risk_policy", {}) or {}),
                **dict(model.config_section("risk", {}) or {}),
                "stop_loss_pct": float(model.risk_param("stop_loss_pct") or 0.0),
                "take_profit_pct": float(model.risk_param("take_profit_pct") or 0.0),
                "trailing_pct": model.risk_param("trailing_pct"),
            },
        )
        return controller.training_simulation_service.build_trader(
            runtime_owner,
            selected_data=selected_data,
            trading_plan=trading_plan,
        )

    def _evaluate_arm(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
        model_name: str,
        config_ref: str,
        arm_name: str,
    ) -> dict[str, Any]:
        try:
            runtime_model = create_investment_model(
                model_name,
                config_path=config_ref,
                runtime_overrides={},
            )
        except Exception as exc:
            logger.warning("A/B arm init failed for %s (%s): %s", arm_name, config_ref, exc)
            return {
                "status": "error",
                "arm": arm_name,
                "config_ref": str(config_ref or ""),
                "error": str(exc),
            }

        try:
            model_output = runtime_model.process(stock_data, cutoff_date)
            meeting_data = controller.selection_meeting_service.run_with_model_output(model_output)
            trading_plan = meeting_data["trading_plan"]
            selected = [position.code for position in list(getattr(trading_plan, "positions", []) or [])]
            selected_data = {code: stock_data[code] for code in selected if code in stock_data}
            if not selected or not selected_data:
                return {
                    "status": "no_selection",
                    "arm": arm_name,
                    "model_name": str(getattr(model_output, "model_name", model_name) or model_name),
                    "config_name": str(getattr(model_output, "config_name", "") or ""),
                    "config_ref": str(config_ref or ""),
                    "selected_stocks": list(selected),
                    "selection_mode": str(getattr(trading_plan, "source", "") or "meeting_empty"),
                    "regime": str(getattr(model_output.signal_packet, "regime", "unknown") or "unknown"),
                }

            trader = self._build_trader(
                controller,
                model=runtime_model,
                selected_data=selected_data,
                trading_plan=trading_plan,
            )
            simulation_days = max(
                1,
                int(controller.experiment_simulation_days or getattr(config, "simulation_days", 30)),
            )
            trading_dates = controller.training_simulation_service.resolve_trading_dates(
                selected_data=selected_data,
                cutoff_date=cutoff_date,
                simulation_days=simulation_days,
            )
            if len(trading_dates) < simulation_days:
                return {
                    "status": "insufficient_future_days",
                    "arm": arm_name,
                    "model_name": str(getattr(model_output, "model_name", model_name) or model_name),
                    "config_name": str(getattr(model_output, "config_name", "") or ""),
                    "config_ref": str(config_ref or ""),
                    "selected_stocks": list(selected),
                    "selection_mode": str(getattr(trading_plan, "source", "") or "meeting_selection"),
                    "regime": str(getattr(model_output.signal_packet, "regime", "unknown") or "unknown"),
                    "available_trading_days": len(trading_dates),
                    "required_trading_days": simulation_days,
                }

            benchmark_daily_values, market_index_frame = controller.training_simulation_service.build_benchmark_context(
                controller,
                cutoff_date=cutoff_date,
                trading_dates=trading_dates,
            )
            if market_index_frame is not None and not market_index_frame.empty:
                trader.set_market_index_data(market_index_frame)
            sim_result = trader.run_simulation(trading_dates[0], trading_dates)
            trade_dicts = controller.training_simulation_service.build_trade_dicts(sim_result)

            benchmark_passed = False
            daily_values = [
                float(row.get("total_value") or 0.0)
                for row in list(getattr(sim_result, "daily_records", []) or [])
                if isinstance(row, dict) and row.get("total_value") is not None
            ]
            if len(daily_values) >= 2:
                aligned_benchmark = benchmark_daily_values if len(benchmark_daily_values) == len(daily_values) else None
                benchmark_metrics = controller.benchmark_evaluator.evaluate(
                    daily_values=daily_values,
                    benchmark_daily_values=aligned_benchmark,
                    trade_history=trade_dicts,
                )
                benchmark_passed = bool(getattr(benchmark_metrics, "passed", False))

            cycle_stub = {
                "cycle_id": cycle_id,
                "return_pct": sim_result.return_pct,
                "profit_loss": getattr(sim_result, "total_pnl", 0.0),
                "total_trades": getattr(sim_result, "total_trades", 0),
                "winning_trades": getattr(sim_result, "winning_trades", 0),
                "losing_trades": getattr(sim_result, "losing_trades", 0),
                "win_rate": getattr(sim_result, "win_rate", 0.0),
                "selected_stocks": list(selected),
            }
            strategy_evaluator = controller.strategy_evaluator.__class__(
                policy=dict(getattr(controller.strategy_evaluator, "policy", {}) or {})
            )
            strategy_eval = strategy_evaluator.evaluate(
                cycle_stub,
                trade_history=trade_dicts,
                daily_records=list(getattr(sim_result, "daily_records", []) or []),
            )
            return {
                "status": "ok",
                "arm": arm_name,
                "model_name": str(getattr(model_output, "model_name", model_name) or model_name),
                "config_name": str(getattr(model_output, "config_name", "") or ""),
                "config_ref": str(config_ref or ""),
                "selected_stocks": list(selected),
                "selection_mode": str(getattr(trading_plan, "source", "") or "meeting_selection"),
                "regime": str(getattr(model_output.signal_packet, "regime", "unknown") or "unknown"),
                "return_pct": round(float(sim_result.return_pct or 0.0), 4),
                "benchmark_passed": benchmark_passed,
                "trade_count": int(getattr(sim_result, "total_trades", 0) or 0),
                "final_value": round(float(getattr(sim_result, "final_value", 0.0) or 0.0), 4),
                "win_rate": round(float(getattr(sim_result, "win_rate", 0.0) or 0.0), 4),
                "strategy_scores": strategy_eval.to_dict(),
            }
        except Exception as exc:
            logger.warning("A/B arm execution failed for %s (%s): %s", arm_name, config_ref, exc)
            return {
                "status": "error",
                "arm": arm_name,
                "config_ref": str(config_ref or ""),
                "error": str(exc),
            }

    def run_candidate_ab_comparison(
        self,
        controller: Any,
        *,
        cycle_id: int,
        cutoff_date: str,
        stock_data: dict[str, Any],
        model_name: str,
        active_config_ref: str,
        candidate_config_ref: str,
        baseline_regime: str = "",
    ) -> dict[str, Any]:
        active_ref = str(active_config_ref or "").strip()
        candidate_ref = str(candidate_config_ref or "").strip()
        if not active_ref or not candidate_ref or active_ref == candidate_ref:
            return {}

        active_arm = self._evaluate_arm(
            controller,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            stock_data=stock_data,
            model_name=model_name,
            config_ref=active_ref,
            arm_name="active",
        )
        candidate_arm = self._evaluate_arm(
            controller,
            cycle_id=cycle_id,
            cutoff_date=cutoff_date,
            stock_data=stock_data,
            model_name=model_name,
            config_ref=candidate_ref,
            arm_name="candidate",
        )

        comparable = active_arm.get("status") == "ok" and candidate_arm.get("status") == "ok"
        comparison: dict[str, Any] = {
            "candidate_present": True,
            "comparable": comparable,
            "winner": "inconclusive",
            "return_lift_pct": None,
            "strategy_score_lift": None,
            "benchmark_lift": None,
            "win_rate_lift": None,
            "selection_overlap_ratio": _selection_overlap_ratio(
                list(active_arm.get("selected_stocks") or []),
                list(candidate_arm.get("selected_stocks") or []),
            ),
        }
        if comparable:
            active_return = float(active_arm.get("return_pct") or 0.0)
            candidate_return = float(candidate_arm.get("return_pct") or 0.0)
            active_score = float(dict(active_arm.get("strategy_scores") or {}).get("overall_score", 0.0) or 0.0)
            candidate_score = float(dict(candidate_arm.get("strategy_scores") or {}).get("overall_score", 0.0) or 0.0)
            active_benchmark = 1.0 if bool(active_arm.get("benchmark_passed", False)) else 0.0
            candidate_benchmark = 1.0 if bool(candidate_arm.get("benchmark_passed", False)) else 0.0
            active_win_rate = float(active_arm.get("win_rate") or 0.0)
            candidate_win_rate = float(candidate_arm.get("win_rate") or 0.0)
            return_lift = round(candidate_return - active_return, 4)
            score_lift = round(candidate_score - active_score, 4)
            benchmark_lift = round(candidate_benchmark - active_benchmark, 4)
            win_rate_lift = round(candidate_win_rate - active_win_rate, 4)
            if return_lift > 0:
                winner = "candidate"
            elif return_lift < 0:
                winner = "active"
            else:
                winner = "tie"
            comparison.update(
                {
                    "winner": winner,
                    "return_lift_pct": return_lift,
                    "strategy_score_lift": score_lift,
                    "benchmark_lift": benchmark_lift,
                    "win_rate_lift": win_rate_lift,
                    "candidate_outperformed": bool(
                        return_lift >= 0.0 and benchmark_lift >= 0.0 and score_lift >= 0.0
                    ),
                }
            )

        return {
            "enabled": True,
            "cycle_id": int(cycle_id),
            "cutoff_date": str(cutoff_date or ""),
            "market_regime": str(baseline_regime or ""),
            "active": active_arm,
            "candidate": candidate_arm,
            "comparison": comparison,
        }
