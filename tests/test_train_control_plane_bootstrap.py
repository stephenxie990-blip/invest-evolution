
from app.train import SelfLearningController
from config.control_plane import clear_control_plane_cache


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
            '    selection_fast_model:',
            '      provider: provider_x',
            '      model: selection-fast-model',
            '    selection_deep_model:',
            '      provider: provider_x',
            '      model: selection-deep-model',
            '    selection_bull_model:',
            '      provider: provider_x',
            '      model: selection-bull-model',
            '    selection_bear_model:',
            '      provider: provider_x',
            '      model: selection-bear-model',
            '    review_fast_model:',
            '      provider: provider_x',
            '      model: review-fast-model',
            '    review_deep_model:',
            '      provider: provider_x',
            '      model: review-deep-model',
            '    risk_conservative_model:',
            '      provider: provider_x',
            '      model: risk-conservative-model',
            '    risk_judge_model:',
            '      provider: provider_x',
            '      model: risk-judge-model',
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
            '    meeting.selection.fast: selection_fast_model',
            '    meeting.selection.deep: selection_deep_model',
            '    meeting.selection.debate.bull: selection_bull_model',
            '    meeting.selection.debate.bear: selection_bear_model',
            '    meeting.review.fast: review_fast_model',
            '    meeting.review.deep: review_deep_model',
            '    meeting.review.risk.conservative: risk_conservative_model',
            '    meeting.review.risk.judge: risk_judge_model',
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
    clear_control_plane_cache()

    controller = SelfLearningController(
        output_dir=str(tmp_path / 'out'),
        meeting_log_dir=str(tmp_path / 'meetings'),
        config_audit_log_path=str(tmp_path / 'state' / 'audit.jsonl'),
        config_snapshot_dir=str(tmp_path / 'state' / 'snapshots'),
    )

    assert controller.llm_caller.model == 'controller-model'
    assert controller.llm_optimizer.llm.model == 'optimizer-model'
    assert controller.agents['trend_hunter'].llm.model == 'trend-model'
    assert controller.agents['evo_judge'].llm.model == 'judge-model'
    assert controller.selection_meeting.llm is not None
    assert controller.review_meeting.llm is not None
    assert controller.selection_meeting.bull_llm is not None
    assert controller.selection_meeting.bear_llm is not None
    assert controller.review_meeting.conservative_llm is not None
    assert controller.selection_meeting.llm.model == 'selection-fast-model'
    assert controller.review_meeting.llm.model == 'review-fast-model'
    assert controller.selection_meeting.bull_llm.model == 'selection-bull-model'
    assert controller.selection_meeting.bear_llm.model == 'selection-bear-model'
    assert getattr(controller.selection_meeting._debate, 'deep_llm').model == 'selection-deep-model'
    assert getattr(controller.selection_meeting._debate, 'bear_llm').model == 'selection-bear-model'
    assert controller.review_meeting.conservative_llm.model == 'risk-conservative-model'
    assert getattr(controller.review_meeting._risk_debate, 'deep_llm').model == 'review-deep-model'
    assert getattr(controller.review_meeting._risk_debate, 'judge_llm').model == 'risk-judge-model'
