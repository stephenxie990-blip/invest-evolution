from typing import Any, cast
from pathlib import Path
from types import SimpleNamespace

from invest_evolution.application.training.policy import (
    ExperimentSpec,
    normalize_cutoff_policy,
    normalize_review_window,
)
from invest_evolution.application.training.review_contracts import (
    CYCLE_STAGE_SNAPSHOT_CONTRACT_VERSION,
    OptimizationInputEnvelope,
    ReviewStageEnvelope,
    SimulationStageEnvelope,
    ValidationInputEnvelope,
    build_cycle_run_context,
    build_cycle_contract_stage_snapshots,
    build_execution_snapshot,
    build_outcome_stage_snapshot,
    build_review_stage_snapshot,
    build_simulation_stage_snapshot,
    build_validation_stage_snapshot,
)
from invest_evolution.application.training.controller import TrainingSessionState


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
            "manager_scope": {
                "allowed_manager_ids": ["value_quality", "momentum", ""],
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
    assert payload["manager_scope"]["allowed_manager_ids"] == ["value_quality", "momentum"]
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
        default_manager_id="momentum",
        default_manager_config_ref="configs/active.yaml",
        current_params={"position_size": 0.12},
        cycle_history=[
            SimpleNamespace(cycle_id=3),
            SimpleNamespace(cycle_id=4),
            SimpleNamespace(cycle_id=5),
        ],
        experiment_review_window={"mode": "rolling", "size": 4},
        experiment_promotion_policy={"min_samples": 3},
    )
    manager_output = SimpleNamespace(
        manager_id="momentum",
        manager_config_ref="configs/active.yaml",
    )
    optimization_events = [
        {
            "stage": "runtime_config_mutation",
            "runtime_config_mutation_payload": {
                "runtime_config_ref": "data/evolution/generations/candidate.yaml",
                "auto_applied": False,
            },
            "decision": {
                "runtime_config_ref": "data/evolution/generations/stale.yaml",
                "auto_applied": True,
            },
            "notes": "candidate runtime config generated; active runtime config unchanged",
        }
    ]

    context = cast(
        dict[str, Any],
        build_cycle_run_context(
            controller,
            cycle_id=6,
            manager_output=manager_output,
            optimization_events=optimization_events,
        ),
    )

    assert context["active_runtime_config_ref"] == str(Path("configs/active.yaml").resolve())
    assert context["candidate_runtime_config_ref"] == str(
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
        context["resolved_train_policy"]["quality_gate_matrix"]["governance"]["allowed_deployment_stages"]
        == ["active"]
    )


def test_build_cycle_run_context_carries_shadow_mode_and_protocol_payload():
    controller = SimpleNamespace(
        default_manager_id="momentum",
        default_manager_config_ref="configs/active.yaml",
        current_params={"position_size": 0.12},
        cycle_history=[],
        experiment_review_window={"mode": "single_cycle", "size": 1},
        experiment_promotion_policy={},
        experiment_protocol={
            "protocol": {
                "shadow_mode": True,
                "review_window": {"mode": "single_cycle", "size": 1},
            },
            "llm": {"mode": "dry_run"},
        },
    )

    context = cast(
        dict[str, Any],
        build_cycle_run_context(
            controller,
            cycle_id=6,
            manager_output=SimpleNamespace(
                manager_id="momentum",
                manager_config_ref="configs/active.yaml",
            ),
            optimization_events=[],
        ),
    )

    assert context["shadow_mode"] is True
    assert context["experiment_protocol"]["protocol"]["shadow_mode"] is True


