
import pytest

from invest_evolution.config.control_plane import (
    ControlPlaneResolver,
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
    assert resolved.binding_name == 'model_a'
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

    from invest_evolution.investment.shared.llm import LLMCaller

    caller = LLMCaller(dry_run=True)

    assert caller.model == 'cp-fast-model'
    assert caller.api_base == 'https://provider-a.example/v1'
    assert caller.api_key == 'local-key'


def test_default_llm_status_reports_missing_provider_secret(tmp_path):
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
    assert status['ownership_mode'] == 'control_plane'
    assert status['fallback_active'] is False
    assert status['provider_name'] == 'default_provider'
    assert status['api_key_configured'] is False
    assert 'config/control_plane.local.yaml' in status['issue']
    assert 'default_provider' in status['issue']


def test_default_llm_caller_surfaces_control_plane_missing_key_message(monkeypatch, tmp_path):
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

    import invest_evolution.common.utils as gateway_module

    monkeypatch.setattr(gateway_module, 'litellm', object())
    clear_control_plane_cache()

    caller = build_default_llm_caller('fast', project_root=tmp_path)

    with pytest.raises(gateway_module.LLMUnavailableError, match='default_provider'):
        caller.gateway.assert_available()


def test_control_plane_binding_does_not_fallback_to_legacy_llm_api_key(monkeypatch, tmp_path):
    cfg_dir = tmp_path / 'config'
    cfg_dir.mkdir(parents=True)
    (cfg_dir / 'control_plane.yaml').write_text(
        '\n'.join([
            'llm:',
            '  providers:',
            '    default_provider:',
            '      api_base: https://api.openai.com/v1',
            '  models:',
            '    default_fast:',
            '      provider: default_provider',
            '      model: gpt-5-mini',
            '  bindings:',
            '    defaults.fast: default_fast',
        ]),
        encoding='utf-8',
    )

    monkeypatch.setenv('LLM_API_KEY', 'legacy-env-key')
    clear_control_plane_cache()

    status = get_default_llm_status('fast', project_root=tmp_path)

    assert status['source'] == 'control_plane'
    assert status['ownership_mode'] == 'control_plane'
    assert status['fallback_active'] is False
    assert status['api_key_configured'] is False
    assert 'control_plane.local.yaml' in status['issue']
    assert 'legacy-env-key' not in status['issue']


def test_component_llm_uses_fallback_when_control_plane_is_absent():
    resolved = resolve_component_llm(
        'agent.TrendHunter',
        fallback_model='fallback-model',
        fallback_api_key='fallback-key',
        fallback_api_base='https://fallback.example/v1',
        project_root='/tmp/non-existent-control-plane-root',
    )

    assert resolved.source == 'fallback'
    assert resolved.model == 'fallback-model'
    assert resolved.api_key == 'fallback-key'
    assert resolved.api_base == 'https://fallback.example/v1'
    status = get_default_llm_status('fast', project_root='/tmp/non-existent-control-plane-root')
    assert status['ownership_mode'] == 'fallback'
    assert status['fallback_active'] is True
    assert 'fallback values active' in status['governance_summary']


def test_component_llm_reports_missing_binding_when_control_plane_is_present(tmp_path):
    cfg_dir = tmp_path / 'config'
    cfg_dir.mkdir(parents=True)
    (cfg_dir / 'control_plane.yaml').write_text(
        '\n'.join([
            'llm:',
            '  providers:',
            '    default_provider:',
            '      api_base: https://api.openai.com/v1',
            '      api_key: local-key',
            '  models:',
            '    default_fast:',
            '      provider: default_provider',
            '      model: gpt-5-mini',
            '  bindings:',
            '    defaults.fast: default_fast',
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

    assert resolved.source == 'fallback'
    assert resolved.model == 'fallback-model'
    assert 'llm.bindings.agent.TrendHunter' in resolved.issue
    assert 'fallback' in resolved.issue


def test_control_plane_resolver_tolerates_non_dict_sections():
    resolver = ControlPlaneResolver(
        {
            "llm": ["bad"],
            "data": "oops",
        }
    )

    policy = resolver.runtime_data_policy()
    assert policy["allow_online_fallback"] is False
    assert policy["allow_capital_flow_sync"] is False

    resolved = resolver.resolve_llm(
        "agent.TrendHunter",
        fallback_model="fallback-model",
        fallback_api_key="fallback-key",
        fallback_api_base="https://fallback.example/v1",
    )
    assert resolved.source == "fallback"
    assert resolved.model == "fallback-model"


def test_control_plane_resolver_tolerates_non_dict_nested_llm_entries():
    resolver = ControlPlaneResolver(
        {
            "llm": {
                "providers": {"default_provider": "invalid"},
                "models": {"default_fast": "invalid"},
                "bindings": {"agent.TrendHunter": "default_fast"},
            }
        }
    )

    resolved = resolver.resolve_llm(
        "agent.TrendHunter",
        fallback_model="fallback-model",
        fallback_api_key="fallback-key",
        fallback_api_base="https://fallback.example/v1",
    )
    assert resolved.source == "control_plane"
    assert resolved.binding_name == "default_fast"
    assert resolved.model == ""
    assert resolved.api_key == ""
