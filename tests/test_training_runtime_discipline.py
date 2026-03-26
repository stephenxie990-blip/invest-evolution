from types import SimpleNamespace

import pytest

from app.training.runtime_discipline import (
    apply_regime_runtime_profile,
    apply_safety_override,
    build_regime_runtime_profile,
    begin_cycle_runtime_window,
    finalize_cycle_runtime_window,
    record_learning_proposal,
    resolve_active_runtime_params,
    resolve_effective_runtime_params,
)


def test_cycle_runtime_window_freezes_active_params_and_reverts_illegal_mutation():
    controller = SimpleNamespace(
        current_params={"position_size": 0.2, "take_profit_pct": 0.15},
        investment_model=SimpleNamespace(runtime_overrides={}),
    )

    begin_cycle_runtime_window(controller, cycle_id=7)
    controller.current_params["position_size"] = 0.12

    assert resolve_active_runtime_params(controller) == {
        "position_size": 0.2,
        "take_profit_pct": 0.15,
    }

    summary = finalize_cycle_runtime_window(controller)

    assert controller.current_params == {
        "position_size": 0.2,
        "take_profit_pct": 0.15,
    }
    assert summary["violation_count"] == 1
    assert summary["violations"][0]["key"] == "position_size"


def test_record_learning_proposal_keeps_active_runtime_unchanged():
    controller = SimpleNamespace(current_params={"position_size": 0.2})

    proposal = record_learning_proposal(
        controller,
        source="review.param_adjustment",
        patch={"position_size": 0.12},
        target_scope="candidate",
        rationale="tighten risk after repeated losses",
    )

    assert controller.current_params == {"position_size": 0.2}
    assert controller.current_cycle_learning_proposals == [proposal]
    assert proposal["patch"] == {"position_size": 0.12}
    assert proposal["target_scope"] == "candidate"


def test_apply_safety_override_only_allows_whitelisted_keys():
    controller = SimpleNamespace(
        current_params={"position_size": 0.2},
        investment_model=SimpleNamespace(runtime_overrides={}),
    )
    begin_cycle_runtime_window(controller, cycle_id=8)

    applied = apply_safety_override(
        controller,
        {"max_total_exposure_override": 0.3},
        source="risk_guard",
    )

    assert applied == {"max_total_exposure_override": 0.3}
    assert resolve_active_runtime_params(controller)["max_total_exposure_override"] == 0.3

    with pytest.raises(ValueError, match="Non-safety param"):
        apply_safety_override(
            controller,
            {"position_size": 0.12},
            source="risk_guard",
        )


def test_regime_runtime_profile_applies_overlay_without_mutating_active_params():
    controller = SimpleNamespace(
        current_params={
            "position_size": 0.2,
            "cash_reserve": 0.2,
            "signal_threshold": 0.7,
            "max_positions": 4,
        },
        risk_policy={"clamps": {"position_size": {"min": 0.05, "max": 0.3}}},
        review_policy={
            "param_clamps": {
                "cash_reserve": {"min": 0.0, "max": 0.8},
                "signal_threshold": {"min": 0.3, "max": 0.95},
            }
        },
        regime_controls={
            "bear": {
                "position_size": 0.1,
                "cash_reserve": 0.45,
                "signal_threshold": 0.78,
                "max_positions": 2,
            }
        },
        investment_model=SimpleNamespace(runtime_overrides={}),
        last_routing_decision={"regime": "bear"},
    )
    begin_cycle_runtime_window(controller, cycle_id=9)

    profile = build_regime_runtime_profile(controller, regime="bear")
    effective = apply_regime_runtime_profile(controller, profile)

    assert resolve_active_runtime_params(controller) == {
        "position_size": 0.2,
        "cash_reserve": 0.2,
        "signal_threshold": 0.7,
        "max_positions": 4,
    }
    assert effective == {
        "position_size": 0.1,
        "cash_reserve": 0.45,
        "signal_threshold": 0.78,
        "max_positions": 2,
    }
    assert resolve_effective_runtime_params(controller) == effective
    assert controller.investment_model.runtime_overrides == effective


