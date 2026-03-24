import json

from invest_evolution.investment.governance import ModelAllocator


LEADERBOARD = {
    "generated_at": "2026-03-09T00:00:00",
    "entries": [
        {
            "manager_id": "momentum",
            "manager_config_ref": "momentum_v1",
            "score": 55.0,
            "avg_return_pct": 4.0,
            "avg_sharpe_ratio": 1.8,
            "avg_max_drawdown": 8.0,
            "benchmark_pass_rate": 0.8,
            "rank": 1,
        },
        {
            "manager_id": "mean_reversion",
            "manager_config_ref": "mean_reversion_v1",
            "score": 48.0,
            "avg_return_pct": 2.0,
            "avg_sharpe_ratio": 1.2,
            "avg_max_drawdown": 6.0,
            "benchmark_pass_rate": 0.7,
            "rank": 2,
        },
        {
            "manager_id": "value_quality",
            "manager_config_ref": "value_quality_v1",
            "score": 44.0,
            "avg_return_pct": 1.5,
            "avg_sharpe_ratio": 1.1,
            "avg_max_drawdown": 5.0,
            "benchmark_pass_rate": 0.75,
            "rank": 3,
        },
        {
            "manager_id": "defensive_low_vol",
            "manager_config_ref": "defensive_low_vol_v1",
            "score": 46.0,
            "avg_return_pct": 1.0,
            "avg_sharpe_ratio": 1.6,
            "avg_max_drawdown": 3.0,
            "benchmark_pass_rate": 0.9,
            "rank": 4,
        },
    ],
    "regime_leaderboards": {
        "bull": [
            {"rank": 1, "manager_id": "momentum"},
            {"rank": 2, "manager_id": "value_quality"},
        ],
        "bear": [
            {"rank": 1, "manager_id": "defensive_low_vol"},
            {"rank": 2, "manager_id": "value_quality"},
        ],
        "oscillation": [
            {"rank": 1, "manager_id": "mean_reversion"},
            {"rank": 2, "manager_id": "defensive_low_vol"},
        ],
    },
}


def test_allocator_prefers_momentum_in_bull():
    plan = ModelAllocator().allocate("bull", LEADERBOARD)
    assert plan.active_manager_ids[0] == "momentum"
    assert plan.manager_budget_weights["momentum"] > plan.manager_budget_weights["value_quality"]
    assert "mean_reversion" not in plan.manager_budget_weights
    assert plan.cash_reserve == 0.10


def test_allocator_prefers_defensive_in_bear():
    plan = ModelAllocator().allocate("bear", LEADERBOARD)
    assert plan.active_manager_ids[0] == "defensive_low_vol"
    assert plan.manager_budget_weights["defensive_low_vol"] >= max(plan.manager_budget_weights.values())
    assert plan.cash_reserve == 0.30


def test_allocator_only_assigns_budget_to_selected_candidates():
    plan = ModelAllocator().allocate("bull", LEADERBOARD, top_n=1)

    assert plan.active_manager_ids == ["momentum"]
    assert plan.manager_budget_weights == {"momentum": 1.0}
    assert set(plan.selected_manager_config_refs) == {"momentum"}


def test_allocator_prefers_higher_strategy_score_when_scores_close(tmp_path):
    from invest_evolution.investment.governance import build_allocation_plan

    leaderboard_path = tmp_path / 'leaderboard.json'
    leaderboard_path.write_text(json.dumps({
        'generated_at': '2026-03-09T00:00:00',
        'entries': [
            {'manager_id': 'momentum', 'manager_config_ref': 'momentum_v1', 'score': 10.0, 'avg_return_pct': 1.0, 'avg_sharpe_ratio': 1.0, 'avg_max_drawdown': 4.0, 'benchmark_pass_rate': 0.6, 'avg_strategy_score': 0.40, 'rank': 2},
            {'manager_id': 'value_quality', 'manager_config_ref': 'value_quality_v1', 'score': 9.9, 'avg_return_pct': 0.9, 'avg_sharpe_ratio': 1.0, 'avg_max_drawdown': 4.0, 'benchmark_pass_rate': 0.6, 'avg_strategy_score': 0.90, 'rank': 1},
        ],
        'regime_leaderboards': {'unknown': [
            {'manager_id': 'momentum', 'rank': 2},
            {'manager_id': 'value_quality', 'rank': 1},
        ]},
    }, ensure_ascii=False), encoding='utf-8')
    plan = build_allocation_plan('unknown', leaderboard_path, top_n=2)
    assert plan.active_manager_ids[0] == 'value_quality'


