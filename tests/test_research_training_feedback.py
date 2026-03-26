from app.train import SelfLearningController, TrainingResult
from app.training.reporting import build_freeze_report
from app.training.runtime_hooks import SelfAssessmentSnapshot


def _make_feedback(*, bias: str, sample_count: int = 8, t20_hit: float = 0.3, t20_invalid: float = 0.4, t20_interval: float = 0.3, t60_hit: float = 0.42, t60_invalid: float = 0.36, t60_interval: float = 0.35, brier: float = 0.31):
    return {
        "sample_count": sample_count,
        "recommendation": {
            "bias": bias,
            "reason_codes": ["t20_hit_rate_low", "t60_invalidation_high"],
            "summary": f"基于 ask 侧归因样本给训练侧的建议：{bias}",
        },
        "horizons": {
            "T+20": {
                "hit_rate": t20_hit,
                "invalidation_rate": t20_invalid,
                "interval_hit_rate": t20_interval,
            },
            "T+60": {
                "hit_rate": t60_hit,
                "invalidation_rate": t60_invalid,
                "interval_hit_rate": t60_interval,
            },
        },
        "brier_like_direction_score": brier,
    }


def _seed_cycle_history(controller: SelfLearningController, count: int = 10):
    controller.cycle_history = [
        TrainingResult(
            cycle_id=index + 1,
            cutoff_date=f"202401{index + 1:02d}",
            selected_stocks=["sh.600000"],
            initial_capital=100000,
            final_value=101000,
            return_pct=1.0,
            is_profit=True,
            trade_history=[],
            params={},
            promotion_record={
                "attempted": index % 2 == 0,
                "gate_status": "awaiting_gate" if index % 3 == 0 else "not_applicable",
            },
            lineage_record={
                "lineage_status": "candidate_pending" if index % 3 == 0 else "active_only",
                "active_config_ref": "active.yaml",
                "candidate_config_ref": "candidate.yaml" if index % 3 == 0 else "",
            },
        )
        for index in range(count)
    ]


def test_feedback_optimization_plan_uses_multi_horizon_failures(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )

    base_position = controller.current_params["position_size"]
    base_cash = controller.current_params["cash_reserve"]
    feedback = _make_feedback(bias="tighten_risk")

    plan = controller._build_feedback_optimization_plan(feedback, cycle_id=5)  # pylint: disable=protected-access

    assert plan["trigger"] == "research_feedback"
    assert set(plan["failed_horizons"]) == {"T+20", "T+60"}
    assert plan["param_adjustments"]["position_size"] < base_position
    assert plan["param_adjustments"]["cash_reserve"] > base_cash
    assert "T+20.hit_rate" in plan["failed_check_names"]


def test_feedback_optimization_plan_tightens_faster_when_benchmark_is_weak(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )
    controller.assessment_history = [
        SelfAssessmentSnapshot(
            cycle_id=index,
            cutoff_date=f"202401{index:02d}",
            regime="bull",
            plan_source="meeting",
            return_pct=-0.5 if index % 2 else 0.2,
            is_profit=index % 2 == 0,
            sharpe_ratio=0.4,
            max_drawdown=12.0,
            excess_return=-0.6,
            benchmark_passed=index == 5,
        )
        for index in range(1, 6)
    ]
    base_position = controller.current_params["position_size"]
    base_cash = controller.current_params["cash_reserve"]
    base_hold_days = controller.current_params["max_hold_days"]
    base_signal_threshold = controller.current_params["signal_threshold"]

    plan = controller._build_feedback_optimization_plan(
        _make_feedback(bias="tighten_risk", brier=0.37),
        cycle_id=6,
    )

    assert plan["benchmark_context"]["current_pass_rate"] < plan["benchmark_context"]["required_pass_rate"]
    assert plan["severity"] > 1.8
    assert plan["param_adjustments"]["position_size"] < base_position
    assert plan["param_adjustments"]["cash_reserve"] > base_cash + 0.04
    assert plan["param_adjustments"]["max_hold_days"] < base_hold_days
    assert plan["param_adjustments"]["signal_threshold"] > base_signal_threshold