def test_regime_runtime_profile_layers_strategy_family_budget_with_model_overrides():
    controller = SimpleNamespace(
        model_name="momentum",
        strategy_family="momentum",
        current_params={
            "position_size": 0.2,
            "cash_reserve": 0.2,
            "signal_threshold": 0.7,
            "max_positions": 4,
        },
        risk_policy={"clamps": {"position_size": {"min": 0.05, "max": 0.3}}},
        review_policy={
            "param_clamps": {
                "cash_reserve": {"min": 0.0, "max": 0.8},
                "signal_threshold": {"min": 0.3, "max": 0.95},
            }
        },
        regime_controls={
            "bear": {
                "signal_threshold": 0.79,
            }
        },
        investment_model=SimpleNamespace(runtime_overrides={}),
        last_routing_decision={"regime": "bear"},
    )
    begin_cycle_runtime_window(controller, cycle_id=10)

    profile = build_regime_runtime_profile(controller, regime="bear")
    effective = apply_regime_runtime_profile(controller, profile)

    assert profile["strategy_family"] == "momentum"
    assert profile["budget_layering"]["family_budget"] == {
        "position_size": 0.1,
        "cash_reserve": 0.45,
        "max_positions": 2,
    }
    assert profile["budget_layering"]["model_budget_override"] == {}
    assert profile["budget_layering"]["behavior_overlay"] == {"signal_threshold": 0.79}
    assert profile["budget_layering"]["resolved_budget"] == {
        "position_size": 0.1,
        "cash_reserve": 0.45,
        "max_positions": 2,
    }
    assert effective == {
        "position_size": 0.1,
        "cash_reserve": 0.45,
        "signal_threshold": 0.79,
        "max_positions": 2,
    }


def test_regime_runtime_profile_applies_mean_reversion_failure_budget_correction():
    controller = SimpleNamespace(
        model_name="mean_reversion",
        strategy_family="mean_reversion",
        current_params={
            "position_size": 0.2,
            "cash_reserve": 0.2,
            "signal_threshold": 0.7,
            "max_positions": 4,
        },
        cycle_history=[
            SimpleNamespace(
                cycle_id=3,
                is_profit=False,
                return_pct=-1.4,
                benchmark_passed=False,
                selection_mode="algorithm",
                review_applied=False,
                model_name="mean_reversion",
                routing_decision={"regime": "oscillation"},
                research_feedback={"sample_count": 5, "recommendation": {"bias": "maintain"}},
                causal_diagnosis={"primary_driver": "benchmark_gap"},
            ),
            SimpleNamespace(
                cycle_id=4,
                is_profit=False,
                return_pct=-1.1,
                benchmark_passed=False,
                selection_mode="algorithm",
                review_applied=False,
                model_name="mean_reversion",
                routing_decision={"regime": "oscillation"},
                research_feedback={"sample_count": 4, "recommendation": {"bias": "maintain"}},
                causal_diagnosis={"primary_driver": "benchmark_gap"},
            ),
        ],
        risk_policy={"clamps": {"position_size": {"min": 0.05, "max": 0.3}}},
        review_policy={"param_clamps": {"cash_reserve": {"min": 0.0, "max": 0.8}}},
        regime_controls={},
        investment_model=SimpleNamespace(runtime_overrides={}),
        last_routing_decision={"regime": "oscillation"},
    )
    begin_cycle_runtime_window(controller, cycle_id=11)

    profile = build_regime_runtime_profile(controller, regime="oscillation")

    assert profile["budget_layering"]["family_budget"] == {
        "position_size": 0.18,
        "cash_reserve": 0.3,
        "max_positions": 4,
    }
    assert profile["budget_layering"]["family_budget_correction"]["applied"] is True
    assert (
        profile["budget_layering"]["family_budget_correction"]["dominant_failure_sub_signature"]
        == "false_rebound_entry"
    )
    assert profile["budget_layering"]["resolved_budget"] == {
        "position_size": 0.16,
        "cash_reserve": 0.35,
        "max_positions": 3,
    }


def test_regime_runtime_profile_scales_mean_reversion_correction_after_repeated_overcrowded_losses():
    repeated_history = [
        SimpleNamespace(
            cycle_id=cycle_id,
            is_profit=False,
            return_pct=-0.9,
            benchmark_passed=False,
            selection_mode="algorithm",
            review_applied=False,
            model_name="mean_reversion",
            routing_decision={"regime": "oscillation"},
            research_feedback={"sample_count": 4, "recommendation": {"bias": "maintain"}},
            similarity_summary={"matched_cycle_ids": [max(1, cycle_id - 1)]},
            causal_diagnosis={"primary_driver": "regime_repeat_loss"},
        )
        for cycle_id in range(3, 8)
    ]
    controller = SimpleNamespace(
        model_name="mean_reversion",
        strategy_family="mean_reversion",
        current_params={
            "position_size": 0.2,
            "cash_reserve": 0.2,
            "signal_threshold": 0.7,
            "max_positions": 4,
        },
        cycle_history=repeated_history,
        risk_policy={"clamps": {"position_size": {"min": 0.05, "max": 0.3}}},
        review_policy={"param_clamps": {"cash_reserve": {"min": 0.0, "max": 0.8}}},
        regime_controls={},
        investment_model=SimpleNamespace(runtime_overrides={}),
        last_routing_decision={"regime": "oscillation"},
    )
    begin_cycle_runtime_window(controller, cycle_id=13)

    profile = build_regime_runtime_profile(controller, regime="oscillation")
    correction = profile["budget_layering"]["family_budget_correction"]

    assert correction["applied"] is True
    assert correction["dominant_failure_sub_signature"] == "overcrowded_reversion_book"
    assert correction["repeat_multiplier"] == 1.6
    assert correction["base_adjustment_deltas"] == {
        "position_size": -0.01,
        "cash_reserve": 0.04,
        "max_positions": -1,
    }
    assert correction["adjustment_deltas"] == {
        "position_size": -0.016,
        "cash_reserve": 0.064,
        "max_positions": -2,
    }
    assert profile["budget_layering"]["resolved_budget"] == {
        "position_size": 0.164,
        "cash_reserve": 0.364,
        "max_positions": 2,
    }


