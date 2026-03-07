import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from invest.shared import (
    LLMCaller,
    compute_bb_position,
    compute_macd_signal,
    compute_rsi,
    format_stock_table,
)
from .base import AgentConfig, InvestAgent, RegimeResult

logger = logging.getLogger(__name__)

try:
    from invest.memory import MarketSituationMemory as _MarketSituationMemory
except ImportError:
    _MarketSituationMemory = None


_MARKET_REGIME_SYSTEM_PROMPT = """你是一个专业的A股市场分析师。
你的唯一职责是判断当前市场的整体状态。

你需要基于提供的市场统计数据，判断市场处于以下三种状态之一：
- bull（牛市）：多数股票上涨，趋势向上
- bear（熊市）：多数股票下跌，趋势向下
- oscillation（震荡市）：方向不明，上下波动

请严格以JSON格式输出，不要有其他内容：
{
    "regime": "bull 或 bear 或 oscillation",
    "confidence": 0.0到1.0之间的数字,
    "suggested_exposure": 0.0到1.0之间的数字（建议总仓位比例）,
    "reasoning": "一句话说明判断依据"
}"""

# 三种市场状态对应的交易参数
REGIME_PARAMS = {
    "bull": {
        "top_n": 8,
        "max_positions": 5,
        "position_size": 0.20,
        "stop_loss_pct": 0.07,
        "take_profit_pct": 0.20,
    },
    "oscillation": {
        "top_n": 6,
        "max_positions": 4,
        "position_size": 0.20,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.15,
    },
    "bear": {
        "top_n": 3,
        "max_positions": 2,
        "position_size": 0.15,
        "stop_loss_pct": 0.03,
        "take_profit_pct": 0.10,
    },
}


