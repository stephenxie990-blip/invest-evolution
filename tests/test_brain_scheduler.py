from pathlib import Path

from brain_scheduler import CronService


def test_cron_service_add_remove(tmp_path: Path):
    store = tmp_path / "cron.json"
    cron = CronService(store)

    job = cron.add_job(name="t1", message="hello", every_sec=30)
    assert job.name == "t1"
    assert len(cron.list_jobs()) == 1

    ok = cron.remove_job(job.id)
    assert ok is True
    assert len(cron.list_jobs()) == 0

