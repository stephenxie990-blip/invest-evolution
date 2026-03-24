from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import invest_evolution.interfaces.web.runtime as runtime_facade_module
import invest_evolution.application.commander.workflow as workflow_module
from invest_evolution.application.commander.workflow import execute_runtime_ask
from invest_evolution.application.commander.status import build_training_memory_entry
from invest_evolution.application.commander.workflow import execute_training_plan_flow
from invest_evolution.interfaces.web.runtime import StateBackedRuntimeFacade


@pytest.mark.asyncio
async def test_execute_runtime_ask_logs_failure_context(caplog):
    records: list[tuple[str, object]] = []

    async def fail_process_direct(message: str, *, session_key: str):
        del message, session_key
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="boom"):
            await execute_runtime_ask(
                message="hello world",
                session_key="api:chat:test",
                channel="api",
                chat_id="chat:test",
                request_id="req:test",
                ensure_runtime_storage=lambda: records.append(("ensure_runtime_storage", None)),
                begin_task=lambda *args, **kwargs: records.append(("begin_task", (args, kwargs))),
                memory=type(
                    "Memory",
                    (),
                    {
                        "append": lambda *args, **kwargs: records.append(("append", (args, kwargs))),
                        "append_audit": lambda *args, **kwargs: records.append(("append_audit", (args, kwargs))),
                    },
                )(),
                record_ask_activity=lambda *args, **kwargs: records.append(("record_ask_activity", (args, kwargs))),
                process_direct=fail_process_direct,
                complete_runtime_task=lambda **kwargs: records.append(("complete_runtime_task", kwargs)),
                status_ok="ok",
                status_error="error",
                event_ask_started="ask_started",
                event_ask_finished="ask_finished",
            )

    assert ("complete_runtime_task", {"status": "error"}) in records
    assert "Runtime ask failed: session_key=api:chat:test channel=api chat_id=chat:test request_id=req:test message_length=11" in caplog.text


@pytest.mark.asyncio
async def test_execute_training_plan_flow_logs_failure_context(caplog):
    class Body:
        async def run_cycles(self, **kwargs):
            raise RuntimeError("boom")

        @staticmethod
        def _extract_data_source_error(payload):
            del payload
            return None

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="boom"):
            await execute_training_plan_flow(
                plan_path=Path("/tmp/plan.json"),
                plan={"source": "api"},
                experiment_spec={},
                rounds=3,
                mock=True,
                plan_id="plan_demo",
                body=Body(),
                body_snapshot=lambda: {"summary": "snapshot"},
                build_run_cycles_kwargs=lambda **kwargs: {},
                write_json_artifact=lambda *args, **kwargs: None,
                begin_task=lambda *args, **kwargs: None,
                set_runtime_state=lambda state: None,
                memory=type("Memory", (), {"append_audit": lambda *args, **kwargs: None})(),
                record_training_lab_artifacts_impl=lambda **kwargs: {"plan": {}, "run": {"payload": {}}, "evaluation": {"artifacts": {}, "run_id": "run_1"}},
                attach_training_lab_paths_impl=lambda payload, lab: None,
                append_training_memory_impl=lambda payload, **kwargs: None,
                complete_runtime_task=lambda **kwargs: None,
                wrap_training_execution_payload=lambda payload, **kwargs: payload,
                build_training_memory_entry=build_training_memory_entry,
                ok_status="ok",
                busy_state="busy",
                idle_state="idle",
                training_state="training",
                error_state="error",
            )

    assert "Training plan execution failed: plan_id=plan_demo rounds=3 mock=True" in caplog.text


def test_state_backed_runtime_facade_read_runtime_paths_logs_boundary_failure(
    monkeypatch, tmp_path, caplog
):
    facade = StateBackedRuntimeFacade(
        project_root_getter=lambda: tmp_path,
        state_file_getter=lambda: tmp_path / "runtime" / "state" / "state.json",
        runtime_lock_file_getter=lambda: tmp_path / "runtime" / "state" / "commander.lock",
        training_lock_file_getter=lambda: tmp_path / "runtime" / "state" / "training.lock",
        runtime_events_path_getter=lambda: tmp_path / "runtime" / "state" / "events.jsonl",
        data_status_getter=lambda _detail: {},
        config_payload_getter=lambda: {},
    )

    monkeypatch.setattr(
        runtime_facade_module,
        "get_runtime_paths_payload",
        lambda *, project_root, runtime=None: (_ for _ in ()).throw(
            ValueError("bad runtime paths")
        ),
    )

    with caplog.at_level(logging.ERROR):
        payload = facade._read_runtime_paths_payload()

    assert payload == {}
    assert "Failed to resolve runtime path payload" in caplog.text


