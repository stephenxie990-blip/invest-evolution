from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from invest_evolution.application.commander.status import memory_brief_row, read_event_rows
from invest_evolution.application.runtime_contracts import safe_read_jsonl


def _make_runtime(tmp_path: Path) -> SimpleNamespace:
    runtime_dir = tmp_path / "runtime"
    training_dir = runtime_dir / "training"
    meeting_dir = runtime_dir / "artifacts"
    snapshot_dir = runtime_dir / "snapshots"
    audit_dir = runtime_dir / "audit"
    plan_dir = runtime_dir / "plans"
    run_dir = runtime_dir / "runs"
    eval_dir = runtime_dir / "evals"
    for path in (training_dir, meeting_dir, snapshot_dir, audit_dir, plan_dir, run_dir, eval_dir):
        path.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        cfg=SimpleNamespace(
            runtime_state_dir=str(runtime_dir),
            training_output_dir=str(training_dir),
            artifact_log_dir=str(meeting_dir),
            config_snapshot_dir=str(snapshot_dir),
            config_audit_log_path=str(audit_dir / "changes.jsonl"),
            training_plan_dir=str(plan_dir),
            training_run_dir=str(run_dir),
            training_eval_dir=str(eval_dir),
        )
    )


def test_read_event_rows_logs_invalid_json_lines(tmp_path, caplog):
    path = tmp_path / "events.jsonl"
    path.write_text('{"event":"ok"}\nnot-json\n{"event":"ok2"}\n', encoding="utf-8")

    with caplog.at_level("WARNING"):
        rows = read_event_rows(path)

    assert [row["event"] for row in rows] == ["ok", "ok2"]
    assert "Skipped 1 invalid runtime event row(s)" in caplog.text


def test_safe_read_jsonl_logs_invalid_json_lines(tmp_path, caplog):
    runtime = _make_runtime(tmp_path)
    path = Path(runtime.cfg.training_output_dir) / "optimization_events.jsonl"
    path.write_text('{"cycle_id":1}\nbad-json\n{"cycle_id":2}\n', encoding="utf-8")

    with caplog.at_level("WARNING"):
        rows = safe_read_jsonl(runtime, str(path))

    assert [row["cycle_id"] for row in rows] == [1, 2]
    assert "Skipped 1 invalid JSONL row(s)" in caplog.text


def test_safe_read_jsonl_respects_zero_limit(tmp_path):
    runtime = _make_runtime(tmp_path)
    path = Path(runtime.cfg.training_output_dir) / "optimization_events.jsonl"
    path.write_text('{"cycle_id":1}\n{"cycle_id":2}\n', encoding="utf-8")

    rows = safe_read_jsonl(runtime, str(path), limit=0)

    assert rows == []


def test_read_event_rows_respects_zero_limit(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"event":"ok"}\n{"event":"ok2"}\n', encoding="utf-8")

    rows = read_event_rows(path, limit=0)

    assert rows == []


def test_memory_brief_row_logs_invalid_timestamp(caplog):
    with caplog.at_level("WARNING"):
        row = memory_brief_row({"ts_ms": "invalid"})

    assert row["ts"] == ""
    assert "Failed to normalize memory ts_ms='invalid'" in caplog.text
