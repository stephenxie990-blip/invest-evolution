import json
from typing import Any, cast

import invest_evolution.config as config_module
import invest_evolution.interfaces.web.server as web_server
from invest_evolution.interfaces.web.runtime import StateBackedRuntimeFacade


def test_bind_embedded_runtime_context_updates_runtime_container():
    runtime = object()
    loop = cast(Any, object())

    web_server.bind_embedded_runtime_context(runtime=runtime, loop=loop)
    try:
        assert web_server._runtime is runtime
        assert web_server._loop is loop
        assert web_server._WEB_RUNTIME_CONTAINER.runtime is runtime
        assert web_server._WEB_RUNTIME_CONTAINER.loop is loop
    finally:
        web_server.bind_embedded_runtime_context(runtime=None, loop=None)


def test_runtime_facade_override_updates_runtime_container():
    override = object()

    web_server.set_runtime_facade_override(override)
    try:
        assert web_server._runtime_facade is override
        assert web_server._WEB_RUNTIME_CONTAINER.runtime_facade_override is override
    finally:
        web_server.set_runtime_facade_override(None)


def test_runtime_facade_selection_respects_compat_runtime_alias(monkeypatch):
    monkeypatch.setattr(web_server, "_runtime_facade", None)
    monkeypatch.setattr(web_server, "_runtime", object())
    monkeypatch.setattr(web_server, "_loop", cast(Any, object()))

    selected = web_server._select_runtime_facade()
    web_server._read_embedded_loop()

    assert selected is web_server._in_process_runtime_facade
    assert web_server._WEB_RUNTIME_CONTAINER.runtime is web_server._runtime
    assert web_server._WEB_RUNTIME_CONTAINER.loop is web_server._loop


def test_runtime_readers_sync_container_from_compat_aliases(monkeypatch):
    runtime = object()
    loop = cast(Any, object())
    facade = object()

    monkeypatch.setattr(web_server, "_runtime", runtime)
    monkeypatch.setattr(web_server, "_loop", loop)
    monkeypatch.setattr(web_server, "_runtime_facade", facade)

    assert web_server._read_embedded_runtime() is runtime
    assert web_server._read_embedded_loop() is loop
    assert web_server._read_runtime_facade_override() is facade
    assert web_server._WEB_RUNTIME_CONTAINER.runtime is runtime
    assert web_server._WEB_RUNTIME_CONTAINER.loop is loop
    assert web_server._WEB_RUNTIME_CONTAINER.runtime_facade_override is facade


def test_runtime_shutdown_reader_syncs_container_from_compat_alias(monkeypatch):
    monkeypatch.setattr(web_server, "_runtime_shutdown_registered", True)
    assert web_server._read_runtime_shutdown_registered() is True
    assert web_server._WEB_RUNTIME_CONTAINER.runtime_shutdown_registered is True


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


def test_read_config_bool_treats_false_string_as_false(monkeypatch):
    monkeypatch.setattr(config_module.config, "web_api_require_auth", "false")

    assert web_server._is_web_api_auth_required() is False


def test_read_config_bool_invalid_value_falls_back_to_default(monkeypatch, caplog):
    monkeypatch.setattr(config_module.config, "web_api_public_read_enabled", "not_bool")

    with caplog.at_level("WARNING"):
        result = web_server._is_web_api_public_read_enabled()

    assert result is False
    assert "Invalid web config boolean" in caplog.text


def test_status_route_can_use_explicit_runtime_facade_override(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    web_server.bind_embedded_runtime_context(runtime=None, loop=None)
    web_server.set_runtime_facade_override(
        StateBackedRuntimeFacade(
            project_root_getter=lambda: tmp_path,
            state_file_getter=lambda: tmp_path / "runtime" / "outputs" / "commander" / "state.json",
            runtime_lock_file_getter=lambda: tmp_path / "runtime" / "state" / "commander.lock",
            training_lock_file_getter=lambda: tmp_path / "runtime" / "state" / "training.lock",
            runtime_events_path_getter=lambda: tmp_path / "runtime" / "state" / "commander_events.jsonl",
            data_status_getter=lambda detail: {
                "status": "ok",
                "detail": detail,
                "sources": [],
            },
            config_payload_getter=lambda: {"web_api_public_read_enabled": True},
        )
    )

    try:
        client = web_server.app.test_client()
        res = client.get("/api/status")
    finally:
        web_server.set_runtime_facade_override(None)

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["runtime"]["state_source"] == "runtime_state"
