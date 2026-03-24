from invest_evolution.application.training.review_contracts import ValidationInputEnvelope
from invest_evolution.application.training.research import run_validation_orchestrator


def _governance_decision(regime: str, dominant_manager_id: str = "momentum") -> dict:
    return {
        "dominant_manager_id": dominant_manager_id,
        "active_manager_ids": [dominant_manager_id],
        "manager_budget_weights": {dominant_manager_id: 1.0},
        "regime": regime,
    }


def test_run_validation_orchestrator_propagates_shadow_mode_and_hold_status():
    report = run_validation_orchestrator(
        cycle_id=7,
        manager_id="momentum",
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate.yaml",
            "shadow_mode": True,
            "promotion_decision": {"applied_to_active": False},
        },
        review_result={
            "regime": "bull",
            "failure_signature": {
                "return_direction": "loss",
                "benchmark_passed": False,
            },
            "regime_summary": {
                "sample_count": 3,
                "dominant_regime_share": 0.4,
            },
            "research_feedback": {"sample_count": 2},
        },
        cycle_result={
            "governance_decision": _governance_decision("bull"),
            "ab_comparison": {
                "comparison": {
                    "candidate_present": True,
                    "comparable": True,
                    "winner": "candidate",
                    "candidate_outperformed": True,
                }
            },
            "research_feedback": {"sample_count": 2},
            "return_pct": -1.0,
            "benchmark_passed": False,
        },
        cycle_history=[],
        optimization_events=[],
        feedback_policy={"min_sample_count": 5},
        governance_policy={"max_pending_cycles": 3},
    )

    assert report["shadow_mode"] is True
    assert report["validation_task_id"].startswith("val_")
    assert report["summary"]["shadow_mode"] is True
    assert report["summary"]["status"] == "hold"
    assert "insufficient_sample" in report["summary"]["reason_codes"]


def test_run_validation_orchestrator_backfills_regime_samples_from_research_feedback_scope():
    research_feedback = {
        "sample_count": 8,
        "scope": {
            "effective_scope": "regime",
            "regime_sample_count": 8,
            "overall_sample_count": 16,
        },
        "recommendation": {"bias": "maintain"},
        "horizons": {
            "T+20": {
                "hit_rate": 0.6,
                "invalidation_rate": 0.2,
                "interval_hit_rate": 0.55,
            }
        },
    }

    report = run_validation_orchestrator(
        cycle_id=11,
        manager_id="momentum",
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate.yaml",
            "promotion_decision": {"applied_to_active": True},
        },
        review_result={
            "regime": "bull",
            "failure_signature": {},
            "research_feedback": research_feedback,
        },
        cycle_result={
            "governance_decision": _governance_decision("bull"),
            "research_feedback": research_feedback,
            "ab_comparison": {
                "comparison": {
                    "candidate_present": True,
                    "comparable": True,
                    "winner": "candidate",
                    "candidate_outperformed": True,
                }
            },
        },
        cycle_history=[],
        optimization_events=[],
        feedback_policy={"min_sample_count": 5},
        governance_policy={"max_pending_cycles": 3},
    )

    regime_sample_check = next(
        item for item in report["checks"] if item["name"] == "regime_summary.sample_count"
    )

    assert regime_sample_check["passed"] is True
    assert regime_sample_check["actual"] == 8
    assert report["summary"]["status"] == "passed"
    assert "insufficient_sample" not in report["summary"]["reason_codes"]