def test_build_cycle_run_context_uses_candidate_as_active_after_auto_apply():
    controller = SimpleNamespace(
        default_manager_id="momentum",
        default_manager_config_ref="data/evolution/generations/candidate.yaml",
        current_params={"position_size": 0.12},
        cycle_history=[],
        experiment_review_window={"mode": "single_cycle", "size": 1},
        experiment_promotion_policy={"min_samples": 3},
    )
    manager_output = SimpleNamespace(
        manager_id="momentum",
        manager_config_ref="configs/active.yaml",
    )
    optimization_events = [
        {
            "stage": "runtime_config_mutation",
            "runtime_config_mutation_payload": {
                "runtime_config_ref": "data/evolution/generations/candidate.yaml",
                "auto_applied": True,
            },
            "decision": {
                "runtime_config_ref": "data/evolution/generations/stale.yaml",
                "auto_applied": False,
            },
            "notes": "active runtime config mutated",
        }
    ]

    context = cast(
        dict[str, Any],
        build_cycle_run_context(
            controller,
            cycle_id=6,
            manager_output=manager_output,
            optimization_events=optimization_events,
        ),
    )

    expected_ref = str(Path("data/evolution/generations/candidate.yaml").resolve())
    assert context["active_runtime_config_ref"] == expected_ref
    assert context["candidate_runtime_config_ref"] == expected_ref
    assert context["promotion_decision"]["status"] == "candidate_auto_applied"
    assert context["deployment_stage"] == "active"


