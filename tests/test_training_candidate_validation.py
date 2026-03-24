from invest_evolution.application.training.research import (
    build_validation_summary,
    build_validation_task_id,
    validate_candidate_ab,
    validate_candidate_feedback,
    validate_candidate_governance,
    validate_candidate_precheck,
    validate_candidate_regimes,
)
from invest_evolution.application.tagging import ValidationCheck


def test_build_validation_task_id_is_deterministic():
    first = build_validation_task_id(
        cycle_id=7,
        candidate_runtime_config_ref="configs/candidate.yaml",
        active_runtime_config_ref="configs/active.yaml",
        manager_id="momentum",
    )
    second = build_validation_task_id(
        cycle_id=7,
        candidate_runtime_config_ref="configs/candidate.yaml",
        active_runtime_config_ref="configs/active.yaml",
        manager_id="momentum",
    )

    assert first == second
    assert first.startswith("val_")


def test_validate_candidate_precheck_marks_missing_candidate():
    checks = validate_candidate_precheck({"active_runtime_config_ref": "configs/active.yaml"})

    assert checks[0].passed is False
    assert checks[0].reason_code == "candidate_missing"
    assert checks[1].passed is True


def test_build_validation_summary_holds_when_candidate_is_missing():
    checks = validate_candidate_precheck({"active_runtime_config_ref": "configs/active.yaml"})

    summary = build_validation_summary(
        validation_task_id="val_candidate_missing",
        checks=checks,
        confidence_score=0.95,
    )

    assert summary.status == "hold"
    assert "candidate_missing" in summary.reason_codes
    assert "candidate_missing" in summary.validation_tags


def test_build_validation_summary_holds_when_governance_blocks():
    checks = validate_candidate_governance(
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate.yaml",
            "promotion_decision": {"applied_to_active": False},
            "research_feedback": {
                "sample_count": 6,
                "recommendation": {"bias": "tighten_risk"},
            },
        },
        cycle_history=[],
        policy={"blocked_feedback_biases": ["tighten_risk"], "min_feedback_samples": 5},
        optimization_events=[],
    )

    summary = build_validation_summary(
        validation_task_id="val_governance_blocked",
        checks=checks,
        confidence_score=0.95,
    )

    assert summary.status == "hold"
    assert "governance_blocked" in summary.reason_codes


def test_validate_candidate_regimes_detects_insufficient_samples():
    checks = validate_candidate_regimes(
        market_tag="bull",
        regime_summary={"sample_count": 1, "dominant_regime_share": 0.9},
        min_samples=2,
    )

    assert checks[0].passed is False
    assert checks[0].reason_code == "insufficient_sample"


def test_validate_candidate_regimes_downgrades_incomplete_shadow_rolling_window_to_advisory():
    checks = validate_candidate_regimes(
        market_tag="bull",
        regime_summary={"sample_count": 15, "dominant_regime_share": 1.0},
        shadow_mode=True,
        review_basis_window={"mode": "rolling", "size": 5, "cycle_ids": [1], "current_cycle_id": 1},
    )

    assert checks[1].passed is True
    assert checks[1].reason_code == "regime_diversified"
    assert checks[1].details["shadow_rolling_window_advisory"] is True


def test_validate_candidate_ab_marks_ab_failed():
    checks = validate_candidate_ab(
        {
            "comparison": {
                "candidate_present": True,
                "comparable": True,
                "winner": "active",
                "candidate_outperformed": False,
            }
        }
    )

    assert checks[-1].passed is False
    assert checks[-1].reason_code == "ab_failed"


def test_validate_candidate_feedback_marks_insufficient_sample():
    checks = validate_candidate_feedback(
        {"sample_count": 2},
        policy={"min_sample_count": 5},
    )

    assert checks[0].passed is False
    assert checks[0].reason_code == "insufficient_sample"


