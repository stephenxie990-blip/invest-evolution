import json

import pytest

import invest_evolution.config as config_module
import invest_evolution.interfaces.web.server as web_server
from invest_evolution.application.commander.ops import update_evolution_config_payload
from invest_evolution.config.control_plane import clear_control_plane_cache

INVALID_EVOLUTION_CONFIG_PATCH = "evolution_config 不接受 llm 相关 patch；请改用 /api/control_plane 管理 provider / model / api_key 绑定"


def test_evolution_config_hides_llm_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    clear_control_plane_cache()

    client = web_server.app.test_client()
    res = client.get("/api/evolution_config")
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["status"] == "ok"
    assert "llm_fast_model" not in payload["config"]
    assert "llm_deep_model" not in payload["config"]
    assert "llm_api_base" not in payload["config"]
    assert "llm_api_key_masked" not in payload["config"]
    assert "llm_api_key_source" not in payload["config"]
    assert "config_path" not in payload["config"]
    assert "config_file_exists" not in payload["config"]
    assert "runtime_override_path" not in payload["config"]
    assert "runtime_override_exists" not in payload["config"]
    assert "config_layers" not in payload["config"]
    assert "local_override_path" not in payload["config"]
    assert "web_api_token_masked" not in payload["config"]
    assert "web_api_token_source" not in payload["config"]
    assert "audit_log_path" not in payload["config"]
    assert "snapshot_dir" not in payload["config"]
    assert "effective_runtime_mode" not in payload["config"]
    assert "runtime_contract_version" not in payload["config"]
    assert "deprecated_flags" not in payload["config"]


