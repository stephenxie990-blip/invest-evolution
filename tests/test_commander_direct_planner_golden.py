import pytest

from invest_evolution.agent_runtime.planner import BOUNDED_WORKFLOW_SCHEMA_VERSION, TASK_BUS_SCHEMA_VERSION
from invest_evolution.agent_runtime.presentation import build_transcript_snapshot
from invest_evolution.application.commander_main import CommanderConfig, CommanderRuntime


@pytest.fixture()
def runtime_with_direct_plans(tmp_path):
    cfg = CommanderConfig(
        workspace=tmp_path / 'workspace',
        playbook_dir=tmp_path / 'strategies',
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
    runtime._ensure_runtime_storage()
    return runtime


def _normalize(payload):
    snapshot = build_transcript_snapshot(
        payload,
        top_level_keys=(),
        include_feedback=False,
        include_next_action=False,
        include_recommended_args=True,
        include_task_bus_coverage=True,
        include_gate_decision=True,
        include_tool_count=True,
        include_orchestration_step_count=False,
        include_orchestration_phase_stats=False,
        include_entrypoint_service=False,
    )
    snapshot.pop("orchestration", None)
    return snapshot


@pytest.mark.parametrize(
    ('factory', 'expected'),
    [
        (
            lambda runtime: runtime.status(),
            {
                'entrypoint': {'agent_kind': 'bounded_runtime_agent', 'domain': 'runtime', 'runtime_tool': 'invest_quick_status'},
                'protocol': {
                    'schema_version': BOUNDED_WORKFLOW_SCHEMA_VERSION,
                    'task_bus_schema_version': TASK_BUS_SCHEMA_VERSION,
                    'plan_schema_version': 'task_plan.v2',
                    'coverage_schema_version': 'task_coverage.v2',
                    'artifact_taxonomy_schema_version': 'artifact_taxonomy.v2',
                    'domain': 'runtime',
                    'operation': 'status',
                },
                'task_bus': {
                    'schema_version': TASK_BUS_SCHEMA_VERSION,
                    'intent': 'runtime_status',
                    'operation': 'status',
                    'mode': 'commander_runtime_method',
                    'recommended_tools': ['invest_quick_status', 'invest_events_summary', 'invest_runtime_diagnostics'],
                    'recommended_args': [{'detail': 'fast'}, {'limit': 100}, {'event_limit': 50, 'memory_limit': 20}],
                    'used_tools': ['invest_quick_status'],
                    'tool_count': 1,
                    'planned_step_coverage': 0.333,
                    'parameterized_step_count': 3,
                    'covered_parameterized_step_ids': ['step_01'],
                    'missing_parameterized_step_ids': ['step_02', 'step_03'],
                    'parameter_coverage': 0.333,
                    'decision': 'allow',
                    'risk_level': 'low',
                    'writes_state': False,
                    'requires_confirmation': False,
                    'confirmation_state': 'not_applicable',
                },
            },
        ),
        (
            lambda runtime: runtime.get_control_plane(),
            {
                'entrypoint': {'agent_kind': 'bounded_config_agent', 'domain': 'config', 'runtime_tool': 'invest_control_plane_get'},
                'protocol': {
                    'schema_version': BOUNDED_WORKFLOW_SCHEMA_VERSION,
                    'task_bus_schema_version': TASK_BUS_SCHEMA_VERSION,
                    'plan_schema_version': 'task_plan.v2',
                    'coverage_schema_version': 'task_coverage.v2',
                    'artifact_taxonomy_schema_version': 'artifact_taxonomy.v2',
                    'domain': 'config',
                    'operation': 'get_control_plane',
                },
                'task_bus': {
                    'schema_version': TASK_BUS_SCHEMA_VERSION,
                    'intent': 'config_control_plane',
                    'operation': 'get_control_plane',
                    'mode': 'commander_runtime_method',
                    'recommended_tools': ['invest_control_plane_get', 'invest_evolution_config_get'],
                    'recommended_args': [{}, {}],
                    'used_tools': ['invest_control_plane_get'],
                    'tool_count': 1,
                    'planned_step_coverage': 0.5,
                    'parameterized_step_count': 0,
                    'covered_parameterized_step_ids': [],
                    'missing_parameterized_step_ids': [],
                    'parameter_coverage': 1.0,
                    'decision': 'allow',
                    'risk_level': 'low',
                    'writes_state': False,
                    'requires_confirmation': False,
                    'confirmation_state': 'not_applicable',
                },
            },
        ),
        (
            lambda runtime: runtime.get_data_status(refresh=False),
            {
                'entrypoint': {'agent_kind': 'bounded_data_agent', 'domain': 'data', 'runtime_tool': 'invest_data_status'},
                'protocol': {
                    'schema_version': BOUNDED_WORKFLOW_SCHEMA_VERSION,
                    'task_bus_schema_version': TASK_BUS_SCHEMA_VERSION,
                    'plan_schema_version': 'task_plan.v2',
                    'coverage_schema_version': 'task_coverage.v2',
                    'artifact_taxonomy_schema_version': 'artifact_taxonomy.v2',
                    'domain': 'data',
                    'operation': 'get_data_status',
                },
                'task_bus': {
                    'schema_version': TASK_BUS_SCHEMA_VERSION,
                    'intent': 'data_status',
                    'operation': 'get_data_status',
                    'mode': 'commander_runtime_method',
                    'recommended_tools': ['invest_data_status', 'invest_data_download'],
                    'recommended_args': [{'refresh': False}, {'action': 'status'}],
                    'used_tools': ['invest_data_status'],
                    'tool_count': 1,
                    'planned_step_coverage': 0.5,
                    'parameterized_step_count': 2,
                    'covered_parameterized_step_ids': ['step_01'],
                    'missing_parameterized_step_ids': ['step_02'],
                    'parameter_coverage': 0.5,
                    'decision': 'allow',
                    'risk_level': 'low',
                    'writes_state': False,
                    'requires_confirmation': False,
                    'confirmation_state': 'not_applicable',
                },
            },
        ),
        (
            lambda runtime: runtime.list_memory(query='alpha', limit=7),
            {
                'entrypoint': {'agent_kind': 'bounded_memory_agent', 'domain': 'memory', 'runtime_tool': 'invest_memory_list'},
                'protocol': {
                    'schema_version': BOUNDED_WORKFLOW_SCHEMA_VERSION,
                    'task_bus_schema_version': TASK_BUS_SCHEMA_VERSION,
                    'plan_schema_version': 'task_plan.v2',
                    'coverage_schema_version': 'task_coverage.v2',
                    'artifact_taxonomy_schema_version': 'artifact_taxonomy.v2',
                    'domain': 'memory',
                    'operation': 'list_memory',
                },
                'task_bus': {
                    'schema_version': TASK_BUS_SCHEMA_VERSION,
                    'intent': 'list_memory',
                    'operation': 'list_memory',
                    'mode': 'commander_runtime_method',
                    'recommended_tools': ['invest_memory_search', 'invest_memory_list'],
                    'recommended_args': [{'query': 'alpha', 'limit': 7}, {'limit': 7}],
                    'used_tools': ['invest_memory_list'],
                    'tool_count': 1,
                    'planned_step_coverage': 0.5,
                    'parameterized_step_count': 2,
                    'covered_parameterized_step_ids': ['step_02'],
                    'missing_parameterized_step_ids': ['step_01'],
                    'parameter_coverage': 0.5,
                    'decision': 'allow',
                    'risk_level': 'low',
                    'writes_state': False,
                    'requires_confirmation': False,
                    'confirmation_state': 'not_applicable',
                },
            },
        ),
        (
            lambda runtime: runtime.get_training_lab_summary(limit=5),
            {
                'entrypoint': {'agent_kind': 'bounded_training_agent', 'domain': 'training', 'runtime_tool': 'invest_training_lab_summary'},
                'protocol': {
                    'schema_version': BOUNDED_WORKFLOW_SCHEMA_VERSION,
                    'task_bus_schema_version': TASK_BUS_SCHEMA_VERSION,
                    'plan_schema_version': 'task_plan.v2',
                    'coverage_schema_version': 'task_coverage.v2',
                    'artifact_taxonomy_schema_version': 'artifact_taxonomy.v2',
                    'domain': 'training',
                    'operation': 'get_training_lab_summary',
                },
                'task_bus': {
                    'schema_version': TASK_BUS_SCHEMA_VERSION,
                    'intent': 'training_lab_summary',
                    'operation': 'get_training_lab_summary',
                    'mode': 'commander_runtime_method',
                    'recommended_tools': ['invest_training_lab_summary', 'invest_training_runs_list', 'invest_training_evaluations_list'],
                    'recommended_args': [{'limit': 5}, {'limit': 5}, {'limit': 5}],
                    'used_tools': ['invest_training_lab_summary'],
                    'tool_count': 1,
                    'planned_step_coverage': 0.333,
                    'parameterized_step_count': 3,
                    'covered_parameterized_step_ids': ['step_01'],
                    'missing_parameterized_step_ids': ['step_02', 'step_03'],
                    'parameter_coverage': 0.333,
                    'decision': 'allow',
                    'risk_level': 'low',
                    'writes_state': False,
                    'requires_confirmation': False,
                    'confirmation_state': 'not_applicable',
                },
            },
        ),
    ],
)
def test_commander_direct_planner_golden(runtime_with_direct_plans, factory, expected):
    payload = factory(runtime_with_direct_plans)
    assert _normalize(payload) == expected
