from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from invest_evolution.application.training.research import TrainingFeedbackService
from invest_evolution.investment.research.analysis import (
    OutcomeAttribution,
    PolicySnapshot,
    ResearchHypothesis,
    ResearchSnapshot,
)
from invest_evolution.investment.research.case_store import ResearchCaseStore


def _d(d: date) -> str:
    return d.isoformat()


def _make_case(
    store: ResearchCaseStore,
    *,
    manager_id: str,
    manager_config_ref: str,
    as_of: str,
    regime: str,
    hypothesis_id: str,
    stance: str = "候选买入",
    label: str = "invalidated",
) -> None:
    snapshot = ResearchSnapshot(
        snapshot_id=f"s_{as_of}_{hypothesis_id}",
        as_of_date=as_of,
        scope="unit_test",
        market_context={
            "regime": regime,
            "manager_id": manager_id,
            "manager_config_ref": manager_config_ref,
        },
    )
    policy = PolicySnapshot(
        policy_id=f"p_{manager_id}_{as_of}",
        manager_id=manager_id,
        manager_config_ref=manager_config_ref,
    )
    hypothesis = ResearchHypothesis(
        hypothesis_id=hypothesis_id,
        snapshot_id=snapshot.snapshot_id,
        policy_id=policy.policy_id,
        stance=stance,
        score=90.0,
    )
    store.save_case(snapshot=snapshot, policy=policy, hypothesis=hypothesis)
    # Momentum's default research_feedback gate policy checks T+5 (and does not apply default horizons).
    # Include T+5 in test fixtures so we exercise the strict gate without relaxing thresholds.
    attribution = OutcomeAttribution(
        # ResearchCaseStore.list_attributions() loads files matching "attribution_*.json".
        # Real pipeline attribution ids follow this convention.
        attribution_id=f"attribution_{hypothesis_id}",
        hypothesis_id=hypothesis_id,
        thesis_result="unknown",
        horizon_results={
            "T+5": {"label": label, "return_pct": -1.0 if label == "invalidated" else 1.0},
            "T+20": {"label": label, "return_pct": -1.0 if label == "invalidated" else 1.0},
        },
    )
    store.save_attribution(attribution)


def test_bull_regime_window_expands_when_recent_window_has_no_bull(tmp_path: Path) -> None:
    # Build a store with older bull cases and newer bear cases.
    # With a short history_limit, the most recent window would be bear-only, which
    # previously starved bull evidence even when bull cases existed in history.
    store = ResearchCaseStore(tmp_path)

    manager_id = "momentum"
    manager_config_ref = "momentum_v1"

    bull_start = date(2025, 1, 1)
    for i in range(10):
        as_of = _d(bull_start + timedelta(days=i))
        _make_case(
            store,
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
            as_of=as_of,
            regime="bull",
            hypothesis_id=f"bull_{i}",
            label="invalidated",
        )

    bear_start = date(2026, 2, 1)
    for i in range(30):
        as_of = _d(bear_start + timedelta(days=i))
        _make_case(
            store,
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
            as_of=as_of,
            regime="bear",
            hypothesis_id=f"bear_{i}",
            label="hit",
        )

    feedback = store.build_training_feedback(
        manager_id=manager_id,
        manager_config_ref=manager_config_ref,
        as_of_date="2026-03-24",
        regime="bull",
        limit=5,  # intentionally too small to satisfy min_sample_count=8 without expansion
        max_history_limit=360,
    )

    scope = dict(feedback.get("scope") or {})
    window = dict(scope.get("window") or {})
    assert scope.get("requested_regime") == "bull"
    # The system should expand beyond the short base limit to recover bull evidence.
    assert int(window.get("requested_regime_effective_history_limit") or 0) > 5
    assert bool(window.get("requested_regime_expanded")) is True
    # With enough bull samples in history, regime scope should become actionable.
    assert int(scope.get("regime_sample_count") or 0) >= 8
    assert scope.get("effective_scope") == "regime"
    assert bool(scope.get("actionable")) is True


