import asyncio
import json

import pytest

import web_server
from brain.schema_contract import (
    ARTIFACT_TAXONOMY_SCHEMA_VERSION,
    BOUNDED_WORKFLOW_SCHEMA_VERSION,
    COVERAGE_SCHEMA_VERSION,
    TASK_BUS_SCHEMA_VERSION,
)
from app.commander_support.observability import append_event_row
from commander import CommanderConfig, CommanderRuntime
from market_data.repository import MarketDataRepository


def _make_runtime(tmp_path, monkeypatch):
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


def _install_runtime(monkeypatch, runtime):
    monkeypatch.setattr(web_server, '_runtime', runtime)
    monkeypatch.setattr(web_server, '_loop', object())
    monkeypatch.setattr(web_server, '_run_async', lambda coro: asyncio.run(coro))


@pytest.fixture()
def client_with_runtime(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path, monkeypatch)
    _install_runtime(monkeypatch, runtime)
    return web_server.app.test_client(), runtime


def _assert_contract_headers(response, *, domain, operation):
    assert response.headers['X-Bounded-Workflow-Schema'] == BOUNDED_WORKFLOW_SCHEMA_VERSION
    assert response.headers['X-Task-Bus-Schema'] == TASK_BUS_SCHEMA_VERSION
    assert response.headers['X-Coverage-Schema'] == COVERAGE_SCHEMA_VERSION
    assert response.headers['X-Artifact-Taxonomy-Schema'] == ARTIFACT_TAXONOMY_SCHEMA_VERSION
    assert response.headers['X-Commander-Domain'] == domain
    assert response.headers['X-Commander-Operation'] == operation


def test_api_status_emits_contract_headers(client_with_runtime):
    client, _runtime = client_with_runtime

    res = client.get('/api/status')

    assert res.status_code == 200
    _assert_contract_headers(res, domain='runtime', operation='status')


def test_api_status_view_human_returns_plain_text(client_with_runtime):
    client, _runtime = client_with_runtime

    res = client.get('/api/status?view=human')

    assert res.status_code == 200
    assert res.mimetype == 'text/plain'
    text = res.get_data(as_text=True)
    assert '结论：' in text
    assert '现状：' in text


def test_api_events_summary_emits_contract_headers(client_with_runtime):
    client, runtime = client_with_runtime
    append_event_row(
        runtime.cfg.runtime_events_path,
        'routing_decided',
        {'current_model': 'deepseek-chat', 'reasoning': 'test'},
        source='runtime',
    )

    res = client.get('/api/events/summary')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['status'] == 'ok'
    assert payload['summary']['count'] == 1
    assert len(payload['items']) == 1
    _assert_contract_headers(res, domain='runtime', operation='get_events_summary')


def test_api_events_summary_view_human_returns_plain_text(client_with_runtime):
    client, runtime = client_with_runtime
    append_event_row(
        runtime.cfg.runtime_events_path,
        'routing_decided',
        {'current_model': 'deepseek-chat', 'reasoning': 'test'},
        source='runtime',
    )

    res = client.get('/api/events/summary?view=human')

    assert res.status_code == 200
    assert res.mimetype == 'text/plain'
    text = res.get_data(as_text=True)
    assert '结论：' in text
    assert '条目数：1' in text
    assert '事件数：1' in text or '现状：' in text


def test_api_events_summary_rejects_unknown_view(client_with_runtime):
    client, _runtime = client_with_runtime

    res = client.get('/api/events/summary?view=xml')

    assert res.status_code == 400
    assert 'view must be one of' in res.get_json()['error']


def test_api_lab_status_quick_emits_contract_headers_for_snapshot_payload(client_with_runtime):
    client, _runtime = client_with_runtime

    res = client.get('/api/lab/status/quick')

    assert res.status_code == 200
    assert res.get_json()['mode'] == 'quick'
    _assert_contract_headers(res, domain='runtime', operation='status')


def test_api_data_status_emits_contract_headers(client_with_runtime):
    client, _runtime = client_with_runtime

    res = client.get('/api/data/status')

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['entrypoint']['agent_kind'] == 'bounded_data_agent'
    _assert_contract_headers(res, domain='data', operation='get_data_status')


def test_api_data_status_view_human_returns_plain_text(client_with_runtime):
    client, _runtime = client_with_runtime

    res = client.get('/api/data/status?view=human')

    assert res.status_code == 200
    assert res.mimetype == 'text/plain'
    assert '结论：' in res.get_data(as_text=True)


