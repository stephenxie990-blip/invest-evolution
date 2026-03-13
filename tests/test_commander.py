"""
Commander fusion tests.
"""

import json
from pathlib import Path

import pytest

from commander import StrategyGeneRegistry
from commander import CommanderConfig, CommanderRuntime


def _assert_bounded_workflow_protocol(payload: dict, *, domain: str, writes_state: bool):
    assert payload["protocol"]["schema_version"] == "bounded_workflow.v2"
    assert payload["protocol"]["task_bus_schema_version"] == "task_bus.v2"
    assert payload["protocol"]["plan_schema_version"] == "task_plan.v2"
    assert payload["protocol"]["coverage_schema_version"] == "task_coverage.v2"
    assert payload["protocol"]["artifact_taxonomy_schema_version"] == "artifact_taxonomy.v2"
    assert payload["protocol"]["domain"] == domain
    assert payload["artifacts"]["domain"] == domain
    assert payload["coverage"]["schema_version"] == "task_coverage.v2"
    assert payload["coverage"]["coverage_kind"] == "workflow_phase_completion"
    assert payload["coverage"]["workflow_step_count"] == len(payload["orchestration"]["workflow"])
    assert payload["coverage"]["completed_workflow_step_count"] == len(payload["orchestration"]["workflow"])
    assert payload["artifact_taxonomy"]["schema_version"] == "artifact_taxonomy.v2"
    assert "workspace" in payload["artifact_taxonomy"]["keys"]
    assert payload["orchestration"]["policy"]["writes_state"] is writes_state
    assert payload["task_bus"]["schema_version"] == "task_bus.v2"
    assert payload["task_bus"]["planner"]["operation"] == payload["protocol"]["operation"]
    assert payload["task_bus"]["planner"]["mode"] == "commander_runtime_method"
    recommended_tools = [step["tool"] for step in payload["task_bus"]["planner"]["recommended_plan"]]
    assert payload["entrypoint"]["runtime_tool"] in recommended_tools
    assert payload["task_bus"]["planner"]["plan_summary"]["schema_version"] == "task_plan.v2"
    assert payload["task_bus"]["gate"]["writes_state"] is writes_state
    assert payload["task_bus"]["audit"]["artifacts"]["domain"] == domain
    assert payload["task_bus"]["audit"]["coverage"]["schema_version"] == "task_coverage.v2"
    assert "parameterized_step_count" in payload["task_bus"]["audit"]["coverage"]
    assert "covered_parameterized_step_ids" in payload["task_bus"]["audit"]["coverage"]
    assert "missing_parameterized_step_ids" in payload["task_bus"]["audit"]["coverage"]
    assert "parameter_coverage" in payload["task_bus"]["audit"]["coverage"]
    assert payload["task_bus"]["audit"]["artifact_taxonomy"]["schema_version"] == "artifact_taxonomy.v2"
    assert payload["feedback"]["summary"]
    assert payload["next_action"]["kind"]
    assert "planned_step_coverage" in payload["feedback"]["coverage"]


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
    assert out['training_lab']['plan']['guardrails']['promotion_gate']['research_feedback']['enabled'] is True
    assert out['training_lab']['evaluation']['promotion']['research_feedback']['passed'] is False
    assert 'research_feedback.available' in out['training_lab']['evaluation']['promotion']['research_feedback']['reason_codes']
    assert '缺少可用研究反馈样本' in out['training_lab']['evaluation']['promotion']['research_feedback']['summary']
    assert out["entrypoint"]["agent_kind"] == "bounded_training_agent"
    assert out["orchestration"]["phase_stats"]["rounds"] == 2
    assert out["orchestration"]["policy"]["fixed_boundary"] is True
    _assert_bounded_workflow_protocol(out, domain="training", writes_state=True)
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
    assert out["entrypoint"]["runtime_tool"] == "invest_training_plan_execute"
    assert out["orchestration"]["workflow"][2] == "training_cycles_execute"
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
                {
                    'status': 'ok',
                    'model_name': 'value_quality',
                    'config_name': 'value_quality_v1',
                    'return_pct': 0.8,
                    'benchmark_passed': True,
                    'strategy_scores': {'overall_score': 0.7},
                    'research_feedback': {
                        'sample_count': 7,
                        'recommendation': {'bias': 'maintain', 'summary': 'maintain'},
                        'horizons': {'T+20': {'hit_rate': 0.54, 'invalidation_rate': 0.18}},
                        'brier_like_direction_score': 0.18,
                    },
                },
                {
                    'status': 'ok',
                    'model_name': 'value_quality',
                    'config_name': 'value_quality_v1',
                    'return_pct': 0.6,
                    'benchmark_passed': False,
                    'strategy_scores': {'overall_score': 0.62},
                    'research_feedback': {
                        'sample_count': 8,
                        'recommendation': {'bias': 'maintain', 'summary': 'maintain'},
                        'horizons': {'T+20': {'hit_rate': 0.58, 'invalidation_rate': 0.16}},
                        'brier_like_direction_score': 0.16,
                    },
                },
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


