from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

from flask import Flask

import invest_evolution.interfaces.web.server as web_server
import invest_evolution.interfaces.web.routes as web_routes
from invest_evolution.application.commander_main import CommanderConfig, CommanderRuntime
from invest_evolution.interfaces.web.routes import (
    _execute_config_update,
    _respond_config_surface_read,
)


def _make_runtime(tmp_path: Path) -> CommanderRuntime:
    return CommanderRuntime(
        CommanderConfig(
            workspace=tmp_path / "workspace",
            playbook_dir=tmp_path / "strategies",
            state_file=tmp_path / "state.json",
            cron_store=tmp_path / "cron.json",
            memory_store=tmp_path / "memory.jsonl",
            plugin_dir=tmp_path / "plugins",
            bridge_inbox=tmp_path / "inbox",
            bridge_outbox=tmp_path / "outbox",
            training_output_dir=tmp_path / "training",
            artifact_log_dir=tmp_path / "artifacts",
            config_audit_log_path=tmp_path / "runtime" / "state" / "config_changes.jsonl",
            config_snapshot_dir=tmp_path / "runtime" / "state" / "config_snapshots",
            mock_mode=True,
            autopilot_enabled=False,
            heartbeat_enabled=False,
            bridge_enabled=False,
        )
    )


def _install_runtime(monkeypatch, runtime: CommanderRuntime) -> None:
    monkeypatch.setattr(web_server, "_runtime", runtime)
    monkeypatch.setattr(web_server, "_loop", object())
    monkeypatch.setattr(web_server, "_run_async", lambda coro: asyncio.run(coro))


def test_api_chat_logs_route_context_on_unexpected_error(tmp_path: Path, monkeypatch, caplog) -> None:
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)

    async def fail_ask(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("boom")

    monkeypatch.setattr(runtime, "ask", fail_ask)
    client = web_server.app.test_client()

    with caplog.at_level(logging.ERROR):
        response = client.post(
            "/api/chat",
            json={
                "message": "hello world",
                "session_key": "api:chat:test",
                "chat_id": "chat:test",
                "request_id": "req:test",
            },
        )

    assert response.status_code == 500
    assert response.get_json()["error"] == "boom"
    assert (
        "Chat route failed: session_key=api:chat:test chat_id=chat:test request_id=req:test view=json message_length=11"
        in caplog.text
    )


def test_api_train_route_is_removed_from_public_api_surface(tmp_path: Path, monkeypatch) -> None:
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)
    client = web_server.app.test_client()
    response = client.post("/api/train", json={"rounds": 3, "mock": True})
    assert response.status_code == 404


def test_api_governance_preview_route_is_removed_from_public_api_surface(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)
    client = web_server.app.test_client()
    response = client.get(
        "/api/governance/preview?cutoff_date=20260321&stock_count=25&min_history_days=120&allowed_manager_ids=momentum&allowed_manager_ids=mean_reversion"
    )
    assert response.status_code == 404


def test_api_training_plan_execute_logs_route_context_on_unexpected_error(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    runtime = _make_runtime(tmp_path)
    _install_runtime(monkeypatch, runtime)

    async def fail_execute_training_plan(plan_id: str):
        del plan_id
        raise RuntimeError("boom")

    monkeypatch.setattr(runtime, "execute_training_plan", fail_execute_training_plan)
    client = web_server.app.test_client()

    with caplog.at_level(logging.ERROR):
        response = client.post("/api/lab/training/plans/plan_demo/execute")

    assert response.status_code == 500
    assert response.get_json()["error"] == "boom"
    assert "Training plan execution route failed: plan_id=plan_demo" in caplog.text


def test_execute_config_update_logs_payload_keys_on_failure(caplog) -> None:
    app = Flask(__name__)
    logger = logging.getLogger("tests.web_route_boundary_logging")

    class RuntimeFacade:
        @staticmethod
        def get_runtime():
            return None

    def fail_fallback_update():
        raise RuntimeError("boom")

    with app.app_context():
        with caplog.at_level(logging.ERROR):
            response = _execute_config_update(
                runtime_facade=cast(Any, RuntimeFacade()),
                build_contract_payload_response=lambda payload: payload,
                runtime_update=lambda runtime: runtime,
                fallback_update=fail_fallback_update,
                logger=logger,
                error_label="Control plane update",
                request_payload={"llm": {"provider": "openai"}, "enabled": True},
            )

    assert response.status_code == 500
    assert response.get_json() == {"status": "error", "error": "boom"}
    assert "Control plane update error: runtime_loaded=False payload_keys=['enabled', 'llm']" in caplog.text


def test_execute_config_update_uses_contract_builder_for_fallback_updates() -> None:
    app = Flask(__name__)
    built_payloads: list[dict[str, Any]] = []

    class RuntimeFacade:
        @staticmethod
        def get_runtime():
            return None

    def build_response(payload: Any):
        built_payloads.append(cast(dict[str, Any], payload))
        return payload

    with app.app_context():
        response = _execute_config_update(
            runtime_facade=cast(Any, RuntimeFacade()),
            build_contract_payload_response=build_response,
            runtime_update=lambda runtime: runtime,
            fallback_update=lambda: {"status": "ok", "updated": ["x"]},
            logger=logging.getLogger("tests.web_route_boundary_logging"),
            error_label="Runtime paths update",
            request_payload={"training_output_dir": "/tmp/training"},
        )

    assert response == {"status": "ok", "updated": ["x"]}
    assert built_payloads == [{"status": "ok", "updated": ["x"]}]


def test_respond_config_surface_read_uses_runtime_surface_when_available() -> None:
    app = Flask(__name__)

    class Runtime:
        @staticmethod
        def get_control_plane():
            return {"source": "runtime", "surface": "control_plane"}

    class RuntimeFacade:
        @staticmethod
        def get_runtime():
            return Runtime()

    with app.test_request_context("/api/control_plane?view=json"):
        response = _respond_config_surface_read(
            surface="control_plane",
            runtime_facade=cast(Any, RuntimeFacade()),
            request_view_arg=lambda: "json",
            respond_with_display=lambda payload, **_: payload,
        )

    assert response == {"source": "runtime", "surface": "control_plane"}


def test_respond_config_surface_read_uses_fallback_surface_when_runtime_missing(
    monkeypatch, tmp_path: Path
) -> None:
    app = Flask(__name__)

    class RuntimeFacade:
        @staticmethod
        def get_runtime():
            return None

    monkeypatch.setattr(web_routes.config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        web_routes,
        "build_config_surface_read_specs",
        lambda *, project_root, live_config: {
            "runtime_paths": web_routes.ConfigSurfaceReadSpec(
                runtime_fetch=lambda runtime: runtime.get_runtime_paths(),
                fallback_fetch=lambda: {
                    "source": "fallback",
                    "surface": "runtime_paths",
                    "project_root": str(project_root),
                    "runtime": None,
                },
            )
        },
    )

    with app.test_request_context("/api/runtime_paths?view=json"):
        response = _respond_config_surface_read(
            surface="runtime_paths",
            runtime_facade=cast(Any, RuntimeFacade()),
            request_view_arg=lambda: "json",
            respond_with_display=lambda payload, **_: payload,
        )

    assert response == {
        "source": "fallback",
        "surface": "runtime_paths",
        "project_root": str(tmp_path),
        "runtime": None,
    }
