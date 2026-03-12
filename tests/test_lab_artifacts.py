from __future__ import annotations

import json
from pathlib import Path

from app.lab.artifacts import TrainingLabArtifactStore


def test_training_lab_artifact_store_roundtrip(tmp_path: Path):
    store = TrainingLabArtifactStore(
        training_plan_dir=tmp_path / 'plans',
        training_run_dir=tmp_path / 'runs',
        training_eval_dir=tmp_path / 'evals',
    )
    store.ensure_storage()

    plan = store.build_training_plan_payload(
        rounds=2,
        mock=False,
        source='manual',
        goal='compare models',
        notes='lab run',
        tags=['lab', 'compare'],
        protocol={'holdout': {'enabled': True}},
        dataset={'symbol_pool': ['AAA']},
        model_scope={'candidate_model': 'momentum'},
        optimization={'promotion_gate': {'min_samples': 1}},
        llm={'timeout': 7, 'max_retries': 1},
        plan_id='plan_demo',
    )
    store.write_json_artifact(store.plan_path(plan['plan_id']), plan)

    eval_payload = {
        'run_id': 'run_demo',
        'plan_id': 'plan_demo',
        'created_at': '2026-03-10T00:00:00',
        'status': 'completed',
        'assessment': {'success_count': 1},
        'promotion': {'verdict': 'rejected'},
        'artifacts': {
            'run_path': str(store.run_path('run_demo')),
            'evaluation_path': str(store.evaluation_path('run_demo')),
        },
    }
    recorded = store.record_training_lab_artifacts(
        plan=plan,
        payload={'results': [{'status': 'ok', 'return_pct': 1.2}]},
        status='completed',
        eval_payload=eval_payload,
        run_id='run_demo',
    )

    assert recorded['plan']['llm']['timeout'] == 7
    assert recorded['run']['plan']['llm']['max_retries'] == 1
    assert recorded['run']['plan']['guardrails']['promotion_gate']['research_feedback']['enabled'] is True
    assert recorded['plan']['last_run_id'] == 'run_demo'
    assert recorded['plan']['artifacts']['latest_run_path'] == str(store.run_path('run_demo'))
    assert store.counts() == {'plan_count': 1, 'run_count': 1, 'evaluation_count': 1}
    assert store.read_json_artifact(store.plan_path('plan_demo'), label='training plan')['status'] == 'completed'
    assert store.list_json_artifacts(store.training_run_dir, limit=5)['count'] == 1
    raw_run = json.loads(store.run_path('run_demo').read_text(encoding='utf-8'))
    assert raw_run['payload']['results'][0]['return_pct'] == 1.2



def test_training_plan_payload_injects_default_research_feedback_gate(tmp_path: Path):
    store = TrainingLabArtifactStore(
        training_plan_dir=tmp_path / 'plans',
        training_run_dir=tmp_path / 'runs',
        training_eval_dir=tmp_path / 'evals',
    )

    plan = store.build_training_plan_payload(
        rounds=1,
        mock=True,
        source='manual',
        plan_id='plan_default_gate',
    )

    gate = plan['optimization']['promotion_gate']['research_feedback']
    assert gate['min_sample_count'] == 5
    assert gate['blocked_biases'] == ['tighten_risk', 'recalibrate_probability']
    assert gate['max_brier_like_direction_score'] == 0.25
    assert gate['horizons']['T+20']['min_hit_rate'] == 0.45
    assert gate['horizons']['T+20']['max_invalidation_rate'] == 0.30
    assert gate['horizons']['T+20']['min_interval_hit_rate'] == 0.40
    guardrail = plan['guardrails']['promotion_gate']['research_feedback']
    assert guardrail['enabled'] is True
    assert guardrail['policy_source']['mode'] == 'default_injected'
    assert 'default_research_feedback_gate_enabled' in guardrail['reason_codes']
    assert '默认启用 research_feedback 校准门' in guardrail['summary']


def test_training_plan_payload_merges_research_feedback_gate_overrides(tmp_path: Path):
    store = TrainingLabArtifactStore(
        training_plan_dir=tmp_path / 'plans',
        training_run_dir=tmp_path / 'runs',
        training_eval_dir=tmp_path / 'evals',
    )

    plan = store.build_training_plan_payload(
        rounds=1,
        mock=True,
        source='manual',
        optimization={
            'promotion_gate': {
                'min_samples': 2,
                'research_feedback': {
                    'min_sample_count': 9,
                    'horizons': {'T+20': {'min_hit_rate': 0.50}},
                },
            }
        },
        plan_id='plan_override_gate',
    )

    promotion_gate = plan['optimization']['promotion_gate']
    gate = promotion_gate['research_feedback']
    assert promotion_gate['min_samples'] == 2
    assert gate['min_sample_count'] == 9
    assert gate['blocked_biases'] == ['tighten_risk', 'recalibrate_probability']
    assert gate['horizons']['T+20']['min_hit_rate'] == 0.50
    assert gate['horizons']['T+20']['max_invalidation_rate'] == 0.30
    assert gate['horizons']['T+20']['min_interval_hit_rate'] == 0.40
    guardrail = plan['guardrails']['promotion_gate']['research_feedback']
    assert guardrail['policy_source']['mode'] == 'default_plus_override'
    assert guardrail['policy_source']['user_override_keys'] == ['horizons', 'min_sample_count']
    assert 'research_feedback_user_override_merged' in guardrail['reason_codes']