def test_allocator_ignores_ineligible_entries_when_eligible_candidates_exist():
    leaderboard = {
        "generated_at": "2026-03-09T00:00:00",
        "entries": [
            {
                "manager_id": "momentum",
                "manager_config_ref": "momentum_v1",
                "score": 80.0,
                "avg_return_pct": 5.0,
                "avg_sharpe_ratio": 1.8,
                "avg_max_drawdown": 9.0,
                "benchmark_pass_rate": 0.2,
                "avg_strategy_score": 0.4,
                "rank": 0,
                "eligible_for_governance": False,
            },
            {
                "manager_id": "value_quality",
                "manager_config_ref": "value_quality_v1",
                "score": 52.0,
                "avg_return_pct": 1.4,
                "avg_sharpe_ratio": 1.2,
                "avg_max_drawdown": 4.0,
                "benchmark_pass_rate": 0.8,
                "avg_strategy_score": 0.82,
                "rank": 1,
                "eligible_for_governance": True,
            },
            {
                "manager_id": "defensive_low_vol",
                "manager_config_ref": "defensive_low_vol_v1",
                "score": 48.0,
                "avg_return_pct": 1.0,
                "avg_sharpe_ratio": 1.3,
                "avg_max_drawdown": 2.5,
                "benchmark_pass_rate": 0.86,
                "avg_strategy_score": 0.75,
                "rank": 2,
                "eligible_for_governance": True,
            },
        ],
        "regime_leaderboards": {
            "bull": [
                {"manager_id": "momentum", "rank": 0, "eligible_for_governance": False},
                {"manager_id": "value_quality", "rank": 1, "eligible_for_governance": True},
            ],
        },
    }

    plan = ModelAllocator().allocate("bull", leaderboard)

    assert plan.active_manager_ids[0] != "momentum"
    assert plan.metadata["used_provisional_leaderboard"] is False


def test_allocator_refuses_to_use_unqualified_entries_as_provisional_candidates():
    leaderboard = {
        "generated_at": "2026-03-09T00:00:00",
        "entries": [
            {
                "manager_id": "mean_reversion",
                "manager_config_ref": "mean_reversion_v1",
                "score": -9.0,
                "avg_return_pct": -1.4,
                "avg_sharpe_ratio": 0.4,
                "avg_max_drawdown": 18.0,
                "benchmark_pass_rate": 0.0,
                "avg_strategy_score": 0.2,
                "rank": 0,
                "eligible_for_governance": False,
                "deployment_stage": "candidate",
                "ineligible_reason": "quality_gate:block_negative_score",
            }
        ],
        "regime_leaderboards": {},
    }

    plan = ModelAllocator().allocate("oscillation", leaderboard)

    assert plan.active_manager_ids == []
    assert plan.manager_budget_weights == {}
    assert plan.metadata["qualified_candidate_count"] == 0
    assert "没有通过质量门的正式候选" in plan.reasoning


def test_allocator_penalizes_regime_style_mismatch_when_bear_uses_fallback_ranking():
    leaderboard = {
        "generated_at": "2026-03-09T00:00:00",
        "entries": [
            {
                "manager_id": "momentum",
                "manager_config_ref": "momentum_v1",
                "score": 92.0,
                "avg_return_pct": 6.0,
                "avg_sharpe_ratio": 1.9,
                "avg_max_drawdown": 9.0,
                "benchmark_pass_rate": 0.82,
                "avg_strategy_score": 0.88,
                "rank": 1,
                "eligible_for_governance": True,
            },
            {
                "manager_id": "defensive_low_vol",
                "manager_config_ref": "defensive_low_vol_v1",
                "score": 54.0,
                "avg_return_pct": 1.4,
                "avg_sharpe_ratio": 1.2,
                "avg_max_drawdown": 2.8,
                "benchmark_pass_rate": 0.9,
                "avg_strategy_score": 0.73,
                "rank": 2,
                "eligible_for_governance": True,
            },
            {
                "manager_id": "value_quality",
                "manager_config_ref": "value_quality_v1",
                "score": 48.0,
                "avg_return_pct": 1.2,
                "avg_sharpe_ratio": 1.0,
                "avg_max_drawdown": 4.0,
                "benchmark_pass_rate": 0.78,
                "avg_strategy_score": 0.7,
                "rank": 3,
                "eligible_for_governance": True,
            },
        ],
        "regime_leaderboards": {},
    }

    plan = ModelAllocator().allocate("bear", leaderboard)

    assert plan.active_manager_ids[0] == "defensive_low_vol"
    top_candidate = plan.metadata["top_candidates"][0]
    assert top_candidate["manager_id"] == "defensive_low_vol"
    assert top_candidate["regime_compatibility"] > 0.9
