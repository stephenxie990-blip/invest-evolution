"""Stable compute-layer helpers.

Legacy feature/factor/indicator functions stay available here.
Stateful streaming indicators live in `invest.foundation.compute.indicators_v2`.
"""

from .data_adapter import filter_by_cutoff, get_date_col
from .factors import calc_algo_score
from .features import compute_market_stats, compute_stock_summary, summarize_stocks
from .market_stats import compute_market_stats as compute_market_snapshot_stats
from .indicators import (
    calc_bb_position,
    calc_macd_signal,
    calc_pct_change,
    calc_rsi,
    calc_volume_ratio,
)

__all__ = [
    "calc_algo_score",
    "compute_market_stats",
    "compute_market_snapshot_stats",
    "compute_stock_summary",
    "summarize_stocks",
    "calc_bb_position",
    "calc_macd_signal",
    "calc_pct_change",
    "calc_rsi",
    "calc_volume_ratio",
    "filter_by_cutoff",
    "get_date_col",
]
