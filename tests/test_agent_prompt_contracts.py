import json

from config import AgentConfigRegistry, agent_config_registry


def test_default_agent_prompts_include_examples_and_negative_constraints():
    expected = [
        "MarketRegime",
        "TrendHunter",
        "Contrarian",
        "Strategist",
        "ReviewDecision",
        "Commander",
        "EvoJudge",
    ]

    for name in expected:
        prompt = agent_config_registry.get_config(name)["system_prompt"]
        assert "少样本示例" in prompt, name
        assert "负例约束" in prompt, name
        assert "只输出一个 JSON 对象" in prompt, name


def test_agent_registry_file_overrides_built_in_prompt(tmp_path):
    config_path = tmp_path / "agents_config.json"
    config_path.write_text(
        json.dumps(
            {
                "TrendHunter": {
                    "system_prompt": "override prompt",
                    "llm_model": "fast",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    registry = AgentConfigRegistry(config_path)

    assert registry.get_config("TrendHunter")["system_prompt"] == "override prompt"
    assert "ReviewDecision" in registry.all()


def test_agent_registry_empty_json_keeps_built_in_defaults(tmp_path):
    config_path = tmp_path / "agents_config.json"
    config_path.write_text("{}", encoding="utf-8")

    registry = AgentConfigRegistry(config_path)

    assert registry.get_config("MarketRegime")["system_prompt"]
    assert registry.get_config("Commander")["system_prompt"]
