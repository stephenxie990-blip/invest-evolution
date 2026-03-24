from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from invest_evolution.application.training.persistence import (
    build_cycle_result_persistence_payload,
    list_cycle_proposal_bundles,
    load_cycle_proposal_bundle,
    persist_cycle_proposal_bundle,
    update_cycle_proposal_bundle,
)


def _build_minimal_result(**overrides):
    payload = {
        "cycle_id": 1,
        "cutoff_date": "20240101",
        "selected_stocks": [],
        "initial_capital": 100000,
        "final_value": 100000,
        "return_pct": 0.0,
        "is_profit": False,
        "trade_history": [],
        "params": {},
        "analysis": "",
        "data_mode": "offline",
        "requested_data_mode": "offline",
        "effective_data_mode": "offline",
        "llm_mode": "dry_run",
        "degraded": False,
        "degrade_reason": "",
        "selection_mode": "manager_portfolio",
        "agent_used": False,
        "llm_used": False,
        "benchmark_passed": False,
        "strategy_scores": {},
        "review_applied": False,
        "config_snapshot_path": "",
        "optimization_events": [],
        "audit_tags": {},
        "governance_decision": {},
        "execution_defaults": {},
        "execution_snapshot": {},
        "run_context": {},
        "promotion_record": {},
        "lineage_record": {},
        "manager_results": [],
        "portfolio_plan": {},
        "portfolio_attribution": {},
        "manager_review_report": {},
        "allocation_review_report": {},
        "dominant_manager_id": "",
        "compatibility_fields": {},
        "review_decision": {},
        "causal_diagnosis": {},
        "similarity_summary": {},
        "similar_results": [],
        "realism_metrics": {},
        "stage_snapshots": {},
        "validation_report": {},
        "validation_summary": {},
        "peer_comparison_report": {},
        "judge_report": {},
        "research_feedback": {},
        "research_artifacts": {},
        "ab_comparison": {},
        "experiment_spec": {},
        "proposal_bundle": {},
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_persist_and_load_cycle_proposal_bundle_roundtrip(tmp_path):
    controller = SimpleNamespace(
        output_dir=str(tmp_path),
        model_name="momentum",
        model_config_path="configs/active.yaml",
        current_cycle_learning_proposals=[
            {
                "source": "review",
                "patch": {"position_size": 0.08},
                "rationale": "tighten position sizing after drawdown",
            }
        ],
    )

    bundle = persist_cycle_proposal_bundle(
        controller,
        cycle_id=5,
        execution_snapshot={
            "active_runtime_config_ref": "configs/active.yaml",
            "runtime_fingerprint": "fp-001",
        },
    )

    assert bundle["proposal_bundle_id"].startswith("proposal_bundle_0005_")
    assert bundle["proposal_count"] == 1
    assert bundle["proposal_ids"]
    assert bundle["suggestion_tracking_summary"]["suggestion_count"] == 1
    assert Path(bundle["bundle_path"]).exists()
    assert controller.last_cycle_proposal_bundle["proposal_bundle_id"] == bundle["proposal_bundle_id"]

    loaded = load_cycle_proposal_bundle(bundle["bundle_path"])
    assert loaded["proposal_bundle_id"] == bundle["proposal_bundle_id"]
    assert loaded["proposal_ids"] == bundle["proposal_ids"]
    assert loaded["proposal_count"] == 1
    assert loaded["suggestion_tracking_summary"]["suggestion_count"] == 1
    assert loaded["proposals"][0]["proposal_id"] == bundle["proposal_ids"][0]


def test_list_cycle_proposal_bundles_supports_limit(tmp_path):
    controller = SimpleNamespace(
        output_dir=str(tmp_path),
        model_name="momentum",
        model_config_path="configs/active.yaml",
        current_cycle_learning_proposals=[{"source": "review", "patch": {"position_size": 0.1}}],
    )
    first = persist_cycle_proposal_bundle(controller, cycle_id=1)
    second = persist_cycle_proposal_bundle(controller, cycle_id=2)

    bundles = list_cycle_proposal_bundles(tmp_path)
    assert [item["cycle_id"] for item in bundles] == [1, 2]
    assert bundles[0]["proposal_bundle_id"] == first["proposal_bundle_id"]
    assert bundles[1]["proposal_bundle_id"] == second["proposal_bundle_id"]

    latest_only = list_cycle_proposal_bundles(tmp_path, limit=1)
    assert len(latest_only) == 1
    assert latest_only[0]["cycle_id"] == 2
    assert latest_only[0]["proposal_bundle_id"] == second["proposal_bundle_id"]


def test_update_cycle_proposal_bundle_rebuilds_summary_and_persists_extra_fields(tmp_path):
    controller = SimpleNamespace(
        output_dir=str(tmp_path),
        model_name="momentum",
        model_config_path="configs/active.yaml",
        current_cycle_learning_proposals=[{"source": "review", "patch": {"position_size": 0.1}}],
    )
    bundle = persist_cycle_proposal_bundle(controller, cycle_id=8)
    updated = update_cycle_proposal_bundle(
        controller,
        bundle_path=bundle["bundle_path"],
        proposals=[
            {
                "source": "optimization",
                "patch": {"stop_loss_pct": 0.04},
                "adoption_status": "adopted_to_candidate",
                "effect_status": "pending",
            }
        ],
        extra_fields={"adoption_window_status": "waiting"},
    )

    assert updated["proposal_count"] == 1
    assert updated["adoption_window_status"] == "waiting"
    assert updated["suggestion_tracking_summary"]["suggestion_count"] == 1
    assert updated["suggestion_tracking_summary"]["adoption_status_counts"]["adopted_to_candidate"] == 1
    assert updated["suggestion_tracking_summary"]["effect_status_counts"]["pending"] == 1

    reloaded = load_cycle_proposal_bundle(bundle["bundle_path"])
    assert reloaded["adoption_window_status"] == "waiting"
    assert reloaded["proposal_count"] == 1
    assert reloaded["suggestion_tracking_summary"]["adoption_status_counts"]["adopted_to_candidate"] == 1


def test_cycle_result_payload_includes_proposal_bundle_digest(tmp_path):
    bundle = {
        "schema_version": "training.proposal_bundle.v1",
        "proposal_bundle_id": "proposal_bundle_0012_deadbeef",
        "cycle_id": 12,
        "bundle_path": str(tmp_path / "proposal_store" / "cycle_0012_proposal_bundle_0012_deadbeef.json"),
        "proposal_count": 2,
        "proposal_ids": ["proposal_0012_001", "proposal_0012_002"],
        "suggestion_tracking_summary": {"suggestion_count": 2, "source_counts": {"review": 2}},
    }
    controller = SimpleNamespace(
        output_dir=str(tmp_path),
        last_allocation_plan={},
        assessment_history=[],
    )
    result = _build_minimal_result(
        cycle_id=12,
        proposal_bundle=bundle,
        execution_snapshot={"proposal_bundle": bundle},
        run_context={"proposal_bundle": bundle},
    )

    payload = build_cycle_result_persistence_payload(controller, result)

    assert payload["proposal_bundle"]["proposal_bundle_id"] == "proposal_bundle_0012_deadbeef"
    assert payload["proposal_bundle"]["proposal_count"] == 2
    assert payload["proposal_bundle"]["suggestion_tracking_summary"]["suggestion_count"] == 2
    assert payload["execution_snapshot"]["proposal_bundle"]["proposal_bundle_id"] == "proposal_bundle_0012_deadbeef"
    assert payload["run_context"]["proposal_bundle"]["proposal_count"] == 2
