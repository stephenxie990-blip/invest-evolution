import json
from types import SimpleNamespace

import invest_evolution.application.training.persistence as persistence_module
from invest_evolution.application.training.persistence import (
    ArtifactTooLargeError,
    MAX_CYCLE_RESULT_BYTES,
    TrainingPersistenceService,
    build_cycle_result_persistence_payload,
    validation_report_artifacts,
    write_json_boundary,
    write_runtime_freeze_boundary,
    write_validation_report_artifacts,
)


def test_build_cycle_result_persistence_payload_summarizes_scoring_and_self_assessment():
    controller = SimpleNamespace(
        last_allocation_plan={"active_manager_ids": ["momentum"]},
        assessment_history=[
            SimpleNamespace(
                cycle_id=4,
                regime="bull",
                plan_source="allocator",
                sharpe_ratio=1.2,
                max_drawdown=-0.08,
                excess_return=0.04,
                benchmark_passed=True,
            )
        ],
    )
    result = SimpleNamespace(
        cycle_id=4,
        cutoff_date="20240204",
        selected_stocks=["sh.600519"],
        initial_capital=100000,
        final_value=102000,
        return_pct=2.0,
        is_profit=True,
        trade_history=[],
        params={"position_size": 0.1},
        analysis="ok",
        data_mode="offline",
        requested_data_mode="offline",
        effective_data_mode="offline",
        llm_mode="dry_run",
        degraded=False,
        degrade_reason="",
        selection_mode="manager_portfolio",
        agent_used=False,
        llm_used=False,
        benchmark_passed=True,
        strategy_scores={"overall_score": 0.9},
        review_applied=False,
        config_snapshot_path="snapshots/cycle_4.yaml",
        optimization_events=[
            {
                "runtime_config_mutation_payload": {
                    "scoring_adjustments": {
                        "entry": {"signal_strength": 0.6},
                        "risk": {"stop_loss": 0.2},
                    }
                },
                "applied_change": {"scoring": {"legacy": {"ignored_when_typed_payload_present": 1}}},
            }
        ],
        audit_tags={"shadow_mode": True},
        governance_decision={"allocation_plan": {"active_manager_ids": ["momentum"]}},
        execution_defaults={"default_manager_id": "momentum"},
        execution_snapshot={
            "manager_config_ref": "configs/active.yaml",
            "runtime_overrides": {"position_size": 0.08},
        },
        run_context={
            "basis_stage": "post_cycle_result",
            "subject_type": "manager_portfolio",
            "runtime_overrides": {"position_size": 0.08},
            "review_basis_window": {"mode": "rolling", "size": 2},
            "fitness_source_cycles": [2, 3],
        },
        promotion_record={},
        lineage_record={},
        manager_results=[{"manager_id": "momentum"}],
        portfolio_plan={"active_manager_ids": ["momentum"]},
        portfolio_attribution={},
        manager_review_report={},
        allocation_review_report={},
        dominant_manager_id="momentum",
        compatibility_fields={"derived": True},
        review_decision={},
        causal_diagnosis={},
        similarity_summary={},
        similar_results=[],
        realism_metrics={},
        stage_snapshots={},
        validation_report={"validation_task_id": "val_4"},
        validation_summary={},
        peer_comparison_report={},
        judge_report={},
        research_feedback={},
        research_artifacts={},
        ab_comparison={},
        experiment_spec={},
    )

    payload = build_cycle_result_persistence_payload(controller, result)

    assert payload["scoring_mutation_count"] == 1
    assert payload["scoring_changed_keys"] == ["entry.signal_strength", "risk.stop_loss"]
    assert payload["self_assessment"]["regime"] == "bull"
    assert payload["self_assessment"]["overall_score"] == 0.9
    assert payload["allocation_plan"]["active_manager_ids"] == ["momentum"]
    assert payload["validation_report"]["validation_task_id"] == "val_4"
    assert payload["peer_comparison_report"] == {
        "compared_market_tag": "",
        "comparable": False,
        "compared_count": 0,
        "dominant_peer": "",
        "peer_dominated": False,
        "candidate_outperformed_peers": False,
        "reason_codes": [],
        "ranked_peers": [],
    }
    assert payload["execution_snapshot"]["runtime_overrides"] == {"position_size": 0.08}
    assert payload["execution_snapshot"]["manager_results"]["count"] == 0
    assert payload["run_context"]["basis_stage"] == "post_cycle_result"
    assert payload["manager_id"] == "momentum"
    assert payload["manager_config_ref"] == "configs/active.yaml"
    assert payload["active_runtime_config_ref"] == "configs/active.yaml"
    assert payload["run_context"]["runtime_overrides"] == {"position_size": 0.08}
    assert payload["run_context"]["review_basis_window"] == {"mode": "rolling", "size": 2}
    assert payload["run_context"]["fitness_source_cycles"] == [2, 3]
    assert payload["artifacts"]["validation_report_path"].endswith("cycle_4_validation.json")


