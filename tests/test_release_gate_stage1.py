import json

from scripts import run_release_gate_stage1 as stage1_module
from scripts.run_release_gate_stage1 import (
    build_stage4_shadow_experiment_spec,
    resolve_stage4_shadow_anchor_date,
)


def test_stage4_shadow_experiment_spec_uses_rolling_review_and_cutoff():
    spec = build_stage4_shadow_experiment_spec()

    assert spec["protocol"]["shadow_mode"] is True
    assert spec["protocol"]["review_window"] == {"mode": "rolling", "size": 5}
    assert spec["protocol"]["cutoff_policy"] == {
        "mode": "rolling",
        "step_days": 30,
        "anchor_date": "",
    }
    assert "llm" not in spec


def test_stage4_shadow_experiment_spec_enables_dry_run_when_requested():
    spec = build_stage4_shadow_experiment_spec(mock=True, llm_dry_run=False, anchor_date="2020-03-01")

    assert spec["llm"] == {"dry_run": True}
    assert spec["protocol"]["cutoff_policy"]["anchor_date"] == "20200301"


class _FakeDataManager:
    def __init__(self, readiness_map):
        self._readiness_map = dict(readiness_map)

    def check_training_readiness(self, cutoff_date, *, stock_count, min_history_days):
        ready = bool(self._readiness_map.get(cutoff_date, False))
        return {
            "ready": ready,
            "date_range": {"max": "20181231"},
            "eligible_stock_count": stock_count if ready else 0,
            "min_history_days": min_history_days,
        }


class _FakeController:
    def __init__(self, readiness_map):
        self.data_manager = _FakeDataManager(readiness_map)
        self.experiment_min_date = "20180101"
        self.experiment_min_history_days = 200


def test_resolve_stage4_shadow_anchor_date_skips_unready_early_windows():
    controller = _FakeController(
        {
            "20180101": False,
            "20180131": False,
            "20180302": True,
        }
    )

    anchor_date = resolve_stage4_shadow_anchor_date(controller, step_days=30)

    assert anchor_date == "20180302"


def test_resolve_stage4_shadow_anchor_date_respects_warmup_windows():
    controller = _FakeController(
        {
            "20180101": True,
            "20180131": True,
            "20180302": True,
        }
    )

    anchor_date = resolve_stage4_shadow_anchor_date(
        controller,
        step_days=30,
        warmup_windows=2,
    )

    assert anchor_date == "20180302"


def test_resolve_stage4_shadow_anchor_date_skips_stale_windows():
    class _StaleDataManager(_FakeDataManager):
        def check_training_readiness(self, cutoff_date, *, stock_count, min_history_days):
            ready = bool(self._readiness_map.get(cutoff_date, False))
            return {
                "ready": ready,
                "date_range": {"max": "20171231" if not ready else cutoff_date},
                "eligible_stock_count": stock_count,
                "min_history_days": min_history_days,
                "stale_data": not ready,
            }

    controller = _FakeController({})
    controller.data_manager = _StaleDataManager(
        {
            "20180101": False,
            "20180131": False,
            "20180302": True,
        }
    )

    anchor_date = resolve_stage4_shadow_anchor_date(controller, step_days=30, max_date="20180331")

    assert anchor_date == "20180302"


def test_stage4_main_persists_run_report_when_interrupted(tmp_path, monkeypatch):
    output_dir = tmp_path / "shadow_run"
    artifact_log_dir = tmp_path / "artifacts"
    config_snapshot_dir = tmp_path / "snapshots"
    config_audit_log_path = tmp_path / "config_audit.jsonl"

    class _FakeRuntimePathConfigService:
        def __init__(self, project_root):
            self.project_root = project_root

        def get_payload(self):
            return {
                "artifact_log_dir": str(artifact_log_dir),
                "config_audit_log_path": str(config_audit_log_path),
                "config_snapshot_dir": str(config_snapshot_dir),
            }

    class _FakeController:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.aggregate_leaderboard_enabled = True
            self.total_cycle_attempts = 3
            self.output_dir = kwargs["output_dir"]

        def set_llm_dry_run(self, enabled):
            self.llm_dry_run = enabled

        def configure_experiment(self, experiment_spec):
            self.experiment_spec = experiment_spec

        def run_continuous(self, *, max_cycles, successful_cycles_target):
            raise KeyboardInterrupt("manual stop")

    monkeypatch.setattr(stage1_module, "RuntimePathConfigService", _FakeRuntimePathConfigService)
    monkeypatch.setattr(stage1_module, "SelfLearningController", _FakeController)
    observed = {}

    def _fake_resolve_anchor(controller, step_days=30, warmup_windows=0):
        observed["step_days"] = step_days
        observed["warmup_windows"] = warmup_windows
        return "20180101"

    monkeypatch.setattr(stage1_module, "resolve_stage4_shadow_anchor_date", _fake_resolve_anchor)
    monkeypatch.setattr(stage1_module, "session_cycle_history", lambda controller: [{"cycle_id": 1}])

    exit_code = stage1_module.main(["--output", str(output_dir)])

    run_report = json.loads((output_dir / "run_report.json").read_text(encoding="utf-8"))
    assert exit_code == 130
    assert observed == {"step_days": 30, "warmup_windows": stage1_module.DEFAULT_STAGE4_SHADOW_WARMUP_WINDOWS}
    assert run_report["status"] == "interrupted"
    assert run_report["error_type"] == "KeyboardInterrupt"
    assert run_report["successful_cycles"] == 1
    assert (output_dir / "release_gate_divergence_report.json").exists()
    assert (output_dir / "release_gate_divergence_report.md").exists()