def test_feedback_optimization_plan_respects_cooldown(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )
    controller.last_feedback_optimization_cycle_id = 4

    plan = controller._build_feedback_optimization_plan(_make_feedback(bias="tighten_risk"), cycle_id=5)  # pylint: disable=protected-access

    assert plan == {}


def test_should_freeze_is_blocked_by_bad_research_feedback(monkeypatch, tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )
    _seed_cycle_history(controller)
    controller.last_research_feedback = _make_feedback(bias="tighten_risk")

    monkeypatch.setattr(
        controller,
        "_rolling_self_assessment",
        lambda window=None: {
            "window": 10,
            "profit_count": 8,
            "win_rate": 0.8,
            "avg_return": 1.6,
            "avg_sharpe": 1.1,
            "avg_max_drawdown": 8.0,
            "avg_excess_return": 0.9,
            "benchmark_pass_rate": 0.8,
        },
    )

    assert controller.should_freeze() is False
    assert controller.last_freeze_gate_evaluation["research_feedback_gate"]["passed"] is False


def test_should_freeze_allows_good_research_feedback(monkeypatch, tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )
    _seed_cycle_history(controller)
    controller.last_research_feedback = _make_feedback(
        bias="maintain",
        sample_count=12,
        t20_hit=0.62,
        t20_invalid=0.12,
        t20_interval=0.58,
        t60_hit=0.56,
        t60_invalid=0.18,
        t60_interval=0.49,
        brier=0.12,
    )

    for item in controller.cycle_history:
        item.promotion_record = {"attempted": False, "gate_status": "not_applicable"}
        item.lineage_record = {
            "lineage_status": "active_only",
            "deployment_stage": "active",
            "active_config_ref": "active.yaml",
            "candidate_config_ref": "",
        }

    monkeypatch.setattr(
        controller,
        "_rolling_self_assessment",
        lambda window=None: {
            "window": 10,
            "profit_count": 8,
            "win_rate": 0.8,
            "avg_return": 1.6,
            "avg_sharpe": 1.1,
            "avg_max_drawdown": 8.0,
            "avg_excess_return": 0.9,
            "benchmark_pass_rate": 0.8,
        },
    )

    assert controller.should_freeze() is True
    assert controller.last_freeze_gate_evaluation["research_feedback_gate"]["passed"] is True


def test_generate_report_includes_freeze_gate_evaluation(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )
    _seed_cycle_history(controller)
    controller.total_cycle_attempts = 10
    controller.last_research_feedback = _make_feedback(bias="tighten_risk")

    controller.last_freeze_gate_evaluation = controller._evaluate_freeze_gate(  # pylint: disable=protected-access
        {
            "window": 10,
            "profit_count": 8,
            "win_rate": 0.8,
            "avg_return": 1.6,
            "avg_sharpe": 1.1,
            "avg_max_drawdown": 8.0,
            "avg_excess_return": 0.9,
            "benchmark_pass_rate": 0.8,
        }
    )

    report = controller._generate_report()  # pylint: disable=protected-access
    assert "freeze_gate_evaluation" in report
    assert "proposal_gate_summary" in report
    assert report["freeze_applied"] is False
    assert report["audit_semantics"]["metric_terms"]["is_frozen"]["legacy_alias_of"] == "freeze_applied"
    assert report["freeze_gate_evaluation"]["research_feedback_gate"]["passed"] is False
    assert report["governance_metrics"]["promotion_attempt_count"] > 0
    assert report["freeze_gate_evaluation"]["governance_metrics"]["candidate_pending_count"] > 0
    assert report["proposal_gate_summary"]["cycles_with_gate"] == 0


