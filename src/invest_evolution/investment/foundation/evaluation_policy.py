from __future__ import annotations

import math
from typing import Any, Dict, Iterable

import numpy as np


_BUY_ACTIONS = {"BUY", "买入"}
_SELL_ACTIONS = {"SELL", "卖出"}
_RISK_EXIT_TRIGGERS = {"stop_loss", "take_profit", "trailing_stop"}


def _normalized_action(record: Dict[str, Any]) -> str:
    return str(record.get("action") or "").strip()


def realized_trade_records(
    trade_history: Iterable[Dict[str, Any]] | None,
) -> list[Dict[str, Any]]:
    records = [dict(item) for item in list(trade_history or [])]
    realized: list[Dict[str, Any]] = []
    for item in records:
        action = _normalized_action(item)
        if action:
            if action in _SELL_ACTIONS:
                realized.append(item)
            continue
        realized.append(item)
    return realized


def entry_trade_records(
    trade_history: Iterable[Dict[str, Any]] | None,
) -> list[Dict[str, Any]]:
    records = [dict(item) for item in list(trade_history or [])]
    return [
        item
        for item in records
        if _normalized_action(item) in _BUY_ACTIONS
    ]


def compute_signal_accuracy(
    trade_history: Iterable[Dict[str, Any]] | None,
    *,
    default_score: float,
) -> float:
    realized = realized_trade_records(trade_history)
    if not realized:
        return float(default_score)
    wins = sum(1 for item in realized if float(item.get("pnl", 0.0) or 0.0) > 0)
    return wins / len(realized)


def compute_risk_control_score(
    trade_history: Iterable[Dict[str, Any]] | None,
    *,
    base_score: float,
    default_score: float,
) -> float:
    realized = realized_trade_records(trade_history)
    if not realized:
        return float(default_score)
    stop_take_profit_count = 0
    for item in realized:
        exit_trigger = str(item.get("exit_trigger") or "").strip().lower()
        reason = str(item.get("exit_reason") or item.get("reason") or "")
        if exit_trigger in _RISK_EXIT_TRIGGERS:
            stop_take_profit_count += 1
            continue
        if any(token in reason for token in ("止损", "止盈", "跟踪止盈")):
            stop_take_profit_count += 1
    return min(1.0, stop_take_profit_count / len(realized) + float(base_score))


def compute_monthly_turnover(
    trade_history: Iterable[Dict[str, Any]] | None,
    *,
    daily_values: Iterable[float] | None,
    trading_days_per_month: int = 21,
) -> float:
    records = [dict(item) for item in list(trade_history or [])]
    values = [
        float(item)
        for item in list(daily_values or [])
        if item is not None and math.isfinite(float(item)) and float(item) > 0
    ]
    if not records or not values:
        return 0.0
    avg_value = float(np.mean(values))
    if avg_value <= 0:
        return 0.0
    total_notional = 0.0
    for item in records:
        amount = item.get("amount")
        if amount is not None:
            try:
                numeric_amount = abs(float(amount))
            except (TypeError, ValueError):
                numeric_amount = 0.0
            if numeric_amount > 0:
                total_notional += numeric_amount
                continue
        try:
            total_notional += abs(
                float(item.get("price", 0.0) or 0.0)
                * float(item.get("shares", 0.0) or 0.0)
            )
        except (TypeError, ValueError):
            continue
    months = max(len(values) / max(1, int(trading_days_per_month)), 1.0)
    return total_notional / avg_value / months


def compute_max_drawdown_pct(values: Iterable[float]) -> float:
    seq = [float(item) for item in list(values)]
    if not seq:
        return 0.0
    peak = seq[0]
    max_drawdown = 0.0
    for value in seq:
        if value > peak:
            peak = value
        if peak <= 0:
            continue
        max_drawdown = max(max_drawdown, (peak - value) / peak * 100)
    return max_drawdown
