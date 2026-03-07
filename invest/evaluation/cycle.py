import logging
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from .benchmark import BenchmarkEvaluator

logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    """单次训练周期评估结果"""
    cycle_id: int
    is_profit: bool
    profit_loss: float
    return_pct: float

    signal_accuracy: float = 0.0    # 信号准确率（胜率代理）
    timing_score: float = 0.0       # 时机评分（最大回撤反转）
    risk_control_score: float = 0.0 # 风控评分（止损/止盈执行）
    overall_score: float = 0.0      # 综合评分（加权平均）

    analysis: Optional[Dict] = None
    suggestions: Optional[List[str]] = None

    def to_dict(self) -> dict:
        return asdict(self)


class StrategyEvaluator:
    """
    策略评估器

    三维度评分：
    - 信号准确率（0~1）= 盈利交易 / 总交易
    - 时机评分（0~1）  = 1 - 最大回撤
    - 风控评分（0~1）  = (止损+止盈次数) / 总交易 + 基础分0.3
    综合评分 = 0.3×信号 + 0.3×时机 + 0.4×风控
    """

    def __init__(self):
        self.evaluation_history: List[EvaluationResult] = []

    def evaluate(
        self,
        cycle_result: Dict,
        trade_history: Optional[List[Dict]] = None,
        daily_records: Optional[List[Dict]] = None,
    ) -> EvaluationResult:
        """
        评估单次训练周期

        Args:
            cycle_result:  周期结果字典（含 return_pct, total_trades, win_rate 等）
            trade_history: 交易历史列表（含 pnl, reason 等）
            daily_records: 每日记录列表（含 total_value）

        Returns:
            EvaluationResult
        """
        cycle_id   = cycle_result.get("cycle_id", 0)
        return_pct = cycle_result.get("return_pct", 0.0)
        profit_loss = cycle_result.get("profit_loss", 0.0)
        is_profit   = return_pct > 0

        logger.info(f"评估周期 #{cycle_id}: 收益率 {return_pct:.2f}%")

        signal_accuracy = self._evaluate_signal_accuracy(trade_history)
        timing_score    = self._evaluate_timing(daily_records)
        risk_score      = self._evaluate_risk_control(trade_history)

        overall_score = (
            signal_accuracy * 0.30 +
            timing_score    * 0.30 +
            risk_score      * 0.40
        )

        analysis = {
            "signal_accuracy":    signal_accuracy,
            "timing_score":       timing_score,
            "risk_control_score": risk_score,
            "total_trades":       cycle_result.get("total_trades", 0),
            "winning_trades":     cycle_result.get("winning_trades", 0),
            "losing_trades":      cycle_result.get("losing_trades", 0),
            "win_rate":           cycle_result.get("win_rate", 0.0),
            "selected_stocks":    cycle_result.get("selected_stocks", []),
        }

        suggestions = self._generate_suggestions(
            is_profit, signal_accuracy, timing_score, risk_score, analysis
        )

        result = EvaluationResult(
            cycle_id=cycle_id,
            is_profit=is_profit,
            profit_loss=profit_loss,
            return_pct=return_pct,
            signal_accuracy=signal_accuracy,
            timing_score=timing_score,
            risk_control_score=risk_score,
            overall_score=overall_score,
            analysis=analysis,
            suggestions=suggestions,
        )
        self.evaluation_history.append(result)

        logger.info(
            f"评估完成: 综合{overall_score:.2f}, "
            f"信号{signal_accuracy:.2f}, 时机{timing_score:.2f}, 风控{risk_score:.2f}"
        )
        return result

    def _evaluate_signal_accuracy(self, trade_history: Optional[List[Dict]]) -> float:
        if not trade_history:
            return 0.5
        winning = sum(1 for t in trade_history if t.get("pnl", 0) > 0)
        total   = len(trade_history)
        return winning / total if total > 0 else 0.5

    def _evaluate_timing(self, daily_records: Optional[List[Dict]]) -> float:
        if not daily_records:
            return 0.5
        peak = 0.0
        max_drawdown = 0.0
        for rec in daily_records:
            val = rec.get("total_value", 0.0)
            if val > peak:
                peak = val
            if peak > 0:
                dd = (peak - val) / peak
                if dd > max_drawdown:
                    max_drawdown = dd
        return max(0.0, min(1.0, 1.0 - max_drawdown))

    def _evaluate_risk_control(self, trade_history: Optional[List[Dict]]) -> float:
        if not trade_history:
            return 0.5
        sl_tp = sum(
            1 for t in trade_history
            if "止损" in t.get("reason", "") or "止盈" in t.get("reason", "")
        )
        total = len(trade_history)
        return min(1.0, sl_tp / total + 0.3) if total > 0 else 0.5

    def _generate_suggestions(
        self,
        is_profit: bool,
        signal_accuracy: float,
        timing_score: float,
        risk_score: float,
        analysis: Dict,
    ) -> List[str]:
        suggestions = []
        if signal_accuracy < 0.4:
            suggestions.append("信号准确率低，建议优化选股策略参数")
        if timing_score < 0.4:
            suggestions.append("买入时机不佳，建议增加趋势确认条件")
        if risk_score < 0.4:
            suggestions.append("风控执行不足，建议严格执行止损纪律")

        if not is_profit:
            if analysis.get("win_rate", 0) < 0.4:
                suggestions.append("胜率低，建议降低交易频率或调整止损幅度")
            if analysis.get("total_trades", 0) > 20:
                suggestions.append("交易过于频繁，建议减少无效交易")
            suggestions.append("当前周期亏损，建议降低仓位或暂停交易")
        elif analysis.get("win_rate", 0) > 0.6:
            suggestions.append("策略表现良好，可考虑适当增加仓位")

        if not suggestions:
            suggestions.append("策略表现正常，继续保持当前参数")
        return suggestions

    def evaluate_consecutive_cycles(self, cycle_results: List[Dict]) -> Dict:
        """汇总分析连续多个周期"""
        if not cycle_results:
            return {"status": "no_data"}
        n      = len(cycle_results)
        rets   = [r.get("return_pct", 0) for r in cycle_results]
        profits = sum(1 for r in rets if r > 0)
        return {
            "total_cycles":       n,
            "profit_count":       profits,
            "loss_count":         n - profits,
            "profit_rate":        profits / n,
            "avg_return":         sum(rets) / n,
            "consecutive_profit": self._count_consecutive(cycle_results, positive=True),
            "consecutive_loss":   self._count_consecutive(cycle_results, positive=False),
        }

    def _count_consecutive(self, results: List[Dict], positive: bool) -> int:
        max_cnt = curr = 0
        for r in results:
            if (r.get("return_pct", 0) > 0) == positive:
                curr += 1
                max_cnt = max(max_cnt, curr)
            else:
                curr = 0
        return max_cnt

    def get_evaluation_report(self, cycle_id: Optional[int] = None) -> str:
        """生成评估报告文本"""
        if cycle_id is not None:
            result = next((e for e in self.evaluation_history if e.cycle_id == cycle_id), None)
            return self._format_single_report(result) if result else f"未找到周期 #{cycle_id} 的评估结果"
        return self._format_overall_report()

    def _format_single_report(self, r: EvaluationResult) -> str:
        lines = [
            f"# 周期 #{r.cycle_id} 评估报告", "",
            f"**盈亏**: {'盈利' if r.is_profit else '亏损'}",
            f"**收益率**: {r.return_pct:.2f}%",
            f"**盈亏**: {r.profit_loss:.2f}", "",
            "## 评分",
            f"- 信号准确率: {r.signal_accuracy:.2f}",
            f"- 时机评分:   {r.timing_score:.2f}",
            f"- 风控评分:   {r.risk_control_score:.2f}",
            f"- 综合评分:   {r.overall_score:.2f}", "",
        ]
        if r.suggestions:
            lines.append("## 改进建议")
            for i, s in enumerate(r.suggestions, 1):
                lines.append(f"{i}. {s}")
        return "\n".join(lines)

    def _format_overall_report(self) -> str:
        if not self.evaluation_history:
            return "暂无评估记录"
        h = self.evaluation_history
        n = len(h)
        profits = sum(1 for e in h if e.is_profit)
        lines = [
            "# 策略评估总报告", "",
            f"评估周期数: {n}", "",
            "## 整体统计",
            f"- 盈利周期: {profits} ({profits/n*100:.1f}%)",
            f"- 亏损周期: {n - profits}",
            f"- 平均收益率: {sum(e.return_pct for e in h)/n:.2f}%", "",
            "## 平均评分",
            f"- 信号准确率: {sum(e.signal_accuracy for e in h)/n:.2f}",
            f"- 时机评分:   {sum(e.timing_score for e in h)/n:.2f}",
            f"- 风控评分:   {sum(e.risk_control_score for e in h)/n:.2f}",
            f"- 综合评分:   {sum(e.overall_score for e in h)/n:.2f}",
        ]
        return "\n".join(lines)

