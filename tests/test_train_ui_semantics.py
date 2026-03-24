from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import invest_evolution.config as config_module

from invest_evolution.application.commander_main import CommanderConfig, InvestmentBodyService
from invest_evolution.application.train import SelfLearningController


def test_thinking_excerpt_accepts_dict(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )
    assert controller._thinking_excerpt({'reasoning': '市场震荡，控制仓位'}) == '市场震荡，控制仓位'


def test_run_cycles_uses_skip_meta_for_no_data(tmp_path):
    cfg = CommanderConfig(mock_mode=True, autopilot_enabled=False, heartbeat_enabled=False, bridge_enabled=False)
    cfg.training_output_dir = tmp_path / 'training'
    cfg.artifact_log_dir = tmp_path / 'artifacts'
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


def test_run_training_cycle_honors_forced_cutoff_env(monkeypatch, tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )

    monkeypatch.setenv('INVEST_FORCE_CUTOFF_DATE', '2025-12-01')

    observed = {}

    def fake_load(cutoff_date, **kwargs):
        observed['cutoff_date'] = cutoff_date
        raise RuntimeError('stop_after_cutoff')

    monkeypatch.setattr(controller.data_manager, 'load_stock_data', fake_load)

    try:
        controller.run_training_cycle()
    except RuntimeError as exc:
        assert str(exc) == 'stop_after_cutoff'

    assert observed['cutoff_date'] == '20251201'


def test_build_mock_provider_respects_history_window():
    from invest_evolution.application.train import _build_mock_provider
    provider = _build_mock_provider()
    diag = provider.diagnose_training_data(provider.random_cutoff_date(), stock_count=30, min_history_days=200)
    eligible_stock_count = cast(int, diag['eligible_stock_count'])
    assert eligible_stock_count > 0


def test_set_llm_dry_run_updates_agent_llms_and_keeps_mock_alias(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )
    controller.set_llm_dry_run(True)
    assert controller.llm_caller.dry_run is True
    assert controller.llm_mode == 'dry_run'
    assert all(getattr(agent.llm, 'dry_run', False) is True for agent in controller.agents.values() if getattr(agent, 'llm', None) is not None)

    controller.set_mock_mode(False)
    assert controller.llm_caller.dry_run is False
    assert controller.llm_mode == 'live'


def test_controller_respects_debate_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module.config, 'enable_debate', False)
    monkeypatch.setattr(config_module.config, 'max_debate_rounds', 3)
    monkeypatch.setattr(config_module.config, 'max_risk_discuss_rounds', 2)

    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )

    assert controller.selection_debate_enabled is False
    assert controller.review_risk_debate_enabled is False


def test_save_cycle_result_serializes_numpy_bool(tmp_path):
    import json
    import numpy as np
    from invest_evolution.application.train import TrainingResult
    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
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
    assert payload['optimization_events'][0]['lineage'] == {}
    assert payload['optimization_events'][0]['stage'] == ''
    assert payload['trades'] == []


