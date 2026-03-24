import json
from pathlib import Path

from invest_evolution.investment.agents.base import AgentConfig


def test_agent_system_prompt_includes_common_contract():
    prompt = AgentConfig(name="TrendHunter", role="hunter").system_prompt
    assert "共同约束" in prompt
    assert "只依据输入中的事实" in prompt
    assert "最终只输出要求的 JSON 对象" in prompt


def test_invest_agent_prompts_include_examples_and_negative_constraints():
    cfg = json.loads(Path('agent_settings/agents_config.json').read_text(encoding='utf-8'))
    expected = [
        'MarketRegime',
        'TrendHunter',
        'Contrarian',
        'Strategist',
        'ReviewDecision',
        'Commander',
        'EvoJudge',
    ]

    for name in expected:
        prompt = cfg[name]['system_prompt']
        assert '少样本示例' in prompt, name
        assert '负例约束' in prompt, name
        assert '只输出一个 JSON 对象' in prompt, name
