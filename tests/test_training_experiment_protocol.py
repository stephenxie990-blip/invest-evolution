from pathlib import Path
from types import SimpleNamespace

from app.training.experiment_protocol import (
    ExperimentSpec,
    build_cycle_run_context,
    normalize_cutoff_policy,
    normalize_review_window,
)


def test_experiment_spec_normalizes_core_fields():
    spec = ExperimentSpec.from_payload(
        {
            "spec": {"rounds": 3, "mock": True},
            "protocol": {
                "seed": "7",
                "date_range": {"min": "2025-01-02", "max": "2025-03-04"},
                "review_window": {"mode": "rolling", "size": 5},
                "cutoff_policy": {"mode": "fixed", "date": "2025-02-14"},
            },
            "dataset": {
                "min_history_days": "240",
                "simulation_days": "45",
            },
            "model_scope": {
                "allowed_models": ["value_quality", "momentum", ""],
            },
            "optimization": {
                "promotion_gate": {"min_samples": 4},
            },
            "llm": {"timeout": 9, "dry_run": True},
        }
    )

    payload = spec.to_payload()

    assert spec.seed == 7
    assert spec.llm_mode == "dry_run"
    assert spec.review_window == {"mode": "rolling", "size": 5}
    assert spec.cutoff_policy == {
        "mode": "fixed",
        "date": "20250214",
        "anchor_date": "",
        "step_days": 30,
        "dates": [],
    }
    assert spec.promotion_policy == {"min_samples": 4}
    assert payload["protocol"]["date_range"] == {"min": "20250102", "max": "20250304"}
    assert payload["protocol"]["review_window"] == {"mode": "rolling", "size": 5}
    assert payload["protocol"]["cutoff_policy"]["mode"] == "fixed"
    assert payload["protocol"]["cutoff_policy"]["date"] == "20250214"
    assert payload["protocol"]["promotion_policy"] == {"min_samples": 4}
    assert payload["dataset"]["min_history_days"] == 240
    assert payload["dataset"]["simulation_days"] == 45
    assert payload["model_scope"]["allowed_models"] == ["value_quality", "momentum"]
    assert payload["llm"]["mode"] == "dry_run"


def test_normalize_review_window_forces_single_cycle_size_to_one():
    assert normalize_review_window({"mode": "single_cycle", "size": 5}) == {
        "mode": "single_cycle",
        "size": 1,
    }


def test_normalize_cutoff_policy_supports_regime_balanced_mode():
    assert normalize_cutoff_policy(
        {
            "mode": "regime_balanced",
            "probe_count": 5,
            "target_regimes": ["bear", "bull", "bear", "oops"],
            "fallback_mode": "rolling",
            "anchor_date": "2025-01-02",
            "step_days": 12,
        }
    ) == {
        "mode": "regime_balanced",
        "date": "",
        "anchor_date": "20250102",
        "step_days": 12,
        "dates": [],
        "probe_count": 5,
        "min_regime_samples": 0,
        "target_regimes": ["bear", "bull"],
        "fallback_mode": "rolling",
    }


def test_build_cycle_run_context_tracks_candidate_and_review_basis_window():
    controller = SimpleNamespace(
        model_config_path="configs/active.yaml",
        current_params={"position_size": 0.12},
        cycle_history=[
            SimpleNamespace(cycle_id=3),
            SimpleNamespace(cycle_id=4),
            SimpleNamespace(cycle_id=5),
        ],
        experiment_review_window={"mode": "rolling", "size": 4},
        experiment_promotion_policy={"min_samples": 3},
    )
    model_output = SimpleNamespace(config_name="configs/active.yaml", model_name="momentum")
    optimization_events = [
        {
            "stage": "yaml_mutation",
            "decision": {
                "config_path": "data/evolution/generations/candidate.yaml",
                "auto_applied": False,
            },
            "notes": "candidate model config generated; active config unchanged",
        }
    ]

    context = build_cycle_run_context(
        controller,
        cycle_id=6,
        model_output=model_output,
        optimization_events=optimization_events,
    )

    assert context["active_config_ref"] == str(Path("configs/active.yaml").resolve())
    assert context["candidate_config_ref"] == str(
        Path("data/evolution/generations/candidate.yaml").resolve()
    )
    assert context["runtime_overrides"]["position_size"] == 0.12
    assert context["review_basis_window"] == {
        "mode": "rolling",
        "size": 4,
        "cycle_ids": [3, 4, 5, 6],
        "current_cycle_id": 6,
    }
    assert context["fitness_source_cycles"] == [3, 4, 5]
    assert context["promotion_decision"]["status"] == "candidate_generated"
    assert context["promotion_decision"]["applied_to_active"] is False
    assert context["deployment_stage"] == "candidate"
    assert context["promotion_discipline"]["status"] == "candidate_pending"
    assert context["resolved_train_policy"]["promotion_gate"]["min_samples"] == 3
    assert context["resolved_train_policy"]["freeze_gate"]["avg_sharpe_gte"] == 0.8
    assert (
        context["resolved_train_policy"]["quality_gate_matrix"]["routing"]["allowed_deployment_stages"]
        == ["active"]
    )


