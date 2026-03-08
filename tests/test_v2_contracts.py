from invest.contracts import AgentContext, EvalReport, ModelOutput, SignalPacket, StockSignal, StrategyAdvice


def test_v2_contracts_round_trip():
    signal = StockSignal(code="sh.600000", score=0.91, rank=1, factor_values={"rsi": 54.0})
    packet = SignalPacket(
        as_of_date="20240131",
        model_name="momentum",
        config_name="momentum_v1",
        regime="bull",
        signals=[signal],
        selected_codes=["sh.600000"],
        max_positions=3,
        cash_reserve=0.2,
        params={"stop_loss_pct": 0.05},
        reasoning="test",
    )
    context = AgentContext(
        as_of_date="20240131",
        model_name="momentum",
        config_name="momentum_v1",
        summary="市场偏强",
        narrative="市场偏强，候选动量延续",
        regime="bull",
        market_stats={"market_breadth": 0.6},
        stock_summaries=[{"code": "sh.600000", "algo_score": 0.91}],
        candidate_codes=["sh.600000"],
    )
    output = ModelOutput(model_name="momentum", config_name="momentum_v1", signal_packet=packet, agent_context=context)
    advice = StrategyAdvice(source="selection_meeting", selected_codes=["sh.600000"], confidence=0.8)
    report = EvalReport(cycle_id=1, as_of_date="20240131", return_pct=2.3, total_pnl=2300, total_trades=4, win_rate=0.5, regime="bull", is_profit=True)

    assert packet.to_dict()["signals"][0]["code"] == "sh.600000"
    assert context.to_dict()["summary"] == "市场偏强"
    assert output.to_dict()["signal_packet"]["selected_codes"] == ["sh.600000"]
    assert advice.to_dict()["source"] == "selection_meeting"
    assert report.to_dict()["return_pct"] == 2.3
    assert report.to_dict()["is_profit"] is True
