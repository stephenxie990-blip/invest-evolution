"""
投资进化系统 - 评估层

包含：
1. EvaluationResult     — 单周期评估数据类
2. StrategyEvaluator    — 策略表现评估器（信号/时机/风控三维度评分）
3. BenchmarkMetrics     — 基准评估指标数据类
4. BenchmarkEvaluator   — 量化指标评估器（Sharpe/Calmar/最大回撤/盈亏比等）
5. PerformanceAnalyzer  — 综合分析入口（融合两个评估器）

合格判定标准（BenchmarkEvaluator.CRITERIA）：
    超额收益 > 0%
    Sharpe   > 1.0
    最大回撤 < 15%
    Calmar   > 1.5
    胜率     > 45%
    盈亏比   > 1.5
    月换手率 < 300%
"""

import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ============================================================
# Part 1: 单周期评估
# ============================================================

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


# ============================================================
# Part 2: 量化基准评估
# ============================================================

@dataclass
class BenchmarkMetrics:
    """基准量化评估指标"""
    total_return: float   = 0.0  # 总收益率 %
    annual_return: float  = 0.0  # 年化收益率 %
    excess_return: float  = 0.0  # 超额收益 %
    benchmark_return: float = 0.0  # 基准收益 %

    sharpe_ratio:   float = 0.0  # Sharpe Ratio
    calmar_ratio:   float = 0.0  # Calmar Ratio
    sortino_ratio:  float = 0.0  # Sortino Ratio

    max_drawdown: float = 0.0   # 最大回撤 %
    volatility:   float = 0.0   # 年化波动率 %

    win_rate:          float = 0.0  # 胜率
    profit_loss_ratio: float = 0.0  # 盈亏比
    monthly_turnover:  float = 0.0  # 月均换手率

    passed: bool = False
    failed_criteria: List[str] = field(default_factory=list)


class BenchmarkEvaluator:
    """
    基准评估器

    评估策略资金曲线，计算全套量化指标并判断是否达标
    """

    # 合格标准
    CRITERIA = {
        "excess_return":     0.0,   # 超额收益 > 0%
        "sharpe_ratio":      1.0,   # Sharpe > 1.0
        "max_drawdown":      15.0,  # 最大回撤 < 15%
        "calmar_ratio":      1.5,   # Calmar > 1.5
        "win_rate":          0.45,  # 胜率 > 45%
        "profit_loss_ratio": 1.5,   # 盈亏比 > 1.5
        "monthly_turnover":  3.0,   # 月换手 < 300%
    }

    def __init__(self, risk_free_rate: float = 0.03):
        self.risk_free_rate = risk_free_rate  # 年化无风险利率

    def evaluate(
        self,
        daily_values: List[float],
        benchmark_daily_values: Optional[List[float]] = None,
        trade_history: Optional[List[Dict]] = None,
        trading_days: int = 252,
    ) -> BenchmarkMetrics:
        """
        计算全套量化指标

        Args:
            daily_values:           每日组合净值序列
            benchmark_daily_values: 基准净值序列（如沪深300），可选
            trade_history:          交易历史，用于计算胜率和换手率
            trading_days:           年化交易日数（A股 252）

        Returns:
            BenchmarkMetrics
        """
        if not daily_values or len(daily_values) < 2:
            return BenchmarkMetrics()

        values  = np.array(daily_values, dtype=float)
        returns = np.diff(values) / np.maximum(values[:-1], 1e-9)
        days    = len(daily_values)

        # 绝对收益
        total_return   = (values[-1] - values[0]) / values[0] * 100
        years          = days / trading_days if trading_days > 0 else 1
        annual_return  = ((values[-1] / values[0]) ** (1 / years) - 1) * 100 if years > 0 else 0

        # 超额收益
        excess_return = 0.0
        benchmark_return = 0.0
        if benchmark_daily_values and len(benchmark_daily_values) == len(daily_values):
            bench = np.array(benchmark_daily_values, dtype=float)
            benchmark_return = (bench[-1] - bench[0]) / bench[0] * 100
            excess_return    = total_return - benchmark_return

        # Sharpe Ratio（年化）
        if np.std(returns) > 0:
            excess_ret   = returns - self.risk_free_rate / trading_days
            sharpe_ratio = np.mean(excess_ret) / np.std(excess_ret) * np.sqrt(trading_days)
        else:
            sharpe_ratio = 0.0

        # 最大回撤
        peak, max_drawdown = values[0], 0.0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > max_drawdown:
                max_drawdown = dd

        # Calmar Ratio
        calmar_ratio = annual_return / max_drawdown if max_drawdown > 0 else 0.0

        # Sortino Ratio
        downside = returns[returns < 0]
        sortino_ratio = (
            np.mean(returns) / np.std(downside) * np.sqrt(trading_days)
            if len(downside) > 0 and np.std(downside) > 0 else 0.0
        )

        # 年化波动率
        volatility = float(np.std(returns) * np.sqrt(trading_days) * 100)

        # 交易统计
        win_rate = profit_loss_ratio = monthly_turnover = 0.0
        if trade_history:
            sells    = [t for t in trade_history if t.get("action") in ("SELL", "卖出")]
            if sells:
                wins   = [t for t in sells if t.get("pnl", 0) > 0]
                losses = [t for t in sells if t.get("pnl", 0) <= 0]
                win_rate = len(wins) / len(sells)
                avg_win  = float(np.mean([t["pnl"] for t in wins])) if wins else 0.0
                avg_loss = abs(float(np.mean([t["pnl"] for t in losses]))) if losses else 1.0
                profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

            months = max(days / 21, 1)
            monthly_turnover = (len(trade_history) / months) / max(len(values), 1)

        # 合格判定
        passed: bool = True
        failed: List[str] = []

        checks = [
            (excess_return <= self.CRITERIA["excess_return"],
             f"超额收益{excess_return:.1f}% ≤ {self.CRITERIA['excess_return']}%"),
            (sharpe_ratio <= self.CRITERIA["sharpe_ratio"],
             f"Sharpe{sharpe_ratio:.2f} ≤ {self.CRITERIA['sharpe_ratio']}"),
            (max_drawdown >= self.CRITERIA["max_drawdown"],
             f"回撤{max_drawdown:.1f}% ≥ {self.CRITERIA['max_drawdown']}%"),
            (calmar_ratio <= self.CRITERIA["calmar_ratio"],
             f"Calmar{calmar_ratio:.2f} ≤ {self.CRITERIA['calmar_ratio']}"),
            (win_rate <= self.CRITERIA["win_rate"],
             f"胜率{win_rate*100:.1f}% ≤ {self.CRITERIA['win_rate']*100}%"),
            (profit_loss_ratio <= self.CRITERIA["profit_loss_ratio"],
             f"盈亏比{profit_loss_ratio:.2f} ≤ {self.CRITERIA['profit_loss_ratio']}"),
            (monthly_turnover >= self.CRITERIA["monthly_turnover"],
             f"月换手{monthly_turnover*100:.0f}% ≥ {self.CRITERIA['monthly_turnover']*100}%"),
        ]
        for cond, msg in checks:
            if cond:
                passed = False
                failed.append(msg)

        metrics = BenchmarkMetrics(
            total_return=total_return,
            annual_return=annual_return,
            excess_return=excess_return,
            benchmark_return=benchmark_return,
            sharpe_ratio=sharpe_ratio,
            calmar_ratio=calmar_ratio,
            sortino_ratio=sortino_ratio,
            max_drawdown=max_drawdown,
            volatility=volatility,
            win_rate=win_rate,
            profit_loss_ratio=profit_loss_ratio,
            monthly_turnover=monthly_turnover,
            passed=passed,
            failed_criteria=failed,
        )

        self._log_metrics(metrics)
        return metrics

    def _log_metrics(self, m: BenchmarkMetrics):
        logger.info("=" * 50)
        logger.info(f"总收益: {m.total_return:+.2f}%   年化: {m.annual_return:+.2f}%")
        logger.info(f"超额收益: {m.excess_return:+.2f}%  基准: {m.benchmark_return:+.2f}%")
        logger.info(f"Sharpe: {m.sharpe_ratio:.2f}  Calmar: {m.calmar_ratio:.2f}  Sortino: {m.sortino_ratio:.2f}")
        logger.info(f"最大回撤: {m.max_drawdown:.2f}%  波动率: {m.volatility:.2f}%")
        logger.info(f"胜率: {m.win_rate*100:.1f}%  盈亏比: {m.profit_loss_ratio:.2f}  月换手: {m.monthly_turnover*100:.0f}%")
        logger.info(f"合格: {'✅ 是' if m.passed else '❌ 否'}")
        for c in m.failed_criteria:
            logger.info(f"  - {c}")
        logger.info("=" * 50)


