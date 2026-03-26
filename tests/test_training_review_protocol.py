from types import SimpleNamespace

from app.training.review_protocol import build_review_input
from invest.contracts import EvalReport


def test_build_review_input_uses_single_cycle_window_by_default():
    controller = SimpleNamespace(
        cycle_history=[
            SimpleNamespace(
                cycle_id=1,
                cutoff_date="20240101",
                return_pct=-1.2,
                is_profit=False,
                selection_mode="meeting",
                benchmark_passed=False,
                review_applied=False,
                model_name="momentum",
                config_name="configs/momentum.yaml",
                routing_decision={"regime": "bear"},
                audit_tags={},
                research_feedback={},
            ),
        ],
        experiment_review_window={},
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
            SimpleNamespace(
                cycle_id=1,
                cutoff_date="20240101",
                return_pct=-1.2,
                is_profit=False,
                selection_mode="meeting",
                benchmark_passed=False,
                review_applied=False,
                model_name="momentum",
                config_name="configs/momentum.yaml",
                routing_decision={"regime": "bear"},
                audit_tags={"routing_regime": "bear"},
                research_feedback={"recommendation": {"bias": "tighten_risk"}},
            ),
            SimpleNamespace(
                cycle_id=2,
                cutoff_date="20240102",
                return_pct=0.8,
                is_profit=True,
                selection_mode="algorithm",
                benchmark_passed=True,
                review_applied=True,
                model_name="momentum",
                config_name="configs/momentum.yaml",
                routing_decision={"regime": "oscillation"},
                audit_tags={"routing_regime": "oscillation"},
                research_feedback={},
            ),
        ],
        experiment_review_window={"mode": "rolling", "size": 3},
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
            SimpleNamespace(
                cycle_id=1,
                cutoff_date="20240101",
                return_pct=-1.2,
                is_profit=False,
                selection_mode="meeting",
                benchmark_passed=False,
                review_applied=False,
                model_name="momentum",
                config_name="configs/momentum_a.yaml",
                routing_decision={"regime": "bear"},
                audit_tags={"routing_regime": "bear"},
                research_feedback={},
                llm_used=True,
            ),
            SimpleNamespace(
                cycle_id=2,
                cutoff_date="20240102",
                return_pct=0.7,
                is_profit=True,
                selection_mode="algorithm",
                benchmark_passed=True,
                review_applied=True,
                model_name="momentum",
                config_name="configs/momentum_a.yaml",
                routing_decision={"regime": "bull"},
                audit_tags={"routing_regime": "bull"},
                research_feedback={},
                llm_used=False,
            ),
            SimpleNamespace(
                cycle_id=3,
                cutoff_date="20240103",
                return_pct=-0.9,
                is_profit=False,
                selection_mode="meeting",
                benchmark_passed=False,
                review_applied=False,
                model_name="momentum",
                config_name="configs/momentum_a.yaml",
                routing_decision={"regime": "bear"},
                audit_tags={"routing_regime": "bear"},
                research_feedback={},
                llm_used=True,
            ),
        ],
        experiment_review_window={"mode": "rolling", "size": 2},
        model_name="momentum",
        model_config_path="configs/momentum_a.yaml",
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
        metadata={"model_name": "momentum", "config_name": "configs/momentum_a.yaml"},
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
            SimpleNamespace(
                cycle_id=1,
                cutoff_date="20240101",
                return_pct=-1.4,
                is_profit=False,
                selection_mode="meeting",
                benchmark_passed=False,
                review_applied=False,
                model_name="momentum",
                config_name="configs/momentum_a.yaml",
                routing_decision={"regime": "bear"},
                audit_tags={"routing_regime": "bear"},
                research_feedback={"sample_count": 6, "recommendation": {"bias": "tighten_risk"}},
                causal_diagnosis={"primary_driver": "benchmark_gap"},
                llm_used=True,
            ),
            SimpleNamespace(
                cycle_id=2,
                cutoff_date="20240102",
                return_pct=-0.6,
                is_profit=False,
                selection_mode="meeting",
                benchmark_passed=True,
                review_applied=False,
                model_name="momentum",
                config_name="configs/momentum_a.yaml",
                routing_decision={"regime": "bear"},
                audit_tags={"routing_regime": "bear"},
                research_feedback={"sample_count": 6, "recommendation": {"bias": "maintain"}},
                causal_diagnosis={"primary_driver": "benchmark_gap"},
                llm_used=True,
            ),
            SimpleNamespace(
                cycle_id=3,
                cutoff_date="20240103",
                return_pct=0.9,
                is_profit=True,
                selection_mode="meeting",
                benchmark_passed=False,
                review_applied=True,
                model_name="momentum",
                config_name="configs/momentum_a.yaml",
                routing_decision={"regime": "bear"},
                audit_tags={"routing_regime": "bear"},
                research_feedback={"sample_count": 6, "recommendation": {"bias": "tighten_risk"}},
                causal_diagnosis={"primary_driver": "benchmark_gap"},
                llm_used=True,
            ),
        ],
        experiment_review_window={"mode": "rolling", "size": 3},
        model_name="momentum",
        model_config_path="configs/momentum_a.yaml",
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
        "metadata": {"model_name": "momentum", "config_name": "configs/momentum_a.yaml"},
        "research_feedback": {"sample_count": 7, "recommendation": {"bias": "tighten_risk"}},
        "causal_diagnosis": {"primary_driver": "benchmark_gap"},
    }

    review_input = build_review_input(controller, cycle_id=4, eval_report=eval_report)

    assert [item["cycle_id"] for item in review_input["similar_results"]] == [1]
    assert review_input["similarity_summary"]["matched_cycle_ids"] == [1]
    assert review_input["similarity_summary"]["matched_primary_driver"] == "benchmark_gap"
    assert review_input["similarity_summary"]["matched_feedback_bias"] == "tighten_risk"