def test_run_validation_orchestrator_backfills_missing_explicit_regime_sample_count_from_feedback_scope():
    research_feedback = {
        "sample_count": 12,
        "scope": {
            "effective_scope": "regime",
            "regime_sample_count": 12,
            "overall_sample_count": 20,
        },
        "recommendation": {"bias": "maintain"},
        "horizons": {
            "T+20": {
                "hit_rate": 0.6,
                "invalidation_rate": 0.2,
                "interval_hit_rate": 0.55,
            }
        },
    }

    report = run_validation_orchestrator(
        cycle_id=11,
        manager_id="momentum",
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate.yaml",
            "promotion_decision": {"applied_to_active": True},
        },
        review_result={
            "regime": "bull",
            "failure_signature": {},
            "regime_summary": {
                "current_regime": "bull",
                "dominant_regime": "bull",
                "match_count": 1,
            },
            "research_feedback": research_feedback,
        },
        cycle_result={
            "governance_decision": _governance_decision("bull"),
            "research_feedback": research_feedback,
            "ab_comparison": {
                "comparison": {
                    "candidate_present": True,
                    "comparable": True,
                    "winner": "candidate",
                    "candidate_outperformed": True,
                }
            },
        },
        cycle_history=[],
        optimization_events=[],
        feedback_policy={"min_sample_count": 5},
        governance_policy={"max_pending_cycles": 3},
    )

    regime_sample_check = next(
        item for item in report["checks"] if item["name"] == "regime_summary.sample_count"
    )
    regime_share_check = next(
        item for item in report["checks"] if item["name"] == "regime_summary.dominant_regime_share"
    )

    assert regime_sample_check["passed"] is True
    assert regime_sample_check["actual"] == 12
    assert regime_share_check["passed"] is True
    assert regime_share_check["actual"] == 0.6
    assert report["summary"]["status"] == "passed"
    assert "insufficient_sample" not in report["summary"]["reason_codes"]


def test_run_validation_orchestrator_skips_candidate_ab_checks_without_candidate():
    research_feedback = {
        "sample_count": 6,
        "recommendation": {"bias": "maintain"},
        "horizons": {
            "T+20": {
                "hit_rate": 0.6,
                "invalidation_rate": 0.2,
                "interval_hit_rate": 0.55,
            }
        },
    }

    report = run_validation_orchestrator(
        cycle_id=12,
        manager_id="momentum",
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "promotion_decision": {"applied_to_active": True},
        },
        review_result={
            "regime": "bull",
            "failure_signature": {},
            "regime_summary": {
                "sample_count": 3,
                "dominant_regime_share": 0.4,
            },
            "research_feedback": research_feedback,
        },
        cycle_result={
            "governance_decision": _governance_decision("bull"),
            "research_feedback": research_feedback,
        },
        cycle_history=[],
        optimization_events=[],
        feedback_policy={"min_sample_count": 5},
        governance_policy={"max_pending_cycles": 3},
    )

    assert not any(item["name"].startswith("candidate_ab.") for item in report["checks"])
    assert "candidate_missing" not in report["summary"]["reason_codes"]
    assert "insufficient_evidence" not in report["summary"]["reason_codes"]


def test_run_validation_orchestrator_reads_nested_research_feedback_policy():
    research_feedback = {
        "sample_count": 6,
        "recommendation": {"bias": "maintain"},
        "horizons": {
            "T+20": {
                "hit_rate": 0.6,
                "invalidation_rate": 0.2,
                "interval_hit_rate": 0.55,
            }
        },
    }

    report = run_validation_orchestrator(
        cycle_id=13,
        manager_id="momentum",
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate.yaml",
            "promotion_decision": {"applied_to_active": True},
        },
        review_result={
            "regime": "bull",
            "failure_signature": {},
            "regime_summary": {
                "sample_count": 3,
                "dominant_regime_share": 0.4,
            },
            "research_feedback": research_feedback,
        },
        cycle_result={
            "governance_decision": _governance_decision("bull"),
            "research_feedback": research_feedback,
            "ab_comparison": {
                "comparison": {
                    "candidate_present": True,
                    "comparable": True,
                    "winner": "candidate",
                    "candidate_outperformed": True,
                }
            },
        },
        cycle_history=[],
        optimization_events=[],
        feedback_policy={
            "research_feedback": {
                "min_sample_count": 5,
                "blocked_biases": [],
                "horizons": {
                    "T+20": {
                        "min_hit_rate": 0.45,
                        "max_invalidation_rate": 0.3,
                        "min_interval_hit_rate": 0.4,
                    }
                },
            }
        },
        governance_policy={"max_pending_cycles": 3},
    )

    assert report["summary"]["status"] == "passed"
    assert "insufficient_sample" not in report["summary"]["reason_codes"]


