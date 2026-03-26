from app.train import SelfLearningController


def test_controller_loads_runtime_policy_from_model_config(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "out"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "state" / "audit.jsonl"),
        config_snapshot_dir=str(tmp_path / "state" / "snapshots"),
    )

    assert controller.current_params["take_profit_pct"] == 0.15
    assert controller.current_params["signal_threshold"] == 0.70
    assert controller.execution_policy["commission_rate"] == 0.00025
    assert controller.selection_meeting.agent_weights["trend_hunter"] == 1.0
    assert controller.benchmark_evaluator.criteria["calmar_ratio"] == 1.5
    assert controller.freeze_total_cycles == 10
    assert controller.freeze_profit_required == 7
    assert controller.max_losses_before_optimize == 3
    assert controller.risk_policy["dynamic_stop"]["atr_period"] == 14
    assert controller.evaluation_policy["weights"]["risk_control"] == 0.40
    assert controller.auto_apply_mutation is False
    assert controller.strategy_family == "momentum"
    assert controller.strategy_family_risk_budgets == {}
    assert controller.regime_controls["bear"]["cash_reserve"] == 0.45
    assert controller.regime_controls["oscillation"]["max_positions"] == 3
    assert controller.quality_gate_matrix["routing"]["regime_hard_fail"]["critical_regimes"] == ["bull", "bear"]
    assert (
        controller.quality_gate_matrix["routing"]["regime_hard_fail"]["per_regime"]["bull"]["min_avg_return_pct"]
        == -0.10
    )
    assert (
        controller.quality_gate_matrix["promotion"]["regime_hard_fail"]["per_regime"]["bear"]["max_win_rate"]
        == 0.40
    )
    assert (
        controller.proposal_gate_policy["identity_protection"]["max_single_step_ratio_vs_baseline"]
        == 0.30
    )
    assert (
        controller.proposal_gate_policy["identity_protection"]["scoring"]["max_single_step_ratio_vs_baseline"]
        == 0.30
    )
    assert (
        controller.proposal_gate_policy["cumulative_drift"]["agent_weights"]["max_ratio_vs_baseline"]
        == 0.50
    )


def test_model_config_exposes_summary_scoring(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "out"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "state" / "audit.jsonl"),
        config_snapshot_dir=str(tmp_path / "state" / "snapshots"),
    )

    summary_scoring = controller.investment_model.config_section("summary_scoring", {})
    assert summary_scoring["weights"]["change_5d"] == 0.15
    assert summary_scoring["weights"]["macd"]["gold_cross"] == 0.20
    assert summary_scoring["bands"]["bb_low"] == 0.30


def test_constructor_train_policy_override_survives_model_sync(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "out"),
        meeting_log_dir=str(tmp_path / "meetings"),
        config_audit_log_path=str(tmp_path / "state" / "audit.jsonl"),
        config_snapshot_dir=str(tmp_path / "state" / "snapshots"),
        max_losses_before_optimize=1,
    )

    assert controller.max_losses_before_optimize == 1
    assert controller.manual_train_policy_overrides["max_losses_before_optimize"] == 1

    controller._reload_investment_model(controller.model_config_path)  # pylint: disable=protected-access

    assert controller.max_losses_before_optimize == 1
