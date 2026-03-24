from pathlib import Path

# Agent Foundation Imports
import invest_evolution.investment.agents as agents
from invest_evolution.investment.agents import (
    MarketRegimeAgent,
    TrendHunterAgent,
    ContrarianAgent,
    InvestAgent,
    AgentConfig,
)
from invest_evolution.config import agent_config_registry, config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# --- Helper Classes ---

class _DummyAgent(InvestAgent):
    def perceive(self, data): return data
    def reason(self, perception): return perception
    def act(self, reasoning): return reasoning


# --- Agent Model Resolution Tests ---

def test_agent_model_alias_resolution(monkeypatch):
    monkeypatch.setattr(config, "llm_fast_model", "f-model")
    monkeypatch.setattr(config, "llm_deep_model", "d-model")
    monkeypatch.setitem(agent_config_registry._configs, "FastAgent", {"llm_model": "fast", "system_prompt": "x"})

    cfg = AgentConfig(name="FastAgent", role="hunter")
    assert cfg.llm_model == "f-model"


# --- Agent Naming & Exports Tests ---

def test_agent_names_and_exports():
    expected = ["MarketRegimeAgent", "TrendHunterAgent", "ContrarianAgent", "StrategistAgent"]
    for name in expected:
        assert hasattr(agents, name)

    retired = ["RegimeAuditorAgent", "TrendAgent"]
    for name in retired:
        assert not hasattr(agents, name)


# --- Agent Role Boundaries & Prompts Tests ---

def test_market_regime_agent_output_structure():
    agent = MarketRegimeAgent(llm_caller=None)
    result = agent.analyze({
        "advance_ratio_5d": 0.5, "avg_change_5d": 0.1, "avg_change_20d": 0.2,
        "above_ma20_ratio": 0.5, "avg_volatility": 0.02,
    })
    assert "regime" in result
    assert "params" not in result


def test_agent_role_differentiation_in_registry():
    review_cfg = agent_config_registry.get_config("ReviewDecision")
    commander_cfg = agent_config_registry.get_config("Commander")
    assert "复盘决策综合员" in review_cfg.get("system_prompt", "")
    assert "总指挥官" in commander_cfg.get("system_prompt", "")


def test_hunter_agents_omit_execution_params_in_fallback():
    trend = TrendHunterAgent(llm_caller=False)
    contrarian = ContrarianAgent(llm_caller=False)

    for agent in (trend, contrarian):
        result = agent._fallback_analysis([{"code": "X", "rsi": 50}])
        assert "stop_loss_pct" not in result["picks"][0]