def test_run_validation_orchestrator_falls_back_to_overall_feedback_when_regime_feedback_is_too_small():
    report = run_validation_orchestrator(
        cycle_id=14,
        manager_id="momentum",
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate.yaml",
            "promotion_decision": {"applied_to_active": False},
        },
        review_result={
            "regime": "bear",
            "failure_signature": {},
            "research_feedback": {
                "sample_count": 4,
                "scope": {
                    "effective_scope": "regime",
                    "regime_sample_count": 4,
                    "overall_sample_count": 12,
                },
                "recommendation": {"bias": "maintain"},
                "horizons": {
                    "T+20": {"hit_rate": 0.1, "invalidation_rate": 0.8, "interval_hit_rate": 0.1}
                },
                "overall_feedback": {
                    "sample_count": 12,
                    "recommendation": {"bias": "maintain"},
                    "brier_like_direction_score": 0.2,
                    "horizons": {
                        "T+20": {"hit_rate": 0.55, "invalidation_rate": 0.2, "interval_hit_rate": 0.45}
                    },
                },
            },
        },
        cycle_result={
            "governance_decision": _governance_decision("bear"),
            "ab_comparison": {
                "comparison": {
                    "candidate_present": True,
                    "comparable": True,
                    "winner": "candidate",
                    "candidate_outperformed": True,
                }
            },
        },
        cycle_history=[],
        optimization_events=[],
        feedback_policy={
            "min_sample_count": 5,
            "blocked_biases": [],
            "max_brier_like_direction_score": 0.25,
            "horizons": {
                "T+20": {
                    "min_hit_rate": 0.45,
                    "max_invalidation_rate": 0.3,
                    "min_interval_hit_rate": 0.4,
                }
            },
        },
        governance_policy={"max_pending_cycles": 3},
    )

    feedback_check = next(item for item in report["checks"] if item["name"] == "research_feedback.passed")

    assert feedback_check["passed"] is True
    assert feedback_check["details"]["evidence_source"] == "overall_feedback_fallback"
    assert "insufficient_sample" not in report["summary"]["reason_codes"]


def test_run_validation_orchestrator_holds_when_governance_blocks_without_ab_failure():
    research_feedback = {
        "sample_count": 8,
        "recommendation": {"bias": "maintain"},
        "brier_like_direction_score": 0.2,
        "horizons": {
            "T+20": {
                "hit_rate": 0.55,
                "invalidation_rate": 0.2,
                "interval_hit_rate": 0.45,
            }
        },
    }

    report = run_validation_orchestrator(
        cycle_id=15,
        manager_id="momentum",
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate_b.yaml",
            "promotion_decision": {"applied_to_active": False},
        },
        review_result={
            "regime": "bull",
            "failure_signature": {},
            "regime_summary": {"sample_count": 8, "dominant_regime_share": 0.4},
            "research_feedback": research_feedback,
        },
        cycle_result={
            "governance_decision": _governance_decision("bull"),
            "research_feedback": research_feedback,
            "ab_comparison": {
                "comparison": {
                    "candidate_present": True,
                    "comparable": True,
                    "winner": "candidate",
                    "candidate_outperformed": True,
                }
            },
        },
        cycle_history=[
            {
                "lineage_record": {
                    "deployment_stage": "candidate",
                    "candidate_runtime_config_ref": "configs/candidate_a.yaml",
                }
            }
        ],
        optimization_events=[],
        feedback_policy={
            "min_sample_count": 5,
            "blocked_biases": [],
            "max_brier_like_direction_score": 0.25,
            "horizons": {
                "T+20": {
                    "min_hit_rate": 0.45,
                    "max_invalidation_rate": 0.3,
                    "min_interval_hit_rate": 0.4,
                }
            },
        },
        governance_policy={"max_pending_candidates": 1},
    )

    assert report["summary"]["status"] == "hold"
    assert "governance_blocked" in report["summary"]["reason_codes"]


