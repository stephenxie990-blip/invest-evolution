import invest_evolution.interfaces.web.server as web_server
from invest_evolution.application.commander_main import CommanderConfig, CommanderRuntime


def _make_runtime(tmp_path):
    return CommanderRuntime(CommanderConfig(
        workspace=tmp_path / 'workspace',
        playbook_dir=tmp_path / 'strategies',
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

def test_governance_preview_route_is_removed_from_public_api_surface(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)
    client = web_server.app.test_client()

    res = client.get('/api/governance/preview?cutoff_date=20260310&stock_count=25&allowed_manager_ids=momentum,mean_reversion')

    assert res.status_code == 404


def test_removed_model_routing_preview_route_returns_404(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)
    client = web_server.app.test_client()

    res = client.get('/api/model-routing/preview?cutoff_date=20260310')

    assert res.status_code == 404


def test_managers_api_route_is_removed_from_public_api_surface(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)
    client = web_server.app.test_client()

    res = client.get('/api/managers')

    assert res.status_code == 404
