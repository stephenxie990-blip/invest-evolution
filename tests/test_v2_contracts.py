from types import SimpleNamespace
from typing import Any, cast

from invest_evolution.investment.contracts import (
    AgentContext,
    EvalReport,
    ManagerOutput,
    SignalPacket,
    SignalPacketContext,
    StockSignal,
    StrategyAdvice,
    resolve_agent_context_confidence,
)


def test_v2_contracts_round_trip():
    signal = StockSignal(code="sh.600000", score=0.91, rank=1, factor_values={"rsi": 54.0})
    packet = SignalPacket(
        as_of_date="20240131",
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        regime="bull",
        signals=[signal],
        selected_codes=["sh.600000"],
        max_positions=3,
        cash_reserve=0.2,
        params={"stop_loss_pct": 0.05},
        reasoning="test",
        context=SignalPacketContext(
            market_stats={"market_breadth": 0.6},
            stock_summaries=cast(Any, [{"code": "sh.600000", "algo_score": 0.91}]),
            raw_summaries=cast(Any, [{"code": "sh.600000", "algo_score": 0.91, "vol_ratio": 1.2}]),
            debug_metadata={"source": "unit-test"},
        ),
        metadata={"legacy_market_stats": {"market_breadth": 0.6}},
    )
    context = AgentContext(
        as_of_date="20240131",
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        summary="市场偏强",
        narrative="市场偏强，候选动量延续",
        regime="bull",
        confidence=0.84,
        market_stats={"market_breadth": 0.6},
        stock_summaries=cast(Any, [{"code": "sh.600000", "algo_score": 0.91}]),
        candidate_codes=["sh.600000"],
    )
    output = ManagerOutput(
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        signal_packet=packet,
        agent_context=context,
    )
    advice = StrategyAdvice(source="selection_meeting", selected_codes=["sh.600000"], confidence=0.8)
    report = EvalReport(cycle_id=1, as_of_date="20240131", return_pct=2.3, total_pnl=2300, total_trades=4, win_rate=0.5, regime="bull", is_profit=True)

    assert packet.to_dict()["signals"][0]["code"] == "sh.600000"
    assert packet.to_dict()["context"]["market_stats"]["market_breadth"] == 0.6
    assert packet.to_dict()["metadata"]["legacy_market_stats"]["market_breadth"] == 0.6
    assert context.to_dict()["summary"] == "市场偏强"
    assert context.to_dict()["confidence"] == 0.84
    assert output.to_dict()["signal_packet"]["selected_codes"] == ["sh.600000"]
    assert advice.to_dict()["source"] == "selection_meeting"
    assert report.to_dict()["return_pct"] == 2.3
    assert report.to_dict()["is_profit"] is True


def test_stock_summary_view_remains_mapping_compatible():
    context = AgentContext(
        as_of_date="20240131",
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        summary="市场偏强",
        narrative="市场偏强，候选动量延续",
        regime="bull",
        stock_summaries=cast(Any, [{"code": "sh.600000", "algo_score": "0.91", "custom_flag": True}]),
    )

    summary = context.stock_summaries[0]

    assert summary["code"] == "sh.600000"
    assert summary.get("algo_score") == 0.91
    assert dict(summary)["custom_flag"] is True


def test_agent_context_effective_confidence_prefers_explicit_field():
    context = AgentContext(
        as_of_date="20240131",
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        summary="市场偏强",
        narrative="市场偏强",
        regime="bull",
        confidence=0.81,
        metadata={"confidence": 0.33},
    )

    assert context.effective_confidence() == 0.81


def test_agent_context_effective_confidence_falls_back_to_metadata():
    context = AgentContext(
        as_of_date="20240131",
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        summary="市场偏强",
        narrative="市场偏强",
        regime="bull",
        confidence=None,  # type: ignore[arg-type]
        metadata={"confidence": 0.67},
    )

    assert context.effective_confidence() == 0.67


def test_agent_context_effective_confidence_clamps_to_unit_interval():
    context = AgentContext(
        as_of_date="20240131",
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        summary="市场偏强",
        narrative="市场偏强",
        regime="bull",
        confidence=1.8,
        metadata={"confidence": -0.3},
    )
    metadata_only = AgentContext(
        as_of_date="20240131",
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        summary="市场偏强",
        narrative="市场偏强",
        regime="bull",
        confidence=None,  # type: ignore[arg-type]
        metadata={"confidence": -0.3},
    )

    assert context.effective_confidence() == 1.0
    assert metadata_only.effective_confidence() == 0.0


def test_resolve_agent_context_confidence_supports_legacy_objects():
    legacy = SimpleNamespace(confidence="", metadata={"confidence": "1.7"})
    invalid_explicit = SimpleNamespace(confidence="bad", metadata={"confidence": "0.64"})

    assert resolve_agent_context_confidence(legacy, default=0.72) == 1.0
    assert resolve_agent_context_confidence(invalid_explicit, default=0.72) == 0.64