def test_get_control_plane_and_data_status_expose_bounded_workflows(tmp_path):
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

    control_plane = runtime.get_control_plane()
    data_status = runtime.get_data_status(refresh=True)

    assert control_plane["entrypoint"]["agent_kind"] == "bounded_config_agent"
    assert control_plane["orchestration"]["workflow"] == ["config_scope_resolve", "control_plane_read", "finalize"]
    _assert_bounded_workflow_protocol(control_plane, domain="config", writes_state=False)
    assert data_status["entrypoint"]["agent_kind"] == "bounded_data_agent"
    assert data_status["orchestration"]["workflow"][1] == "data_status_refresh"
    assert data_status["orchestration"]["policy"]["tool_catalog_scope"] == "data_domain"
    _assert_bounded_workflow_protocol(data_status, domain="data", writes_state=False)


def test_runtime_observability_memory_scheduler_and_analytics_expose_bounded_workflows(tmp_path):
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
    runtime._ensure_runtime_storage()
    runtime.memory.append(kind="note", session_key="test", content="remember this", metadata={"tag": "x"})

    status = runtime.status(detail="fast")
    events = runtime.get_events_summary(limit=10)
    diagnostics = runtime.get_runtime_diagnostics(event_limit=10, memory_limit=5)
    memory_list = runtime.list_memory(query="remember", limit=5)
    record_id = memory_list["items"][0]["id"]
    memory_detail = runtime.get_memory_detail(record_id)
    cron_add = runtime.add_cron_job(name="ping", message="看看系统状态", every_sec=60)
    cron_list = runtime.list_cron_jobs()
    cron_remove = runtime.remove_cron_job(cron_add["job"]["id"])
    models = runtime.get_investment_models()
    leaderboard = runtime.get_leaderboard()

    assert status["entrypoint"]["agent_kind"] == "bounded_runtime_agent"
    _assert_bounded_workflow_protocol(status, domain="runtime", writes_state=False)
    assert events["entrypoint"]["agent_kind"] == "bounded_runtime_agent"
    _assert_bounded_workflow_protocol(events, domain="runtime", writes_state=False)
    assert diagnostics["entrypoint"]["agent_kind"] == "bounded_runtime_agent"
    _assert_bounded_workflow_protocol(diagnostics, domain="runtime", writes_state=False)
    assert memory_list["entrypoint"]["agent_kind"] == "bounded_memory_agent"
    _assert_bounded_workflow_protocol(memory_list, domain="memory", writes_state=False)
    assert memory_detail["entrypoint"]["agent_kind"] == "bounded_memory_agent"
    _assert_bounded_workflow_protocol(memory_detail, domain="memory", writes_state=False)
    assert cron_add["entrypoint"]["agent_kind"] == "bounded_scheduler_agent"
    assert cron_list["entrypoint"]["agent_kind"] == "bounded_scheduler_agent"
    _assert_bounded_workflow_protocol(cron_add, domain="scheduler", writes_state=True)
    _assert_bounded_workflow_protocol(cron_list, domain="scheduler", writes_state=False)
    assert cron_remove["entrypoint"]["agent_kind"] == "bounded_scheduler_agent"
    _assert_bounded_workflow_protocol(cron_remove, domain="scheduler", writes_state=True)
    assert models["entrypoint"]["agent_kind"] == "bounded_analytics_agent"
    _assert_bounded_workflow_protocol(models, domain="analytics", writes_state=False)
    assert leaderboard["entrypoint"]["agent_kind"] == "bounded_analytics_agent"
    _assert_bounded_workflow_protocol(leaderboard, domain="analytics", writes_state=False)
    assert cron_list["orchestration"]["workflow"][1] == "cron_list"
    assert cron_remove["orchestration"]["workflow"][1] == "cron_remove"


