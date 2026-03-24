from __future__ import annotations

import logging

import pytest

from invest_evolution.application.commander.runtime import load_persisted_runtime_state, start_runtime_flow


def test_load_persisted_runtime_state_logs_invalid_json(tmp_path, caplog):
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        payload = load_persisted_runtime_state(path, logger=logging.getLogger("test"))

    assert payload is None
    assert "Failed to restore persisted commander state" in caplog.text


def test_load_persisted_runtime_state_rejects_non_object_payload(tmp_path, caplog):
    path = tmp_path / "state.json"
    path.write_text("[]", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        payload = load_persisted_runtime_state(path, logger=logging.getLogger("test"))

    assert payload is None
    assert "Persisted commander state must be a JSON object" in caplog.text


@pytest.mark.asyncio
async def test_start_runtime_flow_logs_failure_and_rolls_back(caplog):
    events: list[tuple[str, object]] = []

    async def fail_start_background_services():
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="boom"):
            await start_runtime_flow(
                is_started=False,
                logger=logging.getLogger("test"),
                ensure_runtime_storage=lambda: events.append(("ensure_runtime_storage", None)),
                begin_task=lambda task, source: events.append(("begin_task", (task, source))),
                set_runtime_state=lambda state: events.append(("set_runtime_state", state)),
                acquire_runtime_lock=lambda: events.append(("acquire_runtime_lock", None)),
                ensure_default_playbooks=lambda: events.append(("ensure_default_playbooks", None)),
                reload_playbooks=lambda: events.append(("reload_playbooks", None)),
                load_plugins=lambda **kwargs: events.append(("load_plugins", kwargs)) or {},
                write_commander_identity=lambda: events.append(("write_commander_identity", None)),
                start_background_services=fail_start_background_services,
                mark_started=lambda value: events.append(("mark_started", value)),
                set_background_tasks=lambda notify, autopilot: events.append(
                    ("set_background_tasks", (notify, autopilot))
                ),
                complete_runtime_task=lambda **kwargs: events.append(("complete_runtime_task", kwargs)),
                end_task=lambda status: events.append(("end_task", status)),
                release_runtime_lock=lambda: events.append(("release_runtime_lock", None)),
                persist_state=lambda: events.append(("persist_state", None)),
                starting_state="starting",
                idle_state="idle",
                error_state="error",
                ok_status="ok",
            )

    assert ("set_runtime_state", "starting") in events
    assert ("set_runtime_state", "error") in events
    assert ("end_task", "error") in events
    assert ("release_runtime_lock", None) in events
    assert ("persist_state", None) in events
    assert "Commander runtime start failed during bootstrap sequence" in caplog.text
