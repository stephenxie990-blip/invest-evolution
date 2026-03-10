import json
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
    assert "prefer `invest_quick_status` by default" in prompt
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


@pytest.mark.asyncio
async def test_execute_training_plan_passes_experiment_protocol_to_body(tmp_path):
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
    plan = runtime.create_training_plan(
        rounds=2,
        mock=True,
        goal="protocol regression",
        protocol={"seed": 42, "date_range": {"min": "20240101", "max": "20241231"}},
        dataset={"min_history_days": 180, "simulation_days": 20},
        model_scope={"allowed_models": ["momentum"], "allocator_enabled": False},
    )

    observed = {}

    async def _fake_run_cycles(rounds: int, force_mock: bool, task_source: str, experiment_spec=None):
        observed["rounds"] = rounds
        observed["force_mock"] = force_mock
        observed["task_source"] = task_source
        observed["experiment_spec"] = experiment_spec
        return {
            "status": "completed",
            "rounds": rounds,
            "results": [{"status": "ok", "cycle_id": 1, "return_pct": 0.5, "benchmark_passed": True}],
            "summary": {"total_cycles": 1},
        }

    runtime.body.run_cycles = _fake_run_cycles
    out = await runtime.execute_training_plan(plan["plan_id"])

    assert out["status"] == "completed"
    assert observed["experiment_spec"]["protocol"]["seed"] == 42
    assert observed["experiment_spec"]["dataset"]["simulation_days"] == 20
    assert observed["experiment_spec"]["model_scope"]["allowed_models"] == ["momentum"]


