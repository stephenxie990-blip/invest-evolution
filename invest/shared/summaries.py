import logging
from typing import Dict, List

import pandas as pd

from invest.foundation.compute import compute_market_stats, summarize_stocks

logger = logging.getLogger(__name__)


def format_stock_table(summaries: List[dict]) -> str:
    if not summaries:
        return "（无候选股票）"

    lines = [
        "| # | 代码 | 收盘价 | 5日涨跌% | 20日涨跌% | MA趋势 | RSI | MACD信号 | BB位置 | 量比 |",
        "|---|------|--------|----------|-----------|--------|-----|----------|--------|------|",
    ]
    for i, s in enumerate(summaries):
        lines.append(
            f"| {i+1} "
            f"| {s['code']} "
            f"| {s['close']:.1f} "
            f"| {s['change_5d']:+.1f} "
            f"| {s['change_20d']:+.1f} "
            f"| {s['ma_trend']} "
            f"| {s['rsi']:.0f} "
            f"| {s['macd']} "
            f"| {s['bb_pos']:.2f} "
            f"| {s['vol_ratio']:.1f} |"
        )
    return "\n".join(lines)


__all__ = ["summarize_stocks", "format_stock_table", "compute_market_stats"]