def test_api_investment_models_view_human_returns_plain_text(client_with_runtime):
    client, _runtime = client_with_runtime

    res = client.get('/api/investment-models?view=human')

    assert res.status_code == 200
    assert res.mimetype == 'text/plain'
    text = res.get_data(as_text=True)
    assert '结论：' in text
    assert '条目数：' in text


def test_api_control_plane_view_human_returns_plain_text(client_with_runtime):
    client, _runtime = client_with_runtime

    res = client.get('/api/control_plane?view=human')

    assert res.status_code == 200
    assert res.mimetype == 'text/plain'
    text = res.get_data(as_text=True)
    assert '结论：' in text
    assert '默认模型提供方：' in text or '风险提示：' in text


def test_api_runtime_paths_view_human_returns_plain_text(client_with_runtime):
    client, _runtime = client_with_runtime

    res = client.get('/api/runtime_paths?view=human')

    assert res.status_code == 200
    assert res.mimetype == 'text/plain'
    text = res.get_data(as_text=True)
    assert '结论：' in text
    assert '训练输出目录已配置：' in text or '现状：' in text


def test_api_training_plan_list_view_human_returns_plain_text(client_with_runtime):
    client, runtime = client_with_runtime
    runtime.create_training_plan(rounds=1, mock=True, goal='demo')

    res = client.get('/api/lab/training/plans?view=human')

    assert res.status_code == 200
    assert res.mimetype == 'text/plain'
    text = res.get_data(as_text=True)
    assert '结论：已返回 1 条记录。' in text
    assert '条目数：1' in text


def test_api_training_plan_get_view_human_returns_plain_text(client_with_runtime):
    client, runtime = client_with_runtime
    plan = runtime.create_training_plan(rounds=2, mock=True, goal='demo')

    res = client.get(f"/api/lab/training/plans/{plan['plan_id']}?view=human")

    assert res.status_code == 200
    assert res.mimetype == 'text/plain'
    text = res.get_data(as_text=True)
    assert f"训练计划：{plan['plan_id']}" in text
    assert '计划轮数：2' in text


def test_api_strategies_view_human_returns_plain_text(client_with_runtime):
    client, _runtime = client_with_runtime

    res = client.get('/api/strategies?view=human')

    assert res.status_code == 200
    assert res.mimetype == 'text/plain'
    text = res.get_data(as_text=True)
    assert '结论：已返回' in text
    assert '条目数：' in text


def test_api_cron_list_view_human_returns_plain_text(client_with_runtime):
    client, runtime = client_with_runtime
    runtime.cron.add_job(name='heartbeat', message='ping', every_sec=60, deliver=False, channel='web', to='commander')

    res = client.get('/api/cron?view=human')

    assert res.status_code == 200
    assert res.mimetype == 'text/plain'
    text = res.get_data(as_text=True)
    assert '结论：已返回 1 条记录。' in text
    assert '条目数：1' in text


def test_api_train_emits_contract_headers_for_training_workflow(client_with_runtime, monkeypatch):
    client, runtime = client_with_runtime

    async def fake_train_once(rounds=1, mock=False):
        return runtime.build_training_confirmation_required(rounds=rounds, mock=mock)

    runtime.train_once = fake_train_once

    res = client.post(
        '/api/train',
        data=json.dumps({'rounds': 2, 'mock': False}),
        content_type='application/json',
    )

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['status'] == 'confirmation_required'
    assert payload['pending'] == {'rounds': 2, 'mock': False}
    _assert_contract_headers(res, domain='training', operation='train_once')


def test_api_train_view_human_returns_plain_text(client_with_runtime, monkeypatch):
    client, runtime = client_with_runtime

    async def fake_train_once(rounds=1, mock=False):
        return runtime.build_training_confirmation_required(rounds=rounds, mock=mock)

    runtime.train_once = fake_train_once

    res = client.post(
        '/api/train?view=human',
        data=json.dumps({'rounds': 2, 'mock': False}),
        content_type='application/json',
    )

    assert res.status_code == 200
    assert res.mimetype == 'text/plain'
    text = res.get_data(as_text=True)
    assert '结论：' in text
    assert '风险提示：' in text


def test_api_data_download_runtime_requires_confirmation_and_headers(client_with_runtime):
    client, _runtime = client_with_runtime

    res = client.post(
        '/api/data/download',
        data=json.dumps({'confirm': False}),
        content_type='application/json',
    )

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['status'] == 'confirmation_required'
    _assert_contract_headers(res, domain='data', operation='trigger_data_download')


