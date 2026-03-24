from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Sequence

if TYPE_CHECKING:
    from .base import ManagerRuntime

COMMON_PARAM_DEFAULTS = {
    'candidate_pool_size': 100,
    'top_n': 5,
    'max_positions': 4,
    'cash_reserve': 0.30,
    'stop_loss_pct': 0.05,
    'take_profit_pct': 0.15,
    'trailing_pct': 0.10,
    'position_size': 0.20,
    'max_hold_days': 30,
}

COMMON_RISK_DEFAULTS = {
    'stop_loss_pct': 0.05,
    'take_profit_pct': 0.15,
    'trailing_pct': 0.10,
}

COMMON_EXECUTION_DEFAULTS = {
    'initial_capital': 100000,
    'commission_rate': 0.00025,
    'stamp_tax_rate': 0.0005,
    'slippage_rate': 0.002,
}

COMMON_BENCHMARK_DEFAULTS = {
    'risk_free_rate': 0.03,
    'criteria': {
        'excess_return': 0.0,
        'sharpe_ratio': 1.0,
        'max_drawdown': 15.0,
        'calmar_ratio': 1.5,
        'win_rate': 0.45,
        'profit_loss_ratio': 1.5,
        'monthly_turnover': 3.0,
    },
}

# Context rendering


def render_market_narrative(regime: str, market_stats: Dict[str, float], risk_hints: Iterable[str]) -> str:
    hints = list(risk_hints)
    breadth = market_stats.get("market_breadth", 0.0)
    avg_20d = market_stats.get("avg_change_20d", 0.0)
    above_ma20 = market_stats.get("above_ma20_ratio", 0.0)
    lines: List[str] = [
        f"当前市场大致处于 {regime} 状态。",
        f"市场广度 {breadth:.0%}，近20日平均涨跌幅 {avg_20d:+.2f}%，站上MA20比例 {above_ma20:.0%}。",
    ]
    if hints:
        lines.append("风险提示：" + "；".join(hints[:4]))
    return " ".join(lines)


def render_candidate_narrative(stock_summaries: Sequence[Mapping[str, Any]], top_codes: Sequence[str]) -> str:
    focus = [item for item in stock_summaries if item.get("code") in set(top_codes)]
    if not focus:
        focus = stock_summaries[:5]
    if not focus:
        return "当前没有满足条件的候选股票。"
    fragments = []
    for item in focus[:5]:
        fragments.append(
            f"{item['code']} 近5日{item['change_5d']:+.1f}% / 近20日{item['change_20d']:+.1f}% / RSI {item['rsi']:.0f} / MACD {item['macd']}"
        )
    return "候选重点：" + "；".join(fragments)


# Runtime registry



def _runtime_registry() -> dict[str, type["ManagerRuntime"]]:
    from .styles import (
        DefensiveLowVolRuntime,
        MeanReversionRuntime,
        MomentumRuntime,
        ValueQualityRuntime,
    )

    return {
        'momentum': MomentumRuntime,
        'mean_reversion': MeanReversionRuntime,
        'value_quality': ValueQualityRuntime,
        'defensive_low_vol': DefensiveLowVolRuntime,
    }


def list_manager_runtime_ids() -> list[str]:
    return sorted(_runtime_registry())


def resolve_manager_runtime_config_ref(manager_id: str) -> Path:
    key = str(manager_id or 'momentum').strip().lower()
    runtime_cls = _runtime_registry().get(key)
    if runtime_cls is None:
        raise ValueError(f'Unknown manager runtime: {manager_id}')
    return runtime_cls.resolve_runtime_config_ref(None)


def create_manager_runtime(
    manager_id: str,
    runtime_config_ref: str | Path | None = None,
    runtime_overrides: Optional[Dict[str, Any]] = None,
) -> "ManagerRuntime":
    key = str(manager_id or 'momentum').strip().lower()
    runtime_cls = _runtime_registry().get(key)
    if runtime_cls is None:
        raise ValueError(f'Unknown manager runtime: {manager_id}')
    return runtime_cls(runtime_config_ref=runtime_config_ref, runtime_overrides=runtime_overrides)
