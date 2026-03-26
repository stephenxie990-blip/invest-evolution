from __future__ import annotations

from types import SimpleNamespace

from app.validation import phase1_calibration
from app.validation.phase1_calibration import (
    build_phase1_calibration_spec,
    build_phase1_calibration_summary,
    summarize_regime_performance,
)


def test_build_phase1_calibration_spec_pins_standard_mode_single_model():
    spec = build_phase1_calibration_spec(
        model_name="momentum",
        seed=101,
        min_history_days=200,
        simulation_days=30,
        stock_count=50,
        dry_run_llm=True,
        runtime_train_overrides={"max_losses_before_optimize": 1},
    )

    assert spec["model_scope"]["experiment_mode"] == "standard"
    assert spec["model_scope"]["allowed_models"] == ["momentum"]
    assert spec["model_scope"]["model_routing_enabled"] is False
    assert spec["dataset"]["universe_policy"]["mode"] == "stratified_random"
    assert spec["protocol"]["cutoff_policy"]["mode"] == "regime_balanced"
    assert spec["optimization"]["runtime_train_overrides"] == {
        "max_losses_before_optimize": 1,
    }


def test_build_phase1_calibration_spec_supports_target_regime_probe():
    spec = build_phase1_calibration_spec(
        model_name="value_quality",
        seed=101,
        min_history_days=200,
        simulation_days=30,
        stock_count=50,
        dry_run_llm=True,
        target_regime="oscillation",
        target_regime_probe_count=15,
    )

    assert spec["protocol"]["cutoff_policy"]["mode"] == "regime_balanced"
    assert spec["protocol"]["cutoff_policy"]["target_regimes"] == ["oscillation"]
    assert spec["protocol"]["cutoff_policy"]["probe_count"] == 15
    assert spec["protocol"]["cutoff_policy"]["probe_mode"] == "model_cycle"


def test_build_phase1_calibration_spec_preserves_sequence_sampling_seeds():
    spec = build_phase1_calibration_spec(
        model_name="value_quality",
        seed=101,
        min_history_days=200,
        simulation_days=30,
        stock_count=50,
        dry_run_llm=True,
        cutoff_dates=["20240105", "20240112"],
        cutoff_sampling_seeds=[108, 109],
    )

    assert spec["protocol"]["cutoff_policy"]["mode"] == "sequence"
    assert spec["protocol"]["cutoff_policy"]["dates"] == ["20240105", "20240112"]
    assert spec["protocol"]["cutoff_policy"]["sampling_seeds"] == [108, 109]


def test_probe_target_regime_cutoff_dates_uses_model_cycle_preview(monkeypatch):
    class DummyDataManager:
        def __init__(self):
            self._dates = iter(
                [
                    "20240105",
                    "20240112",
                    "20240119",
                    "20240126",
                    "20240202",
                    "20240209",
                    "20240216",
                    "20240223",
                ]
            )

        def random_cutoff_date(self, min_date="20180101", max_date=None):
            del min_date, max_date
            return next(self._dates)

    controller = SimpleNamespace(data_manager=DummyDataManager())
    calls: list[tuple[str, int]] = []

    def fake_preview(
        owner,
        *,
        cutoff_date,
        stock_count,
        min_history_days,
        sampling_policy,
        sampling_seed,
    ):
        del owner, stock_count, min_history_days, sampling_policy
        calls.append((cutoff_date, sampling_seed))
        mapping = {
            "20240105": {"regime": "bull", "regime_confidence": 0.4},
            "20240112": {"regime": "oscillation", "regime_confidence": 0.7},
            "20240119": {"regime": "oscillation", "regime_confidence": 0.9},
            "20240126": {"regime": "bear", "regime_confidence": 0.8},
            "20240202": {"regime": "bull", "regime_confidence": 0.5},
            "20240209": {"regime": "oscillation", "regime_confidence": 0.65},
            "20240216": {"regime": "bear", "regime_confidence": 0.75},
            "20240223": {"regime": "bull", "regime_confidence": 0.55},
        }
        return mapping[cutoff_date]

    monkeypatch.setattr(phase1_calibration, "_preview_model_cycle_regime", fake_preview)

    dates = phase1_calibration._probe_target_regime_cutoff_dates(
        controller,
        target_regime="oscillation",
        cycles=2,
        seed=7,
        min_history_days=200,
        stock_count=50,
        probe_count=8,
        probe_mode="model_cycle",
        universe_policy={"mode": "stratified_random", "stratify_by": "board"},
    )

    assert dates == ["20240119", "20240112"]
    assert calls == [
        ("20240105", 8),
        ("20240112", 9),
        ("20240119", 10),
        ("20240126", 11),
        ("20240202", 12),
        ("20240209", 13),
        ("20240216", 14),
        ("20240223", 15),
    ]