def test_build_freeze_report_injects_default_freeze_gate_thresholds(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )
    _seed_cycle_history(controller)

    report = build_freeze_report(
        controller.cycle_history,
        controller.current_params,
        freeze_total_cycles=10,
        freeze_profit_required=7,
        freeze_gate_policy={"governance": {"max_candidate_pending_count": 0}},
        rolling={
            "window": 10,
            "profit_count": 8,
            "win_rate": 0.8,
            "avg_return": 1.6,
            "avg_sharpe": 1.1,
            "avg_max_drawdown": 8.0,
            "avg_excess_return": 0.9,
            "benchmark_pass_rate": 0.8,
        },
        research_feedback=_make_feedback(bias="maintain", sample_count=12, t20_hit=0.62, t20_invalid=0.12, t20_interval=0.58, t60_hit=0.56, t60_invalid=0.18, t60_interval=0.49, brier=0.12),
    )

    freeze_gate = report["freeze_gate"]
    assert report["freeze_applied"] is True
    assert report["audit_semantics"]["metric_terms"]["active_candidate_drift_rate"]["legacy_alias_of"] == "active_pending_candidate_divergence_rate"
    assert freeze_gate["required_avg_sharpe"] == 0.8
    assert freeze_gate["required_benchmark_pass_rate"] == 0.60
    assert freeze_gate["research_feedback"]["min_episode_count"] == 8
    assert freeze_gate["governance"]["max_candidate_pending_count"] == 0
    assert freeze_gate["governance"]["max_override_pending_count"] == 0
    assert report["proposal_gate_summary"]["cycles_with_gate"] == 0


def test_build_freeze_report_includes_proposal_gate_summary(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )
    _seed_cycle_history(controller, count=2)
    controller.cycle_history[0].proposal_bundle = {
        "proposal_bundle_id": "proposal_bundle_0001_demo",
        "proposal_count": 3,
        "proposal_ids": ["p1", "p2", "p3"],
    }
    controller.cycle_history[1].proposal_bundle = {
        "proposal_bundle_id": "proposal_bundle_0002_demo",
        "proposal_count": 0,
        "proposal_ids": [],
    }
    controller.cycle_history[0].optimization_events = [
        {
            "stage": "candidate_build",
            "evidence": {
                "proposal_gate": {
                    "proposal_summary": {
                        "requested_proposal_count": 3,
                        "approved_proposal_count": 2,
                        "blocked_proposal_count": 1,
                        "partially_blocked_proposal_count": 0,
                        "block_reason_counts": {
                            "single_step_identity_drift_exceeded": 1,
                        },
                        "top_block_reasons": ["single_step_identity_drift_exceeded"],
                    },
                    "drift_summary": {
                        "approved_params": {
                            "position_size": {
                                "candidate_drift_ratio_vs_baseline": 0.2,
                            }
                        }
                    },
                }
            },
        }
    ]

    report = build_freeze_report(
        controller.cycle_history,
        controller.current_params,
        freeze_total_cycles=2,
        freeze_profit_required=1,
        freeze_gate_policy={"governance": {"max_candidate_pending_count": 0}},
        rolling={
            "window": 2,
            "profit_count": 2,
            "win_rate": 1.0,
            "avg_return": 1.0,
            "avg_sharpe": 1.0,
            "avg_max_drawdown": 5.0,
            "avg_excess_return": 0.5,
            "benchmark_pass_rate": 0.5,
        },
        research_feedback={},
    )

    summary = report["proposal_gate_summary"]
    assert summary["cycles_with_proposal_bundle"] == 2
    assert summary["cycles_with_requested_proposals"] == 1
    assert summary["cycles_without_requested_proposals"] == 1
    assert summary["bundle_proposal_count"] == 3
    assert summary["requested_candidate_proposal_count"] == 3
    assert summary["cycles_with_gate"] == 1
    assert summary["cycles_with_candidate_generated"] == 1
    assert summary["requested_proposal_count"] == 3
    assert summary["approved_proposal_count"] == 2
    assert summary["blocked_proposal_count"] == 1
    assert summary["top_block_reasons"] == ["single_step_identity_drift_exceeded"]
    assert summary["max_candidate_drift_ratio_vs_baseline"] == 0.2
    assert summary["no_proposal_reason_counts"]["no_learning_adjustments_requested"] == 1


