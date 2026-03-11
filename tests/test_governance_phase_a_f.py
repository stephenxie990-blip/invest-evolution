import asyncio
import json
import os
from pathlib import Path

import pytest

import app.commander as commander_module
import config as config_module
from commander import CommanderConfig, CommanderRuntime
from config.services import EvolutionConfigService
from market_data.datasets import WebDatasetService
from market_data.quality import DataQualityService
from market_data.repository import MarketDataRepository


def test_config_service_writes_audit_and_snapshot(tmp_path: Path):
    service = EvolutionConfigService(project_root=tmp_path, live_config=config_module.EvolutionConfig())
    payload = service.apply_patch({"max_stocks": 11, "enable_debate": False}, source="test")

    assert payload["updated"] == ["enable_debate", "max_stocks"]
    assert (tmp_path / "config" / "evolution.yaml").exists()
    assert service.audit_log_path.exists()
    assert service.snapshot_dir.exists()

    audit_lines = service.audit_log_path.read_text(encoding="utf-8").splitlines()
    assert audit_lines
    audit = json.loads(audit_lines[-1])
    assert audit["source"] == "test"
    assert "max_stocks" in audit["changed"]


def test_config_service_snapshots_no_longer_include_llm_api_key(tmp_path: Path):
    live = config_module.EvolutionConfig(llm_api_key="sk-secret-12345678")
    service = EvolutionConfigService(project_root=tmp_path, live_config=live)

    service.apply_patch({"max_stocks": 12}, source="test")
    snapshot = json.loads(next(service.snapshot_dir.glob("config_*.json")).read_text(encoding="utf-8"))
    assert "llm_api_key" not in snapshot

    runtime_snapshot = service.write_runtime_snapshot(cycle_id=1, output_dir=tmp_path / "out")
    payload = json.loads(runtime_snapshot.read_text(encoding="utf-8"))
    payload_copy = json.loads((tmp_path / "out" / "cycle_0001_config_snapshot.json").read_text(encoding="utf-8"))
    assert "llm_api_key" not in payload
    assert payload == payload_copy


@pytest.mark.asyncio
async def test_commander_runtime_lock_lifecycle(tmp_path: Path):
    cfg = CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state" / "state.json",
        cron_store=tmp_path / "state" / "cron.json",
        memory_store=tmp_path / "memory" / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "sessions" / "inbox",
        bridge_outbox=tmp_path / "sessions" / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    rt = CommanderRuntime(cfg)
    await rt.start()
    assert cfg.runtime_lock_file.exists()
    status = rt.status()
    assert status["runtime"]["runtime_lock_active"] is True
    assert status["runtime"]["state"] == "idle"
    await rt.stop()
    assert not cfg.runtime_lock_file.exists()


