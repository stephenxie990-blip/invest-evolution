import json
from pathlib import Path

import jsonschema
import web_server


CONTRACT_PATH = Path('docs/contracts/frontend-api-contract.v1.json')
CONTRACT_SCHEMA_PATH = Path('docs/contracts/frontend-api-contract.v1.schema.json')
CONTRACT_OPENAPI_PATH = Path('docs/contracts/frontend-api-contract.v1.openapi.json')


def test_contract_catalog_endpoint_available_without_runtime():
    client = web_server.app.test_client()

    res = client.get('/api/contracts')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['count'] >= 3
    ids = {item['id'] for item in payload['items']}
    assert {'frontend-v1', 'frontend-v1-schema', 'frontend-v1-openapi'} <= ids


def test_frontend_contract_endpoint_returns_machine_readable_contract():
    client = web_server.app.test_client()

    res = client.get('/api/contracts/frontend-v1')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['contract_id'] == 'frontend-v1'
    assert payload['frontend_shell_mount'] == '/app'
    assert payload['components']['schemas']['responseFeedback']['properties']['summary']['type'] == 'string'
    assert payload['components']['schemas']['responseNextAction']['properties']['kind']['type'] == 'string'
    assert payload['components']['schemas']['responseEnvelope']['properties']['feedback']['$ref'] == '#/components/schemas/responseFeedback'
    assert payload['components']['schemas']['statusWrappedConfig']['properties']['feedback']['$ref'] == '#/components/schemas/responseFeedback'
    assert payload['components']['schemas']['chatReply']['properties']['next_action']['$ref'] == '#/components/schemas/responseNextAction'
    assert any(endpoint['path'] == '/api/train' and endpoint['method'] == 'POST' for endpoint in payload['endpoints'])
    assert any(endpoint['path'] == '/api/model-routing/preview' and endpoint['method'] == 'GET' for endpoint in payload['endpoints'])
    assert '#/components/sse_schemas/routingDecision' in payload['sse']['event_refs']
    train_endpoint = next(endpoint for endpoint in payload['endpoints'] if endpoint['path'] == '/api/train' and endpoint['method'] == 'POST')
    assert train_endpoint['request_body']['properties']['mock']['default'] is False
    cycle_complete = payload['components']['sse_schemas']['cycleComplete']['data']['properties']
    assert 'requested_data_mode' in cycle_complete
    assert 'effective_data_mode' in cycle_complete
    assert 'llm_mode' in cycle_complete
    assert payload['sse']['path'] == '/api/events'


def test_frontend_contract_schema_endpoint_returns_json_schema():
    client = web_server.app.test_client()

    res = client.get('/api/contracts/frontend-v1/schema')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['$schema'].startswith('https://json-schema.org/')
    assert payload['title'] == 'Frontend API Contract V1'


def test_frontend_contract_openapi_endpoint_returns_openapi_document():
    client = web_server.app.test_client()

    res = client.get('/api/contracts/frontend-v1/openapi')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['openapi'] == '3.1.0'
    assert '/api/events' in payload['paths']
    assert '/api/lab/status/quick' in payload['paths']
    assert '/api/model-routing/preview' in payload['paths']


def test_generated_contract_derivatives_validate_against_main_contract():
    contract = json.loads(CONTRACT_PATH.read_text(encoding='utf-8'))
    contract_schema = json.loads(CONTRACT_SCHEMA_PATH.read_text(encoding='utf-8'))
    openapi = json.loads(CONTRACT_OPENAPI_PATH.read_text(encoding='utf-8'))

    jsonschema.validate(contract, contract_schema)

    assert openapi['info']['version'] == contract['version']
    assert openapi['paths']['/api/events']['get']['x-sse-event-refs'] == contract['sse']['event_refs']

    for endpoint in contract['endpoints']:
        path_item = openapi['paths'][endpoint['path']]
        assert endpoint['method'].lower() in path_item


def test_app_shell_returns_helpful_404_when_frontend_dist_missing(monkeypatch, tmp_path):
    missing_dist = tmp_path / 'frontend-dist-missing'
    monkeypatch.setattr(web_server, '_FRONTEND_DIST_DIR', missing_dist)

    client = web_server.app.test_client()
    res = client.get('/app')

    assert res.status_code == 404
    payload = res.get_json()
    assert 'frontend dist is not available' in payload['error']
    assert payload['expected_path'].endswith('frontend-dist-missing')
