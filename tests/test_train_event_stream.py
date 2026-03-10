import time
from queue import Queue

import app.train as train_module
import web_server
from market_data import MockDataProvider


def test_event_sink_drops_when_buffer_full(monkeypatch):
    full_buffer = Queue(maxsize=1)
    full_buffer.put_nowait({"type": "existing", "data": {}})

    monkeypatch.setattr(web_server, "_event_buffer", full_buffer)
    monkeypatch.setattr(web_server, "_ensure_event_dispatcher", lambda: None)

    web_server._event_sink("cycle_start", {"cycle_id": 1})

    assert full_buffer.qsize() == 1


def test_event_sink_dispatches_recent_event():
    web_server._ensure_event_dispatcher()
    with web_server._event_condition:
        web_server._event_history.clear()
        web_server._event_seq = 0

    web_server._event_sink("cycle_complete", {"cycle_id": 7, "return_pct": 1.23})

    deadline = time.time() + 1.0
    pending = []
    while time.time() < deadline:
        pending, _ = web_server._snapshot_events_since(0)
        if pending:
            break
        time.sleep(0.01)

    assert pending
    assert pending[-1]["type"] == "cycle_complete"
    assert pending[-1]["data"]["cycle_id"] == 7


def test_cycle_start_event_carries_cutoff_date(monkeypatch, tmp_path):
    events = []
    controller = train_module.SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
        data_provider=MockDataProvider(stock_count=5, days=120, start_date="20200101"),
    )

    monkeypatch.setattr(controller.data_manager, "random_cutoff_date", lambda: "20240229")
    monkeypatch.setattr(controller.data_manager, "diagnose_training_data", lambda **_: {"ready": True})
    monkeypatch.setattr(controller.data_manager, "load_stock_data", lambda *args, **kwargs: {})
    monkeypatch.setattr(train_module, "_event_callback", lambda event_type, data: events.append((event_type, data)))

    result = controller.run_training_cycle()

    assert result is None
    assert events
    assert events[0][0] == "cycle_start"
    assert events[0][1]["cutoff_date"] == "20240229"
    assert events[0][1]["requested_data_mode"] == "mock"
    assert events[0][1]["llm_mode"] in {"live", "dry_run"}
