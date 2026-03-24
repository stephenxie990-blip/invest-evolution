from pathlib import Path
from types import SimpleNamespace

from invest_evolution.application.training.execution import (
    ManagerCompatibilityProjection,
    SelectionStageContext,
    build_manager_compatibility_fields,
    project_cycle_payload_manager_compatibility,
    project_manager_compatibility,
    resolve_payload_manager_identity,
    runtime_manager_config_ref,
)
from invest_evolution.application.training.observability import (
    _resolve_cycle_payload_boundary,
    build_outcome_execution_boundary_projection,
    build_review_eval_projection_boundary,
    build_selection_boundary_projection,
)
from invest_evolution.application.training.controller import TrainingSessionState
from invest_evolution.investment.runtimes import create_manager_runtime


def test_project_manager_compatibility_prefers_canonical_scope_snapshot():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="momentum",
            default_manager_config_ref="configs/active.yaml",
            last_governance_decision={
                "dominant_manager_id": "value_quality",
                "active_manager_ids": ["value_quality"],
                "manager_budget_weights": {"value_quality": 1.0},
                "regime": "bear",
            },
        ),
    )

    projection = project_manager_compatibility(
        controller,
        execution_snapshot={
            "active_runtime_config_ref": "configs/executed.yaml",
            "manager_config_ref": "configs/executed.yaml",
            "execution_defaults": {"default_manager_id": "value_quality"},
            "dominant_manager_id": "value_quality",
            "subject_type": "manager_portfolio",
        },
        dominant_manager_id_hint="value_quality",
    )

    assert projection.manager_id == "value_quality"
    assert projection.manager_config_ref.endswith("configs/executed.yaml")
    assert projection.active_runtime_config_ref.endswith("configs/executed.yaml")
    assert projection.execution_defaults == {
        "default_manager_id": "value_quality",
        "default_manager_config_ref": projection.manager_config_ref,
    }
    assert projection.subject_type == "manager_portfolio"


def test_build_manager_compatibility_fields_marks_derived_projection():
    projection = ManagerCompatibilityProjection(
        manager_id="momentum",
        manager_config_ref="configs/active.yaml",
        active_runtime_config_ref="configs/active.yaml",
        execution_defaults={},
        dominant_manager_id="momentum",
        subject_type="single_manager",
    )

    payload = build_manager_compatibility_fields(
        projection,
        source="dominant_manager",
        derived=True,
    )

    assert payload == {
        "derived": True,
        "source": "dominant_manager",
        "field_role": "derived_compatibility",
        "manager_id": "momentum",
        "manager_config_ref": "configs/active.yaml",
    }


def test_resolve_payload_manager_identity_prefers_payload_then_controller_defaults():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="defensive",
            default_manager_config_ref="configs/defensive.yaml",
        ),
    )

    payload = {
        "metadata": {
            "dominant_manager_id": "value_quality",
            "active_runtime_config_ref": "configs/value.yaml",
        }
    }

    manager_id, manager_config_ref = resolve_payload_manager_identity(
        payload,
        controller=controller,
    )

    assert manager_id == "value_quality"
    assert manager_config_ref.endswith("configs/value.yaml")


def test_runtime_manager_config_ref_prefers_runtime_config_path():
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            path=Path("/tmp/runtime-configs/momentum_v1.yaml"),
            name="momentum_v1",
        )
    )

    assert runtime_manager_config_ref(runtime) == "/tmp/runtime-configs/momentum_v1.yaml"


def test_runtime_process_emits_path_based_manager_config_ref_consistently():
    runtime = create_manager_runtime("momentum")
    output = runtime.process({}, "20240131")

    assert output.manager_config_ref.endswith("configs/momentum_v1.yaml")
    assert output.signal_packet.manager_config_ref == output.manager_config_ref
    assert output.agent_context.manager_config_ref == output.manager_config_ref


