from app.train import SelfLearningController, TrainingResult


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
    assert report["freeze_gate_evaluation"]["research_feedback_gate"]["passed"] is False
