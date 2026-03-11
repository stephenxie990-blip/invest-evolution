import json

import config as config_module
import web_server
from config.control_plane import clear_control_plane_cache


def test_control_plane_api_persists_and_requires_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, 'PROJECT_ROOT', tmp_path)
    clear_control_plane_cache()

    client = web_server.app.test_client()
    res = client.post(
        '/api/control_plane',
        data=json.dumps(
            {
                'llm': {
                    'providers': {
                        'provider_api': {
                            'api_base': 'https://api.example/v1',
                            'api_key': 'api-secret-key',
                        }
                    },
                    'models': {
                        'api_model': {
                            'provider': 'provider_api',
                            'model': 'api-model',
                        }
                    },
                    'bindings': {
                        'controller.main': 'api_model',
                    },
                }
            }
        ),
        content_type='application/json',
    )

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['status'] == 'ok'
    assert payload['restart_required'] is True
    assert 'llm.bindings.controller.main' in payload['updated']

    res2 = client.get('/api/control_plane')
    assert res2.status_code == 200
    body = res2.get_json()
    assert body['config']['llm']['models']['api_model']['model'] == 'api-model'
