import json
from types import SimpleNamespace

import pytest

import config as config_module
import web_server
from commander import CommanderConfig, CommanderRuntime


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
    assert res.get_json()['status'] == 'ok'


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
