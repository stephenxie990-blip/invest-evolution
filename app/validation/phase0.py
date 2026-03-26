from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from app.train import SelfLearningController
from app.training.simulation_services import TrainingSimulationService
from config import config, normalize_date
from invest.foundation.engine.simulator import SimulatedTrader
from invest.foundation.metrics.benchmark import BenchmarkEvaluator
from invest.foundation.metrics.cycle import StrategyEvaluator
from invest.foundation.risk import (
    clamp_position_size,
    clamp_stop_loss_pct,
    clamp_take_profit_pct,
)
from invest.models import create_investment_model
from invest.models.defaults import (
    COMMON_BENCHMARK_DEFAULTS,
    COMMON_EXECUTION_DEFAULTS,
    COMMON_PARAM_DEFAULTS,
)
from invest.shared.contracts import PositionPlan, TradingPlan
from market_data import DataManager


_CYCLE_FILE_RE = re.compile(r"^cycle_(\d+)\.json$")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mean(values: Iterable[float]) -> float:
    items = [float(value) for value in values]
    return sum(items) / len(items) if items else 0.0


def _resolve_output_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _cycle_sort_key(payload: dict[str, Any]) -> tuple[int, str]:
    cycle_id = _safe_int(payload.get("cycle_id"), 0)
    cutoff_date = str(payload.get("cutoff_date") or "")
    return cycle_id, cutoff_date


def _completed_cycles(cycles: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(item) for item in cycles if str(item.get("status") or "ok") == "ok"]


def _compounded_return_pct(values: Iterable[float]) -> float:
    growth = 1.0
    has_value = False
    for value in values:
        growth *= 1.0 + (float(value) / 100.0)
        has_value = True
    if not has_value:
        return 0.0
    return (growth - 1.0) * 100.0


def load_cutoff_dates_from_run(run_dir: str | Path) -> list[str]:
    root = _resolve_output_path(run_dir)
    items: list[tuple[int, str]] = []
    for path in root.iterdir():
        match = _CYCLE_FILE_RE.match(path.name)
        if not match or not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        cutoff_date = normalize_date(str(payload.get("cutoff_date") or ""))
        if not cutoff_date:
            continue
        items.append((_safe_int(match.group(1), 0), cutoff_date))
    items.sort(key=lambda item: item[0])
    return [cutoff_date for _, cutoff_date in items]