def test_gate_is_not_relaxed_by_windowing(tmp_path: Path) -> None:
    # Even with a widened window, the gate should stay strict: bad bull evidence
    # should still produce a risk-tightening recommendation.
    store = ResearchCaseStore(tmp_path)

    manager_id = "momentum"
    manager_config_ref = "momentum_v1"

    start = date(2025, 6, 1)
    for i in range(12):
        as_of = _d(start + timedelta(days=i))
        _make_case(
            store,
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
            as_of=as_of,
            regime="bull",
            hypothesis_id=f"bull_bad_{i}",
            label="invalidated",
        )

    feedback = store.build_training_feedback(
        manager_id=manager_id,
        manager_config_ref=manager_config_ref,
        as_of_date="2026-03-24",
        regime="bull",
        limit=5,  # intentionally small
        max_history_limit=200,
    )

    rec = dict(feedback.get("recommendation") or {})
    assert rec.get("bias") in {"tighten_risk", "recalibrate_probability", "insufficient_samples"}


def test_feedback_coverage_plan_surfaces_gap_and_current_cycle_gain(
    tmp_path: Path,
) -> None:
    store = ResearchCaseStore(tmp_path)

    manager_id = "momentum"
    manager_config_ref = "momentum_v1"

    for i in range(5):
        _make_case(
            store,
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
            as_of=f"2026-03-1{i + 1}",
            regime="bull",
            hypothesis_id=f"bull_cov_{i}",
            label="hit",
        )
    for i in range(3):
        _make_case(
            store,
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
            as_of="2026-03-24",
            regime="bull",
            hypothesis_id=f"bull_today_{i}",
            label="hit",
        )
    for i in range(4):
        _make_case(
            store,
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
            as_of=f"2026-03-2{i + 1}",
            regime="bear",
            hypothesis_id=f"bear_cov_{i}",
            label="invalidated",
        )

    feedback = store.build_training_feedback(
        manager_id=manager_id,
        manager_config_ref=manager_config_ref,
        as_of_date="2026-03-24",
        regime="bear",
        limit=5,
        max_history_limit=200,
    )

    coverage = dict(feedback.get("coverage_plan") or {})
    current_cycle = dict(coverage.get("current_cycle_contribution") or {})
    regime_targets = dict(coverage.get("regime_targets") or {})

    assert coverage.get("schema_version") == "research.feedback_coverage_plan.v1"
    assert coverage.get("requested_regime") == "bear"
    assert coverage.get("requested_regime_ready") is False
    assert int(coverage.get("requested_regime_gap_count") or 0) == 4
    assert "bear" in list(coverage.get("next_target_regimes") or [])
    assert int(dict(regime_targets.get("bear") or {}).get("sample_count") or 0) == 4
    assert int(dict(regime_targets.get("bear") or {}).get("gap_count") or 0) == 4
    assert int(current_cycle.get("sample_count") or 0) == 4
    assert dict(current_cycle.get("regime_counts") or {}) == {"bear": 1, "bull": 3}
    assert int(current_cycle.get("requested_regime_sample_count") or 0) == 1

    summary = TrainingFeedbackService.research_feedback_summary(feedback)
    brief = TrainingFeedbackService.research_feedback_brief(feedback)
    assert summary.get("coverage_ready") is False
    assert int(summary.get("requested_regime_gap_count") or 0) == 4
    assert summary.get("next_target_regimes") and "bear" in summary.get("next_target_regimes")
    assert int(summary.get("current_cycle_requested_regime_gain") or 0) == 1
    assert brief.get("coverage_ready") is False
    assert int(brief.get("requested_regime_gap_count") or 0) == 4