def test_summarize_regime_performance_aggregates_cycle_metrics():
    performance = summarize_regime_performance(
        [
            {"cycle_id": 1, "return_pct": 1.0, "is_profit": True, "benchmark_passed": True, "regime": "bull"},
            {"cycle_id": 2, "return_pct": -0.5, "is_profit": False, "benchmark_passed": False, "regime": "bull"},
            {"cycle_id": 3, "return_pct": -1.2, "is_profit": False, "benchmark_passed": False, "regime": "bear"},
        ]
    )

    assert performance["bull"]["cycles"] == 2
    assert performance["bull"]["avg_return_pct"] == 0.25
    assert performance["bull"]["benchmark_pass_rate"] == 0.5
    assert performance["bear"]["loss_cycles"] == 1


def test_build_phase1_calibration_summary_includes_regime_hard_fail_evaluation(tmp_path):
    summary = build_phase1_calibration_summary(
        model_name="momentum",
        output_dir=tmp_path,
        experiment_spec={"protocol": {"seed": 101}},
        report={
            "total_cycles": 2,
            "regime_discipline_dashboard": {
                "overlay_applied_cycles": 2,
                "hard_filter_cycles": 1,
                "budget_correction_applied_cycles": 1,
                "strategy_families": ["momentum"],
                "top_repeated_budget_correction_signatures": [
                    {"sub_label": "false_rebound_entry", "count": 1}
                ],
            },
            "suggestion_adoption_summary": {
                "suggestion_count": 3,
                "adopted_suggestion_count": 2,
                "pending_effect_count": 1,
                "completed_evaluation_count": 1,
                "improved_suggestion_count": 1,
                "evaluated_effect_suggestions": [
                    {"suggestion_id": "suggestion_0002_001", "effect_status": "improved"}
                ],
                "pending_effect_suggestions": [
                    {"suggestion_id": "suggestion_0002_002", "evaluation_after_cycle_id": 5}
                ],
            },
        },
        cycle_history=[
            {"cycle_id": 1, "cutoff_date": "20240201", "return_pct": -1.0, "is_profit": False, "benchmark_passed": False, "regime": "bear"},
            {"cycle_id": 2, "cutoff_date": "20240208", "return_pct": -0.8, "is_profit": False, "benchmark_passed": False, "regime": "bear"},
        ],
        llm_mode="dry_run",
        regime_hard_fail_policy={
            "critical_regimes": ["bear"],
            "min_cycles": 2,
            "per_regime": {
                "bear": {
                    "min_avg_return_pct": -0.5,
                    "max_benchmark_pass_rate": 0.2,
                    "max_win_rate": 0.3,
                }
            },
        },
    )

    assert summary["completed_cycle_count"] == 2
    assert summary["overlay_applied_cycles"] == 2
    assert summary["hard_filter_cycles"] == 1
    assert summary["budget_correction_applied_cycles"] == 1
    assert summary["strategy_families"] == ["momentum"]
    assert summary["top_budget_correction_signatures"][0]["sub_label"] == "false_rebound_entry"
    assert summary["suggestion_count"] == 3
    assert summary["adopted_suggestion_count"] == 2
    assert summary["pending_effect_count"] == 1
    assert summary["completed_effect_count"] == 1
    assert summary["improved_suggestion_count"] == 1
    assert summary["evaluated_effect_suggestions"][0]["suggestion_id"] == "suggestion_0002_001"
    assert summary["pending_effect_suggestions"][0]["suggestion_id"] == "suggestion_0002_002"
    assert summary["regime_performance"]["bear"]["cycles"] == 2
    assert summary["regime_hard_fail_evaluation"]["passed"] is False
    assert summary["regime_hard_fail_evaluation"]["failed_regime_names"] == ["bear"]