def test_commander_result_dict_serializes_numpy_bool(tmp_path):
    import numpy as np
    from invest_evolution.application.train import TrainingResult
    from invest_evolution.application.commander_main import InvestmentBodyService, CommanderConfig

    cfg = CommanderConfig(mock_mode=True, autopilot_enabled=False, heartbeat_enabled=False, bridge_enabled=False)
    cfg.training_output_dir = tmp_path / 'training'
    cfg.artifact_log_dir = tmp_path / 'artifacts'
    cfg.config_audit_log_path = tmp_path / 'audit' / 'changes.jsonl'
    cfg.config_snapshot_dir = tmp_path / 'snapshots'

    body = InvestmentBodyService(cfg)
    result = TrainingResult(
        cycle_id=1,
        cutoff_date='20240101',
        selected_stocks=['sh.600000'],
        initial_capital=100000,
        final_value=101000,
        return_pct=1.0,
        is_profit=bool(np.bool_(True)),
        trade_history=[],
        params={'x': np.int64(1)},
        analysis='',
        data_mode='mock',
        selection_mode='meeting',
        agent_used=bool(np.bool_(True)),
        llm_used=bool(np.bool_(False)),
        benchmark_passed=bool(np.bool_(True)),
        review_applied=bool(np.bool_(False)),
        config_snapshot_path='',
        optimization_events=[],
        audit_tags={'benchmark_passed': np.bool_(True)},
        experiment_spec={'protocol': {'seed': 7}},
        execution_snapshot={
            'runtime_overrides': {'x': np.int64(1)},
            'basis_stage': 'pre_optimization',
        },
        run_context={
            'active_runtime_config_ref': 'cfg.yaml',
            'candidate_runtime_config_ref': '',
            'runtime_overrides': {'x': np.int64(1)},
            'review_basis_window': {'mode': 'single_cycle', 'size': 1, 'cycle_ids': [1], 'current_cycle_id': 1},
            'fitness_source_cycles': [],
            'promotion_decision': {'status': 'not_evaluated', 'applied_to_active': False},
        },
        promotion_record={'status': 'not_evaluated', 'gate_status': 'not_applicable'},
        lineage_record={'lineage_status': 'active_only', 'active_runtime_config_ref': 'cfg.yaml'},
        review_decision={
            'reasoning': 'tighten risk',
            'causal_diagnosis': {'primary_driver': 'benchmark_gap'},
            'similarity_summary': {'matched_cycle_ids': [2]},
        },
        causal_diagnosis={'primary_driver': 'benchmark_gap'},
        similarity_summary={'matched_cycle_ids': [2]},
        similar_results=[{'cycle_id': 2, 'return_pct': -0.8}],
        realism_metrics={'avg_trade_amount': np.float64(1234.5), 'trade_record_count': np.int64(2)},
        research_artifacts={'saved_case_count': np.int64(3), 'saved_attribution_count': np.int64(2)},
        ab_comparison={'comparison': {'winner': 'candidate', 'return_lift_pct': np.float64(0.7)}},
    )
    result.research_feedback = {'recommendation': {'bias': 'tighten_risk'}}
    payload = body._to_result_dict(result)
    assert payload['is_profit'] is True
    assert payload['benchmark_passed'] is True
    assert payload['params']['x'] == 1
    assert payload['research_feedback']['recommendation']['bias'] == 'tighten_risk'
    assert payload['research_artifacts']['saved_case_count'] == 3
    assert payload['ab_comparison']['comparison']['winner'] == 'candidate'
    assert payload['ab_comparison']['comparison']['return_lift_pct'] == 0.7
    assert payload['experiment_spec']['protocol']['seed'] == 7
    assert payload['execution_snapshot']['runtime_overrides']['x'] == 1
    assert payload['execution_snapshot']['basis_stage'] == 'pre_optimization'
    assert payload['run_context']['active_runtime_config_ref'] == 'cfg.yaml'
    assert payload['promotion_record']['gate_status'] == 'not_applicable'
    assert payload['lineage_record']['lineage_status'] == 'active_only'
    assert payload['review_decision']['causal_diagnosis']['primary_driver'] == 'benchmark_gap'
    assert payload['causal_diagnosis']['primary_driver'] == 'benchmark_gap'
    assert payload['similarity_summary']['matched_cycle_ids'] == [2]
    assert payload['similar_results'][0]['cycle_id'] == 2
    assert payload['realism_metrics']['avg_trade_amount'] == 1234.5
    assert payload['realism_metrics']['trade_record_count'] == 2


def test_commander_snapshot_is_jsonable(tmp_path):
    import json
    import numpy as np
    from invest_evolution.application.commander_main import InvestmentBodyService, CommanderConfig

    cfg = CommanderConfig(mock_mode=True, autopilot_enabled=False, heartbeat_enabled=False, bridge_enabled=False)
    cfg.training_output_dir = tmp_path / 'training'
    cfg.artifact_log_dir = tmp_path / 'artifacts'
    cfg.config_audit_log_path = tmp_path / 'audit' / 'changes.jsonl'
    cfg.config_snapshot_dir = tmp_path / 'snapshots'

    body = InvestmentBodyService(cfg)
    body.last_result = {'benchmark_passed': np.bool_(True)}
    json.dumps(body.snapshot(), ensure_ascii=False)