class PerformanceAnalyzer:
    """
    综合绩效分析器

    融合 StrategyEvaluator（信号/时机/风控）
    和   BenchmarkEvaluator（Sharpe/Calmar/回撤）
    """

    def __init__(self, risk_free_rate: float = 0.03):
        self.strategy_evaluator  = StrategyEvaluator()
        self.benchmark_evaluator = BenchmarkEvaluator(risk_free_rate)
        self.history: List[Dict] = []

    def full_evaluate(
        self,
        cycle_result: Dict,
        trade_history: Optional[List[Dict]] = None,
        daily_records: Optional[List[Dict]] = None,
        benchmark_daily_values: Optional[List[float]] = None,
    ) -> Dict:
        """
        完整评估单个周期

        Returns:
            {
                "strategy": EvaluationResult,
                "benchmark": BenchmarkMetrics,
                "summary": str,
            }
        """
        strategy_eval = self.strategy_evaluator.evaluate(
            cycle_result, trade_history, daily_records
        )

        # 从 daily_records 提取资金曲线
        daily_values = []
        if daily_records:
            daily_values = [r.get("total_value", 0.0) for r in daily_records]

        benchmark = self.benchmark_evaluator.evaluate(
            daily_values, benchmark_daily_values, trade_history
        )

        summary_lines = [
            f"周期 #{cycle_result.get('cycle_id', '?')} 综合评估",
            f"  收益率: {cycle_result.get('return_pct', 0):.2f}%",
            f"  综合评分: {strategy_eval.overall_score:.2f}",
            f"  Sharpe: {benchmark.sharpe_ratio:.2f}",
            f"  最大回撤: {benchmark.max_drawdown:.2f}%",
            f"  基准合格: {'✅' if benchmark.passed else '❌'}",
        ]

        report = {
            "strategy":  strategy_eval,
            "benchmark": benchmark,
            "summary":   "\n".join(summary_lines),
        }
        self.history.append(report)
        return report

    def get_best_cycles(self, top_n: int = 3) -> List[Dict]:
        """返回综合评分最高的 N 个周期"""
        return sorted(
            self.history,
            key=lambda r: r["strategy"].overall_score,
            reverse=True,
        )[:top_n]

    def get_overall_summary(self) -> str:
        """返回所有周期的汇总报告文本"""
        if not self.history:
            return "暂无评估记录"
        n = len(self.history)
        passed = sum(1 for r in self.history if r["benchmark"].passed)
        avg_ret = sum(r["strategy"].return_pct for r in self.history) / n
        avg_sharpe = sum(r["benchmark"].sharpe_ratio for r in self.history) / n
        return (
            f"# 汇总报告（{n} 个周期）\n"
            f"- 基准合格率: {passed}/{n} ({passed/n*100:.1f}%)\n"
            f"- 平均收益率: {avg_ret:.2f}%\n"
            f"- 平均 Sharpe: {avg_sharpe:.2f}"
        )

# ============================================================
# freeze_evaluator.py
# ============================================================

"""
固化评估器

修正后的固化条件：
1. 10轮中≥7轮盈利
2. 总累计收益 > 沪深300收益（超额收益）
3. 最大回撤 < 15%
4. Sharpe Ratio > 1.0
5. 至少覆盖1段牛市 + 1段熊市
6. 在独立Out-of-Sample数据上验证
"""

import sys
import os
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CycleMetrics:
    """单轮指标"""
    cycle_id: int
    cutoff_date: str
    return_pct: float
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0


__all__ = ["EvaluationResult", "StrategyEvaluator", "PerformanceAnalyzer", "CycleMetrics"]
