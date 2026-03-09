import json
from pathlib import Path


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