def test_build_cycle_run_context_skips_fitness_sources_without_runtime_config_mutation():
    controller = SimpleNamespace(
        default_manager_id="momentum",
        default_manager_config_ref="configs/active.yaml",
        current_params={"position_size": 0.12},
        cycle_history=[
            SimpleNamespace(cycle_id=3),
            SimpleNamespace(cycle_id=4),
        ],
        experiment_review_window={"mode": "single_cycle", "size": 9},
        experiment_promotion_policy={"min_samples": 3},
    )

    context = cast(
        dict[str, Any],
        build_cycle_run_context(
            controller,
            cycle_id=5,
            manager_output=SimpleNamespace(
                manager_id="momentum",
                manager_config_ref="configs/active.yaml",
            ),
            optimization_events=[{"stage": "review"}],
        ),
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


def test_simulation_stage_envelope_projects_cycle_contract_state():
    cycle_dict = {
        "cycle_id": 9,
        "cutoff_date": "20250305",
        "regime": "bull",
        "selected_stocks": ["600519.SH", "000001.SZ"],
        "return_pct": 3.2,
        "benchmark_passed": True,
        "benchmark_strict_passed": False,
        "sharpe_ratio": 1.4,
        "max_drawdown": 0.08,
        "excess_return": 0.03,
        "strategy_scores": {"overall_score": 0.88},
        "governance_decision": {"regime": "bull"},
        "execution_snapshot": {
            "basis_stage": "pre_optimization",
            "active_runtime_config_ref": "configs/active.yaml",
        },
    }

    envelope = SimulationStageEnvelope.from_cycle_payload(cycle_dict)
    simulation_snapshot = cast(dict[str, Any], envelope.stage_snapshots.get("simulation") or {})

    assert envelope.cycle_id == 9
    assert envelope.regime == "bull"
    assert envelope.strategy_scores.get("overall_score") == 0.88
    assert simulation_snapshot["contract_version"] == (
        CYCLE_STAGE_SNAPSHOT_CONTRACT_VERSION
    )
    assert dict(simulation_snapshot.get("execution_snapshot") or {})["basis_stage"] == (
        "pre_optimization"
    )


def test_simulation_stage_envelope_from_structured_inputs_without_cycle_dict():
    envelope = SimulationStageEnvelope.from_structured_inputs(
        cycle_id=21,
        cutoff_date="20250320",
        regime="oscillation",
        selection_mode="manager_portfolio",
        selected_stocks=["600036.SH"],
        return_pct=1.25,
        benchmark_passed=True,
        benchmark_strict_passed=True,
        sharpe_ratio=1.18,
        max_drawdown=0.06,
        excess_return=0.02,
        strategy_scores={"overall_score": 0.73},
        governance_decision={"regime": "oscillation"},
        execution_snapshot={
            "basis_stage": "pre_optimization",
            "active_runtime_config_ref": "configs/runtime.yaml",
        },
    )
    simulation_snapshot = cast(dict[str, Any], envelope.stage_snapshots.get("simulation") or {})

    assert envelope.cycle_id == 21
    assert envelope.cutoff_date == "20250320"
    assert envelope.selected_stocks == ["600036.SH"]
    assert simulation_snapshot["stage"] == "simulation"
    assert simulation_snapshot["selection_mode"] == "manager_portfolio"
    assert (
        dict(simulation_snapshot.get("execution_snapshot") or {})["active_runtime_config_ref"]
        == "configs/runtime.yaml"
    )


def test_simulation_stage_envelope_from_cycle_payload_prefers_canonical_payload_shape():
    envelope = SimulationStageEnvelope.from_cycle_payload(
        {
            "cycle_id": 23,
            "cutoff_date": "20250323",
            "regime": "bull",
            "selection_mode": "manager_portfolio",
            "selected_stocks": ["600519.SH"],
            "return_pct": 2.4,
            "benchmark_passed": True,
            "execution_snapshot": {
                "basis_stage": "simulation_envelope",
                "active_runtime_config_ref": "configs/runtime.yaml",
            },
        }
    )

    assert envelope.cycle_id == 23
    assert envelope.execution_snapshot["basis_stage"] == "simulation_envelope"


def test_simulation_stage_envelope_from_cycle_dict_remains_explicit_boundary_only_compat_adapter():
    payload = {
        "cycle_id": 24,
        "cutoff_date": "20250324",
        "regime": "bear",
        "execution_snapshot": {"basis_stage": "legacy_adapter"},
    }

    from_payload = SimulationStageEnvelope.from_cycle_payload(payload)
    from_legacy = SimulationStageEnvelope.from_cycle_dict(payload)

    assert from_legacy == from_payload


def test_simulation_stage_envelope_to_cycle_payload_preserves_runtime_fields():
    envelope = SimulationStageEnvelope.from_structured_inputs(
        cycle_id=22,
        cutoff_date="20250322",
        regime="bull",
        selection_mode="manager_portfolio",
        selected_stocks=["600036.SH"],
        return_pct=1.8,
        benchmark_passed=True,
        benchmark_strict_passed=True,
        sharpe_ratio=1.22,
        max_drawdown=0.05,
        excess_return=0.03,
        strategy_scores={"overall_score": 0.79},
        governance_decision={"regime": "bull"},
        execution_snapshot={
            "basis_stage": "pre_optimization",
            "active_runtime_config_ref": "configs/runtime.yaml",
        },
    )

    payload = envelope.to_cycle_payload(base_payload={"selection_mode": "manager_portfolio"})

    assert payload["cycle_id"] == 22
    assert payload["selection_mode"] == "manager_portfolio"
    assert payload["stage_snapshots"]["simulation"]["stage"] == "simulation"
    assert payload["stage_snapshots"]["simulation"]["execution_snapshot"]["basis_stage"] == (
        "pre_optimization"
    )


def test_review_stage_envelope_from_structured_inputs_without_cycle_dict():
    simulation = SimulationStageEnvelope.from_structured_inputs(
        cycle_id=31,
        cutoff_date="20250321",
        regime="bear",
        return_pct=-0.8,
        benchmark_passed=False,
        strategy_scores={"overall_score": 0.24},
        execution_snapshot={"basis_stage": "pre_optimization"},
    )
    review = ReviewStageEnvelope.from_structured_inputs(
        simulation=simulation,
        analysis="控制回撤优先",
        review_decision={"reasoning": "降低风险敞口"},
        causal_diagnosis={"primary_driver": "volatility_spike"},
        similarity_summary={"match_count": 2},
        similar_results=[{"cycle_id": 7}],
        manager_review_report={"summary": {"verdict_counts": {"hold": 1}}},
        allocation_review_report={"verdict": "hold"},
        ab_comparison={"comparison": {"winner": "inconclusive"}},
        review_applied=True,
    )

    assert review.simulation.cycle_id == 31
    assert review.analysis == "控制回撤优先"
    assert review.review_applied is True
    review_stage_snapshots = cast(dict[str, Any], review.stage_snapshots)
    review_stage_payload = cast(dict[str, Any], review_stage_snapshots.get("review") or {})
    assert review_stage_payload["cycle_id"] == 31
    assert review_stage_payload["analysis"] == "控制回撤优先"
    assert dict(review_stage_payload.get("ab_comparison") or {}).get("comparison", {}).get("winner") == "inconclusive"


def test_review_stage_envelope_to_cycle_payload_rebuilds_review_projection():
    simulation = SimulationStageEnvelope.from_structured_inputs(
        cycle_id=32,
        cutoff_date="20250322",
        regime="bear",
        selection_mode="manager_portfolio",
        return_pct=-1.5,
        benchmark_passed=False,
        benchmark_strict_passed=False,
        strategy_scores={"overall_score": 0.2},
        execution_snapshot={"basis_stage": "pre_optimization"},
    )
    review = ReviewStageEnvelope.from_structured_inputs(
        simulation=simulation,
        analysis="降低暴露",
        review_decision={"reasoning": "降低暴露", "causal_diagnosis": {"primary_driver": "drawdown"}},
        causal_diagnosis={"primary_driver": "drawdown"},
        similarity_summary={"match_count": 1},
        similar_results=[{"cycle_id": 3}],
        manager_review_report={"summary": {"verdict_counts": {"hold": 1}}},
        allocation_review_report={"verdict": "hold"},
        ab_comparison={"comparison": {"winner": "candidate"}},
        review_applied=True,
    )

    payload = review.to_cycle_payload(base_payload={"selection_mode": "manager_portfolio"})

    assert payload["analysis"] == "降低暴露"
    assert payload["review_applied"] is True
    assert payload["ab_comparison"]["comparison"]["winner"] == "candidate"
    assert payload["stage_snapshots"]["review"]["analysis"] == "降低暴露"


def test_review_stage_envelope_normalizes_similar_results_to_compact_contract():
    simulation = SimulationStageEnvelope.from_structured_inputs(
        cycle_id=35,
        cutoff_date="20250325",
        regime="bear",
    )
    review = ReviewStageEnvelope.from_structured_inputs(
        simulation=simulation,
        similar_results=[
            {
                "cycle_id": "7",
                "score": "9",
                "matched_features": ["drawdown", "", "turnover"],
                "failure_signature": None,
            }
        ],
    )

    payload = review.to_cycle_payload()
    similar_result = payload["similar_results"][0]
    review_snapshot = payload["stage_snapshots"]["review"]["similar_results"][0]

    assert similar_result["cycle_id"] == 7
    assert similar_result["similarity_score"] == 9
    assert similar_result["matched_features"] == ["drawdown", "turnover"]
    assert similar_result["failure_signature"] == {}
    assert review_snapshot == similar_result


def test_review_stage_envelope_from_cycle_payload_prefers_canonical_payload_shape():
    review = ReviewStageEnvelope.from_cycle_payload(
        {
            "cycle_id": 33,
            "cutoff_date": "20250323",
            "regime": "bear",
            "analysis": "压缩敞口",
            "review_decision": {"reasoning": "压缩敞口"},
            "execution_snapshot": {"basis_stage": "simulation_envelope"},
        }
    )

    assert review.simulation.cycle_id == 33
    assert review.analysis == "压缩敞口"


def test_review_stage_envelope_from_cycle_dict_remains_explicit_boundary_only_compat_adapter():
    payload = {
        "cycle_id": 34,
        "cutoff_date": "20250324",
        "analysis": "兼容适配",
        "review_decision": {"reasoning": "兼容适配"},
    }

    from_payload = ReviewStageEnvelope.from_cycle_payload(payload)
    from_legacy = ReviewStageEnvelope.from_cycle_dict(payload)

    assert from_legacy == from_payload


def test_review_stage_envelope_builds_validation_payload_from_structured_state():
    simulation = SimulationStageEnvelope.from_cycle_payload(
        {
            "cycle_id": 11,
            "cutoff_date": "20250306",
            "regime": "bear",
            "return_pct": -1.5,
            "benchmark_passed": False,
            "strategy_scores": {"overall_score": 0.21},
        }
    )
    review = ReviewStageEnvelope.from_cycle_payload(
        {
            "cycle_id": 11,
            "analysis": "回撤来自追高",
            "review_decision": {"reasoning": "降低仓位"},
            "causal_diagnosis": {"primary_driver": "chasing_breakout"},
            "manager_review_report": {"summary": {"verdict_counts": {"hold": 1}}},
            "allocation_review_report": {"verdict": "hold"},
        },
        simulation_envelope=simulation,
    )

    payload = review.to_validation_review_payload(
        regime="bear",
        research_feedback={"recommendation": {"bias": "defensive"}},
        regime_summary={"confidence": 0.71},
    )

    assert payload["cycle_id"] == 11
    assert payload["failure_signature"]["return_direction"] == "loss"
    assert payload["failure_signature"]["benchmark_passed"] is False
    assert payload["failure_signature"]["primary_driver"] == "chasing_breakout"
    assert payload["failure_signature"]["feedback_bias"] == "defensive"
    assert payload["manager_review_report"]["summary"]["verdict_counts"]["hold"] == 1
    assert payload["allocation_review_report"]["verdict"] == "hold"


def test_validation_input_envelope_projects_training_result_contract():
    simulation = SimulationStageEnvelope.from_cycle_payload(
        {
            "cycle_id": 12,
            "cutoff_date": "20250307",
            "regime": "oscillation",
            "return_pct": 2.1,
            "benchmark_passed": True,
            "strategy_scores": {"overall_score": 0.67},
        }
    )
    review = ReviewStageEnvelope.from_cycle_payload(
        {
            "cycle_id": 12,
            "causal_diagnosis": {"primary_driver": "rebalance_discipline"},
            "manager_review_report": {"summary": {"verdict_counts": {"continue": 1}}},
            "allocation_review_report": {"verdict": "continue"},
        },
        simulation_envelope=simulation,
    )
    cycle_result = SimpleNamespace(
        cycle_id=12,
        dominant_manager_id="momentum",
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate.yaml",
            "dominant_manager_id": "momentum",
        },
        governance_decision={"dominant_manager_id": "momentum", "regime": "oscillation"},
        ab_comparison={"comparison": {"winner": "candidate"}},
        research_feedback={"sample_count": 6},
        return_pct=2.1,
        benchmark_passed=True,
        strategy_scores={"overall_score": 0.67},
        portfolio_plan={"positions": [{"code": "600519.SH"}]},
        portfolio_attribution={"600519.SH": 0.2},
        manager_results=[{"manager_id": "momentum"}],
        manager_review_report={"summary": {"verdict_counts": {"continue": 1}}},
        allocation_review_report={"verdict": "continue"},
    )

    envelope = ValidationInputEnvelope.from_cycle_result(
        cycle_result,
        review_envelope=review,
        regime="oscillation",
        research_feedback={"recommendation": {"bias": "maintain"}},
        regime_summary={"sample_count": 4},
    )

    assert envelope.manager_id == "momentum"
    run_context: dict[str, Any] = dict(envelope.run_context)
    assert run_context["candidate_runtime_config_ref"] == "configs/candidate.yaml"
    assert envelope.review_result["failure_signature"]["primary_driver"] == (
        "rebalance_discipline"
    )
    assert envelope.cycle_result["strategy_scores"]["overall_score"] == 0.67
    assert envelope.cycle_result["manager_results"][0]["manager_id"] == "momentum"


