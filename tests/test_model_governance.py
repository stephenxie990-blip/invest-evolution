from invest.shared.model_governance import (
    build_optimization_event_lineage,
    evaluate_optimization_event_contract,
    evaluate_promotion_discipline,
    evaluate_routing_quality_gate,
    infer_deployment_stage,
    normalize_strategy_family_name,
    resolve_model_governance_matrix,
    resolve_strategy_family_regime_hard_fail_profile,
)


def test_evaluate_optimization_event_contract_requires_cycle_id_and_lineage():
    payload = {
        "event_id": "opt_123",
        "contract_version": "optimization_event.v2",
        "cycle_id": 5,
        "trigger": "consecutive_losses",
        "stage": "candidate_build",
        "status": "ok",
        "decision": {"config_path": "candidate.yaml"},
        "applied_change": {"params": {"position_size": 0.1}},
        "lineage": build_optimization_event_lineage(
            cycle_id=5,
            model_name="momentum",
            active_config_ref="configs/active.yaml",
            candidate_config_ref="candidate.yaml",
            promotion_status="candidate_generated",
            deployment_stage="candidate",
            review_basis_window={},
            fitness_source_cycles=[1, 2, 3],
            runtime_override_keys=["position_size"],
        ),
        "evidence": {"auto_applied": False},
        "ts": "2026-03-15T00:00:00",
    }

    contract = evaluate_optimization_event_contract(payload)

    assert contract["passed"] is True
    assert contract["failed_checks"] == []


def test_evaluate_routing_quality_gate_blocks_negative_score_candidate_stage():
    gate = evaluate_routing_quality_gate(
        {
            "score": -3.2,
            "avg_return_pct": -0.8,
            "avg_strategy_score": 0.42,
            "benchmark_pass_rate": 0.0,
            "avg_max_drawdown": 18.0,
            "deployment_stage": "candidate",
        }
    )

    assert gate["passed"] is False
    assert any(item["name"] == "block_negative_score" and item["passed"] is False for item in gate["failed_checks"])
    assert any(item["name"] == "allowed_deployment_stages" and item["passed"] is False for item in gate["failed_checks"])


def test_evaluate_promotion_discipline_marks_override_and_candidate_states():
    override = evaluate_promotion_discipline(
        run_context={
            "active_config_ref": "configs/active.yaml",
            "candidate_config_ref": "",
            "promotion_decision": {"applied_to_active": False},
        },
        cycle_history=[],
        optimization_events=[{"applied_change": {"params": {"position_size": 0.1}}}],
    )
    candidate = evaluate_promotion_discipline(
        run_context={
            "active_config_ref": "configs/active.yaml",
            "candidate_config_ref": "configs/candidate.yaml",
            "promotion_decision": {"applied_to_active": False},
        },
        cycle_history=[],
        optimization_events=[],
    )

    assert infer_deployment_stage(
        run_context={"candidate_config_ref": "configs/candidate.yaml", "promotion_decision": {"applied_to_active": False}}
    )["deployment_stage"] == "candidate"
    assert override["deployment_stage"] == "override"
    assert override["status"] == "override_pending"
    assert candidate["deployment_stage"] == "candidate"
    assert candidate["status"] == "candidate_pending"


def test_evaluate_promotion_discipline_prunes_candidate_on_failed_ab_and_blocked_feedback():
    discipline = evaluate_promotion_discipline(
        run_context={
            "active_config_ref": "configs/active.yaml",
            "candidate_config_ref": "configs/candidate.yaml",
            "promotion_decision": {"applied_to_active": False},
            "ab_comparison": {
                "comparison": {
                    "candidate_present": True,
                    "comparable": True,
                    "winner": "active",
                    "candidate_outperformed": False,
                    "selection_overlap_ratio": 0.92,
                }
            },
            "research_feedback": {
                "sample_count": 6,
                "recommendation": {"bias": "tighten_risk"},
            },
        },
        cycle_history=[],
        policy={
            "prune_on_failed_candidate_ab": True,
            "max_selection_overlap_for_failed_candidate": 0.85,
            "blocked_feedback_biases": ["tighten_risk"],
            "min_feedback_samples": 5,
        },
        optimization_events=[],
    )

    assert discipline["deployment_stage"] == "candidate"
    assert discipline["status"] == "candidate_pruned"
    assert "failed_candidate_ab" in discipline["violations"]
    assert "blocked_research_feedback" in discipline["violations"]


