from types import SimpleNamespace

import invest_evolution.application.train as train_module


def _runtime_paths(tmp_path):
    return {
        "training_output_dir": str(tmp_path / "training"),
        "artifact_log_dir": str(tmp_path / "artifacts"),
        "config_audit_log_path": str(tmp_path / "audit" / "changes.jsonl"),
        "config_snapshot_dir": str(tmp_path / "snapshots"),
    }


def test_train_main_applies_shadow_mode_and_llm_dry_run(monkeypatch, tmp_path):
    calls = {}

    class DummyController:
        def __init__(self, **kwargs):
            calls["init_kwargs"] = dict(kwargs)

        def configure_experiment(self, spec=None):
            calls["experiment_spec"] = dict(spec or {})

        def set_llm_dry_run(self, enabled=True):
            calls.setdefault("llm_dry_run_calls", []).append(bool(enabled))

        def run_continuous(self, max_cycles=100, successful_cycles_target=None):
            calls["max_cycles"] = max_cycles
            calls["successful_cycles_target"] = successful_cycles_target
            return {"status": "ok"}

    monkeypatch.setattr(
        train_module.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(
            cycles=3,
            mock=False,
            output=None,
            artifact_log_dir=None,
            config_audit_log_path=None,
            config_snapshot_dir=None,
            freeze_n=10,
            freeze_m=7,
            log_level="INFO",
            use_allocator=False,
            allocator_top_n=None,
            shadow_mode=True,
            llm_dry_run=True,
            successful_cycles_target=7,
            force_full_cycles=False,
        ),
    )
    monkeypatch.setattr(train_module, "SelfLearningController", DummyController)
    monkeypatch.setattr(
        train_module.RuntimePathConfigService,
        "get_payload",
        lambda self: _runtime_paths(tmp_path),
    )
    monkeypatch.setattr(train_module.logging, "basicConfig", lambda **kwargs: None)

    train_module.train_main()

    assert calls["init_kwargs"]["data_provider"] is None
    assert calls["experiment_spec"]["protocol"]["shadow_mode"] is True
    assert calls["experiment_spec"]["llm"]["dry_run"] is True
    assert calls["max_cycles"] == 3
    assert calls["successful_cycles_target"] == 7


def test_train_main_keeps_mock_path_and_forces_llm_dry_run(monkeypatch, tmp_path):
    calls = {}

    class DummyController:
        def __init__(self, **kwargs):
            calls["init_kwargs"] = dict(kwargs)

        def configure_experiment(self, spec=None):
            calls["experiment_spec"] = dict(spec or {})

        def set_llm_dry_run(self, enabled=True):
            calls.setdefault("llm_dry_run_calls", []).append(bool(enabled))

        def run_continuous(self, max_cycles=100, successful_cycles_target=None):
            calls["max_cycles"] = max_cycles
            calls["successful_cycles_target"] = successful_cycles_target
            return {"status": "ok"}

    monkeypatch.setattr(
        train_module.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(
            cycles=2,
            mock=True,
            output=None,
            artifact_log_dir=None,
            config_audit_log_path=None,
            config_snapshot_dir=None,
            freeze_n=10,
            freeze_m=7,
            log_level="INFO",
            use_allocator=False,
            allocator_top_n=None,
            shadow_mode=True,
            llm_dry_run=False,
            successful_cycles_target=None,
            force_full_cycles=False,
        ),
    )
    monkeypatch.setattr(train_module, "SelfLearningController", DummyController)
    monkeypatch.setattr(
        train_module.RuntimePathConfigService,
        "get_payload",
        lambda self: _runtime_paths(tmp_path),
    )
    monkeypatch.setattr(train_module, "_build_mock_provider", lambda: "mock-provider")
    monkeypatch.setattr(train_module.logging, "basicConfig", lambda **kwargs: None)

    train_module.train_main()

    assert calls["init_kwargs"]["data_provider"] == "mock-provider"
    assert calls["llm_dry_run_calls"] == [True]
    assert calls["experiment_spec"]["protocol"]["shadow_mode"] is True
    assert "llm" not in calls["experiment_spec"]
    assert calls["max_cycles"] == 2
    assert calls["successful_cycles_target"] is None
