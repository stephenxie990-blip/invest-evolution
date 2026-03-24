import pytest

from invest_evolution.agent_runtime.planner import BOUNDED_WORKFLOW_SCHEMA_VERSION, TASK_BUS_SCHEMA_VERSION
from invest_evolution.agent_runtime.presentation import build_transcript_snapshot
from invest_evolution.application.commander_main import CommanderConfig, CommanderRuntime
import invest_evolution.application.commander.ops as commander_ops_module


@pytest.fixture()
def runtime_with_mutation_stubs(tmp_path, monkeypatch):
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

    monkeypatch.setattr(commander_ops_module, 'update_runtime_paths_payload', lambda **kwargs: {'status': 'ok', 'updated': ['training_output_dir'], 'paths': {'training_output_dir': '/tmp/train'}})
    monkeypatch.setattr(commander_ops_module, 'update_control_plane_payload', lambda **kwargs: {'status': 'ok', 'updated': ['llm.bindings.controller.main'], 'control_plane': {'llm': {'bindings': {'controller.main': 'foo'}}}})
    monkeypatch.setattr(commander_ops_module, 'update_evolution_config_payload', lambda **kwargs: {'status': 'ok', 'updated': ['data_source'], 'config': {'data_source': 'mock'}})
    monkeypatch.setattr(commander_ops_module, 'update_agent_prompt_payload', lambda **kwargs: {'status': 'ok', 'updated': ['researcher'], 'items': [{'name': 'researcher', 'system_prompt': 'x'}]})
    monkeypatch.setattr(commander_ops_module, 'trigger_data_download', lambda: {'status': 'started', 'message': '后台同步已启动'})
    return runtime


def _normalize(payload):
    return build_transcript_snapshot(
        payload,
        top_level_keys=("status", "pending"),
        include_feedback=False,
        include_next_action=False,
        include_recommended_args=True,
        include_task_bus_coverage=True,
        include_gate_decision=True,
        include_tool_count=True,
        include_orchestration_step_count=False,
        include_entrypoint_service=False,
        orchestration_policy_keys=("writes_state", "confirmation_gate", "fixed_boundary", "fixed_workflow"),
    )


