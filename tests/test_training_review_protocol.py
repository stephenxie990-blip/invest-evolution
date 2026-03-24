from types import SimpleNamespace

from invest_evolution.application.training.review import build_review_input
from invest_evolution.application.training.observability import (
    build_review_eval_projection_boundary,
)
from invest_evolution.application.training.controller import TrainingSessionState
from invest_evolution.investment.contracts import EvalReport


def _governance_decision(regime: str, dominant_manager_id: str = "momentum") -> dict:
    return {
        "dominant_manager_id": dominant_manager_id,
        "active_manager_ids": [dominant_manager_id],
        "manager_budget_weights": {dominant_manager_id: 1.0},
        "regime": regime,
    }


def _history_item(
    *,
    cycle_id: int,
    cutoff_date: str,
    return_pct: float,
    is_profit: bool,
    selection_mode: str,
    benchmark_passed: bool,
    review_applied: bool,
    regime: str,
    manager_id: str = "momentum",
    manager_config_ref: str = "configs/momentum.yaml",
    audit_tags: dict | None = None,
    research_feedback: dict | None = None,
    llm_used: bool = False,
    causal_diagnosis: dict | None = None,
    similarity_summary: dict | None = None,
    review_decision: dict | None = None,
    ab_comparison: dict | None = None,
):
    return SimpleNamespace(
        cycle_id=cycle_id,
        cutoff_date=cutoff_date,
        return_pct=return_pct,
        is_profit=is_profit,
        selection_mode=selection_mode,
        benchmark_passed=benchmark_passed,
        review_applied=review_applied,
        dominant_manager_id=manager_id,
        execution_defaults={"default_manager_config_ref": manager_config_ref},
        run_context={"manager_config_ref": manager_config_ref},
        governance_decision=_governance_decision(regime, dominant_manager_id=manager_id),
        audit_tags=dict(audit_tags or {}),
        research_feedback=dict(research_feedback or {}),
        llm_used=llm_used,
        causal_diagnosis=dict(causal_diagnosis or {}),
        similarity_summary=dict(similarity_summary or {}),
        review_decision=dict(review_decision or {}),
        ab_comparison=dict(ab_comparison or {}),
    )


def test_build_review_input_uses_single_cycle_window_by_default():
    controller = SimpleNamespace(
        cycle_history=[
            _history_item(
                cycle_id=1,
                cutoff_date="20240101",
                return_pct=-1.2,
                is_profit=False,
                selection_mode="meeting",
                benchmark_passed=False,
                review_applied=False,
                regime="bear",
            ),
        ],
        experiment_review_window={},
        default_manager_id="momentum",
        default_manager_config_ref="configs/momentum.yaml",
    )
    eval_report = EvalReport(
        cycle_id=2,
        as_of_date="20240102",
        return_pct=1.5,
        total_pnl=1500.0,
        total_trades=3,
        win_rate=2 / 3,
        regime="bull",
        is_profit=True,
        selected_codes=["sh.600519"],
        selection_mode="meeting",
    )

    review_input = build_review_input(controller, cycle_id=2, eval_report=eval_report)

    assert review_input["review_basis_window"] == {
        "mode": "single_cycle",
        "size": 1,
        "cycle_ids": [2],
        "current_cycle_id": 2,
    }
    assert len(review_input["recent_results"]) == 1
    assert review_input["recent_results"][0]["cycle_id"] == 2


def test_build_review_input_includes_prior_cycle_results_for_rolling_window():
    controller = SimpleNamespace(
        cycle_history=[
            _history_item(
                cycle_id=1,
                cutoff_date="20240101",
                return_pct=-1.2,
                is_profit=False,
                selection_mode="meeting",
                benchmark_passed=False,
                review_applied=False,
                regime="bear",
                audit_tags={"governance_regime": "bear"},
                research_feedback={"recommendation": {"bias": "tighten_risk"}},
            ),
            _history_item(
                cycle_id=2,
                cutoff_date="20240102",
                return_pct=0.8,
                is_profit=True,
                selection_mode="algorithm",
                benchmark_passed=True,
                review_applied=True,
                regime="oscillation",
                audit_tags={"governance_regime": "oscillation"},
            ),
        ],
        experiment_review_window={"mode": "rolling", "size": 3},
        default_manager_id="momentum",
        default_manager_config_ref="configs/momentum.yaml",
    )
    eval_report = EvalReport(
        cycle_id=3,
        as_of_date="20240103",
        return_pct=1.5,
        total_pnl=1500.0,
        total_trades=3,
        win_rate=2 / 3,
        regime="bull",
        is_profit=True,
        selected_codes=["sh.600519"],
        selection_mode="meeting",
    )

    review_input = build_review_input(controller, cycle_id=3, eval_report=eval_report)
    records = review_input["recent_results"]

    assert review_input["review_basis_window"] == {
        "mode": "rolling",
        "size": 3,
        "cycle_ids": [1, 2, 3],
        "current_cycle_id": 3,
    }
    assert [item["cycle_id"] for item in records] == [1, 2, 3]
    assert records[0]["metadata"]["research_feedback"]["recommendation"]["bias"] == "tighten_risk"
    assert records[1]["selection_mode"] == "algorithm"
    assert records[2]["regime"] == "bull"


