import pytest

from brain.schema_contract import BOUNDED_WORKFLOW_SCHEMA_VERSION, TASK_BUS_SCHEMA_VERSION
from commander import CommanderConfig, CommanderRuntime
import app.commander as commander_module


@pytest.fixture()
def runtime_with_mutation_stubs(tmp_path, monkeypatch):
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
    runtime._ensure_runtime_storage()

    monkeypatch.setattr(commander_module, 'update_runtime_paths_payload', lambda **kwargs: {'status': 'ok', 'updated': ['training_output_dir'], 'paths': {'training_output_dir': '/tmp/train'}})
    monkeypatch.setattr(commander_module, 'update_control_plane_payload', lambda **kwargs: {'status': 'ok', 'updated': ['llm.bindings.controller.main'], 'control_plane': {'llm': {'bindings': {'controller.main': 'foo'}}}})
    monkeypatch.setattr(commander_module, 'update_evolution_config_payload', lambda **kwargs: {'status': 'ok', 'updated': ['data_source'], 'config': {'data_source': 'mock'}})
    monkeypatch.setattr(commander_module, 'update_agent_prompt_payload', lambda **kwargs: {'status': 'ok', 'updated': ['researcher'], 'items': [{'name': 'researcher', 'system_prompt': 'x'}]})
    monkeypatch.setattr(commander_module, 'trigger_data_download', lambda: {'status': 'started', 'message': '后台同步已启动'})
    return runtime


def _normalize(payload):
    plan = payload.get('task_bus', {}).get('planner', {}).get('recommended_plan', [])
    normalized = {
        'status': payload.get('status'),
        'entrypoint': {
            'agent_kind': payload.get('entrypoint', {}).get('agent_kind'),
            'domain': payload.get('entrypoint', {}).get('domain'),
            'runtime_tool': payload.get('entrypoint', {}).get('runtime_tool'),
        },
        'orchestration': {
            'workflow': payload.get('orchestration', {}).get('workflow'),
            'mode': payload.get('orchestration', {}).get('mode'),
            'phase_stats': payload.get('orchestration', {}).get('phase_stats'),
            'policy': {
                'writes_state': payload.get('orchestration', {}).get('policy', {}).get('writes_state'),
                'confirmation_gate': payload.get('orchestration', {}).get('policy', {}).get('confirmation_gate'),
                'fixed_boundary': payload.get('orchestration', {}).get('policy', {}).get('fixed_boundary'),
                'fixed_workflow': payload.get('orchestration', {}).get('policy', {}).get('fixed_workflow'),
            },
        },
        'protocol': payload.get('protocol'),
        'task_bus': {
            'schema_version': payload.get('task_bus', {}).get('schema_version'),
            'intent': payload.get('task_bus', {}).get('planner', {}).get('intent'),
            'operation': payload.get('task_bus', {}).get('planner', {}).get('operation'),
            'mode': payload.get('task_bus', {}).get('planner', {}).get('mode'),
            'recommended_tools': payload.get('task_bus', {}).get('planner', {}).get('plan_summary', {}).get('recommended_tools'),
            'recommended_args': [step.get('args') for step in plan],
            'used_tools': payload.get('task_bus', {}).get('audit', {}).get('used_tools'),
            'decision': payload.get('task_bus', {}).get('gate', {}).get('decision'),
            'risk_level': payload.get('task_bus', {}).get('gate', {}).get('risk_level'),
            'writes_state': payload.get('task_bus', {}).get('gate', {}).get('writes_state'),
            'requires_confirmation': payload.get('task_bus', {}).get('gate', {}).get('requires_confirmation'),
            'confirmation_state': payload.get('task_bus', {}).get('gate', {}).get('confirmation', {}).get('state'),
            'tool_count': payload.get('task_bus', {}).get('audit', {}).get('tool_count'),
            'planned_step_coverage': payload.get('task_bus', {}).get('audit', {}).get('coverage', {}).get('planned_step_coverage'),
            'parameterized_step_count': payload.get('task_bus', {}).get('audit', {}).get('coverage', {}).get('parameterized_step_count'),
            'covered_parameterized_step_ids': payload.get('task_bus', {}).get('audit', {}).get('coverage', {}).get('covered_parameterized_step_ids'),
            'missing_parameterized_step_ids': payload.get('task_bus', {}).get('audit', {}).get('coverage', {}).get('missing_parameterized_step_ids'),
            'parameter_coverage': payload.get('task_bus', {}).get('audit', {}).get('coverage', {}).get('parameter_coverage'),
        },
    }
    if 'pending' in payload:
        normalized['pending'] = payload.get('pending')
    return normalized


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
