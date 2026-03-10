import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


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

__all__ = ["BenchmarkMetrics", "BenchmarkEvaluator", "evaluate_benchmark"]



def evaluate_benchmark(
    daily_values: List[float],
    benchmark_daily_values: Optional[List[float]] = None,
    trade_history: Optional[List[Dict]] = None,
    *,
    risk_free_rate: float = 0.03,
    criteria: Optional[Dict[str, float]] = None,
):
    return BenchmarkEvaluator(risk_free_rate=risk_free_rate, criteria=criteria).evaluate(
        daily_values=daily_values,
        benchmark_daily_values=benchmark_daily_values,
        trade_history=trade_history,
    )