def test_build_review_input_adds_similar_sample_retrieval_and_causal_diagnosis():
    controller = SimpleNamespace(
        cycle_history=[
            _history_item(
                cycle_id=1,
                cutoff_date="20240101",
                return_pct=-1.2,
                is_profit=False,
                selection_mode="meeting",
                benchmark_passed=False,
                review_applied=False,
                regime="bear",
                manager_config_ref="configs/momentum_a.yaml",
                audit_tags={"governance_regime": "bear"},
                llm_used=True,
            ),
            _history_item(
                cycle_id=2,
                cutoff_date="20240102",
                return_pct=0.7,
                is_profit=True,
                selection_mode="algorithm",
                benchmark_passed=True,
                review_applied=True,
                regime="bull",
                manager_config_ref="configs/momentum_a.yaml",
                audit_tags={"governance_regime": "bull"},
            ),
            _history_item(
                cycle_id=3,
                cutoff_date="20240103",
                return_pct=-0.9,
                is_profit=False,
                selection_mode="meeting",
                benchmark_passed=False,
                review_applied=False,
                regime="bear",
                manager_config_ref="configs/momentum_a.yaml",
                audit_tags={"governance_regime": "bear"},
                llm_used=True,
            ),
        ],
        experiment_review_window={"mode": "rolling", "size": 2},
        default_manager_id="momentum",
        default_manager_config_ref="configs/momentum_a.yaml",
    )
    eval_report = EvalReport(
        cycle_id=4,
        as_of_date="20240104",
        return_pct=-1.1,
        total_pnl=-1100.0,
        total_trades=4,
        win_rate=0.25,
        regime="bear",
        is_profit=False,
        selected_codes=["sh.600519"],
        selection_mode="meeting",
        benchmark_passed=False,
        metadata={"manager_id": "momentum", "manager_config_ref": "configs/momentum_a.yaml"},
    )

    review_input = build_review_input(controller, cycle_id=4, eval_report=eval_report)

    assert [item["cycle_id"] for item in review_input["similar_results"]] == [3, 1]
    assert review_input["similarity_summary"]["matched_cycle_ids"] == [3, 1]
    assert review_input["similarity_summary"]["dominant_regime"] == "bear"
    assert review_input["causal_diagnosis"]["primary_driver"] == "regime_repeat_loss"
    assert review_input["causal_diagnosis"]["drivers"][0]["code"] == "regime_repeat_loss"
    assert review_input["causal_diagnosis"]["drivers"][0]["evidence_cycle_ids"] == [3, 1]
    assert "同一市场状态下重复亏损" in review_input["causal_diagnosis"]["summary"]


