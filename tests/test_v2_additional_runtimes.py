import numpy as np
import pandas as pd
import pytest
import yaml
from pathlib import Path
from types import SimpleNamespace

from invest_evolution.investment.runtimes import (
    DefensiveLowVolRuntime,
    MeanReversionRuntime,
    MomentumRuntime,
    ValueQualityRuntime,
    list_manager_runtime_ids,
)
from invest_evolution.investment.runtimes import styles as runtime_styles_module


def _make_stock_data(n=8, days=120):
    dates = pd.date_range("2023-01-01", periods=days, freq="B")
    stock_data = {}
    rng = np.random.default_rng(7)
    for i in range(n):
        code = f"sh.{600100 + i}"
        drift = -0.01 if i % 2 == 0 else 0.03
        close = 12 + np.cumsum(rng.normal(drift, 0.35, len(dates)))
        close = np.maximum(close, 1)
        stock_data[code] = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "trade_date": dates.strftime("%Y%m%d"),
            "open": close * 0.998,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": rng.integers(100000, 10000000, len(dates)).astype(float),
            "pct_chg": pd.Series(close).pct_change().fillna(0) * 100,
            "pe_ttm": np.full(len(dates), 8 + i * 2, dtype=float),
            "pb": np.full(len(dates), 0.8 + i * 0.25, dtype=float),
            "roe": np.full(len(dates), 10 + i, dtype=float),
            "market_cap": np.full(len(dates), 50e8 + i * 5e8, dtype=float),
        })
    return stock_data


def test_mean_reversion_runtime_outputs_dual_channel():
    runtime = MeanReversionRuntime(runtime_overrides={"top_n": 4, "max_positions": 3})
    out = runtime.process(_make_stock_data(), "20230601")

    assert out.manager_id == "mean_reversion"
    assert out.signal_packet.manager_id == "mean_reversion"
    assert out.signal_packet.max_positions in {1, 3}
    assert len(out.signal_packet.signals) >= 1
    assert out.agent_context.candidate_codes
    assert any("reversion_score" in signal.factor_values for signal in out.signal_packet.signals)


def test_value_quality_runtime_outputs_dual_channel():
    runtime = ValueQualityRuntime(runtime_overrides={"top_n": 4, "max_positions": 3})
    out = runtime.process(_make_stock_data(), "20230601")

    assert out.manager_id == "value_quality"
    assert out.signal_packet.manager_id == "value_quality"
    assert out.signal_packet.max_positions == 3
    assert len(out.signal_packet.signals) >= 1
    assert out.agent_context.candidate_codes
    assert any("value_quality_score" in signal.factor_values for signal in out.signal_packet.signals)


def test_runtime_registry_includes_all_manager_runtimes():
    runtime_ids = list_manager_runtime_ids()
    assert "momentum" in runtime_ids
    assert "mean_reversion" in runtime_ids
    assert "value_quality" in runtime_ids
    assert "defensive_low_vol" in runtime_ids
    assert MeanReversionRuntime.default_config_relpath == "configs/mean_reversion_v1.yaml"
    assert ValueQualityRuntime.default_config_relpath == "configs/value_quality_v1.yaml"
    assert DefensiveLowVolRuntime.default_config_relpath == "configs/defensive_low_vol_v1.yaml"


def test_momentum_runtime_signal_threshold_filters_candidates():
    stock_data = _make_stock_data()

    permissive = MomentumRuntime(runtime_overrides={"top_n": 4, "max_positions": 3, "signal_threshold": 0.0})
    restrictive = MomentumRuntime(runtime_overrides={"top_n": 4, "max_positions": 3, "signal_threshold": 1.1})

    permissive_out = permissive.process(stock_data, "20230601")
    restrictive_out = restrictive.process(stock_data, "20230601")

    assert permissive_out.signal_packet.signals
    assert permissive_out.signal_packet.selected_codes
    assert restrictive_out.signal_packet.signals == []
    assert restrictive_out.signal_packet.selected_codes == []
    assert restrictive_out.agent_context.candidate_codes == []



def test_defensive_low_vol_runtime_outputs_dual_channel():
    runtime = DefensiveLowVolRuntime(runtime_overrides={"top_n": 4, "max_positions": 3})
    out = runtime.process(_make_stock_data(), "20230601")

    assert out.manager_id == "defensive_low_vol"
    assert out.signal_packet.manager_id == "defensive_low_vol"
    assert out.signal_packet.max_positions in {2, 3}
    assert len(out.signal_packet.signals) >= 1
    assert out.agent_context.candidate_codes
    assert any("defensive_score" in signal.factor_values for signal in out.signal_packet.signals)