def test_validate_candidate_feedback_uses_promotion_defaults_not_freeze_defaults():
    checks = validate_candidate_feedback(
        {
            "sample_count": 8,
            "recommendation": {"bias": "maintain"},
            "brier_like_direction_score": 0.2,
            "horizons": {
                "T+20": {"hit_rate": 0.5, "invalidation_rate": 0.2, "interval_hit_rate": 0.45},
                "T+60": {"hit_rate": 0.1, "invalidation_rate": 0.9, "interval_hit_rate": 0.1},
            },
        },
        policy={
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
    )

    assert checks[0].passed is True
    assert checks[0].reason_code == "research_feedback_passed"


def test_validate_candidate_feedback_downgrades_shadow_failures_to_advisory():
    checks = validate_candidate_feedback(
        {
            "sample_count": 8,
            "recommendation": {"bias": "tighten_risk"},
            "brier_like_direction_score": 0.2,
            "horizons": {
                "T+20": {"hit_rate": 0.1, "invalidation_rate": 0.8, "interval_hit_rate": 0.45},
            },
        },
        policy={
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
        shadow_mode=True,
    )

    assert checks[0].passed is True
    assert checks[0].reason_code == "research_feedback_advisory"
    assert checks[0].details["advisory"] is True


def test_validate_candidate_feedback_falls_back_to_overall_feedback_when_regime_samples_are_insufficient():
    checks = validate_candidate_feedback(
        {
            "sample_count": 4,
            "recommendation": {"bias": "maintain"},
            "horizons": {
                "T+20": {"hit_rate": 0.1, "invalidation_rate": 0.8, "interval_hit_rate": 0.1},
            },
            "overall_feedback": {
                "sample_count": 8,
                "recommendation": {"bias": "maintain"},
                "brier_like_direction_score": 0.2,
                "horizons": {
                    "T+20": {"hit_rate": 0.55, "invalidation_rate": 0.2, "interval_hit_rate": 0.45},
                },
            },
        },
        policy={
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
    )

    assert checks[0].passed is True
    assert checks[0].reason_code == "research_feedback_passed"
    assert checks[0].details["evidence_source"] == "overall_feedback_fallback"


def test_validate_candidate_governance_marks_terminal_discipline_as_blocked():
    checks = validate_candidate_governance(
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate.yaml",
            "promotion_decision": {"applied_to_active": False},
        },
        cycle_history=[],
        policy={"max_pending_cycles": 0},
        optimization_events=[],
    )

    assert checks[0].passed is False
    assert checks[0].reason_code == "governance_blocked"


def test_validate_candidate_governance_downgrades_shadow_feedback_prune_to_advisory():
    checks = validate_candidate_governance(
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate.yaml",
            "promotion_decision": {"applied_to_active": False},
            "shadow_mode": True,
            "research_feedback": {
                "sample_count": 6,
                "recommendation": {"bias": "tighten_risk"},
            },
        },
        cycle_history=[],
        policy={"blocked_feedback_biases": ["tighten_risk"], "min_feedback_samples": 5},
        optimization_events=[],
    )

    assert checks[0].passed is True
    assert checks[0].reason_code == "governance_passed"
    assert checks[0].details["shadow_feedback_advisory"] is True


def test_validate_candidate_governance_downgrades_shadow_failed_ab_prune_to_advisory():
    checks = validate_candidate_governance(
        run_context={
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate.yaml",
            "promotion_decision": {"applied_to_active": False},
            "shadow_mode": True,
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
        policy={"prune_on_failed_candidate_ab": True, "max_selection_overlap_for_failed_candidate": 0.85},
        optimization_events=[],
    )

    assert checks[0].passed is True
    assert checks[0].reason_code == "governance_passed"
    assert checks[0].details["shadow_candidate_prune_advisory"] is True


def test_build_validation_summary_aggregates_failures():
    checks = validate_candidate_ab(
        {
            "comparison": {
                "candidate_present": True,
                "comparable": True,
                "winner": "active",
                "candidate_outperformed": False,
            }
        }
    )

    summary = build_validation_summary(
        validation_task_id="val_deadbeef",
        checks=checks,
        confidence_score=0.9,
    )

    assert summary.status == "failed"
    assert "ab_failed" in summary.reason_codes
    assert summary.failed_checks


def test_build_validation_summary_holds_when_only_governance_blocks():
    summary = build_validation_summary(
        validation_task_id="val_governance_hold",
        checks=[
            ValidationCheck(
                name="research_feedback.active",
                passed=False,
                reason_code="insufficient_sample",
                actual=4,
                threshold=5,
            ),
            ValidationCheck(
                name="promotion_discipline.status",
                passed=False,
                reason_code="governance_blocked",
                actual="candidate_pruned",
                threshold=["candidate_pruned"],
            ),
        ],
        confidence_score=0.95,
    )

    assert summary.status == "hold"
    assert "governance_blocked" in summary.reason_codes
