import json

import config as config_module
import web_server
from config.control_plane import clear_control_plane_cache


def test_evolution_config_hides_llm_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, 'PROJECT_ROOT', tmp_path)
    clear_control_plane_cache()

    client = web_server.app.test_client()
    res = client.get('/api/evolution_config')
    assert res.status_code == 200
    payload = res.get_json()
    assert payload['status'] == 'ok'
    assert 'llm_fast_model' not in payload['config']
    assert 'llm_deep_model' not in payload['config']
    assert 'llm_api_base' not in payload['config']
    assert 'llm_api_key_masked' not in payload['config']
    assert 'llm_api_key_source' not in payload['config']



def test_evolution_config_rejects_llm_patch(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, 'PROJECT_ROOT', tmp_path)
    clear_control_plane_cache()

    client = web_server.app.test_client()
    res = client.post(
        '/api/evolution_config',
        data=json.dumps(
            {
                'llm_fast_model': 'shell-fast-model',
                'max_stocks': 77,
            }
        ),
        content_type='application/json',
    )
    assert res.status_code == 400
    payload = res.get_json()
    assert payload['status'] == 'error'
    assert payload['migrate_to'] == '/api/control_plane'
    assert payload['invalid_keys'] == ['llm_fast_model']



def test_agent_prompts_endpoint_updates_prompt_without_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, 'PROJECT_ROOT', tmp_path)
    clear_control_plane_cache()
    original_configs = dict(config_module.agent_config_registry._configs)
    config_module.agent_config_registry._configs = {
        'hunter': {'llm_model': 'fast', 'system_prompt': 'old prompt'}
    }
    try:
        client = web_server.app.test_client()
        res = client.post(
            '/api/agent_prompts',
            data=json.dumps(
                {
                    'name': 'hunter',
                    'system_prompt': 'new prompt',
                }
            ),
            content_type='application/json',
        )
        assert res.status_code == 200
        payload = res.get_json()
        assert payload['status'] == 'ok'
        assert payload['restart_required'] is False

        listing = client.get('/api/agent_prompts').get_json()
        hunter = next(item for item in listing['configs'] if item['name'] == 'hunter')
        assert hunter['system_prompt'] == 'new prompt'
    finally:
        config_module.agent_config_registry._configs = original_configs



def test_agent_configs_legacy_route_removed(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, 'PROJECT_ROOT', tmp_path)
    clear_control_plane_cache()
    client = web_server.app.test_client()

    res = client.get('/api/agent_configs')

    assert res.status_code == 404



def test_control_plane_api_exposes_metadata_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, 'PROJECT_ROOT', tmp_path)
    clear_control_plane_cache()
    client = web_server.app.test_client()

    res = client.get('/api/control_plane')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['config_path'].endswith('config/control_plane.yaml')
    assert payload['local_override_path'].endswith('config/control_plane.local.yaml')
    assert payload['audit_log_path'].endswith('runtime/state/control_plane_changes.jsonl')
    assert payload['snapshot_dir'].endswith('runtime/state/control_plane_snapshots')
