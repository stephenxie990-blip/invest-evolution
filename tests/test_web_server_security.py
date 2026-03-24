import asyncio
import importlib
import json
import logging
import os
import sys
import threading
from types import SimpleNamespace

import pytest

import invest_evolution.config as config_module
import invest_evolution.interfaces.web.routes as web_routes
import invest_evolution.interfaces.web.server as web_server
from invest_evolution.application.commander_main import (
    CommanderConfig,
    CommanderRuntime,
)


@pytest.fixture(autouse=True)
def _reset_web_server_state():
    original_runtime = web_server._runtime
    original_loop = web_server._loop
    original_shutdown_registered = web_server._runtime_shutdown_registered
    state = web_server.get_ephemeral_web_state()
    original_rate_limit_events = state.rate_limit_events
    original_data_download_running = state.data_download_running
    try:
        web_server._runtime = None
        web_server._loop = None
        state.rate_limit_events = {}
        state.data_download_running = False
        web_server._runtime_shutdown_registered = False
        yield
    finally:
        web_server._runtime = original_runtime
        web_server._loop = original_loop
        state.rate_limit_events = original_rate_limit_events
        state.data_download_running = original_data_download_running
        web_server._runtime_shutdown_registered = original_shutdown_registered


def _make_runtime(tmp_path):
    cfg = CommanderConfig(
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
    return CommanderRuntime(cfg)


def test_healthz_is_public(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    client = web_server.app.test_client()
    res = client.get("/healthz")

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["status"] == "ok"
    assert payload["service"] == "invest-web"
    runtime = payload["runtime"]
    assert runtime["initialized"] is False
    assert runtime["loop_running"] is False
    assert runtime["mode"] == "state_backed"
    assert isinstance(runtime["event_buffer_size"], int)
    assert runtime["event_buffer_size"] >= 0
    assert isinstance(runtime["event_history_size"], int)
    assert runtime["event_history_size"] >= 0
    assert isinstance(runtime["event_dispatcher_started"], bool)


def test_healthz_reports_embedded_runtime_shape(monkeypatch):
    class FakeLoop:
        @staticmethod
        def is_running():
            return True

    monkeypatch.setattr(web_server, "_runtime", object())
    monkeypatch.setattr(web_server, "_loop", FakeLoop())

    client = web_server.app.test_client()
    res = client.get("/healthz")

    assert res.status_code == 200
    payload = res.get_json()
    runtime = payload["runtime"]
    assert runtime["mode"] == "embedded"
    assert runtime["provider"] == "embedded"
    assert runtime["initialized"] is True
    assert runtime["live_runtime"] is True
    assert runtime["loop_running"] is True


def test_root_returns_api_entrypoint_summary():
    client = web_server.app.test_client()
    res = client.get("/")

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["service"] == "invest-api"
    assert payload["human_entrypoint"] == "invest-commander"
    assert payload["batch_entrypoint"] == "invest-train"
    assert payload["entrypoints"]["chat"] == "/api/chat"
    assert payload["entrypoints"]["contracts"] == "/api/contracts/runtime-v2"


def test_api_status_requires_auth_when_enabled(monkeypatch):
    runtime = SimpleNamespace(
        status=lambda detail="fast": {"detail": detail, "status": "ok"}
    )
    monkeypatch.setattr(web_server, "_runtime", runtime)
    monkeypatch.setattr(config_module.config, "web_api_require_auth", True)
    monkeypatch.setattr(config_module.config, "web_api_token", "secret-token")
    monkeypatch.setattr(config_module.config, "web_api_public_read_enabled", False)

    client = web_server.app.test_client()

    missing = client.get("/api/status")
    assert missing.status_code == 401

    wrong = client.get("/api/status", headers={"Authorization": "Bearer wrong-token"})
    assert wrong.status_code == 403

    ok = client.get("/api/status", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200
    assert ok.get_json()["detail"] == "fast"


def test_api_status_allows_x_real_ip_proxy_when_auth_disabled(monkeypatch):
    runtime = SimpleNamespace(
        status=lambda detail="fast": {"detail": detail, "status": "ok"}
    )
    monkeypatch.setattr(web_server, "_runtime", runtime)
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)
    monkeypatch.setattr(config_module.config, "web_api_token", "")
    monkeypatch.setattr(config_module.config, "web_api_public_read_enabled", False)

    client = web_server.app.test_client()
    res = client.get("/api/status", headers={"X-Real-IP": "198.51.100.10"})

    assert res.status_code == 200
    assert res.get_json()["detail"] == "fast"


@pytest.mark.parametrize("header_name", ["Authorization", "X-Invest-Token"])
def test_public_status_can_remain_accessible_when_configured(monkeypatch, header_name):
    runtime = SimpleNamespace(
        status=lambda detail="fast": {"detail": detail, "status": "ok"}
    )
    monkeypatch.setattr(web_server, "_runtime", runtime)
    monkeypatch.setattr(config_module.config, "web_api_require_auth", True)
    monkeypatch.setattr(config_module.config, "web_api_token", "secret-token")
    monkeypatch.setattr(config_module.config, "web_api_public_read_enabled", True)

    client = web_server.app.test_client()

    res = client.get("/api/status")
    assert res.status_code == 200

    authed = client.get(
        "/api/status",
        headers={
            header_name: "Bearer secret-token"
            if header_name == "Authorization"
            else "secret-token"
        },
    )
    assert authed.status_code == 200


def test_mutating_endpoint_still_requires_auth_when_public_reads_enabled(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config_module.config, "web_api_require_auth", True)
    monkeypatch.setattr(config_module.config, "web_api_token", "secret-token")
    monkeypatch.setattr(config_module.config, "web_api_public_read_enabled", True)

    client = web_server.app.test_client()
    res = client.post(
        "/api/evolution_config",
        data=json.dumps({"enable_debate": False}),
        content_type="application/json",
    )

    assert res.status_code == 401


def test_bootstrap_runtime_services_starts_runtime_once(monkeypatch):
    callbacks = []
    thread_names = []
    registered = []
    started = []
    env_calls = []

    class FakeRuntime:
        def __init__(self, cfg):
            self.cfg = cfg

        async def start(self):
            started.append(
                {
                    "mock_mode": self.cfg.mock_mode,
                    "autopilot_enabled": self.cfg.autopilot_enabled,
                    "heartbeat_enabled": self.cfg.heartbeat_enabled,
                    "bridge_enabled": self.cfg.bridge_enabled,
                }
            )

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.name = name

        def start(self):
            thread_names.append(self.name)

    class FakeConfig:
        @staticmethod
        def from_args(args):
            del args
            return SimpleNamespace(
                mock_mode=False,
                autopilot_enabled=True,
                heartbeat_enabled=True,
                bridge_enabled=True,
            )

    monkeypatch.setattr(
        web_server,
        "load_default_commander_runtime_types",
        lambda: (FakeConfig, FakeRuntime),
    )
    monkeypatch.setattr(
        web_server, "ensure_environment", lambda **kwargs: env_calls.append(kwargs)
    )
    monkeypatch.setattr(web_server, "set_event_callback", callbacks.append)
    monkeypatch.setattr(web_server.asyncio, "new_event_loop", lambda: object())
    monkeypatch.setattr(web_server.threading, "Thread", FakeThread)
    monkeypatch.setattr(web_server, "_run_async", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(
        web_server, "_register_runtime_shutdown", lambda: registered.append(True)
    )

    runtime = web_server.bootstrap_runtime_services(
        host="127.0.0.1", mock=True, source="cli"
    )
    runtime_again = web_server.bootstrap_runtime_services(
        host="127.0.0.1", mock=True, source="cli"
    )

    assert runtime is runtime_again
    assert env_calls == [
        {
            "required_modules": ["pandas"],
            "require_project_python": False,
            "validate_requests_stack": False,
            "component": "web embedded runtime",
        }
    ]
    assert started == [
        {
            "mock_mode": True,
            "autopilot_enabled": False,
            "heartbeat_enabled": False,
            "bridge_enabled": False,
        }
    ]
    assert len(callbacks) == 1
    assert thread_names == ["web-event-loop:cli"]
    assert registered == [True]


def test_bootstrap_runtime_services_logs_context_on_start_failure(monkeypatch, caplog):
    callbacks = []

    class FakeRuntime:
        def __init__(self, cfg):
            self.cfg = cfg

        async def start(self):
            return None

    class FakeLoop:
        @staticmethod
        def stop():
            return None

        @staticmethod
        def call_soon_threadsafe(callback):
            del callback
            raise RuntimeError("loop stop failed")

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            del target, args, daemon
            self.name = name

        def start(self):
            return None

    def _fail_run_async(coro):
        coro.close()
        raise RuntimeError("runtime start failed")

    class FakeConfig:
        @staticmethod
        def from_args(args):
            del args
            return SimpleNamespace(
                mock_mode=False,
                autopilot_enabled=True,
                heartbeat_enabled=True,
                bridge_enabled=True,
            )

    monkeypatch.setattr(
        web_server,
        "load_default_commander_runtime_types",
        lambda: (FakeConfig, FakeRuntime),
    )
    monkeypatch.setattr(web_server, "ensure_environment", lambda **kwargs: None)
    monkeypatch.setattr(web_server, "set_event_callback", callbacks.append)
    monkeypatch.setattr(web_server.asyncio, "new_event_loop", lambda: FakeLoop())
    monkeypatch.setattr(web_server.threading, "Thread", FakeThread)
    monkeypatch.setattr(web_server, "_run_async", _fail_run_async)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError, match="runtime start failed"):
            web_server.bootstrap_runtime_services(
                host="127.0.0.1", mock=True, source="cli"
            )

    assert len(callbacks) == 1
    assert (
        "Commander runtime bootstrap failed: host=127.0.0.1 mock=True source=cli runtime_type=FakeRuntime loop_thread=web-event-loop:cli"
        in caplog.text
    )
    assert (
        "Failed to stop event loop after bootstrap error: host=127.0.0.1 mock=True source=cli loop_type=FakeLoop"
        in caplog.text
    )
    assert web_server._runtime is None
    assert web_server._loop is None


def test_bootstrap_runtime_services_requires_auth_for_non_loopback(monkeypatch):
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)
    monkeypatch.setattr(config_module.config, "web_api_token", "")

    with pytest.raises(RuntimeError, match="WEB_API_REQUIRE_AUTH=true"):
        web_server.bootstrap_runtime_services(host="0.0.0.0", source="cli")


def test_embedded_runtime_bootstrap_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WEB_EMBEDDED_RUNTIME_ENABLED", raising=False)
    called = []

    monkeypatch.setattr(
        web_server, "bootstrap_runtime_services", lambda **kwargs: called.append(kwargs)
    )

    result = web_server.bootstrap_embedded_runtime_if_enabled(
        host="127.0.0.1", source="gunicorn"
    )

    assert result is None
    assert called == []


def test_embedded_runtime_bootstrap_rejects_multi_worker_mode(monkeypatch):
    monkeypatch.setenv("WEB_EMBEDDED_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("GUNICORN_WORKERS", "2")

    with pytest.raises(RuntimeError, match="requires GUNICORN_WORKERS=1"):
        web_server.bootstrap_embedded_runtime_if_enabled(
            host="127.0.0.1", source="gunicorn"
        )


def test_embedded_runtime_bootstrap_uses_explicit_helper(monkeypatch):
    monkeypatch.setenv("WEB_EMBEDDED_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("GUNICORN_WORKERS", "1")
    calls = []

    def _bootstrap(**kwargs):
        calls.append(kwargs)
        return "runtime"

    monkeypatch.setattr(web_server, "bootstrap_runtime_services", _bootstrap)

    result = web_server.bootstrap_embedded_runtime_if_enabled(
        host="127.0.0.1", mock=True, source="gunicorn"
    )

    assert result == "runtime"
    assert calls == [{"host": "127.0.0.1", "mock": True, "source": "gunicorn"}]


def test_shutdown_runtime_services_logs_context_on_stop_failure(monkeypatch, caplog):
    class FakeFuture:
        @staticmethod
        def result(timeout):
            del timeout
            raise RuntimeError("runtime stop failed")

    class FakeRuntime:
        async def stop(self):
            return None

    class FakeLoop:
        @staticmethod
        def stop():
            return None

        @staticmethod
        def call_soon_threadsafe(callback):
            del callback
            raise RuntimeError("loop stop failed")

    monkeypatch.setattr(web_server, "_runtime", FakeRuntime())
    monkeypatch.setattr(web_server, "_loop", FakeLoop())
    monkeypatch.setattr(
        web_server.asyncio,
        "run_coroutine_threadsafe",
        lambda coro, loop: (coro.close(), loop, FakeFuture())[2],
    )

    with caplog.at_level(logging.DEBUG):
        web_server.shutdown_runtime_services()

    assert (
        "Failed to stop commander runtime cleanly during shutdown: runtime_type=FakeRuntime loop_type=FakeLoop"
        in caplog.text
    )
    assert (
        "Failed to stop web event loop cleanly during shutdown: runtime_type=FakeRuntime loop_type=FakeLoop"
        in caplog.text
    )
    assert web_server._runtime is None
    assert web_server._loop is None


def test_wsgi_import_is_side_effect_free(monkeypatch):
    import invest_evolution.interfaces.web.server as app_web_server

    def _unexpected_bootstrap(**kwargs):
        raise AssertionError(f"unexpected bootstrap: {kwargs}")

    monkeypatch.setattr(
        app_web_server, "bootstrap_runtime_services", _unexpected_bootstrap
    )
    monkeypatch.setattr(app_web_server, "_runtime", None)
    monkeypatch.setattr(app_web_server, "_loop", None)
    sys.modules.pop("invest_evolution.interfaces.web.wsgi", None)

    module = importlib.import_module("invest_evolution.interfaces.web.wsgi")

    assert module.app is app_web_server.app
    assert app_web_server._runtime is None
    assert app_web_server._loop is None


def test_memory_detail_blocks_artifacts_outside_runtime_roots(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    monkeypatch.setattr(web_server, "_runtime", runtime)
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)

    leaked = tmp_path / "secrets.json"
    leaked.write_text(json.dumps({"secret": "should-not-leak"}), encoding="utf-8")

    rec = runtime.memory.append(
        kind="training_run",
        session_key="runtime:train",
        content="训练记录",
        metadata={
            "training_run": True,
            "summary": {"status": "ok"},
            "results": [
                {
                    "cycle_id": 1,
                    "config_snapshot_path": str(leaked),
                    "artifacts": {
                        "cycle_result_path": str(leaked),
                        "selection_artifact_markdown_path": str(leaked),
                    },
                }
            ],
        },
    )

    client = web_server.app.test_client()
    res = client.get(f"/api/memory/{rec.id}")

    assert res.status_code == 404


def test_read_rate_limit_returns_429(monkeypatch):
    runtime = SimpleNamespace(
        status=lambda detail="fast": {"detail": detail, "status": "ok"}
    )
    state = web_server.get_ephemeral_web_state()
    monkeypatch.setattr(web_server, "_runtime", runtime)
    monkeypatch.setattr(state, "rate_limit_events", {})
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)
    monkeypatch.setattr(config_module.config, "web_rate_limit_enabled", True)
    monkeypatch.setattr(config_module.config, "web_rate_limit_window_sec", 60)
    monkeypatch.setattr(config_module.config, "web_rate_limit_read_max", 2)
    monkeypatch.setattr(config_module.config, "web_rate_limit_write_max", 20)
    monkeypatch.setattr(config_module.config, "web_rate_limit_heavy_max", 5)

    client = web_server.app.test_client()

    assert client.get("/api/status").status_code == 200
    assert client.get("/api/status").status_code == 200
    limited = client.get("/api/status")
    assert limited.status_code == 429
    assert limited.get_json()["error"] == "rate limit exceeded"


def test_read_rate_limit_groups_dynamic_path_by_route_rule(monkeypatch):
    state = web_server.get_ephemeral_web_state()
    runtime = SimpleNamespace(
        get_training_plan=lambda artifact_id: {"plan_id": artifact_id},
    )
    monkeypatch.setattr(web_server, "_runtime", runtime)
    monkeypatch.setattr(state, "rate_limit_events", {})
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)
    monkeypatch.setattr(config_module.config, "web_rate_limit_enabled", True)
    monkeypatch.setattr(config_module.config, "web_rate_limit_window_sec", 60)
    monkeypatch.setattr(config_module.config, "web_rate_limit_read_max", 1)
    monkeypatch.setattr(config_module.config, "web_rate_limit_write_max", 20)
    monkeypatch.setattr(config_module.config, "web_rate_limit_heavy_max", 5)

    client = web_server.app.test_client()

    first = client.get("/api/lab/training/plans/plan_a")
    second = client.get("/api/lab/training/plans/plan_b")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.get_json()["error"] == "rate limit exceeded"


def test_read_rate_limit_ignores_spoofed_x_forwarded_for(monkeypatch):
    runtime = SimpleNamespace(
        status=lambda detail="fast": {"detail": detail, "status": "ok"}
    )
    state = web_server.get_ephemeral_web_state()
    monkeypatch.setattr(web_server, "_runtime", runtime)
    monkeypatch.setattr(state, "rate_limit_events", {})
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)
    monkeypatch.setattr(config_module.config, "web_rate_limit_enabled", True)
    monkeypatch.setattr(config_module.config, "web_rate_limit_window_sec", 60)
    monkeypatch.setattr(config_module.config, "web_rate_limit_read_max", 1)
    monkeypatch.setattr(config_module.config, "web_rate_limit_write_max", 20)
    monkeypatch.setattr(config_module.config, "web_rate_limit_heavy_max", 5)

    client = web_server.app.test_client()

    first = client.get("/api/status", headers={"X-Forwarded-For": "198.51.100.10"})
    second = client.get("/api/status", headers={"X-Forwarded-For": "203.0.113.20"})

    assert first.status_code == 200
    assert second.status_code == 429