# ============================================================
# Part 3: 综合分析入口
# ============================================================

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


@dataclass
class FreezeCriteria:
    """固化条件"""
    min_profit_cycles: int = 7  # 最小盈利轮数
    min_win_rate: float = 0.7  # 最小胜率
    require_excess_return: bool = True  # 需要超额收益
    require_max_drawdown: bool = True  # 需要最大回撤检查
    max_drawdown_threshold: float = 15.0  # 最大回撤阈值(%)
    require_sharpe: bool = True  # 需要Sharpe检查
    min_sharpe: float = 1.0  # 最小Sharpe
    require_bull_bear: bool = True  # 需要牛熊市覆盖
    require_oos_validation: bool = True  # 需要OOS验证


@dataclass
class FreezeResult:
    """固化评估结果"""
    can_freeze: bool
    criteria_met: Dict[str, bool] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)
    details: Dict = field(default_factory=dict)


class FreezeEvaluator:
    """
    固化条件评估器
    """

    def __init__(self, criteria: FreezeCriteria = None):
        self.criteria = criteria or FreezeCriteria()
        self.hs300_data = None  # 沪深300数据
        self.cycle_metrics: List[CycleMetrics] = []

    def load_hs300_data(self, start_date: str, end_date: str = None):
        """
        加载沪深300数据用于基准对比

        Args:
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期
        """
        import baostock as bs

        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        lg = bs.login()

        # 沪深300指数代码: sh.000300
        rs = bs.query_history_k_data_plus(
            "sh.000300",
            "date,open,high,low,close,volume",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2"
        )

        data_list = []
        while rs.next():
            data_list.append(rs.get_row_data())

        if data_list:
            self.hs300_data = pd.DataFrame(data_list, columns=rs.fields)
            from config import normalize_date
            self.hs300_data["trade_date"] = self.hs300_data["date"].apply(normalize_date)
            self.hs300_data["close"] = pd.to_numeric(self.hs300_data["close"], errors="coerce")
            logger.info(f"沪深300数据加载完成: {len(self.hs300_data)} 条")

        bs.logout()

    def calculate_metrics(self, cycle_results: List[Dict]) -> List[CycleMetrics]:
        """
        计算每轮的性能指标

        Args:
            cycle_results: 训练周期结果列表

        Returns:
            每轮指标列表
        """
        metrics = []

        for i, result in enumerate(cycle_results):
            # 提取每日资金曲线来计算最大回撤和Sharpe
            daily_values = result.get("daily_values", [])

            if len(daily_values) > 1:
                values = np.array(daily_values)
                initial = values[0]

                # 计算收益率序列
                returns = np.diff(values) / values[:-1]

                # 最大回撤
                peak = values[0]
                max_dd = 0
                for v in values:
                    if v > peak:
                        peak = v
                    dd = (peak - v) / peak * 100
                    if dd > max_dd:
                        max_dd = dd

                # Sharpe Ratio (年化)
                if len(returns) > 0 and np.std(returns) > 0:
                    sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
                else:
                    sharpe = 0
            else:
                max_dd = 0
                sharpe = 0

            metrics.append(CycleMetrics(
                cycle_id=result.get("cycle_id", i + 1),
                cutoff_date=result.get("cutoff_date", ""),
                return_pct=result.get("return_pct", 0),
                max_drawdown=max_dd,
                sharpe_ratio=sharpe,
            ))

        self.cycle_metrics = metrics
        return metrics

    def check_bull_bear_coverage(self, cycle_results: List[Dict]) -> Dict:
        """
        检查是否覆盖了牛市和熊市

        通过检查训练区间的市场表现来判断
        """
        if not cycle_results:
            return {"covered": False, "bull": False, "bear": False}

        # 提取所有截断日期对应的市场状态
        market_states = []

        for result in cycle_results:
            cutoff = result.get("cutoff_date", "")
            if cutoff and self.hs300_data is not None:
                # 检查T0后30天的市场表现
                row = self.hs300_data[self.hs300_data["trade_date"] >= cutoff]
                if len(row) >= 30:
                    start_price = row.iloc[0]["close"]
                    end_price = row.iloc[29]["close"]
                    change_pct = (end_price - start_price) / start_price * 100

                    if change_pct > 10:
                        market_states.append("bull")
                    elif change_pct < -10:
                        market_states.append("bear")

        bull = "bull" in market_states
        bear = "bear" in market_states

        return {
            "covered": bull and bear,
            "bull": bull,
            "bear": bear,
            "states": market_states,
        }

    def evaluate(self, cycle_results: List[Dict], oos_results: List[Dict] = None) -> FreezeResult:
        """
        评估是否满足固化条件

        Args:
            cycle_results: 训练周期结果
            oos_results: Out-of-Sample验证结果（可选）

        Returns:
            固化评估结果
        """
        criteria_met = {}
        metrics = {}

        if not cycle_results:
            return FreezeResult(can_freeze=False, criteria_met={}, metrics={})

        # 1. 盈利轮数检查
        profit_cycles = sum(1 for r in cycle_results if r.get("return_pct", 0) > 0)
        total_cycles = len(cycle_results)
        win_rate = profit_cycles / total_cycles if total_cycles > 0 else 0

        criteria_met["min_profit_cycles"] = profit_cycles >= self.criteria.min_profit_cycles
        criteria_met["min_win_rate"] = win_rate >= self.criteria.min_win_rate

        metrics["profit_cycles"] = profit_cycles
        metrics["total_cycles"] = total_cycles
        metrics["win_rate"] = win_rate * 100

        # 2. 计算性能指标
        metrics_list = self.calculate_metrics(cycle_results)

        # 总累计收益
        total_return = sum(r.get("return_pct", 0) for r in cycle_results)
        avg_return = total_return / total_cycles if total_cycles > 0 else 0

        criteria_met["positive_return"] = total_return > 0

        metrics["total_return"] = total_return
        metrics["avg_return"] = avg_return

        # 3. 超额收益检查
        if self.criteria.require_excess_return and self.hs300_data:
            # 计算同期沪深300收益
            hs300_return = 0
            for result in cycle_results:
                cutoff = result.get("cutoff_date", "")
                if cutoff:
                    row = self.hs300_data[self.hs300_data["trade_date"] >= cutoff]
                    if len(row) >= 30:
                        start_price = row.iloc[0]["close"]
                        end_price = row.iloc[29]["close"]
                        hs300_return += (end_price - start_price) / start_price * 100

            excess_return = total_return - hs300_return
            criteria_met["excess_return"] = excess_return > 0

            metrics["hs300_return"] = hs300_return
            metrics["excess_return"] = excess_return
        else:
            criteria_met["excess_return"] = True
            metrics["excess_return"] = total_return

        # 4. 最大回撤检查
        if self.criteria.require_max_drawdown:
            max_drawdowns = [m.max_drawdown for m in metrics_list]
            avg_max_dd = np.mean(max_drawdowns) if max_drawdowns else 0
            max_dd_exceeded = avg_max_dd > self.criteria.max_drawdown_threshold

            criteria_met["max_drawdown"] = not max_dd_exceeded

            metrics["avg_max_drawdown"] = avg_max_dd
            metrics["max_drawdown_threshold"] = self.criteria.max_drawdown_threshold
        else:
            criteria_met["max_drawdown"] = True

        # 5. Sharpe Ratio检查
        if self.criteria.require_sharpe:
            sharpe_ratios = [m.sharpe_ratio for m in metrics_list]
            avg_sharpe = np.mean(sharpe_ratios) if sharpe_ratios else 0

            criteria_met["sharpe_ratio"] = avg_sharpe >= self.criteria.min_sharpe

            metrics["avg_sharpe_ratio"] = avg_sharpe
            metrics["min_sharpe_required"] = self.criteria.min_sharpe
        else:
            criteria_met["sharpe_ratio"] = True

        # 6. 牛熊市覆盖检查
        if self.criteria.require_bull_bear:
            bull_bear = self.check_bull_bear_coverage(cycle_results)
            criteria_met["bull_bear_coverage"] = bull_bear["covered"]

            metrics.update(bull_bear)
        else:
            criteria_met["bull_bear_coverage"] = True

        # 7. Out-of-Sample验证
        if self.criteria.require_oos_validation and oos_results:
            oos_profit = sum(1 for r in oos_results if r.get("return_pct", 0) > 0)
            oos_total = len(oos_results)
            oos_win_rate = oos_profit / oos_total if oos_total > 0 else 0

            criteria_met["oos_validation"] = oos_win_rate >= 0.5  # OOS至少50%胜率

            metrics["oos_profit_cycles"] = oos_profit
            metrics["oos_total"] = oos_total
            metrics["oos_win_rate"] = oos_win_rate * 100
        else:
            criteria_met["oos_validation"] = True  # 没有OOS数据时跳过

        # 总结
        can_freeze = all(criteria_met.values())

        logger.info("=" * 60)
        logger.info("固化条件评估结果")
        logger.info("=" * 60)
        logger.info(f"1. 盈利轮数: {profit_cycles}/{total_cycles} {'✓' if criteria_met.get('min_profit_cycles') else '✗'}")
        logger.info(f"2. 胜率: {win_rate*100:.1f}% {'✓' if criteria_met.get('min_win_rate') else '✗'}")
        logger.info(f"3. 总收益: {total_return:+.2f}% {'✓' if criteria_met.get('positive_return') else '✗'}")

        if self.criteria.require_excess_return:
            hs300 = metrics.get("hs300_return", 0)
            excess = metrics.get("excess_return", 0)
            logger.info(f"4. 超额收益(vs沪深300): {excess:+.2f}% {'✓' if criteria_met.get('excess_return') else '✗'}")

        if self.criteria.require_max_drawdown:
            dd = metrics.get("avg_max_drawdown", 0)
            logger.info(f"5. 平均最大回撤: {dd:.2f}% {'✓' if criteria_met.get('max_drawdown') else '✗'}")

        if self.criteria.require_sharpe:
            sharpe = metrics.get("avg_sharpe_ratio", 0)
            logger.info(f"6. Sharpe Ratio: {sharpe:.2f} {'✓' if criteria_met.get('sharpe_ratio') else '✗'}")

        if self.criteria.require_bull_bear:
            bb = metrics.get("covered", False)
            logger.info(f"7. 牛熊市覆盖: {'是' if bb else '否'} {'✓' if criteria_met.get('bull_bear_coverage') else '✗'}")

        if self.criteria.require_oos_validation:
            oos = criteria_met.get("oos_validation", False)
            logger.info(f"8. OOS验证: {'通过' if oos else '未通过'} {'✓' if oos else '✗'}")

        logger.info("=" * 60)
        logger.info(f"最终结果: {'✓ 可以固化' if can_freeze else '✗ 不满足条件'}")
        logger.info("=" * 60)

        return FreezeResult(
            can_freeze=can_freeze,
            criteria_met=criteria_met,
            metrics=metrics,
            details={
                "criteria": self.criteria.__dict__,
                "cycle_results": len(cycle_results),
            }
        )


