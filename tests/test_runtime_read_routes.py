import json
from pathlib import Path

from flask import Flask, jsonify, request

from invest_evolution.interfaces.web.routes import register_runtime_read_routes
from invest_evolution.interfaces.web.runtime import StateBackedRuntimeFacade


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _build_state_backed_app(project_root: Path) -> Flask:
    app = Flask(__name__)
    runtime_root = project_root / "runtime"
    state_dir = runtime_root / "state"

    facade = StateBackedRuntimeFacade(
        project_root_getter=lambda: project_root,
        state_file_getter=lambda: runtime_root / "outputs" / "commander" / "state.json",
        runtime_lock_file_getter=lambda: state_dir / "commander.lock",
        training_lock_file_getter=lambda: state_dir / "training.lock",
        runtime_events_path_getter=lambda: state_dir / "commander_events.jsonl",
        data_status_getter=lambda _detail: {},
        config_payload_getter=lambda: {},
    )

    def _parse_detail_mode(
        value: str,
        *,
        default: str = "fast",
        field_name: str = "detail",
        strict: bool = False,
    ) -> str:
        detail_mode = str(value or default).strip().lower() or default
        if detail_mode in {"fast", "slow"}:
            return detail_mode
        if strict:
            raise ValueError(f"{field_name} must be one of: fast, slow")
        return default

    def _status_response(*, detail_mode: str, route_mode: str | None = None):
        del route_mode
        return jsonify(
            facade.status_snapshot(
                detail_mode=detail_mode,
                runtime_not_ready_response=lambda: (jsonify({"error": "runtime"}), 503),
            )
        )

    def _request_view_arg(*, default: str = "json") -> str:
        return str(request.args.get("view", default) or default)

    def _parse_limit_arg(*, default: int = 20, maximum: int = 200) -> int:
        raw = int(request.args.get("limit", default))
        return max(1, min(maximum, raw))

    def _respond_with_display(payload, *, status_code: int = 200, view: str = "json"):
        del view
        response = jsonify(payload)
        response.status_code = status_code
        return response

    register_runtime_read_routes(
        app,
        runtime_facade=facade,
        parse_detail_mode=_parse_detail_mode,
        status_response=_status_response,
        runtime_not_ready_response=lambda: (jsonify({"error": "runtime"}), 503),
        request_view_arg=_request_view_arg,
        parse_limit_arg=_parse_limit_arg,
        respond_with_display=_respond_with_display,
    )
    return app


