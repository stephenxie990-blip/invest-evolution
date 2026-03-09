from invest.evolution.llm_optimizer import LLMOptimizer


def test_parse_response_falls_back_on_dry_run_payload():
    optimizer = LLMOptimizer()
    result = optimizer._parse_response('{"dry_run": true}', {"cycle_id": 1})

    assert result.cause == "策略表现不佳，需要调整参数"
    assert result.strategy_adjustments["stop_loss_pct"] == 0.05
    assert "增加趋势确认" in result.suggestions


def test_parse_response_falls_back_on_empty_object():
    optimizer = LLMOptimizer()
    result = optimizer._parse_response('{}', {"cycle_id": 2})

    assert result.cause == "策略表现不佳，需要调整参数"
    assert result.strategy_adjustments["position_size"] == 0.15
