from invest.foundation.metrics import StrategyEvaluator


def test_strategy_evaluator_uses_configured_weights_and_thresholds():
    evaluator = StrategyEvaluator(
        policy={
            "weights": {"signal_accuracy": 0.2, "timing": 0.2, "risk_control": 0.6},
            "defaults": {"risk_control_base_score": 0.1},
            "thresholds": {
                "signal_accuracy_low": 0.5,
                "timing_low": 0.5,
                "risk_control_low": 0.5,
                "win_rate_low": 0.5,
                "win_rate_high": 0.7,
                "high_trade_count": 2,
            },
        }
    )
    cycle_result = {
        "cycle_id": 1,
        "return_pct": -2.0,
        "profit_loss": -2000.0,
        "total_trades": 3,
        "winning_trades": 1,
        "losing_trades": 2,
        "win_rate": 1 / 3,
    }
    trade_history = [
        {"pnl": 10, "reason": "正常卖出"},
        {"pnl": -5, "reason": "触发止损"},
        {"pnl": -2, "reason": "正常卖出"},
    ]
    daily_records = [
        {"total_value": 100.0},
        {"total_value": 80.0},
        {"total_value": 90.0},
    ]

    result = evaluator.evaluate(cycle_result, trade_history, daily_records)

    assert round(result.signal_accuracy, 4) == round(1 / 3, 4)
    assert round(result.timing_score, 4) == 0.8
    assert round(result.risk_control_score, 4) == round((1 / 3) + 0.1, 4)
    expected = result.signal_accuracy * 0.2 + result.timing_score * 0.2 + result.risk_control_score * 0.6
    assert round(result.overall_score, 6) == round(expected, 6)
    assert result.suggestions is not None
    assert "信号准确率低，建议优化选股策略参数" in result.suggestions
    assert "交易过于频繁，建议减少无效交易" in result.suggestions
