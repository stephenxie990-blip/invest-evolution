from types import SimpleNamespace

from invest_evolution.application.training import research as research_module
from invest_evolution.application.training import review as review_module


def test_build_learning_proposals_from_review_decision_normalizes_scopes():
    decision = {
        "subject_type": "manager_portfolio",
        "verdict": "adjust",
        "decision_source": "dual_review",
        "reasoning": "tighten risk after weak benchmark pass",
        "strategy_suggestions": ["tighten_position_size"],
        "param_adjustments": {"position_size": 0.12},
        "agent_weight_adjustments": {"manager.momentum": 0.52},
        "manager_budget_adjustments": {"momentum": 0.62, "defensive_low_vol": 0.38},
    }

    proposals = review_module._build_learning_proposals_from_review_decision(
        cycle_id=12,
        review_decision=decision,
        active_params_snapshot={"position_size": 0.15},
    )

    assert [item["proposal_id"] for item in proposals] == [
        "proposal_0012_001",
        "proposal_0012_002",
        "proposal_0012_003",
    ]
    assert proposals[0]["source"] == "review.param_adjustment"
    assert proposals[0]["target_scope"] == "candidate"
    assert proposals[1]["source"] == "review.agent_weight_adjustment"
    assert proposals[2]["source"] == "review.manager_budget_adjustment"
    assert proposals[2]["target_scope"] == "manager_budget"
    assert proposals[0]["metadata"]["proposal_kind"] == "param_adjustment"
    assert proposals[1]["metadata"]["proposal_kind"] == "agent_weight_adjustment"
    assert proposals[2]["metadata"]["proposal_kind"] == "manager_budget_adjustment"


def test_apply_review_decision_attaches_learning_proposals_to_event(monkeypatch):
    monkeypatch.setattr(
        review_module,
        "apply_review_decision_boundary_effects",
        lambda *_args, **_kwargs: True,
    )
    controller = SimpleNamespace(
        current_params={"position_size": 0.15},
        manager_shadow_mode=False,
    )
    review_event = SimpleNamespace(applied_change={})
    review_decision = {
        "subject_type": "manager_portfolio",
        "decision_source": "dual_review",
        "verdict": "adjust",
        "reasoning": "tighten risk",
        "strategy_suggestions": ["tighten_position_size"],
        "param_adjustments": {"position_size": 0.12},
        "agent_weight_adjustments": {"manager.momentum": 0.52},
    }

    applied = review_module.TrainingReviewService().apply_review_decision(
        controller,
        cycle_id=9,
        review_decision=review_decision,
        review_event=review_event,
    )

    assert applied is True
    assert review_event.applied_change["proposal_refs"] == [
        "proposal_0009_001",
        "proposal_0009_002",
    ]
    assert len(review_event.applied_change["learning_proposals"]) == 2
    assert review_event.applied_change["learning_proposals"][0]["source"] == "review.param_adjustment"


def test_feedback_optimization_plan_includes_learning_proposals(monkeypatch):
    monkeypatch.setattr(
        research_module,
        "evaluate_research_feedback_gate",
        lambda *_args, **_kwargs: {
            "active": True,
            "passed": False,
            "bias": "tighten_risk",
            "failed_checks": [
                {
                    "name": "research_feedback.horizons.T+20.hit_rate",
                    "horizon": "T+20",
                }
            ],
        },
    )

    class _FreezeGateService:
        @staticmethod
        def rolling_self_assessment(_controller, *, window: int):
            assert window >= 3
            return {"benchmark_pass_rate": 0.40}

    controller = SimpleNamespace(
        research_feedback_optimization_policy={},
        freeze_total_cycles=10,
        freeze_gate_policy={"benchmark_pass_rate_gte": 0.60},
        freeze_gate_service=_FreezeGateService(),
        last_feedback_optimization_cycle_id=0,
        current_params={
            "position_size": 0.16,
            "stop_loss_pct": 0.09,
            "take_profit_pct": 0.24,
            "cash_reserve": 0.08,
            "trailing_pct": 0.07,
            "max_hold_days": 15,
            "signal_threshold": 0.52,
        },
    )
    feedback = {
        "sample_count": 18,
        "recommendation": {
            "bias": "tighten_risk",
            "summary": "hit rate declined",
        },
    }

    plan = research_module.TrainingFeedbackService().build_feedback_optimization_plan(
        controller,
        feedback,
        cycle_id=11,
    )

    assert plan["trigger"] == "research_feedback"
    assert plan["learning_proposals"]
    assert plan["proposal_count"] == len(plan["learning_proposals"])
    assert plan["proposal_refs"] == [
        item["proposal_id"] for item in plan["learning_proposals"]
    ]
    assert all(item["target_scope"] == "candidate" for item in plan["learning_proposals"])
    assert {
        item["metadata"]["proposal_kind"] for item in plan["learning_proposals"]
    } == {"runtime_param_adjustment"}