def test_training_evaluation_summary_includes_strategy_score(tmp_path):
    cfg = CommanderConfig(
        workspace=tmp_path / 'workspace',
        strategy_dir=tmp_path / 'strategies',
        state_file=tmp_path / 'state' / 'state.json',
        cron_store=tmp_path / 'state' / 'cron.json',
        memory_store=tmp_path / 'memory' / 'memory.jsonl',
        plugin_dir=tmp_path / 'plugins',
        bridge_inbox=tmp_path / 'inbox',
        bridge_outbox=tmp_path / 'outbox',
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    runtime = CommanderRuntime(cfg)
    plan = runtime.create_training_plan(rounds=1, mock=True)
    summary = runtime._build_training_evaluation_summary(
        {
            'status': 'completed',
            'results': [
                {'status': 'ok', 'return_pct': 1.2, 'benchmark_passed': True, 'strategy_scores': {'overall_score': 0.66}},
                {'status': 'ok', 'return_pct': 0.8, 'benchmark_passed': False, 'strategy_scores': {'overall_score': 0.54}},
            ],
        },
        plan=plan,
        run_id='run_x',
    )
    assert summary['assessment']['avg_strategy_score'] == 0.6


def test_training_evaluation_summary_builds_promotion_verdict_against_baseline(tmp_path):
    cfg = CommanderConfig(
        workspace=tmp_path / 'workspace',
        strategy_dir=tmp_path / 'strategies',
        state_file=tmp_path / 'state' / 'state.json',
        cron_store=tmp_path / 'state' / 'cron.json',
        memory_store=tmp_path / 'memory' / 'memory.jsonl',
        plugin_dir=tmp_path / 'plugins',
        bridge_inbox=tmp_path / 'inbox',
        bridge_outbox=tmp_path / 'outbox',
        training_output_dir=tmp_path / 'runtime' / 'training',
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    runtime = CommanderRuntime(cfg)
    root_dir = cfg.training_output_dir.parent
    root_dir.mkdir(parents=True, exist_ok=True)
    (root_dir / 'leaderboard.json').write_text(json.dumps({
        'generated_at': '2026-03-09T00:00:00',
        'entries': [
            {'model_name': 'momentum', 'config_name': 'momentum_v1', 'avg_return_pct': 0.4, 'avg_strategy_score': 0.45},
        ],
    }, ensure_ascii=False), encoding='utf-8')
    plan = runtime.create_training_plan(
        rounds=3,
        mock=True,
        model_scope={'baseline_models': ['momentum']},
        optimization={'promotion_gate': {
            'min_samples': 2,
            'min_avg_return_pct': 0.5,
            'min_avg_strategy_score': 0.6,
            'min_benchmark_pass_rate': 0.5,
            'min_return_advantage_vs_baseline': 0.1,
            'min_strategy_score_advantage_vs_baseline': 0.1,
        }},
        protocol={'holdout': {'enabled': True, 'label': '2025Q4'}, 'walk_forward': {'enabled': True, 'folds': 3}},
    )
    summary = runtime._build_training_evaluation_summary(
        {
            'status': 'completed',
            'results': [
                {'status': 'ok', 'model_name': 'value_quality', 'config_name': 'value_quality_v1', 'return_pct': 0.8, 'benchmark_passed': True, 'strategy_scores': {'overall_score': 0.7}},
                {'status': 'ok', 'model_name': 'value_quality', 'config_name': 'value_quality_v1', 'return_pct': 0.6, 'benchmark_passed': False, 'strategy_scores': {'overall_score': 0.62}},
            ],
        },
        plan=plan,
        run_id='run_promote',
    )
    assert summary['promotion']['candidate']['model_name'] == 'value_quality'
    assert summary['promotion']['baselines']['models'] == ['momentum']
    assert summary['promotion']['verdict'] == 'promoted'
    assert summary['promotion']['protocol']['holdout']['label'] == '2025Q4'
    assert summary['promotion']['protocol']['walk_forward']['folds'] == 3


def test_training_evaluation_summary_rejects_when_gate_not_met(tmp_path):
    cfg = CommanderConfig(
        workspace=tmp_path / 'workspace',
        strategy_dir=tmp_path / 'strategies',
        state_file=tmp_path / 'state' / 'state.json',
        cron_store=tmp_path / 'state' / 'cron.json',
        memory_store=tmp_path / 'memory' / 'memory.jsonl',
        plugin_dir=tmp_path / 'plugins',
        bridge_inbox=tmp_path / 'inbox',
        bridge_outbox=tmp_path / 'outbox',
        training_output_dir=tmp_path / 'runtime' / 'training',
        mock_mode=True,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    runtime = CommanderRuntime(cfg)
    plan = runtime.create_training_plan(
        rounds=1,
        mock=True,
        optimization={'promotion_gate': {'min_samples': 2, 'min_avg_strategy_score': 0.8}},
    )
    summary = runtime._build_training_evaluation_summary(
        {
            'status': 'completed_with_skips',
            'results': [
                {'status': 'ok', 'model_name': 'momentum', 'config_name': 'momentum_v1', 'return_pct': 0.2, 'benchmark_passed': True, 'strategy_scores': {'overall_score': 0.55}},
            ],
        },
        plan=plan,
        run_id='run_reject',
    )
    assert summary['promotion']['verdict'] == 'rejected'
    assert any(check['name'] == 'min_samples' and check['passed'] is False for check in summary['promotion']['checks'])


def test_build_training_evaluation_summary_uses_helper_shape(tmp_path):
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
    plan = runtime.create_training_plan(rounds=2, mock=True)
    payload = {
        "status": "completed",
        "results": [
            {"status": "ok", "model_name": "momentum", "config_name": "momentum_v1", "return_pct": 1.2, "benchmark_passed": True, "strategy_scores": {"overall_score": 0.7}},
            {"status": "no_data"},
        ],
    }
    summary = runtime._build_training_evaluation_summary(payload, plan=plan, run_id='run_x')
    assert summary['assessment']['success_count'] == 1
    assert summary['assessment']['no_data_count'] == 1
    assert summary['artifacts']['run_path'].endswith('run_x.json')



def test_invest_status_tool_is_marked_as_compat_alias():
    from brain.tools import InvestStatusTool

    tool = InvestStatusTool(runtime=None)
    assert tool.name == "invest_status"
    assert "Deprecated compatibility alias" in tool.description
    assert "invest_quick_status" in tool.description



def test_run_cycles_switches_back_to_real_data_manager_after_mock_run(tmp_path):
    import asyncio

    from app.commander import CommanderConfig, InvestmentBodyService
    from app.train import TrainingResult

    cfg = CommanderConfig(
        workspace=tmp_path / 'workspace',
        strategy_dir=tmp_path / 'strategies',
        state_file=tmp_path / 'state' / 'state.json',
        cron_store=tmp_path / 'state' / 'cron.json',
        memory_store=tmp_path / 'memory' / 'memory.jsonl',
        plugin_dir=tmp_path / 'plugins',
        bridge_inbox=tmp_path / 'inbox',
        bridge_outbox=tmp_path / 'outbox',
        training_output_dir=tmp_path / 'training',
        meeting_log_dir=tmp_path / 'meetings',
        config_audit_log_path=tmp_path / 'audit' / 'changes.jsonl',
        config_snapshot_dir=tmp_path / 'snapshots',
        training_lock_file=tmp_path / 'state' / 'training.lock',
        mock_mode=False,
        autopilot_enabled=False,
        heartbeat_enabled=False,
        bridge_enabled=False,
    )
    body = InvestmentBodyService(cfg)
    real_manager = body._real_data_manager

    def _result(mode: str) -> TrainingResult:
        return TrainingResult(
            cycle_id=1,
            cutoff_date='20240101',
            selected_stocks=['sh.600000'],
            initial_capital=100000,
            final_value=101000,
            return_pct=1.0,
            is_profit=True,
            trade_history=[],
            params={},
            data_mode=mode,
            requested_data_mode='mock' if mode == 'mock' else 'live',
            effective_data_mode=mode,
            llm_mode='dry_run' if mode == 'mock' else 'live',
        )

    body.controller.run_training_cycle = lambda: _result('mock')
    asyncio.run(body.run_cycles(rounds=1, force_mock=True))
    assert body.controller.data_manager is body._mock_data_manager
    assert body.controller.llm_mode == 'dry_run'

    body.controller.run_training_cycle = lambda: _result('offline')
    asyncio.run(body.run_cycles(rounds=1, force_mock=False))
    assert body.controller.data_manager is real_manager
    assert body.controller.llm_mode == 'live'