def test_api_data_download_runtime_confirmed_emits_contract_headers(client_with_runtime, monkeypatch):
    client, runtime = client_with_runtime

    monkeypatch.setattr(runtime, 'trigger_data_download', lambda confirm=False: {
        'status': 'started',
        'message': '后台同步已启动',
        'entrypoint': {'domain': 'data', 'agent_kind': 'bounded_data_agent', 'runtime_tool': 'invest_data_download'},
        'orchestration': {'workflow': ['data_scope_resolve', 'download_job_trigger', 'finalize'], 'policy': {'fixed_boundary': True, 'fixed_workflow': True, 'writes_state': True}},
        'protocol': {
            'schema_version': BOUNDED_WORKFLOW_SCHEMA_VERSION,
            'task_bus_schema_version': TASK_BUS_SCHEMA_VERSION,
            'plan_schema_version': 'task_plan.v2',
            'coverage_schema_version': COVERAGE_SCHEMA_VERSION,
            'artifact_taxonomy_schema_version': ARTIFACT_TAXONOMY_SCHEMA_VERSION,
            'domain': 'data',
            'operation': 'trigger_data_download',
        },
        'coverage': {'schema_version': COVERAGE_SCHEMA_VERSION},
        'artifact_taxonomy': {'schema_version': ARTIFACT_TAXONOMY_SCHEMA_VERSION},
    })

    res = client.post(
        '/api/data/download',
        data=json.dumps({'confirm': True}),
        content_type='application/json',
    )

    assert res.status_code == 200
    assert res.get_json()['status'] == 'started'
    _assert_contract_headers(res, domain='data', operation='trigger_data_download')


def test_api_chat_returns_structured_protocol_payload(client_with_runtime, monkeypatch):
    client, runtime = client_with_runtime

    seen = {}

    async def fake_ask(message, session_key=None, channel=None, chat_id=None, request_id=None):
        seen.update({
            'message': message,
            'session_key': session_key,
            'channel': channel,
            'chat_id': chat_id,
            'request_id': request_id,
        })
        return json.dumps({
            'status': 'ok',
            'reply': '已完成分析',
            'message': '已完成分析',
            'feedback': {'message': '已完成分析', 'summary': '当前任务已完成，计划与参数覆盖满足预期。', 'reason_codes': [], 'reason_texts': [], 'requires_confirmation': False, 'decision': 'allow', 'coverage': {'planned_step_coverage': 1.0, 'parameter_coverage': 1.0}},
            'next_action': {'kind': 'complete', 'label': '可继续下一步', 'description': '当前结果满足协议要求，可继续后续分析或执行下一任务。', 'requires_confirmation': False, 'suggested_params': {}},
            'task_bus': {'schema_version': 'task_bus.v2'},
        }, ensure_ascii=False)

    runtime.ask = fake_ask
    res = client.post('/api/chat', data=json.dumps({'message': '你好'}), content_type='application/json')
    assert res.status_code == 200
    payload = res.get_json()
    assert payload['reply'] == '已完成分析'
    assert payload['session_key'] == seen['session_key']
    assert payload['chat_id'] == seen['chat_id']
    assert payload['request_id'] == seen['request_id']
    assert seen['session_key'].startswith('api:chat:')
    assert seen['chat_id'].startswith('chat:')
    assert seen['request_id'].startswith('req:')
    assert seen['channel'] == 'api'
    assert payload['feedback']['summary'] == '当前任务已完成，计划与参数覆盖满足预期。'
    assert payload['next_action']['kind'] == 'complete'
    assert payload['task_bus']['schema_version'] == 'task_bus.v2'
    assert payload['human_reply'].startswith('结论：当前任务已完成，计划与参数覆盖满足预期。')
    assert payload['display']['available'] is True
    assert payload['display']['summary'] == '当前任务已完成，计划与参数覆盖满足预期。'


def test_api_chat_honors_explicit_session_identity(client_with_runtime):
    client, runtime = client_with_runtime

    seen = {}

    async def fake_ask(message, session_key=None, channel=None, chat_id=None, request_id=None):
        seen.update({'session_key': session_key, 'chat_id': chat_id, 'request_id': request_id})
        return json.dumps({'reply': 'ok'}, ensure_ascii=False)

    runtime.ask = fake_ask
    res = client.post(
        '/api/chat',
        data=json.dumps({'message': '继续', 'session_key': 'api:chat:portfolio-1', 'chat_id': 'portfolio-1'}),
        content_type='application/json',
    )

    assert res.status_code == 200
    payload = res.get_json()
    assert seen['session_key'] == 'api:chat:portfolio-1'
    assert seen['chat_id'] == 'portfolio-1'
    assert seen['request_id'].startswith('req:')
    assert payload['session_key'] == 'api:chat:portfolio-1'
    assert payload['chat_id'] == 'portfolio-1'
    assert payload['request_id'] == seen['request_id']