def test_build_cycle_run_context_uses_candidate_as_active_after_auto_apply():
    controller = SimpleNamespace(
        model_config_path="data/evolution/generations/candidate.yaml",
        current_params={"position_size": 0.12},
        cycle_history=[],
        experiment_review_window={"mode": "single_cycle", "size": 1},
        experiment_promotion_policy={"min_samples": 3},
    )
    model_output = SimpleNamespace(config_name="configs/active.yaml", model_name="momentum")
    optimization_events = [
        {
            "stage": "yaml_mutation",
            "decision": {
                "config_path": "data/evolution/generations/candidate.yaml",
                "auto_applied": True,
            },
            "notes": "active model config mutated",
        }
    ]

    context = build_cycle_run_context(
        controller,
        cycle_id=6,
        model_output=model_output,
        optimization_events=optimization_events,
    )

    expected_ref = str(Path("data/evolution/generations/candidate.yaml").resolve())
    assert context["active_config_ref"] == expected_ref
    assert context["candidate_config_ref"] == expected_ref
    assert context["promotion_decision"]["status"] == "candidate_auto_applied"
    assert context["deployment_stage"] == "active"


def test_build_cycle_run_context_skips_fitness_sources_without_yaml_mutation():
    controller = SimpleNamespace(
        model_config_path="configs/active.yaml",
        current_params={"position_size": 0.12},
        cycle_history=[
            SimpleNamespace(cycle_id=3),
            SimpleNamespace(cycle_id=4),
        ],
        experiment_review_window={"mode": "single_cycle", "size": 9},
        experiment_promotion_policy={"min_samples": 3},
    )

    context = build_cycle_run_context(
        controller,
        cycle_id=5,
        model_output=SimpleNamespace(config_name="configs/active.yaml", model_name="momentum"),
        optimization_events=[{"stage": "review"}],
    )

    assert context["basis_stage"] == "post_cycle_result"
    assert context["review_basis_window"] == {
        "mode": "single_cycle",
        "size": 1,
        "cycle_ids": [5],
        "current_cycle_id": 5,
    }
    assert context["fitness_source_cycles"] == []
    assert context["deployment_stage"] == "active"


def test_build_cycle_run_context_prefers_execution_snapshot_state():
    controller = SimpleNamespace(
        model_config_path="configs/post_review.yaml",
        current_params={"position_size": 0.33},
        cycle_history=[],
        experiment_review_window={"mode": "rolling", "size": 2},
        experiment_promotion_policy={},
    )
    execution_snapshot = {
        "basis_stage": "pre_optimization",
        "active_config_ref": "configs/executed.yaml",
        "runtime_overrides": {"position_size": 0.08, "max_positions": 4},
    }

    context = build_cycle_run_context(
        controller,
        cycle_id=6,
        model_output=SimpleNamespace(config_name="configs/post_review.yaml", model_name="momentum"),
        optimization_events=[],
        execution_snapshot=execution_snapshot,
    )

    assert context["basis_stage"] == "pre_optimization"
    assert context["active_config_ref"] == str(Path("configs/executed.yaml").resolve())
    assert context["runtime_overrides"] == {"position_size": 0.08, "max_positions": 4}
    assert context["quality_gate_matrix"]["routing"]["allowed_deployment_stages"] == ["active"]