def test_config_strategy_and_plugin_surfaces_expose_bounded_workflows(tmp_path):
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
    runtime._ensure_runtime_storage()

    prompts = runtime.list_agent_prompts()
    paths = runtime.get_runtime_paths()
    stock_strategies = runtime.list_stock_strategies()
    reloaded = runtime.reload_strategies()
    plugins = runtime.reload_plugins()

    assert prompts["entrypoint"]["agent_kind"] == "bounded_config_agent"
    _assert_bounded_workflow_protocol(prompts, domain="config", writes_state=False)
    assert paths["entrypoint"]["agent_kind"] == "bounded_config_agent"
    _assert_bounded_workflow_protocol(paths, domain="config", writes_state=False)
    assert stock_strategies["entrypoint"]["agent_kind"] == "bounded_strategy_agent"
    assert reloaded["entrypoint"]["agent_kind"] == "bounded_strategy_agent"
    _assert_bounded_workflow_protocol(stock_strategies, domain="strategy", writes_state=False)
    _assert_bounded_workflow_protocol(reloaded, domain="strategy", writes_state=True)
    assert plugins["entrypoint"]["agent_kind"] == "bounded_plugin_agent"
    _assert_bounded_workflow_protocol(plugins, domain="plugin", writes_state=True)
    assert reloaded["orchestration"]["policy"]["fixed_boundary"] is True
    assert plugins["orchestration"]["workflow"][1] == "plugin_reload"