def test_build_freeze_report_includes_suggestion_adoption_summary(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )
    _seed_cycle_history(controller, count=2)
    controller.cycle_history[0].proposal_bundle = {
        "proposal_bundle_id": "proposal_bundle_0001_demo",
        "proposals": [
            {
                "proposal_id": "proposal_0001_001",
                "suggestion_id": "suggestion_0001_001",
                "source": "review.param_adjustment",
                "suggestion_text": "bear 下缩小仓位",
                "adoption_status": "adopted_to_candidate",
                "effect_status": "pending",
                "effect_target_metrics": ["avg_return_pct", "benchmark_pass_rate"],
                "effect_window": {"evaluation_after_cycle_id": 4},
            },
            {
                "proposal_id": "proposal_0001_002",
                "suggestion_id": "suggestion_0001_002",
                "source": "optimization.llm_analysis",
                "suggestion_text": "收紧趋势阈值",
                "adoption_status": "rejected_by_proposal_gate",
                "effect_status": "not_applicable",
                "effect_target_metrics": ["avg_return_pct"],
                "effect_window": {"evaluation_after_cycle_id": 4},
            },
        ],
    }
    controller.cycle_history[1].proposal_bundle = {
        "proposal_bundle_id": "proposal_bundle_0002_demo",
        "proposals": [
            {
                "proposal_id": "proposal_0002_001",
                "suggestion_id": "suggestion_0002_001",
                "source": "review.agent_weight_adjustment",
                "suggestion_text": "提高逆向 agent 权重",
                "adoption_status": "deferred_pending_candidate",
                "effect_status": "pending_adoption",
                "effect_target_metrics": ["avg_strategy_score"],
                "effect_window": {"evaluation_after_cycle_id": 5},
            }
        ],
    }

    report = build_freeze_report(
        controller.cycle_history,
        controller.current_params,
        freeze_total_cycles=2,
        freeze_profit_required=1,
        freeze_gate_policy={"governance": {"max_candidate_pending_count": 0}},
        rolling={
            "window": 2,
            "profit_count": 1,
            "win_rate": 0.5,
            "avg_return": 0.2,
            "avg_sharpe": 0.7,
            "avg_max_drawdown": 4.0,
            "avg_excess_return": 0.1,
            "benchmark_pass_rate": 0.5,
        },
        research_feedback={},
    )

    summary = report["suggestion_adoption_summary"]
    assert summary["schema_version"] == "training.suggestion_adoption_summary.v1"
    assert summary["suggestion_count"] == 3
    assert summary["adopted_suggestion_count"] == 1
    assert summary["deferred_suggestion_count"] == 1
    assert summary["rejected_suggestion_count"] == 1
    assert summary["pending_effect_count"] == 1
    assert summary["pending_effect_suggestions"][0]["suggestion_id"] == "suggestion_0001_001"


