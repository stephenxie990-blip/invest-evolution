import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from config import normalize_date
from .indicators import (
    _filter_by_cutoff,
    _get_date_col,
    compute_algo_score,
    compute_bb_position,
    compute_macd_signal,
    compute_pct_change,
    compute_rsi,
    compute_volume_ratio,
)

logger = logging.getLogger(__name__)


def summarize_stocks(
    stock_data: Dict[str, pd.DataFrame],
    codes: List[str],
    cutoff_date: str,
) -> List[dict]:
    """
    批量计算股票技术摘要，供 Agent Prompt 使用

    Args:
        stock_data: {code: DataFrame}
        codes: 要分析的股票代码列表
        cutoff_date: 截断日期 (YYYYMMDD 或 YYYY-MM-DD)

    Returns:
        list[dict]: 每只股票的技术摘要，按 algo_score 降序
    """
    cutoff_norm = normalize_date(cutoff_date)
    results = []

    for code in codes:
        df = stock_data.get(code)
        if df is None:
            continue
        summary = _compute_stock_summary(df, code, cutoff_norm)
        if summary:
            results.append(summary)

    results.sort(key=lambda x: x.get("algo_score", 0), reverse=True)
    return results


def _compute_stock_summary(df: pd.DataFrame, code: str, cutoff_norm: str) -> Optional[dict]:
    """计算单只股票的技术摘要"""
    try:
        sub = _filter_by_cutoff(df, cutoff_norm)
        if len(sub) < 30:
            return None

        close = pd.to_numeric(sub["close"], errors="coerce").dropna()
        if len(close) < 30 or close.iloc[-1] <= 0:
            return None

        latest = float(close.iloc[-1])

        change_5d = compute_pct_change(latest, close, 5)
        change_20d = compute_pct_change(latest, close, 20)

        ma5 = float(close.iloc[-5:].mean()) if len(close) >= 5 else latest
        ma20 = float(close.iloc[-20:].mean()) if len(close) >= 20 else latest
        if ma5 > ma20 * 1.01:
            ma_trend = "多头"
        elif ma5 < ma20 * 0.99:
            ma_trend = "空头"
        else:
            ma_trend = "交叉"

        rsi = compute_rsi(close, 14)
        macd_signal = compute_macd_signal(close)
        bb_pos = compute_bb_position(close, 20)
        vol_ratio = compute_volume_ratio(sub)
        returns = close.pct_change().dropna()
        volatility = float(returns.iloc[-20:].std()) if len(returns) >= 20 else 0.0
        algo_score = compute_algo_score(change_5d, change_20d, ma_trend, rsi, macd_signal, bb_pos)

        return {
            "code": code,
            "close": round(latest, 2),
            "change_5d": round(change_5d, 2),
            "change_20d": round(change_20d, 2),
            "ma_trend": ma_trend,
            "rsi": round(rsi, 1),
            "macd": macd_signal,
            "bb_pos": round(bb_pos, 2),
            "vol_ratio": round(vol_ratio, 2),
            "volatility": round(volatility, 4),
            "algo_score": round(algo_score, 3),
        }
    except Exception as e:
        logger.debug(f"摘要计算失败 {code}: {e}")
        return None


def format_stock_table(summaries: List[dict]) -> str:
    """将股票摘要格式化为 Markdown 表格（给 LLM 看的）"""
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


# ============================================================
# Part 5: 市场统计（供 MarketRegime 使用）
# ============================================================

def compute_market_stats(
    stock_data: Dict[str, pd.DataFrame],
    cutoff_date: str,
    min_valid: Optional[int] = None,
) -> dict:
    """
    计算市场整体统计摘要

    Args:
        stock_data: {code: DataFrame}
        cutoff_date: 截断日期
        min_valid: 有效股票最低数量（默认None=动态调整：测试环境1，小规模3，大规模5%）

    Returns:
        dict: 市场统计量，供 MarketRegime 判断
    """
    total = len(stock_data)
    if total == 0:
        return _empty_market_stats()

    # 动态调整阈值
    if min_valid is None:
        if total <= 10:
            min_valid = 1  # 测试环境
        elif total <= 100:
            min_valid = 3  # 小规模
        else:
            min_valid = max(10, int(total * 0.05))  # 大规模：至少10只或5%

    cutoff_norm = normalize_date(cutoff_date)
    changes_5d, changes_20d, volatilities = [], [], []
    above_ma20 = 0
    valid_count = 0

    for code, df in stock_data.items():
        try:
            sub = _filter_by_cutoff(df, cutoff_norm)
            if len(sub) < 30:
                continue
            close = pd.to_numeric(sub["close"], errors="coerce").dropna()
            if len(close) < 30 or close.iloc[-1] <= 0:
                continue

            latest = float(close.iloc[-1])
            c5 = compute_pct_change(latest, close, 5)
            c20 = compute_pct_change(latest, close, 20)
            ma20 = float(close.iloc[-20:].mean()) if len(close) >= 20 else latest
            vol = float(close.pct_change().dropna().iloc[-20:].std()) if len(close) >= 20 else 0.0

            valid_count += 1
            changes_5d.append(c5)
            changes_20d.append(c20)
            volatilities.append(vol)
            if latest > ma20:
                above_ma20 += 1
        except Exception:
            continue

    if valid_count < min_valid:
        logger.warning(f"有效股票数过少: {valid_count}/{total}，使用默认统计")
        return _empty_market_stats()

    arr5 = np.array(changes_5d)
    arr20 = np.array(changes_20d)

    result = {
        "total_stocks": total,
        "valid_stocks": valid_count,
        "advance_ratio_5d": float(np.mean(arr5 > 0)),
        "avg_change_5d": float(np.mean(arr5)),
        "median_change_5d": float(np.median(arr5)),
        "avg_change_20d": float(np.mean(arr20)),
        "median_change_20d": float(np.median(arr20)),
        "above_ma20_ratio": above_ma20 / valid_count,
        "avg_volatility": float(np.mean(volatilities)),
        "cutoff_date": cutoff_norm,
    }

    logger.info(
        f"📊 市场统计: {valid_count}只有效 | "
        f"5日中位{result['median_change_5d']:+.2f}% | "
        f"20日中位{result['median_change_20d']:+.2f}% | "
        f"站上MA20 {result['above_ma20_ratio']:.0%}"
    )
    return result


def _empty_market_stats() -> dict:
    """数据不足时的默认市场统计"""
    return {
        "total_stocks": 0,
        "valid_stocks": 0,
        "advance_ratio_5d": 0.5,
        "avg_change_5d": 0.0,
        "median_change_5d": 0.0,
        "avg_change_20d": 0.0,
        "median_change_20d": 0.0,
        "above_ma20_ratio": 0.5,
        "avg_volatility": 0.02,
        "cutoff_date": "",
    }

__all__ = ["summarize_stocks", "format_stock_table", "compute_market_stats"]