def test_build_review_input_filters_to_matching_failure_signature_and_bias():
    controller = SimpleNamespace(
        cycle_history=[
            _history_item(
                cycle_id=1,
                cutoff_date="20240101",
                return_pct=-1.4,
                is_profit=False,
                selection_mode="meeting",
                benchmark_passed=False,
                review_applied=False,
                regime="bear",
                manager_config_ref="configs/momentum_a.yaml",
                audit_tags={"governance_regime": "bear"},
                research_feedback={"sample_count": 6, "recommendation": {"bias": "tighten_risk"}},
                causal_diagnosis={"primary_driver": "benchmark_gap"},
                llm_used=True,
            ),
            _history_item(
                cycle_id=2,
                cutoff_date="20240102",
                return_pct=-0.6,
                is_profit=False,
                selection_mode="meeting",
                benchmark_passed=True,
                review_applied=False,
                regime="bear",
                manager_config_ref="configs/momentum_a.yaml",
                audit_tags={"governance_regime": "bear"},
                research_feedback={"sample_count": 6, "recommendation": {"bias": "maintain"}},
                causal_diagnosis={"primary_driver": "benchmark_gap"},
                llm_used=True,
            ),
            _history_item(
                cycle_id=3,
                cutoff_date="20240103",
                return_pct=0.9,
                is_profit=True,
                selection_mode="meeting",
                benchmark_passed=False,
                review_applied=True,
                regime="bear",
                manager_config_ref="configs/momentum_a.yaml",
                audit_tags={"governance_regime": "bear"},
                research_feedback={"sample_count": 6, "recommendation": {"bias": "tighten_risk"}},
                causal_diagnosis={"primary_driver": "benchmark_gap"},
                llm_used=True,
            ),
        ],
        experiment_review_window={"mode": "rolling", "size": 3},
        default_manager_id="momentum",
        default_manager_config_ref="configs/momentum_a.yaml",
    )
    eval_report = {
        "cycle_id": 4,
        "as_of_date": "20240104",
        "return_pct": -1.1,
        "total_pnl": -1100.0,
        "total_trades": 4,
        "win_rate": 0.25,
        "regime": "bear",
        "is_profit": False,
        "selected_codes": ["sh.600519"],
        "selection_mode": "meeting",
        "benchmark_passed": False,
        "metadata": {"manager_id": "momentum", "manager_config_ref": "configs/momentum_a.yaml"},
        "research_feedback": {"sample_count": 7, "recommendation": {"bias": "tighten_risk"}},
        "causal_diagnosis": {"primary_driver": "benchmark_gap"},
    }

    review_input = build_review_input(controller, cycle_id=4, eval_report=eval_report)

    assert [item["cycle_id"] for item in review_input["similar_results"]] == [1]
    assert review_input["similarity_summary"]["matched_cycle_ids"] == [1]
    assert review_input["similarity_summary"]["matched_primary_driver"] == "benchmark_gap"
    assert review_input["similarity_summary"]["matched_feedback_bias"] == "tighten_risk"


def test_build_review_input_prefers_session_state_cycle_history():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            cycle_history=[
                _history_item(
                    cycle_id=1,
                    cutoff_date="20240101",
                    return_pct=-1.2,
                    is_profit=False,
                    selection_mode="meeting",
                    benchmark_passed=False,
                    review_applied=False,
                    regime="bear",
                ),
            ],
        ),
        cycle_history=[],
        experiment_review_window={"mode": "rolling", "size": 2},
        default_manager_id="momentum",
        default_manager_config_ref="configs/momentum.yaml",
    )
    eval_report = EvalReport(
        cycle_id=2,
        as_of_date="20240102",
        return_pct=0.5,
        total_pnl=500.0,
        total_trades=2,
        win_rate=0.5,
        regime="bull",
        is_profit=True,
        selected_codes=["sh.600519"],
        selection_mode="meeting",
    )

    review_input = build_review_input(controller, cycle_id=2, eval_report=eval_report)

    assert [item["cycle_id"] for item in review_input["recent_results"]] == [1, 2]


def test_build_review_eval_projection_keeps_manager_portfolio_subject_from_snapshot_when_portfolio_plan_missing():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="momentum",
            default_manager_config_ref="configs/momentum_v1.yaml",
        ),
    )
    execution_snapshot = {
        "subject_type": "manager_portfolio",
        "selection_mode": "manager_portfolio",
        "manager_results": [
            {"manager_id": "momentum"},
            {"manager_id": "value_quality"},
        ],
        "dominant_manager_id": "momentum",
        "active_runtime_config_ref": "configs/momentum_v1.yaml",
        "manager_config_ref": "configs/momentum_v1.yaml",
    }

    projection = build_review_eval_projection_boundary(
        controller,
        manager_output=SimpleNamespace(
            manager_id="momentum",
            manager_config_ref="configs/momentum_v1.yaml",
        ),
        cycle_payload={"execution_snapshot": execution_snapshot},
        execution_snapshot=execution_snapshot,
        simulation_envelope=None,
        manager_results=list(execution_snapshot["manager_results"]),
        portfolio_plan={},
        dominant_manager_id="momentum",
    )

    assert projection.subject_type == "manager_portfolio"
    assert projection.compatibility_fields["derived"] is True
    assert projection.compatibility_fields["source"] == "dominant_manager"
