import json

import config as config_module
import web_server


def test_evolution_config_get_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)

    client = web_server.app.test_client()
    res = client.get("/api/evolution_config")
    assert res.status_code == 200

    data = res.get_json()
    assert data["status"] == "ok"
    cfg = data["config"]
    assert "llm_fast_model" in cfg
    assert "llm_deep_model" in cfg
    assert "llm_api_base" in cfg
    assert "data_source" in cfg
    assert "max_stocks" in cfg


def test_evolution_config_update_writes_yaml(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)

    client = web_server.app.test_client()
    res = client.post(
        "/api/evolution_config",
        data=json.dumps({"max_stocks": 12, "data_source": "baostock"}),
        content_type="application/json",
    )
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"

    cfg_path = tmp_path / "config" / "evolution.yaml"
    assert cfg_path.exists()

    res2 = client.get("/api/evolution_config")
    cfg2 = res2.get_json()["config"]
    assert cfg2["max_stocks"] == 12
    assert cfg2["data_source"] == "baostock"


def test_agent_configs_update_persists_json(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)

    cfg_path = tmp_path / "agent_settings" / "agents_config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps({"A": {"role": "x", "llm_model": "", "system_prompt": ""}}, ensure_ascii=False),
        encoding="utf-8",
    )

    config_module.agent_config_registry.json_path = cfg_path
    config_module.agent_config_registry.reload()

    client = web_server.app.test_client()
    res = client.post(
        "/api/agent_configs",
        data=json.dumps({"name": "A", "llm_model": "m", "system_prompt": "p"}),
        content_type="application/json",
    )
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"

    stored = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert stored["A"]["llm_model"] == "m"
    assert stored["A"]["system_prompt"] == "p"

