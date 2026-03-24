import asyncio
import json

import invest_evolution.interfaces.web.server as web_server
from invest_evolution.application.commander_main import CommanderConfig, CommanderRuntime


def _make_runtime(tmp_path):
    cfg = CommanderConfig(
        workspace=tmp_path / 'workspace',
        playbook_dir=tmp_path / 'strategies',
        state_file=tmp_path / 'state.json',
        cron_store=tmp_path / 'cron.json',
        memory_store=tmp_path / 'memory.jsonl',
        plugin_dir=tmp_path / 'plugins',
        bridge_inbox=tmp_path / 'inbox',
        bridge_outbox=tmp_path / 'outbox',
        training_output_dir=tmp_path / 'training',
        artifact_log_dir=tmp_path / 'artifacts',
        config_audit_log_path=tmp_path / 'runtime' / 'state' / 'config_changes.jsonl',
        config_snapshot_dir=tmp_path / 'runtime' / 'state' / 'config_snapshots',
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    return CommanderRuntime(cfg)


def _install_runtime(monkeypatch, runtime):
    web_server.reset_ephemeral_web_state()
    monkeypatch.setattr(web_server, '_runtime', runtime)
    monkeypatch.setattr(web_server, '_loop', object())
    monkeypatch.setattr(web_server, '_run_async', lambda coro: asyncio.run(coro))


def test_status_endpoint_exposes_fast_and_slow_modes_via_detail_query(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)
    monkeypatch.setattr(
        runtime,
        'status',
        lambda detail='fast': {
            'detail_mode': 'slow' if detail == 'slow' else 'fast',
            'runtime': {'state': 'idle'},
            'training_lab': {'plan_count': 0, 'run_count': 0, 'evaluation_count': 0},
        },
    )
    client = web_server.app.test_client()

    quick = client.get('/api/status?detail=fast')
    assert quick.status_code == 200
    quick_data = quick.get_json()
    assert quick_data['detail_mode'] == 'fast'

    deep = client.get('/api/status?detail=slow')
    assert deep.status_code == 200
    deep_data = deep.get_json()
    assert deep_data['detail_mode'] == 'slow'

    compat = client.get('/api/status')
    assert compat.status_code == 200
    assert compat.get_json()['detail_mode'] == 'fast'



def test_training_lab_plan_run_eval_api(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)
    expected_cycle_result_path = str(tmp_path / 'training' / 'cycle_1.json')

    async def fake_run_cycles(rounds=1, force_mock=False, task_source='direct', experiment_spec=None):
        del experiment_spec
        cycle_path = tmp_path / 'training' / 'cycle_1.json'
        cycle_path.parent.mkdir(parents=True, exist_ok=True)
        cycle_path.write_text(json.dumps({'cycle_id': 1, 'return_pct': 1.5}, ensure_ascii=False), encoding='utf-8')
        return {
            'status': 'ok',
            'rounds': rounds,
            'results': [
                {
                    'status': 'ok',
                    'cycle_id': 1,
                    'return_pct': 1.5,
                    'trade_count': 2,
                    'selected_count': 1,
                    'selected_stocks': ['000001.SZ'],
                    'benchmark_passed': True,
                    'realism_metrics': {
                        'avg_holding_days': 4.0,
                        'high_turnover_trade_count': 1,
                    },
                    'promotion_record': {
                        'status': 'candidate_generated',
                        'gate_status': 'awaiting_gate',
                        'active_runtime_config_ref': 'configs/active.yaml',
                        'candidate_runtime_config_ref': 'configs/candidate.yaml',
                        'candidate_runtime_config_meta_ref': 'configs/candidate.json',
                    },
                    'lineage_record': {
                        'lineage_status': 'candidate_pending',
                        'active_runtime_config_ref': 'configs/active.yaml',
                        'candidate_runtime_config_ref': 'configs/candidate.yaml',
                        'candidate_runtime_config_meta_ref': 'configs/candidate.json',
                        'fitness_source_cycles': [1],
                        'review_basis_window': {
                            'mode': 'rolling',
                            'size': 3,
                            'cycle_ids': [1],
                        },
                    },
                    'similarity_summary': {
                        'matched_cycle_ids': [8, 6],
                        'dominant_regime': 'bear',
                        'match_features': ['regime', 'selection_mode'],
                    },
                    'causal_diagnosis': {
                        'primary_driver': 'regime_repeat_loss',
                        'summary': '同一市场状态下重复亏损，建议先围绕风险阈值收敛参数。',
                        'drivers': [
                            {
                                'code': 'regime_repeat_loss',
                                'label': '同一市场状态下重复亏损',
                                'score': 0.55,
                                'evidence_cycle_ids': [8, 6],
                            }
                        ],
                    },
                    'similar_results': [
                        {
                            'cycle_id': 8,
                            'regime': 'bear',
                            'return_pct': -1.4,
                            'matched_features': ['regime', 'selection_mode'],
                        }
                    ],
                    'artifacts': {'cycle_result_path': expected_cycle_result_path},
                }
            ],
            'summary': {'total_cycles': 1, 'success_cycles': 1},
        }

    monkeypatch.setattr(runtime.body, 'run_cycles', fake_run_cycles)
    client = web_server.app.test_client()

    created = client.post(
        '/api/lab/training/plans',
        data=json.dumps({
            'rounds': 2,
            'mock': True,
            'goal': 'compare allocator outcome',
            'notes': 'api smoke',
            'tags': ['lab', 'smoke'],
            'detail_mode': 'slow',
            'protocol': {'seed': 7, 'date_range': {'min': '20240101', 'max': '20241231'}},
            'dataset': {'min_history_days': 160, 'simulation_days': 15},
            'manager_scope': {'allowed_manager_ids': ['momentum'], 'allocator_enabled': False},
        }),
        content_type='application/json',
    )
    assert created.status_code == 201
    plan = created.get_json()
    plan_id = plan['plan_id']
    assert plan['source'] == 'api'
    assert plan['guardrails']['promotion_gate']['research_feedback']['enabled'] is True
    assert '默认启用 research_feedback 校准门' in plan['guardrails']['promotion_gate']['research_feedback']['summary']
    assert plan['spec']['detail_mode'] == 'slow'
    assert plan['protocol']['seed'] == 7
    assert plan['dataset']['simulation_days'] == 15
    assert plan['manager_scope']['allowed_manager_ids'] == ['momentum']
    assert len(list(runtime.cfg.training_plan_dir.glob('*.json'))) == 1

    listed = client.get('/api/lab/training/plans?limit=5')
    assert listed.status_code == 200
    listed_data = listed.get_json()
    assert listed_data['count'] == 1
    assert listed_data['items'][0]['plan_id'] == plan_id

    fetched = client.get(f'/api/lab/training/plans/{plan_id}')
    assert fetched.status_code == 200
    assert fetched.get_json()['plan_id'] == plan_id

    missing = client.get('/api/lab/training/plans/missing-plan')
    assert missing.status_code == 404
    assert 'not found' in missing.get_json()['error']

    executed = client.post(f'/api/lab/training/plans/{plan_id}/execute')
    assert executed.status_code == 200
    executed_data = executed.get_json()
    assert executed_data['training_lab']['plan']['plan_id'] == plan_id
    assert executed_data['training_lab']['plan']['guardrails']['promotion_gate']['research_feedback']['enabled'] is True
    assert executed_data['training_lab']['run']['latest_result']['promotion_record']['gate_status'] == 'awaiting_gate'
    assert executed_data['training_lab']['run']['latest_result']['lineage_record']['lineage_status'] == 'candidate_pending'
    assert executed_data['training_lab']['run']['latest_result']['core_artifacts']['cycle_result_path'] == expected_cycle_result_path
    assert executed_data['training_lab']['run']['ops_panel']['status']['lineage_status'] == 'candidate_pending'
    assert executed_data['training_lab']['run']['ops_panel']['refs']['candidate_runtime_config_ref'] == 'configs/candidate.yaml'
    assert executed_data['training_lab']['run']['ops_panel']['review_window']['mode'] == 'rolling'
    assert executed_data['training_lab']['run']['ops_panel']['fitness_source_cycles'] == [1]
    assert executed_data['training_lab']['run']['ops_panel']['ops_flags']['active_candidate_drift'] is True
    assert '候选配置仍待发布门确认' in executed_data['training_lab']['run']['ops_panel']['warnings']
    assert executed_data['training_lab']['evaluation']['promotion']['research_feedback']['passed'] is False
    assert 'research_feedback.available' in executed_data['training_lab']['evaluation']['promotion']['research_feedback']['reason_codes']
    assert '缺少可用研究反馈样本' in executed_data['training_lab']['evaluation']['promotion']['research_feedback']['summary']
    run_id = executed_data['training_lab']['run']['run_id']

    assert len(list(runtime.cfg.training_run_dir.glob('*.json'))) == 1
    assert len(list(runtime.cfg.training_eval_dir.glob('*.json'))) == 1

    runs = client.get('/api/lab/training/runs')
    assert runs.status_code == 200
    runs_data = runs.get_json()
    assert runs_data['count'] == 1
    assert runs_data['items'][0]['run_id'] == run_id

    run_detail = client.get(f'/api/lab/training/runs/{run_id}')
    assert run_detail.status_code == 200
    run_detail_payload = run_detail.get_json()
    assert run_detail_payload['plan_id'] == plan_id
    assert any(card['id'] == 'training_ops_panel' for card in run_detail_payload['display']['cards'])
    assert any(card['id'] == 'causal_diagnosis' for card in run_detail_payload['display']['cards'])
    assert any(card['id'] == 'similar_samples' for card in run_detail_payload['display']['cards'])

    evaluations = client.get('/api/lab/training/evaluations')
    assert evaluations.status_code == 200
    evaluations_data = evaluations.get_json()
    assert evaluations_data['count'] == 1
    assert evaluations_data['items'][0]['run_id'] == run_id

    evaluation_detail = client.get(f'/api/lab/training/evaluations/{run_id}')
    assert evaluation_detail.status_code == 200
    evaluation_data = evaluation_detail.get_json()
    assert evaluation_data['plan_id'] == plan_id
    assert evaluation_data['assessment']['success_count'] == 1
    assert evaluation_data['governance_metrics']['candidate_pending_count'] == 1
    assert evaluation_data['realism_summary']['avg_holding_days'] == 4.0

    run_human = client.get(f'/api/lab/training/runs/{run_id}?view=human')
    assert run_human.status_code == 200
    run_human_text = run_human.get_data(as_text=True)
    assert '晋升状态：candidate_generated / awaiting_gate' in run_human_text
    assert 'lineage：candidate_pending' in run_human_text
    assert '候选配置：configs/candidate.yaml' in run_human_text
    assert 'review 窗口：rolling / 3' in run_human_text
    assert '因果诊断：regime_repeat_loss' in run_human_text

    status_payload = client.get('/api/status?detail=slow')
    assert status_payload.status_code == 200
    status_data = status_payload.get_json()
    assert status_data['training_lab']['governance_summary']['governance_metrics']['candidate_pending_count'] == 1
    assert status_data['training_lab']['latest_run_summary']['latest_result']['cycle_id'] == 1
    assert status_data['brain']['governance_metrics']['guardrails']['block_count'] >= 0
    assert any(card['id'] == 'training_governance' for card in status_data['display']['cards'])
    assert any(card['id'] == 'runtime_governance' for card in status_data['display']['cards'])



def test_api_train_route_is_removed_from_public_api_surface(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)
    client = web_server.app.test_client()

    res = client.post(
        '/api/train',
        data=json.dumps({'rounds': 1, 'mock': True}),
        content_type='application/json',
    )

    assert res.status_code == 404


def test_api_training_plan_create_rejects_non_integer_rounds(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)
    client = web_server.app.test_client()

    res = client.post(
        '/api/lab/training/plans',
        data=json.dumps({'rounds': 'bad', 'mock': True}),
        content_type='application/json',
    )

    assert res.status_code == 400
    assert res.get_json()['error'] == 'rounds must be an integer'
