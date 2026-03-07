import asyncio
import json
from pathlib import Path

import pytest

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