def test_build_freeze_report_distinguishes_blocked_and_pending_candidate_cycles(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )
    _seed_cycle_history(controller, count=2)
    controller.cycle_history[0].proposal_bundle = {
        "proposal_bundle_id": "proposal_bundle_0001_demo",
        "proposal_count": 2,
        "proposal_ids": ["p1", "p2"],
    }
    controller.cycle_history[1].proposal_bundle = {
        "proposal_bundle_id": "proposal_bundle_0002_demo",
        "proposal_count": 1,
        "proposal_ids": ["p3"],
    }
    controller.cycle_history[0].optimization_events = [
        {
            "stage": "candidate_build_skipped",
            "decision": {"skip_reason": "proposal_governance_rejected"},
            "evidence": {
                "proposal_gate": {
                    "proposal_summary": {
                        "requested_proposal_count": 2,
                        "approved_proposal_count": 0,
                        "blocked_proposal_count": 2,
                        "partially_blocked_proposal_count": 0,
                        "block_reason_counts": {"single_step_identity_drift_exceeded": 2},
                    }
                }
            },
        }
    ]
    controller.cycle_history[1].optimization_events = [
        {
            "stage": "candidate_build_skipped",
            "decision": {"pending_candidate_ref": "pending.yaml"},
            "evidence": {
                "proposal_gate": {
                    "proposal_summary": {
                        "requested_proposal_count": 1,
                        "approved_proposal_count": 1,
                        "blocked_proposal_count": 0,
                        "partially_blocked_proposal_count": 0,
                        "block_reason_counts": {},
                    }
                }
            },
        }
    ]

    report = build_freeze_report(
        controller.cycle_history,
        controller.current_params,
        freeze_total_cycles=2,
        freeze_profit_required=1,
        freeze_gate_policy={"governance": {"max_candidate_pending_count": 0}},
        rolling={
            "window": 2,
            "profit_count": 2,
            "win_rate": 1.0,
            "avg_return": 1.0,
            "avg_sharpe": 1.0,
            "avg_max_drawdown": 5.0,
            "avg_excess_return": 0.5,
            "benchmark_pass_rate": 0.5,
        },
        research_feedback={},
    )

    summary = report["proposal_gate_summary"]
    assert summary["candidate_build_skipped_cycles"] == 2
    assert summary["cycles_with_all_proposals_blocked"] == 1
    assert summary["cycles_with_pending_candidate_skip"] == 1


def test_build_freeze_report_classifies_observe_only_profitable_cycle_without_proposals(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )
    controller.cycle_history = [
        TrainingResult(
            cycle_id=1,
            cutoff_date="20240101",
            selected_stocks=["sh.600000"],
            initial_capital=100000,
            final_value=101500,
            return_pct=1.5,
            is_profit=True,
            trade_history=[],
            params={},
            benchmark_passed=True,
            proposal_bundle={
                "proposal_bundle_id": "proposal_bundle_0001_demo",
                "proposal_count": 0,
                "proposal_ids": [],
            },
            execution_snapshot={
                "is_profit": True,
                "benchmark_passed": True,
            },
            review_decision={
                "param_adjustments": {},
                "agent_weight_adjustments": {},
            },
        )
    ]

    report = build_freeze_report(
        controller.cycle_history,
        controller.current_params,
        freeze_total_cycles=1,
        freeze_profit_required=1,
        freeze_gate_policy={"governance": {"max_candidate_pending_count": 0}},
        rolling={
            "window": 1,
            "profit_count": 1,
            "win_rate": 1.0,
            "avg_return": 1.5,
            "avg_sharpe": 1.0,
            "avg_max_drawdown": 4.0,
            "avg_excess_return": 0.8,
            "benchmark_pass_rate": 1.0,
        },
        research_feedback={},
    )

    summary = report["proposal_gate_summary"]
    assert summary["cycles_without_requested_proposals"] == 1
    assert summary["no_proposal_reason_counts"] == {"observe_only_profitable_cycle": 1}
    assert summary["no_proposal_cycles"][0]["reason"] == "observe_only_profitable_cycle"