def test_save_cycle_result_persists_structured_trades(tmp_path):
    import json
    from invest_evolution.application.train import TrainingResult
    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )
    result = TrainingResult(
        cycle_id=2,
        cutoff_date='20240102',
        selected_stocks=['X'],
        initial_capital=100000,
        final_value=99000,
        return_pct=-1.0,
        is_profit=False,
        trade_history=[{
            'date': '20240102',
            'action': '买入',
            'ts_code': 'X',
            'price': 10.0,
            'shares': 1000,
            'reason': '趋势突破',
            'source': 'trend_hunter',
            'entry_reason': '趋势突破',
            'exit_reason': '',
            'exit_trigger': '',
            'entry_date': '20240102',
            'entry_price': 10.0,
            'holding_days': 0,
        }],
        params={},
        data_mode='mock',
        selection_mode='meeting',
        agent_used=True,
        llm_used=False,
        benchmark_passed=False,
        review_applied=False,
        config_snapshot_path='',
    )
    controller._save_cycle_result(result)
    payload = json.loads((tmp_path / 'training' / 'cycle_2.json').read_text(encoding='utf-8'))
    assert payload['trades'][0]['entry_reason'] == '趋势突破'
    assert payload['trades'][0]['source'] == 'trend_hunter'
    assert payload['artifacts']['trade_history_path'].endswith('cycle_2_trades.json')
    trade_payload = json.loads((tmp_path / 'training' / 'details' / 'cycle_2_trades.json').read_text(encoding='utf-8'))
    assert trade_payload['trades'][0]['entry_reason'] == '趋势突破'


def test_run_cycles_marks_insufficient_data_when_all_cycles_skip(tmp_path):
    cfg = CommanderConfig(mock_mode=True, autopilot_enabled=False, heartbeat_enabled=False, bridge_enabled=False)
    cfg.training_output_dir = tmp_path / 'training'
    cfg.artifact_log_dir = tmp_path / 'artifacts'
    cfg.config_audit_log_path = tmp_path / 'audit' / 'changes.jsonl'
    cfg.config_snapshot_dir = tmp_path / 'snapshots'
    cfg.training_lock_file = tmp_path / 'state' / 'training.lock'

    body = InvestmentBodyService(cfg)

    def _skip_cycle():
        body.controller.last_cycle_meta = {
            'status': 'no_data',
            'cycle_id': 1,
            'cutoff_date': '20240229',
            'stage': 'selection',
            'reason': '无可交易标的',
            'timestamp': '2026-03-08T01:00:00',
        }
        return None

    body.controller.run_training_cycle = MagicMock(side_effect=_skip_cycle)

    import asyncio
    out = asyncio.run(body.run_cycles(rounds=1, force_mock=False))

    assert out['status'] == 'insufficient_data'
    assert out['results'][0]['status'] == 'no_data'
    assert body.last_completed_task is not None
    assert body.last_completed_task['run_status'] == 'insufficient_data'


def test_run_cycles_marks_completed_with_skips_for_mixed_ok_and_skip(tmp_path):
    from invest_evolution.application.train import TrainingResult

    cfg = CommanderConfig(mock_mode=True, autopilot_enabled=False, heartbeat_enabled=False, bridge_enabled=False)
    cfg.training_output_dir = tmp_path / 'training'
    cfg.artifact_log_dir = tmp_path / 'artifacts'
    cfg.config_audit_log_path = tmp_path / 'audit' / 'changes.jsonl'
    cfg.config_snapshot_dir = tmp_path / 'snapshots'
    cfg.training_lock_file = tmp_path / 'state' / 'training.lock'

    body = InvestmentBodyService(cfg)
    ok_result = TrainingResult(
        cycle_id=1,
        cutoff_date='20240101',
        selected_stocks=['sh.600000'],
        initial_capital=100000,
        final_value=101000,
        return_pct=1.0,
        is_profit=True,
        trade_history=[],
        params={},
    )

    state = {'calls': 0}

    def _side_effect():
        calls = int(state['calls'])
        state['calls'] = calls + 1
        if calls == 0:
            return ok_result
        body.controller.last_cycle_meta = {
            'status': 'no_data',
            'cycle_id': 2,
            'cutoff_date': '20240229',
            'stage': 'simulation',
            'reason': '未来交易日不足',
            'timestamp': '2026-03-08T01:00:01',
        }
        return None

    body.controller.run_training_cycle = MagicMock(side_effect=_side_effect)

    import asyncio
    out = asyncio.run(body.run_cycles(rounds=2, force_mock=False))

    assert out['status'] == 'completed_with_skips'
    assert [item['status'] for item in out['results']] == ['ok', 'no_data']
    assert body.last_completed_task is not None
    assert body.last_completed_task['run_status'] == 'completed_with_skips'


