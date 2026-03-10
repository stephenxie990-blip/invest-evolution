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