def test_build_freeze_report_includes_regime_failure_dashboard(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )
    controller.cycle_history = [
        TrainingResult(
            cycle_id=1,
            cutoff_date="20240101",
            selected_stocks=["sh.600000"],
            initial_capital=100000,
            final_value=98000,
            return_pct=-2.0,
            is_profit=False,
            trade_history=[],
            params={},
            benchmark_passed=False,
            selection_mode="meeting",
            routing_decision={"regime": "bear"},
            research_feedback={"sample_count": 6, "recommendation": {"bias": "tighten_risk"}},
            causal_diagnosis={"primary_driver": "regime_repeat_loss"},
        ),
        TrainingResult(
            cycle_id=2,
            cutoff_date="20240108",
            selected_stocks=["sh.600001"],
            initial_capital=100000,
            final_value=97500,
            return_pct=-2.5,
            is_profit=False,
            trade_history=[],
            params={},
            benchmark_passed=False,
            selection_mode="algorithm",
            routing_decision={"regime": "bear"},
            research_feedback={"sample_count": 5, "recommendation": {"bias": "tighten_risk"}},
            causal_diagnosis={"primary_driver": "benchmark_gap"},
        ),
        TrainingResult(
            cycle_id=3,
            cutoff_date="20240115",
            selected_stocks=["sh.600002"],
            initial_capital=100000,
            final_value=99500,
            return_pct=-0.5,
            is_profit=False,
            trade_history=[],
            params={},
            benchmark_passed=True,
            selection_mode="meeting",
            routing_decision={"regime": "bull"},
            research_feedback={"sample_count": 4, "recommendation": {"bias": "maintain"}},
            causal_diagnosis={"primary_driver": "benchmark_gap"},
        ),
        TrainingResult(
            cycle_id=4,
            cutoff_date="20240122",
            selected_stocks=["sh.600003"],
            initial_capital=100000,
            final_value=101500,
            return_pct=1.5,
            is_profit=True,
            trade_history=[],
            params={},
            benchmark_passed=True,
            selection_mode="algorithm",
            routing_decision={"regime": "oscillation"},
            research_feedback={},
            causal_diagnosis={},
        ),
    ]

    report = build_freeze_report(
        controller.cycle_history,
        controller.current_params,
        freeze_total_cycles=4,
        freeze_profit_required=2,
        freeze_gate_policy={"governance": {"max_candidate_pending_count": 0}},
        rolling={
            "window": 4,
            "profit_count": 1,
            "win_rate": 0.25,
            "avg_return": -0.875,
            "avg_sharpe": 0.3,
            "avg_max_drawdown": 9.0,
            "avg_excess_return": -0.6,
            "benchmark_pass_rate": 0.5,
        },
        research_feedback={},
    )

    dashboard = report["regime_failure_dashboard"]
    assert dashboard["schema_version"] == "training.regime_failure_dashboard.v1"
    assert dashboard["regimes"]["bear"]["loss_cycles"] == 2
    assert dashboard["regimes"]["bear"]["top_failure_signature"] == "overexposed_in_bear"
    assert dashboard["regimes"]["bull"]["top_failure_signature"] == "trend_chase_failed"
    assert dashboard["top_repeated_loss_signatures"][0]["label"] == "overexposed_in_bear"
    assert dashboard["top_repeated_loss_signatures"][0]["count"] == 2


def test_build_freeze_report_includes_regime_failure_sub_signature_dashboard(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )
    controller.cycle_history = [
        TrainingResult(
            cycle_id=1,
            cutoff_date="20240101",
            selected_stocks=["sh.600000"],
            initial_capital=100000,
            final_value=99700,
            return_pct=-0.3,
            is_profit=False,
            trade_history=[],
            params={},
            benchmark_passed=False,
            selection_mode="algorithm",
            model_name="value_quality",
            routing_decision={"regime": "oscillation"},
            research_feedback={"sample_count": 5, "recommendation": {"bias": "maintain"}},
            causal_diagnosis={"primary_driver": "benchmark_gap"},
        ),
        TrainingResult(
            cycle_id=2,
            cutoff_date="20240108",
            selected_stocks=["sh.600001"],
            initial_capital=100000,
            final_value=99500,
            return_pct=-0.5,
            is_profit=False,
            trade_history=[],
            params={},
            benchmark_passed=False,
            selection_mode="algorithm",
            model_name="value_quality",
            routing_decision={"regime": "oscillation"},
            research_feedback={"sample_count": 5, "recommendation": {"bias": "maintain"}},
            causal_diagnosis={"primary_driver": "benchmark_gap"},
        ),
    ]

    report = build_freeze_report(
        controller.cycle_history,
        controller.current_params,
        freeze_total_cycles=2,
        freeze_profit_required=1,
        freeze_gate_policy={"governance": {"max_candidate_pending_count": 0}},
        rolling={
            "window": 2,
            "profit_count": 0,
            "win_rate": 0.0,
            "avg_return": -0.4,
            "avg_sharpe": 0.0,
            "avg_max_drawdown": 3.0,
            "avg_excess_return": -0.2,
            "benchmark_pass_rate": 0.0,
        },
        research_feedback={},
    )

    dashboard = report["regime_failure_dashboard"]
    assert dashboard["regimes"]["oscillation"]["top_failure_signature"] == "mean_revert_failed"
    assert dashboard["regimes"]["oscillation"]["top_failure_sub_signature"] == "defensive_lag"
    assert dashboard["regimes"]["oscillation"]["failure_sub_signature_counts"] == {
        "defensive_lag": 2
    }