def test_api_chat_surfaces_human_receipt_when_available(client_with_runtime):
    client, runtime = client_with_runtime

    async def fake_ask(message, session_key=None, channel=None, chat_id=None, request_id=None):
        return json.dumps(
            {
                'status': 'ok',
                'reply': 'raw-reply',
                'message': 'raw-reply',
                'human_readable': {
                    'title': '系统运行摘要',
                    'summary': '系统可用',
                    'receipt_text': '结论：系统可用\\n建议动作：继续观察',
                    'sections': [{'label': '结论', 'text': '系统可用'}],
                    'suggested_actions': ['继续观察'],
                    'recommended_next_step': '继续观察',
                    'risk_level': 'low',
                },
            },
            ensure_ascii=False,
        )

    runtime.ask = fake_ask
    res = client.post('/api/chat', data=json.dumps({'message': '你好'}), content_type='application/json')
    assert res.status_code == 200
    payload = res.get_json()
    assert payload['human_reply'].startswith('结论：系统可用')
    assert payload['display']['title'] == '系统运行摘要'
    assert payload['display']['text'].startswith('结论：系统可用')
    assert payload['display']['sections'][0]['label'] == '结论'


def test_api_chat_view_human_returns_plain_text(client_with_runtime):
    client, runtime = client_with_runtime

    async def fake_ask(message, session_key=None, channel=None, chat_id=None, request_id=None):
        return json.dumps(
            {
                'status': 'ok',
                'reply': 'raw-reply',
                'message': 'raw-reply',
                'human_readable': {
                    'summary': '系统可用',
                    'receipt_text': '结论：系统可用',
                },
            },
            ensure_ascii=False,
        )

    runtime.ask = fake_ask
    res = client.post('/api/chat?view=human', data=json.dumps({'message': '你好'}), content_type='application/json')
    assert res.status_code == 200
    assert res.mimetype == 'text/plain'
    assert '结论：系统可用' in res.get_data(as_text=True)


def test_api_chat_rejects_unknown_view(client_with_runtime):
    client, _runtime = client_with_runtime
    res = client.post('/api/chat', data=json.dumps({'message': '你好', 'view': 'xml'}), content_type='application/json')
    assert res.status_code == 400
    assert 'view must be one of' in res.get_json()['error']


def test_api_chat_stream_returns_session_bound_sse(client_with_runtime):
    client, runtime = client_with_runtime

    async def fake_ask(message, session_key=None, channel=None, chat_id=None, request_id=None):
        runtime._append_runtime_event(
            'module_log',
            {
                'module': 'dispatcher',
                'title': '解析意图',
                'message': '正在整理运行上下文',
                'session_key': session_key,
                'chat_id': chat_id,
                'request_id': request_id,
                'channel': channel,
            },
            source='brain',
        )
        return json.dumps(
            {
                'status': 'ok',
                'reply': '已完成分析',
                'message': '已完成分析',
            },
            ensure_ascii=False,
        )

    runtime.ask = fake_ask
    res = client.post('/api/chat/stream', data=json.dumps({'message': '你好'}), content_type='application/json')

    assert res.status_code == 200
    assert res.mimetype == 'text/event-stream'
    text = res.get_data(as_text=True)
    assert 'event: connected' in text
    assert 'event: runtime_event' in text
    assert 'event: summary' in text
    assert 'event: reply' in text
    assert '"stream_kind": "module_update"' in text
    assert '"phase_label": "模块处理"' in text
    assert '"display_text": "模块处理：模块日志更新：dispatcher / 解析意图 / 正在整理运行上下文。"' in text
    assert '本次共播报 1 条事件' in text
    assert '模块日志更新：dispatcher / 解析意图 / 正在整理运行上下文。' in text


def test_api_status_rejects_unknown_view(client_with_runtime):
    client, _runtime = client_with_runtime
    res = client.get('/api/status?view=xml')
    assert res.status_code == 400
    assert 'view must be one of' in res.get_json()['error']
