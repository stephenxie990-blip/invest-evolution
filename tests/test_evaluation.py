"""
evaluation.py 单元测试

覆盖：
  - StrategyEvaluator（信号/时机/风控三维度评分）
  - BenchmarkEvaluator（Sharpe/Calmar/回撤/胜率等量化指标）
  - PerformanceAnalyzer（综合分析器）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from evaluation import (
    EvaluationResult,
    StrategyEvaluator,
    BenchmarkMetrics,
    BenchmarkEvaluator,
    PerformanceAnalyzer,
)


# ============================================================
# StrategyEvaluator
# ============================================================

class TestStrategyEvaluator:
    """StrategyEvaluator 三维度评分测试"""

    def test_profit_cycle(self):
        """盈利周期的评分计算"""
        ev = StrategyEvaluator()
        cycle = {"cycle_id": 1, "return_pct": 5.0, "profit_loss": 500.0,
                 "total_trades": 10, "winning_trades": 7, "losing_trades": 3, "win_rate": 0.7}
        trades = [{"pnl": 100, "reason": "止盈"}, {"pnl": -50, "reason": "止损"},
                  {"pnl": 80, "reason": "正常"}]
        daily = [{"total_value": 100000}, {"total_value": 101000},
                 {"total_value": 102000}, {"total_value": 103000}]
        result = ev.evaluate(cycle, trades, daily)

        assert isinstance(result, EvaluationResult)
        assert result.is_profit is True
        assert result.return_pct == 5.0
        assert 0 <= result.signal_accuracy <= 1
        assert 0 <= result.timing_score <= 1
        assert 0 <= result.risk_control_score <= 1
        assert 0 <= result.overall_score <= 1
        # 综合评分 = 0.3*signal + 0.3*timing + 0.4*risk
        expected = result.signal_accuracy * 0.3 + result.timing_score * 0.3 + result.risk_control_score * 0.4
        assert abs(result.overall_score - expected) < 1e-9

    def test_loss_cycle_has_suggestions(self):
        """亏损周期的建议列表应包含改进建议"""
        ev = StrategyEvaluator()
        cycle = {"cycle_id": 2, "return_pct": -3.0, "profit_loss": -300.0,
                 "total_trades": 10, "win_rate": 0.3}
        result = ev.evaluate(cycle)

        assert result.is_profit is False
        assert result.suggestions is not None
        assert len(result.suggestions) > 0
        # 亏损周期一定有这条建议
        assert any("亏损" in s for s in result.suggestions)

    def test_signal_accuracy_all_win(self):
        """全赢交易 → accuracy = 1.0"""
        ev = StrategyEvaluator()
        trades = [{"pnl": 10}, {"pnl": 20}, {"pnl": 5}]
        assert ev._evaluate_signal_accuracy(trades) == 1.0

    def test_signal_accuracy_empty(self):
        """无交易 → accuracy = 0.5（默认值）"""
        ev = StrategyEvaluator()
        assert ev._evaluate_signal_accuracy(None) == 0.5
        assert ev._evaluate_signal_accuracy([]) == 0.5

    def test_timing_score_no_drawdown(self):
        """资金单调上升 → timing = 1.0"""
        ev = StrategyEvaluator()
        daily = [{"total_value": v} for v in [100, 110, 120, 130, 140]]
        assert ev._evaluate_timing(daily) == 1.0

    def test_timing_score_with_drawdown(self):
        """50% 回撤 → timing = 0.5"""
        ev = StrategyEvaluator()
        daily = [{"total_value": v} for v in [100, 200, 100]]  # 200→100 = 50% dd
        score = ev._evaluate_timing(daily)
        assert abs(score - 0.5) < 1e-9

    def test_timing_score_empty(self):
        """无每日记录 → timing = 0.5"""
        ev = StrategyEvaluator()
        assert ev._evaluate_timing(None) == 0.5
        assert ev._evaluate_timing([]) == 0.5

    def test_risk_control_with_stops(self):
        """含止损/止盈 reason → 风控基础分 0.3 + 比例"""
        ev = StrategyEvaluator()
        trades = [
            {"pnl": -50, "reason": "止损触发"},
            {"pnl": 100, "reason": "止盈触发"},
            {"pnl": 30, "reason": "正常平仓"},
        ]
        score = ev._evaluate_risk_control(trades)
        # 2/3 + 0.3 = 0.967, capped at 1.0
        assert score > 0.3
        assert score <= 1.0

    def test_risk_control_empty(self):
        """无交易 → risk = 0.5"""
        ev = StrategyEvaluator()
        assert ev._evaluate_risk_control(None) == 0.5

    def test_evaluate_consecutive_cycles(self):
        """多周期汇总 + 连续盈亏计数"""
        ev = StrategyEvaluator()
        cycles = [
            {"return_pct": 1.0}, {"return_pct": 2.0}, {"return_pct": 3.0},  # 3 连盈
            {"return_pct": -1.0},
            {"return_pct": 0.5},
        ]
        summary = ev.evaluate_consecutive_cycles(cycles)
        assert summary["total_cycles"] == 5
        assert summary["profit_count"] == 4
        assert summary["loss_count"] == 1
        assert summary["consecutive_profit"] == 3
        assert summary["consecutive_loss"] == 1
        assert abs(summary["avg_return"] - 1.1) < 1e-9

    def test_evaluate_consecutive_empty(self):
        """空列表 → no_data"""
        ev = StrategyEvaluator()
        assert ev.evaluate_consecutive_cycles([]) == {"status": "no_data"}

    def test_evaluation_history_accumulates(self):
        """多次 evaluate 后 history 正确累积"""
        ev = StrategyEvaluator()
        for i in range(3):
            ev.evaluate({"cycle_id": i, "return_pct": float(i)})
        assert len(ev.evaluation_history) == 3

    def test_evaluation_report_single(self):
        """单周期报告生成"""
        ev = StrategyEvaluator()
        ev.evaluate({"cycle_id": 42, "return_pct": 2.5, "profit_loss": 250})
        report = ev.get_evaluation_report(cycle_id=42)
        assert "42" in report
        assert "2.50%" in report

    def test_evaluation_report_not_found(self):
        """未找到周期"""
        ev = StrategyEvaluator()
        assert "未找到" in ev.get_evaluation_report(cycle_id=999)


# ============================================================
# BenchmarkEvaluator
# ============================================================

class TestBenchmarkEvaluator:
    """BenchmarkEvaluator 量化指标测试"""

    def test_uptrend_curve(self):
        """连续上涨净值曲线 → 正收益、正 Sharpe"""
        be = BenchmarkEvaluator()
        # 每天涨 0.5%
        values = [100.0 * (1.005 ** i) for i in range(252)]
        m = be.evaluate(values, trading_days=252)

        assert m.total_return > 0
        assert m.annual_return > 0
        assert m.sharpe_ratio > 0
        assert m.max_drawdown >= 0  # 单调上涨，回撤应很小

    def test_downtrend_curve(self):
        """连续下跌 → 负收益"""
        be = BenchmarkEvaluator()
        values = [100.0 * (0.995 ** i) for i in range(100)]
        m = be.evaluate(values, trading_days=252)

        assert m.total_return < 0
        assert m.annual_return < 0

    def test_empty_or_single_value(self):
        """空 / 单条数据 → 默认 BenchmarkMetrics"""
        be = BenchmarkEvaluator()
        m0 = be.evaluate([])
        assert m0.total_return == 0.0
        assert m0.passed is False

        m1 = be.evaluate([100.0])
        assert m1.total_return == 0.0

    def test_criteria_pass(self):
        """优秀曲线应通过所有合格标准"""
        be = BenchmarkEvaluator()
        # 模拟一条高 Sharpe、低回撤的曲线
        np.random.seed(42)
        daily_ret = 0.003 + np.random.randn(252) * 0.005  # 均值 0.3% 日回报 + 小噪声
        values = [100.0]
        for r in daily_ret:
            values.append(values[-1] * (1 + r))
        # 带交易历史以覆盖胜率/盈亏比
        trades = [
            {"action": "SELL", "pnl": 500},
            {"action": "SELL", "pnl": 300},
            {"action": "SELL", "pnl": -100},
            {"action": "BUY", "pnl": 0},
        ]
        bench_values = [100.0 * (1 + 0.0002 * i) for i in range(len(values))]  # 基准微涨
        m = be.evaluate(values, bench_values, trades, trading_days=252)

        assert m.total_return > 0
        assert m.sharpe_ratio > 0
        # 检查 win_rate 正确: 2 wins / 3 sells
        assert abs(m.win_rate - 2.0 / 3) < 1e-9

    def test_benchmark_excess_return(self):
        """超额收益 = 策略收益 - 基准收益"""
        be = BenchmarkEvaluator()
        strategy_vals = [100.0, 110.0]   # +10%
        bench_vals = [100.0, 105.0]      # +5%
        m = be.evaluate(strategy_vals, bench_vals, trading_days=252)
        assert abs(m.excess_return - 5.0) < 1e-9
        assert abs(m.benchmark_return - 5.0) < 1e-9

    def test_win_rate_only_sells(self):
        """胜率仅统计卖出交易"""
        be = BenchmarkEvaluator()
        values = [100.0, 110.0]
        trades = [
            {"action": "BUY", "pnl": 0},
            {"action": "SELL", "pnl": 50},
            {"action": "SELL", "pnl": -20},
        ]
        m = be.evaluate(values, trade_history=trades, trading_days=252)
        # 1 win / 2 sells = 0.5
        assert abs(m.win_rate - 0.5) < 1e-9

    def test_max_drawdown_calculation(self):
        """最大回撤精确计算"""
        be = BenchmarkEvaluator()
        # 100 → 200 → 150 → 180: 最大回撤 = (200-150)/200 = 25%
        values = [100.0, 200.0, 150.0, 180.0]
        m = be.evaluate(values, trading_days=252)
        assert abs(m.max_drawdown - 25.0) < 1e-9


# ============================================================
# PerformanceAnalyzer
# ============================================================

class TestPerformanceAnalyzer:
    """PerformanceAnalyzer 综合测试"""

    def test_full_evaluate(self):
        """完整评估融合两个评估器"""
        pa = PerformanceAnalyzer()
        cycle = {"cycle_id": 1, "return_pct": 3.0, "profit_loss": 300}
        trades = [{"pnl": 100, "reason": "止盈", "action": "SELL"},
                  {"pnl": -30, "reason": "止损", "action": "SELL"}]
        daily = [{"total_value": v} for v in [100000, 101000, 102000, 103000]]

        report = pa.full_evaluate(cycle, trades, daily)
        assert "strategy" in report
        assert "benchmark" in report
        assert "summary" in report
        assert isinstance(report["strategy"], EvaluationResult)
        assert isinstance(report["benchmark"], BenchmarkMetrics)

    def test_best_cycles(self):
        """返回综合评分最高的 N 个周期"""
        pa = PerformanceAnalyzer()
        for i in range(5):
            pa.full_evaluate(
                {"cycle_id": i, "return_pct": float(i * 2), "profit_loss": float(i * 200)},
                daily_records=[{"total_value": 100000 + j * 100} for j in range(20)],
            )
        best = pa.get_best_cycles(top_n=2)
        assert len(best) == 2
        # 最高的应排在最前
        assert best[0]["strategy"].overall_score >= best[1]["strategy"].overall_score

    def test_overall_summary_empty(self):
        """空历史 → 暂无记录"""
        pa = PerformanceAnalyzer()
        assert "暂无" in pa.get_overall_summary()

    def test_overall_summary_with_data(self):
        """有数据时报告包含关键统计"""
        pa = PerformanceAnalyzer()
        for i in range(3):
            daily = [{"total_value": 100000 + j * 500} for j in range(50)]
            pa.full_evaluate(
                {"cycle_id": i, "return_pct": 2.0, "profit_loss": 200},
                daily_records=daily,
            )
        report = pa.get_overall_summary()
        assert "3 个周期" in report
        assert "Sharpe" in report