def test_run_cycles_marks_partial_failure_for_mixed_ok_and_error(tmp_path):
    from invest_evolution.application.train import TrainingResult

    cfg = CommanderConfig(mock_mode=True, autopilot_enabled=False, heartbeat_enabled=False, bridge_enabled=False)
    cfg.training_output_dir = tmp_path / 'training'
    cfg.artifact_log_dir = tmp_path / 'artifacts'
    cfg.config_audit_log_path = tmp_path / 'audit' / 'changes.jsonl'
    cfg.config_snapshot_dir = tmp_path / 'snapshots'
    cfg.training_lock_file = tmp_path / 'state' / 'training.lock'

    body = InvestmentBodyService(cfg)
    ok_result = TrainingResult(
        cycle_id=1,
        cutoff_date='20240101',
        selected_stocks=['sh.600000'],
        initial_capital=100000,
        final_value=101000,
        return_pct=1.0,
        is_profit=True,
        trade_history=[],
        params={},
    )

    state = {'calls': 0}

    def _side_effect():
        calls = int(state['calls'])
        state['calls'] = calls + 1
        if calls == 0:
            return ok_result
        raise RuntimeError('boom')

    body.controller.run_training_cycle = MagicMock(side_effect=_side_effect)

    import asyncio
    out = asyncio.run(body.run_cycles(rounds=2, force_mock=False))

    assert out['status'] == 'partial_failure'
    assert out['results'][0]['status'] == 'ok'
    assert out['results'][1]['status'] == 'error'
    assert body.last_completed_task is not None
    assert body.last_completed_task['run_status'] == 'partial_failure'


def test_save_cycle_result_persists_strategy_scores(tmp_path):
    import json
    from invest_evolution.application.train import TrainingResult

    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )
    result = TrainingResult(
        cycle_id=3,
        cutoff_date='20240103',
        selected_stocks=['X'],
        initial_capital=100000,
        final_value=101500,
        return_pct=1.5,
        is_profit=True,
        trade_history=[],
        params={},
        strategy_scores={
            'signal_accuracy': 0.7,
            'timing_score': 0.6,
            'risk_control_score': 0.8,
            'overall_score': 0.71,
        },
    )
    controller._save_cycle_result(result)
    payload = json.loads((tmp_path / 'training' / 'cycle_3.json').read_text(encoding='utf-8'))
    assert payload['strategy_scores']['overall_score'] == 0.71


def test_run_continuous_report_counts_skipped_cycles(tmp_path):
    from invest_evolution.application.train import TrainingResult

    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )

    ok_result = TrainingResult(
        cycle_id=1,
        cutoff_date='20240101',
        selected_stocks=['sh.600000'],
        initial_capital=100000,
        final_value=101000,
        return_pct=1.0,
        is_profit=True,
        trade_history=[],
        params={},
    )

    state = {'calls': 0}

    def _side_effect():
        calls = int(state['calls'])
        state['calls'] = calls + 1
        if calls == 0:
            controller.cycle_history.append(ok_result)
            controller.current_cycle_id = 1
            return ok_result
        controller.last_cycle_meta = {
            'status': 'no_data',
            'cycle_id': 2,
            'cutoff_date': '20240229',
            'stage': 'selection',
            'reason': '无可交易标的',
            'timestamp': '2026-03-10T00:00:00',
        }
        return None

    controller.run_training_cycle = MagicMock(side_effect=_side_effect)

    report = controller.run_continuous(max_cycles=2)

    assert report['status'] == 'completed_with_skips'
    assert report['total_cycles'] == 2
    assert report['attempted_cycles'] == 2
    assert report['successful_cycles'] == 1
    assert report['skipped_cycles'] == 1
    assert report['profit_cycles'] == 1
    assert report['loss_cycles'] == 0