class EnhancedSelfLearningController:
    """
    增强版自我学习控制器

    使用修正后的固化条件
    """

    def __init__(self):
        self.freeze_evaluator = FreezeEvaluator()

        # 固化条件
        self.freeze_criteria = FreezeCriteria(
            min_profit_cycles=7,
            min_win_rate=0.7,
            require_excess_return=True,
            require_max_drawdown=True,
            max_drawdown_threshold=15.0,
            require_sharpe=True,
            min_sharpe=1.0,
            require_bull_bear=True,
            require_oos_validation=True,
        )

    def should_freeze(self, cycle_results: List[Dict], oos_results: List[Dict] = None) -> FreezeResult:
        """
        判断是否满足固化条件

        Args:
            cycle_results: 训练结果
            oos_results: OOS验证结果

        Returns:
            固化评估结果
        """
        # 先加载沪深300数据
        if cycle_results:
            # 找到日期范围
            dates = [r.get("cutoff_date", "") for r in cycle_results if r.get("cutoff_date")]
            if dates:
                min_date = min(dates)
                # 转换为日期格式
                try:
                    dt = datetime.strptime(min_date, "%Y%m%d")
                    start = (dt - timedelta(days=100)).strftime("%Y-%m-%d")
                    self.freeze_evaluator.load_hs300_data(start)
                except Exception:
                    pass

        return self.freeze_evaluator.evaluate(cycle_results, oos_results)



