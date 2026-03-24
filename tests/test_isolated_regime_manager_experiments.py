from invest_evolution.application.training.isolated_experiments import (
    build_isolated_experiment_spec,
    discover_isolated_regime_dates,
    resolve_isolated_experiment_preset,
)


def test_resolve_isolated_experiment_preset_known_lines():
    defensive = resolve_isolated_experiment_preset("defensive_low_vol@bear")
    mean_reversion = resolve_isolated_experiment_preset("mean_reversion@oscillation")

    assert defensive.manager_id == "defensive_low_vol"
    assert defensive.target_regime == "bear"
    assert mean_reversion.manager_id == "mean_reversion"
    assert mean_reversion.target_regime == "oscillation"


def test_build_isolated_experiment_spec_clamps_manager_and_sequence_dates():
    spec = build_isolated_experiment_spec(
        manager_id="defensive_low_vol",
        cutoff_dates=["2025-02-01", "2025-03-03"],
        llm_dry_run=True,
    )

    assert spec == {
        "protocol": {
            "shadow_mode": True,
            "review_window": {"mode": "rolling", "size": 5},
            "cutoff_policy": {
                "mode": "sequence",
                "dates": ["20250201", "20250303"],
            },
        },
        "manager_scope": {
            "allowed_manager_ids": ["defensive_low_vol"],
        },
        "llm": {"dry_run": True},
    }


def test_discover_isolated_regime_dates_collects_matching_dates_with_manager_scope():
    preview_calls: list[dict[str, object]] = []

    class DummyDataManager:
        @staticmethod
        def check_training_readiness(cutoff_date, *, stock_count, min_history_days):
            return {
                "ready": True,
                "date_range": {"max": "20240401"},
                "cutoff_date": cutoff_date,
                "stock_count": stock_count,
                "min_history_days": min_history_days,
            }

    class DummyController:
        experiment_min_date = "20240101"
        experiment_min_history_days = 180
        data_manager = DummyDataManager()

        @staticmethod
        def preview_governance(*, cutoff_date, stock_count, min_history_days, allowed_manager_ids=None):
            preview_calls.append(
                {
                    "cutoff_date": cutoff_date,
                    "stock_count": stock_count,
                    "min_history_days": min_history_days,
                    "allowed_manager_ids": list(allowed_manager_ids or []),
                }
            )
            mapping = {
                "20240131": {"regime": "bull", "regime_confidence": 0.51},
                "20240301": {"regime": "bear", "regime_confidence": 0.72},
                "20240331": {"regime": "bear", "regime_confidence": 0.68},
            }
            return mapping[cutoff_date]

    discovery = discover_isolated_regime_dates(
        DummyController(),
        manager_id="defensive_low_vol",
        target_regime="bear",
        step_days=30,
        warmup_windows=1,
        min_date="20240101",
        max_dates=2,
    )

    assert discovery["anchor_date"] == "20240131"
    assert discovery["matched_dates"] == ["20240301", "20240331"]
    assert all(call["allowed_manager_ids"] == ["defensive_low_vol"] for call in preview_calls)
    assert [probe["matched"] for probe in discovery["probes"]] == [False, True, True]