def test_run_validation_orchestrator_treats_shadow_feedback_prune_as_needs_more_optimization_only():
    research_feedback = {
        "sample_count": 6,
        "recommendation": {"bias": "tighten_risk"},
        "brier_like_direction_score": 0.2,
        "horizons": {
            "T+20": {
                "hit_rate": 0.2,
                "invalidation_rate": 0.6,
                "interval_hit_rate": 0.2,
            }
        },
    }

    report = run_validation_orchestrator(
        cycle_id=16,
        manager_id="momentum",
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate_b.yaml",
            "promotion_decision": {"applied_to_active": False},
            "shadow_mode": True,
        },
        review_result={
            "regime": "bull",
            "failure_signature": {},
            "regime_summary": {"sample_count": 8, "dominant_regime_share": 0.4},
            "research_feedback": research_feedback,
        },
        cycle_result={
            "governance_decision": _governance_decision("bull"),
            "research_feedback": research_feedback,
            "ab_comparison": {
                "comparison": {
                    "candidate_present": True,
                    "comparable": True,
                    "winner": "candidate",
                    "candidate_outperformed": True,
                }
            },
        },
        cycle_history=[],
        optimization_events=[],
        feedback_policy={
            "min_sample_count": 5,
            "blocked_biases": ["tighten_risk"],
            "max_brier_like_direction_score": 0.25,
            "horizons": {
                "T+20": {
                    "min_hit_rate": 0.45,
                    "max_invalidation_rate": 0.3,
                    "min_interval_hit_rate": 0.4,
                }
            },
        },
        governance_policy={
            "blocked_feedback_biases": ["tighten_risk"],
            "min_feedback_samples": 5,
        },
    )

    assert report["summary"]["status"] == "passed"
    assert report["summary"]["reason_codes"] == []
    assert "governance_blocked" not in report["summary"]["reason_codes"]


def test_run_validation_orchestrator_treats_shadow_failed_ab_prune_as_ab_only():
    research_feedback = {
        "sample_count": 6,
        "recommendation": {"bias": "maintain"},
        "brier_like_direction_score": 0.2,
        "horizons": {
            "T+20": {
                "hit_rate": 0.6,
                "invalidation_rate": 0.2,
                "interval_hit_rate": 0.45,
            }
        },
    }

    report = run_validation_orchestrator(
        cycle_id=16,
        manager_id="momentum",
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate_b.yaml",
            "promotion_decision": {"applied_to_active": False},
            "shadow_mode": True,
        },
        review_result={
            "regime": "bull",
            "failure_signature": {},
            "regime_summary": {"sample_count": 8, "dominant_regime_share": 0.4},
            "research_feedback": research_feedback,
        },
        cycle_result={
            "governance_decision": _governance_decision("bull"),
            "research_feedback": research_feedback,
            "ab_comparison": {
                "comparison": {
                    "candidate_present": True,
                    "comparable": True,
                    "winner": "active",
                    "candidate_outperformed": False,
                    "selection_overlap_ratio": 1.0,
                }
            },
        },
        cycle_history=[],
        optimization_events=[],
        feedback_policy={
            "min_sample_count": 5,
            "blocked_biases": [],
            "max_brier_like_direction_score": 0.25,
            "horizons": {
                "T+20": {
                    "min_hit_rate": 0.45,
                    "max_invalidation_rate": 0.3,
                    "min_interval_hit_rate": 0.4,
                }
            },
        },
        governance_policy={
            "prune_on_failed_candidate_ab": True,
            "max_selection_overlap_for_failed_candidate": 0.85,
        },
    )

    assert report["summary"]["status"] == "failed"
    assert "ab_failed" in report["summary"]["reason_codes"]
    assert "governance_blocked" not in report["summary"]["reason_codes"]
    governance_check = next(item for item in report["checks"] if item["name"] == "promotion_discipline.status")
    assert governance_check["passed"] is True


