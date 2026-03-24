import importlib.util
from pathlib import Path

import pytest
import yaml

import invest_evolution.config as config_module
from invest_evolution.config import EvolutionConfig
from invest_evolution.config.control_plane import EvolutionConfigService, RuntimePathConfigService

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# --- Config Layering & Overrides Tests ---

def test_load_config_merges_local_override_and_env(monkeypatch, tmp_path):
    config_dir = tmp_path / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / 'evolution.yaml').write_text('max_stocks: 21\nllm_api_key: ${ENV:LLM_API_KEY}', encoding='utf-8')
    (config_dir / 'evolution.local.yaml').write_text('default_manager_id: mean_reversion', encoding='utf-8')

    monkeypatch.setenv('LLM_API_KEY', 'env-secret')
    cfg = config_module.load_config((config_dir / 'evolution.yaml'))
    assert cfg.max_stocks == 21
    assert cfg.default_manager_id == 'mean_reversion'
    assert cfg.llm_api_key == 'env-secret'


def test_load_config_env_overrides_llm_limits(monkeypatch, tmp_path):
    cfg_file = tmp_path / "evolution.yaml"
    cfg_file.write_text("llm_timeout: 60\n", encoding="utf-8")
    monkeypatch.setenv("LLM_TIMEOUT", "7")
    cfg = config_module.load_config(str(cfg_file))
    assert cfg.llm_timeout == 7


# --- LLM Ownership Hard-Cut Tests ---

def test_config_does_not_read_legacy_llm_auth_fallbacks(monkeypatch, tmp_path):
    module_path = PROJECT_ROOT / "src" / "invest_evolution" / "config" / "__init__.py"
    spec = importlib.util.spec_from_file_location("config_test_no_legacy_fallback", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("INVEST_ALLOW_CODEX_AUTH_FALLBACK", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-env-key")
    monkeypatch.setenv("LLM_API_KEY", "legacy-env-key")
    spec.loader.exec_module(module)

    assert module.DEFAULT_LLM_API_KEY == ""
    assert module.config.llm_api_key == ""
    assert module.config.llm_api_base == module.DEFAULT_LLM_API_BASE


def test_config_ignores_legacy_llm_env_overrides(monkeypatch, tmp_path):
    cfg_file = tmp_path / "evolution.yaml"
    cfg_file.write_text(
        "\n".join([
            "llm_fast_model: yaml-fast",
            "llm_deep_model: yaml-deep",
            "llm_api_key: yaml-key",
            "llm_api_base: https://yaml.example/v1",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_MODEL", "legacy-fast")
    monkeypatch.setenv("LLM_DEEP_MODEL", "legacy-deep")
    monkeypatch.setenv("LLM_API_KEY", "legacy-key")
    monkeypatch.setenv("LLM_API_BASE", "https://legacy.example/v1")

    cfg = config_module.load_config(str(cfg_file))

    assert cfg.llm_fast_model == "yaml-fast"
    assert cfg.llm_deep_model == "yaml-deep"
    assert cfg.llm_api_key == "yaml-key"
    assert cfg.llm_api_base == "https://yaml.example/v1"


# --- Config Service & Security Tests ---

def test_apply_patch_does_not_persist_secrets_to_disk(tmp_path):
    service = EvolutionConfigService(project_root=tmp_path, live_config=EvolutionConfig(llm_api_key='secret'))
    service.apply_patch({'max_stocks': 88}, source='test')
    
    runtime_path = tmp_path / 'runtime' / 'state' / 'evolution.runtime.yaml'
    runtime_payload = yaml.safe_load(runtime_path.read_text(encoding='utf-8'))
    assert runtime_payload['max_stocks'] == 88
    assert 'llm_api_key' not in runtime_payload


def test_get_masked_payload_masks_sensitive_fields(tmp_path):
    service = EvolutionConfigService(project_root=tmp_path, live_config=EvolutionConfig(web_api_token='secret-token'))
    payload = service.get_masked_payload()
    assert 'web_api_token_masked' in payload
    assert 'secret-token' not in str(payload)
    assert payload['web_status_training_lab_limit'] == 3
    assert payload['web_status_events_summary_limit'] == 20
    assert payload['web_runtime_async_timeout_sec'] == 600


# --- Runtime Path & Migration Tests ---

def test_runtime_path_service_rejects_out_of_bounds_paths(tmp_path):
    service = RuntimePathConfigService(project_root=tmp_path)
    with pytest.raises(ValueError, match='runtime directory'):
        service.apply_patch({'training_output_dir': '../escape'})


def test_config_service_normalizes_multi_manager_fields(tmp_path):
    service = EvolutionConfigService(project_root=tmp_path, live_config=EvolutionConfig())
    normalized = service.normalize_patch({"manager_active_ids": "m1,m2"})
    assert normalized["manager_active_ids"] == ["m1", "m2"]


def test_evolution_config_defaults_align_with_manager_portfolio_runtime():
    cfg = EvolutionConfig()

    assert cfg.manager_arch_enabled is True
    assert cfg.portfolio_assembly_enabled is True
    assert cfg.dual_review_enabled is True