def test_build_review_input_assigns_phase1_failure_signature_v1_label():
    controller = SimpleNamespace(
        cycle_history=[],
        experiment_review_window={"mode": "single_cycle", "size": 1},
        model_name="momentum",
        model_config_path="configs/momentum_a.yaml",
    )
    eval_report = {
        "cycle_id": 1,
        "as_of_date": "20240104",
        "return_pct": -2.4,
        "total_pnl": -2400.0,
        "total_trades": 4,
        "win_rate": 0.25,
        "regime": "bear",
        "is_profit": False,
        "selected_codes": ["sh.600519"],
        "selection_mode": "meeting",
        "benchmark_passed": False,
        "metadata": {"model_name": "momentum", "config_name": "configs/momentum_a.yaml"},
        "research_feedback": {"sample_count": 7, "recommendation": {"bias": "tighten_risk"}},
        "causal_diagnosis": {"primary_driver": "regime_repeat_loss"},
    }

    review_input = build_review_input(controller, cycle_id=1, eval_report=eval_report)
    current = review_input["recent_results"][-1]

    assert current["failure_signature"]["schema_version"] == "failure_signature.v1"
    assert current["failure_signature"]["label"] == "overexposed_in_bear"
    assert current["failure_signature"]["confidence"] >= 0.8
    assert "bear_regime" in current["failure_signature"]["reason_codes"]
    assert current["failure_signature"]["reason"]


def test_build_review_input_refines_mean_reversion_oscillation_failure_sub_signature():
    controller = SimpleNamespace(
        cycle_history=[],
        experiment_review_window={"mode": "single_cycle", "size": 1},
        model_name="mean_reversion",
        model_config_path="configs/mean_reversion_v1.yaml",
    )
    eval_report = {
        "cycle_id": 1,
        "as_of_date": "20240104",
        "return_pct": -1.6,
        "total_pnl": -1600.0,
        "total_trades": 4,
        "win_rate": 0.25,
        "regime": "oscillation",
        "is_profit": False,
        "selected_codes": ["sh.600519"],
        "selection_mode": "algorithm",
        "benchmark_passed": False,
        "metadata": {"model_name": "mean_reversion", "config_name": "configs/mean_reversion_v1.yaml"},
        "research_feedback": {"sample_count": 5, "recommendation": {"bias": "maintain"}},
        "causal_diagnosis": {"primary_driver": "benchmark_gap"},
    }

    review_input = build_review_input(controller, cycle_id=1, eval_report=eval_report)
    current = review_input["recent_results"][-1]["failure_signature"]

    assert current["label"] == "mean_revert_failed"
    assert current["strategy_family"] == "mean_reversion"
    assert current["sub_label"] == "false_rebound_entry"
    assert current["sub_description"]


