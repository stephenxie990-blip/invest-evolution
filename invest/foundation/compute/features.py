from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from .batch_snapshot import StockBatchSummary, build_batch_indicator_snapshot, build_batch_summary
from .market_stats import compute_market_stats as compute_market_stats_snapshot
from config import normalize_date


def compute_stock_summary(df: pd.DataFrame, code: str, cutoff_norm: str, summary_scoring: Optional[dict] = None) -> Optional[dict]:
    try:
        return build_batch_summary(df, code, cutoff_norm, summary_scoring=summary_scoring)
    except Exception:
        return None


def summarize_stock_batches(
    stock_data: Dict[str, pd.DataFrame],
    codes: List[str],
    cutoff_date: str,
    summary_scoring: Optional[dict] = None,
) -> List[StockBatchSummary]:
    cutoff_norm = normalize_date(cutoff_date)
    results: List[StockBatchSummary] = []
    for code in codes:
        df = stock_data.get(code)
        if df is None:
            continue
        try:
            batch = build_batch_indicator_snapshot(df, cutoff_norm, summary_scoring=summary_scoring)
            if batch is None:
                continue
            summary = build_batch_summary(df, code, cutoff_norm, summary_scoring=summary_scoring)
            if summary is None:
                continue
            results.append(StockBatchSummary(code=code, batch=batch, summary=summary))
        except Exception:
            continue
    results.sort(key=lambda item: item.summary.get("algo_score", 0), reverse=True)
    return results


def summarize_stocks(stock_data: Dict[str, pd.DataFrame], codes: List[str], cutoff_date: str, summary_scoring: Optional[dict] = None) -> List[dict]:
    return [
        item.summary
        for item in summarize_stock_batches(stock_data, codes, cutoff_date, summary_scoring=summary_scoring)
    ]


def compute_market_stats(stock_data: Dict[str, pd.DataFrame], cutoff_date: str, min_valid: Optional[int] = None, regime_policy: Optional[dict] = None) -> dict:
    return compute_market_stats_snapshot(stock_data, cutoff_date, min_valid=min_valid, regime_policy=regime_policy)
