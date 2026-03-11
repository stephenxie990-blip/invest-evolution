from config import agent_config_registry, config
from invest.agents.base import AgentConfig, InvestAgent


class _DummyAgent(InvestAgent):
    def perceive(self, data):
        return data

    def reason(self, perception):
        return perception

    def act(self, reasoning):
        return reasoning


def test_agent_model_alias_fast_and_deep_follow_global_config(monkeypatch):
    monkeypatch.setattr(config, "llm_fast_model", "global-fast-model")
    monkeypatch.setattr(config, "llm_deep_model", "global-deep-model")
    monkeypatch.setitem(agent_config_registry._configs, "AliasFastAgent", {"llm_model": "fast", "system_prompt": "x"})
    monkeypatch.setitem(agent_config_registry._configs, "AliasDeepAgent", {"llm_model": "deep", "system_prompt": "x"})
    monkeypatch.setitem(agent_config_registry._configs, "AliasDefaultAgent", {"llm_model": "", "system_prompt": "x"})

    assert AgentConfig(name="AliasFastAgent", role="hunter").llm_model == "global-fast-model"
    assert AgentConfig(name="AliasDeepAgent", role="hunter").llm_model == "global-deep-model"
    assert AgentConfig(name="AliasDefaultAgent", role="hunter").llm_model == "global-fast-model"


def test_agent_model_explicit_value_remains_compatible(monkeypatch):
    monkeypatch.setitem(agent_config_registry._configs, "ExplicitAgent", {"llm_model": "custom/provider-model", "system_prompt": "x"})

    cfg = AgentConfig(name="ExplicitAgent", role="hunter")
    assert cfg.llm_model_setting == "custom/provider-model"
    assert cfg.llm_model == "custom/provider-model"


def test_invest_agent_uses_resolved_alias_model(monkeypatch):
    monkeypatch.setattr(config, "llm_fast_model", "alias-fast-model")
    monkeypatch.setitem(agent_config_registry._configs, "RuntimeAliasAgent", {"llm_model": "fast", "system_prompt": "x"})

    agent = _DummyAgent(AgentConfig(name="RuntimeAliasAgent", role="hunter"))
    assert agent.llm is not None
    assert agent.llm.model == "alias-fast-model"
