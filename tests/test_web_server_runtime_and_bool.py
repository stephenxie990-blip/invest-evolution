import json

import config as config_module
import web_server


def test_status_returns_503_when_runtime_not_initialized(monkeypatch):
    monkeypatch.setattr(web_server, "_runtime", None)
    monkeypatch.setattr(web_server, "_loop", None)

    client = web_server.app.test_client()
    res = client.get("/api/status")

    assert res.status_code == 503
    data = res.get_json()
    assert "runtime" in data["error"].lower()


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