# ============================================================
# model_freezer.py
# ============================================================

"""
模型固化模块 - 模型固化与输出报告

功能：
1. 检测固化条件（连续10周期中7次盈利）
2. 固化策略参数
3. 生成最终报告
4. 导出可复用的模型配置
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class FrozenModel:
    """固化模型"""
    model_id: str
    created_at: str

    # 策略信息
    strategy_name: str
    strategy_params: Dict

    # 训练统计
    total_training_cycles: int
    profit_cycles: int
    loss_cycles: int
    profit_rate: float
    avg_return_pct: float
    total_profit: float

    # 评估指标
    avg_signal_accuracy: float
    avg_timing_score: float
    avg_risk_score: float

    # 详细信息
    cycle_history: List[Dict]
    success_cycles: List[Dict]
    failure_cycles: List[Dict]

    # 结论
    conclusion: str

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, output_path: Path):
        """保存到文件"""
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


class ModelFreezer:
    """
    模型固化器

    核心功能：
    - 检测是否满足固化条件
    - 收集和整理训练数据
    - 生成固化报告
    - 导出模型配置
    """

    def __init__(self, output_dir: str = None):
        """
        初始化模型固化器

        Args:
            output_dir: 输出目录
        """
        if output_dir is None:
            from config import config

            output_dir = config.output_dir

        self.output_dir = Path(output_dir)
        self.frozen_models_dir = self.output_dir / "frozen_models"
        self.frozen_models_dir.mkdir(parents=True, exist_ok=True)

    def check_freeze_condition(
        self,
        cycle_history: List[Dict],
        total_cycles: int = 10,
        profit_required: int = 7,
    ) -> Dict:
        """
        检查是否满足固化条件

        Args:
            cycle_history: 周期历史
            total_cycles: 统计周期数（默认10）
            profit_required: 盈利次数要求（默认7）

        Returns:
            dict: 检查结果
        """
        if len(cycle_history) < total_cycles:
            return {
                "can_freeze": False,
                "reason": f"训练周期不足，需要至少 {total_cycles} 个周期",
                "current_cycles": len(cycle_history),
                "required_cycles": total_cycles,
            }

        # 取最近 N 个周期
        recent = cycle_history[-total_cycles:]
        profit_count = sum(1 for r in recent if r.get("return_pct", 0) > 0)
        loss_count = total_cycles - profit_count

        can_freeze = profit_count >= profit_required

        return {
            "can_freeze": can_freeze,
            "reason": (
                f"满足固化条件: 最近 {total_cycles} 个周期中 {profit_count} 次盈利"
                if can_freeze
                else f"不满足固化条件: 最近 {total_cycles} 个周期中仅 {profit_count} 次盈利，需要 {profit_required} 次"
            ),
            "recent_cycles": total_cycles,
            "profit_count": profit_count,
            "loss_count": loss_count,
            "profit_rate": profit_count / total_cycles,
            "required_profit": profit_required,
        }

    def freeze(
        self,
        strategy_name: str,
        strategy_params: Dict,
        cycle_history: List[Dict],
        evaluation_history: List[Dict] = None,
    ) -> FrozenModel:
        """
        固化模型

        Args:
            strategy_name: 策略名称
            strategy_params: 策略参数
            cycle_history: 周期历史
            evaluation_history: 评估历史（可选）

        Returns:
            FrozenModel: 固化模型
        """
        logger.info("开始固化模型...")

        # 统计
        total = len(cycle_history)
        profits = [r for r in cycle_history if r.get("return_pct", 0) > 0]
        losses = [r for r in cycle_history if r.get("return_pct", 0) <= 0]

        # 计算平均收益
        avg_return = sum(r.get("return_pct", 0) for r in cycle_history) / total if total > 0 else 0
        total_profit = sum(r.get("profit_loss", 0) for r in profits)

        # 评估指标
        if evaluation_history:
            avg_signal = sum(e.get("signal_accuracy", 0) for e in evaluation_history) / len(evaluation_history)
            avg_timing = sum(e.get("timing_score", 0) for e in evaluation_history) / len(evaluation_history)
            avg_risk = sum(e.get("risk_control_score", 0) for e in evaluation_history) / len(evaluation_history)
        else:
            avg_signal = 0.5
            avg_timing = 0.5
            avg_risk = 0.5

        # 生成模型ID
        model_id = f"model_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # 生成结论
        conclusion = self._generate_conclusion(
            strategy_name,
            total,
            len(profits),
            avg_return,
            avg_signal,
            avg_timing,
            avg_risk,
        )

        # 创建固化模型
        model = FrozenModel(
            model_id=model_id,
            created_at=datetime.now().isoformat(),
            strategy_name=strategy_name,
            strategy_params=strategy_params,
            total_training_cycles=total,
            profit_cycles=len(profits),
            loss_cycles=len(losses),
            profit_rate=len(profits) / total,
            avg_return_pct=avg_return,
            total_profit=total_profit,
            avg_signal_accuracy=avg_signal,
            avg_timing_score=avg_timing,
            avg_risk_score=avg_risk,
            cycle_history=cycle_history,
            success_cycles=profits,
            failure_cycles=losses,
            conclusion=conclusion,
        )

        # 保存
        model_path = self.frozen_models_dir / f"{model_id}.json"
        model.save(model_path)

        # 同时保存最新固化模型
        latest_path = self.frozen_models_dir / "latest.json"
        model.save(latest_path)

        logger.info(f"模型已固化: {model_id}, 保存至: {model_path}")

        return model

    def _generate_conclusion(
        self,
        strategy_name: str,
        total_cycles: int,
        profit_count: int,
        avg_return: float,
        avg_signal: float,
        avg_timing: float,
        avg_risk: float,
    ) -> str:
        """生成结论"""
        lines = []

        lines.append(f"## 模型固化结论")
        lines.append("")
        lines.append(f"**策略名称**: {strategy_name}")
        lines.append(f"**训练周期数**: {total_cycles}")
        lines.append(f"**盈利周期数**: {profit_count} ({profit_count/total_cycles*100:.1f}%)")
        lines.append(f"**平均收益率**: {avg_return:.2f}%")
        lines.append("")

        # 评分总结
        lines.append("### 评分总结")
        lines.append("")
        lines.append(f"- 信号准确率: {avg_signal:.2f}/1.0")
        lines.append(f"- 时机选择: {avg_timing:.2f}/1.0")
        lines.append(f"- 风控表现: {avg_risk:.2f}/1.0")
        lines.append("")

        # 综合评价
        overall_score = (avg_signal + avg_timing + avg_risk) / 3

        if overall_score >= 0.7:
            rating = "优秀"
            desc = "该策略在测试中表现优秀，建议继续使用。"
        elif overall_score >= 0.5:
            rating = "良好"
            desc = "该策略在测试中表现良好，可作为参考。"
        else:
            rating = "一般"
            desc = "该策略表现一般，建议进一步优化或谨慎使用。"

        lines.append(f"### 综合评价: {rating}")
        lines.append("")
        lines.append(desc)
        lines.append("")

        # 风险提示
        lines.append("### 风险提示")
        lines.append("")
        lines.append("1. 历史表现不代表未来收益")
        lines.append("2. 请根据自身风险承受能力调整仓位")
        lines.append("3. 建议持续监控策略表现，及时调整")
        lines.append("")

        # 使用建议
        lines.append("### 使用建议")
        lines.append("")
        lines.append(f"- 建议仓位: {20 + overall_score * 30:.0f}%")
        lines.append(f"- 止损线: {5 - overall_score * 2:.0f}%")
        lines.append(f"- 止盈线: {10 + overall_score * 5:.0f}%")

        return "\n".join(lines)

    def export_strategy_config(
        self,
        model: FrozenModel,
    ) -> Dict:
        """
        导出策略配置（可用于后续加载）

        Args:
            model: 固化模型

        Returns:
            dict: 策略配置
        """
        config = {
            "strategy_name": model.strategy_name,
            "strategy_params": model.strategy_params,
            "model_id": model.model_id,
            "created_at": model.created_at,

            # 交易参数建议
            "recommended": {
                "position_size": min(0.2 + model.avg_return_pct / 100, 0.5),
                "stop_loss": 0.05,
                "take_profit": 0.15,
                "max_positions": 5,
            },

            # 统计信息
            "statistics": {
                "total_cycles": model.total_training_cycles,
                "profit_rate": model.profit_rate,
                "avg_return_pct": model.avg_return_pct,
                "avg_signal_accuracy": model.avg_signal_accuracy,
                "avg_timing_score": model.avg_timing_score,
                "avg_risk_score": model.avg_risk_score,
            },
        }

        # 保存
        config_path = self.frozen_models_dir / f"{model.model_id}_config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        return config

    def get_latest_model(self) -> Optional[FrozenModel]:
        """获取最新固化的模型"""
        latest_path = self.frozen_models_dir / "latest.json"

        if not latest_path.exists():
            return None

        try:
            with open(latest_path, encoding="utf-8") as f:
                data = json.load(f)
                return FrozenModel(**data)
        except Exception as e:
            logger.error(f"加载最新模型失败: {e}")
            return None

    def list_frozen_models(self) -> List[Dict]:
        """列出所有固化模型"""
        models = []

        for file in self.frozen_models_dir.glob("model_*.json"):
            try:
                with open(file, encoding="utf-8") as f:
                    data = json.load(f)
                    models.append({
                        "model_id": data.get("model_id"),
                        "created_at": data.get("created_at"),
                        "strategy_name": data.get("strategy_name"),
                        "profit_rate": data.get("profit_rate"),
                        "avg_return_pct": data.get("avg_return_pct"),
                    })
            except Exception:
                continue

        models.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return models



# ============================================================
# case_library.py
# ============================================================

"""
案例库管理 - 成功/失败案例库

