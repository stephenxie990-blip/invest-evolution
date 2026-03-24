from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

import invest_evolution.config as config_module
from invest_evolution.config.control_plane import clear_control_plane_cache
from invest_evolution.interfaces.web.runtime import StateBackedRuntimeFacade


def _load_web_server_or_skip():
    try:
        return importlib.import_module("invest_evolution.interfaces.web.server")
    except ModuleNotFoundError as exc:
        if exc.name == "pandas":
            pytest.skip("deploy smoke requires the managed web server import chain")
        raise


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _seed_state_backed_runtime(project_root: Path) -> None:
    runtime_root = project_root / "runtime"
    state_dir = runtime_root / "state"
    outputs_dir = runtime_root / "outputs" / "commander"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "commander_events.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (state_dir / "commander_events.jsonl").write_text("", encoding="utf-8")

    _write_json(
        outputs_dir / "state.json",
        {
            "ts": "2026-03-23T00:00:00",
            "instance_id": "deploy-smoke",
            "workspace": str(runtime_root / "workspace"),
            "playbook_dir": str(project_root / "strategies"),
            "runtime": {"state": "stopped", "started": False},
            "body": {"training_state": "idle"},
            "brain": {"tool_count": 0, "session_count": 0, "governance_metrics": {}},
            "training_lab": {
                "plan_count": 1,
                "run_count": 1,
                "evaluation_count": 1,
                "latest_plans": [{"plan_id": "plan_demo"}],
                "latest_runs": [{"run_id": "run_demo"}],
                "latest_evaluations": [{"run_id": "run_demo"}],
            },
        },
    )
    _write_json(
        state_dir / "training_plans" / "plan_demo.json",
        {
            "plan_id": "plan_demo",
            "status": "ready",
            "created_at": "2026-03-23T09:00:00",
            "spec": {"rounds": 3},
        },
    )
    _write_json(
        state_dir / "training_runs" / "run_demo.json",
        {
            "run_id": "run_demo",
            "status": "completed",
            "created_at": "2026-03-23T09:10:00",
            "payload": {
                "results": [
                    {
                        "cycle_id": 1,
                        "status": "success",
                        "return_pct": 1.5,
                        "benchmark_passed": True,
                    }
                ]
            },
        },
    )
    _write_json(
        state_dir / "training_evals" / "run_demo.json",
        {
            "run_id": "run_demo",
            "status": "completed",
            "assessment": {"success_count": 1, "avg_return_pct": 1.5},
            "promotion": {"verdict": "hold", "passed": False},
        },
    )


@pytest.fixture
def stateless_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    web_server = _load_web_server_or_skip()
    routes_module = importlib.import_module("invest_evolution.interfaces.web.routes")
    _seed_state_backed_runtime(tmp_path)
    facade = StateBackedRuntimeFacade(
        project_root_getter=lambda: tmp_path,
        state_file_getter=lambda: tmp_path / "runtime" / "outputs" / "commander" / "state.json",
        runtime_lock_file_getter=lambda: tmp_path / "runtime" / "state" / "commander.lock",
        training_lock_file_getter=lambda: tmp_path / "runtime" / "state" / "training.lock",
        runtime_events_path_getter=lambda: tmp_path / "runtime" / "state" / "commander_events.jsonl",
        data_status_getter=lambda detail_mode: {
            "status": "ok",
            "detail_mode": detail_mode,
            "source": "deploy-smoke",
        },
        config_payload_getter=lambda: {},
    )
    clear_control_plane_cache()
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web_server, "_runtime", None)
    monkeypatch.setattr(web_server, "_loop", None)
    monkeypatch.setattr(web_server, "_runtime_facade", facade)
    monkeypatch.setattr(
        routes_module,
        "get_data_status_payload",
        lambda *, refresh=False: {
            "status": "ok",
            "refresh": refresh,
            "source": "deploy-smoke",
        },
    )
    web_server.reset_ephemeral_web_state()
    try:
        yield web_server.app.test_client()
    finally:
        clear_control_plane_cache()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/status"),
        ("GET", "/api/events/summary"),
        ("GET", "/api/lab/training/plans"),
        ("GET", "/api/lab/training/plans/plan_demo"),
        ("GET", "/api/lab/training/runs"),
        ("GET", "/api/lab/training/runs/run_demo"),
        ("GET", "/api/lab/training/evaluations"),
        ("GET", "/api/lab/training/evaluations/run_demo"),
        ("GET", "/api/runtime_paths"),
        ("GET", "/api/evolution_config"),
        ("GET", "/api/control_plane"),
        ("GET", "/api/agent_prompts"),
        ("GET", "/api/data/status"),
        ("GET", "/api/contracts/runtime-v2"),
        ("GET", "/api/contracts/runtime-v2/schema"),
        ("GET", "/api/contracts/runtime-v2/openapi"),
    ],
)
def test_state_backed_deploy_public_surface_returns_200(
    stateless_client,
    method: str,
    path: str,
) -> None:
    response = getattr(stateless_client, method.lower())(path)

    assert response.status_code == 200, f"{method} {path} should stay deploy-safe"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/train"),
        ("GET", "/api/leaderboard"),
        ("GET", "/api/allocator"),
        ("GET", "/api/governance/preview"),
        ("GET", "/api/managers"),
        ("GET", "/api/playbooks"),
        ("GET", "/api/lab/status/quick"),
        ("GET", "/api/lab/status/deep"),
        ("POST", "/api/playbooks/reload"),
        ("GET", "/api/cron"),
        ("POST", "/api/cron/job-demo"),
        ("GET", "/api/memory"),
        ("GET", "/api/memory/record-demo"),
        ("GET", "/api/data/capital_flow"),
        ("GET", "/api/data/dragon_tiger"),
        ("GET", "/api/data/intraday_60m"),
        ("GET", "/api/contracts"),
    ],
)
def test_retired_public_surface_returns_404(
    stateless_client,
    method: str,
    path: str,
) -> None:
    response = getattr(stateless_client, method.lower())(path)

    assert response.status_code == 404, f"{method} {path} should remain retired"
