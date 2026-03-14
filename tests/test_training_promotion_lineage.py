from types import SimpleNamespace

from app.training.lineage_services import build_lineage_record
from app.training.promotion_services import build_promotion_record


def test_build_lineage_record_tracks_candidate_pending_state():
    controller = SimpleNamespace(model_name="momentum")
    run_context = {
        "active_config_ref": "configs/active.yaml",
        "candidate_config_ref": "data/evolution/generations/momentum_cycle_0006.yaml",
        "runtime_overrides": {"position_size": 0.12},
        "fitness_source_cycles": [2, 3, 4, 5],
        "promotion_decision": {
            "status": "candidate_generated",
            "source": "runtime_yaml_mutation",
            "reason": "candidate model config generated; active config unchanged",
            "applied_to_active": False,
            "policy": {"min_samples": 4},
        },
    }
    optimization_events = [
        {
            "trigger": "consecutive_losses",
            "stage": "yaml_mutation",
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
    assert record["candidate_meta_ref"].endswith(".json")
    assert record["fitness_source_cycles"] == [2, 3, 4, 5]
    assert record["mutation_trigger"] == "consecutive_losses"


def test_build_promotion_record_tracks_auto_applied_candidate():
    run_context = {
        "active_config_ref": "data/evolution/generations/momentum_cycle_0007.yaml",
        "candidate_config_ref": "data/evolution/generations/momentum_cycle_0007.yaml",
        "promotion_decision": {
            "status": "candidate_auto_applied",
            "source": "runtime_yaml_mutation",
            "reason": "active model config mutated",
            "applied_to_active": True,
            "policy": {"min_samples": 4},
        },
    }
    optimization_events = [
        {
            "trigger": "consecutive_losses",
            "stage": "yaml_mutation",
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
    assert record["candidate_meta_ref"].endswith(".json")
    assert record["attempted"] is True
