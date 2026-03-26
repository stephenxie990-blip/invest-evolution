import json
from pathlib import Path
from types import SimpleNamespace

import app.validation.prephase1 as prephase1_module
from app.validation.prephase1 import (
    build_legacy_term_summary,
    build_candidate_resolution_summary,
    build_prephase1_validation_spec,
    extract_latest_candidate,
    extract_latest_proposal_gate,
    run_legacy_audit_backfill,
    run_candidate_resolution_validation,
    run_terminal_candidate_resolution_from_existing_run,
    run_terminal_candidate_resolution_validation,
    resolve_validation_cutoff_dates,
    run_prephase1_validation,
)


def test_build_prephase1_validation_spec_defaults_to_validation_mode():
    spec = build_prephase1_validation_spec(
        model_name="momentum",
        cutoff_dates=["20240201", "20240208"],
        min_history_days=180,
        simulation_days=30,
        dry_run_llm=True,
        runtime_train_overrides={"max_losses_before_optimize": 1},
    )

    assert spec["model_scope"]["experiment_mode"] == "validation"
    assert spec["model_scope"]["allowed_models"] == ["momentum"]
    assert spec["model_scope"]["model_routing_enabled"] is False
    assert spec["optimization"]["runtime_train_overrides"] == {
        "max_losses_before_optimize": 1,
    }
    assert spec["llm"]["dry_run"] is True


def test_resolve_validation_cutoff_dates_supports_source_run_and_limit(tmp_path):
    (tmp_path / "cycle_2.json").write_text(
        json.dumps({"cycle_id": 2, "cutoff_date": "2024-02-08"}),
        encoding="utf-8",
    )
    (tmp_path / "cycle_1.json").write_text(
        json.dumps({"cycle_id": 1, "cutoff_date": "2024-02-01"}),
        encoding="utf-8",
    )

    resolved = resolve_validation_cutoff_dates(
        cutoff_source_run=tmp_path,
        limit=1,
    )

    assert resolved == ["20240201"]


def test_extract_latest_candidate_and_proposal_gate():
    cycles = [
        {
            "cycle_id": 1,
            "optimization_events": [
                {
                    "stage": "candidate_build",
                    "decision": {
                        "config_path": "/tmp/candidate_1.yaml",
                        "candidate_version_id": "version_a",
                        "candidate_runtime_fingerprint": "fingerprint_a",
                    },
                    "applied_change": {
                        "proposal_refs": ["proposal_0001_001"],
                    },
                    "evidence": {
                        "proposal_gate": {
                            "proposal_summary": {
                                "approved_proposal_count": 1,
                            }
                        }
                    },
                }
            ],
        },
        {
            "cycle_id": 2,
            "optimization_events": [
                {
                    "stage": "candidate_build_skipped",
                    "decision": {
                        "skip_reason": "pending_candidate_unresolved",
                        "pending_candidate_ref": "/tmp/candidate_1.yaml",
                    },
                    "evidence": {
                        "proposal_gate": {
                            "proposal_summary": {
                                "approved_proposal_count": 0,
                            },
                            "violations": [{"type": "single_step_identity_drift_exceeded"}],
                        }
                    },
                }
            ],
        },
    ]

    latest_candidate = extract_latest_candidate(cycles)
    latest_gate = extract_latest_proposal_gate(cycles)

    assert latest_candidate["cycle_id"] == 2
    assert latest_candidate["config_path"] == "/tmp/candidate_1.yaml"
    assert latest_candidate["candidate_version_id"] == "version_a"
    assert latest_candidate["proposal_refs"] == ["proposal_0001_001"]
    assert latest_gate["cycle_id"] == 2
    assert latest_gate["approved"] is False
    assert latest_gate["violations"][0]["type"] == "single_step_identity_drift_exceeded"


def test_build_legacy_term_summary_canonicalizes_legacy_aliases():
    cycles = [
        {
            "cycle_id": 1,
            "optimization_events": [
                {"stage": "yaml_mutation"},
                {"stage": "runtime_yaml_mutation"},
            ],
            "promotion_record": {
                "source": "runtime_yaml_mutation",
            },
            "lineage_record": {
                "mutation_source": "runtime_yaml_mutation",
            },
        }
    ]

    summary = build_legacy_term_summary(cycles)

    assert summary["legacy_terms_present"] is True
    assert summary["legacy_stage_counts"] == {"yaml_mutation": 1}
    assert summary["canonical_stage_counts"]["candidate_build"] == 1
    assert summary["canonical_stage_counts"]["runtime_yaml_mutation"] == 1
    assert summary["legacy_source_counts"] == {"runtime_yaml_mutation": 2}
    assert summary["canonical_source_counts"] == {"runtime_candidate_builder": 2}
    assert summary["canonicalization_map"]["stages"]["yaml_mutation"] == "candidate_build"
    assert (
        summary["canonicalization_map"]["sources"]["runtime_yaml_mutation"]
        == "runtime_candidate_builder"
    )


