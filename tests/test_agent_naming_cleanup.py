import invest.agents as agents


def test_old_agent_aliases_are_retired():
    retired = [
        "RegimeAuditorAgent",
        "TrendAgent",
        "ReversionAgent",
        "RiskReviewerAgent",
        "DecisionSynthesizerAgent",
        "EvolutionAdvisorAgent",
        "CommanderAgent",
    ]
    for name in retired:
        assert not hasattr(agents, name), name


def test_canonical_agent_names_are_exported():
    expected = [
        "MarketRegimeAgent",
        "TrendHunterAgent",
        "ContrarianAgent",
        "QualityAgent",
        "DefensiveAgent",
        "StrategistAgent",
        "ReviewDecisionAgent",
        "EvoJudgeAgent",
    ]
    for name in expected:
        assert hasattr(agents, name), name
