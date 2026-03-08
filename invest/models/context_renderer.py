from __future__ import annotations

from typing import Dict, Iterable, List


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


def render_candidate_narrative(stock_summaries: List[dict], top_codes: List[str]) -> str:
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