def test_build_cycle_result_persistence_payload_bounds_validation_summary_raw_evidence():
    controller = SimpleNamespace(
        last_allocation_plan={},
        assessment_history=[],
    )
    result = SimpleNamespace(
        cycle_id=8,
        cutoff_date="20240208",
        selected_stocks=[],
        initial_capital=100000,
        final_value=100100,
        return_pct=0.1,
        is_profit=True,
        trade_history=[],
        params={},
        analysis="",
        data_mode="offline",
        requested_data_mode="offline",
        effective_data_mode="offline",
        llm_mode="dry_run",
        degraded=False,
        degrade_reason="",
        selection_mode="manager_portfolio",
        agent_used=False,
        llm_used=False,
        benchmark_passed=True,
        strategy_scores={"overall_score": 0.8},
        review_applied=False,
        config_snapshot_path="",
        optimization_events=[],
        audit_tags={},
        governance_decision={},
        execution_defaults={},
        execution_snapshot={"contract_stage_snapshots": {"validation": {"stage": "validation"}}},
        run_context={
            "basis_stage": "post_cycle_result",
            "contract_stage_snapshots": {"validation": {"stage": "validation"}},
        },
        promotion_record={},
        lineage_record={},
        manager_results=[],
        portfolio_plan={},
        portfolio_attribution={},
        manager_review_report={},
        allocation_review_report={},
        dominant_manager_id="",
        compatibility_fields={},
        review_decision={"reasoning": "review"},
        causal_diagnosis={},
        similarity_summary={},
        similar_results=[],
        realism_metrics={},
        stage_snapshots={
            "simulation": {
                "stage": "simulation",
                "cycle_id": 8,
                "cutoff_date": "20240208",
                "regime": "bear",
                "selection_mode": "manager_portfolio",
                "selected_stocks": ["sh.600519"],
                "return_pct": 0.1,
                "benchmark_passed": True,
                "benchmark_strict_passed": False,
            },
            "review": {
                "stage": "review",
                "cycle_id": 8,
                "analysis": "review summary",
                "review_applied": True,
                "similarity_summary": {
                    "match_count": 3,
                    "similarity_band": "high",
                    "summary": "matched",
                    "matched_cycle_ids": [3, 5, 8],
                },
            },
            "validation": {
                "stage": "validation",
                "cycle_id": 8,
                "validation_task_id": "val_8",
                "shadow_mode": True,
                "validation_summary": {
                    "validation_task_id": "val_8",
                    "status": "hold",
                    "shadow_mode": True,
                    "reason_codes": ["candidate_missing"],
                    "raw_evidence": {
                        "cycle_result": {"huge": "x" * 10000},
                    },
                },
                "market_tagging": {
                    "contract_version": "tagging.v1",
                    "tag_family": "market",
                    "primary_tag": "bear",
                    "normalized_tags": ["bear"],
                    "confidence_score": 0.9,
                    "review_required": False,
                    "reason_codes": ["market_tag_explicit"],
                },
                "validation_tagging": {
                    "contract_version": "tagging.v1",
                    "tag_family": "validation",
                    "primary_tag": "candidate_missing",
                    "normalized_tags": ["candidate_missing"],
                    "confidence_score": 0.7,
                    "review_required": False,
                    "reason_codes": ["candidate_missing"],
                },
                "judge_report": {
                    "decision": "hold",
                    "summary": "need more evidence",
                    "next_actions": ["collect more data"],
                },
            },
            "outcome": {
                "stage": "outcome",
                "cycle_id": 8,
                "promotion_record": {
                    "status": "candidate_generated",
                    "gate_status": "awaiting_gate",
                    "applied_to_active": False,
                },
                "lineage_record": {
                    "lineage_status": "candidate_pending",
                    "parent_cycle_id": 7,
                },
                "realism_metrics": {
                    "trade_record_count": 2,
                    "avg_trade_amount": 1234.5,
                    "source_mix": {"signal_engine": 1.0},
                },
            },
        },
        validation_report={
            "validation_task_id": "val_8",
            "shadow_mode": True,
            "market_tagging": {
                "contract_version": "tagging.v1",
                "tag_family": "market",
                "primary_tag": "bear",
                "normalized_tags": ["bear"],
                "confidence_score": 0.9,
                "review_required": False,
                "reason_codes": ["market_tag_explicit"],
            },
            "failure_tagging": {
                "contract_version": "tagging.v1",
                "tag_family": "failure",
                "primary_tag": "loss",
                "normalized_tags": ["loss", "benchmark_miss"],
                "confidence_score": 0.85,
                "review_required": False,
                "reason_codes": ["failure_signature_classified"],
                "raw_evidence": {"extra": {"primary_driver": "insufficient_history"}},
            },
            "validation_tagging": {
                "contract_version": "tagging.v1",
                "tag_family": "validation",
                "primary_tag": "candidate_missing",
                "normalized_tags": ["candidate_missing"],
                "confidence_score": 0.7,
                "review_required": False,
                "reason_codes": ["candidate_missing"],
            },
            "summary": {
                "validation_task_id": "val_8",
                "status": "hold",
                "shadow_mode": True,
                "checks": [{"name": "candidate.present", "passed": False, "reason_code": "candidate_missing"}],
                "failed_checks": [{"name": "candidate.present", "passed": False, "reason_code": "candidate_missing"}],
                "raw_evidence": {
                    "run_context": {"basis_stage": "post_cycle_result"},
                    "review_result": {"reasoning": "review"},
                    "cycle_result": {
                        "return_pct": 0.1,
                        "benchmark_passed": True,
                        "strategy_scores": {"overall_score": 0.8},
                        "research_feedback": {"sample_count": 3},
                        "ab_comparison": {},
                        "huge": "y" * 10000,
                    },
                },
            },
        },
        validation_summary={
            "validation_task_id": "val_8",
            "status": "hold",
            "shadow_mode": True,
            "checks": [{"name": "candidate.present", "passed": False, "reason_code": "candidate_missing"}],
            "failed_checks": [{"name": "candidate.present", "passed": False, "reason_code": "candidate_missing"}],
            "raw_evidence": {
                "run_context": {"basis_stage": "post_cycle_result"},
                "review_result": {"reasoning": "review"},
                "cycle_result": {
                    "return_pct": 0.1,
                    "benchmark_passed": True,
                    "strategy_scores": {"overall_score": 0.8},
                    "research_feedback": {"sample_count": 3},
                    "ab_comparison": {},
                    "huge": "y" * 10000,
                },
            },
        },
        peer_comparison_report={},
        judge_report={},
        research_feedback={},
        research_artifacts={},
        ab_comparison={},
        experiment_spec={},
    )

    payload = build_cycle_result_persistence_payload(controller, result)

    assert payload["validation_summary"]["raw_evidence"]["cycle_result"]["return_pct"] == 0.1
    assert "huge" not in payload["validation_summary"]["raw_evidence"]["cycle_result"]
    assert payload["validation_summary"]["check_count"] == 1
    assert payload["validation_report"]["summary"]["failed_check_count"] == 1
    assert "raw_evidence" not in payload["validation_report"]["summary"]
    assert payload["validation_report"]["market_tagging"]["primary_tag"] == "bear"
    assert payload["validation_report"]["failure_tagging"]["primary_tag"] == "loss"
    assert payload["validation_report"]["failure_tagging"]["tag_family"] == "failure"
    assert "primary_driver" not in payload["validation_report"]["failure_tagging"]
    assert payload["validation_report"]["validation_tagging"]["normalized_tags"] == ["candidate_missing"]
    assert payload["review_decision"]["reasoning"] == "review"
    assert "similarity_summary" not in payload["review_decision"]
    assert payload["stage_snapshots"]["simulation"]["selected_stocks"] == ["sh.600519"]
    assert payload["stage_snapshots"]["review"]["similarity_summary"]["matched_cycle_ids"] == [3, 5, 8]
    assert payload["stage_snapshots"]["validation"]["validation_summary"]["status"] == "hold"
    assert "raw_evidence" not in payload["stage_snapshots"]["validation"]["validation_summary"]
    assert payload["stage_snapshots"]["validation"]["market_tagging"]["primary_tag"] == "bear"
    assert payload["stage_snapshots"]["validation"]["market_tagging"]["tag_family"] == "market"
    assert payload["stage_snapshots"]["validation"]["judge_report"]["decision"] == "hold"
    assert payload["stage_snapshots"]["outcome"]["realism_metrics"]["avg_trade_amount"] == 1234.5
    assert payload["stage_snapshots"]["outcome"]["promotion_record"]["gate_status"] == "awaiting_gate"
    assert payload["execution_snapshot"]["contract_stage_refs"]["stage_names"] == ["validation"]
    assert payload["execution_snapshot"]["contract_stage_snapshots"]["validation"]["stage"] == "validation"
    assert (
        payload["execution_snapshot"]["contract_stage_snapshots"]["validation"]["validation_summary"]["check_count"]
        == 0
    )
    assert payload["run_context"]["contract_stage_refs"]["stage_names"] == ["validation"]
    assert payload["run_context"]["contract_stage_snapshots"]["validation"]["judge_decision"] == ""
    assert "huge" not in json.dumps(payload["stage_snapshots"], ensure_ascii=False)


