
from invest_evolution.application.train import SelfLearningController
from invest_evolution.config import config as live_config
from invest_evolution.config.control_plane import clear_control_plane_cache


def test_controller_bootstraps_llm_components_from_control_plane(monkeypatch, tmp_path):
    control_plane = tmp_path / 'control_plane.yaml'
    control_plane.write_text(
        '\n'.join([
            'llm:',
            '  providers:',
            '    provider_x:',
            '      api_base: https://provider-x.example/v1',
            '      api_key: provider-x-key',
            '  models:',
            '    controller_model:',
            '      provider: provider_x',
            '      model: controller-model',
            '    governance_selector_model:',
            '      provider: provider_x',
            '      model: governance-selector-model',
            '    optimizer_model:',
            '      provider: provider_x',
            '      model: optimizer-model',
            '    trend_model:',
            '      provider: provider_x',
            '      model: trend-model',
            '    judge_model:',
            '      provider: provider_x',
            '      model: judge-model',
            '  bindings:',
            '    controller.main: controller_model',
            '    agent.GovernanceSelector: governance_selector_model',
            '    optimizer.loss_analysis: optimizer_model',
            '    agent.TrendHunter: trend_model',
            '    agent.EvoJudge: judge_model',
            'data:',
            '  runtime_policy:',
            '    allow_online_fallback: false',
            '    allow_capital_flow_sync: false',
        ]),
        encoding='utf-8',
    )

    monkeypatch.setenv('INVEST_CONTROL_PLANE_PATH', str(control_plane))
    monkeypatch.setattr(live_config, 'enable_debate', True)
    monkeypatch.setattr(live_config, 'max_debate_rounds', 1)
    monkeypatch.setattr(live_config, 'max_risk_discuss_rounds', 1)
    clear_control_plane_cache()

    controller = SelfLearningController(
        output_dir=str(tmp_path / 'out'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'state' / 'audit.jsonl'),
        config_snapshot_dir=str(tmp_path / 'state' / 'snapshots'),
    )

    assert controller.llm_caller.model == 'controller-model'
    assert controller.llm_optimizer.llm.model == 'optimizer-model'
    assert controller.runtime_evolution_optimizer is not None
    assert controller.runtime_evolution_optimizer.llm_optimizer is controller.llm_optimizer
    assert controller.agents['governance_selector'].llm.model == 'governance-selector-model'
    assert controller.agents['trend_hunter'].llm.model == 'trend-model'
    assert controller.agents['evo_judge'].llm.model == 'judge-model'
    assert controller.selection_debate_enabled is True
    assert controller.review_risk_debate_enabled is True
