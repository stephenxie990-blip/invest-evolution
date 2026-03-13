from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from config import config, normalize_date
from invest.foundation import SimulatedTrader
from invest.models.defaults import COMMON_EXECUTION_DEFAULTS, COMMON_PARAM_DEFAULTS


class TrainingSimulationService:
    """Owns simulation bootstrap, date resolution, and evaluation payload assembly."""

    def build_trader(
        self,
        controller: Any,
        *,
        selected_data: dict[str, Any],
        trading_plan: Any,
    ) -> SimulatedTrader:
        trader = SimulatedTrader(
            initial_capital=float(
                controller.execution_policy.get(
                    "initial_capital",
                    getattr(config, "initial_capital", COMMON_EXECUTION_DEFAULTS["initial_capital"]),
                )
                or getattr(config, "initial_capital", COMMON_EXECUTION_DEFAULTS["initial_capital"])
            ),
            max_positions=trading_plan.max_positions or len(selected_data),
            position_size_pct=controller.current_params.get(
                "position_size",
                COMMON_PARAM_DEFAULTS["position_size"],
            ),
            commission_rate=float(
                controller.execution_policy.get(
                    "commission_rate",
                    COMMON_EXECUTION_DEFAULTS["commission_rate"],
                )
                or COMMON_EXECUTION_DEFAULTS["commission_rate"]
            ),
            stamp_tax_rate=float(
                controller.execution_policy.get(
                    "stamp_tax_rate",
                    COMMON_EXECUTION_DEFAULTS["stamp_tax_rate"],
                )
                or COMMON_EXECUTION_DEFAULTS["stamp_tax_rate"]
            ),
            slippage_rate=float(
                controller.execution_policy.get(
                    "slippage_rate",
                    COMMON_EXECUTION_DEFAULTS["slippage_rate"],
                )
                or COMMON_EXECUTION_DEFAULTS["slippage_rate"]
            ),
            risk_policy=controller.risk_policy,
        )
        trader.set_stock_data(selected_data)
        trader.set_stock_info(self._build_stock_info(selected_data))
        trader.set_trading_plan(trading_plan)
        return trader

    def resolve_trading_dates(
        self,
        *,
        selected_data: dict[str, Any],
        cutoff_date: str,
        simulation_days: int,
    ) -> list[str]:
        all_dates: set[str] = set()
        for frame in selected_data.values():
            date_col = "trade_date" if "trade_date" in frame.columns else "date"
            if date_col not in frame.columns:
                continue
            all_dates.update(frame[date_col].apply(normalize_date).tolist())
        dates_after = sorted(date for date in all_dates if date > cutoff_date)
        return dates_after[:simulation_days]

    def build_benchmark_context(
        self,
        controller: Any,
        *,
        cutoff_date: str,
        trading_dates: list[str],
    ) -> tuple[list[float], Any]:
        benchmark_daily_values = controller.data_manager.get_benchmark_daily_values(
            trading_dates,
            index_code="sh.000300",
        )
        market_index_start = (
            datetime.strptime(cutoff_date, "%Y%m%d") - timedelta(days=180)
        ).strftime("%Y%m%d")
        market_index_frame = controller.data_manager.get_market_index_frame(
            index_code="sh.000300",
            start_date=market_index_start,
            end_date=trading_dates[-1] if trading_dates else cutoff_date,
        )
        return benchmark_daily_values, market_index_frame

    def build_trade_dicts(self, sim_result: Any) -> list[dict[str, Any]]:
        return [
            {
                "date": trade.date,
                "action": trade.action.value if hasattr(trade.action, "value") else str(trade.action),
                "ts_code": trade.ts_code,
                "price": trade.price,
                "shares": trade.shares,
                "pnl": trade.pnl,
                "pnl_pct": trade.pnl_pct,
                "reason": trade.reason,
                "source": getattr(trade, "source", ""),
                "entry_reason": getattr(trade, "entry_reason", ""),
                "exit_reason": getattr(trade, "exit_reason", ""),
                "exit_trigger": getattr(trade, "exit_trigger", ""),
                "entry_date": getattr(trade, "entry_date", ""),
                "entry_price": getattr(trade, "entry_price", 0.0),
                "holding_days": getattr(trade, "holding_days", 0),
                "stop_loss_price": getattr(trade, "stop_loss_price", 0.0),
                "take_profit_price": getattr(trade, "take_profit_price", 0.0),
                "trailing_pct": getattr(trade, "trailing_pct", None),
                "capital_before": getattr(trade, "capital_before", 0.0),
                "capital_after": getattr(trade, "capital_after", 0.0),
                "open_price": getattr(trade, "open_price", 0.0),
                "high_price": getattr(trade, "high_price", 0.0),
                "low_price": getattr(trade, "low_price", 0.0),
                "volume": getattr(trade, "volume", 0.0),
                "amount": getattr(trade, "amount", 0.0),
                "pct_chg": getattr(trade, "pct_chg", 0.0),
            }
            for trade in sim_result.trade_history
        ]

    def build_cycle_dict(
        self,
        *,
        cycle_id: int,
        cutoff_date: str,
        sim_result: Any,
        selected: list[str],
        is_profit: bool,
        regime_result: dict[str, Any],
        routing_decision: dict[str, Any],
        trading_plan: Any,
        data_mode: str,
        requested_data_mode: str,
        effective_data_mode: str,
        llm_mode: str,
        degraded: bool,
        degrade_reason: str,
        selection_mode: str,
        agent_used: bool,
        llm_used: bool,
    ) -> dict[str, Any]:
        return {
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "return_pct": sim_result.return_pct,
            "profit_loss": sim_result.total_pnl,
            "total_trades": sim_result.total_trades,
            "winning_trades": sim_result.winning_trades,
            "losing_trades": sim_result.losing_trades,
            "win_rate": sim_result.win_rate,
            "selected_stocks": selected,
            "is_profit": is_profit,
            "regime": regime_result.get("regime", "unknown"),
            "routing_decision": dict(routing_decision or {}),
            "plan_source": trading_plan.source,
            "data_mode": data_mode,
            "requested_data_mode": requested_data_mode,
            "effective_data_mode": effective_data_mode,
            "llm_mode": llm_mode,
            "degraded": degraded,
            "degrade_reason": degrade_reason,
            "selection_mode": selection_mode,
            "agent_used": agent_used,
            "llm_used": llm_used,
            "initial_capital": sim_result.initial_capital,
            "final_value": sim_result.final_value,
        }

    def evaluate_cycle(
        self,
        controller: Any,
        *,
        cycle_dict: dict[str, Any],
        trade_dicts: list[dict[str, Any]],
        sim_result: Any,
        benchmark_daily_values: list[float],
    ) -> bool:
        daily_values = [
            float(row.get("total_value") or 0.0)
            for row in sim_result.daily_records
            if isinstance(row, dict) and row.get("total_value") is not None
        ]
        benchmark_passed = False
        if len(daily_values) >= 2:
            aligned_benchmark = (
                benchmark_daily_values if len(benchmark_daily_values) == len(daily_values) else None
            )
            benchmark_metrics = controller.benchmark_evaluator.evaluate(
                daily_values=daily_values,
                benchmark_daily_values=aligned_benchmark,
                trade_history=trade_dicts,
            )
            benchmark_passed = bool(benchmark_metrics.passed)
            cycle_dict.update(
                {
                    "sharpe_ratio": benchmark_metrics.sharpe_ratio,
                    "max_drawdown": benchmark_metrics.max_drawdown,
                    "excess_return": benchmark_metrics.excess_return,
                    "benchmark_return": benchmark_metrics.benchmark_return,
                    "benchmark_source": "index_bar:sh.000300" if aligned_benchmark else "none",
                    "benchmark_passed": benchmark_passed,
                    "benchmark_strict_passed": benchmark_metrics.passed,
                }
            )
        else:
            cycle_dict["benchmark_passed"] = False
            cycle_dict["benchmark_strict_passed"] = False

        strategy_eval = controller.strategy_evaluator.evaluate(
            cycle_dict,
            trade_dicts,
            sim_result.daily_records,
        )
        cycle_dict["strategy_scores"] = {
            "signal_accuracy": float(strategy_eval.signal_accuracy),
            "timing_score": float(strategy_eval.timing_score),
            "risk_control_score": float(strategy_eval.risk_control_score),
            "overall_score": float(strategy_eval.overall_score),
            "suggestions": list(strategy_eval.suggestions or []),
        }
        return benchmark_passed

    def _build_stock_info(self, selected_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            code: {
                "name": str(frame["name"].iloc[-1]) if "name" in frame.columns and not frame.empty else code,
                "industry": str(frame["industry"].iloc[-1]) if "industry" in frame.columns and not frame.empty else "其他",
                "market_cap": float(frame["market_cap"].dropna().iloc[-1]) if "market_cap" in frame.columns and not frame["market_cap"].dropna().empty else 0.0,
                "roe": float(frame["roe"].dropna().iloc[-1]) if "roe" in frame.columns and not frame["roe"].dropna().empty else 0.0,
            }
            for code, frame in selected_data.items()
        }
