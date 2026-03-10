from pathlib import Path

import yaml

from config import EvolutionConfig
from config.services import EvolutionConfigService


def test_apply_patch_does_not_persist_runtime_secret_to_primary_or_local(tmp_path):
    project_root = tmp_path
    (project_root / 'config').mkdir(parents=True, exist_ok=True)

    service = EvolutionConfigService(
        project_root=project_root,
        live_config=EvolutionConfig(llm_api_key='env-secret'),
    )

    service.apply_patch({'max_stocks': 88}, source='test')

    primary_payload = yaml.safe_load((project_root / 'config' / 'evolution.yaml').read_text(encoding='utf-8')) or {}
    assert primary_payload['max_stocks'] == 88
    assert 'llm_api_key' not in primary_payload
    assert not (project_root / 'config' / 'evolution.local.yaml').exists()


def test_apply_patch_persists_explicit_secret_to_local_override(tmp_path):
    project_root = tmp_path
    (project_root / 'config').mkdir(parents=True, exist_ok=True)

    service = EvolutionConfigService(
        project_root=project_root,
        live_config=EvolutionConfig(),
    )

    service.apply_patch({'llm_api_key': 'file-secret'}, source='test')

    primary_payload = yaml.safe_load((project_root / 'config' / 'evolution.yaml').read_text(encoding='utf-8')) or {}
    local_payload = yaml.safe_load((project_root / 'config' / 'evolution.local.yaml').read_text(encoding='utf-8')) or {}

    assert 'llm_api_key' not in primary_payload
    assert local_payload['llm_api_key'] == 'file-secret'