功能：
1. 成功案例库（盈利策略）
2. 失败案例库（亏损策略）
3. 策略参数记录
4. 策略禁用检查

借鉴: GeneTrader strategy database
"""

import json
import logging
import hashlib
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict, field

logger = logging.getLogger(__name__)


@dataclass
class StrategyCase:
    """策略案例"""
    case_id: str
    case_type: str  # "success" or "failure"

    # 策略参数
    strategy_name: str
    strategy_params: Dict

    # 训练结果
    cycle_id: int
    cutoff_date: str
    initial_capital: float
    final_value: float
    return_pct: float
    profit_loss: float

    # 选股结果
    selected_stocks: List[str]

    # 交易统计
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float

    # 时间戳
    created_at: str

    # 原因
    reason: str  # "连续3次盈利" or "连续3次亏损"

    # 元数据
    tags: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def generate_id(params: Dict) -> str:
        """根据策略参数生成唯一ID"""
        params_str = json.dumps(params, sort_keys=True)
        return hashlib.md5(params_str.encode()).hexdigest()[:12]


class CaseLibrary:
    """
    案例库管理器

    功能：
    - 存储成功/失败案例
    - 查询策略是否被禁用
    - 统计案例库信息
    """

    def __init__(self, library_dir: str = None):
        """
        初始化案例库

        Args:
            library_dir: 案例库目录路径
        """
        if library_dir is None:
            from config import config

            library_dir = config.case_library_dir

        self.library_dir = Path(library_dir)
        self.library_dir.mkdir(parents=True, exist_ok=True)

        # 文件路径
        self.success_file = self.library_dir / "success_cases.json"
        self.failure_file = self.library_dir / "failure_cases.json"

        # 加载已有案例
        self.success_cases: List[StrategyCase] = self._load_cases(self.success_file)
        self.failure_cases: List[StrategyCase] = self._load_cases(self.failure_file)

        logger.info(
            f"案例库初始化: 成功案例 {len(self.success_cases)} 个, "
            f"失败案例 {len(self.failure_cases)} 个"
        )

    def _load_cases(self, file_path: Path) -> List[StrategyCase]:
        """加载案例"""
        if not file_path.exists():
            return []

        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
                return [StrategyCase(**case) for case in data]
        except Exception as e:
            logger.warning(f"加载案例失败: {file_path}, {e}")
            return []

    def _save_cases(self, file_path: Path, cases: List[StrategyCase]):
        """保存案例"""
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump([c.to_dict() for c in cases], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存案例失败: {file_path}, {e}")

    def _save(self):
        """保存所有案例"""
        self._save_cases(self.success_file, self.success_cases)
        self._save_cases(self.failure_file, self.failure_cases)

    def add_success_case(
        self,
        strategy_name: str,
        strategy_params: Dict,
        cycle_result: Dict,
        reason: str = "连续3次盈利",
    ) -> StrategyCase:
        """
        添加成功案例

        Args:
            strategy_name: 策略名称
            strategy_params: 策略参数
            cycle_result: 周期结果
            reason: 原因

        Returns:
            StrategyCase: 创建的案例
        """
        case = StrategyCase(
            case_id=StrategyCase.generate_id(strategy_params),
            case_type="success",
            strategy_name=strategy_name,
            strategy_params=strategy_params,
            cycle_id=cycle_result.get("cycle_id", 0),
            cutoff_date=cycle_result.get("cutoff_date", ""),
            initial_capital=cycle_result.get("initial_capital", 0),
            final_value=cycle_result.get("final_value", 0),
            return_pct=cycle_result.get("return_pct", 0),
            profit_loss=cycle_result.get("profit_loss", 0),
            selected_stocks=cycle_result.get("selected_stocks", []),
            total_trades=cycle_result.get("total_trades", 0),
            winning_trades=cycle_result.get("winning_trades", 0),
            losing_trades=cycle_result.get("losing_trades", 0),
            win_rate=cycle_result.get("win_rate", 0),
            created_at=datetime.now().isoformat(),
            reason=reason,
        )

        self.success_cases.append(case)
        self._save()

        logger.info(f"添加成功案例: {case.case_id}, 策略: {strategy_name}")
        return case

    def add_failure_case(
        self,
        strategy_name: str,
        strategy_params: Dict,
        cycle_result: Dict,
        reason: str = "连续3次亏损",
    ) -> StrategyCase:
        """
        添加失败案例

        Args:
            strategy_name: 策略名称
            strategy_params: 策略参数
            cycle_result: 周期结果
            reason: 原因

        Returns:
            StrategyCase: 创建的案例
        """
        case = StrategyCase(
            case_id=StrategyCase.generate_id(strategy_params),
            case_type="failure",
            strategy_name=strategy_name,
            strategy_params=strategy_params,
            cycle_id=cycle_result.get("cycle_id", 0),
            cutoff_date=cycle_result.get("cutoff_date", ""),
            initial_capital=cycle_result.get("initial_capital", 0),
            final_value=cycle_result.get("final_value", 0),
            return_pct=cycle_result.get("return_pct", 0),
            profit_loss=cycle_result.get("profit_loss", 0),
            selected_stocks=cycle_result.get("selected_stocks", []),
            total_trades=cycle_result.get("total_trades", 0),
            winning_trades=cycle_result.get("winning_trades", 0),
            losing_trades=cycle_result.get("losing_trades", 0),
            win_rate=cycle_result.get("win_rate", 0),
            created_at=datetime.now().isoformat(),
            reason=reason,
        )

        self.failure_cases.append(case)
        self._save()

        logger.warning(f"添加失败案例: {case.case_id}, 策略: {strategy_name}")
        return case

    def is_strategy_allowed(self, strategy_params: Dict) -> bool:
        """
        检查策略是否允许使用

        Args:
            strategy_params: 策略参数

        Returns:
            bool: 是否允许使用
        """
        case_id = StrategyCase.generate_id(strategy_params)

        # 检查是否在失败案例中
        for case in self.failure_cases:
            if case.case_id == case_id:
                logger.info(f"策略 {case_id} 已被禁用")
                return False

        return True

    def get_strategy_stats(self, strategy_name: str = None) -> Dict:
        """
        获取策略统计

        Args:
            strategy_name: 策略名称（可选）

        Returns:
            dict: 统计信息
        """
        success = self.success_cases
        failure = self.failure_cases

        if strategy_name:
            success = [c for c in success if c.strategy_name == strategy_name]
            failure = [c for c in failure if c.strategy_name == strategy_name]

        return {
            "success_count": len(success),
            "failure_count": len(failure),
            "total_cases": len(success) + len(failure),
            "success_rate": len(success) / (len(success) + len(failure)) if (success or failure) else 0,
            "avg_return_pct": sum(c.return_pct for c in success) / len(success) if success else 0,
            "total_profit": sum(c.profit_loss for c in success),
            "total_loss": sum(c.profit_loss for c in failure),
        }

    def get_recent_cases(self, case_type: str = None, limit: int = 10) -> List[StrategyCase]:
        """
        获取最近的案例

        Args:
            case_type: "success" or "failure" or None
            limit: 返回数量

        Returns:
            list: 案例列表
        """
        all_cases = []

        if case_type is None or case_type == "success":
            all_cases.extend(self.success_cases)
        if case_type is None or case_type == "failure":
            all_cases.extend(self.failure_cases)

        # 按创建时间排序
        all_cases.sort(key=lambda x: x.created_at, reverse=True)

        return all_cases[:limit]

    def export_report(self) -> str:
        """
        导出案例库报告

        Returns:
            str: 报告文本
        """
        lines = []
        lines.append("# 策略案例库报告")
        lines.append("")
        lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        # 统计
        stats = self.get_strategy_stats()
        lines.append("## 统计概览")
        lines.append("")
        lines.append(f"- 成功案例: {stats['success_count']} 个")
        lines.append(f"- 失败案例: {stats['failure_count']} 个")
        lines.append(f"- 成功率: {stats['success_rate']*100:.1f}%")
        lines.append(f"- 平均收益率: {stats['avg_return_pct']:.2f}%")
        lines.append(f"- 总盈利: {stats['total_profit']:.2f}")
        lines.append(f"- 总亏损: {stats['total_loss']:.2f}")
        lines.append("")

        # 最近案例
        lines.append("## 最近案例")
        lines.append("")

        recent = self.get_recent_cases(limit=5)
        for case in recent:
            lines.append(f"### {case.strategy_name} ({case.case_type})")
            lines.append(f"- 案例ID: {case.case_id}")
            lines.append(f"- 周期: #{case.cycle_id}")
            lines.append(f"- 收益率: {case.return_pct:.2f}%")
            lines.append(f"- 原因: {case.reason}")
            lines.append(f"- 创建时间: {case.created_at}")
            lines.append("")

        return "\n".join(lines)

    def clear(self, case_type: str = None):
        """
        清空案例库

        Args:
            case_type: "success" or "failure" or None (全部)
        """
        if case_type is None:
            self.success_cases = []
            self.failure_cases = []
        elif case_type == "success":
            self.success_cases = []
        elif case_type == "failure":
            self.failure_cases = []

        self._save()
        logger.info(f"案例库已清空: {case_type or '全部'}")



# ============================================================
# strategy_manager.py
# ============================================================

"""
策略管理器 - 可插拔策略管理