def test_write_json_boundary_rejects_oversized_artifact(tmp_path):
    path = tmp_path / "big.json"

    try:
        write_json_boundary(path, {"payload": "x" * 512}, max_bytes=128)
    except ArtifactTooLargeError as exc:
        assert exc.path == path
        assert exc.actual_bytes > exc.max_bytes
    else:
        raise AssertionError("expected ArtifactTooLargeError")


def test_persistence_boundary_writes_freeze_and_validation_artifacts(tmp_path):
    freeze_path = write_runtime_freeze_boundary(
        output_dir=tmp_path,
        report={"freeze_gate_evaluation": {"passed": True}},
    )
    assert freeze_path.name == "runtime_frozen.json"
    assert json.loads(freeze_path.read_text(encoding="utf-8"))["freeze_gate_evaluation"]["passed"] is True

    payloads = validation_report_artifacts(
        SimpleNamespace(
            validation_report={"validation_task_id": "val_9"},
            peer_comparison_report={"compared_market_tag": "bull"},
            judge_report={"decision": "hold"},
        )
    )
    write_validation_report_artifacts(
        output_dir=tmp_path,
        cycle_id=9,
        report_payloads=payloads,
    )

    assert json.loads((tmp_path / "validation" / "cycle_9_validation.json").read_text(encoding="utf-8"))[
        "validation_task_id"
    ] == "val_9"
    assert json.loads(
        (tmp_path / "validation" / "cycle_9_peer_comparison.json").read_text(encoding="utf-8")
    )["compared_market_tag"] == "bull"
    assert json.loads((tmp_path / "validation" / "cycle_9_judge.json").read_text(encoding="utf-8"))[
        "decision"
    ] == "hold"


