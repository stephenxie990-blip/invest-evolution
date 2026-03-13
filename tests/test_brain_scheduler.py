from pathlib import Path

import pytest

from brain.scheduler import CronService, HeartbeatService


def test_cron_service_add_remove(tmp_path: Path):
    store = tmp_path / "cron.json"
    cron = CronService(store)

    job = cron.add_job(name="t1", message="hello", every_sec=30)
    assert job.name == "t1"
    assert len(cron.list_jobs()) == 1

    ok = cron.remove_job(job.id)
    assert ok is True
    assert len(cron.list_jobs()) == 0


def test_cron_service_quarantines_corrupt_store(tmp_path: Path, caplog):
    store = tmp_path / "cron.json"
    store.write_text('{"jobs":', encoding="utf-8")
    cron = CronService(store)

    with caplog.at_level("WARNING"):
        cron._load()

    assert cron.list_jobs() == []
    quarantined = list(tmp_path.glob("cron.corrupt.*.json"))
    assert quarantined
    assert "Moved corrupt cron store" in caplog.text


def test_cron_service_logs_invalid_job_payload_and_keeps_valid_rows(tmp_path: Path, caplog):
    store = tmp_path / "cron.json"
    store.write_text(
        '{"jobs": [{"id": "a1", "name": "ok", "every_sec": 5}, {"name": "bad"}]}',
        encoding="utf-8",
    )
    cron = CronService(store)

    with caplog.at_level("WARNING"):
        cron._load()

    assert len(cron.list_jobs()) == 1
    assert cron.list_jobs()[0].id == "a1"
    assert "Skipping invalid cron job payload" in caplog.text


@pytest.mark.asyncio
async def test_heartbeat_service_logs_read_failure(tmp_path: Path, monkeypatch, caplog):
    heartbeat = HeartbeatService(tmp_path, enabled=True)
    heartbeat.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat.heartbeat_file.write_text("do thing", encoding="utf-8")

    original_read_text = Path.read_text

    def _broken_read_text(path_obj: Path, *args, **kwargs):
        if path_obj == heartbeat.heartbeat_file:
            raise OSError("unreadable")
        return original_read_text(path_obj, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _broken_read_text)

    with caplog.at_level("WARNING"):
        await heartbeat._tick()

    assert "Failed to read heartbeat file" in caplog.text
