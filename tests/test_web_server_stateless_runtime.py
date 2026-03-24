import importlib
import json

import invest_evolution.config as config_module
import pytest
from invest_evolution.application.commander.status import append_event_row


def _make_state_backed_runtime(tmp_path):
    runtime_root = tmp_path / "runtime"
    state_dir = runtime_root / "state"
    state_file = runtime_root / "outputs" / "commander" / "state.json"
    events_path = state_dir / "commander_events.jsonl"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "ts": "2026-03-20T00:00:00",
                "detail_mode": "fast",
                "instance_id": "test-instance",
                "workspace": str(runtime_root / "workspace"),
                "playbook_dir": str(tmp_path / "strategies"),
                "model": "",
                "autopilot_enabled": False,
                "heartbeat_enabled": False,
                "training_interval_sec": 0,
                "heartbeat_interval_sec": 0,
                "runtime": {
                    "state": "stopped",
                    "started": False,
                    "current_task": None,
                    "last_task": None,
                },
                "brain": {"tool_count": 0, "session_count": 0, "cron": {}, "governance_metrics": {}},
                "body": {
                    "total_cycles": 0,
                    "success_cycles": 0,
                    "no_data_cycles": 0,
                    "failed_cycles": 0,
                    "last_result": None,
                    "last_error": "",
                    "last_run_at": "",
                    "training_state": "idle",
                    "current_task": None,
                    "last_completed_task": None,
                },
                "memory": {},
                "bridge": {},
                "plugins": {"count": 0, "items": []},
                "playbooks": {"total": 0, "enabled": 0, "items": []},
                "config": {},
                "data": {},
                "events": {"count": 0, "counts": {}, "latest": None, "window_start": "", "window_end": ""},
                "training_lab": {
                    "plan_count": 0,
                    "run_count": 0,
                    "evaluation_count": 0,
                    "latest_plans": [],
                    "latest_runs": [],
                    "latest_evaluations": [],
                    "latest_run_summary": {},
                    "latest_evaluation_summary": {},
                    "governance_summary": {},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return events_path


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _load_web_server_or_skip():
    try:
        return importlib.import_module("invest_evolution.interfaces.web.server")
    except ModuleNotFoundError as exc:
        if exc.name == "pandas":
            pytest.skip("WS1 env recovery blocker: web_server import chain still requires pandas")
        raise


def test_events_summary_uses_state_backed_runtime_without_inprocess_runtime(tmp_path, monkeypatch):
    web_server = _load_web_server_or_skip()
    events_path = _make_state_backed_runtime(tmp_path)
    append_event_row(
        events_path,
        "cycle_complete",
        {"cycle_id": 3, "return_pct": 1.2},
        source="runtime",
    )
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web_server, "_runtime", None)
    monkeypatch.setattr(web_server, "_loop", None)

    client = web_server.app.test_client()
    res = client.get("/api/events/summary")

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["summary"]["count"] == 1
    assert payload["items"][0]["event"] == "cycle_complete"


def test_api_events_stream_reads_runtime_event_log_without_inprocess_runtime(tmp_path, monkeypatch):
    web_server = _load_web_server_or_skip()
    events_path = _make_state_backed_runtime(tmp_path)
    append_event_row(
        events_path,
        "cycle_complete",
        {"cycle_id": 9, "return_pct": 2.5},
        source="runtime",
    )
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web_server, "_runtime", None)
    monkeypatch.setattr(web_server, "_loop", None)

    client = web_server.app.test_client()
    res = client.get("/api/events", buffered=False)

    chunks = []
    for chunk in res.response:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
        if len(chunks) >= 2:
            break

    joined = "".join(chunks)
    assert res.status_code == 200
    assert "event: connected" in joined
    assert "event: cycle_complete" in joined
    assert json.dumps({"cycle_id": 9, "return_pct": 2.5}, ensure_ascii=False) in joined


def test_training_lab_routes_use_state_backed_runtime_artifacts(tmp_path, monkeypatch):
    web_server = _load_web_server_or_skip()
    _make_state_backed_runtime(tmp_path)
    runtime_state = tmp_path / "runtime" / "state"
    _write_json(
        runtime_state / "training_plans" / "plan_demo.json",
        {
            "plan_id": "plan_demo",
            "status": "ready",
            "created_at": "2026-03-20T12:00:00",
            "spec": {"cycles": 5},
        },
    )
    _write_json(
        runtime_state / "training_runs" / "run_demo.json",
        {
            "run_id": "run_demo",
            "status": "completed",
            "created_at": "2026-03-20T12:10:00",
            "payload": {
                "results": [
                    {
                        "cycle_id": 5,
                        "status": "success",
                        "return_pct": 3.2,
                        "benchmark_passed": True,
                    }
                ]
            },
        },
    )
    _write_json(
        runtime_state / "training_evals" / "run_demo.json",
        {
            "run_id": "run_demo",
            "status": "completed",
            "assessment": {
                "success_count": 5,
                "avg_return_pct": 1.8,
            },
            "promotion": {
                "verdict": "promote",
                "passed": True,
            },
        },
    )
    _write_json(
        tmp_path / "runtime" / "outputs" / "leaderboard.json",
        {
            "generated_at": "2026-03-20T12:30:00",
            "total_managers": 2,
            "eligible_managers": 1,
            "entries": [{"manager_id": "momentum", "score": 0.92}],
        },
    )

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web_server, "_runtime", None)
    monkeypatch.setattr(web_server, "_loop", None)

    client = web_server.app.test_client()

    plans_res = client.get("/api/lab/training/plans")
    runs_res = client.get("/api/lab/training/runs")
    evals_res = client.get("/api/lab/training/evaluations")
    plan_detail_res = client.get("/api/lab/training/plans/plan_demo")
    leaderboard_res = client.get("/api/leaderboard")

    assert plans_res.status_code == 200
    assert plans_res.get_json()["count"] == 1
    assert plans_res.get_json()["items"][0]["plan_id"] == "plan_demo"

    assert runs_res.status_code == 200
    assert runs_res.get_json()["items"][0]["run_id"] == "run_demo"
    assert runs_res.get_json()["items"][0]["latest_result"]["cycle_id"] == 5

    assert evals_res.status_code == 200
    assert evals_res.get_json()["items"][0]["promotion"]["verdict"] == "promote"

    assert plan_detail_res.status_code == 200
    assert plan_detail_res.get_json()["spec"]["cycles"] == 5

    assert leaderboard_res.status_code == 404


def test_training_lab_detail_route_returns_404_when_state_backed_artifact_missing(tmp_path, monkeypatch):
    web_server = _load_web_server_or_skip()
    _make_state_backed_runtime(tmp_path)
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web_server, "_runtime", None)
    monkeypatch.setattr(web_server, "_loop", None)

    client = web_server.app.test_client()
    res = client.get("/api/lab/training/runs/missing_run")

    assert res.status_code == 404
    assert "training run not found" in res.get_json()["error"]


def test_state_backed_retired_leaderboard_route_returns_404_without_creating_snapshot(tmp_path, monkeypatch):
    web_server = _load_web_server_or_skip()
    _make_state_backed_runtime(tmp_path)
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web_server, "_runtime", None)
    monkeypatch.setattr(web_server, "_loop", None)
    leaderboard_path = tmp_path / "runtime" / "outputs" / "leaderboard.json"

    client = web_server.app.test_client()
    res = client.get("/api/leaderboard")

    assert res.status_code == 404
    assert not leaderboard_path.exists()
