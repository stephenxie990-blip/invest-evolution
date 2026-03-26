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


def test_parse_response_dry_run_payload_does_not_emit_fallback_warning(caplog):
    optimizer = LLMOptimizer()

    with caplog.at_level("WARNING"):
        result = optimizer._parse_response('{"dry_run": true}', {"cycle_id": 3})

    assert result.cause == "策略表现不佳，需要调整参数"
    assert "解析 LLM 响应失败或为空占位，使用默认分析" not in caplog.text


def test_analyze_loss_short_circuits_llm_call_when_dry_run(caplog):
    class DryRunLLM:
        dry_run = True

        def call(self, *args, **kwargs):  # pragma: no cover - should never be reached
            raise AssertionError("dry_run should bypass llm.call")

    optimizer = LLMOptimizer(llm_caller=DryRunLLM())

    with caplog.at_level("WARNING"):
        result = optimizer.analyze_loss(
            {"cycle_id": 9, "return_pct": -1.2, "total_trades": 3, "win_rate": 0.0},
            [],
        )

    assert result.cause == "策略表现不佳，需要调整参数"
    assert optimizer.analysis_history[-1]["cycle_id"] == 9
    assert "LLM 分析失败" not in caplog.text
    assert "解析 LLM 响应失败或为空占位，使用默认分析" not in caplog.text
