from invest_evolution.investment.governance.engine import build_leaderboard


def test_leaderboard_includes_scoring_mutation_summary():
    records = [
        {
            "cycle_id": 1,
            "manager_id": "mean_reversion",
            "manager_config_ref": "mean_reversion_v1",
            "return_pct": 1.2,
            "is_profit": True,
            "benchmark_passed": True,
            "regime": "oscillation",
            "self_assessment": {"sharpe_ratio": 1.0, "max_drawdown": 2.0, "excess_return": 0.5},
            "optimization_events": [
                {"applied_change": {"scoring": {"weights": {"volume_ratio_bonus": 0.1}, "penalties": {"overheat_rsi": 0.18}}}}
            ],
        }
    ]
    board = build_leaderboard(records)
    entry = board["entries"][0]
    assert entry["scoring_mutation_count"] == 1
    assert "weights.volume_ratio_bonus" in entry["scoring_changed_keys"]