def test_save_cycle_result_warning_event_contains_severity_fields(tmp_path, monkeypatch):
    events: list[tuple[str, dict[str, object]]] = []
    controller = SimpleNamespace(
        output_dir=str(tmp_path),
        _emit_runtime_event=lambda event_type, payload: events.append((event_type, dict(payload))),
    )
    result = SimpleNamespace(cycle_id=9, trade_history=[], execution_defaults={})

    monkeypatch.setattr(persistence_module, "write_trade_history_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr(persistence_module, "build_cycle_result_persistence_payload", lambda *args, **kwargs: {})
    monkeypatch.setattr(persistence_module, "write_json_boundary", lambda *args, **kwargs: None)

    service = TrainingPersistenceService()
    service.save_validation_reports = lambda controller, result: None

    def _raise_refresh(controller):
        raise RuntimeError("refresh failed")

    service.refresh_leaderboards = _raise_refresh

    service.save_cycle_result(controller, result)

    assert events
    assert events[0][0] == "warning"
    assert events[0][1]["severity"] == "warning"
    assert events[0][1]["risk_level"] == "medium"


def test_build_cycle_result_persistence_payload_normalizes_relative_artifact_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    controller = SimpleNamespace(
        output_dir="runtime/outputs/training",
        last_allocation_plan={},
        assessment_history=[],
    )
    result = SimpleNamespace(
        cycle_id=7,
        cutoff_date="20240207",
        selected_stocks=[],
        initial_capital=100000,
        final_value=100000,
        return_pct=0.0,
        is_profit=False,
        trade_history=[],
        params={},
        analysis="",
        data_mode="offline",
        requested_data_mode="offline",
        effective_data_mode="offline",
        llm_mode="dry_run",
        degraded=False,
        degrade_reason="",
        selection_mode="manager_portfolio",
        agent_used=False,
        llm_used=False,
        benchmark_passed=False,
        strategy_scores={},
        review_applied=False,
        config_snapshot_path="",
        optimization_events=[],
        audit_tags={},
        governance_decision={},
        execution_defaults={},
        execution_snapshot={},
        run_context={},
        promotion_record={},
        lineage_record={},
        manager_results=[],
        portfolio_plan={},
        portfolio_attribution={},
        manager_review_report={},
        allocation_review_report={},
        dominant_manager_id="",
        compatibility_fields={},
        review_decision={},
        causal_diagnosis={},
        similarity_summary={},
        similar_results=[],
        realism_metrics={},
        stage_snapshots={},
        validation_report={},
        validation_summary={},
        peer_comparison_report={},
        judge_report={},
        research_feedback={},
        research_artifacts={},
        ab_comparison={},
        experiment_spec={},
    )

    payload = build_cycle_result_persistence_payload(controller, result)

    expected_root = (tmp_path / "runtime" / "outputs" / "training").resolve()
    assert payload["artifacts"]["validation_report_path"] == str(
        expected_root / "validation" / "cycle_7_validation.json"
    )
    assert payload["artifacts"]["trade_history_path"] == str(
        expected_root / "details" / "cycle_7_trades.json"
    )


def test_build_cycle_result_persistence_payload_keeps_large_nested_inputs_bounded():
    controller = SimpleNamespace(
        output_dir="outputs/formal_shadow",
        last_allocation_plan={},
        assessment_history=[],
    )
    huge_blob = "x" * 20000
    contract_snapshots = {
        "simulation": {
            "stage": "simulation",
            "cycle_id": 9,
            "selected_stocks": [f"stk{i}" for i in range(100)],
            "analysis": huge_blob,
        },
        "validation": {
            "stage": "validation",
            "cycle_id": 9,
            "validation_summary": {
                "validation_task_id": "val_9",
                "status": "hold",
                "shadow_mode": True,
                "reason_codes": ["candidate_missing"] * 20,
                "checks": [
                    {
                        "name": f"check_{idx}",
                        "passed": False,
                        "reason_code": "candidate_missing",
                        "actual": huge_blob,
                        "threshold": idx,
                    }
                    for idx in range(20)
                ],
                "raw_evidence": {
                    "run_context": {
                        "basis_stage": "post_cycle_result",
                        "contract_stage_snapshots": {
                            f"stage_{idx}": {"stage": "validation", "details": huge_blob}
                            for idx in range(10)
                        },
                    },
                    "review_result": {"reasoning": huge_blob, "similarity_summary": {"matched_cycle_ids": list(range(50))}},
                    "cycle_result": {
                        "return_pct": 0.3,
                        "benchmark_passed": False,
                        "strategy_scores": {"overall_score": 0.2},
                        "research_feedback": {"sample_count": 6, "recommendation": {"summary": huge_blob}},
                        "ab_comparison": {"comparison": {"winner": "active", "summary": huge_blob}},
                        "huge": huge_blob,
                    },
                },
            },
            "judge_report": {"decision": "hold", "summary": huge_blob, "next_actions": [huge_blob] * 20},
        },
    }
    result = SimpleNamespace(
        cycle_id=9,
        cutoff_date="20240209",
        selected_stocks=["sh.600519"],
        initial_capital=100000,
        final_value=99500,
        return_pct=-0.5,
        is_profit=False,
        trade_history=[],
        params={},
        analysis="bounded",
        data_mode="offline",
        requested_data_mode="offline",
        effective_data_mode="offline",
        llm_mode="dry_run",
        degraded=False,
        degrade_reason="",
        selection_mode="manager_portfolio",
        agent_used=False,
        llm_used=False,
        benchmark_passed=False,
        strategy_scores={"overall_score": 0.2},
        review_applied=True,
        config_snapshot_path="",
        optimization_events=[],
        audit_tags={},
        governance_decision={
            "dominant_manager_id": "momentum",
            "regime": "bear",
            "metadata": {
                "active_runtime_config_ref": "configs/active.yaml",
                "subject_type": "manager_portfolio",
                "large_blob": huge_blob,
            },
            "evidence": {
                "research_feedback": {"summary": huge_blob},
                "ab_comparison": {"summary": huge_blob},
            },
            "guardrail_checks": [
                {"name": "allocation", "passed": True, "summary": huge_blob, "reason_codes": ["ok"]},
            ],
            "allocation_plan": {
                "active_manager_ids": ["momentum", "value_quality"],
                "budget_weights": {"momentum": 0.6, "value_quality": 0.4},
                "metadata": {"dominant_manager_config": "configs/active.yaml"},
            },
        },
        execution_defaults={},
        execution_snapshot={
            "basis_stage": "post_cycle_result",
            "runtime_overrides": {"position_size": 0.08},
            "contract_stage_snapshots": contract_snapshots,
            "manager_results": [
                {"manager_id": f"mgr_{idx}", "score": idx / 10.0, "notes": huge_blob}
                for idx in range(12)
            ],
            "portfolio_plan": {
                "active_manager_ids": ["momentum", "value_quality"],
                "budget_weights": {"momentum": 0.6, "value_quality": 0.4},
            },
        },
        run_context={
            "basis_stage": "post_cycle_result",
            "active_runtime_config_ref": "configs/active.yaml",
            "candidate_runtime_config_ref": "configs/candidate.yaml",
            "promotion_decision": {
                "status": "candidate_generated",
                "applied_to_active": False,
                "gate_status": "awaiting_gate",
                "notes": huge_blob,
            },
            "ab_comparison": {"comparison": {"winner": "candidate", "summary": huge_blob}},
            "contract_stage_snapshots": contract_snapshots,
        },
        promotion_record={"status": "candidate_generated", "gate_status": "awaiting_gate", "notes": huge_blob},
        lineage_record={"lineage_status": "candidate_pending", "notes": huge_blob},
        manager_results=[{"manager_id": f"mgr_{idx}", "score": idx / 10.0, "notes": huge_blob} for idx in range(12)],
        portfolio_plan={"active_manager_ids": ["momentum", "value_quality"], "budget_weights": {"momentum": 0.6}},
        portfolio_attribution={},
        manager_review_report={},
        allocation_review_report={},
        dominant_manager_id="momentum",
        compatibility_fields={},
        review_decision={"reasoning": huge_blob, "similarity_summary": {"matched_cycle_ids": list(range(50))}},
        causal_diagnosis={},
        similarity_summary={},
        similar_results=[],
        realism_metrics={},
        stage_snapshots=contract_snapshots,
        validation_report={
            "validation_task_id": "val_9",
            "shadow_mode": True,
            "summary": contract_snapshots["validation"]["validation_summary"],
            "market_tagging": {"primary_tag": "bear", "confidence_score": 0.9, "reason_codes": ["explicit"]},
        },
        validation_summary=contract_snapshots["validation"]["validation_summary"],
        peer_comparison_report={},
        judge_report={"decision": "hold", "summary": huge_blob, "next_actions": [huge_blob] * 20},
        research_feedback={},
        research_artifacts={},
        ab_comparison={},
        experiment_spec={},
    )

    payload = build_cycle_result_persistence_payload(controller, result)
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    assert len(encoded) < MAX_CYCLE_RESULT_BYTES
    assert len(encoded) < 160 * 1024
    assert "contract_stage_snapshots" not in json.dumps(payload["execution_snapshot"], ensure_ascii=False)
    assert "contract_stage_snapshots" not in json.dumps(payload["run_context"], ensure_ascii=False)
    assert "large_blob" not in json.dumps(payload["governance_decision"], ensure_ascii=False)
    assert "raw_evidence" not in json.dumps(payload["validation_report"], ensure_ascii=False)
    assert payload["execution_snapshot"]["contract_stage_refs"]["count"] == 2
    assert payload["manager_results"]["count"] == 12
