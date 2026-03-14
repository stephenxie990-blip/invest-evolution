import asyncio
import json
from types import SimpleNamespace

import pytest

import config as config_module
import web_server
from commander import CommanderConfig, CommanderRuntime


@pytest.fixture(autouse=True)
def _reset_web_server_state():
    original_runtime = web_server._runtime
    original_loop = web_server._loop
    original_rate_limit_events = web_server._rate_limit_events
    original_shutdown_registered = web_server._runtime_shutdown_registered
    try:
        web_server._runtime = None
        web_server._loop = None
        web_server._rate_limit_events = {}
        web_server._runtime_shutdown_registered = False
        yield
    finally:
        web_server._runtime = original_runtime
        web_server._loop = original_loop
        web_server._rate_limit_events = original_rate_limit_events
        web_server._runtime_shutdown_registered = original_shutdown_registered


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


def test_healthz_is_public():
    client = web_server.app.test_client()
    res = client.get('/healthz')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['status'] == 'ok'
    assert payload['service'] == 'invest-web'
    runtime = payload['runtime']
    assert runtime['initialized'] is False
    assert runtime['loop_running'] is False
    assert isinstance(runtime['event_buffer_size'], int)
    assert runtime['event_buffer_size'] >= 0
    assert isinstance(runtime['event_history_size'], int)
    assert runtime['event_history_size'] >= 0
    assert isinstance(runtime['event_dispatcher_started'], bool)


def test_root_returns_api_entrypoint_summary():
    client = web_server.app.test_client()
    res = client.get('/')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['service'] == 'invest-api'
    assert payload['entrypoints']['chat'] == '/api/chat'


def test_removed_web_ui_routes_return_tombstone():
    client = web_server.app.test_client()

    legacy = client.get('/legacy')
    app_shell = client.get('/app')

    assert legacy.status_code == 410
    assert app_shell.status_code == 410
    assert legacy.get_json()['error'] == 'web ui has been removed'
    assert app_shell.get_json()['removed_path'] == '/app'


def test_api_status_requires_auth_when_enabled(monkeypatch):
    runtime = SimpleNamespace(status=lambda detail='fast': {'detail': detail, 'status': 'ok'})
    monkeypatch.setattr(web_server, '_runtime', runtime)
    monkeypatch.setattr(config_module.config, 'web_api_require_auth', True)
    monkeypatch.setattr(config_module.config, 'web_api_token', 'secret-token')
    monkeypatch.setattr(config_module.config, 'web_api_public_read_enabled', False)

    client = web_server.app.test_client()

    missing = client.get('/api/status')
    assert missing.status_code == 401

    wrong = client.get('/api/status', headers={'Authorization': 'Bearer wrong-token'})
    assert wrong.status_code == 403

    ok = client.get('/api/status', headers={'Authorization': 'Bearer secret-token'})
    assert ok.status_code == 200
    assert ok.get_json()['detail'] == 'fast'


@pytest.mark.parametrize('header_name', ['Authorization', 'X-Invest-Token'])
def test_public_status_can_remain_accessible_when_configured(monkeypatch, header_name):
    runtime = SimpleNamespace(status=lambda detail='fast': {'detail': detail, 'status': 'ok'})
    monkeypatch.setattr(web_server, '_runtime', runtime)
    monkeypatch.setattr(config_module.config, 'web_api_require_auth', True)
    monkeypatch.setattr(config_module.config, 'web_api_token', 'secret-token')
    monkeypatch.setattr(config_module.config, 'web_api_public_read_enabled', True)

    client = web_server.app.test_client()

    res = client.get('/api/status')
    assert res.status_code == 200

    authed = client.get(
        '/api/status',
        headers={header_name: 'Bearer secret-token' if header_name == 'Authorization' else 'secret-token'},
    )
    assert authed.status_code == 200


def test_mutating_endpoint_still_requires_auth_when_public_reads_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(config_module.config, 'web_api_require_auth', True)
    monkeypatch.setattr(config_module.config, 'web_api_token', 'secret-token')
    monkeypatch.setattr(config_module.config, 'web_api_public_read_enabled', True)

    client = web_server.app.test_client()
    res = client.post(
        '/api/evolution_config',
        data=json.dumps({'enable_debate': False}),
        content_type='application/json',
    )

    assert res.status_code == 401