def test_build_freeze_report_includes_regime_discipline_dashboard(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )
    controller.cycle_history = [
        TrainingResult(
            cycle_id=1,
            cutoff_date="20240101",
            selected_stocks=["sh.600519"],
            initial_capital=100000,
            final_value=99000,
            return_pct=-1.0,
            is_profit=False,
            trade_history=[],
            params={"position_size": 0.1, "cash_reserve": 0.45, "max_positions": 2},
            benchmark_passed=False,
            routing_decision={"regime": "bear"},
            selection_intercepts={
                "schema_version": "training.regime_hard_filter.v1",
                "active": True,
                "intercepted_count": 2,
                "reason_counts": {
                    "weak_signal_below_regime_threshold": 1,
                    "exposure_budget_regime_cap": 1,
                },
                "exposure_before": 0.32,
                "exposure_after": 0.10,
            },
            regime_runtime_profile={
                "schema_version": "training.regime_runtime_profile.v1",
                "regime": "bear",
                "applied": True,
            },
        ),
        TrainingResult(
            cycle_id=2,
            cutoff_date="20240108",
            selected_stocks=["sh.600010"],
            initial_capital=100000,
            final_value=101000,
            return_pct=1.0,
            is_profit=True,
            trade_history=[],
            params={"position_size": 0.18, "cash_reserve": 0.22, "max_positions": 4},
            benchmark_passed=True,
            routing_decision={"regime": "bull"},
            selection_intercepts={
                "schema_version": "training.regime_hard_filter.v1",
                "active": False,
                "intercepted_count": 0,
                "reason_counts": {},
                "exposure_before": 0.18,
                "exposure_after": 0.18,
            },
            regime_runtime_profile={
                "schema_version": "training.regime_runtime_profile.v1",
                "regime": "bull",
                "applied": True,
            },
        ),
    ]

    report = build_freeze_report(
        controller.cycle_history,
        controller.current_params,
        freeze_total_cycles=2,
        freeze_profit_required=1,
        freeze_gate_policy={"governance": {"max_candidate_pending_count": 0}},
        rolling={
            "window": 2,
            "profit_count": 1,
            "win_rate": 0.5,
            "avg_return": 0.0,
            "avg_sharpe": 0.6,
            "avg_max_drawdown": 7.0,
            "avg_excess_return": -0.1,
            "benchmark_pass_rate": 0.5,
        },
        research_feedback={},
    )

    dashboard = report["regime_discipline_dashboard"]
    assert dashboard["schema_version"] == "training.regime_discipline_dashboard.v1"
    assert dashboard["overlay_applied_cycles"] == 2
    assert dashboard["strategy_families"] == ["momentum"]
    assert dashboard["hard_filter_cycles"] == 1
    assert dashboard["regimes"]["bear"]["top_reason"] == "exposure_budget_regime_cap"
    assert dashboard["regimes"]["bear"]["intercepted_count"] == 2
    assert dashboard["regimes"]["bear"]["avg_position_size_cap"] == 0.1
    assert dashboard["regimes"]["bear"]["avg_cash_reserve_floor"] == 0.45
    assert dashboard["regimes"]["bear"]["avg_max_positions_cap"] == 2.0
    assert dashboard["top_repeated_intercept_reasons"] == []