def test_project_cycle_payload_manager_compatibility_prefers_cycle_snapshot_over_controller_defaults():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="defensive",
            default_manager_config_ref="configs/defensive.yaml",
        ),
    )

    projection = project_cycle_payload_manager_compatibility(
        controller,
        cycle_payload={
            "dominant_manager_id": "momentum",
            "execution_snapshot": {
                "dominant_manager_id": "momentum",
                "active_runtime_config_ref": "configs/momentum.generated.yaml",
                "manager_config_ref": "configs/momentum.generated.yaml",
                "execution_defaults": {
                    "default_manager_id": "momentum",
                    "default_manager_config_ref": "configs/momentum.generated.yaml",
                },
                "subject_type": "manager_portfolio",
            },
        },
    )

    assert projection.manager_id == "momentum"
    assert projection.active_runtime_config_ref.endswith("configs/momentum.generated.yaml")
    assert projection.manager_config_ref.endswith("configs/momentum.generated.yaml")
    assert projection.execution_defaults == {
        "default_manager_id": "momentum",
        "default_manager_config_ref": projection.manager_config_ref,
    }


def test_project_cycle_payload_manager_compatibility_repairs_stale_config_for_new_dominant_manager():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="momentum",
            default_manager_config_ref="configs/defensive_low_vol_v1.yaml",
            last_governance_decision={
                "dominant_manager_id": "momentum",
                "active_manager_ids": ["momentum", "defensive_low_vol"],
                "manager_budget_weights": {"momentum": 0.6, "defensive_low_vol": 0.4},
                "regime": "bull",
            },
        ),
    )

    projection = project_cycle_payload_manager_compatibility(
        controller,
        cycle_payload={
            "governance_decision": {
                "dominant_manager_id": "momentum",
                "active_manager_ids": ["momentum", "defensive_low_vol"],
                "manager_budget_weights": {"momentum": 0.6, "defensive_low_vol": 0.4},
                "regime": "bull",
            },
            "execution_snapshot": {
                "dominant_manager_id": "momentum",
                "active_runtime_config_ref": "defensive_low_vol_v1",
                "manager_config_ref": "defensive_low_vol_v1",
                "execution_defaults": {
                    "default_manager_id": "momentum",
                    "default_manager_config_ref": "defensive_low_vol_v1",
                },
            },
        },
    )

    assert projection.manager_id == "momentum"
    assert projection.manager_config_ref.endswith("momentum_v1.yaml")
    assert projection.active_runtime_config_ref.endswith("momentum_v1.yaml")
    assert projection.execution_defaults == {
        "default_manager_id": "momentum",
        "default_manager_config_ref": projection.manager_config_ref,
    }


def test_build_selection_boundary_projection_uses_bundle_only_for_compat_views():
    trading_plan = SimpleNamespace(positions=[SimpleNamespace(code="sh.600519")], max_positions=1)
    manager_output = SimpleNamespace(manager_id="momentum", manager_config_ref="configs/momentum.yaml")
    bundle = SimpleNamespace(
        portfolio_plan=SimpleNamespace(to_trading_plan=lambda: trading_plan),
        manager_outputs={"momentum": manager_output},
    )
    selection_result = SimpleNamespace(
        manager_bundle=bundle,
        portfolio_plan={"active_manager_ids": ["momentum"]},
        dominant_manager_id="momentum",
    )

    projection = build_selection_boundary_projection(selection_result)

    assert projection.manager_output is manager_output
    assert projection.trading_plan is trading_plan
    assert projection.strategy_advice == {
        "source": "manager_runtime",
        "portfolio_plan": {"active_manager_ids": ["momentum"]},
        "dominant_manager_id": "momentum",
    }
    assert projection.compatibility_fields == {}


