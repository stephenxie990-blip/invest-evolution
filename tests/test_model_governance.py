from invest.shared.model_governance import (
    build_optimization_event_lineage,
    evaluate_optimization_event_contract,
    evaluate_promotion_discipline,
    evaluate_routing_quality_gate,
    infer_deployment_stage,
)


def test_evaluate_optimization_event_contract_requires_cycle_id_and_lineage():
    payload = {
        "event_id": "opt_123",
        "contract_version": "optimization_event.v2",
        "cycle_id": 5,
        "trigger": "consecutive_losses",
        "stage": "yaml_mutation",
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