def test_run_validation_orchestrator_allows_shadow_feedback_failures_to_remain_advisory():
    research_feedback = {
        "sample_count": 6,
        "recommendation": {"bias": "tighten_risk"},
        "brier_like_direction_score": 0.2,
        "horizons": {
            "T+20": {
                "hit_rate": 0.2,
                "invalidation_rate": 0.6,
                "interval_hit_rate": 0.45,
            }
        },
    }

    report = run_validation_orchestrator(
        cycle_id=17,
        manager_id="momentum",
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate_b.yaml",
            "promotion_decision": {"applied_to_active": False},
            "shadow_mode": True,
        },
        review_result={
            "regime": "bull",
            "failure_signature": {},
            "regime_summary": {"sample_count": 8, "dominant_regime_share": 0.4},
            "research_feedback": research_feedback,
        },
        cycle_result={
            "governance_decision": _governance_decision("bull"),
            "research_feedback": research_feedback,
            "ab_comparison": {
                "comparison": {
                    "candidate_present": True,
                    "comparable": True,
                    "winner": "candidate",
                    "candidate_outperformed": True,
                }
            },
        },
        cycle_history=[],
        optimization_events=[],
        feedback_policy={
            "min_sample_count": 5,
            "blocked_biases": ["tighten_risk"],
            "max_brier_like_direction_score": 0.25,
            "horizons": {
                "T+20": {
                    "min_hit_rate": 0.45,
                    "max_invalidation_rate": 0.3,
                    "min_interval_hit_rate": 0.4,
                }
            },
        },
        governance_policy={
            "blocked_feedback_biases": ["tighten_risk"],
            "min_feedback_samples": 5,
        },
    )

    assert report["summary"]["status"] == "passed"
    assert report["summary"]["reason_codes"] == []


def test_run_validation_orchestrator_downgrades_incomplete_shadow_rolling_regime_concentration():
    research_feedback = {
        "sample_count": 15,
        "scope": {
            "effective_scope": "regime",
            "regime_sample_count": 15,
            "overall_sample_count": 15,
        },
        "recommendation": {"bias": "tighten_risk"},
        "brier_like_direction_score": 0.00818,
        "horizons": {
            "T+20": {
                "hit_rate": 0.0,
                "invalidation_rate": 1.0,
                "interval_hit_rate": 0.6667,
            }
        },
        "overall_feedback": {
            "sample_count": 15,
            "recommendation": {"bias": "tighten_risk"},
            "brier_like_direction_score": 0.00818,
            "horizons": {
                "T+20": {
                    "hit_rate": 0.0,
                    "invalidation_rate": 1.0,
                    "interval_hit_rate": 0.6667,
                }
            },
        },
    }

    report = run_validation_orchestrator(
        cycle_id=18,
        manager_id="momentum",
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate_b.yaml",
            "promotion_decision": {"applied_to_active": False},
            "shadow_mode": True,
            "review_basis_window": {
                "mode": "rolling",
                "size": 5,
                "cycle_ids": [1],
                "current_cycle_id": 1,
            },
        },
        review_result={
            "regime": "bull",
            "failure_signature": {},
            "regime_summary": {},
            "research_feedback": research_feedback,
        },
        cycle_result={
            "governance_decision": _governance_decision("bull"),
            "research_feedback": research_feedback,
            "ab_comparison": {
                "comparison": {
                    "candidate_present": True,
                    "comparable": True,
                    "winner": "candidate",
                    "candidate_outperformed": True,
                }
            },
        },
        cycle_history=[],
        optimization_events=[],
        feedback_policy={
            "min_sample_count": 5,
            "blocked_biases": ["tighten_risk"],
            "max_brier_like_direction_score": 0.25,
            "horizons": {
                "T+20": {
                    "min_hit_rate": 0.45,
                    "max_invalidation_rate": 0.3,
                    "min_interval_hit_rate": 0.4,
                }
            },
        },
        governance_policy={
            "blocked_feedback_biases": ["tighten_risk"],
            "min_feedback_samples": 5,
        },
    )

    regime_check = next(item for item in report["checks"] if item["name"] == "regime_summary.dominant_regime_share")

    assert regime_check["passed"] is True
    assert regime_check["details"]["shadow_rolling_window_advisory"] is True
    assert report["summary"]["status"] == "passed"
    assert report["summary"]["reason_codes"] == []