def test_optimization_input_envelope_projects_structured_state():
    simulation = SimulationStageEnvelope.from_structured_inputs(
        cycle_id=14,
        cutoff_date="20250308",
        regime="bear",
        selection_mode="manager_portfolio",
        selected_stocks=["600519.SH"],
        return_pct=-2.4,
        benchmark_passed=False,
        benchmark_strict_passed=False,
        sharpe_ratio=-0.7,
        max_drawdown=0.11,
        excess_return=-0.05,
        strategy_scores={"overall_score": 0.19},
        governance_decision={"regime": "bear"},
        execution_snapshot={"active_runtime_config_ref": "configs/active.yaml"},
    )

    envelope = OptimizationInputEnvelope(
        simulation=simulation,
        research_feedback={"sample_count": 6},
        research_feedback_optimization={"triggered": True, "bias": "defensive"},
    )

    payload = envelope.to_cycle_payload(base_payload={"selection_mode": "manager_portfolio"})

    assert payload["cycle_id"] == 14
    assert payload["selection_mode"] == "manager_portfolio"
    assert payload["research_feedback"]["sample_count"] == 6
    assert payload["research_feedback_optimization"]["bias"] == "defensive"
    assert payload["stage_snapshots"]["simulation"]["stage"] == "simulation"


def test_build_cycle_run_context_prefers_execution_snapshot_state():
    controller = SimpleNamespace(
        default_manager_id="momentum",
        default_manager_config_ref="configs/post_review.yaml",
        current_params={"position_size": 0.33},
        cycle_history=[],
        experiment_review_window={"mode": "rolling", "size": 2},
        experiment_promotion_policy={},
    )
    execution_snapshot = {
        "basis_stage": "pre_optimization",
        "active_runtime_config_ref": "configs/executed.yaml",
        "runtime_overrides": {"position_size": 0.08, "max_positions": 4},
    }

    context = cast(
        dict[str, Any],
        build_cycle_run_context(
            controller,
            cycle_id=6,
            manager_output=SimpleNamespace(
                manager_id="momentum",
                manager_config_ref="configs/post_review.yaml",
            ),
            optimization_events=[],
            execution_snapshot=execution_snapshot,
        ),
    )

    assert context["basis_stage"] == "pre_optimization"
    assert context["active_runtime_config_ref"] == str(Path("configs/executed.yaml").resolve())
    assert context["runtime_overrides"] == {"position_size": 0.08, "max_positions": 4}
    assert context["quality_gate_matrix"]["governance"]["allowed_deployment_stages"] == ["active"]