def test_run_continuous_preserves_last_non_empty_research_feedback_across_terminal_skip(tmp_path):
    from invest_evolution.application.train import TrainingResult

    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )

    ok_result = TrainingResult(
        cycle_id=1,
        cutoff_date='20240101',
        selected_stocks=['sh.600000'],
        initial_capital=100000,
        final_value=101000,
        return_pct=1.0,
        is_profit=True,
        trade_history=[],
        params={},
    )

    state = {'calls': 0}

    def _side_effect():
        calls = int(state['calls'])
        state['calls'] = calls + 1
        if calls == 0:
            controller.last_research_feedback = {
                'sample_count': 13,
                'recommendation': {'bias': 'tighten_risk'},
            }
            controller.cycle_history.append(ok_result)
            controller.current_cycle_id = 1
            return ok_result
        controller.last_research_feedback = {}
        controller.last_cycle_meta = {
            'status': 'no_data',
            'cycle_id': 2,
            'cutoff_date': '20240229',
            'stage': 'simulation',
            'reason': '交易日不足',
            'timestamp': '2026-03-24T00:00:00',
        }
        return None

    controller.run_training_cycle = MagicMock(side_effect=_side_effect)

    report = controller.run_continuous(max_cycles=2)

    assert report['status'] == 'completed_with_skips'
    assert report['research_feedback']['sample_count'] == 13
    assert report['freeze_gate_evaluation']['research_feedback_gate']['sample_count'] == 13
    assert controller.last_research_feedback['sample_count'] == 13


def test_run_continuous_report_no_data_counts_attempts(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )

    def _skip_cycle():
        controller.last_cycle_meta = {
            'status': 'no_data',
            'cycle_id': 1,
            'cutoff_date': '20240229',
            'stage': 'selection',
            'reason': '无可交易标的',
            'timestamp': '2026-03-10T00:00:00',
        }
        return None

    controller.run_training_cycle = MagicMock(side_effect=_skip_cycle)

    report = controller.run_continuous(max_cycles=2)

    assert report['status'] == 'no_data'
    assert report['total_cycles'] == 2
    assert report['attempted_cycles'] == 2
    assert report['successful_cycles'] == 0
    assert report['skipped_cycles'] == 2


def test_run_continuous_stops_after_target_successes(tmp_path):
    from invest_evolution.application.train import TrainingResult

    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )

    ok_results = [
        TrainingResult(
            cycle_id=1,
            cutoff_date='20240101',
            selected_stocks=['sh.600000'],
            initial_capital=100000,
            final_value=101000,
            return_pct=1.0,
            is_profit=True,
            trade_history=[],
            params={},
        ),
        TrainingResult(
            cycle_id=2,
            cutoff_date='20240102',
            selected_stocks=['sh.600001'],
            initial_capital=100000,
            final_value=99000,
            return_pct=-1.0,
            is_profit=False,
            trade_history=[],
            params={},
        ),
    ]

    state = {'calls': 0}

    def _side_effect():
        calls = int(state['calls'])
        state['calls'] = calls + 1
        if calls in {0, 2}:
            controller.last_cycle_meta = {
                'status': 'no_data',
                'cycle_id': calls + 1,
                'cutoff_date': '20240229',
                'stage': 'selection',
                'reason': '无可交易标的',
                'timestamp': '2026-03-10T00:00:00',
            }
            return None
        result = ok_results[(calls - 1) // 2]
        controller.cycle_history.append(result)
        controller.current_cycle_id = len(controller.cycle_history)
        return result

    controller.run_training_cycle = MagicMock(side_effect=_side_effect)

    report = controller.run_continuous(max_cycles=10, successful_cycles_target=2)

    assert state['calls'] == 4
    assert report['attempted_cycles'] == 4
    assert report['successful_cycles'] == 2
    assert report['skipped_cycles'] == 2
    assert report['successful_cycles_target'] == 2
    assert report['target_met'] is True


def test_run_continuous_reports_unmet_success_target(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )

    def _skip_cycle():
        controller.last_cycle_meta = {
            'status': 'no_data',
            'cycle_id': 1,
            'cutoff_date': '20240229',
            'stage': 'selection',
            'reason': '无可交易标的',
            'timestamp': '2026-03-10T00:00:00',
        }
        return None

    controller.run_training_cycle = MagicMock(side_effect=_skip_cycle)

    report = controller.run_continuous(max_cycles=3, successful_cycles_target=2)

    assert report['attempted_cycles'] == 3
    assert report['successful_cycles'] == 0
    assert report['successful_cycles_target'] == 2
    assert report['target_met'] is False


def test_runtime_config_mutation_generates_candidate_without_auto_apply_by_default(tmp_path, monkeypatch):
    from invest_evolution.application.train import SelfLearningController
    from invest_evolution.investment.evolution.mutation import RuntimeConfigMutator

    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )
    controller.runtime_config_mutator = RuntimeConfigMutator(generations_dir=tmp_path / 'generations')

    mutation = controller.runtime_config_mutator.mutate(
        controller.default_manager_config_ref,
        param_adjustments={'signal_threshold': 0.61},
        generation_label='test_candidate',
        parent_meta={'cycle_id': 1},
    )

    reloaded = {'called': False}

    def _fake_reload(path: str | None = None) -> None:
        reloaded['called'] = True

    monkeypatch.setattr(controller, '_reload_manager_runtime', _fake_reload)
    auto_applied = bool(controller.auto_apply_mutation)
    if auto_applied:
        cast(Any, controller)._reload_manager_runtime(mutation['runtime_config_ref'])

    assert controller.auto_apply_mutation is False
    assert Path(mutation['runtime_config_ref']).exists()
    assert reloaded['called'] is False



