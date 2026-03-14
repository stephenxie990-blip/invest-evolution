"""Public foundation-layer exports."""

from invest.foundation.engine.contracts import Position
from invest.foundation.engine.simulator import SimulatedTrader, run_simulation_with_plan
from invest.foundation.metrics.benchmark import BenchmarkEvaluator
from invest.foundation.metrics.cycle import StrategyEvaluator
from invest.foundation.risk.controller import (
    clamp_position_size,
    clamp_stop_loss_pct,
    clamp_take_profit_pct,
    sanitize_risk_params,
)

__all__ = [
    "Position",
    "SimulatedTrader",
    "run_simulation_with_plan",
    "BenchmarkEvaluator",
    "StrategyEvaluator",
    "sanitize_risk_params",
    "clamp_position_size",
    "clamp_stop_loss_pct",
    "clamp_take_profit_pct",
]