def test_build_outcome_execution_boundary_projection_rehydrates_snapshot_outside_core_service():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="momentum",
            default_manager_config_ref="configs/active.yaml",
            current_params={"position_size": 0.12},
            last_governance_decision={
                "dominant_manager_id": "momentum",
                "active_manager_ids": ["momentum", "value_quality"],
                "manager_budget_weights": {"momentum": 0.6, "value_quality": 0.4},
                "regime": "bull",
            },
        ),
    )
    projection = build_outcome_execution_boundary_projection(
        controller,
        cycle_id=4,
        cycle_payload={"execution_snapshot": {}},
        manager_output=SimpleNamespace(manager_id="momentum", manager_config_ref="configs/active.yaml"),
        selection_mode="manager_portfolio",
        benchmark_passed=True,
        manager_results_payload=[{"manager_id": "momentum"}],
        portfolio_payload={"active_manager_ids": ["momentum", "value_quality"]},
        dominant_manager_id="momentum",
    )

    assert projection.governance_decision["dominant_manager_id"] == "momentum"
    assert projection.execution_defaults["default_manager_id"] == "momentum"
    assert str(projection.execution_defaults["default_manager_config_ref"]).endswith("configs/active.yaml")
    assert projection.execution_snapshot["subject_type"] == "manager_portfolio"
    assert projection.execution_snapshot["manager_results"] == [{"manager_id": "momentum"}]
    assert projection.compatibility_fields["derived"] is True


def test_build_outcome_execution_boundary_projection_prefers_explicit_execution_snapshot_seed():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="momentum",
            default_manager_config_ref="configs/active.yaml",
            current_params={"position_size": 0.12},
            last_governance_decision={
                "dominant_manager_id": "momentum",
                "active_manager_ids": ["momentum"],
                "manager_budget_weights": {"momentum": 1.0},
                "regime": "bull",
            },
        ),
    )

    projection = build_outcome_execution_boundary_projection(
        controller,
        cycle_id=5,
        cycle_payload={
            "execution_snapshot": {
                "basis_stage": "legacy_cycle_dict",
                "active_runtime_config_ref": "configs/legacy.yaml",
            }
        },
        execution_snapshot={
            "basis_stage": "simulation_envelope",
            "active_runtime_config_ref": "configs/envelope.yaml",
            "manager_config_ref": "configs/envelope.yaml",
            "governance_decision": {"dominant_manager_id": "momentum", "regime": "bull"},
        },
        governance_decision={"dominant_manager_id": "momentum", "regime": "bull"},
        manager_output=SimpleNamespace(manager_id="momentum", manager_config_ref="configs/active.yaml"),
        selection_mode="single_manager",
        benchmark_passed=True,
        manager_results_payload=[],
        portfolio_payload={},
        dominant_manager_id="momentum",
    )

    assert projection.execution_snapshot["basis_stage"] == "simulation_envelope"
    assert projection.execution_snapshot["active_runtime_config_ref"].endswith(
        "configs/envelope.yaml"
    )


def test_build_review_eval_projection_boundary_projects_manager_identity_and_metadata():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="defensive",
            default_manager_config_ref="configs/defensive.yaml",
        ),
    )

    projection = build_review_eval_projection_boundary(
        controller,
        manager_output=SimpleNamespace(
            manager_id="value_quality",
            manager_config_ref="configs/value_quality.yaml",
        ),
        cycle_payload={},
        simulation_envelope=None,
        manager_results=[{"manager_id": "value_quality"}],
        portfolio_plan={"active_manager_ids": ["value_quality"]},
        dominant_manager_id="value_quality",
    )

    assert projection.manager_id == "value_quality"
    assert projection.manager_config_ref.endswith("configs/value_quality.yaml")
    assert projection.subject_type == "manager_portfolio"
    assert projection.compatibility_fields == {
        "derived": True,
        "source": "dominant_manager",
        "field_role": "derived_compatibility",
        "manager_id": "value_quality",
        "manager_config_ref": projection.manager_config_ref,
    }


def test_resolve_cycle_payload_boundary_keeps_cycle_dict_only_as_compat_adapter():
    payload = _resolve_cycle_payload_boundary(
        cycle_dict={
            "execution_snapshot": {
                "active_runtime_config_ref": "configs/legacy.yaml",
                "manager_config_ref": "configs/legacy.yaml",
            }
        }
    )

    assert payload == {
        "execution_snapshot": {
            "active_runtime_config_ref": "configs/legacy.yaml",
            "manager_config_ref": "configs/legacy.yaml",
        }
    }