def test_commander_config_relocates_state_relative_paths(tmp_path):
    cfg = CommanderConfig(
        state_file=tmp_path / "custom-state" / "state.json",
        workspace=tmp_path / "workspace",
        strategy_dir=tmp_path / "strategies",
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

    assert cfg.runtime_state_dir == tmp_path / "custom-state"
    assert cfg.runtime_lock_file == tmp_path / "custom-state" / "commander.lock"
    assert cfg.training_plan_dir == tmp_path / "custom-state" / "training_plans"
    assert cfg.runtime_events_path == tmp_path / "custom-state" / "commander_events.jsonl"


def test_run_cycles_returns_nodata_item_with_artifacts(tmp_path):
    import asyncio

    from app.commander import CommanderConfig, InvestmentBodyService

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
    body.controller.last_cycle_meta = {
        'cycle_id': 7,
        'cutoff_date': '20240201',
        'stage': 'selection',
        'requested_data_mode': 'live',
        'effective_data_mode': 'offline',
        'llm_mode': 'live',
        'degraded': False,
    }
    body.controller.run_training_cycle = lambda: None

    payload = asyncio.run(body.run_cycles(rounds=1, force_mock=False))

    assert payload['results'][0]['status'] == 'no_data'
    assert payload['results'][0]['cycle_id'] == 7
    assert payload['results'][0]['artifacts']['cycle_result_path'].endswith('cycle_7.json')


@pytest.mark.asyncio
async def test_execute_training_plan_rejects_invalid_json_artifact(tmp_path):
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
    runtime._ensure_runtime_storage()
    broken_path = runtime._training_plan_path("broken")
    broken_path.write_text("{not-json}", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid training plan json"):
        await runtime.execute_training_plan("broken")



def test_status_invalid_detail_falls_back_to_fast(tmp_path):
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

    payload = runtime.status(detail="unexpected-mode")

    assert payload["detail_mode"] == "fast"
    assert payload["entrypoint"]["runtime_tool"] == "invest_quick_status"
    assert payload["orchestration"]["workflow"][1] == "status_read"


def test_read_runtime_lock_payload_handles_invalid_json_with_warning(tmp_path, caplog):
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
    cfg.runtime_lock_file.parent.mkdir(parents=True, exist_ok=True)

    cfg.runtime_lock_file.write_text('{broken', encoding='utf-8')
    with caplog.at_level("WARNING"):
        assert runtime._read_runtime_lock_payload() == {}
    assert "Invalid runtime lock payload" in caplog.text

    caplog.clear()
    cfg.runtime_lock_file.write_text('[]', encoding='utf-8')
    with caplog.at_level("WARNING"):
        assert runtime._read_runtime_lock_payload() == {}
    assert "Runtime lock payload must be a JSON object" in caplog.text


def test_persist_state_uses_lightweight_snapshot(tmp_path, monkeypatch):
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

    def _boom(*args, **kwargs):
        raise AssertionError("heavy status path should not run during persist")

    monkeypatch.setattr(runtime, "status", _boom)
    monkeypatch.setattr(runtime, "list_training_plans", _boom)
    monkeypatch.setattr(runtime, "list_training_runs", _boom)
    monkeypatch.setattr(runtime, "list_training_evaluations", _boom)
    monkeypatch.setattr(runtime, "_collect_data_status", lambda detail_mode: {"status": "ok", "detail_mode": detail_mode})

    runtime._persist_state()

    payload = json.loads(cfg.state_file.read_text(encoding="utf-8"))
    assert payload["detail_mode"] == "fast"
    assert payload["training_lab"]["latest_plans"] == []
    assert payload["training_lab"]["latest_runs"] == []
    assert payload["training_lab"]["latest_evaluations"] == []
    assert payload["data"]["detail_mode"] == "fast"
    assert "entrypoint" not in payload


@pytest.mark.asyncio
async def test_ask_failure_records_error_last_task(tmp_path, monkeypatch):
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

    async def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(runtime.brain, "process_direct", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        await runtime.ask("hello", session_key="test:ask-fail")

    state, current_task, last_task = runtime._snapshot_runtime_fields()
    payload = json.loads(cfg.state_file.read_text(encoding="utf-8"))

    assert state == "initialized"
    assert current_task is None
    assert last_task["type"] == "ask"
    assert last_task["status"] == "error"
    assert payload["runtime"]["current_task"] is None
    assert payload["runtime"]["last_task"]["status"] == "error"


@pytest.mark.asyncio
async def test_ask_finished_audit_captures_structured_reply_metadata(tmp_path, monkeypatch):
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

    async def _ok(*args, **kwargs):
        return json.dumps(
            {
                "status": "ok",
                "reply": "已完成",
                "protocol": {
                    "schema_version": "bounded_workflow.v2",
                    "domain": "runtime",
                    "operation": "status",
                },
                "entrypoint": {
                    "kind": "commander_builtin_intent",
                    "intent": "runtime_status",
                },
                "next_action": {
                    "kind": "continue",
                },
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(runtime.brain, "process_direct", _ok)

    await runtime.ask("请汇总系统状态", session_key="test:ask-metadata", channel="api", chat_id="chat")

    audit_rows = [
        json.loads(line)
        for line in runtime.memory.audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    finished = next(row for row in audit_rows if row["event"] == "ask_finished")
    assert finished["payload"]["channel"] == "api"
    assert finished["payload"]["message_length"] == len("请汇总系统状态")
    assert finished["payload"]["domain"] == "runtime"
    assert finished["payload"]["operation"] == "status"
    assert finished["payload"]["intent"] == "runtime_status"
    assert finished["payload"]["next_action_kind"] == "continue"


def test_reload_strategies_resets_runtime_to_idle_and_persists_last_task(tmp_path):
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

    out = runtime.reload_strategies()
    state, current_task, last_task = runtime._snapshot_runtime_fields()
    payload = json.loads(cfg.state_file.read_text(encoding="utf-8"))

    assert out["status"] == "ok"
    assert state == "idle"
    assert current_task is None
    assert last_task["type"] == "reload_strategies"
    assert last_task["status"] == "ok"
    assert last_task["gene_count"] == out["count"]
    assert payload["runtime"]["state"] == "idle"
    assert payload["runtime"]["last_task"]["gene_count"] == out["count"]


def test_runtime_restores_persisted_runtime_and_body_state(tmp_path):
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
    runtime._update_runtime_fields(  # pylint: disable=protected-access
        state="idle",
        current_task=None,
        last_task={"type": "training", "status": "ok", "source": "direct"},
    )
    runtime.body.total_cycles = 3
    runtime.body.success_cycles = 1
    runtime.body.no_data_cycles = 1
    runtime.body.failed_cycles = 1
    runtime.body.last_result = {"status": "no_data", "cycle_id": 3}
    runtime.body.last_run_at = "2026-03-12T20:08:02"
    runtime.body.training_state = "idle"
    runtime.body.last_completed_task = {"type": "training", "last_status": "no_data"}
    runtime._persist_state()  # pylint: disable=protected-access

    restored = CommanderRuntime(cfg)
    status = restored.status(detail="fast")

    assert status["runtime"]["last_task"]["type"] == "training"
    assert status["body"]["total_cycles"] == 3
    assert status["body"]["success_cycles"] == 1
    assert status["body"]["no_data_cycles"] == 1
    assert status["body"]["failed_cycles"] == 1
    assert status["body"]["last_result"]["status"] == "no_data"
    assert status["body"]["last_completed_task"]["last_status"] == "no_data"


@pytest.mark.asyncio
async def test_mutating_workflow_gate_includes_coverage_gap_reasons(tmp_path):
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
    payload = runtime.status()
    assert "incomplete_plan_coverage" in payload["task_bus"]["gate"]["reasons"]
    assert payload["task_bus"]["gate"]["writes_state"] is False



def test_training_evaluation_summary_rejects_when_research_feedback_gate_fails(tmp_path):
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
        rounds=2,
        mock=True,
        optimization={'promotion_gate': {
            'min_samples': 2,
            'min_avg_strategy_score': 0.6,
            'research_feedback': {
                'min_sample_count': 5,
                'blocked_biases': ['tighten_risk', 'recalibrate_probability'],
                'max_brier_like_direction_score': 0.25,
                'horizons': {'T+20': {'min_hit_rate': 0.45, 'max_invalidation_rate': 0.30}},
            },
        }},
    )
    summary = runtime._build_training_evaluation_summary(
        {
            'status': 'completed',
            'results': [
                {
                    'status': 'ok',
                    'cycle_id': 1,
                    'cutoff_date': '20240228',
                    'model_name': 'momentum',
                    'config_name': 'momentum_v1',
                    'return_pct': 0.9,
                    'benchmark_passed': True,
                    'strategy_scores': {'overall_score': 0.72},
                    'research_feedback': {
                        'sample_count': 7,
                        'recommendation': {'bias': 'tighten_risk', 'summary': 'tighten risk'},
                        'horizons': {'T+20': {'hit_rate': 0.30, 'invalidation_rate': 0.42}},
                        'brier_like_direction_score': 0.31,
                    },
                },
                {
                    'status': 'ok',
                    'cycle_id': 2,
                    'cutoff_date': '20240315',
                    'model_name': 'momentum',
                    'config_name': 'momentum_v1',
                    'return_pct': 0.8,
                    'benchmark_passed': True,
                    'strategy_scores': {'overall_score': 0.68},
                    'research_feedback': {
                        'sample_count': 8,
                        'recommendation': {'bias': 'tighten_risk', 'summary': 'tighten risk'},
                        'horizons': {'T+20': {'hit_rate': 0.28, 'invalidation_rate': 0.40}},
                        'brier_like_direction_score': 0.29,
                    },
                },
            ],
        },
        plan=plan,
        run_id='run_feedback_reject',
    )
    assert summary['promotion']['verdict'] == 'rejected'
    assert summary['promotion']['research_feedback']['passed'] is False
    assert summary['promotion']['research_feedback']['latest_feedback']['bias'] == 'tighten_risk'
    assert any(check['name'] == 'research_feedback.blocked_biases' and check['passed'] is False for check in summary['promotion']['checks'])


def test_training_evaluation_summary_promotes_when_research_feedback_gate_passes(tmp_path):
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
        rounds=2,
        mock=True,
        optimization={'promotion_gate': {
            'min_samples': 2,
            'min_avg_strategy_score': 0.6,
            'research_feedback': {
                'min_sample_count': 5,
                'blocked_biases': ['tighten_risk', 'recalibrate_probability'],
                'max_brier_like_direction_score': 0.25,
                'horizons': {'T+20': {'min_hit_rate': 0.45, 'max_invalidation_rate': 0.30}},
            },
        }},
    )
    summary = runtime._build_training_evaluation_summary(
        {
            'status': 'completed',
            'results': [
                {
                    'status': 'ok',
                    'cycle_id': 1,
                    'cutoff_date': '20240228',
                    'model_name': 'momentum',
                    'config_name': 'momentum_v1',
                    'return_pct': 0.9,
                    'benchmark_passed': True,
                    'strategy_scores': {'overall_score': 0.72},
                    'research_feedback': {
                        'sample_count': 7,
                        'recommendation': {'bias': 'maintain', 'summary': 'maintain'},
                        'horizons': {'T+20': {'hit_rate': 0.54, 'invalidation_rate': 0.18}},
                        'brier_like_direction_score': 0.18,
                    },
                },
                {
                    'status': 'ok',
                    'cycle_id': 2,
                    'cutoff_date': '20240315',
                    'model_name': 'momentum',
                    'config_name': 'momentum_v1',
                    'return_pct': 0.8,
                    'benchmark_passed': True,
                    'strategy_scores': {'overall_score': 0.68},
                    'research_feedback': {
                        'sample_count': 8,
                        'recommendation': {'bias': 'maintain', 'summary': 'maintain'},
                        'horizons': {'T+20': {'hit_rate': 0.58, 'invalidation_rate': 0.16}},
                        'brier_like_direction_score': 0.16,
                    },
                },
            ],
        },
        plan=plan,
        run_id='run_feedback_promote',
    )
    assert summary['promotion']['verdict'] == 'promoted'
    assert summary['promotion']['research_feedback']['passed'] is True
    assert summary['promotion']['research_feedback']['latest_feedback']['bias'] == 'maintain'
    assert any(check['name'] == 'research_feedback.blocked_biases' and check['passed'] is True for check in summary['promotion']['checks'])


def test_confirmation_workflow_message_includes_human_readable_gate_reasons(tmp_path):
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
    payload = runtime.build_training_confirmation_required(rounds=2, mock=False)

    assert payload["status"] == "confirmation_required"
    assert payload["feedback"]["requires_confirmation"] is True
    assert "confirm=true" in payload["message"]
    assert "当前操作仍需要人工确认" in payload["feedback"]["reason_texts"]
    assert "confirmation_required" in payload["feedback"]["reason_codes"]



def test_create_training_plan_persists_default_research_feedback_gate(tmp_path):
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

    plan = runtime.create_training_plan(
        rounds=1,
        mock=True,
        optimization={'promotion_gate': {'min_samples': 2}},
    )

    promotion_gate = plan['optimization']['promotion_gate']
    gate = promotion_gate['research_feedback']
    assert promotion_gate['min_samples'] == 2
    assert gate['min_sample_count'] == 5
    assert gate['blocked_biases'] == ['tighten_risk', 'recalibrate_probability']
    assert gate['horizons']['T+20']['min_hit_rate'] == 0.45
    assert plan['guardrails']['promotion_gate']['research_feedback']['enabled'] is True
    assert plan['guardrails']['promotion_gate']['research_feedback']['policy_source']['mode'] == 'default_injected'
    assert '默认启用 research_feedback 校准门' in plan['guardrails']['promotion_gate']['research_feedback']['summary']

    saved = runtime.get_training_plan(plan['plan_id'])
    assert saved['optimization']['promotion_gate']['research_feedback']['horizons']['T+20']['max_invalidation_rate'] == 0.30
    assert saved['optimization']['promotion_gate']['research_feedback']['horizons']['T+20']['min_interval_hit_rate'] == 0.40
    assert saved['guardrails']['promotion_gate']['research_feedback']['policy_source']['mode'] == 'default_injected'