def test_evaluate_promotion_discipline_prunes_candidate_on_regime_hard_fail():
    discipline = evaluate_promotion_discipline(
        run_context={
            "active_config_ref": "configs/active.yaml",
            "candidate_config_ref": "configs/candidate.yaml",
            "promotion_decision": {"applied_to_active": False},
        },
        cycle_history=[
            {
                "cycle_id": 1,
                "return_pct": -1.4,
                "is_profit": False,
                "benchmark_passed": False,
                "routing_decision": {"regime": "bear"},
            },
            {
                "cycle_id": 2,
                "return_pct": -0.9,
                "is_profit": False,
                "benchmark_passed": False,
                "routing_decision": {"regime": "bear"},
            },
        ],
        optimization_events=[],
    )

    assert discipline["status"] == "candidate_pruned"
    assert "regime_hard_fail.bear" in discipline["violations"]
    assert "prune_regime_hard_fail_candidate" in discipline["discipline_actions"]
    assert discipline["regime_hard_fail"]["failed_regime_names"] == ["bear"]


def test_evaluate_routing_quality_gate_blocks_regime_hard_fail():
    gate = evaluate_routing_quality_gate(
        {
            "score": 1.8,
            "avg_return_pct": 0.4,
            "avg_strategy_score": 0.63,
            "benchmark_pass_rate": 0.55,
            "avg_max_drawdown": 6.0,
            "deployment_stage": "active",
            "regime_performance": {
                "bear": {
                    "cycles": 3,
                    "avg_return_pct": -1.2,
                    "benchmark_pass_rate": 0.0,
                    "win_rate": 0.0,
                    "loss_cycles": 3,
                }
            },
        }
    )

    assert gate["passed"] is False
    assert gate["regime_hard_fail"]["failed_regime_names"] == ["bear"]
    assert any(
        item["name"] == "regime_hard_fail.bear" and item["passed"] is False
        for item in gate["failed_checks"]
    )


def test_resolve_model_governance_matrix_applies_shared_regime_hard_fail_profile():
    matrix = resolve_model_governance_matrix(
        {
            "shared_regime_hard_fail": {
                "critical_regimes": ["oscillation"],
                "per_regime": {"oscillation": {"min_avg_return_pct": -0.15}},
            }
        }
    )

    assert matrix["routing"]["regime_hard_fail"]["critical_regimes"] == ["oscillation"]
    assert matrix["promotion"]["regime_hard_fail"]["critical_regimes"] == ["oscillation"]
    assert (
        matrix["routing"]["regime_hard_fail"]["per_regime"]["oscillation"]["min_avg_return_pct"]
        == -0.15
    )


def test_resolve_model_governance_matrix_applies_strategy_family_regime_hard_fail_profile():
    matrix = resolve_model_governance_matrix(strategy_family="mean_reversion_v1")

    assert normalize_strategy_family_name("mean_reversion_v1") == "mean_reversion"
    assert (
        resolve_strategy_family_regime_hard_fail_profile("mean_reversion")["per_regime"]["oscillation"]["min_avg_return_pct"]
        == -0.20
    )
    assert matrix["routing"]["regime_hard_fail"]["critical_regimes"] == ["oscillation", "bear"]
    assert (
        matrix["promotion"]["regime_hard_fail"]["per_regime"]["bear"]["max_benchmark_pass_rate"]
        == 0.20
    )


def test_value_quality_oscillation_hard_fail_profile_uses_calibrated_loss_structure_thresholds():
    profile = resolve_strategy_family_regime_hard_fail_profile("value_quality")

    assert profile["per_regime"]["oscillation"]["min_cycles"] == 3
    assert profile["per_regime"]["oscillation"]["max_loss_share"] == 0.65
    assert profile["per_regime"]["oscillation"]["min_negative_contribution_pct"] == -4.0

    gate = evaluate_routing_quality_gate(
        {
            "model_name": "value_quality_v1",
            "score": 1.2,
            "avg_return_pct": 0.2,
            "avg_strategy_score": 0.62,
            "benchmark_pass_rate": 0.45,
            "avg_max_drawdown": 6.0,
            "deployment_stage": "active",
            "regime_performance": {
                "oscillation": {
                    "cycles": 7,
                    "avg_return_pct": -0.9,
                    "benchmark_pass_rate": 0.0,
                    "win_rate": 0.28,
                    "loss_cycles": 5,
                    "negative_contribution_pct": -6.4,
                }
            },
        }
    )

    assert gate["passed"] is False
    assert gate["regime_hard_fail"]["failed_regime_names"] == ["oscillation"]


