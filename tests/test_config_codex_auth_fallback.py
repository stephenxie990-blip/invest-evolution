import importlib.util
import sys
from pathlib import Path


def test_config_does_not_read_codex_auth_file_even_when_present(monkeypatch, tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "auth.json").write_text('{"OPENAI_API_KEY": "codex-auth-key"}', encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    module_path = Path(__file__).resolve().parents[1] / "config" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "config_public_env_only_test",
        module_path,
        submodule_search_locations=[str(module_path.parent)],
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        assert module.DEFAULT_LLM_API_KEY == ""
        assert module.config.llm_api_key == ""
    finally:
        sys.modules.pop(spec.name, None)


def test_config_no_longer_warns_eagerly_when_llm_key_missing(monkeypatch, tmp_path, caplog):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    module_path = Path(__file__).resolve().parents[1] / "config" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "config_no_eager_llm_warning_test",
        module_path,
        submodule_search_locations=[str(module_path.parent)],
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        with caplog.at_level("WARNING"):
            spec.loader.exec_module(module)
        assert "LLM_API_KEY 未设置" not in caplog.text
    finally:
        sys.modules.pop(spec.name, None)
