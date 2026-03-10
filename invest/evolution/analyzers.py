import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List

import numpy as np

from invest.shared.llm import parse_llm_json_object

logger = logging.getLogger(__name__)


@dataclass
class TradeDetail:
    """交易明细"""
    date: str
    code: str
    action: str
    price: float
    shares: int
    pnl: float
    pnl_pct: float
    reason: str
    industry: str = ""


@dataclass
class FactorPerformance:
    """因子表现"""
    factor_name: str
    selected_count: int
    avg_return: float
    win_rate: float


@dataclass
class StopLossAnalysis:
    """止损分析"""
    total_count: int
    avg_loss_pct: float
    post_stop_performance: float  # 止损后股价走势


@dataclass
class LLMAnalysisResult:
    """LLM分析结果"""
    factor_adjustments: Dict[str, float]  # 因子权重调整
    stop_loss_suggestion: float
    take_profit_suggestion: float
    position_size_suggestion: float
    market_regime: str  # bull, bear, neutral
    confidence: float
    suggestions: List[str] = field(default_factory=list)
    raw_response: str = ""


class LLMPromptBuilder:
    """
    LLM提示词构建器

    将回测结果转换为结构化提示词
    """

    def __init__(self):
        pass

    def build_analysis_prompt(
        self,
        start_date: str,
        end_date: str,
        benchmark_return: float,
        total_return: float,
        trades: List[TradeDetail],
        factor_performance: List[FactorPerformance],
        stop_loss_analysis: StopLossAnalysis,
    ) -> str:
        """
        构建分析提示词
        """
        # 构建交易明细表格
        trade_details = self._build_trade_table(trades)

        # 构建因子表现表格
        factor_table = self._build_factor_table(factor_performance)

        prompt = f"""
## 交易回测结果分析

### 输入数据
- 回测期间: {start_date} ~ {end_date}
- 市场环境: 沪深300同期涨跌 {benchmark_return:+.2f}%
- 总收益: {total_return:+.2f}%
- 交易次数: {len(trades)}

### 交易明细
{trade_details}

### 各因子表现
{factor_table}

### 止损触发分析
- 止损次数: {stop_loss_analysis.total_count}
- 止损平均亏损: {stop_loss_analysis.avg_loss_pct:+.2f}%
- 止损后股价平均走势: {stop_loss_analysis.post_stop_performance:+.2f}%

### 请分析以下具体问题:

1. **因子贡献分析**: 哪个因子贡献为负？给出具体调整建议（因子名称和权重）

2. **止损止盈参数**: 当前止损{stop_loss_analysis.avg_loss_pct:+.1f}%是否合理？
   - 给出建议的止损值（精确到小数点后2位，如0.05表示5%）
   - 给出建议的止盈值

3. **行业集中度分析**: 是否存在行业集中风险？
   - 统计各行业交易次数
   - 给出行业分散度建议

4. **市场状态判断**: 当前参数在什么市场状态下表现最好？
   - 判断市场状态：bull（牛市）/ bear（熊市）/ neutral（震荡）
   - 评估参数在该状态下的适用性（1-10分）

5. **仓位建议**: 根据当前市场环境，建议的仓位比例

### 输出格式（严格JSON）:
```json
{{
  "factor_adjustments": {{
    "momentum_weight": 0.15,
    "rsi_weight": 0.10,
    "reversal_weight": 0.05
  }},
  "stop_loss_suggestion": 0.06,
  "take_profit_suggestion": 0.12,
  "position_size_suggestion": 0.15,
  "market_regime": "bear",
  "confidence": 0.7,
  "suggestions": [
    "减少动量因子权重",
    "将止损从5%调整到6%"
  ]
}}
```
"""

        return prompt

    def _build_trade_table(self, trades: List[TradeDetail]) -> str:
        """构建交易明细表格"""
        if not trades:
            return "无交易记录"

        lines = ["| 日期 | 代码 | 操作 | 价格 | 股数 | 盈亏 | 盈亏% | 原因 |"]
        lines.append("|------|------|------|------|------|------|--------|------|")

        for t in trades[:20]:  # 最多显示20条
            action = "买入" if t.action == "BUY" else "卖出"
            lines.append(
                f"| {t.date} | {t.code} | {action} | {t.price:.2f} | "
                f"{t.shares} | {t.pnl:+.2f} | {t.pnl_pct:+.2f}% | {t.reason[:10]} |"
            )

        if len(trades) > 20:
            lines.append(f"\n... 共 {len(trades)} 条记录")

        return "\n".join(lines)

    def _build_factor_table(self, factors: List[FactorPerformance]) -> str:
        """构建因子表现表格"""
        if not factors:
            return "| 因子 | 选中次数 | 平均收益 | 胜率 |\n|------|----------|----------|------|"

        lines = ["| 因子 | 选中次数 | 平均收益 | 胜率 |"]
        lines.append("|------|----------|----------|------|")

        for f in factors:
            lines.append(
                f"| {f.factor_name} | {f.selected_count} | "
                f"{f.avg_return:+.2f}% | {f.win_rate*100:.1f}% |"
            )

        return "\n".join(lines)