def test_build_candidate_resolution_summary_tracks_pending_to_expired_candidate():
    candidate_ref = "/tmp/candidate_1.yaml"
    cycles = [
        {
            "cycle_id": 2,
            "cutoff_date": "20240208",
            "optimization_events": [
                {
                    "stage": "candidate_build",
                    "decision": {
                        "config_path": candidate_ref,
                        "candidate_version_id": "version_a",
                        "candidate_runtime_fingerprint": "fingerprint_a",
                    },
                    "applied_change": {"proposal_refs": ["proposal_0002_001"]},
                }
            ],
            "lineage_record": {
                "lineage_status": "candidate_pending",
                "deployment_stage": "candidate",
                "candidate_config_ref": candidate_ref,
                "candidate_version_id": "version_a",
                "candidate_runtime_fingerprint": "fingerprint_a",
            },
            "promotion_record": {
                "gate_status": "awaiting_gate",
                "candidate_config_ref": candidate_ref,
            },
        },
        {
            "cycle_id": 3,
            "cutoff_date": "20240215",
            "optimization_events": [
                {
                    "stage": "candidate_build_skipped",
                    "decision": {"pending_candidate_ref": candidate_ref},
                }
            ],
            "lineage_record": {
                "lineage_status": "candidate_pending",
                "deployment_stage": "candidate",
                "candidate_config_ref": candidate_ref,
            },
            "promotion_record": {
                "gate_status": "awaiting_gate",
                "candidate_config_ref": candidate_ref,
            },
        },
        {
            "cycle_id": 5,
            "cutoff_date": "20240229",
            "lineage_record": {
                "lineage_status": "candidate_expired",
                "deployment_stage": "candidate",
                "candidate_config_ref": candidate_ref,
                "promotion_discipline": {
                    "status": "candidate_expired",
                    "violations": ["max_pending_cycles"],
                },
            },
            "promotion_record": {
                "gate_status": "rejected",
                "status": "candidate_expired",
                "candidate_config_ref": candidate_ref,
                "reason": "max_pending_cycles",
            },
        },
    ]

    summary = build_candidate_resolution_summary(
        cycles,
        target_candidate_ref=candidate_ref,
    )

    assert summary["candidate_count"] == 1
    assert summary["resolved_candidate_count"] == 1
    assert summary["resolution_status_counts"] == {"candidate_expired": 1}
    assert summary["focus_candidate"]["resolved"] is True
    assert summary["focus_candidate"]["resolved_cycle_id"] == 5
    assert summary["focus_candidate"]["proposal_refs"] == ["proposal_0002_001"]
    assert len(summary["focus_candidate"]["path"]) == 3


