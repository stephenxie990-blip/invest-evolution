import web_server
from commander import CommanderConfig, CommanderRuntime



def _make_runtime(tmp_path):
    return CommanderRuntime(CommanderConfig(
        workspace=tmp_path / 'workspace',
        strategy_dir=tmp_path / 'strategies',
        state_file=tmp_path / 'state.json',
        cron_store=tmp_path / 'cron.json',
        memory_store=tmp_path / 'memory.jsonl',
        plugin_dir=tmp_path / 'plugins',
        bridge_inbox=tmp_path / 'inbox',
        bridge_outbox=tmp_path / 'outbox',
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    ))



def _install_runtime(monkeypatch, runtime):
    monkeypatch.setattr(web_server, '_runtime', runtime)



def test_model_routing_preview_api_returns_controller_preview(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)
    controller = runtime.body.controller
    monkeypatch.setattr(
        controller,
        'preview_model_routing',
        lambda **kwargs: {
            'current_model': 'momentum',
            'selected_model': 'mean_reversion',
            'regime': 'oscillation',
            'decision_source': 'rule_allocator',
            'switch_applied': True,
            'hold_current': False,
        },
    )
    client = web_server.app.test_client()

    res = client.get('/api/model-routing/preview?cutoff_date=20260310&stock_count=25&allowed_models=momentum,mean_reversion')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['status'] == 'ok'
    assert payload['routing']['selected_model'] == 'mean_reversion'
    assert payload['routing']['regime'] == 'oscillation'



def test_investment_models_api_exposes_routing_state(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)
    controller = runtime.body.controller
    controller.model_routing_enabled = True
    controller.model_routing_mode = 'rule'
    controller.model_routing_allowed_models = ['momentum', 'mean_reversion']
    controller.last_routing_decision = {'selected_model': 'mean_reversion', 'regime': 'oscillation'}
    client = web_server.app.test_client()

    res = client.get('/api/investment-models')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["count"] == len(payload["items"])
    assert payload['routing']['enabled'] is True
    assert payload['routing']['mode'] == 'rule'
    assert payload['routing']['allowed_models'] == ['momentum', 'mean_reversion']
    assert payload['routing']['last_decision']['selected_model'] == 'mean_reversion'
