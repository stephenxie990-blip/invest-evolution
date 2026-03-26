import json

from app.validation.phase0 import (
    aggregate_cycle_metrics,
    build_bare_trading_plan,
    build_calibration_experiment_spec,
    build_trade_trace_records,
    compare_validation_runs,
    load_cutoff_dates_from_run,
)
from invest.contracts import SignalPacket, SignalPacketContext, StockSignal


def test_load_cutoff_dates_from_run_orders_cycle_files(tmp_path):
    (tmp_path / "cycle_2.json").write_text(
        json.dumps({"cycle_id": 2, "cutoff_date": "2024-02-02"}),
        encoding="utf-8",
    )
    (tmp_path / "cycle_0001_config_snapshot.json").write_text("{}", encoding="utf-8")
    (tmp_path / "cycle_1.json").write_text(
        json.dumps({"cycle_id": 1, "cutoff_date": "2024-01-01"}),
        encoding="utf-8",
    )

    assert load_cutoff_dates_from_run(tmp_path) == ["20240101", "20240202"]


def test_build_bare_trading_plan_uses_signal_defaults_and_cash_reserve():
    packet = SignalPacket(
        as_of_date="20240201",
        model_name="momentum",
        config_name="demo",
        regime="bull",
        signals=[
            StockSignal(
                code="sh.600001",
                score=0.91,
                rank=1,
                weight_hint=0.28,
                stop_loss_pct=0.07,
                take_profit_pct=0.18,
                evidence=["20日涨幅领先", "站上均线"],
            ),
            StockSignal(
                code="sh.600002",
                score=0.86,
                rank=2,
                evidence=["量价配合"],
            ),
        ],
        selected_codes=["sh.600001", "sh.600002"],
        max_positions=2,
        cash_reserve=0.2,
        params={
            "position_size": 0.25,
            "stop_loss_pct": 0.05,
            "take_profit_pct": 0.15,
            "max_hold_days": 20,
        },
        reasoning="raw momentum picks",
        context=SignalPacketContext(),
    )

    plan = build_bare_trading_plan(packet)

    assert plan.source == "bare_strategy"
    assert plan.cash_reserve == 0.2
    assert plan.max_positions == 2
    assert [item.code for item in plan.positions] == ["sh.600001", "sh.600002"]
    assert plan.positions[0].weight == 0.28
    assert plan.positions[0].stop_loss_pct == 0.07
    assert plan.positions[0].take_profit_pct == 0.18
    assert plan.positions[1].weight == 0.25
    assert plan.positions[1].stop_loss_pct == 0.05
    assert plan.positions[1].take_profit_pct == 0.15
    assert plan.positions[1].max_hold_days == 20


def test_aggregate_cycle_metrics_builds_regime_breakdown():
    payload = [
        {
            "status": "ok",
            "cycle_id": 1,
            "regime": "bull",
            "return_pct": 4.0,
            "benchmark_passed": True,
            "self_assessment": {
                "sharpe_ratio": 1.2,
                "max_drawdown": 3.0,
                "excess_return": 2.0,
            },
            "strategy_scores": {"overall_score": 0.7},
        },
        {
            "status": "ok",
            "cycle_id": 2,
            "regime": "bear",
            "return_pct": -2.0,
            "benchmark_passed": False,
            "self_assessment": {
                "sharpe_ratio": -0.4,
                "max_drawdown": 6.0,
                "excess_return": -1.0,
            },
            "strategy_scores": {"overall_score": 0.4},
        },
        {
            "status": "skipped",
            "cycle_id": 3,
        },
    ]

    summary = aggregate_cycle_metrics(payload)

    assert summary["cycle_count"] == 3
    assert summary["completed_cycle_count"] == 2
    assert summary["skipped_cycle_count"] == 1
    assert summary["profit_cycle_count"] == 1
    assert summary["avg_return_pct"] == 1.0
    assert summary["benchmark_pass_rate"] == 0.5
    assert summary["regime_breakdown"]["bull"]["avg_return_pct"] == 4.0
    assert summary["regime_breakdown"]["bear"]["avg_return_pct"] == -2.0


def test_build_trade_trace_records_pairs_benchmark_and_fees():
    payload = {
        "cycles": [
            {
                "status": "ok",
                "cycle_id": 1,
                "cutoff_date": "20240201",
                "regime": "bull",
                "execution_policy": {
                    "commission_rate": 0.00025,
                    "stamp_tax_rate": 0.0005,
                },
                "selected_signal_details": {
                    "sh.600001": {"score": 0.9, "evidence": ["20日涨幅领先"]},
                },
                "benchmark_series": [
                    {"date": "20240202", "close": 100.0},
                    {"date": "20240208", "close": 103.0},
                ],
                "trades": [
                    {
                        "action": "卖出",
                        "ts_code": "sh.600001",
                        "date": "20240208",
                        "entry_date": "20240202",
                        "entry_price": 10.0,
                        "price": 10.6,
                        "shares": 1000,
                        "pnl": 560.0,
                        "pnl_pct": 5.6,
                        "holding_days": 4,
                        "exit_trigger": "take_profit",
                        "exit_reason": "止盈",
                    }
                ],
            }
        ]
    }

    traces = build_trade_trace_records(payload, limit=3)

    assert len(traces) == 1
    trace = traces[0]
    assert trace["ts_code"] == "sh.600001"
    assert round(trace["fees"]["buy_commission"], 4) == 2.5
    assert round(trace["fees"]["sell_commission"], 4) == 2.65
    assert round(trace["fees"]["stamp_tax"], 4) == 5.3
    assert round(trace["benchmark"]["return_pct"], 4) == 3.0
    assert round(trace["benchmark"]["excess_return_pct"], 4) == 2.6
    assert trace["raw_signal"]["score"] == 0.9