class LLMAnalyzer:
    """
    LLM分析器

    仅负责把结构化回测结果交给外部注入的 LLM 调用器。
    若未注入调用器，则安全降级为默认分析结果，不再返回仓库内置 mock 响应。
    """

    def __init__(self, model: str = "gpt-4", llm_callable: Callable[[str], str] | None = None):
        self.model = model
        self.prompt_builder = LLMPromptBuilder()
        self.llm_callable = llm_callable

    def analyze(
        self,
        start_date: str,
        end_date: str,
        benchmark_return: float,
        total_return: float,
        trades: List[TradeDetail],
        factor_performance: List[FactorPerformance] = None,
        stop_loss_analysis: StopLossAnalysis = None,
    ) -> LLMAnalysisResult:
        """
        分析回测结果
        """
        # 构建提示词
        if stop_loss_analysis is None:
            stop_loss_analysis = StopLossAnalysis(0, 0, 0)

        prompt = self.prompt_builder.build_analysis_prompt(
            start_date=start_date,
            end_date=end_date,
            benchmark_return=benchmark_return,
            total_return=total_return,
            trades=trades,
            factor_performance=factor_performance or [],
            stop_loss_analysis=stop_loss_analysis,
        )

        logger.info("正在调用LLM分析...")

        try:
            response = self._call_llm(prompt)
        except Exception as exc:
            logger.warning("LLMAnalyzer 未配置或调用失败，回退默认分析结果: %s", exc)
            return self._default_result(
                suggestions=[f"LLM 分析不可用，已回退默认建议: {exc}"],
            )

        return self._parse_response(response)

    def _call_llm(self, prompt: str) -> str:
        """调用注入的 LLM 适配器。"""
        logger.info("提示词长度: %s 字符", len(prompt))

        if self.llm_callable is None:
            raise RuntimeError("llm_callable is not configured")

        response = self.llm_callable(prompt)
        if not isinstance(response, str) or not response.strip():
            raise ValueError("llm_callable must return a non-empty string")
        return response

    def _parse_response(self, response: str) -> LLMAnalysisResult:
        """解析LLM响应"""
        data = parse_llm_json_object(response)
        if data.get("_parse_error"):
            logger.error("无法解析LLM响应")
            return self._default_result()

        return LLMAnalysisResult(
            factor_adjustments=data.get("factor_adjustments", {}),
            stop_loss_suggestion=data.get("stop_loss_suggestion", 0.05),
            take_profit_suggestion=data.get("take_profit_suggestion", 0.15),
            position_size_suggestion=data.get("position_size_suggestion", 0.2),
            market_regime=data.get("market_regime", "neutral"),
            confidence=data.get("confidence", 0.5),
            suggestions=data.get("suggestions", []),
            raw_response=response,
        )

    def _default_result(self, suggestions: List[str] | None = None, raw_response: str = "") -> LLMAnalysisResult:
        """默认结果"""
        return LLMAnalysisResult(
            factor_adjustments={},
            stop_loss_suggestion=0.05,
            take_profit_suggestion=0.15,
            position_size_suggestion=0.2,
            market_regime="neutral",
            confidence=0.5,
            suggestions=list(suggestions or []),
            raw_response=raw_response,
        )


class TradingAnalyzer:
    """
    交易分析器

    从交易记录中提取分析所需的数据
    """

    def __init__(self):
        pass

    def get_industry(self, code: str) -> str:
        """获取行业"""
        from config import industry_registry
        return industry_registry.get_industry(code)

    def analyze_trades(
        self,
        trades: List[TradeDetail],
    ) -> Dict:
        """
        分析交易记录

        Returns:
            包含各种统计数据的字典
        """
        if not trades:
            return {}

        # 按行业统计
        industry_stats = {}
        for t in trades:
            industry = self.get_industry(t.code)
            if industry not in industry_stats:
                industry_stats[industry] = {"count": 0, "pnl": 0, "wins": 0}

            industry_stats[industry]["count"] += 1
            industry_stats[industry]["pnl"] += t.pnl
            if t.pnl > 0:
                industry_stats[industry]["wins"] += 1

        # 止损分析
        stop_losses = [t for t in trades if "止损" in t.reason or "STOP" in t.reason]
        stop_loss_pnls = [t.pnl_pct for t in stop_losses]
        avg_stop_loss = np.mean(stop_loss_pnls) if stop_loss_pnls else 0

        # 盈利/亏损统计
        sells = [t for t in trades if t.action == "SELL"]
        wins = sum(1 for t in sells if t.pnl > 0)
        losses = len(sells) - wins

        return {
            "total_trades": len(trades),
            "sell_trades": len(sells),
            "winning_trades": wins,
            "losing_trades": losses,
            "win_rate": wins / len(sells) if sells else 0,
            "total_pnl": sum(t.pnl for t in trades),
            "avg_pnl": np.mean([t.pnl for t in sells]) if sells else 0,
            "industry_stats": industry_stats,
            "stop_loss_count": len(stop_losses),
            "avg_stop_loss": avg_stop_loss,
        }

    def build_factor_performance(
        self,
        trades: List[TradeDetail],
    ) -> List[FactorPerformance]:
        """
        构建因子表现数据
        """
        # 简化版：按原因分类
        reason_stats = {}
        for t in trades:
            reason = t.reason or "unknown"
            if reason not in reason_stats:
                reason_stats[reason] = {"count": 0, "pnl": 0, "wins": 0}

            reason_stats[reason]["count"] += 1
            reason_stats[reason]["pnl"] += t.pnl
            if t.pnl > 0:
                reason_stats[reason]["wins"] += 1

        # 转换为FactorPerformance
        factors = []
        for reason, stats in reason_stats.items():
            factors.append(FactorPerformance(
                factor_name=reason[:20],
                selected_count=stats["count"],
                avg_return=stats["pnl"] / stats["count"] if stats["count"] > 0 else 0,
                win_rate=stats["wins"] / stats["count"] if stats["count"] > 0 else 0,
            ))

        return factors



__all__ = [
    "TradeDetail",
    "FactorPerformance",
    "StopLossAnalysis",
    "LLMAnalysisResult",
    "LLMPromptBuilder",
    "LLMAnalyzer",
    "TradingAnalyzer",
]
