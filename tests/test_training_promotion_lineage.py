from types import SimpleNamespace

from app.training.lineage_services import build_lineage_record
from app.training.promotion_services import build_promotion_record


def test_build_lineage_record_tracks_candidate_pending_state():
    controller = SimpleNamespace(model_name="momentum")
    run_context = {
        "active_config_ref": "configs/active.yaml",
        "active_version_id": "version_active_1",
        "active_runtime_fingerprint": "fingerprint_active_1",
        "candidate_config_ref": "data/evolution/generations/momentum_cycle_0006.yaml",
        "candidate_version_id": "version_candidate_1",
        "candidate_runtime_fingerprint": "fingerprint_candidate_1",
        "runtime_overrides": {"position_size": 0.12},
        "fitness_source_cycles": [2, 3, 4, 5],
        "promotion_decision": {
            "status": "candidate_generated",
            "source": "runtime_candidate_builder",
            "reason": "candidate model config generated; active config unchanged",
            "applied_to_active": False,
            "policy": {"min_samples": 4},
        },
    }
    optimization_events = [
        {
            "trigger": "consecutive_losses",
            "stage": "candidate_build",
            "notes": "candidate model config generated; active config unchanged",
        }
    ]
    model_output = SimpleNamespace(model_name="momentum", config_name="configs/active.yaml")

    record = build_lineage_record(
        controller,
        cycle_id=6,
        model_output=model_output,
        run_context=run_context,
        optimization_events=optimization_events,
    )

    assert record["lineage_status"] == "candidate_pending"
    assert record["deployment_stage"] == "candidate"
    assert record["candidate_meta_ref"].endswith(".json")
    assert record["fitness_source_cycles"] == [2, 3, 4, 5]
    assert record["mutation_trigger"] == "consecutive_losses"
    assert record["active_version_id"] == "version_active_1"
    assert record["candidate_version_id"] == "version_candidate_1"
    assert record["candidate_runtime_fingerprint"] == "fingerprint_candidate_1"


def test_build_promotion_record_tracks_auto_applied_candidate():
    run_context = {
        "active_config_ref": "data/evolution/generations/momentum_cycle_0007.yaml",
        "active_version_id": "version_candidate_2",
        "active_runtime_fingerprint": "fingerprint_candidate_2",
        "candidate_config_ref": "data/evolution/generations/momentum_cycle_0007.yaml",
        "candidate_version_id": "version_candidate_2",
        "candidate_runtime_fingerprint": "fingerprint_candidate_2",
        "promotion_decision": {
            "status": "candidate_auto_applied",
            "source": "runtime_candidate_builder",
            "reason": "active model config mutated",
            "applied_to_active": True,
            "policy": {"min_samples": 4},
        },
    }
    optimization_events = [
        {
            "trigger": "consecutive_losses",
            "stage": "candidate_build",
            "notes": "active model config mutated",
        }
    ]

    record = build_promotion_record(
        cycle_id=7,
        run_context=run_context,
        optimization_events=optimization_events,
    )

    assert record["status"] == "candidate_auto_applied"
    assert record["gate_status"] == "applied_to_active"
    assert record["deployment_stage"] == "active"
    assert record["candidate_meta_ref"].endswith(".json")
    assert record["attempted"] is True
    assert record["candidate_version_id"] == "version_candidate_2"


def test_build_lineage_and_promotion_record_distinguish_override_stage():
    controller = SimpleNamespace(model_name="momentum")
    run_context = {
        "active_config_ref": "configs/active.yaml",
        "candidate_config_ref": "",
        "runtime_overrides": {"position_size": 0.10},
        "promotion_decision": {
            "status": "not_evaluated",
            "source": "review_meeting",
            "reason": "review override pending promotion",
            "applied_to_active": False,
            "policy": {"max_override_cycles": 2},
        },
        "deployment_stage": "override",
        "promotion_discipline": {
            "deployment_stage": "override",
            "status": "override_pending",
            "override_streak": 1,
            "runtime_override_keys": ["position_size"],
        },
    }
    optimization_events = [
        {
            "trigger": "review_meeting",
            "stage": "review_decision",
            "notes": "review override pending promotion",
            "applied_change": {"params": {"position_size": 0.10}},
        }
    ]

    lineage = build_lineage_record(
        controller,
        cycle_id=8,
        model_output=SimpleNamespace(model_name="momentum", config_name="configs/active.yaml"),
        run_context=run_context,
        optimization_events=optimization_events,
    )
    promotion = build_promotion_record(
        cycle_id=8,
        run_context=run_context,
        optimization_events=optimization_events,
    )

    assert lineage["deployment_stage"] == "override"
    assert lineage["lineage_status"] == "override_pending"
    assert promotion["deployment_stage"] == "override"
    assert promotion["gate_status"] == "override_pending"