def test_run_candidate_resolution_validation_persists_summary(tmp_path):
    candidate_ref = str(tmp_path / "candidate_1.yaml")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "cycle_1.json").write_text(
        json.dumps(
            {
                "cycle_id": 1,
                "cutoff_date": "20240201",
                "optimization_events": [
                    {
                        "stage": "candidate_build",
                        "decision": {
                            "config_path": candidate_ref,
                            "candidate_version_id": "version_a",
                            "candidate_runtime_fingerprint": "fingerprint_a",
                        },
                    }
                ],
                "lineage_record": {
                    "lineage_status": "candidate_pending",
                    "deployment_stage": "candidate",
                    "candidate_config_ref": candidate_ref,
                },
                "promotion_record": {
                    "gate_status": "awaiting_gate",
                    "candidate_config_ref": candidate_ref,
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "cycle_2.json").write_text(
        json.dumps(
            {
                "cycle_id": 2,
                "cutoff_date": "20240208",
                "lineage_record": {
                    "lineage_status": "candidate_pruned",
                    "deployment_stage": "candidate",
                    "candidate_config_ref": candidate_ref,
                    "promotion_discipline": {
                        "status": "candidate_pruned",
                        "violations": ["failed_candidate_ab"],
                    },
                },
                "promotion_record": {
                    "gate_status": "rejected",
                    "status": "candidate_pruned",
                    "candidate_config_ref": candidate_ref,
                    "reason": "failed_candidate_ab",
                },
            }
        ),
        encoding="utf-8",
    )

    summary = run_candidate_resolution_validation(
        run_dir=run_dir,
        target_candidate_ref=candidate_ref,
    )

    persisted_path = Path(summary["candidate_resolution_summary_path"])
    persisted = json.loads(persisted_path.read_text(encoding="utf-8"))

    assert summary["resolution_status_counts"]["candidate_pruned"] == 1
    assert summary["focus_candidate"]["final_status"] == "candidate_pruned"
    assert persisted["candidate_resolution_summary_path"] == str(persisted_path)


def test_run_prephase1_validation_persists_summary_and_uses_standardized_spec(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class FakeController:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = dict(kwargs)
            self.training_routing_service = SimpleNamespace(
                reload_investment_model=self._reload_runtime_model
            )
            self.stop_on_freeze = True
            self.model_name = "momentum"
            self.model_config_path = "configs/momentum.yaml"
            self.current_params = {"position_size": 0.2}
            self.llm_mode = "live"

        def _reload_runtime_model(self, owner, config_path):
            captured["reloaded_config_path"] = config_path

        def configure_experiment(self, spec):
            captured["experiment_spec"] = dict(spec)

        def set_llm_dry_run(self, enabled=True):
            self.llm_mode = "dry_run" if enabled else "live"

        def run_continuous(self, max_cycles=0):
            captured["max_cycles"] = max_cycles
            return {"status": "completed", "successful_cycles": max_cycles}

    monkeypatch.setattr(prephase1_module, "SelfLearningController", FakeController)
    monkeypatch.setattr(
        prephase1_module,
        "resolve_model_config_path",
        lambda model_name: Path(tmp_path / f"{model_name}.yaml"),
    )
    monkeypatch.setattr(
        prephase1_module,
        "load_controller_run_summary",
        lambda run_dir: {
            "summary": {"completed_cycle_count": 2, "profit_cycle_count": 1},
            "cycles": [
                {
                    "cycle_id": 1,
                    "optimization_events": [
                        {
                            "stage": "candidate_build",
                            "decision": {
                                "config_path": str(tmp_path / "candidate_1.yaml"),
                                "candidate_version_id": "version_a",
                                "candidate_runtime_fingerprint": "fingerprint_a",
                            },
                            "applied_change": {"proposal_refs": ["proposal_0001_001"]},
                            "evidence": {
                                "proposal_gate": {
                                    "proposal_summary": {"approved_proposal_count": 1}
                                }
                            },
                        }
                    ],
                }
            ],
        },
    )

    summary = run_prephase1_validation(
        model_name="momentum",
        cutoff_dates=["20240201", "20240208"],
        output_dir=tmp_path / "run",
        min_history_days=180,
        simulation_days=30,
        dry_run_llm=True,
        runtime_train_overrides={"max_losses_before_optimize": 1},
    )

    summary_path = Path(summary["validation_summary_path"])
    persisted = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["run_type"] == "prephase1_validation"
    assert summary["schema_version"] == "prephase1.validation_summary.v2"
    assert summary["terminology_version"] == "2026-03-16.prephase1_closure_v1"
    assert summary["llm_mode"] == "dry_run"
    assert summary["runtime_train_overrides"] == {"max_losses_before_optimize": 1}
    assert summary["audit_semantics"]["schema_version"] == "training.audit_summary.v1"
    assert summary["latest_candidate"]["candidate_version_id"] == "version_a"
    assert summary["latest_proposal_gate"]["approved"] is True
    assert summary["candidate_resolution_summary"]["candidate_count"] == 1
    assert summary["candidate_resolution_summary"]["unresolved_candidate_count"] == 1
    assert summary["audit_summary"]["candidate_resolution"]["candidate_count"] == 1
    assert captured["max_cycles"] == 2
    assert captured["reloaded_config_path"] == str(tmp_path / "momentum.yaml")
    assert captured["experiment_spec"]["model_scope"]["experiment_mode"] == "validation"
    assert captured["experiment_spec"]["optimization"]["runtime_train_overrides"] == {
        "max_losses_before_optimize": 1,
    }
    assert persisted["validation_summary_path"] == str(summary_path)
    assert Path(summary["normalized_validation_summary_path"]).exists()
    assert Path(summary["candidate_resolution_summary"]["candidate_resolution_summary_path"]).exists()


def test_run_legacy_audit_backfill_persists_normalized_summary(tmp_path):
    candidate_ref = str(tmp_path / "candidate_1.yaml")
    run_dir = tmp_path / "legacy_run"
    output_dir = tmp_path / "backfill"
    run_dir.mkdir()
    (run_dir / "validation_summary.json").write_text(
        json.dumps(
            {
                "model_name": "momentum",
                "model_config_path": "configs/momentum.yaml",
                "llm_mode": "dry_run",
                "cutoff_dates": ["20240201", "20240208"],
                "report": {
                    "freeze_applied": False,
                    "governance_metrics": {"active_candidate_drift_rate": 0.0},
                    "proposal_gate_summary": {"approved_proposal_count": 1},
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "cycle_1.json").write_text(
        json.dumps(
            {
                "cycle_id": 1,
                "cutoff_date": "20240201",
                "model_name": "momentum",
                "config_name": "configs/momentum.yaml",
                "optimization_events": [
                    {
                        "stage": "candidate_build",
                        "decision": {
                            "config_path": candidate_ref,
                            "candidate_version_id": "version_a",
                            "candidate_runtime_fingerprint": "fingerprint_a",
                        },
                        "applied_change": {"proposal_refs": ["proposal_0001_001"]},
                    }
                ],
                "lineage_record": {
                    "lineage_status": "candidate_pending",
                    "deployment_stage": "candidate",
                    "candidate_config_ref": candidate_ref,
                },
                "promotion_record": {
                    "gate_status": "awaiting_gate",
                    "candidate_config_ref": candidate_ref,
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "cycle_2.json").write_text(
        json.dumps(
            {
                "cycle_id": 2,
                "cutoff_date": "20240208",
                "model_name": "momentum",
                "config_name": "configs/momentum.yaml",
                "lineage_record": {
                    "lineage_status": "candidate_pruned",
                    "deployment_stage": "candidate",
                    "candidate_config_ref": candidate_ref,
                    "promotion_discipline": {
                        "status": "candidate_pruned",
                        "violations": ["failed_candidate_ab"],
                    },
                },
                "promotion_record": {
                    "gate_status": "rejected",
                    "status": "candidate_pruned",
                    "candidate_config_ref": candidate_ref,
                    "reason": "failed_candidate_ab",
                },
            }
        ),
        encoding="utf-8",
    )

    summary = run_legacy_audit_backfill(
        run_dir=run_dir,
        output_dir=output_dir,
        target_candidate_ref=candidate_ref,
    )

    normalized_path = Path(summary["normalized_validation_summary_path"])
    persisted = json.loads(normalized_path.read_text(encoding="utf-8"))

    assert summary["run_type"] == "legacy_audit_backfill"
    assert summary["source_run_dir"] == str(run_dir.resolve())
    assert summary["output_dir"] == str(output_dir.resolve())
    assert summary["candidate_resolution_summary"]["focus_candidate"]["final_status"] == "candidate_pruned"
    assert summary["validation_summary_written"] is False
    assert summary["validation_summary_path"] == ""
    assert summary["legacy_term_summary"]["legacy_terms_present"] is False
    assert normalized_path.exists()
    assert Path(summary["candidate_resolution_summary"]["candidate_resolution_summary_path"]).exists()
    assert not (output_dir / "validation_summary.json").exists()
    assert persisted["backfill"]["source_validation_summary_present"] is True
    assert persisted["backfill"]["source_validation_summary_path"].endswith("validation_summary.json")
    assert persisted["validation_summary_written"] is False
    assert persisted["validation_summary_path"] == ""
    assert persisted["legacy_term_summary"]["legacy_terms_present"] is False


def test_run_legacy_audit_backfill_derives_report_without_existing_summary(tmp_path):
    candidate_ref = str(tmp_path / "candidate_1.yaml")
    run_dir = tmp_path / "legacy_run_no_summary"
    run_dir.mkdir()
    (run_dir / "cycle_1.json").write_text(
        json.dumps(
            {
                "cycle_id": 1,
                "cutoff_date": "20240201",
                "model_name": "momentum",
                "config_name": "configs/momentum.yaml",
                "is_profit": True,
                "optimization_events": [
                    {
                        "stage": "candidate_build",
                        "decision": {
                            "config_path": candidate_ref,
                            "candidate_version_id": "version_a",
                        },
                    }
                ],
                "lineage_record": {
                    "lineage_status": "candidate_pending",
                    "deployment_stage": "candidate",
                    "candidate_config_ref": candidate_ref,
                },
                "promotion_record": {
                    "gate_status": "awaiting_gate",
                    "candidate_config_ref": candidate_ref,
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "cycle_2.json").write_text(
        json.dumps(
            {
                "cycle_id": 2,
                "cutoff_date": "20240208",
                "model_name": "momentum",
                "config_name": "configs/momentum.yaml",
                "is_profit": False,
                "lineage_record": {
                    "lineage_status": "candidate_pruned",
                    "deployment_stage": "candidate",
                    "candidate_config_ref": candidate_ref,
                },
                "promotion_record": {
                    "gate_status": "rejected",
                    "status": "candidate_pruned",
                    "candidate_config_ref": candidate_ref,
                },
            }
        ),
        encoding="utf-8",
    )

    summary = run_legacy_audit_backfill(
        run_dir=run_dir,
        target_candidate_ref=candidate_ref,
    )

    assert summary["report"]["successful_cycles"] == 2
    assert summary["report"]["profit_cycles"] == 1
    assert summary["validation_summary_written"] is False
    assert summary["candidate_resolution_summary"]["focus_candidate"]["final_status"] == "candidate_pruned"


def test_run_terminal_candidate_resolution_validation_advances_to_terminal_status(
    monkeypatch,
    tmp_path,
):
    captured: dict[str, object] = {}
    candidate_ref = str(tmp_path / "candidate_1.yaml")

    class FakeTerminalController:
        def __init__(self, **kwargs):
            self.output_dir = Path(kwargs["output_dir"])
            self.training_routing_service = SimpleNamespace(
                reload_investment_model=self._reload_runtime_model
            )
            self.stop_on_freeze = False
            self.model_name = "momentum"
            self.model_config_path = str(tmp_path / "momentum.yaml")
            self.current_params = {}
            self.current_cycle_id = 0
            self.llm_mode = "live"
            self.experiment_cutoff_policy = {"mode": "sequence", "dates": []}

        def _reload_runtime_model(self, owner, config_path):
            captured["reloaded_config_path"] = config_path

        def configure_experiment(self, spec):
            captured["experiment_spec"] = dict(spec)
            self.experiment_cutoff_policy = dict(spec["protocol"]["cutoff_policy"])

        def set_llm_dry_run(self, enabled=True):
            self.llm_mode = "dry_run" if enabled else "live"

        def run_continuous(self, max_cycles=0):
            run_calls = list(captured.get("run_calls") or [])
            run_calls.append(max_cycles)
            captured["run_calls"] = run_calls
            for _ in range(max_cycles):
                self.current_cycle_id += 1
                cycle_id = self.current_cycle_id
                cutoff_date = self.experiment_cutoff_policy["dates"][cycle_id - 1]
                payload = {
                    "cycle_id": cycle_id,
                    "cutoff_date": cutoff_date,
                    "model_name": self.model_name,
                    "config_name": self.model_config_path,
                    "llm_mode": self.llm_mode,
                    "params": {"position_size": 0.2},
                }
                if cycle_id == 1:
                    payload.update(
                        {
                            "optimization_events": [
                                {
                                    "stage": "candidate_build",
                                    "decision": {
                                        "config_path": candidate_ref,
                                        "candidate_version_id": "version_a",
                                        "candidate_runtime_fingerprint": "fingerprint_a",
                                    },
                                    "applied_change": {
                                        "proposal_refs": ["proposal_0001_001"]
                                    },
                                }
                            ],
                            "lineage_record": {
                                "lineage_status": "candidate_pending",
                                "deployment_stage": "candidate",
                                "candidate_config_ref": candidate_ref,
                                "candidate_version_id": "version_a",
                                "candidate_runtime_fingerprint": "fingerprint_a",
                            },
                            "promotion_record": {
                                "gate_status": "awaiting_gate",
                                "candidate_config_ref": candidate_ref,
                            },
                        }
                    )
                elif cycle_id == 2:
                    payload.update(
                        {
                            "optimization_events": [
                                {
                                    "stage": "candidate_build_skipped",
                                    "decision": {
                                        "pending_candidate_ref": candidate_ref,
                                        "skip_reason": "pending_candidate_unresolved",
                                    },
                                }
                            ],
                            "lineage_record": {
                                "lineage_status": "candidate_pending",
                                "deployment_stage": "candidate",
                                "candidate_config_ref": candidate_ref,
                            },
                            "promotion_record": {
                                "gate_status": "awaiting_gate",
                                "candidate_config_ref": candidate_ref,
                            },
                        }
                    )
                else:
                    payload.update(
                        {
                            "lineage_record": {
                                "lineage_status": "candidate_pruned",
                                "deployment_stage": "candidate",
                                "candidate_config_ref": candidate_ref,
                                "promotion_discipline": {
                                    "status": "candidate_pruned",
                                    "violations": ["failed_candidate_ab"],
                                },
                            },
                            "promotion_record": {
                                "gate_status": "rejected",
                                "status": "candidate_pruned",
                                "candidate_config_ref": candidate_ref,
                                "reason": "failed_candidate_ab",
                            },
                        }
                    )
                (self.output_dir / f"cycle_{cycle_id}.json").write_text(
                    json.dumps(payload),
                    encoding="utf-8",
                )
            return {
                "freeze_applied": False,
                "governance_metrics": {"active_candidate_drift_rate": 0.0},
                "proposal_gate_summary": {"approved_proposal_count": 1},
            }

    monkeypatch.setattr(prephase1_module, "SelfLearningController", FakeTerminalController)
    monkeypatch.setattr(
        prephase1_module,
        "resolve_model_config_path",
        lambda model_name: Path(tmp_path / f"{model_name}.yaml"),
    )

    summary = run_terminal_candidate_resolution_validation(
        model_name="momentum",
        cutoff_dates=["20240201", "20240208"],
        followup_cutoff_dates=["20240215", "20240222"],
        max_followup_cycles=2,
        output_dir=tmp_path / "terminal_run",
        min_history_days=180,
        simulation_days=30,
        dry_run_llm=True,
    )

    assert summary["run_type"] == "terminal_candidate_resolution_validation"
    assert summary["terminal_resolution"]["terminal_reached"] is True
    assert summary["terminal_resolution"]["terminal_status"] == "candidate_pruned"
    assert summary["terminal_resolution"]["followup_cycle_count"] == 1
    assert summary["candidate_resolution_summary"]["focus_candidate"]["resolved"] is True
    assert summary["candidate_resolution_summary"]["focus_candidate"]["resolved_cycle_id"] == 3
    assert Path(summary["validation_summary_path"]).exists()
    assert Path(summary["normalized_validation_summary_path"]).exists()
    assert captured["run_calls"] == [2, 1]


def test_run_terminal_candidate_resolution_from_existing_run_delegates_metadata(
    monkeypatch,
    tmp_path,
):
    run_dir = tmp_path / "existing_run"
    run_dir.mkdir()
    (run_dir / "validation_summary.json").write_text(
        json.dumps(
            {
                "model_name": "momentum",
                "model_config_path": str(tmp_path / "momentum.yaml"),
                "llm_mode": "dry_run",
                "cutoff_dates": ["20240201", "20240208"],
                "runtime_train_overrides": {"max_losses_before_optimize": 1},
                "experiment_spec": {
                    "dataset": {
                        "min_history_days": 180,
                        "simulation_days": 30,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        prephase1_module,
        "load_controller_run_summary",
        lambda _: {"cycles": []},
    )

    def fake_terminal_validation(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", **kwargs}

    monkeypatch.setattr(
        prephase1_module,
        "run_terminal_candidate_resolution_validation",
        fake_terminal_validation,
    )

    summary = run_terminal_candidate_resolution_from_existing_run(
        run_dir=run_dir,
        followup_cutoff_dates=["20240215", "20240222"],
        max_followup_cycles=2,
        output_dir=tmp_path / "terminal_cleanup",
        target_candidate_ref=str(tmp_path / "candidate_1.yaml"),
    )

    assert summary["status"] == "ok"
    assert captured["model_name"] == "momentum"
    assert captured["config_path"] == str(tmp_path / "momentum.yaml")
    assert captured["cutoff_dates"] == ["20240201", "20240208"]
    assert captured["min_history_days"] == 180
    assert captured["simulation_days"] == 30
    assert captured["dry_run_llm"] is True
    assert captured["runtime_train_overrides"] == {"max_losses_before_optimize": 1}
    assert captured["followup_cutoff_dates"] == ["20240215", "20240222"]
    assert captured["max_followup_cycles"] == 2
    assert captured["output_dir"] == tmp_path / "terminal_cleanup"
    assert captured["target_candidate_ref"] == str(tmp_path / "candidate_1.yaml")
