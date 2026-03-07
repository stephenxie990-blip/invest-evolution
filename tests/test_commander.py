"""
Commander fusion tests.
"""

from pathlib import Path

from commander import StrategyGeneRegistry


def test_strategy_registry_templates(tmp_path: Path):
    registry = StrategyGeneRegistry(tmp_path)
    registry.ensure_default_templates()
    genes = registry.reload()

    assert len(genes) >= 3
    assert (tmp_path / "momentum_trend.md").exists()
    assert (tmp_path / "mean_reversion.json").exists()
    assert (tmp_path / "risk_guard.py").exists()

    ids = {g.gene_id for g in genes}
    assert "momentum_trend" in ids
    assert "mean_reversion" in ids
    assert "risk_guard" in ids


def test_strategy_registry_parses_custom_files(tmp_path: Path):
    (tmp_path / "a.md").write_text(
        "---\n"
        "id: alpha\n"
        "name: Alpha Gene\n"
        "enabled: false\n"
        "priority: 77\n"
        "description: alpha desc\n"
        "---\n"
        "\n"
        "body\n",
        encoding="utf-8",
    )
    (tmp_path / "b.json").write_text(
        '{"id":"beta","name":"Beta Gene","enabled":true,"priority":66,"description":"beta desc"}',
        encoding="utf-8",
    )
    (tmp_path / "c.py").write_text(
        '"""gamma doc"""\n'
        "GENE_META = {\n"
        '  "id": "gamma",\n'
        '  "name": "Gamma Gene",\n'
        '  "enabled": True,\n'
        '  "priority": 99,\n'
        '  "description": "gamma desc",\n'
        "}\n"
        "def run(ctx):\n"
        "  return ctx\n",
        encoding="utf-8",
    )

    registry = StrategyGeneRegistry(tmp_path)
    genes = registry.reload()

    assert len(genes) == 3
    ids = [g.gene_id for g in genes]
    assert ids == ["gamma", "alpha", "beta"]  # sorted by priority desc

    alpha = next(g for g in genes if g.gene_id == "alpha")
    assert alpha.enabled is False
    assert alpha.priority == 77


import pytest
from commander import CommanderConfig, CommanderRuntime

@pytest.mark.asyncio
async def test_commander_runtime_init(tmp_path):
    cfg = CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state.json",
        cron_store=tmp_path / "cron.json",
        memory_store=tmp_path / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "inbox",
        bridge_outbox=tmp_path / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    runtime = CommanderRuntime(cfg)
    assert runtime.cfg.workspace.exists()
    assert runtime.cfg.strategy_dir.exists()
    assert runtime.body is not None
    
    status = runtime.status()
    assert status["autopilot_enabled"] is False
    assert status["workspace"] == str(cfg.workspace)

@pytest.mark.asyncio
async def test_commander_runtime_start_stop(tmp_path):
    cfg = CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state.json",
        cron_store=tmp_path / "cron.json",
        memory_store=tmp_path / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "inbox",
        bridge_outbox=tmp_path / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    runtime = CommanderRuntime(cfg)
    await runtime.start()
    assert runtime._started is True
    await runtime.stop()
    assert runtime._started is False
