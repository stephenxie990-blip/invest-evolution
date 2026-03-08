import web_server
from commander import CommanderConfig, CommanderRuntime


def test_investment_models_api(tmp_path, monkeypatch):
    runtime = CommanderRuntime(CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state.json",
        cron_store=tmp_path / "cron.json",
        memory_store=tmp_path / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "inbox",
        bridge_outbox=tmp_path / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    ))
    monkeypatch.setattr(web_server, "_runtime", runtime)
    client = web_server.app.test_client()

    res = client.get("/api/investment-models")
    assert res.status_code == 200
    data = res.get_json()
    assert "momentum" in data["items"]
    assert data["active_model"] == runtime.body.controller.model_name
    assert "enable_v2_pipeline" not in data
