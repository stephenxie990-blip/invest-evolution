import json
import importlib
from pathlib import Path
from types import SimpleNamespace

# Release Imports
from invest_evolution.application.training.observability import (
    summarize_release_gate_run,
    write_release_gate_report,
)
import invest_evolution.application.release as release_module
from invest_evolution.application.release import (
    RELEASE_P0_TESTS,
    ShadowGateThresholds,
    apply_shadow_gate_threshold_overrides,
    build_bundle_command,
    bundle_catalog,
)
from invest_evolution.application.release import evaluate_release_shadow_gate
from scripts.run_release_readiness_gate import (
    MANUAL_SIGNOFF_DOC,
    build_release_readiness_steps,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# --- Helper Functions ---

def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _prepare_shadow_gate_artifacts(run_dir: Path) -> None:
    (run_dir / "runtime_generations").mkdir(parents=True, exist_ok=True)


def test_summarize_release_gate_run_builds_divergence_report(tmp_path):
    run_dir = tmp_path / "run"
    _write_json(run_dir / "cycle_1.json", {"cycle_id": 1, "review_applied": True, "promotion_record": {"gate_status": "override_pending"}})
    _write_json(run_dir / "run_report.json", {"status": "completed", "attempted_cycles": 1, "successful_cycles": 1, "target_met": True})
    summary = summarize_release_gate_run(run_dir, label="test")
    assert summary["window"]["attempted_cycles"] == 1
    assert "legacy_governance" not in summary
    assert "legacy_action_mapping" not in summary


def test_report_release_gate_divergence_uses_observability_module():
    module = importlib.import_module("scripts.report_release_gate_divergence")

    assert module.summarize_release_gate_run is summarize_release_gate_run
    assert module.write_release_gate_report is write_release_gate_report


# --- Release Readiness Gate Tests ---

def test_release_readiness_gate_defaults_include_core_bundles():
    steps = build_release_readiness_steps()
    assert any(step.name == "stage2-p0-web-runtime-bundle" for step in steps)
    assert any(step.name == "stage1-freeze-gate-quick" for step in steps)
    assert all("historical" not in step.name for step in steps)


def test_release_readiness_gate_shadow_smoke_defaults_are_automation_safe():
    steps = build_release_readiness_steps(include_shadow_gate=True)

    run_step = next(step for step in steps if step.name == "stage4-release-shadow-smoke-run")
    verify_step = next(step for step in steps if step.name == "stage4-release-shadow-smoke-verify")

    assert "--llm-dry-run" in run_step.command
    assert run_step.command[run_step.command.index("--cycles") + 1] == "5"
    assert run_step.command[run_step.command.index("--successful-cycles-target") + 1] == "5"
    assert verify_step.command[-2:] == ["--profile", "smoke"]


def test_release_readiness_gate_can_forward_shadow_verify_overrides():
    steps = build_release_readiness_steps(
        include_shadow_gate=True,
        shadow_profile="strict",
        shadow_cycles=8,
        shadow_successful_cycles_target=5,
        shadow_verify_successful_cycles_min=5,
        shadow_verify_validation_pass_count_min=1,
        shadow_verify_promote_count_min=0,
    )

    verify_step = next(step for step in steps if step.name == "stage4-release-shadow-strict-verify")

    assert "--profile" in verify_step.command
    assert verify_step.command[verify_step.command.index("--profile") + 1] == "strict"
    assert verify_step.command[verify_step.command.index("--successful-cycles-min") + 1] == "5"
    assert verify_step.command[verify_step.command.index("--validation-pass-count-min") + 1] == "1"
    assert verify_step.command[verify_step.command.index("--promote-count-min") + 1] == "0"


def test_release_readiness_checklist_documents_gate_and_manual_signoff():
    assert MANUAL_SIGNOFF_DOC == PROJECT_ROOT / "docs" / "RELEASE_READINESS.md"
    checklist = MANUAL_SIGNOFF_DOC.read_text(encoding="utf-8")
    assert "uv run python scripts/run_release_readiness_gate.py --include-commander-brain --include-shadow-gate" in checklist
    assert "uv run python -m invest_evolution.application.release shadow-gate --run-dir <fresh-output-dir> --profile strict" in checklist
    assert "smoke profile" in checklist
    assert "strict profile" in checklist
    assert "Stage 4 Release Shadow Gate" in checklist
    assert "Stage 5 Manual Release Sign-off" in checklist
    assert "deploy public surface `200/404` smoke 通过" in checklist
    assert "WSGI import smoke 与 Gunicorn `--check-config` 通过" in checklist
    assert "runtime mutation 工件只允许落在 fresh output dir 下的 `runtime_generations/`" in checklist
    assert "不存在 `data/evolution/generations/*` 这类 tracked runtime artifact 漂移" in checklist


# --- Release Shadow Gate Tests ---

def test_release_shadow_gate_passes_when_thresholds_satisfied(tmp_path):
    run_dir = tmp_path / "shadow_run"
    _prepare_shadow_gate_artifacts(run_dir)
    _write_json(
        run_dir / "run_report.json",
        {
            "status": "completed",
            "attempted_cycles": 30,
            "successful_cycles": 30,
            "target_met": True,
            "freeze_gate_evaluation": {
                "research_feedback_gate": {
                    "active": True,
                    "passed": True,
                    "sample_count": 18,
                    "bias": "maintain",
                }
            },
        },
    )
    for cycle_id in range(1, 31):
        _write_json(
            run_dir / f"cycle_{cycle_id}.json",
            {
                "cycle_id": cycle_id,
                "cutoff_date": f"202401{cycle_id:02d}",
                "review_applied": False,
                "validation_summary": {
                    "status": "passed" if cycle_id <= 2 else "hold",
                    "reason_codes": [],
                },
                "judge_report": {
                    "decision": "promote" if cycle_id == 1 else "hold",
                    "reason_codes": [],
                },
                "promotion_record": {"gate_status": "awaiting_gate"},
                "lineage_record": {"lineage_status": "active_only"},
            },
        )
    result = evaluate_release_shadow_gate(run_dir, label="pass")
    assert result["passed"] is True


def test_apply_shadow_gate_threshold_overrides_supports_strict_probe_thresholds():
    strict = ShadowGateThresholds(
        profile="strict",
        research_feedback_gate_required=True,
        research_feedback_gate_must_pass=True,
    )

    probe = apply_shadow_gate_threshold_overrides(
        strict,
        successful_cycles_min=5,
        validation_pass_count_min=1,
        promote_count_min=0,
    )

    assert probe.profile == "strict"
    assert probe.successful_cycles_min == 5
    assert probe.validation_pass_count_min == 1
    assert probe.promote_count_min == 0
    assert probe.research_feedback_gate_required is True
    assert probe.research_feedback_gate_must_pass is True


def test_release_shadow_gate_smoke_profile_accepts_pipeline_only_evidence(tmp_path):
    run_dir = tmp_path / "shadow_smoke"
    _prepare_shadow_gate_artifacts(run_dir)
    _write_json(
        run_dir / "run_report.json",
        {
            "status": "completed",
            "attempted_cycles": 1,
            "successful_cycles": 1,
            "target_met": True,
            "freeze_gate_evaluation": {
                "research_feedback_gate": {
                    "active": True,
                    "passed": False,
                    "sample_count": 12,
                    "bias": "tighten_risk",
                }
            },
        },
    )
    _write_json(
        run_dir / "cycle_1.json",
        {
            "cycle_id": 1,
            "cutoff_date": "20240101",
            "review_applied": False,
            "validation_summary": {"status": "hold", "reason_codes": ["insufficient_sample"]},
            "judge_report": {"decision": "continue_optimize", "reason_codes": ["insufficient_sample"]},
            "promotion_record": {"gate_status": "override_pending"},
            "lineage_record": {"lineage_status": "override_pending"},
        },
    )
    result = evaluate_release_shadow_gate(run_dir, profile="smoke", label="smoke")
    assert result["profile"] == "smoke"
    assert result["metrics"]["research_feedback_gate_passed"] is False
    assert result["passed"] is True


def test_release_shadow_gate_rejects_interrupted_run_even_in_smoke_profile(tmp_path):
    run_dir = tmp_path / "shadow_interrupted"
    _prepare_shadow_gate_artifacts(run_dir)
    _write_json(run_dir / "run_report.json", {"status": "interrupted", "attempted_cycles": 1, "successful_cycles": 1, "target_met": False})
    _write_json(
        run_dir / "cycle_1.json",
        {
            "cycle_id": 1,
            "review_applied": False,
            "validation_summary": {"status": "hold", "reason_codes": []},
            "judge_report": {"decision": "hold", "reason_codes": []},
            "promotion_record": {"gate_status": "awaiting_gate"},
            "lineage_record": {"lineage_status": "active_only"},
        },
    )
    result = evaluate_release_shadow_gate(run_dir, profile="smoke", label="interrupted")
    assert result["metrics"]["run_status"] == "interrupted"
    assert result["checks"]["run_status"] is False
    assert result["passed"] is False


def test_release_shadow_gate_strict_profile_requires_active_and_passing_research_gate(tmp_path):
    run_dir = tmp_path / "shadow_strict_research_fail"
    _prepare_shadow_gate_artifacts(run_dir)
    _write_json(
        run_dir / "run_report.json",
        {
            "status": "completed",
            "attempted_cycles": 30,
            "successful_cycles": 30,
            "target_met": True,
            "freeze_gate_evaluation": {
                "research_feedback_gate": {
                    "active": True,
                    "passed": False,
                    "sample_count": 18,
                    "bias": "tighten_risk",
                }
            },
        },
    )
    for cycle_id in range(1, 31):
        _write_json(
            run_dir / f"cycle_{cycle_id}.json",
            {
                "cycle_id": cycle_id,
                "cutoff_date": f"202401{cycle_id:02d}",
                "review_applied": False,
                "validation_summary": {
                    "status": "passed" if cycle_id <= 3 else "hold",
                    "reason_codes": [],
                },
                "judge_report": {
                    "decision": "promote" if cycle_id == 1 else "hold",
                    "reason_codes": [],
                },
                "promotion_record": {"gate_status": "awaiting_gate"},
                "lineage_record": {"lineage_status": "active_only"},
            },
        )

    result = evaluate_release_shadow_gate(run_dir, profile="strict", label="strict_research_fail")

    assert result["metrics"]["research_feedback_gate_active"] is True
    assert result["metrics"]["research_feedback_gate_passed"] is False
    assert result["metrics"]["research_feedback_gate_contract_ready"] is True
    assert result["checks"]["research_feedback_gate_contract_ready"] is True
    assert result["checks"]["research_feedback_gate_passed"] is False
    assert result["passed"] is False


def test_release_shadow_gate_strict_profile_rejects_non_actionable_inactive_research_gate(tmp_path):
    run_dir = tmp_path / "shadow_strict_research_inactive"
    _prepare_shadow_gate_artifacts(run_dir)
    _write_json(
        run_dir / "run_report.json",
        {
            "status": "completed",
            "attempted_cycles": 30,
            "successful_cycles": 30,
            "target_met": True,
            "freeze_gate_evaluation": {
                "research_feedback_gate": {
                    "active": False,
                    "passed": True,
                    "reason": "insufficient_samples",
                    "sample_count": 0,
                    "bias": "insufficient_samples",
                }
            },
        },
    )
    for cycle_id in range(1, 31):
        _write_json(
            run_dir / f"cycle_{cycle_id}.json",
            {
                "cycle_id": cycle_id,
                "cutoff_date": f"202401{cycle_id:02d}",
                "review_applied": False,
                "validation_summary": {
                    "status": "passed" if cycle_id <= 3 else "hold",
                    "reason_codes": [],
                },
                "judge_report": {
                    "decision": "promote" if cycle_id == 1 else "hold",
                    "reason_codes": [],
                },
                "promotion_record": {"gate_status": "awaiting_gate"},
                "lineage_record": {"lineage_status": "active_only"},
            },
        )

    result = evaluate_release_shadow_gate(run_dir, profile="strict", label="strict_research_inactive")

    assert result["metrics"]["research_feedback_gate_active"] is False
    assert result["metrics"]["research_feedback_gate_contract_ready"] is False
    assert result["checks"]["research_feedback_gate_contract_ready"] is False
    assert result["passed"] is False


def test_release_shadow_gate_strict_profile_accepts_requested_regime_unavailable_gate(tmp_path):
    run_dir = tmp_path / "shadow_strict_requested_regime_unavailable"
    _prepare_shadow_gate_artifacts(run_dir)
    _write_json(
        run_dir / "run_report.json",
        {
            "status": "completed",
            "attempted_cycles": 30,
            "successful_cycles": 30,
            "target_met": True,
            "freeze_gate_evaluation": {
                "research_feedback_gate": {
                    "active": False,
                    "passed": True,
                    "reason": "requested_regime_feedback_unavailable",
                    "sample_count": 0,
                    "bias": "maintain",
                }
            },
        },
    )
    for cycle_id in range(1, 31):
        _write_json(
            run_dir / f"cycle_{cycle_id}.json",
            {
                "cycle_id": cycle_id,
                "cutoff_date": f"202401{cycle_id:02d}",
                "review_applied": False,
                "validation_summary": {
                    "status": "passed" if cycle_id <= 3 else "hold",
                    "reason_codes": [],
                },
                "judge_report": {
                    "decision": "promote" if cycle_id == 1 else "hold",
                    "reason_codes": [],
                },
                "promotion_record": {"gate_status": "awaiting_gate"},
                "lineage_record": {"lineage_status": "active_only"},
            },
        )

    result = evaluate_release_shadow_gate(
        run_dir,
        profile="strict",
        label="strict_requested_regime_unavailable",
    )

    assert result["metrics"]["research_feedback_gate_active"] is False
    assert result["metrics"]["research_feedback_gate_passed"] is True
    assert (
        result["metrics"]["research_feedback_gate_reason"]
        == "requested_regime_feedback_unavailable"
    )
    assert result["metrics"]["research_feedback_gate_contract_ready"] is True
    assert result["checks"]["research_feedback_gate_contract_ready"] is True
    assert result["checks"]["research_feedback_gate_passed"] is True
    assert result["passed"] is True


def test_release_shadow_gate_strict_profile_accepts_passing_research_gate(tmp_path):
    run_dir = tmp_path / "shadow_strict_research_pass"
    _prepare_shadow_gate_artifacts(run_dir)
    _write_json(
        run_dir / "run_report.json",
        {
            "status": "completed",
            "attempted_cycles": 30,
            "successful_cycles": 30,
            "target_met": True,
            "freeze_gate_evaluation": {
                "research_feedback_gate": {
                    "active": True,
                    "passed": True,
                    "sample_count": 20,
                    "bias": "maintain",
                }
            },
        },
    )
    for cycle_id in range(1, 31):
        _write_json(
            run_dir / f"cycle_{cycle_id}.json",
            {
                "cycle_id": cycle_id,
                "cutoff_date": f"202401{cycle_id:02d}",
                "review_applied": False,
                "validation_summary": {
                    "status": "passed" if cycle_id <= 3 else "hold",
                    "reason_codes": [],
                },
                "judge_report": {
                    "decision": "promote" if cycle_id == 1 else "hold",
                    "reason_codes": [],
                },
                "promotion_record": {"gate_status": "awaiting_gate"},
                "lineage_record": {"lineage_status": "active_only"},
            },
        )

    result = evaluate_release_shadow_gate(run_dir, profile="strict", label="strict_research_pass")

    assert result["metrics"]["research_feedback_gate_active"] is True
    assert result["metrics"]["research_feedback_gate_passed"] is True
    assert result["metrics"]["research_feedback_gate_contract_ready"] is True
    assert result["checks"]["research_feedback_gate_contract_ready"] is True
    assert result["checks"]["research_feedback_gate_passed"] is True
    assert result["passed"] is True


def test_release_shadow_gate_accepts_probe_overrides_without_dropping_strict_research_requirements(tmp_path):
    run_dir = tmp_path / "shadow_strict_probe"
    _prepare_shadow_gate_artifacts(run_dir)
    _write_json(
        run_dir / "run_report.json",
        {
            "status": "completed",
            "attempted_cycles": 8,
            "successful_cycles": 5,
            "target_met": True,
            "freeze_gate_evaluation": {
                "research_feedback_gate": {
                    "active": True,
                    "passed": True,
                    "sample_count": 10,
                    "bias": "maintain",
                }
            },
        },
    )
    for cycle_id in range(1, 6):
        _write_json(
            run_dir / f"cycle_{cycle_id}.json",
            {
                "cycle_id": cycle_id,
                "cutoff_date": f"202402{cycle_id:02d}",
                "review_applied": False,
                "validation_summary": {
                    "status": "passed" if cycle_id == 1 else "hold",
                    "reason_codes": [],
                },
                "judge_report": {
                    "decision": "hold",
                    "reason_codes": [],
                },
                "promotion_record": {"gate_status": "awaiting_gate"},
                "lineage_record": {"lineage_status": "active_only"},
            },
        )

    thresholds = apply_shadow_gate_threshold_overrides(
        ShadowGateThresholds(
            profile="strict",
            research_feedback_gate_required=True,
            research_feedback_gate_must_pass=True,
        ),
        successful_cycles_min=5,
        validation_pass_count_min=1,
        promote_count_min=0,
    )

    result = evaluate_release_shadow_gate(run_dir, thresholds=thresholds, profile="strict", label="strict_probe")

    assert result["metrics"]["successful_cycles"] == 5
    assert result["metrics"]["research_feedback_gate_active"] is True
    assert result["metrics"]["research_feedback_gate_passed"] is True
    assert result["metrics"]["research_feedback_gate_contract_ready"] is True
    assert result["checks"]["successful_cycles"] is True
    assert result["checks"]["validation_pass_count"] is True
    assert result["checks"]["promote_count"] is True
    assert result["checks"]["research_feedback_gate_contract_ready"] is True
    assert result["checks"]["research_feedback_gate_passed"] is True
    assert result["passed"] is True


def test_release_shadow_gate_requires_runtime_generations_dir(tmp_path):
    run_dir = tmp_path / "shadow_missing_runtime_generations"
    _write_json(
        run_dir / "run_report.json",
        {
            "status": "completed",
            "attempted_cycles": 1,
            "successful_cycles": 1,
            "target_met": True,
            "freeze_gate_evaluation": {
                "research_feedback_gate": {
                    "active": True,
                    "passed": False,
                    "sample_count": 12,
                    "bias": "maintain",
                }
            },
        },
    )
    _write_json(
        run_dir / "cycle_1.json",
        {
            "cycle_id": 1,
            "cutoff_date": "20240101",
            "review_applied": False,
            "validation_summary": {"status": "hold", "reason_codes": []},
            "judge_report": {"decision": "continue_optimize", "reason_codes": []},
            "promotion_record": {"gate_status": "override_pending"},
            "lineage_record": {"lineage_status": "override_pending"},
        },
    )

    result = evaluate_release_shadow_gate(run_dir, profile="smoke", label="missing_runtime_generations")

    assert result["checks"]["runtime_generations_dir"] is False
    assert result["passed"] is False


def test_release_shadow_gate_rejects_legacy_generation_drift(tmp_path):
    run_dir = tmp_path / "shadow_legacy_generation_drift"
    _prepare_shadow_gate_artifacts(run_dir)
    _write_json(
        run_dir / "run_report.json",
        {
            "status": "completed",
            "attempted_cycles": 1,
            "successful_cycles": 1,
            "target_met": True,
            "freeze_gate_evaluation": {
                "research_feedback_gate": {
                    "active": True,
                    "passed": False,
                    "sample_count": 12,
                    "bias": "maintain",
                }
            },
        },
    )
    _write_json(
        run_dir / "cycle_1.json",
        {
            "cycle_id": 1,
            "cutoff_date": "20240101",
            "review_applied": False,
            "validation_summary": {"status": "hold", "reason_codes": []},
            "judge_report": {"decision": "continue_optimize", "reason_codes": []},
            "promotion_record": {"gate_status": "override_pending"},
            "lineage_record": {"lineage_status": "override_pending"},
        },
    )
    _write_json(
        run_dir / "data" / "evolution" / "generations" / "cycle_0001.json",
        {"cycle_id": 1},
    )

    result = evaluate_release_shadow_gate(run_dir, profile="smoke", label="legacy_generation_drift")

    assert result["checks"]["legacy_generation_paths"] is False
    assert result["passed"] is False


# --- Release Verification Tests ---

def test_release_verification_catalog_contains_expected_bundles():
    catalog = bundle_catalog()
    assert "p0" in catalog
    assert "performance-regression" in catalog
    assert catalog["p0"].tests == RELEASE_P0_TESTS


def test_release_verification_builds_pytest_command():
    command = build_bundle_command("p0")
    assert "pytest" in " ".join(command)
    assert "tests/test_web_server_security.py" in command


def test_release_verification_main_runs_from_repo_root(monkeypatch):
    calls = []

    def _fake_run(command, cwd):
        calls.append({"command": list(command), "cwd": Path(cwd)})
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(release_module.subprocess, "run", _fake_run)

    result = release_module.release_verification_main(["--bundle", "p0"])

    assert result == 7
    assert calls == [{"command": build_bundle_command("p0"), "cwd": PROJECT_ROOT}]


def test_performance_benchmark_governance_doc_tracks_release_bundle():
    doc = (PROJECT_ROOT / "docs" / "PERFORMANCE_BENCHMARK_GOVERNANCE.md").read_text(encoding="utf-8")
    assert "performance-regression" in doc
    assert "tests/test_memory.py" in doc
    assert "tests/test_market_data_ingestion.py" in doc
    assert "src/invest_evolution/investment/foundation/compute.py" in doc
    assert "src/invest_evolution/market_data/manager.py" in doc
    assert "indicators_v2.py" not in doc
