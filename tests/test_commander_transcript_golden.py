import json

import pytest

from brain.schema_contract import BOUNDED_WORKFLOW_SCHEMA_VERSION, TASK_BUS_SCHEMA_VERSION
from commander import CommanderConfig, CommanderRuntime
from market_data.repository import MarketDataRepository


@pytest.fixture()
def runtime_with_db(tmp_path, monkeypatch):
    db_path = tmp_path / 'market.db'
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([
        {'code': 'sh.600001', 'name': 'FooBank', 'list_date': '20200101', 'source': 'test'}
    ])
    repo.upsert_daily_bars([
        {
            'code': 'sh.600001',
            'trade_date': f'202401{day:02d}',
            'open': 10 + day * 0.1,
            'high': 10.5 + day * 0.1,
            'low': 9.5 + day * 0.1,
            'close': 10 + day * 0.12,
            'volume': 1000 + day * 10,
            'amount': 5000 + day * 100,
            'pct_chg': 0.5,
            'turnover': 1.2,
            'source': 'test',
        }
        for day in range(1, 31)
    ])
    monkeypatch.setenv('INVEST_DB_PATH', str(db_path))

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
    return runtime


def _normalize_payload(payload):
    normalized = {
        'entrypoint': {
            'agent_kind': payload.get('entrypoint', {}).get('agent_kind'),
            'domain': payload.get('entrypoint', {}).get('domain'),
            'runtime_tool': payload.get('entrypoint', {}).get('runtime_tool'),
            'service': payload.get('entrypoint', {}).get('service'),
        },
        'orchestration': {
            'workflow': payload.get('orchestration', {}).get('workflow'),
            'mode': payload.get('orchestration', {}).get('mode'),
            'step_count': payload.get('orchestration', {}).get('step_count'),
            'policy': {
                'fixed_boundary': payload.get('orchestration', {}).get('policy', {}).get('fixed_boundary'),
                'fixed_workflow': payload.get('orchestration', {}).get('policy', {}).get('fixed_workflow'),
                'writes_state': payload.get('orchestration', {}).get('policy', {}).get('writes_state'),
                'confirmation_gate': payload.get('orchestration', {}).get('policy', {}).get('confirmation_gate'),
                'tool_catalog_scope': payload.get('orchestration', {}).get('policy', {}).get('tool_catalog_scope'),
                'workflow_mode': payload.get('orchestration', {}).get('policy', {}).get('workflow_mode'),
            },
            'phase_stats': payload.get('orchestration', {}).get('phase_stats'),
        },
        'task_bus': {
            'schema_version': payload.get('task_bus', {}).get('schema_version'),
            'intent': payload.get('task_bus', {}).get('planner', {}).get('intent'),
            'operation': payload.get('task_bus', {}).get('planner', {}).get('operation'),
            'mode': payload.get('task_bus', {}).get('planner', {}).get('mode'),
            'recommended_tools': payload.get('task_bus', {}).get('planner', {}).get('plan_summary', {}).get('recommended_tools'),
            'used_tools': payload.get('task_bus', {}).get('audit', {}).get('used_tools'),
            'requires_confirmation': payload.get('task_bus', {}).get('gate', {}).get('requires_confirmation'),
            'confirmation_state': payload.get('task_bus', {}).get('gate', {}).get('confirmation', {}).get('state'),
        },
        'protocol': payload.get('protocol'),
        'feedback': {
            'summary': payload.get('feedback', {}).get('summary'),
        },
        'next_action': {
            'kind': payload.get('next_action', {}).get('kind'),
            'requires_confirmation': payload.get('next_action', {}).get('requires_confirmation'),
        },
    }
    for key in ('status', 'detail_mode', 'intent', 'pending'):
        if key in payload:
            normalized[key] = payload.get(key)
    if 'strategy' in payload:
        normalized['strategy'] = {
            'name': payload.get('strategy', {}).get('name'),
            'required_tools': payload.get('strategy', {}).get('required_tools'),
            'analysis_steps': payload.get('strategy', {}).get('analysis_steps'),
        }
    if 'resolved' in payload:
        normalized['resolved'] = {
            'code': payload.get('resolved', {}).get('code'),
            'name': payload.get('resolved', {}).get('name'),
        }
    return normalized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('query', 'expected'),
    [
        (
            '请看看系统状态',
            {
                'detail_mode': 'fast',
                'entrypoint': {
                    'agent_kind': 'bounded_runtime_agent',
                    'domain': 'runtime',
                    'runtime_tool': 'invest_quick_status',
                    'service': None,
                },
                'orchestration': {
                    'workflow': ['runtime_scope_resolve', 'status_read', 'finalize'],
                    'mode': 'bounded_readonly_workflow',
                    'step_count': None,
                    'policy': {
                        'fixed_boundary': True,
                        'fixed_workflow': True,
                        'writes_state': False,
                        'confirmation_gate': None,
                        'tool_catalog_scope': 'runtime_domain',
                        'workflow_mode': None,
                    },
                    'phase_stats': {'detail_mode': 'fast', 'event_count': 2},
                },
                'task_bus': {
                    'schema_version': TASK_BUS_SCHEMA_VERSION,
                    'intent': 'runtime_status',
                    'operation': 'invest_quick_status',
                    'mode': 'builtin_intent',
                    'recommended_tools': ['invest_quick_status', 'invest_events_summary', 'invest_runtime_diagnostics'],
                    'used_tools': ['invest_quick_status'],
                    'requires_confirmation': False,
                    'confirmation_state': 'not_applicable',
                },
                'protocol': {
                    'schema_version': BOUNDED_WORKFLOW_SCHEMA_VERSION,
                    'task_bus_schema_version': TASK_BUS_SCHEMA_VERSION,
                    'plan_schema_version': 'task_plan.v2',
                    'coverage_schema_version': 'task_coverage.v2',
                    'artifact_taxonomy_schema_version': 'artifact_taxonomy.v2',
                    'domain': 'runtime',
                    'operation': 'status',
                },
                'feedback': {'summary': '分析已完成，但证据覆盖仍不完整，结论应谨慎使用。'},
                'next_action': {'kind': 'inspect_artifact', 'requires_confirmation': False},
            },
        ),
        (
            '用缠论分析 FooBank',
            {
                'status': 'ok',
                'entrypoint': {
                    'agent_kind': None,
                    'domain': None,
                    'runtime_tool': 'invest_ask_stock',
                    'service': 'StockAnalysisService',
                },
                'orchestration': {
                    'workflow': ['yaml_strategy_loaded', 'yaml_plan_execute', 'finalize'],
                    'mode': 'yaml_react_like',
                    'step_count': 5,
                    'policy': {
                        'fixed_boundary': True,
                        'fixed_workflow': True,
                        'writes_state': None,
                        'confirmation_gate': None,
                        'tool_catalog_scope': 'strategy_restricted',
                        'workflow_mode': 'llm_react_with_yaml_gap_fill',
                    },
                    'phase_stats': {
                        'llm_react_steps': 0,
                        'yaml_gap_fill_steps': 0,
                        'yaml_planned_steps': 5,
                        'total_steps': 5,
                    },
                },
                'task_bus': {
                    'schema_version': TASK_BUS_SCHEMA_VERSION,
                    'intent': 'stock_analysis',
                    'operation': 'ask_stock',
                    'mode': 'yaml_react_like',
                    'recommended_tools': ['get_daily_history', 'get_indicator_snapshot', 'analyze_support_resistance', 'get_capital_flow', 'get_realtime_quote'],
                    'used_tools': ['get_daily_history', 'get_indicator_snapshot', 'analyze_support_resistance', 'get_capital_flow', 'get_realtime_quote'],
                    'requires_confirmation': False,
                    'confirmation_state': 'not_applicable',
                },
                'protocol': None,
                'feedback': {'summary': '当前任务已完成，计划与参数覆盖满足预期。'},
                'next_action': {'kind': 'inspect_artifact', 'requires_confirmation': False},
                'strategy': {
                    'name': 'chan_theory',
                    'required_tools': ['get_daily_history', 'get_indicator_snapshot', 'analyze_support_resistance', 'get_capital_flow', 'get_realtime_quote'],
                    'analysis_steps': ['获取近60日日线', '识别指标状态', '判断支撑阻力', '观察资金确认', '结合最新价格输出结论'],
                },
                'resolved': {
                    'code': 'sh.600001',
                    'name': 'FooBank',
                },
            },
        ),
        (
            '请帮我真实训练2轮',
            {
                'status': 'confirmation_required',
                'pending': {'rounds': 2, 'mock': False},
                'entrypoint': {
                    'agent_kind': 'bounded_training_agent',
                    'domain': 'training',
                    'runtime_tool': 'invest_train',
                    'service': None,
                },
                'orchestration': {
                    'workflow': ['training_scope_resolve', 'gate_confirmation', 'finalize'],
                    'mode': 'bounded_mutating_workflow',
                    'step_count': None,
                    'policy': {
                        'fixed_boundary': True,
                        'fixed_workflow': True,
                        'writes_state': True,
                        'confirmation_gate': True,
                        'tool_catalog_scope': 'training_domain',
                        'workflow_mode': None,
                    },
                    'phase_stats': {'rounds': 2, 'mock': False, 'requires_confirmation': True},
                },
                'task_bus': {
                    'schema_version': TASK_BUS_SCHEMA_VERSION,
                    'intent': 'training_execution',
                    'operation': 'invest_train',
                    'mode': 'builtin_intent',
                    'recommended_tools': ['invest_quick_test', 'invest_training_plan_create', 'invest_training_plan_execute', 'invest_training_evaluations_list', 'invest_training_lab_summary'],
                    'used_tools': ['invest_train'],
                    'requires_confirmation': True,
                    'confirmation_state': 'pending_confirmation',
                },
                'protocol': {
                    'schema_version': BOUNDED_WORKFLOW_SCHEMA_VERSION,
                    'task_bus_schema_version': TASK_BUS_SCHEMA_VERSION,
                    'plan_schema_version': 'task_plan.v2',
                    'coverage_schema_version': 'task_coverage.v2',
                    'artifact_taxonomy_schema_version': 'artifact_taxonomy.v2',
                    'domain': 'training',
                    'operation': 'train_once',
                },
                'feedback': {'summary': '当前任务仍需人工确认后才能视为审计闭环完成。'},
                'next_action': {'kind': 'confirm', 'requires_confirmation': True},
            },
        ),
    ],
)
async def test_commander_transcript_golden(runtime_with_db, query, expected):
    result = await runtime_with_db.ask(query, session_key='test:golden')
    payload = json.loads(result)
    assert _normalize_payload(payload) == expected