@pytest.mark.parametrize(
    ('factory', 'expected'),
    [
        (
            lambda runtime: runtime.update_runtime_paths({'training_output_dir': '/tmp/train'}, confirm=False),
            {
                'status': 'confirmation_required',
                'pending': {'patch': {'training_output_dir': '/tmp/train'}},
                'entrypoint': {'agent_kind': 'bounded_config_agent', 'domain': 'config', 'runtime_tool': 'invest_runtime_paths_update'},
                'orchestration': {'workflow': ['config_scope_resolve', 'gate_confirmation', 'finalize'], 'mode': 'bounded_mutating_workflow', 'phase_stats': {'pending_key_count': 1, 'requires_confirmation': True}, 'policy': {'writes_state': True, 'confirmation_gate': True, 'fixed_boundary': True, 'fixed_workflow': True}},
                'protocol': {'schema_version': BOUNDED_WORKFLOW_SCHEMA_VERSION, 'task_bus_schema_version': TASK_BUS_SCHEMA_VERSION, 'plan_schema_version': 'task_plan.v2', 'coverage_schema_version': 'task_coverage.v2', 'artifact_taxonomy_schema_version': 'artifact_taxonomy.v2', 'domain': 'config', 'operation': 'update_runtime_paths'},
                'task_bus': {'schema_version': TASK_BUS_SCHEMA_VERSION, 'intent': 'config_runtime_paths_update', 'operation': 'update_runtime_paths', 'mode': 'commander_runtime_method', 'recommended_tools': ['invest_runtime_paths_get', 'invest_runtime_paths_update'], 'recommended_args': [{}, {'confirm': False}], 'used_tools': [], 'decision': 'confirm', 'risk_level': 'high', 'writes_state': True, 'requires_confirmation': True, 'confirmation_state': 'pending_confirmation', 'tool_count': 0, 'planned_step_coverage': 0.0, 'parameterized_step_count': 1, 'covered_parameterized_step_ids': [], 'missing_parameterized_step_ids': ['step_02'], 'parameter_coverage': 0.0},
            },
        ),
        (
            lambda runtime: runtime.update_runtime_paths({'training_output_dir': '/tmp/train'}, confirm=True),
            {
                'status': 'ok',
                'entrypoint': {'agent_kind': 'bounded_config_agent', 'domain': 'config', 'runtime_tool': 'invest_runtime_paths_update'},
                'orchestration': {'workflow': ['config_scope_resolve', 'runtime_paths_write', 'finalize'], 'mode': 'bounded_mutating_workflow', 'phase_stats': {'updated_count': 1, 'confirmed': True}, 'policy': {'writes_state': True, 'confirmation_gate': True, 'fixed_boundary': True, 'fixed_workflow': True}},
                'protocol': {'schema_version': BOUNDED_WORKFLOW_SCHEMA_VERSION, 'task_bus_schema_version': TASK_BUS_SCHEMA_VERSION, 'plan_schema_version': 'task_plan.v2', 'coverage_schema_version': 'task_coverage.v2', 'artifact_taxonomy_schema_version': 'artifact_taxonomy.v2', 'domain': 'config', 'operation': 'update_runtime_paths'},
                'task_bus': {'schema_version': TASK_BUS_SCHEMA_VERSION, 'intent': 'config_runtime_paths_update', 'operation': 'update_runtime_paths', 'mode': 'commander_runtime_method', 'recommended_tools': ['invest_runtime_paths_get', 'invest_runtime_paths_update'], 'recommended_args': [{}, {'confirm': True}], 'used_tools': ['invest_runtime_paths_update'], 'decision': 'confirm', 'risk_level': 'high', 'writes_state': True, 'requires_confirmation': True, 'confirmation_state': 'pending_confirmation', 'tool_count': 1, 'planned_step_coverage': 0.5, 'parameterized_step_count': 1, 'covered_parameterized_step_ids': ['step_02'], 'missing_parameterized_step_ids': [], 'parameter_coverage': 1.0},
            },
        ),
        (
            lambda runtime: runtime.update_control_plane({'llm': {'bindings': {'controller.main': 'foo'}}}, confirm=False),
            {
                'status': 'confirmation_required',
                'pending': {'patch': {'llm': {'bindings': {'controller.main': 'foo'}}}},
                'entrypoint': {'agent_kind': 'bounded_config_agent', 'domain': 'config', 'runtime_tool': 'invest_control_plane_update'},
                'orchestration': {'workflow': ['config_scope_resolve', 'gate_confirmation', 'finalize'], 'mode': 'bounded_mutating_workflow', 'phase_stats': {'pending_key_count': 1, 'requires_confirmation': True, 'restart_required': True}, 'policy': {'writes_state': True, 'confirmation_gate': True, 'fixed_boundary': True, 'fixed_workflow': True}},
                'protocol': {'schema_version': BOUNDED_WORKFLOW_SCHEMA_VERSION, 'task_bus_schema_version': TASK_BUS_SCHEMA_VERSION, 'plan_schema_version': 'task_plan.v2', 'coverage_schema_version': 'task_coverage.v2', 'artifact_taxonomy_schema_version': 'artifact_taxonomy.v2', 'domain': 'config', 'operation': 'update_control_plane'},
                'task_bus': {'schema_version': TASK_BUS_SCHEMA_VERSION, 'intent': 'config_control_plane_update', 'operation': 'update_control_plane', 'mode': 'commander_runtime_method', 'recommended_tools': ['invest_control_plane_get', 'invest_control_plane_update', 'invest_evolution_config_get'], 'recommended_args': [{}, {'confirm': False}, {}], 'used_tools': [], 'decision': 'confirm', 'risk_level': 'high', 'writes_state': True, 'requires_confirmation': True, 'confirmation_state': 'pending_confirmation', 'tool_count': 0, 'planned_step_coverage': 0.0, 'parameterized_step_count': 1, 'covered_parameterized_step_ids': [], 'missing_parameterized_step_ids': ['step_02'], 'parameter_coverage': 0.0},
            },
        ),
        (
            lambda runtime: runtime.update_agent_prompt(agent_name='researcher', system_prompt='x'),
            {
                'status': 'ok',
                'entrypoint': {'agent_kind': 'bounded_config_agent', 'domain': 'config', 'runtime_tool': 'invest_agent_prompts_update'},
                'orchestration': {'workflow': ['config_scope_resolve', 'agent_prompt_write', 'finalize'], 'mode': 'bounded_mutating_workflow', 'phase_stats': {'agent_name': 'researcher', 'prompt_length': 1}, 'policy': {'writes_state': True, 'confirmation_gate': True, 'fixed_boundary': True, 'fixed_workflow': True}},
                'protocol': {'schema_version': BOUNDED_WORKFLOW_SCHEMA_VERSION, 'task_bus_schema_version': TASK_BUS_SCHEMA_VERSION, 'plan_schema_version': 'task_plan.v2', 'coverage_schema_version': 'task_coverage.v2', 'artifact_taxonomy_schema_version': 'artifact_taxonomy.v2', 'domain': 'config', 'operation': 'update_agent_prompt'},
                'task_bus': {'schema_version': TASK_BUS_SCHEMA_VERSION, 'intent': 'config_agent_prompt_update', 'operation': 'update_agent_prompt', 'mode': 'commander_runtime_method', 'recommended_tools': ['invest_agent_prompts_list', 'invest_agent_prompts_update'], 'recommended_args': [{}, {'agent_name': 'researcher'}], 'used_tools': ['invest_agent_prompts_update'], 'decision': 'confirm', 'risk_level': 'high', 'writes_state': True, 'requires_confirmation': True, 'confirmation_state': 'pending_confirmation', 'tool_count': 1, 'planned_step_coverage': 0.5, 'parameterized_step_count': 1, 'covered_parameterized_step_ids': ['step_02'], 'missing_parameterized_step_ids': [], 'parameter_coverage': 1.0},
            },
        ),
        (
            lambda runtime: runtime.trigger_data_download(confirm=False),
            {
                'status': 'confirmation_required',
                'entrypoint': {'agent_kind': 'bounded_data_agent', 'domain': 'data', 'runtime_tool': 'invest_data_download'},
                'orchestration': {'workflow': ['data_scope_resolve', 'gate_confirmation', 'finalize'], 'mode': 'bounded_mutating_workflow', 'phase_stats': {'requires_confirmation': True}, 'policy': {'writes_state': True, 'confirmation_gate': True, 'fixed_boundary': True, 'fixed_workflow': True}},
                'protocol': {'schema_version': BOUNDED_WORKFLOW_SCHEMA_VERSION, 'task_bus_schema_version': TASK_BUS_SCHEMA_VERSION, 'plan_schema_version': 'task_plan.v2', 'coverage_schema_version': 'task_coverage.v2', 'artifact_taxonomy_schema_version': 'artifact_taxonomy.v2', 'domain': 'data', 'operation': 'trigger_data_download'},
                'task_bus': {'schema_version': TASK_BUS_SCHEMA_VERSION, 'intent': 'trigger_data_download', 'operation': 'trigger_data_download', 'mode': 'commander_runtime_method', 'recommended_tools': ['invest_data_download', 'invest_data_status'], 'recommended_args': [{'action': 'status'}, {'action': 'trigger', 'confirm': False}, {'refresh': True}], 'used_tools': [], 'decision': 'confirm', 'risk_level': 'high', 'writes_state': True, 'requires_confirmation': True, 'confirmation_state': 'pending_confirmation', 'tool_count': 0, 'planned_step_coverage': 0.0, 'parameterized_step_count': 3, 'covered_parameterized_step_ids': [], 'missing_parameterized_step_ids': ['step_01', 'step_02', 'step_03'], 'parameter_coverage': 0.0},
            },
        ),
        (
            lambda runtime: runtime.trigger_data_download(confirm=True),
            {
                'status': 'started',
                'entrypoint': {'agent_kind': 'bounded_data_agent', 'domain': 'data', 'runtime_tool': 'invest_data_download'},
                'orchestration': {'workflow': ['data_scope_resolve', 'download_job_trigger', 'finalize'], 'mode': 'bounded_mutating_workflow', 'phase_stats': {'job_status': 'started', 'confirmed': True}, 'policy': {'writes_state': True, 'confirmation_gate': True, 'fixed_boundary': True, 'fixed_workflow': True}},
                'protocol': {'schema_version': BOUNDED_WORKFLOW_SCHEMA_VERSION, 'task_bus_schema_version': TASK_BUS_SCHEMA_VERSION, 'plan_schema_version': 'task_plan.v2', 'coverage_schema_version': 'task_coverage.v2', 'artifact_taxonomy_schema_version': 'artifact_taxonomy.v2', 'domain': 'data', 'operation': 'trigger_data_download'},
                'task_bus': {'schema_version': TASK_BUS_SCHEMA_VERSION, 'intent': 'trigger_data_download', 'operation': 'trigger_data_download', 'mode': 'commander_runtime_method', 'recommended_tools': ['invest_data_download', 'invest_data_status'], 'recommended_args': [{'action': 'status', 'job_status': 'started'}, {'action': 'trigger', 'confirm': True}, {'refresh': True}], 'used_tools': ['invest_data_download'], 'decision': 'confirm', 'risk_level': 'high', 'writes_state': True, 'requires_confirmation': True, 'confirmation_state': 'pending_confirmation', 'tool_count': 1, 'planned_step_coverage': 0.667, 'parameterized_step_count': 3, 'covered_parameterized_step_ids': ['step_02'], 'missing_parameterized_step_ids': ['step_01', 'step_03'], 'parameter_coverage': 0.333},
            },
        ),
    ],
)
def test_commander_mutating_workflow_golden(runtime_with_mutation_stubs, factory, expected):
    payload = factory(runtime_with_mutation_stubs)
    assert _normalize(payload) == expected


def test_update_evolution_config_requires_confirmation_for_manager_budget_weights(
    runtime_with_mutation_stubs,
):
    payload = runtime_with_mutation_stubs.update_evolution_config(
        {'manager_budget_weights': {'momentum': 1.0}},
        confirm=False,
    )

    assert payload['status'] == 'confirmation_required'
    assert payload['pending']['patch'] == {'manager_budget_weights': {'momentum': 1.0}}
    assert payload['protocol']['operation'] == 'update_evolution_config'