功能：
1. 策略注册与注销
2. 策略切换
3. 策略参数管理
4. 策略表现追踪

这是自我进化系统的核心模块，支持策略的动态替换和演化
"""

import logging
import json
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from enum import Enum

logger = logging.getLogger(__name__)


class StrategyStatus(Enum):
    """策略状态"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    TESTING = "testing"
    ELIMINATED = "eliminated"
    FROZEN = "frozen"


@dataclass
class StrategyConfig:
    """策略配置"""
    name: str
    description: str = ""
    version: str = "1.0"

    # 策略参数
    params: Dict = field(default_factory=dict)

    # 策略函数
    select_stocks_func: Callable = None  # 选股函数
    analyze_func: Callable = None  # 分析函数
    signal_func: Callable = None  # 信号生成函数

    # 状态
    status: str = "inactive"

    # 表现统计
    total_runs: int = 0
    profit_runs: int = 0
    loss_runs: int = 0
    avg_return: float = 0.0

    # 时间戳
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        """转换为字典"""
        data = asdict(self)
        # 移除函数对象
        data.pop("select_stocks_func", None)
        data.pop("analyze_func", None)
        data.pop("signal_func", None)
        return data


class StrategyManager:
    """
    策略管理器

    核心功能：
    - 注册新策略
    - 切换当前使用的策略
    - 更新策略参数
    - 追踪策略表现
    - 支持策略进化（参数自动调整）
    """

    def __init__(self, config_dir: str = None):
        """
        初始化策略管理器

        Args:
            config_dir: 策略配置目录
        """
        if config_dir is None:
            from config import config

            config_dir = config.output_dir / "strategies"

        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # 策略注册表
        self.strategies: Dict[str, StrategyConfig] = {}

        # 当前活跃策略
        self.active_strategy: Optional[str] = None

        # 默认策略
        self._register_default_strategies()

        logger.info(f"策略管理器初始化完成: {len(self.strategies)} 个策略")

    def _register_default_strategies(self):
        """注册默认策略"""
        # 1. 趋势跟踪策略
        self.register_strategy(
            name="trend_following",
            description="趋势跟踪策略 - 跟随大盘/板块趋势选择股票",
            params={
                "ma_short": 5,
                "ma_long": 20,
                "trend_period": 20,
            },
            select_stocks_func=self._trend_following_selector,
        )

        # 2. 动量策略
        self.register_strategy(
            name="momentum",
            description="动量策略 - 选择近期涨幅靠前的股票",
            params={
                "momentum_period": 10,
                "top_n": 5,
                "min_volume": 1000000,
            },
            select_stocks_func=self._momentum_selector,
        )

        # 3. 价值策略
        self.register_strategy(
            name="value",
            description="价值策略 - 选择低估值的优质股票",
            params={
                "pe_threshold": 30,
                "pb_threshold": 3,
                "roe_threshold": 10,
            },
            select_stocks_func=self._value_selector,
        )

        # 4. 均衡策略
        self.register_strategy(
            name="balanced",
            description="均衡策略 - 综合多种因素均衡选择",
            params={
                "momentum_weight": 0.3,
                "value_weight": 0.3,
                "quality_weight": 0.4,
            },
            select_stocks_func=self._balanced_selector,
        )

        # 设置默认策略
        self.active_strategy = "trend_following"
        self.strategies["trend_following"].status = "active"

    def register_strategy(
        self,
        name: str,
        description: str = "",
        params: Dict = None,
        select_stocks_func: Callable = None,
        analyze_func: Callable = None,
        signal_func: Callable = None,
    ) -> StrategyConfig:
        """
        注册新策略

        Args:
            name: 策略名称
            description: 策略描述
            params: 策略参数
            select_stocks_func: 选股函数
            analyze_func: 分析函数
            signal_func: 信号函数

        Returns:
            StrategyConfig: 策略配置
        """
        if name in self.strategies:
            logger.warning(f"策略 {name} 已存在，将被覆盖")
        config = StrategyConfig(
            name=name,
            description=description,
            params=params or {},
            select_stocks_func=select_stocks_func,
            analyze_func=analyze_func,
            signal_func=signal_func,
            status="inactive",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )

        self.strategies[name] = config

        # 保存到文件
        self._save_strategy_config(name, config)

        logger.info(f"注册策略: {name}")
        return config

    def unregister_strategy(self, name: str) -> bool:
        """
        注销策略

        Args:
            name: 策略名称

        Returns:
            bool: 是否成功
        """
        if name not in self.strategies:
            logger.warning(f"策略 {name} 不存在")
            return False

        # 不能注销活跃策略
        if name == self.active_strategy:
            logger.warning(f"无法注销活跃策略 {name}")
            return False

        del self.strategies[name]

        # 删除配置文件
        config_file = self.config_dir / f"{name}.json"
        if config_file.exists():
            config_file.unlink()

        logger.info(f"注销策略: {name}")
        return True

    def activate_strategy(self, name: str) -> bool:
        """
        激活策略

        Args:
            name: 策略名称

        Returns:
            bool: 是否成功
        """
        if name not in self.strategies:
            logger.error(f"策略 {name} 不存在")
            return False

        # 禁用当前活跃策略
        if self.active_strategy and self.active_strategy in self.strategies:
            self.strategies[self.active_strategy].status = "inactive"

        # 激活新策略
        self.strategies[name].status = "active"
        self.active_strategy = name

        logger.info(f"激活策略: {name}")
        return True

    def update_strategy_params(self, name: str, params: Dict) -> bool:
        """
        更新策略参数

        Args:
            name: 策略名称
            params: 新参数

        Returns:
            bool: 是否成功
        """
        if name not in self.strategies:
            logger.error(f"策略 {name} 不存在")
            return False

        strategy = self.strategies[name]
        old_params = strategy.params.copy()

        # 更新参数
        strategy.params.update(params)
        strategy.updated_at = datetime.now().isoformat()

        # 保存
        self._save_strategy_config(name, strategy)

        logger.info(f"更新策略参数: {name}, {old_params} -> {strategy.params}")
        return True

    def get_active_strategy(self) -> Optional[StrategyConfig]:
        """获取当前活跃策略"""
        if self.active_strategy:
            return self.strategies.get(self.active_strategy)
        return None

    def get_strategy(self, name: str) -> Optional[StrategyConfig]:
        """获取指定策略"""
        return self.strategies.get(name)

    def list_strategies(self, status: str = None) -> List[StrategyConfig]:
        """
        列出策略

        Args:
            status: 状态过滤

        Returns:
            list: 策略列表
        """
        strategies = list(self.strategies.values())

        if status:
            strategies = [s for s in strategies if s.status == status]

        return strategies

    def record_run_result(
        self,
        strategy_name: str,
        is_profit: bool,
        return_pct: float,
    ):
        """
        记录策略运行结果

        Args:
            strategy_name: 策略名称
            is_profit: 是否盈利
            return_pct: 收益率
        """
        if strategy_name not in self.strategies:
            return

        strategy = self.strategies[strategy_name]
        strategy.total_runs += 1

        if is_profit:
            strategy.profit_runs += 1
        else:
            strategy.loss_runs += 1

        # 更新平均收益率
        total = strategy.total_runs
        old_avg = strategy.avg_return
        strategy.avg_return = (old_avg * (total - 1) + return_pct) / total

        strategy.updated_at = datetime.now().isoformat()

        self._save_strategy_config(strategy_name, strategy)

    def evolve_strategy(
        self,
        strategy_name: str,
        evolution_method: str = "random",
    ) -> Optional[Dict]:
        """
        演化策略

        根据运行结果自动调整参数

        Args:
            strategy_name: 策略名称
            evolution_method: 演化方法 ("random", "genetic", "bayesian")

        Returns:
            dict: 新的参数
        """
        if strategy_name not in self.strategies:
            return None

        strategy = self.strategies[strategy_name]

        if strategy.total_runs < 3:
            logger.info(f"策略 {strategy_name} 运行次数不足，等待更多数据")
            return None

        # 简单演化：如果是亏损，减少风险参数；如果是盈利，增加激进参数
        if strategy.avg_return < 0:
            # 亏损，减少风险
            new_params = strategy.params.copy()
            if "position_size" in new_params:
                new_params["position_size"] *= 0.8
            if "stop_loss" in new_params:
                new_params["stop_loss"] *= 0.9
        else:
            # 盈利，可适当增加风险
            new_params = strategy.params.copy()
            if "position_size" in new_params:
                new_params["position_size"] = min(new_params["position_size"] * 1.1, 0.5)
            if "take_profit" in new_params:
                new_params["take_profit"] *= 1.1

        # 应用新参数
        self.update_strategy_params(strategy_name, new_params)

        logger.info(f"策略 {strategy_name} 演化: {strategy.params} -> {new_params}")
        return new_params

    def _save_strategy_config(self, name: str, config: StrategyConfig):
        """保存策略配置"""
        config_file = self.config_dir / f"{name}.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)

    # ========== 内置选股函数 ==========

    def _trend_following_selector(
        self,
        stock_data: Dict,
        cutoff_date: str,
        params: Dict,
        max_stocks: int = 5,
    ) -> List[str]:
        """趋势跟踪选股"""
        import pandas as pd

        ma_short = params.get("ma_short", 5)
        ma_long = params.get("ma_long", 20)

        scores = []

        for ts_code, df in stock_data.items():
            if df is None or df.empty:
                continue

            # 过滤截止日期前的数据
            df = df[df["trade_date"] <= cutoff_date]
            if len(df) < ma_long:
                continue

            # 计算均线
            df = df.tail(60).copy()
            df["ma_s"] = df["close"].rolling(ma_short).mean()
            df["ma_l"] = df["close"].rolling(ma_long).mean()

            if df.iloc[-1]["ma_s"] > df.iloc[-1]["ma_l"]:
                # 上涨趋势
                score = (df.iloc[-1]["ma_s"] / df.iloc[-1]["ma_l"] - 1) * 100
                scores.append((ts_code, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scores[:max_stocks]]

    def _momentum_selector(
        self,
        stock_data: Dict,
        cutoff_date: str,
        params: Dict,
        max_stocks: int = 5,
    ) -> List[str]:
        """动量选股"""
        period = params.get("momentum_period", 10)

        scores = []

        for ts_code, df in stock_data.items():
            if df is None or df.empty:
                continue

            df = df[df["trade_date"] <= cutoff_date]
            if len(df) < period:
                continue

            # 计算动量
            recent = df.tail(period)
            momentum = (recent.iloc[-1]["close"] - recent.iloc[0]["close"]) / recent.iloc[0]["close"] * 100
            scores.append((ts_code, momentum))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scores[:max_stocks]]

    def _value_selector(
        self,
        stock_data: Dict,
        cutoff_date: str,
        params: Dict,
        max_stocks: int = 5,
    ) -> List[str]:
        """价值选股（简化版）"""
        # 简化：选择低波动率的股票作为价值股
        scores = []

        for ts_code, df in stock_data.items():
            if df is None or df.empty:
                continue

            df = df[df["trade_date"] <= cutoff_date]
            if len(df) < 20:
                continue

            # 低波动 = 价值
            volatility = df["pct_chg"].std()
            score = -volatility  # 负值，波动越小分数越高
            scores.append((ts_code, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scores[:max_stocks]]

    def _balanced_selector(
        self,
        stock_data: Dict,
        cutoff_date: str,
        params: Dict,
        max_stocks: int = 5,
    ) -> List[str]:
        """均衡选股"""
        momentum = self._momentum_selector(stock_data, cutoff_date, params, max_stocks * 2)
        value = self._value_selector(stock_data, cutoff_date, params, max_stocks * 2)

        # 合并去重
        combined = list(set(momentum + value))[:max_stocks]
        return combined