def test_resolve_payload_manager_identity_repairs_metadata_config_mismatch():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="defensive_low_vol",
            default_manager_config_ref="configs/defensive_low_vol.yaml",
            last_governance_decision={
                "dominant_manager_id": "momentum",
                "active_manager_ids": ["momentum", "defensive_low_vol"],
                "manager_budget_weights": {"momentum": 0.65, "defensive_low_vol": 0.35},
                "regime": "bull",
            },
        ),
    )

    manager_id, manager_config_ref = resolve_payload_manager_identity(
        {
            "governance_decision": {
                "dominant_manager_id": "momentum",
                "active_manager_ids": ["momentum", "defensive_low_vol"],
                "manager_budget_weights": {"momentum": 0.65, "defensive_low_vol": 0.35},
                "regime": "bull",
            },
            "metadata": {
                "dominant_manager_id": "momentum",
                "manager_config_ref": "defensive_low_vol_v1",
                "active_runtime_config_ref": "defensive_low_vol_v1",
            },
        },
        controller=controller,
    )

    assert manager_id == "momentum"
    assert manager_config_ref.endswith("momentum_v1.yaml")


def test_project_manager_compatibility_canonicalizes_alias_ref_to_runtime_config_path():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="momentum",
            default_manager_config_ref="configs/momentum_v1.yaml",
            last_governance_decision={
                "dominant_manager_id": "momentum",
                "active_manager_ids": ["momentum", "value_quality"],
                "manager_budget_weights": {"momentum": 0.7, "value_quality": 0.3},
                "regime": "bull",
            },
        )
    )

    projection = project_manager_compatibility(
        controller,
        manager_output=SimpleNamespace(
            manager_id="momentum",
            manager_config_ref="momentum_v1",
        ),
        execution_snapshot={
            "dominant_manager_id": "momentum",
            "active_runtime_config_ref": "momentum_v1",
            "manager_config_ref": "momentum_v1",
            "execution_defaults": {
                "default_manager_id": "momentum",
                "default_manager_config_ref": "momentum_v1",
            },
        },
        dominant_manager_id_hint="momentum",
    )

    assert projection.manager_id == "momentum"
    assert projection.manager_config_ref.endswith("configs/momentum_v1.yaml")
    assert projection.active_runtime_config_ref.endswith("configs/momentum_v1.yaml")
    assert projection.execution_defaults["default_manager_config_ref"].endswith(
        "configs/momentum_v1.yaml"
    )


def test_project_manager_compatibility_prefers_portfolio_subject_when_snapshot_was_trimmed():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="momentum",
            default_manager_config_ref="configs/momentum_v1.yaml",
            last_governance_decision={
                "dominant_manager_id": "momentum",
                "active_manager_ids": ["momentum", "value_quality"],
                "manager_budget_weights": {"momentum": 0.6, "value_quality": 0.4},
                "regime": "bull",
            },
        ),
    )

    projection = project_manager_compatibility(
        controller,
        execution_snapshot={
            "subject_type": "single_manager",
            "selection_mode": "manager_portfolio",
            "manager_results": [
                {"manager_id": "momentum"},
                {"manager_id": "value_quality"},
            ],
            "active_runtime_config_ref": "momentum_v1",
            "manager_config_ref": "momentum_v1",
            "dominant_manager_id": "momentum",
        },
        dominant_manager_id_hint="momentum",
    )

    assert projection.subject_type == "manager_portfolio"
    assert projection.manager_id == "momentum"
    assert projection.manager_config_ref.endswith("configs/momentum_v1.yaml")


