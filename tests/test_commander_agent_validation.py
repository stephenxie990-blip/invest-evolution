from invest.agents.reviewers import ReviewDecisionAgent


def test_review_decision_validate_allows_null_trailing_pct():
    agent = ReviewDecisionAgent(llm_caller=False)
    result = {
        "positions": [
            {
                "code": "600519.SH",
                "weight": 0.2,
                "stop_loss_pct": 0.05,
                "take_profit_pct": 0.15,
                "trailing_pct": None,
                "entry_method": "market",
                "source": "contrarian",
                "reasoning": "test",
            }
        ],
        "cash_reserve": 0.3,
        "reasoning": "ok",
    }
    regime = {"params": {"max_positions": 5}}

    validated = agent._validate(result, {"600519.SH"}, regime)
    assert validated["positions"][0]["trailing_pct"] is None
