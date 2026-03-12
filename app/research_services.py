from __future__ import annotations

from pathlib import Path
from typing import Any

from invest.research.case_store import ResearchCaseStore


def _brief_case(item: dict[str, Any]) -> dict[str, Any]:
    snapshot = dict(item.get('snapshot') or {})
    policy = dict(item.get('policy') or {})
    hypothesis = dict(item.get('hypothesis') or {})
    attribution = dict(item.get('attribution') or {})
    return {
        'research_case_id': str(item.get('research_case_id') or ''),
        'as_of_date': str(snapshot.get('as_of_date') or ''),
        'code': str(dict(snapshot.get('security') or {}).get('code') or snapshot.get('metadata', {}).get('query_code') or ''),
        'name': str(dict(snapshot.get('security') or {}).get('name') or ''),
        'policy_id': str(policy.get('policy_id') or ''),
        'model_name': str(policy.get('model_name') or ''),
        'config_name': str(policy.get('config_name') or ''),
        'stance': str(hypothesis.get('stance') or ''),
        'thesis_result': str(attribution.get('thesis_result') or ''),
        'path': str(item.get('path') or ''),
        'attribution_path': str(item.get('attribution_path') or ''),
    }


def _brief_attribution(item: dict[str, Any]) -> dict[str, Any]:
    attribution = dict(item.get('attribution') or {})
    metadata = dict(item.get('metadata') or {})
    horizon_results = dict(attribution.get('horizon_results') or {})
    return {
        'attribution_id': str(item.get('attribution_id') or ''),
        'hypothesis_id': str(attribution.get('hypothesis_id') or ''),
        'thesis_result': str(attribution.get('thesis_result') or ''),
        'policy_id': str(metadata.get('policy_id') or ''),
        'code': str(metadata.get('code') or ''),
        'as_of_date': str(metadata.get('as_of_date') or ''),
        'scored_horizons': sorted(list(horizon_results.keys())),
        'path': str(item.get('path') or ''),
    }


def get_research_cases_payload(
    *,
    case_store: ResearchCaseStore,
    limit: int = 20,
    policy_id: str = '',
    symbol: str = '',
    as_of_date: str = '',
    horizon: str = '',
) -> dict[str, Any]:
    matches = case_store.find_cases(
        policy_id=policy_id,
        symbol=symbol,
        as_of_date=as_of_date,
        horizon=horizon,
        limit=limit,
    )
    return {
        'status': 'ok',
        'count': len(matches),
        'filters': {
            'limit': int(limit),
            'policy_id': str(policy_id or ''),
            'symbol': str(symbol or ''),
            'as_of_date': str(as_of_date or ''),
            'horizon': str(horizon or ''),
        },
        'items': [_brief_case(item) for item in matches],
    }


def get_research_attributions_payload(*, case_store: ResearchCaseStore, limit: int = 20) -> dict[str, Any]:
    items = case_store.list_attributions(limit=limit)
    return {
        'status': 'ok',
        'count': len(items),
        'filters': {'limit': int(limit)},
        'items': [_brief_attribution(item) for item in items],
    }


def get_research_calibration_payload(*, case_store: ResearchCaseStore, policy_id: str = '') -> dict[str, Any]:
    report = case_store.build_calibration_report(policy_id=policy_id)
    file_name = f"policy_{policy_id}.json" if policy_id else 'policy_all.json'
    artifact_path = case_store.calibration_dir / file_name
    return {
        'status': 'ok',
        'policy_id': str(policy_id or ''),
        'artifact_path': str(artifact_path) if artifact_path.exists() else '',
        'report': report,
    }


__all__ = [
    'get_research_attributions_payload',
    'get_research_calibration_payload',
    'get_research_cases_payload',
]