def test_value_quality_oscillation_hard_fail_uses_structural_failures_plus_any_confirm_metric():
    gate = evaluate_routing_quality_gate(
        {
            "model_name": "value_quality_v1",
            "score": 0.8,
            "avg_return_pct": 0.1,
            "avg_strategy_score": 0.55,
            "benchmark_pass_rate": 0.4,
            "avg_max_drawdown": 7.0,
            "deployment_stage": "active",
            "regime_performance": {
                "oscillation": {
                    "cycles": 10,
                    "avg_return_pct": -0.8246,
                    "benchmark_pass_rate": 0.20,
                    "win_rate": 0.30,
                    "loss_cycles": 7,
                    "negative_contribution_pct": -17.98,
                }
            },
        }
    )

    failed_check = next(
        item for item in gate["regime_hard_fail"]["checks"] if item["name"] == "regime_hard_fail.oscillation"
    )

    assert gate["passed"] is False
    assert gate["regime_hard_fail"]["failed_regime_names"] == ["oscillation"]
    assert failed_check["failed_metric_status"]["avg_return_pct"] is True
    assert failed_check["failed_metric_status"]["loss_share"] is True
    assert failed_check["failed_metric_status"]["negative_contribution_pct"] is True
    assert failed_check["failed_metric_status"]["benchmark_pass_rate"] is False
    assert failed_check["failed_metric_status"]["win_rate"] is True


def test_evaluate_routing_quality_gate_honors_per_regime_threshold_override():
    gate = evaluate_routing_quality_gate(
        {
            "score": 2.0,
            "avg_return_pct": 0.4,
            "avg_strategy_score": 0.68,
            "benchmark_pass_rate": 0.60,
            "avg_max_drawdown": 5.0,
            "deployment_stage": "active",
            "regime_performance": {
                "bull": {
                    "cycles": 2,
                    "avg_return_pct": -0.15,
                    "benchmark_pass_rate": 0.20,
                    "win_rate": 0.0,
                }
            },
        },
        policy={
            "regime_hard_fail": {
                "critical_regimes": ["bull"],
                "per_regime": {
                    "bull": {
                        "min_avg_return_pct": -0.10,
                        "max_benchmark_pass_rate": 0.25,
                        "max_win_rate": 0.40,
                    }
                },
            }
        },
    )

    assert gate["passed"] is False
    assert gate["regime_hard_fail"]["failed_regime_names"] == ["bull"]


def test_evaluate_routing_quality_gate_uses_strategy_family_defaults():
    gate = evaluate_routing_quality_gate(
        {
            "model_name": "momentum_v1",
            "score": 1.4,
            "avg_return_pct": 0.6,
            "avg_strategy_score": 0.70,
            "benchmark_pass_rate": 0.50,
            "avg_max_drawdown": 6.0,
            "deployment_stage": "active",
            "regime_performance": {
                "bear": {
                    "cycles": 3,
                    "avg_return_pct": -0.45,
                    "benchmark_pass_rate": 0.0,
                    "win_rate": 0.0,
                    "loss_cycles": 3,
                }
            },
        }
    )

    assert gate["passed"] is False
    assert gate["regime_hard_fail"]["failed_regime_names"] == ["bear"]


def test_evaluate_promotion_discipline_uses_strategy_family_defaults():
    discipline = evaluate_promotion_discipline(
        run_context={
            "model_name": "defensive_low_vol_v1",
            "strategy_family": "defensive_low_vol",
            "active_config_ref": "configs/active.yaml",
            "candidate_config_ref": "configs/candidate.yaml",
            "promotion_decision": {"applied_to_active": False},
        },
        cycle_history=[
            {
                "cycle_id": 1,
                "return_pct": -0.30,
                "is_profit": False,
                "benchmark_passed": False,
                "routing_decision": {"regime": "oscillation"},
            },
            {
                "cycle_id": 2,
                "return_pct": -0.26,
                "is_profit": False,
                "benchmark_passed": False,
                "routing_decision": {"regime": "oscillation"},
            },
        ],
        optimization_events=[],
    )

    assert discipline["status"] == "candidate_pruned"
    assert discipline["regime_hard_fail"]["failed_regime_names"] == ["oscillation"]