def test_build_cycle_run_context_prefers_session_state_runtime_overrides():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            current_params={"position_size": 0.27, "cash_reserve": 0.14},
            default_manager_id="momentum",
            default_manager_config_ref="configs/active.yaml",
            cycle_history=[SimpleNamespace(cycle_id=3)],
        ),
        current_params={"position_size": 0.99},
        cycle_history=[],
        experiment_review_window={"mode": "rolling", "size": 2},
        experiment_promotion_policy={},
    )

    context = cast(
        dict[str, Any],
        build_cycle_run_context(
            controller,
            cycle_id=4,
            manager_output=SimpleNamespace(
                manager_id="momentum",
                manager_config_ref="configs/active.yaml",
            ),
            optimization_events=[],
        ),
    )

    assert context["runtime_overrides"] == {"position_size": 0.27, "cash_reserve": 0.14}
    assert context["review_basis_window"]["cycle_ids"] == [3, 4]


def test_build_cycle_run_context_prefers_manager_output_scope_over_controller_default():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="defensive",
            default_manager_config_ref="configs/defensive.yaml",
            current_params={"position_size": 0.27},
        ),
        current_params={"position_size": 0.99},
        cycle_history=[],
        experiment_review_window={"mode": "single_cycle", "size": 1},
        experiment_promotion_policy={},
    )

    context = cast(
        dict[str, Any],
        build_cycle_run_context(
            controller,
            cycle_id=9,
            manager_output=SimpleNamespace(
                manager_id="value_quality",
                manager_config_ref="configs/value_quality.yaml",
            ),
            optimization_events=[],
        ),
    )

    assert context["dominant_manager_id"] == "value_quality"
    assert context["active_runtime_config_ref"] == str(Path("configs/value_quality.yaml").resolve())
    assert context["manager_config_ref"] == str(Path("configs/value_quality.yaml").resolve())


