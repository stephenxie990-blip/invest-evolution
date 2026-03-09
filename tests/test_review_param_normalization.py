from invest.meetings.review import ReviewMeeting


def test_review_decision_normalizes_percentage_style_adjustments():
    meeting = ReviewMeeting(llm_caller=None)
    facts = {"agent_accuracy": {"trend_hunter": {}, "contrarian": {}}}
    result = {
        "strategy_suggestions": [],
        "param_adjustments": {"cash_reserve": 20, "take_profit_pct": 50, "position_size": 15},
        "agent_weight_adjustments": {"trend_hunter": 1.0},
        "reasoning": "ok",
    }
    cleaned = meeting._validate_decision(result, facts)
    assert cleaned["param_adjustments"]["cash_reserve"] == 0.2
    assert cleaned["param_adjustments"]["take_profit_pct"] == 0.5
    assert cleaned["param_adjustments"]["position_size"] == 0.15
