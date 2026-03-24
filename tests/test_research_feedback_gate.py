from app.training.reporting import evaluate_research_feedback_gate
from invest.research.case_store import ResearchCaseStore
from invest.shared.model_governance import DEFAULT_FREEZE_GATE_POLICY


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


def test_research_case_store_feedback_recommendation_uses_defensive_freeze_gate():
    feedback = ResearchCaseStore._feedback_recommendation(
        {
            "subject": {
                "model_name": "defensive_low_vol",
                "config_name": "defensive_low_vol_v1",
            },
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
    )

    assert feedback["recommendation"]["bias"] == "maintain"
    assert feedback["recommendation"]["reason_codes"] == []
