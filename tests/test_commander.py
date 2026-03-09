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
    assert runtime.cfg.workspace.exists() is False
    assert runtime.cfg.strategy_dir.exists() is False
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


@pytest.mark.asyncio
async def test_commander_runtime_init_is_read_only(tmp_path):
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
    runtime.status()
    assert not cfg.workspace.exists()
    assert not cfg.strategy_dir.exists()
    assert not cfg.plugin_dir.exists()
    assert not cfg.bridge_inbox.exists()
    assert not cfg.bridge_outbox.exists()
    assert not cfg.memory_store.exists()
    assert not cfg.state_file.exists()


@pytest.mark.asyncio
async def test_commander_system_prompt_includes_tool_policy(tmp_path):
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

    prompt = runtime._build_system_prompt()
    assert "prefer `invest_status` first" in prompt
    assert "invest_quick_test" in prompt
    assert "Read-only questions should stay read-only" in prompt
    assert "verified facts first, then risks" in prompt


def test_build_commander_tools_exposes_status_and_training_plan_tools(tmp_path):
    from brain.tools import build_commander_tools

    cfg = CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state" / "state.json",
        cron_store=tmp_path / "state" / "cron.json",
        memory_store=tmp_path / "memory" / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "inbox",
        bridge_outbox=tmp_path / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    runtime = CommanderRuntime(cfg)
    names = [tool.name for tool in build_commander_tools(runtime)]

    assert "invest_quick_status" in names
    assert "invest_deep_status" in names
    assert "invest_training_plan_create" in names
    assert "invest_training_plan_list" in names
    assert "invest_training_plan_execute" in names


@pytest.mark.asyncio
async def test_train_once_writes_plan_run_and_evaluation_artifacts(tmp_path):
    cfg = CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state" / "state.json",
        cron_store=tmp_path / "state" / "cron.json",
        memory_store=tmp_path / "memory" / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "inbox",
        bridge_outbox=tmp_path / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    runtime = CommanderRuntime(cfg)

    async def _fake_run_cycles(rounds: int, force_mock: bool, task_source: str):
        return {
            "status": "ok",
            "rounds": rounds,
            "results": [{"status": "ok", "cycle_id": 1, "return_pct": 1.23, "benchmark_passed": True}],
            "summary": {"total_cycles": 1},
        }

    runtime.body.run_cycles = _fake_run_cycles
    out = await runtime.train_once(rounds=2, mock=True)

    assert "training_lab" in out
    assert len(list(cfg.training_plan_dir.glob("*.json"))) == 1
    assert len(list(cfg.training_run_dir.glob("*.json"))) == 1
    assert len(list(cfg.training_eval_dir.glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_execute_training_plan_runs_persisted_plan(tmp_path):
    cfg = CommanderConfig(
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
        state_file=tmp_path / "state" / "state.json",
        cron_store=tmp_path / "state" / "cron.json",
        memory_store=tmp_path / "memory" / "memory.jsonl",
        plugin_dir=tmp_path / "plugins",
        bridge_inbox=tmp_path / "inbox",
        bridge_outbox=tmp_path / "outbox",
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    runtime = CommanderRuntime(cfg)
    plan = runtime.create_training_plan(rounds=3, mock=False, goal="compare model behavior", tags=["lab"])

    observed = {}

    async def _fake_run_cycles(rounds: int, force_mock: bool, task_source: str):
        observed["rounds"] = rounds
        observed["force_mock"] = force_mock
        observed["task_source"] = task_source
        return {
            "status": "ok",
            "rounds": rounds,
            "results": [{"status": "ok", "cycle_id": 9, "return_pct": -0.4, "benchmark_passed": False}],
            "summary": {"total_cycles": 9},
        }

    runtime.body.run_cycles = _fake_run_cycles
    out = await runtime.execute_training_plan(plan["plan_id"])

    assert observed == {"rounds": 3, "force_mock": False, "task_source": "manual"}
    assert out["training_lab"]["plan"]["plan_id"] == plan["plan_id"]
    saved_plan = next(cfg.training_plan_dir.glob("*.json")).read_text(encoding="utf-8")
    assert '"status": "completed"' in saved_plan