def test_build_execution_snapshot_prefers_manager_output_scope_over_controller_default():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="defensive",
            default_manager_config_ref="configs/defensive.yaml",
            current_params={"position_size": 0.19},
            last_governance_decision={
                "dominant_manager_id": "value_quality",
                "active_manager_ids": ["value_quality"],
                "manager_budget_weights": {"value_quality": 1.0},
                "regime": "bear",
            },
        ),
        current_params={"position_size": 0.99},
    )

    snapshot = build_execution_snapshot(
        controller,
        cycle_id=14,
        manager_output=SimpleNamespace(
            manager_id="value_quality",
            manager_config_ref="configs/value_quality.yaml",
        ),
        selection_mode="manager_portfolio",
        manager_results=[{"manager_id": "value_quality"}],
        portfolio_plan={"active_manager_ids": ["value_quality"]},
        dominant_manager_id="value_quality",
    )

    assert snapshot["manager_id"] == "value_quality"
    assert snapshot["dominant_manager_id"] == "value_quality"
    assert snapshot["active_runtime_config_ref"] == str(Path("configs/value_quality.yaml").resolve())
    assert snapshot["manager_config_ref"] == str(Path("configs/value_quality.yaml").resolve())
    assert snapshot["execution_defaults"]["default_manager_config_ref"] == str(
        Path("configs/value_quality.yaml").resolve()
    )


