import json
import logging
import re
from dataclasses import dataclass
from typing import Dict, List

from invest.shared import LLMCaller
from invest.shared.llm import parse_llm_json_object

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """LLM 分析结果"""
    cause: str
    suggestions: List[str]
    strategy_adjustments: Dict
    new_strategy_needed: bool


class LLMOptimizer:
    """
    LLM 亏损分析 + 策略参数优化

    LLM 调用通过统一 LLMGateway（与 commander 共用）
    调用失败时自动降级到默认规则分析
    """

    def __init__(self):
        self.analysis_history: List[Dict] = []
        self.llm = LLMCaller()

    def analyze_loss(
        self,
        cycle_result: Dict,
        trade_history: List[Dict],
    ) -> AnalysisResult:
        """分析亏损原因"""
        prompt = self._build_prompt(cycle_result, trade_history)
        try:
            response = self._call_llm(prompt)
            result = self._parse_response(response, cycle_result)
        except Exception as e:
            logger.info(f"LLM 分析失败 ({e})，使用默认分析")
            result = self._default_analysis(cycle_result)

        self.analysis_history.append({
            "cycle_id": cycle_result.get("cycle_id"),
            "result": result,
        })
        return result

    def _build_prompt(self, cycle_result: Dict, trade_history: List[Dict]) -> str:
        trades_summary = [
            {
                "date":   t.get("date"),
                "action": t.get("action"),
                "code":   t.get("ts_code"),
                "price":  t.get("price"),
                "pnl":    t.get("pnl", 0),
                "reason": t.get("reason", ""),
            }
            for t in trade_history[-10:]
        ]
        return f"""
你是专业的量化交易分析师，请分析以下交易亏损：

## 周期结果
- 截断日期: {cycle_result.get('cutoff_date')}
- 收益率: {cycle_result.get('return_pct')}%
- 交易次数: {cycle_result.get('total_trades')}
- 胜率: {cycle_result.get('win_rate', 0)*100:.1f}%

## 交易记录（最近10笔）
{json.dumps(trades_summary, ensure_ascii=False, indent=2)}

以JSON格式回答：
{{
  "cause": "亏损原因",
  "suggestions": ["建议1","建议2","建议3"],
  "strategy_adjustments": {{
    "stop_loss_pct": 数值或null,
    "take_profit_pct": 数值或null,
    "position_size": 数值或null,
    "ma_short": 数值或null,
    "ma_long": 数值或null
  }},
  "new_strategy_needed": true或false
}}
"""

    def _call_llm(self, prompt: str) -> str:
        return self.llm.call(
            system_prompt="你是专业的量化交易分析师。请仅返回JSON。",
            user_message=prompt,
            temperature=0.7,
            max_tokens=2048,
        )

    def _parse_response(self, response: str, cycle_result: Dict) -> AnalysisResult:
        data = parse_llm_json_object(response)
        if data.get("_parse_error"):
            logger.warning("解析 LLM 响应失败，使用默认分析")
            return self._default_analysis(cycle_result)
        return AnalysisResult(
            cause=data.get("cause", "未知原因"),
            suggestions=data.get("suggestions", []),
            strategy_adjustments=data.get("strategy_adjustments", {}),
            new_strategy_needed=data.get("new_strategy_needed", False),
        )

    def _default_analysis(self, cycle_result: Dict) -> AnalysisResult:
        return AnalysisResult(
            cause="策略表现不佳，需要调整参数",
            suggestions=["降低仓位", "收紧止损", "增加趋势确认", "减少交易频率"],
            strategy_adjustments={
                "stop_loss_pct": 0.05,
                "take_profit_pct": 0.10,
                "position_size": 0.15,
            },
            new_strategy_needed=False,
        )

    def generate_strategy_fix(self, analysis: AnalysisResult) -> Dict:
        """根据分析结果生成策略调整参数"""
        logger.info(f"策略调整: {analysis.cause}")
        return analysis.strategy_adjustments or {
            "stop_loss_pct":   0.05,
            "take_profit_pct": 0.10,
            "position_size":   0.15,
        }

__all__ = ["AnalysisResult", "LLMOptimizer"]