def test_state_backed_training_lab_routes_return_artifacts(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    state_dir = runtime_root / "state"
    _write_json(
        state_dir / "training_plans" / "plan_demo.json",
        {
            "plan_id": "plan_demo",
            "status": "ready",
            "created_at": "2026-03-20T12:00:00",
        },
    )
    _write_json(
        state_dir / "training_runs" / "run_demo.json",
        {
            "run_id": "run_demo",
            "status": "completed",
            "payload": {
                "results": [
                    {
                        "cycle_id": 7,
                        "status": "success",
                        "return_pct": 2.4,
                        "benchmark_passed": True,
                        "artifacts": {
                            "cycle_result_path": str(
                                runtime_root / "outputs" / "training" / "cycle_7.json"
                            ),
                            "selection_artifact_json_path": str(
                                runtime_root
                                / "logs"
                                / "artifacts"
                                / "selection"
                                / "artifact_7.json"
                            ),
                        },
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
            "assessment": {"success_count": 7},
            "promotion": {"verdict": "promote", "passed": True},
        },
    )
    _write_json(
        runtime_root / "outputs" / "leaderboard.json",
        {
            "generated_at": "2026-03-20T12:30:00",
            "total_managers": 3,
            "eligible_managers": 2,
            "entries": [{"manager_id": "momentum", "score": 0.91}],
        },
    )

    app = _build_state_backed_app(tmp_path)
    client = app.test_client()

    plans = client.get("/api/lab/training/plans")
    runs = client.get("/api/lab/training/runs")
    evaluations = client.get("/api/lab/training/evaluations")
    leaderboard = client.get("/api/leaderboard")

    assert plans.status_code == 200
    assert plans.get_json()["items"][0]["plan_id"] == "plan_demo"

    assert runs.status_code == 200
    assert runs.get_json()["items"][0]["latest_result"]["cycle_id"] == 7
    assert runs.get_json()["items"][0]["latest_result"]["core_artifacts"][
        "cycle_result_path"
    ].endswith("cycle_7.json")
    assert runs.get_json()["items"][0]["latest_result"]["core_artifacts"][
        "selection_artifact_json_path"
    ].endswith("artifact_7.json")

    assert evaluations.status_code == 200
    assert evaluations.get_json()["items"][0]["promotion"]["verdict"] == "promote"

    assert leaderboard.status_code == 404


def test_state_backed_training_lab_detail_returns_404_for_missing_artifact(
    tmp_path: Path,
) -> None:
    app = _build_state_backed_app(tmp_path)
    client = app.test_client()

    response = client.get("/api/lab/training/evaluations/missing_run")

    assert response.status_code == 404
    assert "training evaluation not found" in response.get_json()["error"]


def test_state_backed_status_route_rejects_invalid_detail_query(tmp_path: Path) -> None:
    app = _build_state_backed_app(tmp_path)
    client = app.test_client()

    response = client.get("/api/status?detail=invalid")

    assert response.status_code == 400
    assert response.get_json()["error"] == "detail must be one of: fast, slow"


def test_state_backed_retired_leaderboard_route_returns_404_without_creating_snapshot(
    tmp_path: Path,
) -> None:
    app = _build_state_backed_app(tmp_path)
    client = app.test_client()
    leaderboard_path = tmp_path / "runtime" / "outputs" / "leaderboard.json"

    response = client.get("/api/leaderboard")

    assert response.status_code == 404
    assert not leaderboard_path.exists()


def test_state_backed_status_snapshot_backfills_partial_persisted_sections(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    state_file = runtime_root / "outputs" / "commander" / "state.json"
    _write_json(
        state_file,
        {
            "instance_id": "partial-runtime",
            "runtime": {"state": "idle"},
            "body": {"training_state": "busy"},
            "brain": {"tool_count": 3},
        },
    )
    facade = StateBackedRuntimeFacade(
        project_root_getter=lambda: tmp_path,
        state_file_getter=lambda: state_file,
        runtime_lock_file_getter=lambda: runtime_root / "state" / "commander.lock",
        training_lock_file_getter=lambda: runtime_root / "state" / "training.lock",
        runtime_events_path_getter=lambda: (
            runtime_root / "state" / "commander_events.jsonl"
        ),
        data_status_getter=lambda _detail: {},
        config_payload_getter=lambda: {},
    )

    payload = facade.status_snapshot(
        detail_mode="fast",
        runtime_not_ready_response=lambda: None,
    )

    assert payload["instance_id"] == "partial-runtime"
    assert payload["workspace"] == str(tmp_path / "runtime" / "workspace")
    assert payload["playbook_dir"] == str(tmp_path / "strategies")
    assert payload["runtime"]["state"] == "idle"
    assert payload["runtime"]["state_source"] == "runtime_state"
    assert payload["runtime"]["runtime_lock_active"] is False
    assert payload["body"]["training_state"] == "busy"
    assert payload["body"]["total_cycles"] == 0
    assert payload["brain"]["tool_count"] == 3
    assert payload["brain"]["governance_metrics"] == {}
    assert payload["training_lab"]["latest_runs"] == []


def test_state_backed_status_snapshot_prefers_canonical_surface_sections(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    state_file = runtime_root / "outputs" / "commander" / "state.json"
    _write_json(
        state_file,
        {
            "instance_id": "persisted-runtime",
            "config": {
                "config_path": "stale-config.yaml",
                "web_rate_limit_enabled": False,
            },
            "runtime_paths": {"training_output_dir": "stale-training-output"},
            "training_lab": {
                "plan_count": 99,
                "run_count": 88,
                "evaluation_count": 77,
                "latest_plans": [{"plan_id": "stale_plan"}],
            },
        },
    )
    _write_json(
        runtime_root / "state" / "runtime_paths.json",
        {
            "training_output_dir": str(runtime_root / "outputs" / "custom_training"),
        },
    )
    _write_json(
        runtime_root / "state" / "training_plans" / "plan_live.json",
        {
            "plan_id": "plan_live",
            "status": "ready",
        },
    )
    facade = StateBackedRuntimeFacade(
        project_root_getter=lambda: tmp_path,
        state_file_getter=lambda: state_file,
        runtime_lock_file_getter=lambda: runtime_root / "state" / "commander.lock",
        training_lock_file_getter=lambda: runtime_root / "state" / "training.lock",
        runtime_events_path_getter=lambda: (
            runtime_root / "state" / "commander_events.jsonl"
        ),
        data_status_getter=lambda _detail: {},
        config_payload_getter=lambda: {
            "config_path": "live-config.yaml",
            "web_rate_limit_enabled": True,
        },
    )

    payload = facade.status_snapshot(
        detail_mode="fast",
        runtime_not_ready_response=lambda: None,
    )

    assert payload["config"]["config_path"] == "live-config.yaml"
    assert payload["config"]["web_rate_limit_enabled"] is True
    assert payload["runtime_paths"]["training_output_dir"] == str(
        runtime_root / "outputs" / "custom_training"
    )
    assert "artifact_log_dir" in payload["runtime_paths"]
    assert "config_path" not in payload["runtime_paths"]
    assert "config_audit_log_path" not in payload["runtime_paths"]
    assert "config_snapshot_dir" not in payload["runtime_paths"]
    assert payload["training_lab"]["plan_count"] == 1
    assert payload["training_lab"]["run_count"] == 0
    assert payload["training_lab"]["evaluation_count"] == 0
    assert payload["training_lab"]["latest_plans"][0]["plan_id"] == "plan_live"


def test_state_backed_status_snapshot_respects_configured_training_lab_limit(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    state_file = runtime_root / "outputs" / "commander" / "state.json"
    for index in range(5):
        _write_json(
            runtime_root / "state" / "training_plans" / f"plan_{index}.json",
            {
                "plan_id": f"plan_{index}",
                "status": "ready",
            },
        )
    facade = StateBackedRuntimeFacade(
        project_root_getter=lambda: tmp_path,
        state_file_getter=lambda: state_file,
        runtime_lock_file_getter=lambda: runtime_root / "state" / "commander.lock",
        training_lock_file_getter=lambda: runtime_root / "state" / "training.lock",
        runtime_events_path_getter=lambda: (
            runtime_root / "state" / "commander_events.jsonl"
        ),
        data_status_getter=lambda _detail: {},
        config_payload_getter=lambda: {"web_status_training_lab_limit": 2},
    )

    payload = facade.status_snapshot(
        detail_mode="fast",
        runtime_not_ready_response=lambda: None,
    )

    assert payload["training_lab"]["plan_count"] == 5
    assert len(payload["training_lab"]["latest_plans"]) == 2
    assert payload["config"]["web_status_training_lab_limit"] == 2


def test_state_backed_status_snapshot_respects_configured_events_summary_limit(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    state_file = runtime_root / "outputs" / "commander" / "state.json"
    events_path = runtime_root / "state" / "commander_events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        "\n".join(
            [
                json.dumps({"event": "runtime_start", "ts": "2026-03-22T10:00:00"}),
                json.dumps({"event": "cycle_completed", "ts": "2026-03-22T10:01:00"}),
                json.dumps({"event": "cycle_completed", "ts": "2026-03-22T10:02:00"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    facade = StateBackedRuntimeFacade(
        project_root_getter=lambda: tmp_path,
        state_file_getter=lambda: state_file,
        runtime_lock_file_getter=lambda: runtime_root / "state" / "commander.lock",
        training_lock_file_getter=lambda: runtime_root / "state" / "training.lock",
        runtime_events_path_getter=lambda: events_path,
        data_status_getter=lambda _detail: {},
        config_payload_getter=lambda: {"web_status_events_summary_limit": 2},
    )

    payload = facade.status_snapshot(
        detail_mode="fast",
        runtime_not_ready_response=lambda: None,
    )

    assert payload["events"]["count"] == 2
    assert payload["events"]["window_start"] == "2026-03-22T10:01:00"
    assert payload["events"]["latest"]["event"] == "cycle_completed"
    assert payload["config"]["web_status_events_summary_limit"] == 2


def test_state_backed_leaderboard_snapshot_prefers_runtime_path_surface_over_stale_state(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    state_file = runtime_root / "outputs" / "commander" / "state.json"
    _write_json(
        state_file,
        {
            "runtime_paths": {"training_output_dir": "stale-training-output"},
        },
    )
    current_training_dir = runtime_root / "outputs" / "current_training"
    _write_json(
        runtime_root / "state" / "runtime_paths.json",
        {
            "training_output_dir": str(current_training_dir),
        },
    )
    _write_json(
        runtime_root / "outputs" / "leaderboard.json",
        {
            "generated_at": "2026-03-22T10:30:00",
            "total_managers": 4,
            "eligible_managers": 3,
            "entries": [{"manager_id": "value_quality", "score": 0.94}],
        },
    )
    facade = StateBackedRuntimeFacade(
        project_root_getter=lambda: tmp_path,
        state_file_getter=lambda: state_file,
        runtime_lock_file_getter=lambda: runtime_root / "state" / "commander.lock",
        training_lock_file_getter=lambda: runtime_root / "state" / "training.lock",
        runtime_events_path_getter=lambda: (
            runtime_root / "state" / "commander_events.jsonl"
        ),
        data_status_getter=lambda _detail: {},
        config_payload_getter=lambda: {},
    )

    payload = facade.leaderboard_snapshot()

    assert payload["generated_at"] == "2026-03-22T10:30:00"
    assert payload["entries"][0]["manager_id"] == "value_quality"