def test_list_cases_and_attributions_cache_hit_and_invalidation(monkeypatch, tmp_path: Path) -> None:
    store = ResearchCaseStore(tmp_path)
    manager_id = "momentum"
    manager_config_ref = "momentum_v1"

    _make_case(
        store,
        manager_id=manager_id,
        manager_config_ref=manager_config_ref,
        as_of="2026-01-01",
        regime="bull",
        hypothesis_id="cache_0",
        label="hit",
    )

    original_read_text = Path.read_text
    read_counter = {"case": 0, "attribution": 0}

    def _tracking_read_text(path: Path, *args, **kwargs):
        if path.parent == store.case_dir and path.name.startswith("case_"):
            read_counter["case"] += 1
        if path.parent == store.attribution_dir and path.name.startswith("attribution_"):
            read_counter["attribution"] += 1
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _tracking_read_text)

    first_cases = store.list_cases()
    first_attributions = store.list_attributions()
    assert len(first_cases) == 1
    assert len(first_attributions) == 1

    case_reads_after_first = int(read_counter["case"])
    attr_reads_after_first = int(read_counter["attribution"])
    assert case_reads_after_first >= 1
    assert attr_reads_after_first >= 1

    # Returned payloads should not mutate internal cache content.
    first_cases[0]["snapshot"]["as_of_date"] = "1900-01-01"
    first_attributions[0]["attribution"]["thesis_result"] = "mutated"

    second_cases = store.list_cases()
    second_attributions = store.list_attributions()
    assert read_counter["case"] == case_reads_after_first
    assert read_counter["attribution"] == attr_reads_after_first
    assert second_cases[0]["snapshot"]["as_of_date"] == "2026-01-01"
    assert second_attributions[0]["attribution"]["thesis_result"] == "unknown"

    _make_case(
        store,
        manager_id=manager_id,
        manager_config_ref=manager_config_ref,
        as_of="2026-01-02",
        regime="bull",
        hypothesis_id="cache_1",
        label="invalidated",
    )
    refreshed_cases = store.list_cases()
    refreshed_attributions = store.list_attributions()
    assert len(refreshed_cases) == 2
    assert len(refreshed_attributions) == 2
    assert read_counter["case"] > case_reads_after_first
    assert read_counter["attribution"] > attr_reads_after_first


def test_iter_case_attribution_records_cache_hit_and_invalidation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = ResearchCaseStore(tmp_path)
    manager_id = "momentum"
    manager_config_ref = "momentum_v1"

    for i in range(3):
        _make_case(
            store,
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
            as_of=f"2026-01-0{i + 1}",
            regime="bull" if i < 2 else "bear",
            hypothesis_id=f"iter_{i}",
            label="hit" if i < 2 else "invalidated",
        )

    original_compute = store._compute_case_attribution_records
    compute_counter = {"count": 0}

    def _tracking_compute(*args, **kwargs):
        compute_counter["count"] += 1
        return original_compute(*args, **kwargs)

    monkeypatch.setattr(store, "_compute_case_attribution_records", _tracking_compute)

    first = list(
        store._iter_case_attribution_records(
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
            regime="bull",
        )
    )
    assert len(first) == 2
    assert compute_counter["count"] == 1

    second = list(
        store._iter_case_attribution_records(
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
            regime="bull",
        )
    )
    assert len(second) == len(first)
    assert second == first
    assert compute_counter["count"] == 1

    third = list(
        store._iter_case_attribution_records(
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
            regime="bear",
        )
    )
    assert len(third) == 1
    assert compute_counter["count"] == 2

    _make_case(
        store,
        manager_id=manager_id,
        manager_config_ref=manager_config_ref,
        as_of="2026-01-04",
        regime="bull",
        hypothesis_id="iter_3",
        label="hit",
    )
    refreshed = list(
        store._iter_case_attribution_records(
            manager_id=manager_id,
            manager_config_ref=manager_config_ref,
            regime="bull",
        )
    )
    assert len(refreshed) == len(first) + 1
    assert compute_counter["count"] == 3
