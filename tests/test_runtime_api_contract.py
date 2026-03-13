import json
from pathlib import Path

import jsonschema
import web_server
from app.runtime_contract_catalog import RUNTIME_CONTRACT_DOCUMENTS


CONTRACT_PATH = Path('docs/contracts/runtime-api-contract.v1.json')
CONTRACT_SCHEMA_PATH = Path('docs/contracts/runtime-api-contract.v1.schema.json')
CONTRACT_OPENAPI_PATH = Path('docs/contracts/runtime-api-contract.v1.openapi.json')


def test_contract_catalog_endpoint_available_without_runtime():
    client = web_server.app.test_client()

    res = client.get('/api/contracts')

    assert res.status_code == 200
    payload = res.get_json()
    expected = {
        item.id: {
            'format': item.format,
            'kind': item.kind,
            'path': item.path,
            'source_path': str(item.source_path),
            'shell_mount': item.shell_mount,
        }
        for item in RUNTIME_CONTRACT_DOCUMENTS
        if item.source_path.exists()
    }
    assert payload['count'] == len(expected)
    actual = {item['id']: {k: v for k, v in item.items() if k != 'id'} for item in payload['items']}
    assert actual == expected


def test_runtime_contract_endpoint_returns_machine_readable_contract():
    client = web_server.app.test_client()

    res = client.get('/api/contracts/runtime-v1')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['contract_id'] == 'runtime-v1'
    assert payload['runtime_entrypoint'] == '/api/chat'
    assert payload['removed_web_shell_mount'] == '/'
    assert payload['components']['schemas']['responseFeedback']['properties']['summary']['type'] == 'string'
    assert payload['components']['schemas']['responseNextAction']['properties']['kind']['type'] == 'string'
    assert payload['components']['schemas']['responseEnvelope']['properties']['feedback']['$ref'] == '#/components/schemas/responseFeedback'
    assert payload['components']['schemas']['statusWrappedConfig']['properties']['feedback']['$ref'] == '#/components/schemas/responseFeedback'
    assert payload['components']['schemas']['chatReply']['properties']['next_action']['$ref'] == '#/components/schemas/responseNextAction'
    assert payload['transcript_snapshots']['schema_version'] == 'transcript_snapshots.v1'
    assert 'ask_stock' in payload['transcript_snapshots']['examples']
    assert payload['transcript_snapshots']['examples']['ask_stock']['entrypoint']['domain'] == 'stock'
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


def test_runtime_contract_schema_endpoint_returns_json_schema():
    client = web_server.app.test_client()

    res = client.get('/api/contracts/runtime-v1/schema')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['$schema'].startswith('https://json-schema.org/')
    assert payload['title'] == 'Runtime API Contract V1'


def test_runtime_contract_openapi_endpoint_returns_openapi_document():
    client = web_server.app.test_client()

    res = client.get('/api/contracts/runtime-v1/openapi')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['openapi'] == '3.1.0'
    assert '/api/events' in payload['paths']
    assert '/api/lab/status/quick' in payload['paths']
    assert '/api/model-routing/preview' in payload['paths']
    assert payload['x-transcript-snapshots']['schema_version'] == 'transcript_snapshots.v1'
    assert 'runtime_status' in payload['x-transcript-snapshots']['examples']


def test_runtime_contract_endpoint_returns_404_when_document_missing(monkeypatch):
    client = web_server.app.test_client()

    def fake_load(document):
        raise FileNotFoundError(document.source_path)

    monkeypatch.setattr(web_server, 'load_runtime_contract_document', fake_load)

    res = client.get('/api/contracts/runtime-v1/schema')

    assert res.status_code == 404
    assert res.get_json()['error'] == 'runtime contract schema not found'


def test_runtime_contract_endpoint_returns_500_for_invalid_document(monkeypatch):
    client = web_server.app.test_client()

    def fake_load(document):
        raise ValueError('broken contract payload')

    monkeypatch.setattr(web_server, 'load_runtime_contract_document', fake_load)

    res = client.get('/api/contracts/runtime-v1/openapi')

    assert res.status_code == 500
    assert res.get_json()['error'] == 'broken contract payload'


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


def test_runtime_contract_removes_legacy_frontend_keys():
    contract = json.loads(CONTRACT_PATH.read_text(encoding='utf-8'))

    assert 'frontend_shell_mount' not in contract
    assert 'legacy_shell_mount' not in contract
    assert 'frontend_preferred_flows' not in contract
    assert all('frontend_preferred' not in endpoint for endpoint in contract['endpoints'])