def test_build_review_input_refines_value_quality_oscillation_failure_sub_signature():
    controller = SimpleNamespace(
        cycle_history=[],
        experiment_review_window={"mode": "single_cycle", "size": 1},
        model_name="value_quality",
        model_config_path="configs/value_quality_v1.yaml",
    )
    eval_report = {
        "cycle_id": 1,
        "as_of_date": "20240104",
        "return_pct": -0.4,
        "total_pnl": -400.0,
        "total_trades": 3,
        "win_rate": 0.33,
        "regime": "oscillation",
        "is_profit": False,
        "selected_codes": ["sh.600519"],
        "selection_mode": "algorithm",
        "benchmark_passed": False,
        "metadata": {"model_name": "value_quality", "config_name": "configs/value_quality_v1.yaml"},
        "research_feedback": {"sample_count": 5, "recommendation": {"bias": "maintain"}},
        "causal_diagnosis": {"primary_driver": "benchmark_gap"},
    }

    review_input = build_review_input(controller, cycle_id=1, eval_report=eval_report)
    current = review_input["recent_results"][-1]["failure_signature"]

    assert current["label"] == "mean_revert_failed"
    assert current["strategy_family"] == "value_quality"
    assert current["sub_label"] == "defensive_lag"
    assert current["sub_description"]


def test_build_review_input_uses_trade_micro_attribution_for_value_quality_concentration_mismatch():
    controller = SimpleNamespace(
        cycle_history=[],
        experiment_review_window={"mode": "single_cycle", "size": 1},
        model_name="value_quality",
        model_config_path="configs/value_quality_v1.yaml",
    )
    eval_report = {
        "cycle_id": 1,
        "as_of_date": "20240104",
        "return_pct": -1.4,
        "total_pnl": -1400.0,
        "total_trades": 3,
        "win_rate": 0.0,
        "regime": "oscillation",
        "is_profit": False,
        "selected_codes": ["sh.600519"],
        "selection_mode": "algorithm",
        "benchmark_passed": False,
        "metadata": {"model_name": "value_quality", "config_name": "configs/value_quality_v1.yaml"},
        "trade_history": [
            {"action": "SELL", "ts_code": "A", "pnl": -1000.0, "pnl_pct": -8.0, "holding_days": 6},
            {"action": "SELL", "ts_code": "B", "pnl": -250.0, "pnl_pct": -2.0, "holding_days": 8},
            {"action": "SELL", "ts_code": "C", "pnl": -150.0, "pnl_pct": -1.0, "holding_days": 5},
        ],
        "research_feedback": {"sample_count": 5, "recommendation": {"bias": "maintain"}},
        "causal_diagnosis": {"primary_driver": "regime_repeat_loss"},
    }

    review_input = build_review_input(controller, cycle_id=1, eval_report=eval_report)
    current = review_input["recent_results"][-1]

    assert current["trade_micro_attribution"]["dominant_loss_share"] > 0.7
    assert current["failure_signature"]["sub_label"] == "concentration_mismatch"


def test_build_review_input_uses_trade_micro_attribution_for_mean_reversion_chop_stopout():
    controller = SimpleNamespace(
        cycle_history=[],
        experiment_review_window={"mode": "single_cycle", "size": 1},
        model_name="mean_reversion",
        model_config_path="configs/mean_reversion_v1.yaml",
    )
    eval_report = {
        "cycle_id": 1,
        "as_of_date": "20240104",
        "return_pct": -0.6,
        "total_pnl": -600.0,
        "total_trades": 3,
        "win_rate": 0.0,
        "regime": "oscillation",
        "is_profit": False,
        "selected_codes": ["sh.600519"],
        "selection_mode": "algorithm",
        "benchmark_passed": True,
        "metadata": {"model_name": "mean_reversion", "config_name": "configs/mean_reversion_v1.yaml"},
        "trade_history": [
            {"action": "SELL", "ts_code": "A", "pnl": -220.0, "pnl_pct": -1.6, "holding_days": 2, "exit_trigger": "stop_loss"},
            {"action": "SELL", "ts_code": "B", "pnl": -180.0, "pnl_pct": -1.3, "holding_days": 2, "exit_trigger": "stop_loss"},
            {"action": "SELL", "ts_code": "C", "pnl": -200.0, "pnl_pct": -1.4, "holding_days": 3, "exit_trigger": "stop_loss"},
        ],
        "research_feedback": {"sample_count": 5, "recommendation": {"bias": "maintain"}},
        "causal_diagnosis": {"primary_driver": "benchmark_gap"},
    }

    review_input = build_review_input(controller, cycle_id=1, eval_report=eval_report)
    current = review_input["recent_results"][-1]

    assert current["trade_micro_attribution"]["stop_loss_exit_count"] == 3
    assert current["failure_signature"]["sub_label"] == "chop_stopout"