def test_evolution_config_rejects_llm_patch(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    clear_control_plane_cache()

    client = web_server.app.test_client()
    res = client.post(
        "/api/evolution_config",
        data=json.dumps(
            {
                "llm_fast_model": "shell-fast-model",
                "max_stocks": 77,
            }
        ),
        content_type="application/json",
    )
    assert res.status_code == 400
    payload = res.get_json()
    assert payload["status"] == "error"
    assert payload["error"] == INVALID_EVOLUTION_CONFIG_PATCH
    assert payload["invalid_keys"] == ["llm_fast_model"]


def test_evolution_config_rejects_nested_llm_patch(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    clear_control_plane_cache()

    client = web_server.app.test_client()
    res = client.post(
        "/api/evolution_config",
        data=json.dumps(
            {
                "llm": {
                    "bindings": {
                        "controller.main": "foo",
                    }
                },
                "max_stocks": 77,
            }
        ),
        content_type="application/json",
    )
    assert res.status_code == 400
    payload = res.get_json()
    assert payload["status"] == "error"
    assert payload["error"] == INVALID_EVOLUTION_CONFIG_PATCH
    assert payload["invalid_keys"] == ["llm"]


def test_evolution_config_helper_rejects_nested_llm_patch(tmp_path):
    with pytest.raises(ValueError, match="evolution_config 不接受 llm 相关 patch"):
        update_evolution_config_payload(
            patch={"llm": {"bindings": {"controller.main": "foo"}}},
            project_root=tmp_path,
            live_config=config_module.config,
        )


def test_agent_prompts_endpoint_updates_prompt_without_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    clear_control_plane_cache()
    original_configs = dict(config_module.agent_config_registry._configs)
    config_module.agent_config_registry._configs = {
        "hunter": {"llm_model": "fast", "system_prompt": "old prompt"}
    }
    try:
        client = web_server.app.test_client()
        res = client.post(
            "/api/agent_prompts",
            data=json.dumps(
                {
                    "name": "hunter",
                    "system_prompt": "new prompt",
                }
            ),
            content_type="application/json",
        )
        assert res.status_code == 200
        payload = res.get_json()
        assert payload["status"] == "ok"
        assert payload["restart_required"] is False

        listing = client.get("/api/agent_prompts").get_json()
        hunter = next(item for item in listing["configs"] if item["name"] == "hunter")
        assert hunter["system_prompt"] == "new prompt"
        assert "llm_model" not in hunter
    finally:
        config_module.agent_config_registry._configs = original_configs


def test_agent_prompts_endpoint_rejects_llm_model_patch(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    clear_control_plane_cache()

    client = web_server.app.test_client()
    res = client.post(
        "/api/agent_prompts",
        data=json.dumps(
            {
                "name": "hunter",
                "llm_model": "gpt-5-mini",
                "system_prompt": "new prompt",
            }
        ),
        content_type="application/json",
    )

    assert res.status_code == 400
    assert res.get_json()["error"] == (
        "llm_model is not editable on /api/agent_prompts; use /api/control_plane for model binding"
    )


def test_runtime_paths_endpoint_hides_internal_runtime_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    clear_control_plane_cache()
    (tmp_path / "runtime" / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "state" / "runtime_paths.json").write_text(
        json.dumps(
            {
                "training_output_dir": str(
                    tmp_path / "runtime" / "outputs" / "custom_training"
                ),
                "artifact_log_dir": str(
                    tmp_path / "runtime" / "logs" / "custom_artifacts"
                ),
                "config_audit_log_path": str(
                    tmp_path / "runtime" / "state" / "config_changes.jsonl"
                ),
                "config_snapshot_dir": str(
                    tmp_path / "runtime" / "state" / "config_snapshots"
                ),
            }
        ),
        encoding="utf-8",
    )

    client = web_server.app.test_client()
    res = client.get("/api/runtime_paths")

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["config"]["training_output_dir"].endswith("custom_training")
    assert payload["config"]["artifact_log_dir"].endswith("custom_artifacts")
    assert "config_path" not in payload["config"]
    assert "config_file_exists" not in payload["config"]
    assert "config_audit_log_path" not in payload["config"]
    assert "config_snapshot_dir" not in payload["config"]


def test_runtime_paths_endpoint_rejects_internal_runtime_metadata_patch(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    clear_control_plane_cache()

    client = web_server.app.test_client()
    res = client.post(
        "/api/runtime_paths",
        data=json.dumps(
            {
                "config_audit_log_path": str(
                    tmp_path / "runtime" / "state" / "config_changes.jsonl"
                ),
            }
        ),
        content_type="application/json",
    )

    assert res.status_code == 400
    payload = res.get_json()
    assert payload["status"] == "error"
    assert payload["error"] == (
        "runtime_paths public API only accepts training_output_dir and artifact_log_dir; "
        "config audit/snapshot paths are internal runtime details"
    )
    assert payload["invalid_keys"] == ["config_audit_log_path"]


def test_control_plane_api_hides_internal_metadata_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    clear_control_plane_cache()
    client = web_server.app.test_client()

    res = client.get("/api/control_plane")

    assert res.status_code == 200
    payload = res.get_json()
    assert "config_path" not in payload
    assert "local_override_path" not in payload
    assert "local_override_exists" not in payload
    assert "audit_log_path" not in payload
    assert "audit_log_exists" not in payload
    assert "snapshot_dir" not in payload
    assert payload["restart_required"] is False
    assert payload["llm_resolution"]["fast"]["kind"] == "fast"
    assert payload["llm_resolution"]["deep"]["kind"] == "deep"


def test_control_plane_api_rejects_non_public_root_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    clear_control_plane_cache()
    client = web_server.app.test_client()

    res = client.post(
        "/api/control_plane",
        data=json.dumps(
            {
                "config_path": str(tmp_path / "config" / "control_plane.yaml"),
            }
        ),
        content_type="application/json",
    )

    assert res.status_code == 400
    payload = res.get_json()
    assert payload["status"] == "error"
    assert payload["error"] == (
        "control_plane public API only accepts llm and data roots; "
        "config metadata and runtime-only fields are internal details"
    )
    assert payload["invalid_keys"] == ["config_path"]


def test_control_plane_api_rejects_non_public_nested_llm_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    clear_control_plane_cache()
    client = web_server.app.test_client()

    res = client.post(
        "/api/control_plane",
        data=json.dumps(
            {
                "llm": {
                    "providers": {
                        "openai": {
                            "api_base": "https://api.openai.com/v1",
                            "timeout_sec": 30,
                        }
                    }
                }
            }
        ),
        content_type="application/json",
    )

    assert res.status_code == 400
    payload = res.get_json()
    assert payload["status"] == "error"
    assert payload["error"] == (
        "control_plane.llm.providers entries only accept api_base and api_key"
    )
    assert payload["invalid_keys"] == ["llm.providers.openai.timeout_sec"]