@pytest.mark.asyncio
async def test_workflow_start_stop_runtime_use_runtime_module_dispatch(monkeypatch):
    calls: list[str] = []

    async def start_runtime_flow(**kwargs):
        calls.append("start_runtime_flow")
        await kwargs["start_background_services"]()

    async def start_runtime_background_services(**kwargs):
        del kwargs
        calls.append("start_runtime_background_services")

    async def stop_runtime_flow(**kwargs):
        calls.append("stop_runtime_flow")
        await kwargs["stop_background_services"]()

    async def stop_runtime_background_services(**kwargs):
        del kwargs
        calls.append("stop_runtime_background_services")

    workflow_module._commander_runtime_module.cache_clear()
    monkeypatch.setattr(
        workflow_module,
        "_commander_runtime_module",
        lambda: SimpleNamespace(
            start_runtime_flow=start_runtime_flow,
            start_runtime_background_services=start_runtime_background_services,
            stop_runtime_flow=stop_runtime_flow,
            stop_runtime_background_services=stop_runtime_background_services,
            serve_forever_loop=None,
        ),
    )

    runtime = SimpleNamespace(
        _started=False,
        _ensure_runtime_storage=lambda: None,
        _begin_task=lambda *args, **kwargs: None,
        _set_runtime_state=lambda *args, **kwargs: None,
        _acquire_runtime_lock=lambda: None,
        playbook_registry=SimpleNamespace(
            ensure_default_playbooks=lambda: None,
            reload=lambda: None,
        ),
        _load_plugins=lambda: None,
        _write_commander_identity=lambda: None,
        cron=object(),
        heartbeat=object(),
        bridge=object(),
        cfg=SimpleNamespace(
            heartbeat_enabled=False,
            bridge_enabled=False,
            autopilot_enabled=False,
            training_interval_sec=60,
        ),
        body=SimpleNamespace(autopilot_loop=object()),
        _drain_notifications=lambda: None,
        _set_started_flag=lambda *args, **kwargs: None,
        _set_background_tasks=lambda *args, **kwargs: None,
        _complete_runtime_task=lambda **kwargs: None,
        _end_task=lambda: None,
        _release_runtime_lock=lambda: None,
        _persist_state=lambda: None,
        _autopilot_task=None,
        _notify_task=None,
        brain=object(),
    )

    await workflow_module.start_runtime(runtime)
    await workflow_module.stop_runtime(runtime)

    assert calls == [
        "start_runtime_flow",
        "start_runtime_background_services",
        "stop_runtime_flow",
        "stop_runtime_background_services",
    ]


@pytest.mark.asyncio
async def test_workflow_serve_forever_uses_runtime_module_dispatch(monkeypatch):
    calls: list[tuple[str, object]] = []

    async def serve_forever_loop(**kwargs):
        calls.append(("serve_forever_loop", kwargs["interactive"]))
        await kwargs["start_runtime"]()
        await kwargs["ask_runtime"]("hello", session_key="cli:test")

    workflow_module._commander_runtime_module.cache_clear()
    monkeypatch.setattr(
        workflow_module,
        "_commander_runtime_module",
        lambda: SimpleNamespace(serve_forever_loop=serve_forever_loop),
    )

    async def _start():
        calls.append(("start", True))

    async def _ask(message: str, *, session_key: str):
        calls.append(("ask", (message, session_key)))
        return "ok"

    runtime = SimpleNamespace(start=_start, ask=_ask)

    await workflow_module.serve_forever(runtime, interactive=True)

    assert calls == [
        ("serve_forever_loop", True),
        ("start", True),
        ("ask", ("hello", "cli:test")),
    ]


def test_state_backed_runtime_facade_leaderboard_logs_boundary_failure(tmp_path, caplog):
    runtime_root = tmp_path / "runtime"
    leaderboard_path = runtime_root / "outputs" / "leaderboard.json"
    leaderboard_path.parent.mkdir(parents=True, exist_ok=True)
    leaderboard_path.write_text("{broken", encoding="utf-8")

    facade = StateBackedRuntimeFacade(
        project_root_getter=lambda: tmp_path,
        state_file_getter=lambda: runtime_root / "state" / "state.json",
        runtime_lock_file_getter=lambda: runtime_root / "state" / "commander.lock",
        training_lock_file_getter=lambda: runtime_root / "state" / "training.lock",
        runtime_events_path_getter=lambda: runtime_root / "state" / "events.jsonl",
        data_status_getter=lambda _detail: {},
        config_payload_getter=lambda: {},
    )

    with caplog.at_level(logging.ERROR):
        payload = facade.leaderboard_snapshot()

    assert payload == {"generated_at": "", "total_managers": 0, "eligible_managers": 0, "entries": []}
    assert "Failed to read leaderboard snapshot" in caplog.text