def test_selection_stage_context_preserves_minimal_identity_when_manager_persistence_disabled():
    context = SelectionStageContext(
        selection_result=None,
        manager_output=None,
        regime_result={"regime": "bull"},
        trading_plan=SimpleNamespace(),
        selected=["sh.600519"],
        selected_data={},
        selection_mode="manager_portfolio",
        agent_used=False,
        manager_bundle=None,
        manager_results_payload=[
            {"manager_id": "momentum", "manager_config_ref": "configs/momentum_v1.yaml"},
            {"manager_id": "value_quality", "manager_config_ref": "configs/value_quality_v1.yaml"},
        ],
        portfolio_plan_payload={"active_manager_ids": ["momentum", "value_quality"]},
        dominant_manager_id="momentum",
        portfolio_attribution_payload={"sh.600519": {"momentum": 0.7}},
        compatibility_fields={},
    )

    payload = context.outcome_persistence_inputs(persistence_enabled=False)

    assert payload["dominant_manager_id"] == "momentum"
    assert payload["portfolio_plan"]["active_manager_ids"] == ["momentum", "value_quality"]
    assert payload["portfolio_plan"]["manager_count"] == 2
    assert payload["manager_results"] == [
        {"manager_id": "momentum", "manager_config_ref": "configs/momentum_v1.yaml"},
        {"manager_id": "value_quality", "manager_config_ref": "configs/value_quality_v1.yaml"},
    ]


def test_selection_stage_context_does_not_synthesize_portfolio_shape_for_single_manager_subject():
    context = SelectionStageContext(
        selection_result=None,
        manager_output=None,
        regime_result={"regime": "bear"},
        trading_plan=SimpleNamespace(),
        selected=["sh.600519"],
        selected_data={},
        selection_mode="single_manager",
        agent_used=False,
        manager_bundle=None,
        manager_results_payload=[
            {"manager_id": "defensive_low_vol", "manager_config_ref": "configs/defensive_low_vol_v1.yaml"},
        ],
        portfolio_plan_payload={"active_manager_ids": ["defensive_low_vol"]},
        dominant_manager_id="defensive_low_vol",
        portfolio_attribution_payload={},
        compatibility_fields={},
    )

    payload = context.outcome_persistence_inputs(persistence_enabled=False)

    assert payload["dominant_manager_id"] == "defensive_low_vol"
    assert payload["manager_results"] == [
        {
            "manager_id": "defensive_low_vol",
            "manager_config_ref": "configs/defensive_low_vol_v1.yaml",
        }
    ]
    assert payload["portfolio_plan"] == {}


def test_build_outcome_execution_boundary_projection_rewrites_subject_fields_without_full_portfolio_payload():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="momentum",
            default_manager_config_ref="configs/active.yaml",
            current_params={"position_size": 0.12},
            last_governance_decision={
                "dominant_manager_id": "momentum",
                "active_manager_ids": ["momentum", "value_quality"],
                "manager_budget_weights": {"momentum": 0.6, "value_quality": 0.4},
                "regime": "bull",
            },
        ),
    )

    projection = build_outcome_execution_boundary_projection(
        controller,
        cycle_id=6,
        execution_snapshot={
            "subject_type": "single_manager",
            "selection_mode": "manager_portfolio",
            "dominant_manager_id": "momentum",
        },
        governance_decision={
            "dominant_manager_id": "momentum",
            "active_manager_ids": ["momentum", "value_quality"],
            "manager_budget_weights": {"momentum": 0.6, "value_quality": 0.4},
            "regime": "bull",
        },
        manager_output=SimpleNamespace(
            manager_id="momentum",
            manager_config_ref="configs/active.yaml",
        ),
        selection_mode="manager_portfolio",
        benchmark_passed=True,
        manager_results_payload=[
            {"manager_id": "momentum"},
            {"manager_id": "value_quality"},
        ],
        portfolio_payload={},
        dominant_manager_id="momentum",
    )

    assert projection.execution_snapshot["subject_type"] == "manager_portfolio"
    assert projection.execution_snapshot["manager_id"] == "momentum"
    assert projection.execution_snapshot["dominant_manager_id"] == "momentum"