def test_build_execution_snapshot_prefers_compatibility_manager_config_ref_seed():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="defensive",
            default_manager_config_ref="configs/defensive.yaml",
            last_governance_decision={
                "dominant_manager_id": "value_quality",
                "active_manager_ids": ["value_quality"],
                "manager_budget_weights": {"value_quality": 1.0},
                "regime": "bear",
            },
        ),
        current_params={},
    )

    snapshot = build_execution_snapshot(
        controller,
        cycle_id=15,
        manager_output=SimpleNamespace(
            manager_id="value_quality",
            manager_config_ref="configs/stale_output.yaml",
        ),
        selection_mode="manager_portfolio",
        manager_results=[{"manager_id": "value_quality"}],
        portfolio_plan={"active_manager_ids": ["value_quality"]},
        dominant_manager_id="value_quality",
        compatibility_fields={"manager_config_ref": "configs/canonical.yaml"},
    )

    assert snapshot["active_runtime_config_ref"] == str(Path("configs/canonical.yaml").resolve())
    assert snapshot["manager_config_ref"] == str(Path("configs/canonical.yaml").resolve())


def test_cycle_stage_snapshot_builders_produce_canonical_shapes():
    simulation = cast(
        dict[str, Any],
        build_simulation_stage_snapshot(
            {
                "cycle_id": 8,
                "cutoff_date": "20240201",
                "regime": "bull",
                "selection_mode": "manager_portfolio",
                "selected_stocks": ["sh.600519"],
                "return_pct": 2.5,
                "benchmark_passed": True,
                "benchmark_strict_passed": True,
                "strategy_scores": {"overall_score": 0.82},
                "governance_decision": {"dominant_manager_id": "value_quality"},
            },
            execution_snapshot={
                "basis_stage": "pre_optimization",
                "active_runtime_config_ref": "configs/executed.yaml",
                "manager_config_ref": "configs/executed.yaml",
                "dominant_manager_id": "value_quality",
                "execution_defaults": {"default_manager_id": "value_quality"},
                "subject_type": "manager_portfolio",
            },
        ),
    )
    review = cast(
        dict[str, Any],
        build_review_stage_snapshot(
            {
                "cycle_id": 8,
                "analysis": "review ok",
                "review_decision": {"verdict": "continue"},
                "manager_review_report": {"coverage": 0.7},
                "allocation_review_report": {"exposure": "balanced"},
            }
        ),
    )
    outcome = cast(
        dict[str, Any],
        build_outcome_stage_snapshot(
            cycle_id=8,
            execution_snapshot={"basis_stage": "pre_optimization"},
            run_context={"basis_stage": "pre_optimization"},
            promotion_record={"status": "candidate_generated"},
            lineage_record={"status": "tracked"},
            realism_metrics={"trade_record_count": 3},
        ),
    )
    validation = cast(
        dict[str, Any],
        build_validation_stage_snapshot(
            cycle_id=8,
            validation_report={
                "validation_task_id": "task-8",
                "shadow_mode": True,
                "summary": {"status": "passed"},
                "market_tagging": {"primary_tag": "bull"},
            },
            judge_report={"decision": "promote"},
        ),
    )

    assert simulation["contract_version"] == CYCLE_STAGE_SNAPSHOT_CONTRACT_VERSION
    assert simulation["execution_snapshot"]["dominant_manager_id"] == "value_quality"
    assert review["review_decision"]["verdict"] == "continue"
    assert outcome["promotion_record"]["status"] == "candidate_generated"
    assert validation["validation_summary"]["status"] == "passed"
    assert validation["judge_report"]["decision"] == "promote"


