
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

    primary_path = project_root / 'config' / 'evolution.yaml'
    runtime_override_path = project_root / 'runtime' / 'state' / 'evolution.runtime.yaml'
    runtime_payload = yaml.safe_load(runtime_override_path.read_text(encoding='utf-8')) or {}

    assert not primary_path.exists()
    assert runtime_payload['max_stocks'] == 88
    assert 'llm_api_key' not in runtime_payload
    assert not (project_root / 'config' / 'evolution.local.yaml').exists()


def test_apply_patch_ignores_llm_secret_after_control_plane_split(tmp_path):
    project_root = tmp_path
    (project_root / 'config').mkdir(parents=True, exist_ok=True)

    service = EvolutionConfigService(
        project_root=project_root,
        live_config=EvolutionConfig(),
    )

    service.apply_patch({'llm_api_key': 'file-secret', 'max_stocks': 66}, source='test')

    runtime_payload = yaml.safe_load((project_root / 'runtime' / 'state' / 'evolution.runtime.yaml').read_text(encoding='utf-8')) or {}

    assert runtime_payload['max_stocks'] == 66
    assert 'llm_api_key' not in runtime_payload
    assert not (project_root / 'config' / 'evolution.local.yaml').exists()


def test_apply_patch_preserves_shared_primary_config_file(tmp_path):
    project_root = tmp_path
    config_dir = project_root / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    primary_path = config_dir / 'evolution.yaml'
    primary_path.write_text(
        yaml.safe_dump(
            {
                'freeze_total_cycles': 99,
                'freeze_profit_required': 88,
                'max_stocks': 50,
                'custom_note': 'keep-me',
            },
            allow_unicode=True,
            sort_keys=True,
        ),
        encoding='utf-8',
    )

    service = EvolutionConfigService(
        project_root=project_root,
        live_config=EvolutionConfig(freeze_total_cycles=99, freeze_profit_required=88),
        config_path=primary_path,
    )

    service.apply_patch({'max_stocks': 66}, source='test')

    primary_payload = yaml.safe_load(primary_path.read_text(encoding='utf-8')) or {}
    runtime_payload = yaml.safe_load((project_root / 'runtime' / 'state' / 'evolution.runtime.yaml').read_text(encoding='utf-8')) or {}

    assert primary_payload['freeze_total_cycles'] == 99
    assert primary_payload['freeze_profit_required'] == 88
    assert primary_payload['custom_note'] == 'keep-me'
    assert primary_payload['max_stocks'] == 50
    assert runtime_payload['max_stocks'] == 66


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
    assert payload['runtime_override_exists'] is False
    assert 'super-secret-token' not in yaml.safe_dump(payload, allow_unicode=True)


def test_runtime_path_service_rejects_paths_outside_runtime(tmp_path):
    service = RuntimePathConfigService(project_root=tmp_path)

    with pytest.raises(ValueError, match='runtime directory'):
        service.apply_patch({'training_output_dir': '../escape'})


def test_config_service_updates_rate_limit_fields(tmp_path):
    project_root = tmp_path
    (project_root / 'config').mkdir(parents=True, exist_ok=True)
    service = EvolutionConfigService(
        project_root=project_root,
        live_config=EvolutionConfig(),
    )

    payload = service.apply_patch(
        {
            'web_rate_limit_enabled': 'true',
            'web_rate_limit_window_sec': '90',
        },
        source='test',
    )

    cfg = payload['config']
    assert cfg['web_rate_limit_enabled'] is True
    assert cfg['web_rate_limit_window_sec'] == 90
