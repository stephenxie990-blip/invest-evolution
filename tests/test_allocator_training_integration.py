from invest_evolution.application.train import SelfLearningController


def test_controller_enables_allocator_from_config(monkeypatch, tmp_path):
    import invest_evolution.config as config_module

    monkeypatch.setattr(config_module.config, "allocator_enabled", True, raising=False)
    monkeypatch.setattr(config_module.config, "allocator_top_n", 2, raising=False)
    controller = SelfLearningController(
        output_dir=str(tmp_path / "out"),
        artifact_log_dir=str(tmp_path / "artifacts"),
        config_audit_log_path=str(tmp_path / "state" / "audit.jsonl"),
        config_snapshot_dir=str(tmp_path / "state" / "snapshots"),
    )
    assert controller.allocator_enabled is True
    assert controller.allocator_top_n == 2
