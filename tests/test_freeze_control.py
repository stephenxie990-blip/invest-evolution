from app.train import SelfLearningController


def test_force_full_cycles_disables_early_freeze(monkeypatch, tmp_path):
    import config as config_module
    monkeypatch.setattr(config_module.config, "stop_on_freeze", False, raising=False)
    controller = SelfLearningController(
        output_dir=str(tmp_path / "out"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "state" / "audit.jsonl"),
        config_snapshot_dir=str(tmp_path / "state" / "snapshots"),
    )
    assert controller.stop_on_freeze is False