def test_build_trade_trace_records_mixed_selection_prefers_losses_wins_and_flat():
    payload = {
        "cycles": [
            {
                "status": "ok",
                "cycle_id": 1,
                "cutoff_date": "20240201",
                "regime": "bull",
                "execution_policy": {
                    "commission_rate": 0.00025,
                    "stamp_tax_rate": 0.0005,
                },
                "selected_signal_details": {},
                "benchmark_series": [],
                "trades": [
                    {"action": "卖出", "ts_code": "A", "date": "20240208", "entry_date": "20240202", "entry_price": 10.0, "price": 12.0, "shares": 100, "pnl": 200.0, "pnl_pct": 20.0},
                    {"action": "卖出", "ts_code": "B", "date": "20240208", "entry_date": "20240202", "entry_price": 10.0, "price": 9.5, "shares": 100, "pnl": -50.0, "pnl_pct": -5.0},
                    {"action": "卖出", "ts_code": "C", "date": "20240208", "entry_date": "20240202", "entry_price": 10.0, "price": 10.02, "shares": 100, "pnl": 2.0, "pnl_pct": 0.2},
                    {"action": "卖出", "ts_code": "D", "date": "20240208", "entry_date": "20240202", "entry_price": 10.0, "price": 8.0, "shares": 100, "pnl": -200.0, "pnl_pct": -20.0},
                    {"action": "卖出", "ts_code": "E", "date": "20240208", "entry_date": "20240202", "entry_price": 10.0, "price": 11.0, "shares": 100, "pnl": 100.0, "pnl_pct": 10.0},
                ],
            }
        ]
    }

    traces = build_trade_trace_records(payload, limit=5, selection="mixed")
    names = [item["ts_code"] for item in traces]

    assert "D" in names
    assert "B" in names
    assert "A" in names
    assert "E" in names
    assert "C" in names


def test_build_calibration_experiment_spec_and_compare_runs():
    spec = build_calibration_experiment_spec(
        model_name="momentum",
        cutoff_dates=["20240201", "20240208"],
        min_history_days=180,
        simulation_days=30,
        dry_run_llm=True,
    )

    assert spec["protocol"]["cutoff_policy"]["mode"] == "sequence"
    assert spec["protocol"]["cutoff_policy"]["dates"] == ["20240201", "20240208"]
    assert spec["dataset"]["min_history_days"] == 180
    assert spec["model_scope"]["experiment_mode"] == "validation"
    assert spec["model_scope"]["model_routing_enabled"] is False
    assert spec["llm"]["dry_run"] is True

    comparison = compare_validation_runs(
        bare_summary={
            "summary": {
                "avg_return_pct": 3.0,
                "median_return_pct": 3.0,
                "compounded_return_pct": 3.0,
                "avg_sharpe_ratio": 1.1,
                "avg_max_drawdown": 4.0,
                "avg_excess_return": 1.5,
                "avg_strategy_score": 0.7,
                "benchmark_pass_rate": 0.6,
                "profit_cycle_rate": 0.6,
                "regime_breakdown": {
                    "bull": {
                        "avg_return_pct": 4.0,
                        "avg_sharpe_ratio": 1.2,
                        "benchmark_pass_rate": 0.8,
                    }
                },
            }
        },
        system_summary={
            "summary": {
                "avg_return_pct": 1.0,
                "median_return_pct": 1.0,
                "compounded_return_pct": 1.0,
                "avg_sharpe_ratio": 0.4,
                "avg_max_drawdown": 5.0,
                "avg_excess_return": 0.3,
                "avg_strategy_score": 0.5,
                "benchmark_pass_rate": 0.2,
                "profit_cycle_rate": 0.4,
                "regime_breakdown": {
                    "bull": {
                        "avg_return_pct": 1.5,
                        "avg_sharpe_ratio": 0.6,
                        "benchmark_pass_rate": 0.3,
                    }
                },
            }
        },
    )

    assert comparison["delta"]["avg_return_pct"] == -2.0
    assert comparison["delta"]["benchmark_pass_rate"] == -0.39999999999999997
    assert comparison["regime_delta"]["bull"]["avg_return_pct"] == -2.5
    assert comparison["system_worse_than_bare"] is True
