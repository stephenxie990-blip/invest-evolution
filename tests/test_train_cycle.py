from pathlib import Path

from app.train import SelfLearningController
from market_data import MockDataProvider


def test_run_training_cycle_emits_cutoff_date_before_diagnostics(
    monkeypatch,
    tmp_path: Path,
):
    import app.train as train_module

    events: list[tuple[str, dict]] = []
    output_dir = tmp_path / "training"
    meeting_dir = tmp_path / "meetings"
    audit_log = tmp_path / "runtime" / "state" / "config_changes.jsonl"
    snapshot_dir = tmp_path / "runtime" / "state" / "config_snapshots"

    controller = SelfLearningController(
        output_dir=str(output_dir),
        meeting_log_dir=str(meeting_dir),
        config_audit_log_path=str(audit_log),
        config_snapshot_dir=str(snapshot_dir),
        data_provider=MockDataProvider(stock_count=5, days=300, start_date="20230101"),
    )

    monkeypatch.setattr(controller.data_manager, "random_cutoff_date", lambda **_: "20240131")
    monkeypatch.setattr(
        controller.data_manager,
        "diagnose_training_data",
        lambda **kwargs: {"ready": False, "reason": "insufficient_history"},
    )

    train_module.set_event_callback(lambda event_type, payload: events.append((event_type, payload)))
    try:
        result = controller.run_training_cycle()
    finally:
        train_module._event_callback_state.callback = None

    assert result is None
    assert events
    assert events[0][0] == "cycle_start"
    assert events[0][1]["cycle_id"] == 1
    assert events[0][1]["cutoff_date"] == "20240131"
    assert events[0][1]["requested_data_mode"] == "mock"
    assert events[0][1]["llm_mode"] in {"live", "dry_run"}