def test_commander_runtime_replaces_stale_lock_file(tmp_path: Path, monkeypatch):
    cfg = CommanderConfig(state_file=tmp_path / "state" / "state.json")
    cfg.runtime_lock_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.runtime_lock_file.write_text(json.dumps({"pid": 999999, "host": "stale"}), encoding="utf-8")

    rt = CommanderRuntime(cfg)
    monkeypatch.setattr(rt, "_is_pid_alive", lambda pid: False)

    rt._acquire_runtime_lock()

    payload = json.loads(cfg.runtime_lock_file.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["instance_id"] == rt.instance_id

    rt._release_runtime_lock()
    assert not cfg.runtime_lock_file.exists()


def test_commander_runtime_lock_detects_live_race(tmp_path: Path, monkeypatch):
    cfg = CommanderConfig(state_file=tmp_path / "state" / "state.json")
    rt = CommanderRuntime(cfg)
    real_open = commander_module.os.open
    attempts = {"count": 0}

    def fake_open(path, flags, mode):
        if attempts["count"] == 0:
            attempts["count"] += 1
            cfg.runtime_lock_file.write_text(
                json.dumps({"pid": os.getpid(), "host": "peer", "instance_id": "peer:1"}),
                encoding="utf-8",
            )
            raise FileExistsError
        return real_open(path, flags, mode)

    monkeypatch.setattr(commander_module.os, "open", fake_open)

    with pytest.raises(RuntimeError, match="already active"):
        rt._acquire_runtime_lock()


def test_commander_runtime_body_events_update_runtime_snapshot(tmp_path: Path):
    cfg = CommanderConfig(state_file=tmp_path / "state" / "state.json")
    rt = CommanderRuntime(cfg)

    rt._on_body_event("training_started", {"type": "training", "rounds": 1})
    started = rt.status()["runtime"]
    assert started["state"] == "training"
    assert started["current_task"]["rounds"] == 1

    rt._on_body_event("training_finished", {"type": "training", "status": "ok"})
    finished = rt.status()["runtime"]
    assert finished["state"] == "idle"
    assert finished["current_task"] is None
    assert finished["last_task"]["status"] == "ok"


@pytest.mark.asyncio
async def test_body_returns_busy_when_training_in_progress(tmp_path: Path):
    cfg = CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state" / "state.json",
        cron_store=tmp_path / "state" / "cron.json",
        memory_store=tmp_path / "memory" / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "sessions" / "inbox",
        bridge_outbox=tmp_path / "sessions" / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    rt = CommanderRuntime(cfg)
    await rt.body._lock.acquire()
    try:
        result = await rt.body.run_cycles(rounds=1, force_mock=True, task_source="test")
    finally:
        rt.body._lock.release()
    assert result["status"] == "busy"
    assert "training" in result["error"]


def test_web_dataset_service_exposes_quality_summary(tmp_path: Path):
    repo = MarketDataRepository(tmp_path / "stock.db")
    quality = DataQualityService(repository=repo).persist_audit()
    payload = WebDatasetService(repository=repo).get_status_summary()
    assert payload["quality"]["healthy"] == quality["healthy"]
    assert "meta" in payload["quality"]


def test_commander_config_derives_runtime_artifact_paths(tmp_path: Path):
    cfg = CommanderConfig(state_file=tmp_path / "state" / "state.json")

    assert cfg.runtime_state_dir == tmp_path / "state"
    assert cfg.training_output_dir == tmp_path / "state" / "training"
    assert cfg.meeting_log_dir == tmp_path / "state" / "meetings"
    assert cfg.config_audit_log_path == tmp_path / "state" / "config_changes.jsonl"
    assert cfg.config_snapshot_dir == tmp_path / "state" / "config_snapshots"


def test_train_controller_accepts_injected_artifact_paths(tmp_path: Path):
    from app.train import SelfLearningController

    output_dir = tmp_path / "outputs"
    meeting_dir = tmp_path / "meetings"
    audit_log = tmp_path / "runtime" / "state" / "config_changes.jsonl"
    snapshot_dir = tmp_path / "runtime" / "state" / "config_snapshots"

    controller = SelfLearningController(
        output_dir=str(output_dir),
        meeting_log_dir=str(meeting_dir),
        config_audit_log_path=str(audit_log),
        config_snapshot_dir=str(snapshot_dir),
    )

    assert controller.output_dir == output_dir
    assert controller.meeting_recorder.base_dir == meeting_dir
    assert controller.config_service.audit_log_path == audit_log
    assert controller.config_service.snapshot_dir == snapshot_dir


def test_quality_audit_uses_snapshot_by_default(tmp_path: Path, monkeypatch):
    repo = MarketDataRepository(tmp_path / "stock.db")
    repo.initialize_schema()
    repo.upsert_security_master([{"code": "sh.600001", "name": "Foo", "list_date": "20200101", "source": "test"}])
    repo.upsert_daily_bars([{"code": "sh.600001", "trade_date": "20240108", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000, "amount": 5000, "pct_chg": 0.5, "turnover": 1.2, "source": "test"}])

    svc = DataQualityService(repository=repo)
    first = svc.audit(force_refresh=True)
    assert first["healthy"] is True

    def _boom():
        raise AssertionError("snapshot path should not recompute quality audit")

    monkeypatch.setattr(svc, "_compute_audit_payload", _boom)
    second = svc.audit()
    assert second["healthy"] is True


def test_quality_audit_force_refresh_bypasses_snapshot(tmp_path: Path, monkeypatch):
    repo = MarketDataRepository(tmp_path / "stock.db")
    repo.initialize_schema()
    svc = DataQualityService(repository=repo)
    called = {"count": 0}

    def _fake_compute():
        called["count"] += 1
        return {
            "status": {"stock_count": 0, "kline_count": 0, "latest_date": ""},
            "date_range": {"min": None, "max": None},
            "index_date_range": {"min": None, "max": None},
            "meta": {},
            "checks": {},
            "issues": ["daily_bar is empty"],
            "healthy": False,
            "health_status": "degraded",
            "has_data": False,
        }

    monkeypatch.setattr(svc, "_compute_audit_payload", _fake_compute)
    svc.audit(force_refresh=True)
    svc.audit(force_refresh=True)
    assert called["count"] == 2


def test_commander_status_supports_fast_and_slow_modes(tmp_path: Path):
    cfg = CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state" / "state.json",
        cron_store=tmp_path / "state" / "cron.json",
        memory_store=tmp_path / "memory" / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "sessions" / "inbox",
        bridge_outbox=tmp_path / "sessions" / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    rt = CommanderRuntime(cfg)

    fast = rt.status()
    slow = rt.status(detail="slow")

    assert fast["detail_mode"] == "fast"
    assert slow["detail_mode"] == "slow"
    assert fast["data"]["detail_mode"] == "fast"
    assert slow["data"]["detail_mode"] == "slow"


def test_commander_status_includes_training_lab_summary(tmp_path: Path):
    cfg = CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state" / "state.json",
        cron_store=tmp_path / "state" / "cron.json",
        memory_store=tmp_path / "memory" / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "sessions" / "inbox",
        bridge_outbox=tmp_path / "sessions" / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    rt = CommanderRuntime(cfg)
    plan = rt.create_training_plan(rounds=1, mock=True, goal="smoke")

    status = rt.status()
    assert "training_lab" in status
    assert status["training_lab"]["plan_count"] >= 1
    assert status["training_lab"]["latest_plans"][0]["plan_id"] == plan["plan_id"]
    assert status["training_lab"]["latest_runs"] == []
    assert status["training_lab"]["latest_evaluations"] == []