def test_run_validation_orchestrator_falls_back_to_overall_feedback_when_regime_scope_is_under_policy_min():
    research_feedback = {
        "sample_count": 4,
        "recommendation": {"bias": "tighten_risk"},
        "scope": {
            "effective_scope": "regime",
            "requested_regime": "bear",
            "regime_sample_count": 4,
            "overall_sample_count": 24,
        },
        "overall_feedback": {
            "sample_count": 24,
            "recommendation": {"bias": "maintain"},
            "brier_like_direction_score": 0.18,
            "horizons": {
                "T+20": {
                    "hit_rate": 0.55,
                    "invalidation_rate": 0.2,
                    "interval_hit_rate": 0.5,
                }
            },
        },
        "requested_regime_feedback": {
            "sample_count": 4,
            "recommendation": {"bias": "tighten_risk"},
            "horizons": {
                "T+20": {
                    "hit_rate": 0.2,
                    "invalidation_rate": 0.6,
                    "interval_hit_rate": 0.1,
                }
            },
        },
    }

    report = run_validation_orchestrator(
        cycle_id=14,
        manager_id="momentum",
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate.yaml",
            "promotion_decision": {"applied_to_active": False},
        },
        review_result={
            "regime": "bear",
            "failure_signature": {},
            "regime_summary": {
                "sample_count": 4,
                "dominant_regime_share": 0.4,
            },
            "research_feedback": research_feedback,
        },
        cycle_result={
            "governance_decision": _governance_decision("bear"),
            "research_feedback": research_feedback,
            "ab_comparison": {
                "comparison": {
                    "candidate_present": True,
                    "comparable": True,
                    "winner": "candidate",
                    "candidate_outperformed": True,
                }
            },
        },
        cycle_history=[],
        optimization_events=[],
        feedback_policy={
            "min_sample_count": 5,
            "blocked_biases": ["tighten_risk", "recalibrate_probability"],
            "max_brier_like_direction_score": 0.25,
            "horizons": {
                "T+20": {
                    "min_hit_rate": 0.45,
                    "max_invalidation_rate": 0.3,
                    "min_interval_hit_rate": 0.4,
                }
            },
        },
        governance_policy={"max_pending_cycles": 3},
    )

    assert report["summary"]["status"] == "passed"
    assert "insufficient_sample" not in report["summary"]["reason_codes"]
    assert "needs_more_optimization" not in report["summary"]["reason_codes"]


def test_run_validation_orchestrator_accepts_validation_input_envelope_as_primary_input():
    validation_input = ValidationInputEnvelope(
        cycle_id=22,
        manager_id="manager_alpha",
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate.yaml",
            "promotion_decision": {"applied_to_active": True},
            "shadow_mode": False,
        },
        review_result={
            "regime": "bull",
            "failure_signature": {},
            "regime_summary": {"sample_count": 8, "dominant_regime_share": 0.4},
            "research_feedback": {
                "sample_count": 8,
                "recommendation": {"bias": "maintain"},
                "horizons": {
                    "T+20": {
                        "hit_rate": 0.6,
                        "invalidation_rate": 0.2,
                        "interval_hit_rate": 0.5,
                    }
                },
            },
        },
        cycle_result={
            "governance_decision": _governance_decision("bull", dominant_manager_id="manager_alpha"),
            "ab_comparison": {
                "comparison": {
                    "candidate_present": True,
                    "comparable": True,
                    "winner": "candidate",
                    "candidate_outperformed": True,
                }
            },
            "research_feedback": {
                "sample_count": 8,
                "recommendation": {"bias": "maintain"},
                "horizons": {
                    "T+20": {
                        "hit_rate": 0.6,
                        "invalidation_rate": 0.2,
                        "interval_hit_rate": 0.5,
                    }
                },
            },
        },
    )

    report = run_validation_orchestrator(
        cycle_id=22,
        validation_input=validation_input,
        run_context=None,
        cycle_history=[],
        optimization_events=[],
        feedback_policy={"min_sample_count": 5},
        governance_policy={"max_pending_cycles": 3},
    )

    assert report["summary"]["status"] == "passed"
    assert report["summary"]["reason_codes"] == []
    assert report["validation_task_id"].startswith("val_")
