from types import SimpleNamespace

from invest_evolution.application.training import observability


def test_ensure_proposal_tracking_fields_populates_defaults():
    proposal = {
        "cycle_id": 7,
        "source": "review",
        "patch": {"position_size": 0.12},
        "metadata": {"proposal_kind": "runtime_param_adjustment"},
    }

    normalized = observability.ensure_proposal_tracking_fields(proposal)

    assert normalized["proposal_id"] == "proposal_0007_001"
    assert normalized["suggestion_id"] == "suggestion_0007_001"
    assert normalized["adoption_status"] == "queued"
    assert normalized["effect_status"] == "pending_adoption"
    assert "avg_max_drawdown" in normalized["effect_target_metrics"]
    assert normalized["adoption_ref"]["decision_stage"] == "proposal_recorded"


def test_apply_proposal_outcome_marks_rejected_as_not_applicable():
    proposal = {"cycle_id": 9, "source": "optimization", "patch": {"cash_reserve": 0.2}}

    updated = observability.apply_proposal_outcome(
        proposal,
        adoption_status="rejected_by_proposal_gate",
        decision_cycle_id=9,
        decision_stage="candidate_build_skipped",
        decision_reason="proposal_governance_rejected",
        proposal_bundle_id="bundle_9",
        block_reasons=["single_step_identity_drift_exceeded"],
    )

    assert updated["adoption_status"] == "rejected_by_proposal_gate"
    assert updated["effect_status"] == "not_applicable"
    assert (
        updated["effect_result"]["summary"]
        == "proposal blocked before adoption"
    )
    assert updated["adoption_ref"]["proposal_bundle_id"] == "bundle_9"


def test_evaluate_proposal_effect_marks_improved_after_window():
    proposal = observability.ensure_proposal_tracking_fields(
        {
            "cycle_id": 2,
            "source": "review",
            "patch": {"position_size": 0.08},
            "effect_target_metrics": ["avg_return_pct"],
            "effect_window": {
                "window_cycles": 2,
                "start_cycle_id": 3,
                "end_cycle_id": 4,
                "evaluation_after_cycle_id": 4,
            },
        }
    )
    adopted = observability.apply_proposal_outcome(
        proposal,
        adoption_status="adopted_to_candidate",
        decision_cycle_id=2,
        decision_stage="candidate_build",
        decision_reason="candidate_generated",
    )
    cycle_history = [
        {"cycle_id": 1, "return_pct": 0.02, "benchmark_passed": True},
        {"cycle_id": 2, "return_pct": 0.03, "benchmark_passed": True},
        {"cycle_id": 3, "return_pct": 0.30, "benchmark_passed": True},
        {"cycle_id": 4, "return_pct": 0.25, "benchmark_passed": True},
    ]

    evaluated = observability.evaluate_proposal_effect(
        adopted,
        cycle_history=cycle_history,
        current_cycle_id=4,
    )

    assert evaluated["effect_status"] == "improved"
    assert evaluated["effect_result"]["status"] == "improved"
    assert evaluated["effect_result"]["observed_cycles"] == 2
    assert evaluated["effect_result"]["metric_results"][0]["metric"] == "avg_return_pct"


def test_refresh_cycle_history_suggestion_effects_updates_bundle_and_calls_persistence(
    monkeypatch,
):
    calls: list[tuple[str, int]] = []

    def _fake_update(controller, *, bundle_path, proposals):
        calls.append((bundle_path, len(proposals)))
        return {
            "bundle_path": bundle_path,
            "proposal_bundle_id": "bundle_0001",
            "proposals": proposals,
            "suggestion_tracking_summary": observability.build_suggestion_tracking_summary(
                proposals
            ),
        }

    monkeypatch.setattr(
        observability,
        "_persistence_module",
        lambda: SimpleNamespace(update_cycle_proposal_bundle=_fake_update),
    )
    proposal = observability.apply_proposal_outcome(
        {
            "cycle_id": 1,
            "source": "review",
            "patch": {"position_size": 0.08},
            "effect_target_metrics": ["avg_return_pct"],
            "effect_window": {
                "window_cycles": 1,
                "start_cycle_id": 2,
                "end_cycle_id": 2,
                "evaluation_after_cycle_id": 2,
            },
        },
        adoption_status="adopted_to_candidate",
        decision_cycle_id=1,
        decision_stage="candidate_build",
        decision_reason="candidate_generated",
    )
    cycle_history = [
        {
            "cycle_id": 1,
            "return_pct": 0.02,
            "benchmark_passed": True,
            "proposal_bundle": {
                "bundle_path": "artifacts/cycle_0001_proposals.json",
                "proposal_bundle_id": "bundle_0001",
                "proposals": [proposal],
            },
        },
        {"cycle_id": 2, "return_pct": 0.30, "benchmark_passed": True},
    ]

    summary = observability.refresh_cycle_history_suggestion_effects(
        controller=SimpleNamespace(),
        cycle_history=cycle_history,
    )

    bundle = cycle_history[0]["proposal_bundle"]
    assert summary["updated_bundle_count"] == 1
    assert summary["completed_effect_count"] == 1
    assert summary["evaluated_suggestion_count"] == 1
    assert calls == [("artifacts/cycle_0001_proposals.json", 1)]
    assert bundle["suggestion_tracking_summary"]["improved_suggestion_count"] == 1


def test_build_suggestion_tracking_summary_counts_statuses():
    proposals = [
        {"cycle_id": 1, "source": "review", "adoption_status": "queued"},
        {
            "cycle_id": 2,
            "source": "review",
            "adoption_status": "adopted_to_candidate",
            "effect_status": "improved",
        },
        {
            "cycle_id": 3,
            "source": "optimizer",
            "adoption_status": "rejected_by_proposal_gate",
            "effect_status": "not_applicable",
        },
    ]

    summary = observability.build_suggestion_tracking_summary(proposals)

    assert summary["suggestion_count"] == 3
    assert summary["queued_suggestion_count"] == 1
    assert summary["adopted_suggestion_count"] == 1
    assert summary["rejected_suggestion_count"] == 1
    assert summary["improved_suggestion_count"] == 1
    assert summary["source_counts"]["review"] == 2
    assert summary["source_counts"]["optimizer"] == 1
