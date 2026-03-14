from typing import Any, cast

from invest.contracts import AgentContext, ModelOutput, SignalPacket, StockSignal
from invest.meetings.selection import SelectionMeeting


class _StubAgent:
    def __init__(self, code: str, label: str):
        self.code = code
        self.label = label
        self.calls = 0

    def analyze_context(self, agent_context):
        self.calls += 1
        return {
            "picks": [{"code": self.code, "score": 0.9, "reasoning": f"{self.label} thesis"}],
            "overall_view": f"{self.label} overall",
            "confidence": 0.8,
        }


def _build_output(model_name: str, code: str):
    packet = SignalPacket(
        as_of_date="20240131",
        model_name=model_name,
        config_name=f"{model_name}_v1",
        regime="oscillation",
        signals=[StockSignal(code=code, score=0.9, rank=1)],
        selected_codes=[code],
        max_positions=1,
        cash_reserve=0.2,
        params={"stop_loss_pct": 0.05, "take_profit_pct": 0.15},
        reasoning="test",
    )
    context = AgentContext(
        as_of_date="20240131",
        model_name=model_name,
        config_name=f"{model_name}_v1",
        summary="summary",
        narrative="narrative",
        regime="oscillation",
        market_stats={"market_breadth": 0.5},
        stock_summaries=cast(Any, [{"code": code, "algo_score": 0.9}]),
        candidate_codes=[code],
    )
    return ModelOutput(model_name=model_name, config_name=f"{model_name}_v1", signal_packet=packet, agent_context=context)


def test_selection_meeting_uses_quality_agent_for_value_quality():
    trend = _StubAgent("TREND", "trend")
    reversion = _StubAgent("REV", "reversion")
    quality = _StubAgent("AAA", "quality")
    defensive = _StubAgent("DEF", "defensive")
    meeting = SelectionMeeting(llm_caller=None, trend_hunter=trend, contrarian=reversion, quality_agent=quality, defensive_agent=defensive)
    out = meeting.run_with_model_output(_build_output("value_quality", code="AAA"))
    assert quality.calls == 1
    assert trend.calls == 0
    assert reversion.calls == 0
    assert defensive.calls == 0
    assert out["strategy_advice"]["selected_codes"] == ["AAA"]
    assert out["meeting_log"]["hunters"][0]["name"] == "quality_agent"


def test_selection_meeting_uses_defensive_agent_for_defensive_model():
    trend = _StubAgent("TREND", "trend")
    reversion = _StubAgent("REV", "reversion")
    quality = _StubAgent("AAA", "quality")
    defensive = _StubAgent("DDD", "defensive")
    meeting = SelectionMeeting(llm_caller=None, trend_hunter=trend, contrarian=reversion, quality_agent=quality, defensive_agent=defensive)
    out = meeting.run_with_model_output(_build_output("defensive_low_vol", code="DDD"))
    assert defensive.calls == 1
    assert trend.calls == 0
    assert reversion.calls == 0
    assert quality.calls == 0
    assert out["strategy_advice"]["selected_codes"] == ["DDD"]
    assert out["meeting_log"]["hunters"][0]["name"] == "defensive_agent"