def test_bootstrap_runtime_services_starts_runtime_once(monkeypatch):
    callbacks = []
    thread_names = []
    registered = []
    started = []

    class FakeRuntime:
        def __init__(self, cfg):
            self.cfg = cfg

        async def start(self):
            started.append(
                {
                    'mock_mode': self.cfg.mock_mode,
                    'autopilot_enabled': self.cfg.autopilot_enabled,
                    'heartbeat_enabled': self.cfg.heartbeat_enabled,
                    'bridge_enabled': self.cfg.bridge_enabled,
                }
            )

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.name = name

        def start(self):
            thread_names.append(self.name)

    monkeypatch.setattr(
        web_server.CommanderConfig,
        'from_args',
        lambda args: SimpleNamespace(
            mock_mode=False,
            autopilot_enabled=True,
            heartbeat_enabled=True,
            bridge_enabled=True,
        ),
    )
    monkeypatch.setattr(web_server, 'CommanderRuntime', FakeRuntime)
    monkeypatch.setattr(web_server, 'set_event_callback', callbacks.append)
    monkeypatch.setattr(web_server.asyncio, 'new_event_loop', lambda: object())
    monkeypatch.setattr(web_server.threading, 'Thread', FakeThread)
    monkeypatch.setattr(web_server, '_run_async', lambda coro: asyncio.run(coro))
    monkeypatch.setattr(web_server, '_register_runtime_shutdown', lambda: registered.append(True))

    runtime = web_server.bootstrap_runtime_services(host='127.0.0.1', mock=True, source='cli')
    runtime_again = web_server.bootstrap_runtime_services(host='127.0.0.1', mock=True, source='cli')

    assert runtime is runtime_again
    assert started == [{
        'mock_mode': True,
        'autopilot_enabled': False,
        'heartbeat_enabled': False,
        'bridge_enabled': False,
    }]
    assert len(callbacks) == 1
    assert thread_names == ['web-event-loop:cli']
    assert registered == [True]


def test_bootstrap_runtime_services_requires_auth_for_non_loopback(monkeypatch):
    monkeypatch.setattr(config_module.config, 'web_api_require_auth', False)
    monkeypatch.setattr(config_module.config, 'web_api_token', '')

    with pytest.raises(RuntimeError, match='WEB_API_REQUIRE_AUTH=true'):
        web_server.bootstrap_runtime_services(host='0.0.0.0', source='cli')


def test_bootstrap_runtime_services_rejects_multi_worker_wsgi(monkeypatch):
    monkeypatch.setenv('GUNICORN_WORKERS', '2')

    with pytest.raises(RuntimeError, match='single gunicorn worker'):
        web_server.bootstrap_runtime_services(host='127.0.0.1', source='wsgi')


def test_memory_detail_blocks_artifacts_outside_runtime_roots(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    monkeypatch.setattr(web_server, '_runtime', runtime)
    monkeypatch.setattr(config_module.config, 'web_api_require_auth', False)

    leaked = tmp_path / 'secrets.json'
    leaked.write_text(json.dumps({'secret': 'should-not-leak'}), encoding='utf-8')

    rec = runtime.memory.append(
        kind='training_run',
        session_key='runtime:train',
        content='训练记录',
        metadata={
            'training_run': True,
            'summary': {'status': 'ok'},
            'results': [
                {
                    'cycle_id': 1,
                    'config_snapshot_path': str(leaked),
                    'artifacts': {
                        'cycle_result_path': str(leaked),
                        'selection_meeting_markdown_path': str(leaked),
                    },
                }
            ],
        },
    )

    client = web_server.app.test_client()
    res = client.get(f'/api/memory/{rec.id}')

    assert res.status_code == 200
    payload = res.get_json()
    result = payload['details']['results'][0]
    assert result['cycle_result'] is None
    assert result['config_snapshot'] is None
    assert result['selection_meeting_markdown'] == ''


def test_read_rate_limit_returns_429(monkeypatch):
    runtime = SimpleNamespace(status=lambda detail='fast': {'detail': detail, 'status': 'ok'})
    monkeypatch.setattr(web_server, '_runtime', runtime)
    monkeypatch.setattr(web_server, '_rate_limit_events', {})
    monkeypatch.setattr(config_module.config, 'web_api_require_auth', False)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_enabled', True)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_window_sec', 60)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_read_max', 2)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_write_max', 20)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_heavy_max', 5)

    client = web_server.app.test_client()

    assert client.get('/api/status').status_code == 200
    assert client.get('/api/status').status_code == 200
    limited = client.get('/api/status')
    assert limited.status_code == 429
    assert limited.get_json()['error'] == 'rate limit exceeded'


