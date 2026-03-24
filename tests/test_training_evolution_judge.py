from invest_evolution.application.tagging import PeerComparisonResult, TaggingResult, ValidationSummary
from invest_evolution.application.training.research import build_judge_report


def test_build_judge_report_promotes_valid_candidate():
    summary = ValidationSummary(
        validation_task_id="val_123",
        status="passed",
        confidence_score=0.95,
        validation_tags=["validation_passed"],
        reason_codes=[],
        checks=[],
        failed_checks=[],
    )
    peer = PeerComparisonResult(
        compared_market_tag="bull",
        comparable=True,
        compared_count=1,
        ranked_peers=[],
        candidate_outperformed_peers=True,
        reason_codes=["candidate_outperformed_peers"],
        summary="candidate outperformed selected peers",
    )
    failure = TaggingResult(
        tag_family="failure",
        primary_tag="no_failure_signal",
        normalized_tags=["no_failure_signal"],
        confidence_score=1.0,
    )

    report = build_judge_report(summary, peer_comparison=peer, failure_tagging=failure)

    assert report.decision == "promote"
    assert report.actionable is True


def test_build_judge_report_holds_low_confidence_summary():
    summary = ValidationSummary(
        validation_task_id="val_123",
        status="hold",
        confidence_score=0.4,
        validation_tags=["insufficient_evidence"],
        reason_codes=["insufficient_evidence"],
        checks=[],
        failed_checks=[],
        review_required=True,
    )

    report = build_judge_report(summary)

    assert report.decision == "hold"
    assert "collect_more_evidence" in report.next_actions


def test_build_judge_report_holds_when_candidate_is_missing():
    summary = ValidationSummary(
        validation_task_id="val_123",
        status="hold",
        confidence_score=0.95,
        validation_tags=["candidate_missing"],
        reason_codes=["candidate_missing"],
        checks=[],
        failed_checks=[],
    )

    report = build_judge_report(summary)

    assert report.decision == "hold"
    assert "reject_candidate" not in report.next_actions


def test_build_judge_report_switches_to_peer_when_dominated():
    summary = ValidationSummary(
        validation_task_id="val_123",
        status="hold",
        confidence_score=0.92,
        validation_tags=["needs_more_optimization"],
        reason_codes=["needs_more_optimization"],
        checks=[],
        failed_checks=[],
    )
    peer = PeerComparisonResult(
        compared_market_tag="bull",
        comparable=True,
        compared_count=2,
        ranked_peers=[{"manager_id": "peer_a"}],
        dominant_peer="peer_a",
        peer_dominated=True,
        candidate_outperformed_peers=False,
        reason_codes=["peer_dominated"],
        summary="dominant peer detected: peer_a",
    )

    report = build_judge_report(summary, peer_comparison=peer)

    assert report.decision == "switch_to_peer"
    assert "peer_dominated" in report.reason_codes


def test_build_judge_report_rejects_before_switching_to_peer_when_ab_failed():
    summary = ValidationSummary(
        validation_task_id="val_123",
        status="failed",
        confidence_score=0.92,
        validation_tags=["ab_failed"],
        reason_codes=["ab_failed"],
        checks=[],
        failed_checks=[],
    )
    peer = PeerComparisonResult(
        compared_market_tag="bull",
        comparable=True,
        compared_count=2,
        ranked_peers=[{"manager_id": "peer_a"}],
        dominant_peer="peer_a",
        peer_dominated=True,
        candidate_outperformed_peers=False,
        reason_codes=["peer_dominated"],
        summary="dominant peer detected: peer_a",
    )

    report = build_judge_report(summary, peer_comparison=peer)

    assert report.decision == "reject"


def test_build_judge_report_rejects_before_switching_to_peer_when_governance_blocks():
    summary = ValidationSummary(
        validation_task_id="val_123",
        status="failed",
        confidence_score=0.92,
        validation_tags=["governance_blocked"],
        reason_codes=["governance_blocked"],
        checks=[],
        failed_checks=[],
    )
    peer = PeerComparisonResult(
        compared_market_tag="bull",
        comparable=True,
        compared_count=2,
        ranked_peers=[{"manager_id": "peer_a"}],
        dominant_peer="peer_a",
        peer_dominated=True,
        candidate_outperformed_peers=False,
        reason_codes=["peer_dominated"],
        summary="dominant peer detected: peer_a",
    )

    report = build_judge_report(summary, peer_comparison=peer)

    assert report.decision == "reject"


def test_build_judge_report_rejects_when_governance_blocks():
    summary = ValidationSummary(
        validation_task_id="val_123",
        status="failed",
        confidence_score=0.95,
        validation_tags=["governance_blocked"],
        reason_codes=["governance_blocked"],
        checks=[],
        failed_checks=[],
    )

    report = build_judge_report(summary)

    assert report.decision == "reject"
    assert "reject_candidate" in report.next_actions


def test_build_judge_report_rejects_governance_block_even_when_validation_is_hold():
    summary = ValidationSummary(
        validation_task_id="val_123",
        status="hold",
        confidence_score=0.95,
        validation_tags=["governance_blocked"],
        reason_codes=["governance_blocked"],
        checks=[],
        failed_checks=[],
    )

    report = build_judge_report(summary)

    assert report.decision == "reject"


def test_build_judge_report_rejects_when_governance_blocks_even_if_validation_is_hold():
    summary = ValidationSummary(
        validation_task_id="val_123",
        status="hold",
        confidence_score=0.95,
        validation_tags=["governance_blocked"],
        reason_codes=["governance_blocked"],
        checks=[],
        failed_checks=[],
    )

    report = build_judge_report(summary)

    assert report.decision == "reject"


def test_build_judge_report_continues_optimization_for_soft_failures():
    summary = ValidationSummary(
        validation_task_id="val_123",
        status="hold",
        confidence_score=0.9,
        validation_tags=["needs_more_optimization"],
        reason_codes=["needs_more_optimization"],
        checks=[],
        failed_checks=[],
    )
    failure = TaggingResult(
        tag_family="failure",
        primary_tag="loss",
        normalized_tags=["loss", "benchmark_miss"],
        confidence_score=0.95,
    )

    report = build_judge_report(summary, failure_tagging=failure)

    assert report.decision == "continue_optimize"
    assert "trigger_next_optimization_round" in report.next_actions


def test_build_judge_report_respects_shadow_mode_from_summary():
    summary = ValidationSummary(
        validation_task_id="val_123",
        status="passed",
        shadow_mode=True,
        confidence_score=0.95,
        validation_tags=["validation_passed"],
        reason_codes=[],
        checks=[],
        failed_checks=[],
    )

    report = build_judge_report(summary)

    assert report.decision == "promote"
    assert report.shadow_mode is True
    assert report.actionable is False