def test_read_rate_limit_uses_x_real_ip_from_loopback_proxy(monkeypatch):
    runtime = SimpleNamespace(
        status=lambda detail="fast": {"detail": detail, "status": "ok"}
    )
    state = web_server.get_ephemeral_web_state()
    monkeypatch.setattr(web_server, "_runtime", runtime)
    monkeypatch.setattr(state, "rate_limit_events", {})
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)
    monkeypatch.setattr(config_module.config, "web_rate_limit_enabled", True)
    monkeypatch.setattr(config_module.config, "web_rate_limit_window_sec", 60)
    monkeypatch.setattr(config_module.config, "web_rate_limit_read_max", 1)
    monkeypatch.setattr(config_module.config, "web_rate_limit_write_max", 20)
    monkeypatch.setattr(config_module.config, "web_rate_limit_heavy_max", 5)

    client = web_server.app.test_client()

    first = client.get("/api/status", headers={"X-Real-IP": "198.51.100.10"})
    second = client.get("/api/status", headers={"X-Real-IP": "203.0.113.20"})

    assert first.status_code == 200
    assert second.status_code == 200


def test_read_rate_limit_uses_shared_state_in_multi_worker_mode(tmp_path, monkeypatch):
    runtime = SimpleNamespace(
        status=lambda detail="fast": {"detail": detail, "status": "ok"}
    )
    state = web_server.get_ephemeral_web_state()
    monkeypatch.setattr(web_server, "_runtime", runtime)
    monkeypatch.setattr(state, "rate_limit_events", {})
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)
    monkeypatch.setattr(config_module.config, "web_rate_limit_enabled", True)
    monkeypatch.setattr(config_module.config, "web_rate_limit_window_sec", 60)
    monkeypatch.setattr(config_module.config, "web_rate_limit_read_max", 1)
    monkeypatch.setattr(config_module.config, "web_rate_limit_write_max", 20)
    monkeypatch.setattr(config_module.config, "web_rate_limit_heavy_max", 5)
    monkeypatch.setenv("GUNICORN_WORKERS", "2")
    monkeypatch.setattr(
        web_server,
        "_default_rate_limit_state_file",
        lambda: tmp_path / "runtime" / "state" / "web_rate_limits.json",
    )

    client = web_server.app.test_client()

    first = client.get("/api/status")
    assert first.status_code == 200

    state.rate_limit_events = {}
    second = client.get("/api/status")
    assert second.status_code == 429


