import json
from pathlib import Path

import jsonschema

import app.train as train_module
from app.train import SelfLearningController
from market_data import MockDataProvider


CONTRACT = json.loads(Path('docs/contracts/runtime-api-contract.v1.json').read_text(encoding='utf-8'))
SSE_SCHEMAS = CONTRACT['components']['sse_schemas']


def _make_controller(tmp_path):
    return SelfLearningController(
        output_dir=str(tmp_path / 'training'),
        meeting_log_dir=str(tmp_path / 'meetings'),
        config_audit_log_path=str(tmp_path / 'audit' / 'changes.jsonl'),
        config_snapshot_dir=str(tmp_path / 'snapshots'),
        data_provider=MockDataProvider(stock_count=5, days=180, start_date='20230101'),
    )


def _capture_events(monkeypatch):
    events = []
    monkeypatch.setattr(train_module, 'emit_event', lambda event_type, data: events.append((event_type, data)))
    return events


def _validate(event_type, payload):
    schema_name = {
        'agent_status': 'agentStatus',
        'agent_progress': 'agentStatus',
        'module_log': 'moduleLog',
        'meeting_speech': 'meetingSpeech',
    }[event_type]
    jsonschema.validate(payload, SSE_SCHEMAS[schema_name]['data'])


def test_selection_progress_emits_contract_backed_observability_events(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    controller.last_cycle_meta = {'cycle_id': 7, 'cutoff_date': '20240229'}
    events = _capture_events(monkeypatch)

    controller._handle_selection_progress({
        'agent': 'TrendHunter',
        'status': 'running',
        'message': '正在分析趋势候选',
        'speech': '趋势延续，候选质量较高',
        'picks': ['sh.600000', 'sz.000001'],
        'confidence': 0.82,
        'details': {'candidate_count': 2},
    })

    event_types = [event_type for event_type, _ in events]
    assert event_types == ['agent_status', 'agent_progress', 'meeting_speech', 'module_log']

    for event_type, payload in events:
        _validate(event_type, payload)

    module_log = events[-1][1]
    assert module_log['module'] == 'selection'
    assert module_log['kind'] == 'selection_candidates'
    assert module_log['metrics']['candidate_count'] == 2


def test_review_progress_emits_contract_backed_observability_events(monkeypatch, tmp_path):
    controller = _make_controller(tmp_path)
    controller.last_cycle_meta = {'cycle_id': 8, 'cutoff_date': '20240301'}
    events = _capture_events(monkeypatch)

    controller._handle_review_progress({
        'agent': 'ReviewDecision',
        'status': 'completed',
        'message': '复盘完成，建议收紧仓位',
        'reasoning': '连续亏损说明风险暴露偏高',
        'suggestions': ['降低仓位', '延长确认周期'],
        'decision': {'action': 'tighten_risk'},
        'confidence': 0.76,
    })

    event_types = [event_type for event_type, _ in events]
    assert event_types == ['agent_status', 'agent_progress', 'meeting_speech', 'module_log']

    for event_type, payload in events:
        _validate(event_type, payload)

    meeting_speech = events[2][1]
    assert meeting_speech['meeting'] == 'review'
    assert meeting_speech['role'] == 'reviewer'
    assert meeting_speech['confidence'] == 0.76
