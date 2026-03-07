"""
optimization.py 单元测试

覆盖：
  - StockSelector / AdaptiveSelector — 多因子选股
  - DynamicFactorWeight — IC/IR 动态权重
  - EvolutionEngine — 遗传算法进化
  - LLMOptimizer — 降级规则分析 + 策略修复
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
from invest.optimization import (
    StockSelector,
    AdaptiveSelector,
    DynamicFactorWeight,
    EvolutionEngine,
    Individual,
    LLMOptimizer,
    AnalysisResult,
)


# ============================================================
# Helpers
# ============================================================

def _make_stock_df(days: int = 120, seed: int = 0, trend: float = 0.001):
    """生成带趋势的模拟股票 DataFrame"""
    np.random.seed(seed)
    dates = pd.date_range("2022-01-01", periods=days, freq="B")
    close = 10.0 + np.cumsum(np.random.randn(days) * 0.3 + trend)
    close = np.maximum(close, 1.0)
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "trade_date": dates.strftime("%Y%m%d"),
        "open": close * (1 + np.random.randn(days) * 0.003),
        "high": close * (1 + np.abs(np.random.randn(days)) * 0.01),
        "low": close * (1 - np.abs(np.random.randn(days)) * 0.01),
        "close": close,
        "volume": np.random.randint(100000, 50000000, days).astype(float),
        "pct_chg": pd.Series(close).pct_change().fillna(0) * 100,
    })


def _make_stock_data(n: int = 20, days: int = 120):
    """生成多只股票数据"""
    return {f"sh.{600000+i}": _make_stock_df(days, seed=i) for i in range(n)}


# ============================================================
# StockSelector
# ============================================================

class TestStockSelector:
    """StockSelector 多因子选股"""

    def test_basic_select(self):
        """基本选股：返回 top_n 支股票"""
        sel = StockSelector()
        data = _make_stock_data(20, 120)
        result = sel.select(data, "2022-06-01", top_n=5)
        assert isinstance(result, list)
        assert len(result) <= 5
        # 返回的应该都是有效股票代码
        for code in result:
            assert code in data

    def test_cutoff_filter(self):
        """截止日期过滤：只使用 cutoff 前的数据"""
        sel = StockSelector()
        data = _make_stock_data(10, 120)
        # 使用较早的截止日，确保至少 60 天数据
        result_early = sel.select(data, "2022-04-15", top_n=5)
        # 结果应该是有效的（可能为空如果不足 60 天）
        assert isinstance(result_early, list)

    def test_empty_data(self):
        """空数据 → 空列表"""
        sel = StockSelector()
        assert sel.select({}, "2022-06-01") == []

    def test_insufficient_data(self):
        """数据不足 60 天 → 跳过该股票"""
        sel = StockSelector()
        data = {"sh.600001": _make_stock_df(days=30, seed=1)}
        result = sel.select(data, "2022-03-01", top_n=5)
        assert result == []

    def test_update_params(self):
        """参数更新"""
        sel = StockSelector()
        old_weights = dict(sel.weights)
        sel.update_params({"weights": {"momentum_5d": 0.99}})
        assert sel.weights["momentum_5d"] == 0.99
        assert old_weights["momentum_5d"] != 0.99


# ============================================================
# AdaptiveSelector
# ============================================================

class TestAdaptiveSelector:
    """AdaptiveSelector 自适应权重"""

    def test_regime_adjustment_bull(self):
        """牛市环境：动量因子权重增大"""
        sel = AdaptiveSelector()
        sel.market_regime = "bull"
        sel._adjust_weights_for_regime()
        assert sel.weights["momentum_5d"] == 0.15
        assert sel.weights["momentum_20d"] == 0.15
        assert sel.weights["ma_golden_cross"] == 0.20

    def test_regime_adjustment_bear(self):
        """熊市环境：价值/超卖因子权重增大"""
        sel = AdaptiveSelector()
        sel.market_regime = "bear"
        sel._adjust_weights_for_regime()
        assert sel.weights["low_pe"] == 0.20
        assert sel.weights["rsi_oversold"] == 0.20

    def test_regime_adjustment_oscillation(self):
        """震荡环境：均衡配置"""
        sel = AdaptiveSelector()
        sel.market_regime = "oscillation"
        sel._adjust_weights_for_regime()
        assert sel.weights["rsi_oversold"] == 0.15
        assert sel.weights["macd_bullish"] == 0.15


# ============================================================
# DynamicFactorWeight
# ============================================================

class TestDynamicFactorWeight:
    """DynamicFactorWeight IC/IR 权重"""

    def test_calculate_ic(self):
        """IC = 因子值与未来收益的相关系数"""
        dfw = DynamicFactorWeight(lookback=60)
        # 完美正相关
        factor_values = {"momentum": list(range(10))}
        future_returns = list(range(10))
        ic = dfw.calculate_ic(factor_values, future_returns)
        assert "momentum" in ic
        assert abs(ic["momentum"] - 1.0) < 1e-9

    def test_calculate_ic_negative_correlation(self):
        """负相关 → IC 接近 -1"""
        dfw = DynamicFactorWeight()
        ic = dfw.calculate_ic(
            {"factor_a": list(range(10))},
            list(range(9, -1, -1)),
        )
        assert ic["factor_a"] < -0.9

    def test_calculate_ir(self):
        """IR = mean(IC) / std(IC)"""
        dfw = DynamicFactorWeight()
        # 全是 1.0 → std=0 → IR should handle gracefully
        assert dfw.calculate_ir([1.0, 1.0, 1.0]) == 0.0
        # 实际 IC 序列
        ic_series = [0.1, 0.2, 0.15, 0.18, 0.12]
        ir = dfw.calculate_ir(ic_series)
        expected = np.mean(ic_series) / np.std(ic_series)
        assert abs(ir - expected) < 1e-9

    def test_update_weights(self):
        """权重按 IR 归一化"""
        dfw = DynamicFactorWeight()
        result = dfw.update_weights({"momentum": 0.5, "rsi": 0.2, "macd": 0.1})
        total = sum(result.values())
        assert abs(total - 1.0) < 1e-9  # 归一化为 1
        assert all(w > 0 for w in result.values())
        # factor_weights 也应被更新
        assert abs(sum(dfw.factor_weights.values()) - 1.0) < 1e-9


# ============================================================
# EvolutionEngine
# ============================================================

class TestEvolutionEngine:
    """EvolutionEngine 遗传算法"""

    def test_initialize_population(self):
        """种群初始化：数量正确 + 参数在范围内"""
        ee = EvolutionEngine(population_size=10)
        base = {"ma_short": 5, "ma_long": 20, "rsi_period": 14,
                "stop_loss_pct": 0.05, "take_profit_pct": 0.15, "position_size": 0.2}
        ee.initialize_population(base)
        assert len(ee.population) == 10
        # 第一个个体应为 base 参数
        assert ee.population[0].params["ma_short"] == 5
        # 所有个体参数都应存在
        for ind in ee.population:
            assert "ma_short" in ind.params
            assert "stop_loss_pct" in ind.params

    def test_evolve_preserves_population_size(self):
        """进化后种群数量不变"""
        ee = EvolutionEngine(population_size=10, elite_size=2)
        ee.initialize_population({"ma_short": 5, "ma_long": 20, "rsi_period": 14,
                                  "stop_loss_pct": 0.05, "take_profit_pct": 0.15, "position_size": 0.2})
        fitness = [float(i) for i in range(10)]
        new_pop = ee.evolve(fitness)
        assert len(new_pop) == 10
        assert ee.generation == 1

    def test_evolve_best_individual_tracked(self):
        """进化后追踪最优个体"""
        ee = EvolutionEngine(population_size=5)
        ee.initialize_population({"ma_short": 5, "ma_long": 20, "rsi_period": 14,
                                  "stop_loss_pct": 0.05, "take_profit_pct": 0.15, "position_size": 0.2})
        fitness = [1.0, 5.0, 3.0, 2.0, 4.0]
        ee.evolve(fitness)
        assert ee.best_individual is not None
        assert ee.best_individual.fitness == 5.0

    def test_get_best_params(self):
        """获取最优参数"""
        ee = EvolutionEngine(population_size=5)
        ee.initialize_population({"ma_short": 5, "ma_long": 20, "rsi_period": 14,
                                  "stop_loss_pct": 0.05, "take_profit_pct": 0.15, "position_size": 0.2})
        ee.evolve([1.0, 5.0, 3.0, 2.0, 4.0])
        params = ee.get_best_params()
        assert isinstance(params, dict)
        assert "ma_short" in params

    def test_zero_fitness_no_crash(self):
        """全零适应度不崩溃"""
        ee = EvolutionEngine(population_size=5)
        ee.initialize_population({"ma_short": 5, "ma_long": 20, "rsi_period": 14,
                                  "stop_loss_pct": 0.05, "take_profit_pct": 0.15, "position_size": 0.2})
        new_pop = ee.evolve([0.0] * 5)
        assert len(new_pop) == 5

    def test_negative_fitness_no_crash(self):
        """全负适应度不崩溃"""
        ee = EvolutionEngine(population_size=5)
        ee.initialize_population({"ma_short": 5, "ma_long": 20, "rsi_period": 14,
                                  "stop_loss_pct": 0.05, "take_profit_pct": 0.15, "position_size": 0.2})
        new_pop = ee.evolve([-10.0] * 5)
        assert len(new_pop) == 5

    def test_fitness_mismatch_auto_pad(self):
        """适应度数量不足时自动填充"""
        ee = EvolutionEngine(population_size=10)
        ee.initialize_population({"ma_short": 5, "ma_long": 20, "rsi_period": 14,
                                  "stop_loss_pct": 0.05, "take_profit_pct": 0.15, "position_size": 0.2})
        # 只提供 3 个适应度，应自动 pad 到 10
        new_pop = ee.evolve([1.0, 2.0, 3.0])
        assert len(new_pop) == 10


# ============================================================
# LLMOptimizer
# ============================================================

class TestLLMOptimizer:
    """LLMOptimizer 降级分析"""

    def test_default_analysis(self):
        """无 LLM 时降级到规则分析"""
        opt = LLMOptimizer()
        result = opt._default_analysis({"return_pct": -5.0})
        assert isinstance(result, AnalysisResult)
        assert len(result.suggestions) > 0
        assert "stop_loss_pct" in result.strategy_adjustments

    def test_generate_strategy_fix(self):
        """策略修复参数在合理范围内"""
        opt = LLMOptimizer()
        analysis = AnalysisResult(
            cause="测试",
            suggestions=["降低仓位"],
            strategy_adjustments={"stop_loss_pct": 0.03, "position_size": 0.10},
            new_strategy_needed=False,
        )
        fix = opt.generate_strategy_fix(analysis)
        assert isinstance(fix, dict)
        assert fix["stop_loss_pct"] == 0.03
        assert fix["position_size"] == 0.10

    def test_generate_strategy_fix_empty_adjustments(self):
        """分析结果无调整时 → 使用默认参数"""
        opt = LLMOptimizer()
        analysis = AnalysisResult(
            cause="测试", suggestions=[], strategy_adjustments={},
            new_strategy_needed=False,
        )
        fix = opt.generate_strategy_fix(analysis)
        assert "stop_loss_pct" in fix
        assert "take_profit_pct" in fix
        assert "position_size" in fix
