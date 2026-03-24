from typing import Any, cast

from invest_evolution.investment.research.analysis import PolicySnapshot, ResearchSnapshot
from invest_evolution.investment.research.analysis import build_research_hypothesis


def test_hypothesis_prefers_canonical_snapshot_fields():
    snapshot = ResearchSnapshot(
        snapshot_id="snapshot_test",
        as_of_date="20240130",
        scope="single_security",
        security={"code": "sh.600001"},
        cross_section_context={
            "selected_by_policy": True,
            "percentile": 0.82,
            "rank": 3,
            "threshold_gap": 0.12,
        },
        feature_snapshot={
            "summary": {"close": 12.34},
            "signal": {"evidence": ["量价配合"]},
            "factor_values": {"rsi": 62.0},
            "metadata": {
                "flags": {"趋势向上": True, "逼近阻力": False},
                "matched_signals": ["多头排列"],
                "latest_close": 12.34,
            },
            "evidence": ["突破后缩量回踩"],
        },
    )
    policy = PolicySnapshot(policy_id="policy_test", manager_id="momentum", manager_config_ref="momentum_v1")

    hypothesis = build_research_hypothesis(
        snapshot=snapshot,
        policy=policy,
        scenario={"horizons": {"T+20": {"positive_return_probability": 0.7, "interval": {"low": 0.03, "high": 0.12}}}},
        strategy_name="momentum",
        strategy_display_name="Momentum",
    )

    assert hypothesis.entry_rule["price"] == 12.22
    assert "多头排列" in hypothesis.supporting_factors
    assert "趋势向上" in hypothesis.supporting_factors
    assert "逼近阻力" in hypothesis.contradicting_factors


def test_snapshot_builder_promotes_derived_fields_into_canonical_metadata():
    from types import SimpleNamespace
    from invest_evolution.investment.research.artifacts import build_research_snapshot

    signal_packet = SimpleNamespace(
        context=SimpleNamespace(
            market_stats={"market_breadth": 0.72},
            stock_summaries=[{"code": "sh.600001", "algo_score": 0.81, "close": 10.5}],
            raw_summaries=[{"code": "sh.600001", "algo_score": 0.8, "close": 10.5}],
        ),
        metadata={"raw_summaries": [{"code": "sh.600099", "algo_score": 0.1, "close": 1.0}]},
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        regime="bull",
        cash_reserve=0.2,
        signals=[SimpleNamespace(code="sh.600001", to_dict=lambda: {"score": 0.9, "evidence": ["强势放量"], "factor_values": {}})],
        selected_codes=["sh.600001"],
        as_of_date="20240130",
        reasoning="",
    )
    manager_output = SimpleNamespace(
        signal_packet=signal_packet,
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        agent_context=SimpleNamespace(summary=""),
    )

    snapshot = build_research_snapshot(
        manager_output=cast(Any, manager_output),
        security={"code": "sh.600001"},
        query_code="sh.600001",
        stock_data={"sh.600001": []},
        derived_signals={"flags": {"趋势向上": True}, "matched_signals": ["多头排列"], "latest_close": 10.5, "rsi": 48.0},
    )

    metadata = snapshot.feature_snapshot["metadata"]
    factor_values = snapshot.feature_snapshot["factor_values"]

    assert metadata["flags"]["趋势向上"] is True
    assert metadata["matched_signals"] == ["多头排列"]
    assert metadata["latest_close"] == 10.5
    assert factor_values["rsi"] == 48.0
    assert snapshot.market_context["market_stats"]["market_breadth"] == 0.72
    assert snapshot.universe["summary_top5"][0]["code"] == "sh.600001"

def test_snapshot_builder_discards_noncanonical_derived_fields():
    from types import SimpleNamespace
    from invest_evolution.investment.research.artifacts import build_research_snapshot

    signal_packet = SimpleNamespace(
        context=SimpleNamespace(
            market_stats={},
            stock_summaries=[{"code": "sh.600001", "algo_score": 0.8, "close": 10.5}],
            raw_summaries=[{"code": "sh.600001", "algo_score": 0.8, "close": 10.5}],
        ),
        metadata={"raw_summaries": [{"code": "sh.600099", "algo_score": 0.1, "close": 1.0}]},
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        regime="bull",
        cash_reserve=0.2,
        signals=[SimpleNamespace(code="sh.600001", to_dict=lambda: {"score": 0.9, "evidence": ["强势放量"], "factor_values": {}})],
        selected_codes=["sh.600001"],
        as_of_date="20240130",
        reasoning="",
    )
    manager_output = SimpleNamespace(
        signal_packet=signal_packet,
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        agent_context=SimpleNamespace(summary=""),
    )

    snapshot = build_research_snapshot(
        manager_output=cast(Any, manager_output),
        security={"code": "sh.600001"},
        query_code="sh.600001",
        stock_data={"sh.600001": []},
        derived_signals={
            "flags": {"趋势向上": True},
            "matched_signals": ["多头排列"],
            "latest_close": 10.5,
            "ma20": 10.1,
            "rsi": 48.0,
            "algo_score": 0.8,
            "structure": "uptrend",
            "unused_blob": {"foo": "bar"},
        },
    )

    assert snapshot.feature_snapshot["metadata"]["flags"] == {"趋势向上": True}
    assert snapshot.feature_snapshot["metadata"]["matched_signals"] == ["多头排列"]
    assert snapshot.feature_snapshot["metadata"]["latest_close"] == 10.5
    assert snapshot.feature_snapshot["factor_values"]["ma20"] == 10.1
    assert snapshot.feature_snapshot["factor_values"]["rsi"] == 48.0
    assert "legacy_signals" not in snapshot.feature_snapshot


def test_snapshot_builder_prefers_signal_packet_context_over_legacy_metadata():
    from types import SimpleNamespace
    from invest_evolution.investment.research.artifacts import build_research_snapshot

    signal_packet = SimpleNamespace(
        context=SimpleNamespace(
            market_stats={"market_breadth": 0.88},
            stock_summaries=[{"code": "sh.600001", "algo_score": 0.93, "close": 11.2}],
            raw_summaries=[{"code": "sh.600001", "algo_score": 0.93, "close": 11.2}],
        ),
        metadata={
            "market_stats": {"market_breadth": 0.11},
            "raw_summaries": [{"code": "sh.600099", "algo_score": 0.01, "close": 1.0}],
        },
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        regime="bull",
        cash_reserve=0.2,
        signals=[SimpleNamespace(code="sh.600001", to_dict=lambda: {"score": 0.9, "evidence": [], "factor_values": {}})],
        selected_codes=["sh.600001"],
        as_of_date="20240130",
        reasoning="",
    )
    manager_output = SimpleNamespace(
        signal_packet=signal_packet,
        manager_id="momentum",
        manager_config_ref="momentum_v1",
        agent_context=SimpleNamespace(summary=""),
    )

    snapshot = build_research_snapshot(
        manager_output=cast(Any, manager_output),
        security={"code": "sh.600001"},
        query_code="sh.600001",
        stock_data={"sh.600001": []},
        derived_signals={},
    )

    assert snapshot.market_context["market_stats"]["market_breadth"] == 0.88
    assert snapshot.universe["summary_top5"][0]["code"] == "sh.600001"
