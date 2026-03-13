
import config as config_module


def test_load_config_merges_local_override_and_env(monkeypatch, tmp_path):
    config_dir = tmp_path / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    primary = config_dir / 'evolution.yaml'
    local = config_dir / 'evolution.local.yaml'

    primary.write_text(
        '\n'.join([
            'max_stocks: 21',
            'llm_api_key: ${ENV:LLM_API_KEY}',
        ]),
        encoding='utf-8',
    )
    local.write_text(
        '\n'.join([
            'investment_model: mean_reversion',
        ]),
        encoding='utf-8',
    )

    monkeypatch.setenv('LLM_API_KEY', 'env-secret')
    monkeypatch.setenv('LLM_MODEL', 'env-fast-model')

    cfg = config_module.load_config(primary)

    assert [path.name for path in config_module.get_config_layer_paths(primary)] == ['evolution.yaml', 'evolution.local.yaml']
    assert cfg.max_stocks == 21
    assert cfg.investment_model == 'mean_reversion'
    assert cfg.llm_api_key == 'env-secret'
    assert cfg.llm_fast_model == 'env-fast-model'


def test_load_config_includes_runtime_override_layer(monkeypatch, tmp_path):
    config_dir = tmp_path / 'config'
    runtime_dir = tmp_path / 'runtime' / 'state'
    config_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    primary = config_dir / 'evolution.yaml'
    local = config_dir / 'evolution.local.yaml'
    runtime_override = runtime_dir / 'evolution.runtime.yaml'

    primary.write_text('max_stocks: 21\n', encoding='utf-8')
    local.write_text('investment_model: mean_reversion\n', encoding='utf-8')
    runtime_override.write_text('max_stocks: 34\n', encoding='utf-8')

    monkeypatch.delenv('INVEST_CONFIG_PATH', raising=False)

    cfg = config_module.load_config(primary)

    assert [path.name for path in config_module.get_config_layer_paths(primary)] == [
        'evolution.yaml',
        'evolution.local.yaml',
        'evolution.runtime.yaml',
    ]
    assert cfg.max_stocks == 34
    assert cfg.investment_model == 'mean_reversion'
