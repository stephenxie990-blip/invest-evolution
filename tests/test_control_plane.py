
import pytest

from config.control_plane import (
    ControlPlaneConfigService,
    build_default_llm_caller,
    clear_control_plane_cache,
    get_default_llm_status,
    resolve_component_llm,
)


def test_control_plane_resolves_component_binding_with_local_secret(tmp_path):
    cfg_dir = tmp_path / 'config'
    cfg_dir.mkdir(parents=True)
    (cfg_dir / 'control_plane.yaml').write_text(
        '\n'.join([
            'llm:',
            '  providers:',
            '    provider_a:',
            '      api_base: https://provider-a.example/v1',
            '  models:',
            '    model_a:',
            '      provider: provider_a',
            '      model: alpha-model',
            '  bindings:',
            '    agent.TrendHunter: model_a',
            'data:',
            '  runtime_policy:',
            '    allow_online_fallback: false',
            '    allow_capital_flow_sync: false',
        ]),
        encoding='utf-8',
    )
    (cfg_dir / 'control_plane.local.yaml').write_text(
        '\n'.join([
            'llm:',
            '  providers:',
            '    provider_a:',
            '      api_key: local-secret-key',
        ]),
        encoding='utf-8',
    )

    clear_control_plane_cache()
    resolved = resolve_component_llm(
        'agent.TrendHunter',
        fallback_model='fallback-model',
        fallback_api_key='fallback-key',
        fallback_api_base='https://fallback.example/v1',
        project_root=tmp_path,
    )

    assert resolved.source == 'control_plane'
    assert resolved.model == 'alpha-model'
    assert resolved.api_base == 'https://provider-a.example/v1'
    assert resolved.api_key == 'local-secret-key'


def test_control_plane_service_writes_api_keys_to_local_override(tmp_path):
    service = ControlPlaneConfigService(project_root=tmp_path)
    payload = service.apply_patch(
        {
            'llm': {
                'providers': {
                    'provider_b': {
                        'api_base': 'https://provider-b.example/v1',
                        'api_key': 'write-secret',
                    }
                },
                'models': {
                    'model_b': {
                        'provider': 'provider_b',
                        'model': 'beta-model',
                    }
                },
                'bindings': {
                    'commander.brain': 'model_b',
                },
            }
        },
        source='test',
    )

    assert payload['restart_required'] is True
    public_text = (tmp_path / 'config' / 'control_plane.yaml').read_text(encoding='utf-8')
    local_text = (tmp_path / 'config' / 'control_plane.local.yaml').read_text(encoding='utf-8')
    assert 'write-secret' not in public_text
    assert 'write-secret' in local_text
    snapshot_dir = tmp_path / 'runtime' / 'state' / 'control_plane_snapshots'
    snapshots = sorted(snapshot_dir.glob('control_plane_*.json'))
    assert snapshots
    snapshot_text = snapshots[-1].read_text(encoding='utf-8')
    assert 'write-secret' not in snapshot_text
    assert 'cret' in snapshot_text
    masked = service.get_masked_payload()
    assert masked['llm']['providers']['provider_b']['api_key'].endswith('cret')


def test_default_llm_caller_uses_control_plane_defaults(monkeypatch, tmp_path):
    cfg_dir = tmp_path / 'config'
    cfg_dir.mkdir(parents=True)
    (cfg_dir / 'control_plane.yaml').write_text(
        '\n'.join([
            'llm:',
            '  providers:',
            '    provider_a:',
            '      api_base: https://provider-a.example/v1',
            '      api_key: local-key',
            '  models:',
            '    model_fast:',
            '      provider: provider_a',
            '      model: cp-fast-model',
            '  bindings:',
            '    defaults.fast: model_fast',
        ]),
        encoding='utf-8',
    )
    monkeypatch.setenv('INVEST_CONTROL_PLANE_PATH', str(cfg_dir / 'control_plane.yaml'))
    clear_control_plane_cache()

    from invest.shared.llm import LLMCaller

    caller = LLMCaller(dry_run=True)

    assert caller.model == 'cp-fast-model'
    assert caller.api_base == 'https://provider-a.example/v1'
    assert caller.api_key == 'local-key'


def test_default_llm_status_reports_missing_provider_secret(monkeypatch, tmp_path):
    monkeypatch.setattr('config.config.llm_api_key', '')
    cfg_dir = tmp_path / 'config'
    cfg_dir.mkdir(parents=True)
    (cfg_dir / 'control_plane.yaml').write_text(
        '\n'.join([
            'llm:',
            '  providers:',
            '    default_provider:',
            '      api_base: https://provider-a.example/v1',
            '  models:',
            '    default_fast:',
            '      provider: default_provider',
            '      model: cp-fast-model',
            '  bindings:',
            '    defaults.fast: default_fast',
        ]),
        encoding='utf-8',
    )

    clear_control_plane_cache()
    status = get_default_llm_status('fast', project_root=tmp_path)

    assert status['source'] == 'control_plane'
    assert status['provider_name'] == 'default_provider'
    assert status['api_key_configured'] is False
    assert 'config/control_plane.local.yaml' in status['issue']
    assert 'default_provider' in status['issue']


def test_default_llm_caller_surfaces_control_plane_missing_key_message(monkeypatch, tmp_path):
    monkeypatch.setattr('config.config.llm_api_key', '')
    cfg_dir = tmp_path / 'config'
    cfg_dir.mkdir(parents=True)
    (cfg_dir / 'control_plane.yaml').write_text(
        '\n'.join([
            'llm:',
            '  providers:',
            '    default_provider:',
            '      api_base: https://provider-a.example/v1',
            '  models:',
            '    default_fast:',
            '      provider: default_provider',
            '      model: cp-fast-model',
            '  bindings:',
            '    defaults.fast: default_fast',
        ]),
        encoding='utf-8',
    )

    import app.llm_gateway as gateway_module

    monkeypatch.setattr(gateway_module, 'litellm', object())
    clear_control_plane_cache()

    caller = build_default_llm_caller('fast', project_root=tmp_path)

    with pytest.raises(gateway_module.LLMUnavailableError, match='default_provider'):
        caller.gateway.assert_available()