def test_heavy_rate_limit_returns_429_for_training_plan_execute(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path)
    state = web_server.get_ephemeral_web_state()
    monkeypatch.setattr(web_server, "_runtime", runtime)
    monkeypatch.setattr(web_server, "_loop", object())
    monkeypatch.setattr(state, "rate_limit_events", {})
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)
    monkeypatch.setattr(config_module.config, "web_rate_limit_enabled", True)
    monkeypatch.setattr(config_module.config, "web_rate_limit_window_sec", 60)
    monkeypatch.setattr(config_module.config, "web_rate_limit_read_max", 20)
    monkeypatch.setattr(config_module.config, "web_rate_limit_write_max", 10)
    monkeypatch.setattr(config_module.config, "web_rate_limit_heavy_max", 1)

    async def fake_execute_training_plan(plan_id: str):
        return {"status": "ok", "training_lab": {"plan": {"plan_id": plan_id}}}

    monkeypatch.setattr(runtime, "execute_training_plan", fake_execute_training_plan)
    monkeypatch.setattr(web_server, "_run_async", lambda coro: asyncio.run(coro))

    client = web_server.app.test_client()

    first = client.post("/api/lab/training/plans/plan_demo/execute")
    assert first.status_code == 200

    second = client.post("/api/lab/training/plans/plan_demo/execute")
    assert second.status_code == 429
    assert second.headers["Retry-After"]