def test_build_cycle_contract_stage_snapshots_aggregates_stage_baseline():
    snapshots = cast(
        dict[str, Any],
        build_cycle_contract_stage_snapshots(
            cycle_payload={
                "cycle_id": 9,
                "cutoff_date": "20240201",
                "regime": "bull",
                "selection_mode": "manager_portfolio",
                "selected_stocks": ["sh.600000"],
                "benchmark_passed": True,
                "strategy_scores": {"overall_score": 0.91},
                "analysis": "cycle looks healthy",
                "review_decision": {"reasoning": "cycle looks healthy"},
                "execution_snapshot": {
                    "basis_stage": "pre_optimization",
                    "cycle_id": 9,
                    "active_runtime_config_ref": "configs/active.yaml",
                    "manager_config_ref": "configs/active.yaml",
                    "subject_type": "manager_portfolio",
                },
            },
            validation_report={
                "shadow_mode": True,
                "summary": {"status": "passed"},
                "market_tagging": {"primary_tag": "bull"},
            },
            run_context={
                "basis_stage": "pre_optimization",
                "active_runtime_config_ref": "configs/active.yaml",
                "candidate_runtime_config_ref": "configs/candidate.yaml",
                "subject_type": "manager_portfolio",
                "dominant_manager_id": "momentum",
                "active_manager_ids": ["momentum"],
            },
        ),
    )

    assert snapshots["simulation"]["stage"] == "simulation"
    assert snapshots["simulation"]["selected_stocks"] == ["sh.600000"]
    assert snapshots["review"]["stage"] == "review"
    assert snapshots["review"]["review_decision"]["reasoning"] == "cycle looks healthy"
    assert snapshots["validation"]["stage"] == "validation"
    assert snapshots["validation"]["validation_summary"]["status"] == "passed"
    assert snapshots["outcome"]["stage"] == "outcome"
    assert snapshots["outcome"]["run_context"]["candidate_runtime_config_ref"] == "configs/candidate.yaml"
