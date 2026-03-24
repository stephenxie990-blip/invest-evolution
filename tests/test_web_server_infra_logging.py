from __future__ import annotations

import logging
from collections import deque

import invest_evolution.config as config_module
import invest_evolution.interfaces.web.server as app_web_server
import pytest


def test_read_runtime_event_rows_logs_truncation_and_invalid_rows(tmp_path, caplog):
    events_path = tmp_path / "runtime_events.jsonl"
    events_path.write_text('{"event":"ok","payload":{"cycle_id": 1}}\n[1,2,3]\n{broken\n', encoding="utf-8")

    with caplog.at_level(logging.DEBUG):
        rows, next_offset = app_web_server._read_runtime_event_rows_since(events_path, 999)

    assert rows == [{"event": "ok", "payload": {"cycle_id": 1}}]
    assert next_offset == events_path.stat().st_size
    assert "Runtime event stream truncated; resetting offset" in caplog.text
    assert "Skipped invalid runtime event row(s) while reading SSE event stream" in caplog.text
    assert "invalid_json_rows=1" in caplog.text
    assert "invalid_payload_rows=1" in caplog.text


def test_parse_detail_mode_logs_default_fallback(caplog):
    with caplog.at_level(logging.DEBUG):
        value = app_web_server._parse_detail_mode("mystery", default="fast", field_name="detail", strict=False)

    assert value == "fast"
    assert "Invalid detail mode; using default: field_name=detail raw_value='mystery' default=fast" in caplog.text


def test_rate_limit_config_clamp_logs_warning(monkeypatch, caplog):
    monkeypatch.setattr(config_module.config, "web_rate_limit_window_sec", 0)
    monkeypatch.setattr(config_module.config, "web_rate_limit_read_max", -1)
    monkeypatch.setattr(config_module.config, "web_rate_limit_write_max", 0)
    monkeypatch.setattr(config_module.config, "web_rate_limit_heavy_max", -5)
    monkeypatch.setattr(config_module.config, "web_rate_limit_max_keys", 0)
    monkeypatch.setattr(config_module.config, "web_event_wait_timeout_sec", 0)
    monkeypatch.setattr(config_module.config, "web_runtime_async_timeout_sec", 0)

    with caplog.at_level(logging.WARNING):
        window = app_web_server._web_rate_limit_window_sec()
        read_max = app_web_server._web_rate_limit_read_max()
        write_max = app_web_server._web_rate_limit_write_max()
        heavy_max = app_web_server._web_rate_limit_heavy_max()
        max_keys = app_web_server._web_rate_limit_max_keys()
        wait_timeout = app_web_server._web_event_wait_timeout_sec()
        async_timeout = app_web_server._web_runtime_async_timeout_sec()

    assert (window, read_max, write_max, heavy_max, max_keys, wait_timeout, async_timeout) == (1, 1, 1, 1, 1, 0.1, 1)
    assert "field=web_rate_limit_window_sec raw_value=0 minimum=1" in caplog.text
    assert "field=web_rate_limit_read_max raw_value=-1 minimum=1" in caplog.text
    assert "field=web_rate_limit_write_max raw_value=0 minimum=1" in caplog.text
    assert "field=web_rate_limit_heavy_max raw_value=-5 minimum=1" in caplog.text
    assert "field=web_rate_limit_max_keys raw_value=0 minimum=1" in caplog.text
    assert "field=web_event_wait_timeout_sec raw_value=0 minimum=0.1" in caplog.text
    assert "field=web_runtime_async_timeout_sec raw_value=0 minimum=1" in caplog.text


def test_config_helpers_share_normalization_semantics(monkeypatch):
    monkeypatch.setattr(config_module.config, "web_api_token", " secret-token ")
    monkeypatch.setattr(config_module.config, "web_api_require_auth", 1)
    monkeypatch.setattr(config_module.config, "web_api_public_read_enabled", "")
    monkeypatch.setattr(config_module.config, "web_rate_limit_enabled", 0)

    assert app_web_server._web_api_token() == "secret-token"
    assert app_web_server._web_api_require_auth() is True
    assert app_web_server._web_api_public_read_enabled() is False
    assert app_web_server._web_rate_limit_enabled() is False


def test_reset_ephemeral_web_state_clears_shared_runtime_counters():
    state = app_web_server.get_ephemeral_web_state()
    state.rate_limit_events = {("127.0.0.1", "GET", "/api/status"): deque([1.0])}
    state.event_history.append({"id": 9, "type": "status", "data": {"state": "idle"}})
    state.event_seq = 9

    app_web_server.reset_ephemeral_web_state()

    assert state.rate_limit_events == {}
    assert list(state.event_history) == []
    assert state.event_seq == 0


def test_env_helpers_keep_strict_boolean_parsing(monkeypatch):
    monkeypatch.setenv("WEB_EMBEDDED_RUNTIME_ENABLED", "maybe")

    with pytest.raises(RuntimeError, match="WEB_EMBEDDED_RUNTIME_ENABLED must be a boolean when configured."):
        app_web_server._embedded_runtime_enabled()


def test_env_helpers_parse_host_and_worker_defaults(monkeypatch):
    monkeypatch.delenv("GUNICORN_BIND", raising=False)
    monkeypatch.delenv("GUNICORN_WORKERS", raising=False)

    assert app_web_server._configured_gunicorn_host() == "127.0.0.1"
    assert app_web_server._configured_gunicorn_workers() == 2


def test_run_async_wraps_timeout_with_runtime_bridge_context(monkeypatch):
    cancelled: list[bool] = []

    class FakeFuture:
        @staticmethod
        def result(timeout):
            del timeout
            raise TimeoutError("slow runtime")

        @staticmethod
        def cancel():
            cancelled.append(True)

    async def _noop():
        return None

    monkeypatch.setattr(app_web_server, "_loop", object())
    monkeypatch.setattr(config_module.config, "web_runtime_async_timeout_sec", 7)
    monkeypatch.setattr(
        app_web_server.asyncio,
        "run_coroutine_threadsafe",
        lambda coro, loop: (coro.close(), loop, FakeFuture())[2],
    )

    with pytest.raises(RuntimeError, match="Commander runtime async bridge timed out after 7 seconds"):
        app_web_server._run_async(_noop())

    assert cancelled == [True]
