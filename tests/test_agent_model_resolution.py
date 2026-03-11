from config.control_plane import clear_control_plane_cache
import config as config_module
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


def test_invest_agent_uses_global_alias_model_for_registry_alias(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, 'PROJECT_ROOT', tmp_path)
    (tmp_path / 'config').mkdir(parents=True, exist_ok=True)
    control_plane = tmp_path / 'config' / 'control_plane.yaml'
    control_plane.write_text(
        '\n'.join([
            'llm:',
            '  providers:',
            '    provider_a:',
            '      api_base: https://provider.example/v1',
            '      api_key: test-key',
            '  models:',
            '    model_fast:',
            '      provider: provider_a',
            '      model: cp-fast-model',
            '  bindings:',
            '    defaults.fast: model_fast',
        ]),
        encoding='utf-8',
    )
    monkeypatch.delenv('INVEST_CONTROL_PLANE_PATH', raising=False)
    clear_control_plane_cache()
    monkeypatch.setattr(config, "llm_fast_model", "alias-fast-model")
    monkeypatch.setitem(agent_config_registry._configs, "RuntimeAliasAgent", {"llm_model": "fast", "system_prompt": "x"})

    agent = _DummyAgent(AgentConfig(name="RuntimeAliasAgent", role="hunter"))
    assert agent.llm is not None
    assert agent.llm.model == "alias-fast-model"
