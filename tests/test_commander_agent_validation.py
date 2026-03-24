from invest_evolution.investment.agents.specialists import ReviewDecisionAgent


def test_review_decision_fallback_returns_review_payload_only():
    agent = ReviewDecisionAgent(llm_caller=False)
    result = agent.decide(
        facts={
            "agent_accuracy": {
                "trend_hunter": {"accuracy": 0.2, "traded_count": 5},
                "contrarian": {"accuracy": 0.7, "traded_count": 6},
                "quality_agent": {"accuracy": 0.8, "traded_count": 1},
            }
        },
        strategy_analysis={"problems": ["回撤偏大"], "suggestions": ["降低仓位"]},
        evo_assessment={
            "evolution_direction": "conservative",
            "param_adjustments": {"position_size": 0.15, "cash_reserve": None},
            "suggestions": ["先收紧风险暴露"],
        },
        current_params={"position_size": 0.2},
    )
    assert set(result.keys()) >= {"strategy_suggestions", "param_adjustments", "agent_weight_adjustments", "reasoning"}
    assert "positions" not in result
    assert "cash_reserve" not in result
    assert result["param_adjustments"] == {"position_size": 0.15}
    assert result["agent_weight_adjustments"]["trend_hunter"] == 0.7
    assert result["agent_weight_adjustments"]["contrarian"] == 1.2
    assert result["agent_weight_adjustments"]["quality_agent"] == 1.0
