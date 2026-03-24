from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

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
