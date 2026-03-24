from __future__ import annotations

from collections import Counter
from typing import cast

import pandas as pd
import pytest

# Factor Imports
from invest_evolution.investment.factors.core import build_factor_audit_inventory
from invest_evolution.investment.factors.core import (
    FACTOR_AUDIT_CONTRACT_VERSION,
    FACTOR_REGISTRY_CONTRACT_VERSION,
    FACTOR_REGISTRY_SNAPSHOT_CONTRACT_VERSION,
)
from invest_evolution.investment.factors.core import (
    allowed_actions,
    apply_lifecycle_action,
    build_lifecycle_transition_table,
)
from invest_evolution.investment.factors.core import (
    build_factor_registry_snapshot,
    factors_for_consumer,
    lookup_factor,
)

# Indicator/Compute Imports
from invest_evolution.investment.foundation.compute import (
    RollingWindow,
    SimpleMovingAverageIndicator,
    build_batch_indicator_snapshot,
    calc_rsi,
    compute_indicator_snapshot,
    summarize_stock_batches,
)


# --- Helper Functions ---

def _factor_with_status(status: str):
    seed = build_factor_audit_inventory().factor_map()["reversion_score"]
    return seed.model_copy(update={"status": status})


# --- Factor Audit Tests ---

def test_factor_audit_inventory_validates_seed_contract() -> None:
    inventory = build_factor_audit_inventory()
    assert inventory.contract_version == FACTOR_AUDIT_CONTRACT_VERSION
    assert inventory.registry_contract_version == FACTOR_REGISTRY_CONTRACT_VERSION
    assert len(inventory.factors) >= 30


def test_factor_audit_inventory_covers_core_factor_domains() -> None:
    inventory = build_factor_audit_inventory()
    category_counts = Counter(item.category for item in inventory.factors)
    assert category_counts["derived_feature"] >= 10
    assert category_counts["strategy_factor"] >= 4


def test_factor_audit_inventory_tracks_dependencies_and_consumers() -> None:
    inventory = build_factor_audit_inventory()
    factor_map = {item.factor_id: item for item in inventory.factors}
    reversion = factor_map["reversion_score"]
    assert "change_5d_pct" in reversion.depends_on
    assert "invest.runtimes.mean_reversion.MeanReversionRuntime" in reversion.producers


# --- Factor Lifecycle Tests ---

def test_factor_lifecycle_happy_path_reaches_active() -> None:
    draft = _factor_with_status("draft")
    candidate, _ = apply_lifecycle_action(draft, action="submit_candidate", effective_on="20260318", actor="p1")
    shadowed, _ = apply_lifecycle_action(candidate, action="start_shadow", effective_on="20260318", actor="p1")
    active, _ = apply_lifecycle_action(shadowed, action="activate", effective_on="20260318", actor="p1")
    assert active.status == "active"


def test_factor_lifecycle_blocks_invalid_transition() -> None:
    active = _factor_with_status("active")
    with pytest.raises(ValueError, match="invalid lifecycle transition"):
        apply_lifecycle_action(active, action="start_shadow", effective_on="20260318", actor="p1")


def test_factor_lifecycle_transition_table_and_allowed_actions() -> None:
    transitions = build_lifecycle_transition_table()
    assert transitions["draft"]["submit_candidate"] == "candidate"
    assert allowed_actions("candidate") == ["reject", "reopen_draft", "start_shadow"]


# --- Factor Registry Tests ---

def test_factor_registry_snapshot_builds_from_audit_inventory() -> None:
    inventory = build_factor_audit_inventory()
    snapshot = build_factor_registry_snapshot(inventory)
    assert snapshot.contract_version == FACTOR_REGISTRY_SNAPSHOT_CONTRACT_VERSION
    assert len(snapshot.entries) == len(inventory.factors)


def test_factor_registry_lookup_and_consumer_index() -> None:
    snapshot = build_factor_registry_snapshot()
    record = lookup_factor(snapshot, "reversion_score")
    assert record is not None
    selection_factors = factors_for_consumer(snapshot, "invest_evolution.application.training.execution.TrainingSelectionService")
    assert any(f.factor_id == "reversion_score" for f in selection_factors)


# --- Indicators V2 Tests ---

def test_rolling_window_keeps_latest_first():
    window = RollingWindow[int](3)
    for i in range(1, 5):
        window.add(i)
    assert window.to_list() == [4, 3, 2]


def test_numeric_indicators_reach_ready_state():
    sma = SimpleMovingAverageIndicator(3)
    for value in [10.0, 11.0, 12.0, 13.0, 14.0]:
        sma.update(value)
    assert sma.is_ready is True
    assert round(float(sma.current), 2) == 13.0


def test_compute_indicator_snapshot_returns_core_metrics():
    frame = pd.DataFrame([{"trade_date": f"202401{i:02d}", "close": 10 + i * 0.15, "volume": 1000} for i in range(1, 91)])
    snapshot = compute_indicator_snapshot(frame)
    assert snapshot["ready"] is True
    assert "rsi_14" in snapshot["indicators"]


def test_compute_indicator_snapshot_reuses_default_registry(monkeypatch):
    import invest_evolution.investment.foundation.compute as indicators_module
    builds = {"count": 0}
    original_build = indicators_module.IndicatorRegistry._build_default_registry
    def counted_build():
        builds["count"] += 1
        return original_build()
    monkeypatch.setattr(indicators_module.IndicatorRegistry, "_build_default_registry", staticmethod(counted_build))
    if hasattr(indicators_module._INDICATOR_REGISTRY_LOCAL, "default_registry"):
        delattr(indicators_module._INDICATOR_REGISTRY_LOCAL, "default_registry")
    frame = pd.DataFrame([{"close": 10 + i} for i in range(1, 91)])
    compute_indicator_snapshot(frame)
    compute_indicator_snapshot(frame)
    assert builds["count"] == 1


def test_legacy_indicator_functions_align_with_v2_snapshot_core_values():
    frame = pd.DataFrame([{"trade_date": f"202402{i:02d}", "close": 20 + i * 0.25, "volume": 5000} for i in range(1, 91)])
    close = cast(pd.Series, frame["close"])
    snapshot = compute_indicator_snapshot(frame)
    assert snapshot["indicators"]["rsi_14"] == round(calc_rsi(close, 14), 6)


def test_batch_snapshot_adapter_projects_v2_snapshot_into_legacy_summary_fields():
    frame = pd.DataFrame([{"trade_date": f"202403{i:02d}", "close": 30 + i * 0.22, "volume": 8000} for i in range(1, 91)])
    batch = build_batch_indicator_snapshot(frame, "20240331")
    assert batch is not None
    assert batch.ma_trend in {"多头", "空头", "交叉"}


def test_summarize_stock_batches_keeps_batch_and_summary_in_one_ranked_pass():
    frame = pd.DataFrame([{"trade_date": f"202404{i:02d}", "close": 12 + i * 0.14, "volume": 3000} for i in range(1, 61)])
    items = summarize_stock_batches({"AAA": frame}, ["AAA"], "20240430")
    assert len(items) == 1
    assert items[0].code == "AAA"