def test_regime_runtime_profile_family_correction_owns_budget_keys_over_model_budget_override():
    controller = SimpleNamespace(
        model_name="mean_reversion",
        strategy_family="mean_reversion",
        current_params={
            "position_size": 0.2,
            "cash_reserve": 0.2,
            "signal_threshold": 0.7,
            "max_positions": 4,
        },
        cycle_history=[
            SimpleNamespace(
                cycle_id=7,
                is_profit=False,
                return_pct=-1.4,
                benchmark_passed=False,
                selection_mode="algorithm",
                review_applied=False,
                model_name="mean_reversion",
                routing_decision={"regime": "oscillation"},
                research_feedback={"sample_count": 4, "recommendation": {"bias": "maintain"}},
                causal_diagnosis={"primary_driver": "benchmark_gap"},
            ),
            SimpleNamespace(
                cycle_id=8,
                is_profit=False,
                return_pct=-1.1,
                benchmark_passed=False,
                selection_mode="algorithm",
                review_applied=False,
                model_name="mean_reversion",
                routing_decision={"regime": "oscillation"},
                research_feedback={"sample_count": 4, "recommendation": {"bias": "maintain"}},
                causal_diagnosis={"primary_driver": "benchmark_gap"},
            ),
        ],
        risk_policy={"clamps": {"position_size": {"min": 0.05, "max": 0.3}}},
        review_policy={"param_clamps": {"cash_reserve": {"min": 0.0, "max": 0.8}}},
        regime_controls={
            "oscillation": {
                "position_size": 0.19,
                "cash_reserve": 0.27,
                "max_positions": 5,
                "min_reversion_score": 0.82,
            }
        },
        investment_model=SimpleNamespace(runtime_overrides={}),
        last_routing_decision={"regime": "oscillation"},
    )
    begin_cycle_runtime_window(controller, cycle_id=14)

    profile = build_regime_runtime_profile(controller, regime="oscillation")

    assert profile["budget_layering"]["model_budget_override"] == {
        "position_size": 0.19,
        "cash_reserve": 0.27,
        "max_positions": 5,
    }
    assert profile["budget_layering"]["family_budget_correction"]["applied"] is True
    assert profile["budget_layering"]["resolved_budget"] == {
        "position_size": 0.17,
        "cash_reserve": 0.32,
        "max_positions": 4,
    }
    assert profile["effective_params"]["min_reversion_score"] == 0.82


def test_regime_runtime_profile_applies_value_quality_failure_budget_correction():
    controller = SimpleNamespace(
        model_name="value_quality",
        strategy_family="value_quality",
        current_params={
            "position_size": 0.16,
            "cash_reserve": 0.28,
            "signal_threshold": 0.7,
            "max_positions": 3,
        },
        cycle_history=[
            SimpleNamespace(
                cycle_id=5,
                is_profit=False,
                return_pct=-0.3,
                benchmark_passed=False,
                selection_mode="algorithm",
                review_applied=False,
                model_name="value_quality",
                routing_decision={"regime": "oscillation"},
                research_feedback={"sample_count": 5, "recommendation": {"bias": "maintain"}},
                causal_diagnosis={"primary_driver": "benchmark_gap"},
            ),
            SimpleNamespace(
                cycle_id=6,
                is_profit=False,
                return_pct=-0.5,
                benchmark_passed=False,
                selection_mode="algorithm",
                review_applied=False,
                model_name="value_quality",
                routing_decision={"regime": "oscillation"},
                research_feedback={"sample_count": 5, "recommendation": {"bias": "maintain"}},
                causal_diagnosis={"primary_driver": "benchmark_gap"},
            ),
        ],
        risk_policy={"clamps": {"position_size": {"min": 0.05, "max": 0.3}}},
        review_policy={"param_clamps": {"cash_reserve": {"min": 0.0, "max": 0.8}}},
        regime_controls={},
        investment_model=SimpleNamespace(runtime_overrides={}),
        last_routing_decision={"regime": "oscillation"},
    )
    begin_cycle_runtime_window(controller, cycle_id=12)

    profile = build_regime_runtime_profile(controller, regime="oscillation")

    assert profile["budget_layering"]["family_budget_correction"]["applied"] is True
    assert profile["budget_layering"]["family_budget_correction"]["dominant_failure_sub_signature"] == "defensive_lag"
    assert profile["budget_layering"]["resolved_budget"] == {
        "position_size": 0.18,
        "cash_reserve": 0.24,
        "max_positions": 4,
    }