def build_bare_trading_plan(signal_packet: Any) -> TradingPlan:
    signal_by_code = {
        str(signal.code): signal
        for signal in list(getattr(signal_packet, "signals", []) or [])
    }
    selected_codes = list(getattr(signal_packet, "selected_codes", []) or [])
    if not selected_codes:
        selected_codes = list(signal_packet.top_codes(limit=getattr(signal_packet, "max_positions", 0) or None))
    max_positions = max(1, _safe_int(getattr(signal_packet, "max_positions", 0), 0) or len(selected_codes) or 1)
    selected_codes = selected_codes[:max_positions]

    cash_reserve = max(0.0, min(0.7, _safe_float(getattr(signal_packet, "cash_reserve", 0.0), 0.0)))
    available_weight = max(0.0, 1.0 - cash_reserve)
    params = dict(getattr(signal_packet, "params", {}) or {})
    preferred_weight = _safe_float(
        params.get("position_size", COMMON_PARAM_DEFAULTS["position_size"]),
        COMMON_PARAM_DEFAULTS["position_size"],
    )
    spread = available_weight / max(len(selected_codes), 1) if selected_codes else clamp_position_size(preferred_weight)
    default_weight = round(min(clamp_position_size(preferred_weight), spread), 3) if spread > 0 else 0.0

    positions: list[PositionPlan] = []
    for index, code in enumerate(selected_codes, start=1):
        signal = signal_by_code.get(str(code))
        stop_loss_pct = (
            getattr(signal, "stop_loss_pct", None)
            if signal is not None and getattr(signal, "stop_loss_pct", None) is not None
            else params.get("stop_loss_pct", COMMON_PARAM_DEFAULTS["stop_loss_pct"])
        )
        take_profit_pct = (
            getattr(signal, "take_profit_pct", None)
            if signal is not None and getattr(signal, "take_profit_pct", None) is not None
            else params.get("take_profit_pct", COMMON_PARAM_DEFAULTS["take_profit_pct"])
        )
        trailing_pct = (
            getattr(signal, "trailing_pct", None)
            if signal is not None
            else params.get("trailing_pct")
        )
        weight_hint = (
            getattr(signal, "weight_hint", None)
            if signal is not None and getattr(signal, "weight_hint", None) is not None
            else default_weight
        )
        reason_parts = []
        if signal is not None:
            reason_parts.extend(list(getattr(signal, "evidence", []) or [])[:2])
        reason = "；".join(str(part) for part in reason_parts if str(part).strip()) or str(
            getattr(signal_packet, "reasoning", "") or "bare_strategy_selection"
        )
        positions.append(
            PositionPlan(
                code=str(code),
                priority=index,
                weight=min(_safe_float(weight_hint, default_weight), available_weight),
                entry_method="market",
                stop_loss_pct=clamp_stop_loss_pct(_safe_float(stop_loss_pct, COMMON_PARAM_DEFAULTS["stop_loss_pct"])),
                take_profit_pct=clamp_take_profit_pct(
                    _safe_float(take_profit_pct, COMMON_PARAM_DEFAULTS["take_profit_pct"])
                ),
                trailing_pct=_safe_float(trailing_pct) if trailing_pct not in (None, "") else None,
                max_hold_days=max(
                    1,
                    _safe_int(params.get("max_hold_days", COMMON_PARAM_DEFAULTS["max_hold_days"]), COMMON_PARAM_DEFAULTS["max_hold_days"]),
                ),
                reason=reason,
                source="bare_strategy",
            )
        )

    return TradingPlan(
        date=str(getattr(signal_packet, "as_of_date", "") or ""),
        positions=positions,
        cash_reserve=cash_reserve,
        max_positions=max_positions,
        source="bare_strategy",
        reasoning=str(getattr(signal_packet, "reasoning", "") or "bare_strategy"),
    )


def _resolve_trader_stock_info(selected_data: dict[str, Any]) -> dict[str, dict[str, str]]:
    stock_info: dict[str, dict[str, str]] = {}
    for code, frame in selected_data.items():
        name = code
        if hasattr(frame, "columns") and "name" in frame.columns and not frame.empty:
            raw_name = str(frame.iloc[-1].get("name") or "").strip()
            if raw_name:
                name = raw_name
        stock_info[str(code)] = {"name": name}
    return stock_info


def _benchmark_series(trading_dates: list[str], benchmark_daily_values: list[float]) -> list[dict[str, Any]]:
    if len(trading_dates) != len(benchmark_daily_values):
        return []
    return [
        {"date": str(date), "close": _safe_float(value)}
        for date, value in zip(trading_dates, benchmark_daily_values)
    ]


def _build_market_index_frame(
    data_manager: DataManager,
    *,
    cutoff_date: str,
    trading_dates: list[str],
    benchmark_index_code: str,
):
    if not trading_dates:
        return None
    market_index_start = (
        datetime.strptime(cutoff_date, "%Y%m%d") - timedelta(days=180)
    ).strftime("%Y%m%d")
    frame = data_manager.get_market_index_frame(
        index_code=benchmark_index_code,
        start_date=market_index_start,
        end_date=trading_dates[-1],
    )
    return frame if frame is not None and not getattr(frame, "empty", True) else None