def test_fallback_data_download_lock_file_uses_owner_only_permissions(
    tmp_path, monkeypatch
):
    captured = {}
    real_open = os.open

    def _capturing_open(path, flags, mode=0o777):
        captured["mode"] = mode
        return real_open(path, flags, mode)

    monkeypatch.setattr(web_routes.os, "open", _capturing_open)

    class _Thread:
        def __init__(self, target):
            self._target = target

        def start(self):
            return None

    running = {"value": False}
    with web_server.app.test_request_context(
        "/api/data/download",
        method="POST",
        data=json.dumps({"confirm": True}),
        content_type="application/json",
    ):
        response = web_routes._respond_fallback_data_download(
            parse_bool=web_server._parse_bool,
            build_json_payload_response=lambda payload: payload,
            lock_file_path=tmp_path / "runtime" / "state" / "web_data_download.lock",
            logger=logging.getLogger(__name__),
            data_download_lock=threading.Lock(),
            get_data_download_running=lambda: running["value"],
            set_data_download_running=lambda value: running.__setitem__("value", value),
            thread_factory=lambda target: _Thread(target),
        )

    assert response["status"] == "started"
    assert captured["mode"] == 0o600


def test_api_data_status_rejects_invalid_refresh_query(monkeypatch):
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)

    client = web_server.app.test_client()
    res = client.get("/api/data/status?refresh=bad")

    assert res.status_code == 400
    assert "refresh must be a boolean" in res.get_json()["error"]


