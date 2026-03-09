from invest.agents.base import AgentConfig


def test_agent_system_prompt_includes_common_contract():
    prompt = AgentConfig(name="TrendHunter", role="hunter").system_prompt
    assert "共同约束" in prompt
    assert "只依据输入中的事实" in prompt
    assert "最终只输出要求的 JSON 对象" in prompt
