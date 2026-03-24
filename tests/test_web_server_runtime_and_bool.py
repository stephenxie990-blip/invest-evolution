import json

import invest_evolution.config as config_module
import invest_evolution.interfaces.web.server as web_server


def test_status_returns_state_backed_snapshot_when_runtime_not_initialized(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web_server, "_runtime", None)
    monkeypatch.setattr(web_server, "_loop", None)

    client = web_server.app.test_client()
    res = client.get("/api/status")

    assert res.status_code == 200
    data = res.get_json()
    assert data["runtime"]["state"] == "stopped"
    assert data["runtime"]["live_runtime"] is False
    assert data["runtime"]["state_source"] == "runtime_state"
    assert data["config"]["config_path"].endswith("config/evolution.yaml.example")
    assert data["config"]["effective_runtime_mode"] == "manager_portfolio"
    assert data["runtime_paths"]["training_output_dir"] == str(
        tmp_path / "runtime" / "outputs" / "training"
    )


def test_evolution_config_bool_string_false(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config_module.config, "enable_debate", True)

    client = web_server.app.test_client()
    res = client.post(
        "/api/evolution_config",
        data=json.dumps({"enable_debate": "false"}),
        content_type="application/json",
    )

    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"
    assert config_module.config.enable_debate is False

    res2 = client.get("/api/evolution_config")
    assert res2.status_code == 200
    assert res2.get_json()["config"]["enable_debate"] is False
    assert "effective_runtime_mode" not in res2.get_json()["config"]
    assert "runtime_contract_version" not in res2.get_json()["config"]
    assert "deprecated_flags" not in res2.get_json()["config"]


def test_evolution_config_invalid_bool_returns_400(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)

    client = web_server.app.test_client()
    res = client.post(
        "/api/evolution_config",
        data=json.dumps({"enable_debate": "not_bool"}),
        content_type="application/json",
    )

    assert res.status_code == 400
    payload = res.get_json()
    assert payload["status"] == "error"
    assert "enable_debate" in payload["error"]


def test_evolution_config_patch_reports_effective_runtime_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)

    client = web_server.app.test_client()
    res = client.post(
        "/api/evolution_config",
        data=json.dumps({"manager_arch_enabled": "false"}),
        content_type="application/json",
    )

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["config"]["manager_arch_enabled"] is False
    assert "effective_runtime_mode" not in payload["config"]
    assert "deprecated_flags" not in payload["config"]