def run_bare_validation(
    *,
    model_name: str,
    cutoff_dates: list[str],
    config_path: str | Path | None = None,
    stock_count: int | None = None,
    min_history_days: int | None = None,
    simulation_days: int | None = None,
    benchmark_index_code: str = "sh.000300",
    data_manager: DataManager | None = None,
) -> dict[str, Any]:
    runtime_stock_count = max(1, int(stock_count or getattr(config, "max_stocks", 50) or 50))
    runtime_min_history_days = max(
        30,
        int(min_history_days or getattr(config, "min_history_days", 200) or 200),
    )
    runtime_simulation_days = max(
        1,
        int(simulation_days or getattr(config, "simulation_days", 30) or 30),
    )
    manager = data_manager or DataManager()
    model = create_investment_model(model_name, config_path=config_path)
    simulation_service = TrainingSimulationService()

    cycles: list[dict[str, Any]] = []
    for cycle_id, raw_cutoff in enumerate(cutoff_dates, start=1):
        cutoff_date = normalize_date(raw_cutoff)
        diagnostics = manager.diagnose_training_data(
            cutoff_date=cutoff_date,
            stock_count=runtime_stock_count,
            min_history_days=runtime_min_history_days,
        )
        stock_data = manager.load_stock_data(
            cutoff_date=cutoff_date,
            stock_count=runtime_stock_count,
            min_history_days=runtime_min_history_days,
            include_future_days=runtime_simulation_days,
        )
        model_output = model.process(stock_data, cutoff_date)
        signal_packet = model_output.signal_packet
        trading_plan = build_bare_trading_plan(signal_packet)
        selected_data = {
            str(code): stock_data[code]
            for code in trading_plan.stock_codes
            if code in stock_data
        }
        cycle_payload: dict[str, Any] = {
            "status": "ok",
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "model_name": str(model_output.model_name or model_name),
            "config_name": str(model_output.config_name or model.config.name),
            "config_path": str(model.config.path),
            "regime": str(signal_packet.regime or "unknown"),
            "diagnostics": diagnostics,
            "data_resolution": dict(getattr(manager, "last_resolution", {}) or {}),
            "selected_stocks": list(selected_data.keys()),
            "selected_signal_details": {
                str(signal.code): signal.to_dict()
                for signal in list(signal_packet.signals or [])
                if str(signal.code) in selected_data
            },
            "trading_plan": {
                "source": trading_plan.source,
                "cash_reserve": trading_plan.cash_reserve,
                "max_positions": trading_plan.max_positions,
                "positions": [
                    {
                        "code": item.code,
                        "priority": item.priority,
                        "weight": item.weight,
                        "stop_loss_pct": item.stop_loss_pct,
                        "take_profit_pct": item.take_profit_pct,
                        "trailing_pct": item.trailing_pct,
                        "max_hold_days": item.max_hold_days,
                        "reason": item.reason,
                    }
                    for item in list(trading_plan.positions or [])
                ],
            },
            "simulation_days": runtime_simulation_days,
            "execution_policy": {
                "initial_capital": _safe_float(
                    model.execution_param("initial_capital", COMMON_EXECUTION_DEFAULTS["initial_capital"]),
                    COMMON_EXECUTION_DEFAULTS["initial_capital"],
                ),
                "commission_rate": _safe_float(
                    model.execution_param("commission_rate", COMMON_EXECUTION_DEFAULTS["commission_rate"]),
                    COMMON_EXECUTION_DEFAULTS["commission_rate"],
                ),
                "stamp_tax_rate": _safe_float(
                    model.execution_param("stamp_tax_rate", COMMON_EXECUTION_DEFAULTS["stamp_tax_rate"]),
                    COMMON_EXECUTION_DEFAULTS["stamp_tax_rate"],
                ),
                "slippage_rate": _safe_float(
                    model.execution_param("slippage_rate", COMMON_EXECUTION_DEFAULTS["slippage_rate"]),
                    COMMON_EXECUTION_DEFAULTS["slippage_rate"],
                ),
            },
        }
        if not selected_data:
            cycle_payload.update(
                {
                    "status": "skipped",
                    "skip_reason": "no_selected_data",
                }
            )
            cycles.append(cycle_payload)
            continue

        trading_dates = simulation_service.resolve_trading_dates(
            selected_data=selected_data,
            cutoff_date=cutoff_date,
            simulation_days=runtime_simulation_days,
        )
        cycle_payload["trading_dates"] = list(trading_dates)
        if len(trading_dates) < runtime_simulation_days:
            cycle_payload.update(
                {
                    "status": "skipped",
                    "skip_reason": f"insufficient_trading_days:{len(trading_dates)}<{runtime_simulation_days}",
                }
            )
            cycles.append(cycle_payload)
            continue

        trader = SimulatedTrader(
            initial_capital=cycle_payload["execution_policy"]["initial_capital"],
            max_positions=trading_plan.max_positions or len(selected_data),
            position_size_pct=_safe_float(
                signal_packet.params.get("position_size", COMMON_PARAM_DEFAULTS["position_size"]),
                COMMON_PARAM_DEFAULTS["position_size"],
            ),
            commission_rate=cycle_payload["execution_policy"]["commission_rate"],
            stamp_tax_rate=cycle_payload["execution_policy"]["stamp_tax_rate"],
            slippage_rate=cycle_payload["execution_policy"]["slippage_rate"],
            risk_policy=model.config_section("risk_policy", {}) or {},
        )
        trader.set_stock_data(selected_data)
        trader.set_stock_info(_resolve_trader_stock_info(selected_data))
        trader.set_trading_plan(trading_plan)
        benchmark_daily_values = manager.get_benchmark_daily_values(
            trading_dates=trading_dates,
            index_code=benchmark_index_code,
        )
        market_index_frame = _build_market_index_frame(
            manager,
            cutoff_date=cutoff_date,
            trading_dates=trading_dates,
            benchmark_index_code=benchmark_index_code,
        )
        if market_index_frame is not None:
            trader.set_market_index_data(market_index_frame)

        sim_result = trader.run_simulation(trading_dates[0], trading_dates)
        trade_dicts = simulation_service.build_trade_dicts(sim_result)
        benchmark_policy = model.config_section("benchmark", {}) or {}
        benchmark_evaluator = BenchmarkEvaluator(
            risk_free_rate=_safe_float(
                benchmark_policy.get("risk_free_rate", COMMON_BENCHMARK_DEFAULTS["risk_free_rate"]),
                COMMON_BENCHMARK_DEFAULTS["risk_free_rate"],
            ),
            criteria=dict(benchmark_policy.get("criteria") or COMMON_BENCHMARK_DEFAULTS.get("criteria") or {}),
        )
        daily_values = [
            _safe_float(record.get("total_value"))
            for record in list(sim_result.daily_records or [])
            if isinstance(record, dict) and record.get("total_value") is not None
        ]
        aligned_benchmark = benchmark_daily_values if len(benchmark_daily_values) == len(daily_values) else None
        benchmark_metrics = benchmark_evaluator.evaluate(
            daily_values=daily_values,
            benchmark_daily_values=aligned_benchmark,
            trade_history=trade_dicts,
        )
        cycle_result = {
            "cycle_id": cycle_id,
            "return_pct": _safe_float(sim_result.return_pct),
            "profit_loss": _safe_float(sim_result.total_pnl),
            "total_trades": _safe_int(sim_result.total_trades),
            "winning_trades": _safe_int(sim_result.winning_trades),
            "losing_trades": _safe_int(sim_result.losing_trades),
            "win_rate": _safe_float(sim_result.win_rate),
            "selected_stocks": list(selected_data.keys()),
        }
        strategy_evaluator = StrategyEvaluator(policy=model.config_section("evaluation_policy", {}) or {})
        strategy_eval = strategy_evaluator.evaluate(
            cycle_result,
            trade_history=trade_dicts,
            daily_records=list(sim_result.daily_records or []),
        )
        cycle_payload.update(
            {
                "initial_capital": _safe_float(sim_result.initial_capital),
                "final_value": _safe_float(sim_result.final_value),
                "return_pct": _safe_float(sim_result.return_pct),
                "profit_loss": _safe_float(sim_result.total_pnl),
                "trade_count": len(trade_dicts),
                "closed_trade_count": _safe_int(sim_result.total_trades),
                "win_rate": _safe_float(sim_result.win_rate),
                "benchmark_passed": bool(benchmark_metrics.passed),
                "benchmark": {
                    "total_return": _safe_float(benchmark_metrics.total_return),
                    "annual_return": _safe_float(benchmark_metrics.annual_return),
                    "excess_return": _safe_float(benchmark_metrics.excess_return),
                    "benchmark_return": _safe_float(benchmark_metrics.benchmark_return),
                    "sharpe_ratio": _safe_float(benchmark_metrics.sharpe_ratio),
                    "calmar_ratio": _safe_float(benchmark_metrics.calmar_ratio),
                    "sortino_ratio": _safe_float(benchmark_metrics.sortino_ratio),
                    "max_drawdown": _safe_float(benchmark_metrics.max_drawdown),
                    "volatility": _safe_float(benchmark_metrics.volatility),
                    "win_rate": _safe_float(benchmark_metrics.win_rate),
                    "profit_loss_ratio": _safe_float(benchmark_metrics.profit_loss_ratio),
                    "monthly_turnover": _safe_float(benchmark_metrics.monthly_turnover),
                    "passed": bool(benchmark_metrics.passed),
                    "failed_criteria": list(benchmark_metrics.failed_criteria or []),
                    "index_code": benchmark_index_code,
                },
                "self_assessment": {
                    "regime": str(signal_packet.regime or "unknown"),
                    "plan_source": trading_plan.source,
                    "sharpe_ratio": _safe_float(benchmark_metrics.sharpe_ratio),
                    "max_drawdown": _safe_float(benchmark_metrics.max_drawdown),
                    "excess_return": _safe_float(benchmark_metrics.excess_return),
                    "benchmark_passed": bool(benchmark_metrics.passed),
                    "signal_accuracy": _safe_float(strategy_eval.signal_accuracy),
                    "timing_score": _safe_float(strategy_eval.timing_score),
                    "risk_control_score": _safe_float(strategy_eval.risk_control_score),
                    "overall_score": _safe_float(strategy_eval.overall_score),
                },
                "strategy_scores": {
                    "signal_accuracy": _safe_float(strategy_eval.signal_accuracy),
                    "timing_score": _safe_float(strategy_eval.timing_score),
                    "risk_control_score": _safe_float(strategy_eval.risk_control_score),
                    "overall_score": _safe_float(strategy_eval.overall_score),
                    "suggestions": list(strategy_eval.suggestions or []),
                },
                "trades": trade_dicts,
                "benchmark_series": _benchmark_series(trading_dates, benchmark_daily_values),
            }
        )
        cycles.append(cycle_payload)

    summary = aggregate_cycle_metrics(cycles)
    return {
        "run_type": "bare_strategy_validation",
        "model_name": model_name,
        "config_name": model.config.name,
        "config_path": str(model.config.path),
        "cutoff_dates": [normalize_date(item) for item in cutoff_dates],
        "stock_count": runtime_stock_count,
        "min_history_days": runtime_min_history_days,
        "simulation_days": runtime_simulation_days,
        "benchmark_index_code": benchmark_index_code,
        "summary": summary,
        "cycles": sorted(cycles, key=_cycle_sort_key),
    }


