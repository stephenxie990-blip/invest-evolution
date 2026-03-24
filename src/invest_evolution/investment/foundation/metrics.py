from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional

import logging
import numpy as np

logger = logging.getLogger(__name__)

# Metrics attribution


def compute_per_stock_contribution(per_stock_pnl: Dict[str, float]) -> Dict[str, float]:
    total = sum(float(value) for value in per_stock_pnl.values())
    if total == 0:
        return {code: 0.0 for code in per_stock_pnl}
    return {code: float(value) / total for code, value in per_stock_pnl.items()}


# Metrics returns


def compute_total_return_pct(values: Iterable[float]) -> float:
    seq: List[float] = [float(item) for item in values]
    if len(seq) < 2 or seq[0] == 0:
        return 0.0
    return (seq[-1] - seq[0]) / seq[0] * 100


# Metrics benchmark


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

    评估策略资金曲线，计算全套量化指标并根据调用方给定门槛判断是否达标。
    Foundation 层不再内置策略合格线；若未提供 criteria，则仅计算指标。
    """

    def __init__(self, risk_free_rate: float = 0.03, criteria: Optional[Dict[str, float]] = None):
        self.risk_free_rate = risk_free_rate  # 年化无风险利率
        self.criteria = dict(criteria or {})

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

        checks = []
        if "excess_return" in self.criteria:
            checks.append((
                excess_return <= float(self.criteria["excess_return"]),
                f"超额收益{excess_return:.1f}% ≤ {self.criteria['excess_return']}%",
            ))
        if "sharpe_ratio" in self.criteria:
            checks.append((
                sharpe_ratio <= float(self.criteria["sharpe_ratio"]),
                f"Sharpe{sharpe_ratio:.2f} ≤ {self.criteria['sharpe_ratio']}",
            ))
        if "max_drawdown" in self.criteria:
            checks.append((
                max_drawdown >= float(self.criteria["max_drawdown"]),
                f"回撤{max_drawdown:.1f}% ≥ {self.criteria['max_drawdown']}%",
            ))
        if "calmar_ratio" in self.criteria:
            checks.append((
                calmar_ratio <= float(self.criteria["calmar_ratio"]),
                f"Calmar{calmar_ratio:.2f} ≤ {self.criteria['calmar_ratio']}",
            ))
        if "win_rate" in self.criteria:
            checks.append((
                win_rate <= float(self.criteria["win_rate"]),
                f"胜率{win_rate*100:.1f}% ≤ {float(self.criteria['win_rate'])*100}%",
            ))
        if "profit_loss_ratio" in self.criteria:
            checks.append((
                profit_loss_ratio <= float(self.criteria["profit_loss_ratio"]),
                f"盈亏比{profit_loss_ratio:.2f} ≤ {self.criteria['profit_loss_ratio']}",
            ))
        if "monthly_turnover" in self.criteria:
            checks.append((
                monthly_turnover >= float(self.criteria["monthly_turnover"]),
                f"月换手{monthly_turnover*100:.0f}% ≥ {float(self.criteria['monthly_turnover'])*100}%",
            ))
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


# Metrics cycle evaluation


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
    - 风控评分（0~1）  = (止损+止盈次数) / 总交易 + 配置基础分
    综合评分 = 模型配置中的三类权重加权
    """

    DEFAULT_POLICY = {
        "weights": {
            "signal_accuracy": 0.30,
            "timing": 0.30,
            "risk_control": 0.40,
        },
        "defaults": {
            "empty_signal_score": 0.50,
            "empty_timing_score": 0.50,
            "empty_risk_score": 0.50,
            "risk_control_base_score": 0.30,
        },
        "thresholds": {
            "signal_accuracy_low": 0.40,
            "timing_low": 0.40,
            "risk_control_low": 0.40,
            "win_rate_low": 0.40,
            "win_rate_high": 0.60,
            "high_trade_count": 20,
        },
    }

    def __init__(self, policy: Optional[Dict] = None):
        self.evaluation_history: List[EvaluationResult] = []
        self.policy: Dict = {}
        self.set_policy(policy)

    def set_policy(self, policy: Optional[Dict] = None) -> None:
        merged = {
            "weights": dict(self.DEFAULT_POLICY["weights"]),
            "defaults": dict(self.DEFAULT_POLICY["defaults"]),
            "thresholds": dict(self.DEFAULT_POLICY["thresholds"]),
        }
        for key, value in (policy or {}).items():
            if isinstance(value, dict) and key in merged:
                nested = dict(merged[key])
                nested.update(value)
                merged[key] = nested
            else:
                merged[key] = value
        self.policy = merged

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
        cycle_id = cycle_result.get("cycle_id", 0)
        return_pct = cycle_result.get("return_pct", 0.0)
        profit_loss = cycle_result.get("profit_loss", 0.0)
        is_profit = return_pct > 0

        logger.info(f"评估周期 #{cycle_id}: 收益率 {return_pct:.2f}%")

        signal_accuracy = self._evaluate_signal_accuracy(trade_history)
        timing_score = self._evaluate_timing(daily_records)
        risk_score = self._evaluate_risk_control(trade_history)

        weights = self.policy["weights"]
        overall_score = (
            signal_accuracy * float(weights.get("signal_accuracy", 0.30))
            + timing_score * float(weights.get("timing", 0.30))
            + risk_score * float(weights.get("risk_control", 0.40))
        )

        analysis = {
            "signal_accuracy": signal_accuracy,
            "timing_score": timing_score,
            "risk_control_score": risk_score,
            "total_trades": cycle_result.get("total_trades", 0),
            "winning_trades": cycle_result.get("winning_trades", 0),
            "losing_trades": cycle_result.get("losing_trades", 0),
            "win_rate": cycle_result.get("win_rate", 0.0),
            "selected_stocks": cycle_result.get("selected_stocks", []),
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
        default_score = float(self.policy["defaults"].get("empty_signal_score", 0.50))
        if not trade_history:
            return default_score
        winning = sum(1 for t in trade_history if t.get("pnl", 0) > 0)
        total = len(trade_history)
        return winning / total if total > 0 else default_score

    def _evaluate_timing(self, daily_records: Optional[List[Dict]]) -> float:
        default_score = float(self.policy["defaults"].get("empty_timing_score", 0.50))
        if not daily_records:
            return default_score
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
        defaults = self.policy["defaults"]
        default_score = float(defaults.get("empty_risk_score", 0.50))
        base_score = float(defaults.get("risk_control_base_score", 0.30))
        if not trade_history:
            return default_score
        sl_tp = sum(
            1 for t in trade_history
            if "止损" in t.get("reason", "") or "止盈" in t.get("reason", "")
        )
        total = len(trade_history)
        return min(1.0, sl_tp / total + base_score) if total > 0 else default_score

    def _generate_suggestions(
        self,
        is_profit: bool,
        signal_accuracy: float,
        timing_score: float,
        risk_score: float,
        analysis: Dict,
    ) -> List[str]:
        thresholds = self.policy["thresholds"]
        suggestions = []
        if signal_accuracy < float(thresholds.get("signal_accuracy_low", 0.40)):
            suggestions.append("信号准确率低，建议优化选股策略参数")
        if timing_score < float(thresholds.get("timing_low", 0.40)):
            suggestions.append("买入时机不佳，建议增加趋势确认条件")
        if risk_score < float(thresholds.get("risk_control_low", 0.40)):
            suggestions.append("风控执行不足，建议严格执行止损纪律")

        if not is_profit:
            if analysis.get("win_rate", 0) < float(thresholds.get("win_rate_low", 0.40)):
                suggestions.append("胜率低，建议降低交易频率或调整止损幅度")
            if analysis.get("total_trades", 0) > int(thresholds.get("high_trade_count", 20)):
                suggestions.append("交易过于频繁，建议减少无效交易")
            suggestions.append("当前周期亏损，建议降低仓位或暂停交易")
        elif analysis.get("win_rate", 0) > float(thresholds.get("win_rate_high", 0.60)):
            suggestions.append("策略表现良好，可考虑适当增加仓位")

        if not suggestions:
            suggestions.append("策略表现正常，继续保持当前参数")
        return suggestions

    def evaluate_consecutive_cycles(self, cycle_results: List[Dict]) -> Dict:
        """汇总分析连续多个周期"""
        if not cycle_results:
            return {"status": "no_data"}
        n = len(cycle_results)
        rets = [r.get("return_pct", 0) for r in cycle_results]
        profits = sum(1 for r in rets if r > 0)
        return {
            "total_cycles": n,
            "profit_count": profits,
            "loss_count": n - profits,
            "profit_rate": profits / n,
            "avg_return": sum(rets) / n,
            "consecutive_profit": self._count_consecutive(cycle_results, positive=True),
            "consecutive_loss": self._count_consecutive(cycle_results, positive=False),
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

    def __init__(self, risk_free_rate: float = 0.03, strategy_policy: Optional[Dict] = None):
        self.strategy_evaluator  = StrategyEvaluator(policy=strategy_policy)
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
logger = logging.getLogger(__name__)


@dataclass
class CycleMetrics:
    """单轮指标"""
    cycle_id: int
    cutoff_date: str
    return_pct: float
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0

__all__ = [name for name in globals() if not name.startswith('_')]
