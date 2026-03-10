import web_server


def test_contract_catalog_endpoint_available_without_runtime():
    client = web_server.app.test_client()

    res = client.get('/api/contracts')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['count'] >= 1
    assert any(item['id'] == 'frontend-v1' for item in payload['items'])


def test_frontend_contract_endpoint_returns_machine_readable_contract():
    client = web_server.app.test_client()

    res = client.get('/api/contracts/frontend-v1')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['contract_id'] == 'frontend-v1'
    assert payload['frontend_shell_mount'] == '/app'
    assert any(endpoint['path'] == '/api/train' and endpoint['method'] == 'POST' for endpoint in payload['endpoints'])
    assert payload['sse']['path'] == '/api/events'


def test_app_shell_returns_helpful_404_when_frontend_dist_missing():
    client = web_server.app.test_client()

    res = client.get('/app')

    assert res.status_code == 404
    payload = res.get_json()
    assert 'frontend dist is not available' in payload['error']
    assert payload['expected_path'].endswith('/frontend/dist')
