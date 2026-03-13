
import config as config_module


def test_load_config_env_overrides_llm_limits(monkeypatch, tmp_path):
    cfg_file = tmp_path / "evolution.yaml"
    cfg_file.write_text(
        "\n".join([
            "llm_timeout: 60",
            "llm_max_retries: 3",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_TIMEOUT", "7")
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")

    cfg = config_module.load_config(str(cfg_file))

    assert cfg.llm_timeout == 7
    assert cfg.llm_max_retries == 1


def test_load_config_env_overrides_llm_models_and_endpoint(monkeypatch, tmp_path):
    cfg_file = tmp_path / "evolution.yaml"
    cfg_file.write_text(
        "\n".join([
            "llm_fast_model: yaml-fast",
            "llm_deep_model: yaml-deep",
            "llm_api_base: https://yaml.example",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_MODEL", "env-fast")
    monkeypatch.setenv("LLM_DEEP_MODEL", "env-deep")
    monkeypatch.setenv("LLM_API_BASE", "https://env.example")

    cfg = config_module.load_config(str(cfg_file))

    assert cfg.llm_fast_model == "env-fast"
    assert cfg.llm_deep_model == "env-deep"
    assert cfg.llm_api_base == "https://env.example"


def test_load_config_invalid_env_int_falls_back_to_yaml(monkeypatch, tmp_path):
    cfg_file = tmp_path / "evolution.yaml"
    cfg_file.write_text("llm_timeout: 42\n", encoding="utf-8")
    monkeypatch.setenv("LLM_TIMEOUT", "oops")

    cfg = config_module.load_config(str(cfg_file))

    assert cfg.llm_timeout == 42
