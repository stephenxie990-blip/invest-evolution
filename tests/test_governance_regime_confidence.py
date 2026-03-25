from invest_evolution.investment.governance.engine import build_leaderboard
from invest_evolution.investment.governance.regime_confidence import (
    build_regime_confidence_map,
)


def test_regime_confidence_marks_under_sampled_regimes_as_exploratory():
    items = [
        {"regime": "bull", "return_pct": 2.0, "benchmark_passed": True},
        {"regime": "bull", "return_pct": 1.0, "benchmark_passed": True},
        {"regime": "bear", "return_pct": -1.0, "benchmark_passed": False},
    ]

    confidence = build_regime_confidence_map(
        items,
        min_cycles_per_regime=2,
    )

    assert confidence["bull"]["exploratory_only"] is False
    assert confidence["bear"]["exploratory_only"] is True
    assert confidence["bear"]["sample_count"] == 1


def test_leaderboard_includes_regime_confidence_and_exploratory_flag():
    leaderboard = build_leaderboard(
        [
            {
                "cycle_id": 1,
                "manager_id": "momentum",
                "manager_config_ref": "momentum_v1",
                "return_pct": 3.0,
                "is_profit": True,
                "benchmark_passed": True,
                "cutoff_date": "20240101",
                "regime": "bull",
                "self_assessment": {
                    "sharpe_ratio": 1.1,
                    "max_drawdown": 3.0,
                    "excess_return": 0.8,
                    "overall_score": 0.72,
                },
            },
            {
                "cycle_id": 2,
                "manager_id": "momentum",
                "manager_config_ref": "momentum_v1",
                "return_pct": -1.0,
                "is_profit": False,
                "benchmark_passed": False,
                "cutoff_date": "20240102",
                "regime": "bear",
                "self_assessment": {
                    "sharpe_ratio": 0.4,
                    "max_drawdown": 6.0,
                    "excess_return": -0.2,
                    "overall_score": 0.35,
                },
            },
        ],
        policy={"min_cycles": 1, "min_cycles_per_regime": 2},
    )

    entry = leaderboard["entries"][0]
    assert "regime_confidence" in entry
    assert entry["regime_confidence"]["bull"]["exploratory_only"] is True
    assert entry["exploratory_only"] is True
