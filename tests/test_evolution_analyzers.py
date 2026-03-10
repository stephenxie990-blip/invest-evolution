import json

from invest.evolution.analyzers import LLMAnalyzer, TradeDetail


def test_llm_analyzer_without_callable_falls_back_to_default_result():
    analyzer = LLMAnalyzer()

    result = analyzer.analyze(
        start_date="20240101",
        end_date="20240131",
        benchmark_return=1.2,
        total_return=-0.8,
        trades=[],
    )

    assert result.market_regime == "neutral"
    assert result.stop_loss_suggestion == 0.05
    assert result.take_profit_suggestion == 0.15
    assert result.position_size_suggestion == 0.2
    assert result.confidence == 0.5
    assert result.raw_response == ""
    assert result.suggestions
    assert "LLM 分析不可用" in result.suggestions[0]


def test_llm_analyzer_uses_injected_callable_response():
    captured = {}

    def fake_llm(prompt: str) -> str:
        captured["prompt"] = prompt
        return json.dumps({
            "factor_adjustments": {"momentum_weight": 0.11},
            "stop_loss_suggestion": 0.06,
            "take_profit_suggestion": 0.13,
            "position_size_suggestion": 0.18,
            "market_regime": "bear",
            "confidence": 0.73,
            "suggestions": ["降低仓位"],
        }, ensure_ascii=False)

    analyzer = LLMAnalyzer(llm_callable=fake_llm)
    trades = [
        TradeDetail(
            date="20240102",
            code="sh.600000",
            action="SELL",
            price=10.5,
            shares=1000,
            pnl=1200,
            pnl_pct=12.0,
            reason="趋势突破",
        )
    ]

    result = analyzer.analyze(
        start_date="20240101",
        end_date="20240131",
        benchmark_return=1.2,
        total_return=5.6,
        trades=trades,
    )

    assert "20240101" in captured["prompt"]
    assert "20240131" in captured["prompt"]
    assert result.factor_adjustments == {"momentum_weight": 0.11}
    assert result.stop_loss_suggestion == 0.06
    assert result.take_profit_suggestion == 0.13
    assert result.position_size_suggestion == 0.18
    assert result.market_regime == "bear"
    assert result.confidence == 0.73
    assert result.suggestions == ["降低仓位"]
