from __future__ import annotations

COMMON_PARAM_DEFAULTS = {
    "candidate_pool_size": 100,
    "top_n": 5,
    "max_positions": 4,
    "cash_reserve": 0.30,
    "stop_loss_pct": 0.05,
    "take_profit_pct": 0.15,
    "trailing_pct": 0.10,
    "position_size": 0.20,
    "max_hold_days": 30,
}

COMMON_RISK_DEFAULTS = {
    "stop_loss_pct": 0.05,
    "take_profit_pct": 0.15,
    "trailing_pct": 0.10,
}

COMMON_EXECUTION_DEFAULTS = {
    "initial_capital": 100000,
    "commission_rate": 0.00025,
    "stamp_tax_rate": 0.0005,
    "slippage_rate": 0.002,
}

COMMON_BENCHMARK_DEFAULTS = {
    "risk_free_rate": 0.03,
    "criteria": {
        "excess_return": 0.0,
        "sharpe_ratio": 1.0,
        "max_drawdown": 15.0,
        "calmar_ratio": 1.5,
        "win_rate": 0.45,
        "profit_loss_ratio": 1.5,
        "monthly_turnover": 3.0,
    },
}
