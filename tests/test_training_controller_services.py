import json

from app.train import SelfLearningController, TrainingResult
from app.training.controller_services import FreezeGateService, TrainingFeedbackService, TrainingPersistenceService


def _make_controller(tmp_path):
    return SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        meeting_log_dir=str(tmp_path / 'meetings'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )


def _make_feedback(*, bias: str = 'tighten_risk'):
    return {
        'sample_count': 8,
        'recommendation': {
            'bias': bias,
            'summary': f'feedback:{bias}',
        },
        'horizons': {
            'T+20': {'hit_rate': 0.30, 'invalidation_rate': 0.40, 'interval_hit_rate': 0.30},
            'T+60': {'hit_rate': 0.42, 'invalidation_rate': 0.36, 'interval_hit_rate': 0.35},
        },
        'brier_like_direction_score': 0.31,
    }


def test_controller_exposes_training_services(tmp_path):
    controller = _make_controller(tmp_path)

    assert isinstance(controller.training_feedback_service, TrainingFeedbackService)
    assert isinstance(controller.freeze_gate_service, FreezeGateService)
    assert isinstance(controller.training_persistence_service, TrainingPersistenceService)


def test_feedback_plan_delegates_to_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    captured = {}

    def fake_build(owner, feedback, *, cycle_id):
        captured['owner'] = owner
        captured['cycle_id'] = cycle_id
        captured['feedback'] = dict(feedback)
        return {'trigger': 'research_feedback', 'summary': 'delegated'}

    monkeypatch.setattr(controller.training_feedback_service, 'build_feedback_optimization_plan', fake_build)

    payload = controller._build_feedback_optimization_plan(_make_feedback(), cycle_id=7)  # pylint: disable=protected-access

    assert payload['summary'] == 'delegated'
    assert captured['owner'] is controller
    assert captured['cycle_id'] == 7
    assert captured['feedback']['sample_count'] == 8


def test_save_cycle_result_delegates_to_persistence_service(tmp_path):
    controller = _make_controller(tmp_path)
    result = TrainingResult(
        cycle_id=3,
        cutoff_date='20240103',
        selected_stocks=['sh.600000'],
        initial_capital=100000,
        final_value=101500,
        return_pct=1.5,
        is_profit=True,
        trade_history=[],
        params={},
        analysis='',
        data_mode='mock',
        selection_mode='meeting',
        agent_used=True,
        llm_used=False,
        benchmark_passed=True,
        review_applied=False,
        config_snapshot_path='',
        optimization_events=[],
        audit_tags={},
    )

    controller._save_cycle_result(result)  # pylint: disable=protected-access

    payload = json.loads((tmp_path / 'training' / 'cycle_3.json').read_text(encoding='utf-8'))
    assert payload['cycle_id'] == 3
    assert payload['return_pct'] == 1.5
    assert payload['benchmark_passed'] is True


def test_generate_report_delegates_to_freeze_gate_service(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)

    monkeypatch.setattr(
        controller.freeze_gate_service,
        'generate_training_report',
        lambda owner: {'status': 'ok', 'owner_bound': owner is controller},
    )

    payload = controller._generate_report()  # pylint: disable=protected-access

    assert payload == {'status': 'ok', 'owner_bound': True}
