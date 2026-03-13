
from typing import Any, cast

from invest.agents import MarketRegimeAgent, ReviewDecisionAgent, TrendHunterAgent, ContrarianAgent
from config import agent_config_registry


def test_market_regime_agent_no_longer_emits_params():
    agent = MarketRegimeAgent(llm_caller=None)
    result = agent.analyze({
        "advance_ratio_5d": 0.5,
        "avg_change_5d": 0.1,
        "avg_change_20d": 0.2,
        "above_ma20_ratio": 0.5,
        "avg_volatility": 0.02,
    })
    assert "params" not in result
    assert set(result.keys()) >= {"regime", "confidence", "suggested_exposure", "reasoning", "source"}


def test_review_decision_agent_uses_clear_role_prompt():
    agent = ReviewDecisionAgent(llm_caller=None)
    prompt = agent.config.system_prompt
    assert "复盘决策综合员" in prompt or "系统总指挥官" not in prompt


def test_agent_settings_distinguish_review_decision_and_system_commander():
    review_cfg = agent_config_registry.get_config("ReviewDecision")
    commander_cfg = agent_config_registry.get_config("Commander")
    assert "复盘决策综合员" in review_cfg.get("system_prompt", "")
    assert "总指挥官" in commander_cfg.get("system_prompt", "")


def test_hunter_agents_do_not_emit_execution_params():
    trend = TrendHunterAgent(llm_caller=False)
    contrarian = ContrarianAgent(llm_caller=False)

    trend_result = trend._fallback_analysis([{
        "code": "AAA", "trend_score": 0.8, "algo_score": 0.8, "ma_trend": "多头", "macd": "金叉", "rsi": 55
    }])
    contrarian_result = contrarian._fallback_analysis([{
        "code": "BBB", "contrarian_score": 0.7, "algo_score": 0.7, "rsi": 28, "bb_pos": 0.2, "change_5d": -4
    }])

    for result in (trend_result, contrarian_result):
        assert result["picks"]
        pick = result["picks"][0]
        assert "stop_loss_pct" not in pick
        assert "take_profit_pct" not in pick



def test_strategist_review_report_does_not_emit_execution_params():
    from invest.agents import StrategistAgent
    from types import SimpleNamespace

    agent = StrategistAgent(llm_caller=None)
    report = SimpleNamespace(regime="bull", selected_codes=["AAA"])
    result = agent.review_report(cast(Any, report))
    assert isinstance(result.get("concerns", []), list)
