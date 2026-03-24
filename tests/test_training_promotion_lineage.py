from types import SimpleNamespace
from typing import Any, cast

from invest_evolution.application.training.execution import build_lineage_record
from invest_evolution.application.training.execution import build_promotion_record


def test_build_lineage_record_tracks_candidate_pending_state():
    controller = SimpleNamespace(
        default_manager_id="momentum",
        default_manager_config_ref="configs/active.yaml",
    )
    run_context = {
        "active_runtime_config_ref": "configs/active.yaml",
        "manager_config_ref": "configs/active.yaml",
        "dominant_manager_id": "momentum",
        "candidate_runtime_config_ref": "data/evolution/generations/momentum_cycle_0006.yaml",
        "runtime_overrides": {"position_size": 0.12},
        "fitness_source_cycles": [2, 3, 4, 5],
        "promotion_decision": {
            "status": "candidate_generated",
            "source": "runtime_config_mutation",
            "reason": "candidate runtime config generated; active runtime config unchanged",
            "applied_to_active": False,
            "policy": {"min_samples": 4},
        },
    }
    optimization_events = [
        {
            "trigger": "consecutive_losses",
            "stage": "runtime_config_mutation",
            "notes": "candidate runtime config generated; active runtime config unchanged",
        }
    ]
    record = cast(
        dict[str, Any],
        build_lineage_record(
            controller,
            cycle_id=6,
            manager_output=None,
            run_context=run_context,
            optimization_events=optimization_events,
        ),
    )

    assert record["lineage_status"] == "candidate_pending"
    assert record["deployment_stage"] == "candidate"
    assert record["candidate_runtime_config_meta_ref"].endswith(".json")
    assert record["fitness_source_cycles"] == [2, 3, 4, 5]
    assert record["mutation_trigger"] == "consecutive_losses"


def test_build_promotion_record_tracks_auto_applied_candidate():
    run_context = {
        "active_runtime_config_ref": "data/evolution/generations/momentum_cycle_0007.yaml",
        "candidate_runtime_config_ref": "data/evolution/generations/momentum_cycle_0007.yaml",
        "promotion_decision": {
            "status": "candidate_auto_applied",
            "source": "runtime_config_mutation",
            "reason": "active runtime config mutated",
            "applied_to_active": True,
            "policy": {"min_samples": 4},
        },
    }
    optimization_events = [
        {
            "trigger": "consecutive_losses",
            "stage": "runtime_config_mutation",
            "notes": "active runtime config mutated",
        }
    ]

    record = cast(
        dict[str, Any],
        build_promotion_record(
            cycle_id=7,
            run_context=run_context,
            optimization_events=optimization_events,
        ),
    )

    assert record["status"] == "candidate_auto_applied"
    assert record["gate_status"] == "applied_to_active"
    assert record["deployment_stage"] == "active"
    assert record["candidate_runtime_config_meta_ref"].endswith(".json")
    assert record["attempted"] is True


def test_build_lineage_and_promotion_record_distinguish_override_stage():
    controller = SimpleNamespace(
        default_manager_id="momentum",
        default_manager_config_ref="configs/active.yaml",
    )
    run_context = {
        "active_runtime_config_ref": "configs/active.yaml",
        "manager_config_ref": "configs/active.yaml",
        "dominant_manager_id": "momentum",
        "candidate_runtime_config_ref": "",
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

    lineage = dict(
        build_lineage_record(
        controller,
        cycle_id=8,
        manager_output=None,
        run_context=run_context,
        optimization_events=optimization_events,
        )
    )
    promotion = dict(
        build_promotion_record(
        cycle_id=8,
        run_context=run_context,
        optimization_events=optimization_events,
        )
    )

    assert lineage["deployment_stage"] == "override"
    assert lineage["lineage_status"] == "override_pending"
    assert promotion["deployment_stage"] == "override"
    assert promotion["gate_status"] == "override_pending"


def test_build_lineage_and_promotion_record_propagate_shadow_mode():
    controller = SimpleNamespace(
        default_manager_id="momentum",
        default_manager_config_ref="configs/active.yaml",
    )
    run_context = {
        "active_runtime_config_ref": "configs/active.yaml",
        "manager_config_ref": "configs/active.yaml",
        "dominant_manager_id": "momentum",
        "candidate_runtime_config_ref": "data/evolution/generations/momentum_cycle_0006.yaml",
        "shadow_mode": True,
        "promotion_decision": {
            "status": "candidate_generated",
            "source": "runtime_config_mutation",
            "reason": "candidate runtime config generated; active runtime config unchanged",
            "applied_to_active": False,
        },
    }

    lineage = dict(
        build_lineage_record(
        controller,
        cycle_id=6,
        manager_output=None,
        run_context=run_context,
        optimization_events=[],
        )
    )
    promotion = dict(
        build_promotion_record(
        cycle_id=6,
        run_context=run_context,
        optimization_events=[],
        )
    )

    assert lineage["shadow_mode"] is True
    assert promotion["shadow_mode"] is True