@pytest.mark.parametrize(
    ("runtime_cls", "runtime_overrides"),
    [
        (MeanReversionRuntime, {"top_n": 4, "max_positions": 3, "min_reversion_score": 2.0}),
        (ValueQualityRuntime, {"top_n": 4, "max_positions": 3, "min_value_quality_score": 1.1}),
        (DefensiveLowVolRuntime, {"top_n": 4, "max_positions": 3, "min_defensive_score": 2.0}),
    ],
)
def test_thresholded_runtimes_do_not_fallback_to_unfiltered_candidates(runtime_cls, runtime_overrides):
    runtime = runtime_cls(runtime_overrides=runtime_overrides)
    out = runtime.process(_make_stock_data(), "20230601")

    assert out.signal_packet.signals == []
    assert out.signal_packet.selected_codes == []
    assert out.agent_context.candidate_codes == []


def test_mean_reversion_runtime_applies_oscillation_guards(monkeypatch):
    monkeypatch.setattr(
        runtime_styles_module,
        "compute_market_stats",
        lambda *_args, **_kwargs: {
            "regime_hint": "oscillation",
            "avg_volatility": 0.02,
            "market_breadth": 0.5,
            "avg_change_20d": 0.0,
        },
    )
    monkeypatch.setattr(
        runtime_styles_module,
        "summarize_stock_batches",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                code="sh.600100",
                rsi=31.0,
                bb_pos=0.08,
                change_5d=-8.5,
                change_20d=-12.0,
                vol_ratio=1.0,
                volatility=0.03,
                ma_trend="空头",
                macd="bearish",
                summary={
                    "code": "sh.600100",
                    "rsi": 31.0,
                    "bb_pos": 0.08,
                    "change_5d": -8.5,
                    "change_20d": -12.0,
                    "vol_ratio": 1.0,
                    "volatility": 0.03,
                    "ma_trend": "空头",
                    "macd": "bearish",
                },
            ),
            SimpleNamespace(
                code="sh.600101",
                rsi=24.0,
                bb_pos=0.10,
                change_5d=-4.0,
                change_20d=-8.0,
                vol_ratio=1.1,
                volatility=0.02,
                ma_trend="多头",
                macd="gold_cross",
                summary={
                    "code": "sh.600101",
                    "rsi": 24.0,
                    "bb_pos": 0.10,
                    "change_5d": -4.0,
                    "change_20d": -8.0,
                    "vol_ratio": 1.1,
                    "volatility": 0.02,
                    "ma_trend": "多头",
                    "macd": "gold_cross",
                },
            ),
            SimpleNamespace(
                code="sh.600102",
                rsi=22.0,
                bb_pos=0.09,
                change_5d=1.5,
                change_20d=-4.0,
                vol_ratio=1.0,
                volatility=0.02,
                ma_trend="多头",
                macd="gold_cross",
                summary={
                    "code": "sh.600102",
                    "rsi": 22.0,
                    "bb_pos": 0.09,
                    "change_5d": 1.5,
                    "change_20d": -4.0,
                    "vol_ratio": 1.0,
                    "volatility": 0.02,
                    "ma_trend": "多头",
                    "macd": "gold_cross",
                },
            ),
            SimpleNamespace(
                code="sh.600103",
                rsi=23.0,
                bb_pos=0.11,
                change_5d=-3.6,
                change_20d=-4.5,
                vol_ratio=1.1,
                volatility=0.02,
                ma_trend="多头",
                macd="bearish",
                summary={
                    "code": "sh.600103",
                    "rsi": 23.0,
                    "bb_pos": 0.11,
                    "change_5d": -3.6,
                    "change_20d": -4.5,
                    "vol_ratio": 1.1,
                    "volatility": 0.02,
                    "ma_trend": "多头",
                    "macd": "bearish",
                },
            ),
        ],
    )

    runtime = MeanReversionRuntime(runtime_overrides={"top_n": 4, "max_positions": 3})
    out = runtime.process(_make_stock_data(), "20230601")

    assert out.signal_packet.regime == "oscillation"
    assert out.signal_packet.max_positions == 1
    assert out.signal_packet.cash_reserve >= 0.65
    assert out.signal_packet.selected_codes == ["sh.600101"]
    debug = dict(out.signal_packet.context.debug_metadata or {})
    assert debug.get("runtime_profile") == "oscillation"
    assert debug.get("resolved_profile_source") == "regime_profiles"
    assert dict(debug.get("applied_profile_params") or {}).get("max_positions") == 1
    assert dict(debug.get("applied_profile_filters") or {}).get("entry_5d_ceiling") == 0.0


