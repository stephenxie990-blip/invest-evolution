import pandas as pd

from invest.foundation.engine import SimulatedTrader
from invest.foundation.risk import DynamicStopLoss, sanitize_risk_params


RISK_POLICY = {
    "clamps": {
        "stop_loss_pct": {"min": 0.03, "max": 0.10},
        "take_profit_pct": {"min": 0.08, "max": 0.25},
        "position_size": {"min": 0.10, "max": 0.35},
    },
    "dynamic_stop": {
        "atr_period": 5,
        "stop_loss_atr_multiplier": 1.0,
        "take_profit_atr_multiplier": 2.0,
        "trailing_atr_multiplier": 0.5,
    },
    "portfolio": {
        "max_drawdown_to_reduce": 0.05,
        "max_drawdown_to_close": 0.10,
        "market_ma_period": 3,
        "bull_threshold": 1.01,
        "bear_threshold": 0.99,
        "max_industry_pct": 0.25,
        "max_correlation": 0.55,
    },
    "emergency": {
        "single_stock_crash_pct": -5.0,
        "rapid_loss_pct": -4.0,
        "rapid_loss_days": 2,
    },
}


def test_sanitize_risk_params_uses_policy_clamps():
    clean = sanitize_risk_params(
        {"stop_loss_pct": 0.20, "take_profit_pct": 0.01, "position_size": 0.50},
        policy=RISK_POLICY,
    )
    assert clean["stop_loss_pct"] == 0.10
    assert clean["take_profit_pct"] == 0.08
    assert clean["position_size"] == 0.35


def test_risk_controller_uses_configured_dynamic_stop_policy():
    df = pd.DataFrame(
        {
            "high": [10, 12, 14, 16, 18, 20],
            "low": [9, 10, 12, 14, 16, 18],
            "close": [9.5, 11, 13, 15, 17, 19],
        }
    )
    dynamic_stop = DynamicStopLoss(policy=RISK_POLICY)
    levels = dynamic_stop.get_stop_levels("AAA", entry_price=20.0, current_price=22.0, df=df)
    atr = dynamic_stop.calculate_atr(df)

    assert dynamic_stop.atr_period == 5
    assert levels["stop_loss"] == 20.0 - atr
    assert levels["take_profit"] == 20.0 + 2.0 * atr
    assert levels["trailing_stop"] == max(22.0 - 0.5 * atr, 20.0)


def test_simulated_trader_wires_risk_policy_into_controllers():
    trader = SimulatedTrader(enable_risk_control=True, risk_policy=RISK_POLICY)
    assert trader.risk_controller.dynamic_stop.atr_period == 5
    assert trader.emergency_detector.rapid_loss_days == 2
    assert trader.risk_controller.portfolio_risk.max_industry_pct == 0.25


def test_risk_controller_marks_explicit_policy_source():
    trader = SimulatedTrader(enable_risk_control=True, risk_policy=RISK_POLICY)
    assert trader.risk_controller.policy_source == 'explicit'
    assert trader.risk_controller.dynamic_stop.policy_source == 'explicit'
    assert trader.emergency_detector.policy_source == 'explicit'


def test_risk_controller_marks_safety_fallback_when_policy_missing():
    trader = SimulatedTrader(enable_risk_control=True, risk_policy=None)
    assert trader.risk_controller.policy_source == 'safety_fallback'
    assert trader.risk_controller.dynamic_stop.policy_source == 'safety_fallback'
    assert trader.emergency_detector.policy_source == 'safety_fallback'