def test_generate_report_wrapper_preserves_fields(tmp_path):
    from invest_evolution.application.train import TrainingResult

    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )
    controller.total_cycle_attempts = 2
    controller.skipped_cycle_count = 1
    controller.last_research_feedback = {
        'sample_count': 4,
        'recommendation': {'bias': 'tighten_risk', 'summary': 'ask calibration says tighten risk'},
    }
    controller.cycle_history.append(TrainingResult(
        cycle_id=1,
        cutoff_date='20240101',
        selected_stocks=['x'],
        initial_capital=1,
        final_value=2,
        return_pct=1.0,
        is_profit=True,
        trade_history=[],
        params={},
        realism_metrics={
            'avg_trade_amount': 1000.0,
            'avg_turnover_rate': 0.2,
            'avg_holding_days': 3.0,
            'high_turnover_trade_count': 1,
        },
    ))

    report = controller._generate_report()
    assert report['status'] == 'completed_with_skips'
    assert report['successful_cycles'] == 1
    assert report['skipped_cycles'] == 1
    assert report['research_feedback']['recommendation']['bias'] == 'tighten_risk'
    assert report['realism_summary']['cycles_with_realism_metrics'] == 1
    assert report['realism_summary']['avg_trade_amount'] == 1000.0


def test_generate_report_ignores_non_finite_realism_metrics(tmp_path):
    from invest_evolution.application.train import TrainingResult

    controller = SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        artifact_log_dir=str(tmp_path / 'artifacts'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
    )
    controller.total_cycle_attempts = 2
    controller.cycle_history.extend([
        TrainingResult(
            cycle_id=1,
            cutoff_date='20240101',
            selected_stocks=['x'],
            initial_capital=1,
            final_value=2,
            return_pct=1.0,
            is_profit=True,
            trade_history=[],
            params={},
            realism_metrics={'avg_trade_amount': float('nan'), 'avg_turnover_rate': float('nan'), 'avg_holding_days': float('nan')},
        ),
        TrainingResult(
            cycle_id=2,
            cutoff_date='20240102',
            selected_stocks=['y'],
            initial_capital=1,
            final_value=2,
            return_pct=1.0,
            is_profit=True,
            trade_history=[],
            params={},
            realism_metrics={'avg_trade_amount': 200.0, 'avg_turnover_rate': 0.5, 'avg_holding_days': 4.0},
        ),
    ])

    report = controller._generate_report()

    assert report['realism_summary']['cycles_with_realism_metrics'] == 2
    assert report['realism_summary']['avg_trade_amount'] == 200.0
    assert report['realism_summary']['avg_turnover_rate'] == 0.5
    assert report['realism_summary']['avg_holding_days'] == 4.0


def test_commander_snapshot_exposes_research_feedback(tmp_path):
    from invest_evolution.application.commander_main import InvestmentBodyService, CommanderConfig

    cfg = CommanderConfig(mock_mode=True, autopilot_enabled=False, heartbeat_enabled=False, bridge_enabled=False)
    cfg.training_output_dir = tmp_path / 'training'
    cfg.artifact_log_dir = tmp_path / 'artifacts'
    cfg.config_audit_log_path = tmp_path / 'audit' / 'changes.jsonl'
    cfg.config_snapshot_dir = tmp_path / 'snapshots'

    body = InvestmentBodyService(cfg)
    body.controller.last_research_feedback = {
        'sample_count': 6,
        'recommendation': {'bias': 'recalibrate_probability'},
    }

    snapshot = body.snapshot()
    assert snapshot['research_feedback']['recommendation']['bias'] == 'recalibrate_probability'
    assert 'freeze_gate_evaluation' in snapshot
    assert 'research_feedback_optimization' in snapshot