def test_api_allocator_route_is_removed_from_public_api_surface(monkeypatch):
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)

    client = web_server.app.test_client()
    res = client.get("/api/allocator?top_n=bad")

    assert res.status_code == 404


def test_api_cron_route_is_removed_from_public_api_surface(monkeypatch, tmp_path):
    runtime = _make_runtime(tmp_path)
    monkeypatch.setattr(web_server, "_runtime", runtime)
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)

    client = web_server.app.test_client()
    res = client.post(
        "/api/cron",
        data=json.dumps({"name": "heartbeat", "message": "ping", "every_sec": "bad"}),
        content_type="application/json",
    )

    assert res.status_code == 404


def test_api_agent_prompts_update_requires_system_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)

    client = web_server.app.test_client()
    res = client.post(
        "/api/agent_prompts",
        data=json.dumps({"name": "researcher"}),
        content_type="application/json",
    )

    assert res.status_code == 400
    assert res.get_json()["error"] == "system_prompt is required"


def test_non_loopback_config_reads_require_auth_even_when_loopback_auth_disabled(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)
    monkeypatch.setattr(config_module.config, "web_api_token", "secret-token")
    monkeypatch.setattr(config_module.config, "web_api_public_read_enabled", False)

    client = web_server.app.test_client()
    res = client.get(
        "/api/control_plane",
        environ_overrides={"REMOTE_ADDR": "198.51.100.20"},
    )

    assert res.status_code == 401
    assert res.get_json()["error"] == "authentication required"


def test_non_loopback_config_updates_require_auth_before_payload_validation(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)
    monkeypatch.setattr(config_module.config, "web_api_token", "secret-token")
    monkeypatch.setattr(config_module.config, "web_api_public_read_enabled", False)

    client = web_server.app.test_client()
    res = client.post(
        "/api/agent_prompts",
        data=json.dumps({"name": "researcher"}),
        content_type="application/json",
        environ_overrides={"REMOTE_ADDR": "198.51.100.20"},
    )

    assert res.status_code == 401
    assert res.get_json()["error"] == "authentication required"


def test_api_control_plane_rejects_non_object_json_body(monkeypatch):
    monkeypatch.setattr(config_module.config, "web_api_require_auth", False)

    client = web_server.app.test_client()
    res = client.post(
        "/api/control_plane",
        data="[]",
        content_type="application/json",
    )

    assert res.status_code == 400
    assert res.get_json()["error"] == "request body must be a JSON object, got list"
