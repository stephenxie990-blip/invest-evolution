from unittest.mock import MagicMock

from app.commander import CommanderConfig, InvestmentBodyService
from app.train import SelfLearningController


def test_thinking_excerpt_accepts_dict(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        meeting_log_dir=str(tmp_path / 'meetings'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )
    assert controller._thinking_excerpt({'reasoning': '市场震荡，控制仓位'}) == '市场震荡，控制仓位'


def test_run_cycles_uses_skip_meta_for_no_data(tmp_path):
    cfg = CommanderConfig(mock_mode=True, autopilot_enabled=False, heartbeat_enabled=False, bridge_enabled=False)
    cfg.training_output_dir = tmp_path / 'training'
    cfg.meeting_log_dir = tmp_path / 'meetings'
    cfg.config_audit_log_path = tmp_path / 'audit' / 'changes.jsonl'
    cfg.config_snapshot_dir = tmp_path / 'snapshots'
    cfg.training_lock_file = tmp_path / 'state' / 'training.lock'

    body = InvestmentBodyService(cfg)
    body.controller.run_training_cycle = MagicMock(return_value=None)
    body.controller.last_cycle_meta = {
        'status': 'no_data',
        'cycle_id': 1,
        'cutoff_date': '20240229',
        'stage': 'selection',
        'reason': '无可交易标的',
        'timestamp': '2026-03-08T01:00:00',
    }

    import asyncio
    out = asyncio.run(body.run_cycles(rounds=1, force_mock=False))

    assert out['results'][0]['status'] == 'no_data'
    assert out['results'][0]['cycle_id'] == 1
    assert out['results'][0]['reason'] == '无可交易标的'
    assert out['results'][0]['stage'] == 'selection'


def test_build_mock_provider_respects_history_window():
    from app.train import _build_mock_provider
    provider = _build_mock_provider()
    diag = provider.diagnose_training_data(provider.random_cutoff_date(), stock_count=30, min_history_days=200)
    assert diag['eligible_stock_count'] > 0


def test_set_mock_mode_updates_agent_llms(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        meeting_log_dir=str(tmp_path / 'meetings'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )
    controller.set_mock_mode(True)
    assert controller.llm_caller.dry_run is True
    assert all(getattr(agent.llm, 'dry_run', False) is True for agent in controller.agents.values() if getattr(agent, 'llm', None) is not None)


def test_save_cycle_result_serializes_numpy_bool(tmp_path):
    import json
    import numpy as np
    from app.train import TrainingResult
    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        meeting_log_dir=str(tmp_path / 'meetings'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )
    result = TrainingResult(
        cycle_id=1,
        cutoff_date='20240101',
        selected_stocks=['sh.600000'],
        initial_capital=100000,
        final_value=101000,
        return_pct=1.0,
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
        optimization_events=[{'ok': np.bool_(True)}],
        audit_tags={'benchmark_passed': np.bool_(True)},
    )
    controller._save_cycle_result(result)
    payload = json.loads((tmp_path / 'training' / 'cycle_1.json').read_text(encoding='utf-8'))
    assert payload['audit_tags']['benchmark_passed'] is True
    assert payload['optimization_events'][0]['ok'] is True
