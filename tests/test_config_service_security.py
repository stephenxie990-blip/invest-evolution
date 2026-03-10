from pathlib import Path

import pytest
import yaml

from config import EvolutionConfig
from config.services import EvolutionConfigService, RuntimePathConfigService


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


def test_get_masked_payload_masks_web_api_token(tmp_path):
    project_root = tmp_path
    (project_root / 'config').mkdir(parents=True, exist_ok=True)

    service = EvolutionConfigService(
        project_root=project_root,
        live_config=EvolutionConfig(web_api_token='super-secret-token', web_api_require_auth=True),
    )

    payload = service.get_masked_payload()

    assert payload['web_api_require_auth'] is True
    assert payload['web_api_token_masked'].endswith('oken')
    assert 'super-secret-token' not in yaml.safe_dump(payload, allow_unicode=True)


def test_runtime_path_service_rejects_paths_outside_runtime(tmp_path):
    service = RuntimePathConfigService(project_root=tmp_path)

    with pytest.raises(ValueError, match='runtime directory'):
        service.apply_patch({'training_output_dir': '../escape'})
