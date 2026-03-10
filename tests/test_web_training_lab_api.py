import asyncio
import json

import web_server
from commander import CommanderConfig, CommanderRuntime
from market_data import DataSourceUnavailableError


def _make_runtime(tmp_path):
    cfg = CommanderConfig(
        workspace=tmp_path / 'workspace',
        strategy_dir=tmp_path / 'strategies',
        state_file=tmp_path / 'state.json',
        cron_store=tmp_path / 'cron.json',
        memory_store=tmp_path / 'memory.jsonl',
        plugin_dir=tmp_path / 'plugins',
        bridge_inbox=tmp_path / 'inbox',
        bridge_outbox=tmp_path / 'outbox',
        training_output_dir=tmp_path / 'training',
        meeting_log_dir=tmp_path / 'meetings',
        config_audit_log_path=tmp_path / 'runtime' / 'state' / 'config_changes.jsonl',
        config_snapshot_dir=tmp_path / 'runtime' / 'state' / 'config_snapshots',
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    return CommanderRuntime(cfg)


def _install_runtime(monkeypatch, runtime):
    monkeypatch.setattr(web_server, '_runtime', runtime)
    monkeypatch.setattr(web_server, '_loop', object())
    monkeypatch.setattr(web_server, '_run_async', lambda coro: asyncio.run(coro))


def test_lab_status_endpoints_expose_quick_and_deep_modes(tmp_path, monkeypatch):
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

    quick = client.get('/api/lab/status/quick')
    assert quick.status_code == 200
    quick_data = quick.get_json()
    assert quick_data['mode'] == 'quick'
    assert quick_data['snapshot']['detail_mode'] == 'fast'

    deep = client.get('/api/lab/status/deep')
    assert deep.status_code == 200
    deep_data = deep.get_json()
    assert deep_data['mode'] == 'deep'
    assert deep_data['snapshot']['detail_mode'] == 'slow'

    compat = client.get('/api/status?detail=slow')
    assert compat.status_code == 200
    assert compat.get_json()['detail_mode'] == 'slow'



def test_training_lab_plan_run_eval_api(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)

    async def fake_run_cycles(rounds=1, force_mock=False, task_source='direct'):
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
                    'artifacts': {'cycle_result_path': str(cycle_path)},
                }
            ],
            'summary': {'total_cycles': 1, 'success_cycles': 1},
        }

    runtime.body.run_cycles = fake_run_cycles
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
            'model_scope': {'allowed_models': ['momentum'], 'allocator_enabled': False},
        }),
        content_type='application/json',
    )
    assert created.status_code == 201
    plan = created.get_json()
    plan_id = plan['plan_id']
    assert plan['source'] == 'api'
    assert plan['spec']['detail_mode'] == 'slow'
    assert plan['protocol']['seed'] == 7
    assert plan['dataset']['simulation_days'] == 15
    assert plan['model_scope']['allowed_models'] == ['momentum']
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
    assert run_detail.get_json()['plan_id'] == plan_id

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



def test_api_train_still_returns_training_lab_bundle(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)

    async def fake_run_cycles(rounds=1, force_mock=False, task_source='direct'):
        return {
            'status': 'ok',
            'rounds': rounds,
            'results': [
                {
                    'status': 'ok',
                    'cycle_id': 7,
                    'return_pct': 0.8,
                    'trade_count': 1,
                    'selected_count': 1,
                    'selected_stocks': ['600000.SH'],
                    'benchmark_passed': False,
                }
            ],
            'summary': {'total_cycles': 1, 'success_cycles': 1},
        }

    runtime.body.run_cycles = fake_run_cycles
    client = web_server.app.test_client()

    res = client.post(
        '/api/train',
        data=json.dumps({'rounds': 1, 'mock': True}),
        content_type='application/json',
    )
    assert res.status_code == 200
    payload = res.get_json()
    assert payload['status'] == 'ok'
    assert 'training_lab' in payload
    assert payload['training_lab']['plan']['plan_id'].startswith('plan_')
    assert len(list(runtime.cfg.training_plan_dir.glob('*.json'))) == 1
    assert len(list(runtime.cfg.training_run_dir.glob('*.json'))) == 1
    assert len(list(runtime.cfg.training_eval_dir.glob('*.json'))) == 1



def test_api_train_defaults_to_live_mode_when_mock_omitted(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)

    observed = {}

    async def fake_train_once(rounds=1, mock=False):
        observed['rounds'] = rounds
        observed['mock'] = mock
        return {'status': 'ok', 'results': [], 'summary': {}, 'training_lab': {}}

    runtime.train_once = fake_train_once
    client = web_server.app.test_client()

    res = client.post(
        '/api/train',
        data=json.dumps({'rounds': 2}),
        content_type='application/json',
    )

    assert res.status_code == 200
    assert observed == {'rounds': 2, 'mock': False}



def test_api_train_returns_structured_503_for_data_source_unavailable(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)

    async def fake_train_once(rounds=1, mock=False):
        raise DataSourceUnavailableError(
            '训练数据源不可用：离线库与在线兜底均未能返回可训练数据，且当前未显式启用 mock 模式。',
            cutoff_date='20260310',
            stock_count=50,
            min_history_days=200,
            requested_data_mode='live',
            available_sources={'offline': True, 'online': True, 'mock': False},
            offline_diagnostics={'ready': False, 'issues': ['daily_bar 为空'], 'suggestions': ['先下载历史日线']},
            online_error='network down',
            suggestions=['先下载历史日线'],
            allow_mock_fallback=False,
        )

    runtime.train_once = fake_train_once
    client = web_server.app.test_client()

    res = client.post(
        '/api/train',
        data=json.dumps({'rounds': 1}),
        content_type='application/json',
    )

    assert res.status_code == 503
    payload = res.get_json()
    assert payload['error_code'] == 'data_source_unavailable'
    assert payload['requested_data_mode'] == 'live'
    assert payload['allow_mock_fallback'] is False