class MarketRegimeAgent(InvestAgent):
    """
    市场分析师

    判断当前市场处于牛市/熊市/震荡市
    输出影响后续选股数量、仓位大小、止损止盈参数
    两种模式：analyze()（LLM）/ analyze_fallback()（纯算法）
    """

    def __init__(self, llm_caller=None):
        super().__init__(AgentConfig(name="MarketRegime", role="regime"), llm_caller)
        self.history: List[Dict] = []

    def perceive(self, market_stats: dict) -> dict:
        """感知：接收市场统计数据"""
        return market_stats

    def reason(self, perception: dict) -> dict:
        """推理：调用分析逻辑"""
        return self.analyze(perception)

    def act(self, reasoning: dict) -> dict:
        """行动：返回最终分析结果（含参数）"""
        return reasoning

    def analyze(self, market_stats: dict) -> dict:
        """
        LLM 版本：调用大模型判断市场状态

        Args:
            market_stats: compute_market_stats() 的输出

        Returns:
            {"regime", "confidence", "suggested_exposure", "reasoning", "source", "params"}
        """
        if not self.llm:
            return self.analyze_fallback(market_stats)

        try:
            result = self.llm.call_json(self.config.system_prompt, self._build_prompt(market_stats))
        except (ValueError, TypeError) as e:
            logger.warning(f"MarketRegime LLM调用异常(数据/参数): {e}")
            return self.analyze_fallback(market_stats)
        except Exception as e:
            logger.exception(f"MarketRegime LLM调用失败(网络/未知): {e}")
            return self.analyze_fallback(market_stats)

        if result.get("_parse_error"):
            return self.analyze_fallback(market_stats)

        result = self._validate(result)
        result["source"] = "llm"
        result["params"] = REGIME_PARAMS.get(result["regime"], REGIME_PARAMS["oscillation"])
        self._record(result, market_stats)
        return result

    def analyze_fallback(self, market_stats: dict) -> dict:
        """纯算法版本：基于统计规则判断，不调用 LLM"""
        regime, confidence, reasoning = self._rule_based_judgment(market_stats)
        exposure_map = {"bull": 0.8, "oscillation": 0.5, "bear": 0.2}
        result = {
            "regime": regime,
            "confidence": confidence,
            "suggested_exposure": exposure_map[regime],
            "reasoning": reasoning,
            "source": "algorithm",
            "params": REGIME_PARAMS[regime],
        }
        self._record(result, market_stats)
        return result

    def get_last_regime(self) -> str:
        return self.history[-1]["regime"] if self.history else "oscillation"

    def regime_changed(self) -> bool:
        """最近两次判断是否不同"""
        return len(self.history) >= 2 and self.history[-1]["regime"] != self.history[-2]["regime"]

    def _build_prompt(self, stats: dict) -> str:
        lines = [
            "以下是当前A股市场的统计数据：",
            "",
            f"- 统计股票数: {stats.get('valid_stocks', 0)}",
            f"- 近5日上涨股票占比: {stats.get('advance_ratio_5d', 0):.0%}",
            f"- 近5日平均涨幅: {stats.get('avg_change_5d', 0):+.2f}%",
            f"- 近5日涨幅中位数: {stats.get('median_change_5d', 0):+.2f}%",
            f"- 近20日平均涨幅: {stats.get('avg_change_20d', 0):+.2f}%",
            f"- 近20日涨幅中位数: {stats.get('median_change_20d', 0):+.2f}%",
            f"- 站上20日均线占比: {stats.get('above_ma20_ratio', 0):.0%}",
            f"- 20日平均波动率: {stats.get('avg_volatility', 0):.4f}",
            "",
            "请判断当前市场状态。",
        ]
        return "\n".join(lines)

    def _rule_based_judgment(self, stats: dict):
        avg_20d = stats.get("avg_change_20d", 0)
        median_20d = stats.get("median_change_20d", 0)
        advance = stats.get("advance_ratio_5d", 0.5)
        above_ma20 = stats.get("above_ma20_ratio", 0.5)

        score = 0.0
        reasons = []

        if median_20d > 5:
            score += 2; reasons.append(f"20日涨幅{median_20d:+.1f}%强势")
        elif median_20d > 0:
            score += 1; reasons.append(f"20日涨幅{median_20d:+.1f}%温和")
        elif median_20d > -5:
            score -= 1; reasons.append(f"20日跌幅{median_20d:+.1f}%偏弱")
        else:
            score -= 2; reasons.append(f"20日跌幅{median_20d:+.1f}%疲弱")

        if advance > 0.6:
            score += 1; reasons.append(f"多数股票上涨({advance:.0%})")
        elif advance < 0.4:
            score -= 1; reasons.append(f"多数股票下跌({advance:.0%})")

        if above_ma20 > 0.6:
            score += 1; reasons.append(f"多数在MA20上方({above_ma20:.0%})")
        elif above_ma20 < 0.4:
            score -= 1; reasons.append(f"多数在MA20下方({above_ma20:.0%})")

        if score >= 2:
            regime, confidence = "bull", min(0.9, 0.5 + score * 0.1)
        elif score <= -2:
            regime, confidence = "bear", min(0.9, 0.5 + abs(score) * 0.1)
        else:
            regime, confidence = "oscillation", 0.5

        return regime, confidence, "；".join(reasons) if reasons else "数据不足"

    def _validate(self, result: dict) -> dict:
        if result.get("regime") not in {"bull", "bear", "oscillation"}:
            result["regime"] = "oscillation"
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
        if not isinstance(result.get("suggested_exposure"), (int, float)):
            result["suggested_exposure"] = {"bull": 0.8, "oscillation": 0.5, "bear": 0.2}[result["regime"]]
        result["suggested_exposure"] = max(0.0, min(1.0, result["suggested_exposure"]))
        if not isinstance(result.get("reasoning"), str):
            result["reasoning"] = ""
        return result

    def _record(self, result: dict, stats: dict):
        self.history.append({
            "regime": result["regime"],
            "confidence": result["confidence"],
            "source": result.get("source", "unknown"),
            "cutoff_date": stats.get("cutoff_date", ""),
        })



# ============================================================
# Part 3: 趋势猎手 Agent

__all__ = ["MarketRegimeAgent", "REGIME_PARAMS"]