def aggregate_cycle_metrics(cycles: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = [dict(item) for item in cycles]
    completed = _completed_cycles(items)
    profit_returns = [_safe_float(item.get("return_pct")) for item in completed]
    sharpe_values = [
        _safe_float(dict(item.get("self_assessment") or {}).get("sharpe_ratio"))
        for item in completed
    ]
    drawdowns = [
        _safe_float(dict(item.get("self_assessment") or {}).get("max_drawdown"))
        for item in completed
    ]
    excess_returns = [
        _safe_float(dict(item.get("self_assessment") or {}).get("excess_return"))
        for item in completed
    ]
    strategy_scores = [
        _safe_float(dict(item.get("strategy_scores") or {}).get("overall_score"))
        for item in completed
    ]
    benchmark_passes = [1.0 if bool(item.get("benchmark_passed")) else 0.0 for item in completed]
    profit_cycles = [1.0 if _safe_float(item.get("return_pct")) > 0 else 0.0 for item in completed]

    regime_breakdown: dict[str, dict[str, Any]] = {}
    for item in completed:
        regime = str(
            item.get("regime")
            or dict(item.get("routing_decision") or {}).get("regime")
            or dict(item.get("self_assessment") or {}).get("regime")
            or "unknown"
        )
        bucket = regime_breakdown.setdefault(
            regime,
            {
                "cycle_count": 0,
                "profit_cycle_count": 0,
                "avg_return_pct": 0.0,
                "avg_sharpe_ratio": 0.0,
                "avg_max_drawdown": 0.0,
                "avg_excess_return": 0.0,
                "benchmark_pass_rate": 0.0,
            },
        )
        bucket["cycle_count"] += 1
        bucket["profit_cycle_count"] += 1 if _safe_float(item.get("return_pct")) > 0 else 0
        bucket.setdefault("_returns", []).append(_safe_float(item.get("return_pct")))
        bucket.setdefault("_sharpes", []).append(
            _safe_float(dict(item.get("self_assessment") or {}).get("sharpe_ratio"))
        )
        bucket.setdefault("_drawdowns", []).append(
            _safe_float(dict(item.get("self_assessment") or {}).get("max_drawdown"))
        )
        bucket.setdefault("_excess", []).append(
            _safe_float(dict(item.get("self_assessment") or {}).get("excess_return"))
        )
        bucket.setdefault("_benchmark", []).append(1.0 if bool(item.get("benchmark_passed")) else 0.0)

    for regime, bucket in regime_breakdown.items():
        bucket["avg_return_pct"] = _mean(bucket.pop("_returns", []))
        bucket["avg_sharpe_ratio"] = _mean(bucket.pop("_sharpes", []))
        bucket["avg_max_drawdown"] = _mean(bucket.pop("_drawdowns", []))
        bucket["avg_excess_return"] = _mean(bucket.pop("_excess", []))
        bucket["benchmark_pass_rate"] = _mean(bucket.pop("_benchmark", []))
        cycle_count = max(1, _safe_int(bucket.get("cycle_count"), 0))
        bucket["profit_cycle_rate"] = _safe_int(bucket.get("profit_cycle_count"), 0) / cycle_count
        regime_breakdown[regime] = bucket

    return {
        "cycle_count": len(items),
        "completed_cycle_count": len(completed),
        "skipped_cycle_count": len(items) - len(completed),
        "profit_cycle_count": int(sum(profit_cycles)),
        "avg_return_pct": _mean(profit_returns),
        "median_return_pct": median(profit_returns) if profit_returns else 0.0,
        "compounded_return_pct": _compounded_return_pct(profit_returns),
        "avg_sharpe_ratio": _mean(sharpe_values),
        "avg_max_drawdown": _mean(drawdowns),
        "avg_excess_return": _mean(excess_returns),
        "avg_strategy_score": _mean(strategy_scores),
        "benchmark_pass_rate": _mean(benchmark_passes),
        "profit_cycle_rate": _mean(profit_cycles),
        "regime_breakdown": regime_breakdown,
    }


def build_trade_trace_records(
    validation_payload: dict[str, Any],
    *,
    limit: int = 5,
    selection: str = "top_abs",
) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    for cycle in list(validation_payload.get("cycles") or []):
        if str(cycle.get("status") or "ok") != "ok":
            continue
        signal_map = dict(cycle.get("selected_signal_details") or {})
        benchmark_map = {
            str(item.get("date")): _safe_float(item.get("close"))
            for item in list(cycle.get("benchmark_series") or [])
            if str(item.get("date") or "").strip()
        }
        execution_policy = dict(cycle.get("execution_policy") or {})
        commission_rate = _safe_float(
            execution_policy.get("commission_rate", COMMON_EXECUTION_DEFAULTS["commission_rate"]),
            COMMON_EXECUTION_DEFAULTS["commission_rate"],
        )
        stamp_tax_rate = _safe_float(
            execution_policy.get("stamp_tax_rate", COMMON_EXECUTION_DEFAULTS["stamp_tax_rate"]),
            COMMON_EXECUTION_DEFAULTS["stamp_tax_rate"],
        )
        for trade in list(cycle.get("trades") or []):
            action = str(trade.get("action") or "")
            if action not in {"SELL", "卖出"}:
                continue
            shares = _safe_int(trade.get("shares"), 0)
            entry_price = _safe_float(trade.get("entry_price"))
            exit_price = _safe_float(trade.get("price"))
            if shares <= 0 or entry_price <= 0 or exit_price <= 0:
                continue
            entry_notional = entry_price * shares
            exit_notional = exit_price * shares
            buy_commission = entry_notional * commission_rate
            sell_commission = exit_notional * commission_rate
            stamp_tax = exit_notional * stamp_tax_rate
            total_fees = buy_commission + sell_commission + stamp_tax
            benchmark_entry = benchmark_map.get(str(trade.get("entry_date") or ""))
            benchmark_exit = benchmark_map.get(str(trade.get("date") or ""))
            benchmark_return_pct = None
            excess_return_pct = None
            if benchmark_entry and benchmark_exit:
                benchmark_return_pct = (benchmark_exit - benchmark_entry) / benchmark_entry * 100.0
                excess_return_pct = _safe_float(trade.get("pnl_pct")) - benchmark_return_pct
            traces.append(
                {
                    "cycle_id": _safe_int(cycle.get("cycle_id"), 0),
                    "cutoff_date": str(cycle.get("cutoff_date") or ""),
                    "regime": str(cycle.get("regime") or "unknown"),
                    "ts_code": str(trade.get("ts_code") or ""),
                    "entry_date": str(trade.get("entry_date") or ""),
                    "exit_date": str(trade.get("date") or ""),
                    "holding_days": _safe_int(trade.get("holding_days"), 0),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "shares": shares,
                    "gross_pnl": (exit_price - entry_price) * shares,
                    "net_pnl": _safe_float(trade.get("pnl")),
                    "gross_return_pct": ((exit_price - entry_price) / entry_price) * 100.0,
                    "net_return_pct": _safe_float(trade.get("pnl_pct")),
                    "fees": {
                        "buy_commission": buy_commission,
                        "sell_commission": sell_commission,
                        "stamp_tax": stamp_tax,
                        "total": total_fees,
                    },
                    "exit_trigger": str(trade.get("exit_trigger") or ""),
                    "exit_reason": str(trade.get("exit_reason") or trade.get("reason") or ""),
                    "benchmark": {
                        "entry_close": benchmark_entry,
                        "exit_close": benchmark_exit,
                        "return_pct": benchmark_return_pct,
                        "excess_return_pct": excess_return_pct,
                    },
                    "raw_signal": dict(signal_map.get(str(trade.get("ts_code") or "")) or {}),
                }
            )
    limit = max(1, int(limit))
    traces.sort(key=lambda item: abs(_safe_float(item.get("net_pnl"))), reverse=True)
    if selection != "mixed":
        return traces[:limit]

    winners = [item for item in traces if _safe_float(item.get("net_pnl")) > 0]
    losers = [item for item in traces if _safe_float(item.get("net_pnl")) < 0]
    flats = sorted(traces, key=lambda item: abs(_safe_float(item.get("net_pnl"))))
    chosen: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()

    def _pick(items: list[dict[str, Any]], quota: int) -> None:
        for item in items:
            if len(chosen) >= limit or quota <= 0:
                break
            trace_key = (
                item.get("cycle_id"),
                item.get("ts_code"),
                item.get("entry_date"),
                item.get("exit_date"),
            )
            if trace_key in seen_keys:
                continue
            seen_keys.add(trace_key)
            chosen.append(item)
            quota -= 1

    _pick(losers, 2)
    _pick(winners, 2)
    _pick(flats, 1)
    if len(chosen) < limit:
        _pick(traces, limit - len(chosen))
    return chosen[:limit]


def build_calibration_experiment_spec(
    *,
    model_name: str,
    cutoff_dates: list[str],
    min_history_days: int,
    simulation_days: int,
    dry_run_llm: bool = False,
) -> dict[str, Any]:
    return {
        "protocol": {
            "review_window": {"mode": "single_cycle", "size": 1},
            "cutoff_policy": {
                "mode": "sequence",
                "dates": [normalize_date(item) for item in cutoff_dates],
            },
        },
        "dataset": {
            "min_history_days": int(min_history_days),
            "simulation_days": int(simulation_days),
        },
        "model_scope": {
            "experiment_mode": "validation",
            "allowed_models": [str(model_name)],
            "allocator_enabled": False,
            "model_routing_enabled": False,
            "routing_mode": "off",
        },
        "llm": {
            "dry_run": bool(dry_run_llm),
        },
    }


def load_controller_run_summary(run_dir: str | Path) -> dict[str, Any]:
    root = _resolve_output_path(run_dir)
    cycles: list[dict[str, Any]] = []
    for path in root.iterdir():
        match = _CYCLE_FILE_RE.match(path.name)
        if not match or not path.is_file():
            continue
        cycles.append(json.loads(path.read_text(encoding="utf-8")))
    cycles.sort(key=_cycle_sort_key)
    return {
        "run_type": "controller_calibration",
        "run_dir": str(root),
        "summary": aggregate_cycle_metrics(cycles),
        "cycles": cycles,
    }


def compare_validation_runs(
    *,
    bare_summary: dict[str, Any],
    system_summary: dict[str, Any],
) -> dict[str, Any]:
    bare_metrics = dict(bare_summary.get("summary") or {})
    system_metrics = dict(system_summary.get("summary") or {})
    metric_names = [
        "avg_return_pct",
        "median_return_pct",
        "compounded_return_pct",
        "avg_sharpe_ratio",
        "avg_max_drawdown",
        "avg_excess_return",
        "avg_strategy_score",
        "benchmark_pass_rate",
        "profit_cycle_rate",
    ]
    deltas = {
        name: _safe_float(system_metrics.get(name)) - _safe_float(bare_metrics.get(name))
        for name in metric_names
    }
    bare_regimes = dict(bare_metrics.get("regime_breakdown") or {})
    system_regimes = dict(system_metrics.get("regime_breakdown") or {})
    regime_delta: dict[str, dict[str, float]] = {}
    for regime in sorted(set(bare_regimes) | set(system_regimes)):
        regime_delta[regime] = {
            "avg_return_pct": _safe_float(
                dict(system_regimes.get(regime) or {}).get("avg_return_pct")
            ) - _safe_float(dict(bare_regimes.get(regime) or {}).get("avg_return_pct")),
            "avg_sharpe_ratio": _safe_float(
                dict(system_regimes.get(regime) or {}).get("avg_sharpe_ratio")
            ) - _safe_float(dict(bare_regimes.get(regime) or {}).get("avg_sharpe_ratio")),
            "benchmark_pass_rate": _safe_float(
                dict(system_regimes.get(regime) or {}).get("benchmark_pass_rate")
            ) - _safe_float(dict(bare_regimes.get(regime) or {}).get("benchmark_pass_rate")),
        }
    return {
        "bare": bare_metrics,
        "system": system_metrics,
        "delta": deltas,
        "regime_delta": regime_delta,
        "system_worse_than_bare": bool(
            deltas.get("avg_return_pct", 0.0) < 0.0
            or deltas.get("avg_sharpe_ratio", 0.0) < 0.0
            or deltas.get("benchmark_pass_rate", 0.0) < 0.0
        ),
    }


def run_controller_calibration(
    *,
    model_name: str,
    config_path: str | Path,
    cutoff_dates: list[str],
    output_dir: str | Path,
    min_history_days: int,
    simulation_days: int,
    dry_run_llm: bool = False,
) -> dict[str, Any]:
    run_dir = _resolve_output_path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    controller = SelfLearningController(
        output_dir=str(run_dir),
        meeting_log_dir=str(run_dir / "meetings"),
        config_audit_log_path=str(run_dir / "config_audit.jsonl"),
        config_snapshot_dir=str(run_dir / "snapshots"),
    )
    controller.stop_on_freeze = False
    controller.model_name = str(model_name)
    controller.model_config_path = str(_resolve_output_path(config_path))
    controller.current_params = {}
    controller.training_routing_service.reload_investment_model(
        controller,
        controller.model_config_path,
    )
    experiment_spec = build_calibration_experiment_spec(
        model_name=model_name,
        cutoff_dates=cutoff_dates,
        min_history_days=min_history_days,
        simulation_days=simulation_days,
        dry_run_llm=dry_run_llm,
    )
    controller.configure_experiment(experiment_spec)
    if dry_run_llm:
        controller.set_llm_dry_run(True)
    report = controller.run_continuous(max_cycles=len(cutoff_dates))
    summary = load_controller_run_summary(run_dir)
    summary["report"] = report
    summary["experiment_spec"] = experiment_spec
    summary["config_path"] = controller.model_config_path
    return summary
