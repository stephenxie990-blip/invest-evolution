from invest_evolution.investment.foundation.evaluation_policy import (
    compute_monthly_turnover,
)
from invest_evolution.investment.foundation.metrics import BenchmarkEvaluator, StrategyEvaluator


def test_strategy_evaluator_ignores_buy_records_when_actions_are_present():
    evaluator = StrategyEvaluator(
        policy={
            "weights": {"signal_accuracy": 1.0, "timing": 0.0, "risk_control": 0.0},
            "defaults": {"risk_control_base_score": 0.0},
        }
    )

    result = evaluator.evaluate(
        {"cycle_id": 1, "return_pct": 1.0, "profit_loss": 100.0},
        trade_history=[
            {"action": "买入", "pnl": 0.0, "reason": "开仓"},
            {"action": "卖出", "pnl": 12.0, "reason": "止盈", "exit_trigger": "take_profit"},
            {"action": "买入", "pnl": 0.0, "reason": "再次开仓"},
        ],
        daily_records=[{"total_value": 100.0}, {"total_value": 101.0}],
    )

    assert result.signal_accuracy == 1.0
    assert result.risk_control_score == 1.0


def test_benchmark_evaluator_monthly_turnover_uses_trade_notional():
    trade_history = [
        {"action": "买入", "price": 10.0, "shares": 10},
        {"action": "卖出", "price": 12.0, "shares": 10},
    ]
    turnover = compute_monthly_turnover(
        trade_history,
        daily_values=[100.0, 110.0, 120.0],
    )

    assert round(turnover, 4) == 2.0

    metrics = BenchmarkEvaluator().evaluate(
        daily_values=[100.0, 110.0, 120.0],
        benchmark_daily_values=[100.0, 101.0, 102.0],
        trade_history=trade_history,
    )

    assert round(metrics.monthly_turnover, 4) == 2.0
