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
    assert recorded['plan']['last_run_id'] == 'run_demo'
    assert recorded['plan']['artifacts']['latest_run_path'] == str(store.run_path('run_demo'))
    assert store.counts() == {'plan_count': 1, 'run_count': 1, 'evaluation_count': 1}
    assert store.read_json_artifact(store.plan_path('plan_demo'), label='training plan')['status'] == 'completed'
    assert store.list_json_artifacts(store.training_run_dir, limit=5)['count'] == 1
    raw_run = json.loads(store.run_path('run_demo').read_text(encoding='utf-8'))
    assert raw_run['payload']['results'][0]['return_pct'] == 1.2