def test_read_rate_limit_ignores_spoofed_x_forwarded_for(monkeypatch):
    runtime = SimpleNamespace(status=lambda detail='fast': {'detail': detail, 'status': 'ok'})
    monkeypatch.setattr(web_server, '_runtime', runtime)
    monkeypatch.setattr(web_server, '_rate_limit_events', {})
    monkeypatch.setattr(config_module.config, 'web_api_require_auth', False)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_enabled', True)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_window_sec', 60)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_read_max', 1)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_write_max', 20)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_heavy_max', 5)

    client = web_server.app.test_client()

    first = client.get('/api/status', headers={'X-Forwarded-For': '198.51.100.10'})
    second = client.get('/api/status', headers={'X-Forwarded-For': '203.0.113.20'})

    assert first.status_code == 200
    assert second.status_code == 429


def test_read_rate_limit_uses_x_real_ip_from_loopback_proxy(monkeypatch):
    runtime = SimpleNamespace(status=lambda detail='fast': {'detail': detail, 'status': 'ok'})
    monkeypatch.setattr(web_server, '_runtime', runtime)
    monkeypatch.setattr(web_server, '_rate_limit_events', {})
    monkeypatch.setattr(config_module.config, 'web_api_require_auth', False)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_enabled', True)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_window_sec', 60)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_read_max', 1)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_write_max', 20)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_heavy_max', 5)

    client = web_server.app.test_client()

    first = client.get('/api/status', headers={'X-Real-IP': '198.51.100.10'})
    second = client.get('/api/status', headers={'X-Real-IP': '203.0.113.20'})

    assert first.status_code == 200
    assert second.status_code == 200


def test_heavy_rate_limit_returns_429_for_train(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    monkeypatch.setattr(web_server, '_runtime', runtime)
    monkeypatch.setattr(web_server, '_loop', object())
    monkeypatch.setattr(web_server, '_rate_limit_events', {})
    monkeypatch.setattr(config_module.config, 'web_api_require_auth', False)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_enabled', True)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_window_sec', 60)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_read_max', 20)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_write_max', 10)
    monkeypatch.setattr(config_module.config, 'web_rate_limit_heavy_max', 1)

    async def fake_train_once(rounds=1, mock=False):
        return {'status': 'ok', 'results': [], 'summary': {}, 'training_lab': {}}

    monkeypatch.setattr(runtime, 'train_once', fake_train_once)
    monkeypatch.setattr(web_server, '_run_async', lambda coro: asyncio.run(coro))

    client = web_server.app.test_client()

    first = client.post('/api/train', data=json.dumps({'rounds': 1, 'mock': True}), content_type='application/json')
    assert first.status_code == 200

    second = client.post('/api/train', data=json.dumps({'rounds': 1, 'mock': True}), content_type='application/json')
    assert second.status_code == 429
    assert second.headers['Retry-After']


def test_api_data_status_rejects_invalid_refresh_query(monkeypatch):
    monkeypatch.setattr(config_module.config, 'web_api_require_auth', False)

    client = web_server.app.test_client()
    res = client.get('/api/data/status?refresh=bad')

    assert res.status_code == 400
    assert 'refresh must be a boolean' in res.get_json()['error']


def test_api_allocator_rejects_invalid_top_n_query(monkeypatch):
    called = {'value': False}

    def _allocator_preview(**kwargs):
        called['value'] = True
        return {'status': 'ok'}

    runtime = SimpleNamespace(get_allocator_preview=_allocator_preview)
    monkeypatch.setattr(web_server, '_runtime', runtime)
    monkeypatch.setattr(config_module.config, 'web_api_require_auth', False)

    client = web_server.app.test_client()
    res = client.get('/api/allocator?top_n=bad')

    assert res.status_code == 400
    assert 'top_n must be an integer' in res.get_json()['error']
    assert called['value'] is False
