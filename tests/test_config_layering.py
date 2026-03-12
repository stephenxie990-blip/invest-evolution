from pathlib import Path

import config as config_module


def test_load_config_merges_local_override_and_env(monkeypatch, tmp_path):
    config_dir = tmp_path / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    primary = config_dir / 'evolution.yaml'
    local = config_dir / 'evolution.local.yaml'

    primary.write_text(
        '\n'.join([
            'max_stocks: 21',
            'web_ui_shell_mode: legacy',
            'llm_api_key: ${ENV:LLM_API_KEY}',
        ]),
        encoding='utf-8',
    )
    local.write_text(
        '\n'.join([
            'investment_model: mean_reversion',
            'frontend_canary_enabled: true',
        ]),
        encoding='utf-8',
    )

    monkeypatch.setenv('LLM_API_KEY', 'env-secret')
    monkeypatch.setenv('LLM_MODEL', 'env-fast-model')
    monkeypatch.setenv('FRONTEND_CANARY_QUERY_PARAM', ' rollout ')

    cfg = config_module.load_config(primary)

    assert [path.name for path in config_module.get_config_layer_paths(primary)] == ['evolution.yaml', 'evolution.local.yaml']
    assert cfg.max_stocks == 21
    assert cfg.investment_model == 'mean_reversion'
    assert cfg.llm_api_key == 'env-secret'
    assert cfg.llm_fast_model == 'env-fast-model'
    assert cfg.web_ui_shell_mode == 'legacy'
    assert cfg.frontend_canary_enabled is True
    assert cfg.frontend_canary_query_param == 'rollout'


def test_load_config_falls_back_for_invalid_web_ui_shell_mode(tmp_path):
    config_dir = tmp_path / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    primary = config_dir / 'evolution.yaml'

    primary.write_text(
        '\n'.join([
            'web_ui_shell_mode: future-mode',
            'frontend_canary_query_param: "  launch  "',
        ]),
        encoding='utf-8',
    )

    cfg = config_module.load_config(primary)

    assert cfg.web_ui_shell_mode == 'legacy'
    assert cfg.frontend_canary_query_param == 'launch'
