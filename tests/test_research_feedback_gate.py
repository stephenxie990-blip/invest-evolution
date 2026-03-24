from invest_evolution.application.training.observability import (
    evaluate_research_feedback_gate,
)
from invest_evolution.investment.shared.policy import DEFAULT_FREEZE_GATE_POLICY
from invest_evolution.investment.research.case_store import ResearchCaseStore


def test_evaluate_research_feedback_gate_can_disable_default_horizon_policy():
    feedback = {
        "sample_count": 12,
        "recommendation": {"bias": "maintain"},
        "brier_like_direction_score": 0.21,
        "horizons": {
            "T+5": {
                "hit_rate": 0.45,
                "invalidation_rate": 0.15,
                "interval_hit_rate": 0.72,
            },
            "T+10": {
                "hit_rate": 0.40,
                "invalidation_rate": 0.22,
                "interval_hit_rate": 0.64,
            },
            "T+20": {
                "hit_rate": 0.32,
                "invalidation_rate": 0.32,
                "interval_hit_rate": 0.74,
            },
            "T+60": {
                "hit_rate": 0.0,
                "invalidation_rate": 0.90,
                "interval_hit_rate": 0.66,
            },
        },
    }
    policy = {
        "apply_default_horizon_policy": False,
        "min_sample_count": 8,
        "blocked_biases": ["tighten_risk", "recalibrate_probability"],
        "max_brier_like_direction_score": 0.25,
        "horizons": {
            "T+5": {
                "min_hit_rate": 0.40,
                "max_invalidation_rate": 0.20,
                "min_interval_hit_rate": 0.65,
            },
            "T+10": {
                "min_hit_rate": 0.38,
                "max_invalidation_rate": 0.25,
                "min_interval_hit_rate": 0.60,
            },
            "T+20": {
                "min_hit_rate": 0.30,
                "max_invalidation_rate": 0.33,
                "min_interval_hit_rate": 0.70,
            },
        },
    }

    result = evaluate_research_feedback_gate(
        feedback,
        policy=policy,
        defaults=dict(DEFAULT_FREEZE_GATE_POLICY.get("research_feedback") or {}),
    )

    assert result["active"] is True
    assert result["passed"] is True
    assert all(item.get("horizon") != "T+60" for item in result["checks"])


def test_defensive_low_vol_feedback_recommendation_uses_defensive_horizon_bias():
    summary = {
        "subject": {"manager_id": "defensive_low_vol"},
        "sample_count": 103,
        "brier_like_direction_score": 0.2108,
        "horizons": {
            "T+5": {
                "hit_rate": 0.5049,
                "invalidation_rate": 0.1068,
                "interval_hit_rate": 0.7476,
            },
            "T+10": {
                "hit_rate": 0.4078,
                "invalidation_rate": 0.2136,
                "interval_hit_rate": 0.6699,
            },
            "T+20": {
                "hit_rate": 0.3204,
                "invalidation_rate": 0.3107,
                "interval_hit_rate": 0.7767,
            },
        },
    }

    feedback = ResearchCaseStore._feedback_recommendation(summary)

    assert feedback["recommendation"]["bias"] == "maintain"
    assert feedback["recommendation"]["reason_codes"] == []


def test_feedback_recommendation_reads_thresholds_from_manager_config(monkeypatch):
    monkeypatch.setattr(
        ResearchCaseStore,
        "_manager_research_feedback_policy",
        staticmethod(
            lambda manager_id, manager_config_ref: {
                "apply_default_horizon_policy": False,
                "min_sample_count": 8,
                "blocked_biases": ["tighten_risk", "recalibrate_probability"],
                "max_brier_like_direction_score": 0.25,
                "horizons": {
                    "T+5": {
                        "min_hit_rate": 0.55,
                        "max_invalidation_rate": 0.20,
                        "min_interval_hit_rate": 0.65,
                    },
                    "T+20": {
                        "min_hit_rate": 0.30,
                        "max_invalidation_rate": 0.33,
                        "min_interval_hit_rate": 0.70,
                    },
                },
            }
        ),
    )
    summary = {
        "subject": {
            "manager_id": "defensive_low_vol",
            "manager_config_ref": "defensive_low_vol_v1",
        },
        "sample_count": 20,
        "brier_like_direction_score": 0.20,
        "horizons": {
            "T+5": {
                "hit_rate": 0.50,
                "invalidation_rate": 0.10,
                "interval_hit_rate": 0.80,
            },
            "T+20": {
                "hit_rate": 0.35,
                "invalidation_rate": 0.30,
                "interval_hit_rate": 0.75,
            },
        },
    }

    feedback = ResearchCaseStore._feedback_recommendation(summary)

    assert feedback["recommendation"]["bias"] == "tighten_risk"
    assert feedback["recommendation"]["reason_codes"] == ["t5_hit_rate_low"]


def test_feedback_recommendation_falls_back_to_generic_heuristics_when_policy_is_inactive(monkeypatch):
    monkeypatch.setattr(
        ResearchCaseStore,
        "_manager_research_feedback_policy",
        staticmethod(
            lambda manager_id, manager_config_ref: {
                "apply_default_horizon_policy": False,
                "min_sample_count": 5,
                "blocked_biases": ["tighten_risk", "recalibrate_probability"],
                "max_brier_like_direction_score": 0.25,
                "horizons": {
                    "T+20": {
                        "min_hit_rate": 0.30,
                        "max_invalidation_rate": 0.33,
                        "min_interval_hit_rate": 0.70,
                    },
                },
            }
        ),
    )
    summary = {
        "subject": {
            "manager_id": "momentum",
            "manager_config_ref": "momentum_v1",
        },
        "sample_count": 5,
        "brier_like_direction_score": 0.20,
        "horizons": {
            "T+20": {
                "hit_rate": 0.20,
                "invalidation_rate": 0.20,
                "interval_hit_rate": 0.80,
            },
        },
    }

    feedback = ResearchCaseStore._feedback_recommendation(summary)

    assert feedback["recommendation"]["bias"] == "tighten_risk"
    assert feedback["recommendation"]["reason_codes"] == ["t20_hit_rate_low"]


def test_evaluate_research_feedback_gate_treats_legacy_overall_fallback_as_unavailable():
    feedback = {
        "sample_count": 200,
        "recommendation": {"bias": "tighten_risk"},
        "scope": {
            "requested_regime": "bull",
            "effective_scope": "overall_fallback",
            "overall_sample_count": 200,
            "regime_sample_count": 0,
        },
        "horizons": {
            "T+20": {
                "hit_rate": 0.06,
                "invalidation_rate": 0.65,
                "interval_hit_rate": 0.69,
            }
        },
    }

    result = evaluate_research_feedback_gate(
        feedback,
        policy=dict(DEFAULT_FREEZE_GATE_POLICY.get("research_feedback") or {}),
        defaults=dict(DEFAULT_FREEZE_GATE_POLICY.get("research_feedback") or {}),
    )

    assert result["active"] is False
    assert result["passed"] is True
    assert result["reason"] == "requested_regime_feedback_unavailable"
    assert result["checks"][0]["name"] == "requested_regime_scope"