def test_defensive_runtime_applies_bear_quality_guards(monkeypatch):
    monkeypatch.setattr(
        runtime_styles_module,
        "compute_market_stats",
        lambda *_args, **_kwargs: {
            "regime_hint": "bear",
            "avg_volatility": 0.03,
            "market_breadth": 0.3,
            "above_ma20_ratio": 0.35,
        },
    )
    monkeypatch.setattr(
        runtime_styles_module,
        "summarize_stock_batches",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                code="sh.600200",
                rsi=50.0,
                bb_pos=0.45,
                change_5d=-0.5,
                change_20d=-3.5,
                vol_ratio=1.0,
                volatility=0.03,
                ma_trend="空头",
                macd="bearish",
                summary={
                    "code": "sh.600200",
                    "rsi": 50.0,
                    "bb_pos": 0.45,
                    "change_5d": -0.5,
                    "change_20d": -3.5,
                    "vol_ratio": 1.0,
                    "volatility": 0.03,
                    "ma_trend": "空头",
                    "macd": "bearish",
                },
            ),
            SimpleNamespace(
                code="sh.600201",
                rsi=51.0,
                bb_pos=0.48,
                change_5d=0.6,
                change_20d=1.8,
                vol_ratio=1.1,
                volatility=0.018,
                ma_trend="多头",
                macd="bullish",
                summary={
                    "code": "sh.600201",
                    "rsi": 51.0,
                    "bb_pos": 0.48,
                    "change_5d": 0.6,
                    "change_20d": 1.8,
                    "vol_ratio": 1.1,
                    "volatility": 0.018,
                    "ma_trend": "多头",
                    "macd": "bullish",
                },
            ),
        ],
    )

    runtime = DefensiveLowVolRuntime(runtime_overrides={"top_n": 4, "max_positions": 3})
    out = runtime.process(_make_stock_data(), "20230601")

    assert out.signal_packet.regime == "bear"
    assert out.signal_packet.max_positions == 2
    assert out.signal_packet.cash_reserve >= 0.48
    assert out.signal_packet.selected_codes == ["sh.600201"]
    debug = dict(out.signal_packet.context.debug_metadata or {})
    assert debug.get("runtime_profile") == "bear"
    assert debug.get("resolved_profile_source") == "regime_profiles"
    assert dict(debug.get("applied_profile_params") or {}).get("max_positions") == 2
    assert dict(debug.get("applied_profile_filters") or {}).get("max_volatility_guard") == 0.025


def test_defensive_runtime_regime_profile_falls_back_to_legacy_prefix(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(
        runtime_styles_module,
        "compute_market_stats",
        lambda *_args, **_kwargs: {
            "regime_hint": "bear",
            "avg_volatility": 0.03,
            "market_breadth": 0.3,
            "above_ma20_ratio": 0.35,
        },
    )
    monkeypatch.setattr(
        runtime_styles_module,
        "summarize_stock_batches",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                code="sh.600201",
                rsi=51.0,
                bb_pos=0.48,
                change_5d=0.6,
                change_20d=1.8,
                vol_ratio=1.1,
                volatility=0.018,
                ma_trend="多头",
                macd="bullish",
                summary={
                    "code": "sh.600201",
                    "rsi": 51.0,
                    "bb_pos": 0.48,
                    "change_5d": 0.6,
                    "change_20d": 1.8,
                    "vol_ratio": 1.1,
                    "volatility": 0.018,
                    "ma_trend": "多头",
                    "macd": "bullish",
                },
            ),
        ],
    )

    default_path = DefensiveLowVolRuntime.resolve_runtime_config_ref(None)
    config_payload = yaml.safe_load(Path(default_path).read_text(encoding="utf-8"))
    config_payload.pop("regime_profiles", None)
    config_path = Path(default_path).parent / "_defensive_low_vol_legacy_test.yaml"
    try:
        config_path.write_text(
            yaml.safe_dump(config_payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        runtime = DefensiveLowVolRuntime(runtime_config_ref=config_path)
        out = runtime.process(_make_stock_data(), "20230601")

        assert out.signal_packet.regime == "bear"
        assert out.signal_packet.max_positions == 2
        assert out.signal_packet.cash_reserve >= 0.48
        debug = dict(out.signal_packet.context.debug_metadata or {})
        assert debug.get("resolved_profile_source") == "legacy_prefix"
        assert dict(debug.get("applied_profile_params") or {}).get("max_positions") == 2
    finally:
        config_path.unlink(missing_ok=True)
