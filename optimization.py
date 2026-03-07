"""
投资进化系统 - 优化层

包含：
1. StockSelector / AdaptiveSelector  — 多因子选股策略模型
2. AlphaFactorModel / DynamicFactorWeight  — Alpha 因子模型（IC 动态权重）
3. RiskFactorModel          — 风险因子（行业/市值中性化）
4. LLMOptimizer             — LLM 亏损分析 + 策略参数优化 (LLMGateway)
5. StrategyEvolutionOptimizer — 遗传进化 + LLM 结合的优化器
6. FrozenStrategy / StrategyLibrary — 策略库（固化存储）
7. DynamicWeightAllocator   — 动态权重分配（近期收益加权）
8. StrategyEnsemble         — 多策略集成（加权投票）
"""

import json
import logging
import random
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from config import config
from core import LLMCaller, compute_rsi, compute_macd_signal, compute_bb_position

logger = logging.getLogger(__name__)


# ============================================================
# Part 1: 多因子选股
# ============================================================

class StockSelector:
    """
    多因子选股策略模型

    因子：
    - 动量因子（5/20日）
    - 技术因子（RSI、MACD、布林带）
    - 质量因子（ROE 代理）
    - 趋势因子（均线金叉/死叉）
    """

    DEFAULT_WEIGHTS = {
        "momentum_5d":   0.10,
        "momentum_20d":  0.10,
        "low_pe":        0.10,
        "low_pb":        0.05,
        "high_roe":      0.15,
        "rsi_oversold":  0.10,
        "macd_bullish":  0.15,
        "bb_lower":      0.10,
        "ma_golden_cross": 0.15,
    }

    def __init__(self, params: Dict = None):
        self.params = params or {}
        self.weights = dict(self.DEFAULT_WEIGHTS)
        if "weights" in self.params:
            self.weights.update(self.params["weights"])

    def select(
        self,
        stock_data: Dict[str, pd.DataFrame],
        cutoff_date: str,
        top_n: int = 5,
    ) -> List[str]:
        """
        多因子选股，只使用 cutoff_date 之前的数据

        Args:
            stock_data: {ts_code: DataFrame}
            cutoff_date: 截止日期 (T0)
            top_n: 返回股票数量

        Returns:
            选中的股票代码列表
        """
        scores = {}
        for ts_code, df in stock_data.items():
            try:
                score = self._compute_stock_score(df, cutoff_date)
                if score is not None:
                    scores[ts_code] = score
            except Exception as e:
                logger.debug(f"计算 {ts_code} 得分失败: {e}")

        if not scores:
            logger.warning("没有股票通过筛选")
            return []

        sorted_stocks = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        selected = [s[0] for s in sorted_stocks[:top_n]]
        logger.info(f"选中股票: {selected}, 得分: {[f'{scores[s]:.2f}' for s in selected]}")
        return selected

    def _compute_stock_score(self, df: pd.DataFrame, cutoff_date: str) -> Optional[float]:
        date_col = "date" if "date" in df.columns else "trade_date"
        df = df[df[date_col] <= cutoff_date].copy()
        if len(df) < 60:
            return None

        factor_funcs = {
            "momentum_5d":    self._compute_momentum_5d,
            "momentum_20d":   self._compute_momentum_20d,
            "low_pe":         self._compute_low_pe,
            "low_pb":         self._compute_low_pb,
            "high_roe":       self._compute_high_roe,
            "rsi_oversold":   self._compute_rsi,
            "macd_bullish":   self._compute_macd,
            "bb_lower":       self._compute_bb_position,
            "ma_golden_cross": self._compute_ma_trend,
        }

        total = 0.0
        for factor_name, weight in self.weights.items():
            fn = factor_funcs.get(factor_name)
            if fn:
                try:
                    total += fn(df) * weight
                except Exception:
                    pass
        return total

    # ===== 因子计算函数 =====

    def _compute_momentum_5d(self, df: pd.DataFrame) -> float:
        if len(df) < 5:
            return 0.0
        past, current = df.iloc[-5]["close"], df.iloc[-1]["close"]
        return 0.0 if past == 0 else max(-1, min(1, (current - past) / past * 10))

    def _compute_momentum_20d(self, df: pd.DataFrame) -> float:
        if len(df) < 20:
            return 0.0
        past, current = df.iloc[-20]["close"], df.iloc[-1]["close"]
        return 0.0 if past == 0 else max(-1, min(1, (current - past) / past * 5))

    def _compute_low_pe(self, df: pd.DataFrame) -> float:
        if len(df) < 20:
            return 0.0
        recent = df.tail(20)
        vol = recent["close"].std() / (recent["close"].mean() or 1)
        return max(-1, min(1, 1 - vol * 10))

    def _compute_low_pb(self, df: pd.DataFrame) -> float:
        return self._compute_low_pe(df)

    def _compute_high_roe(self, df: pd.DataFrame) -> float:
        if len(df) < 10 or "pct_chg" not in df.columns:
            return 0.0
        avg = df.tail(10)["pct_chg"].mean()
        return max(-1, min(1, avg * 2))

    def _compute_rsi(self, df: pd.DataFrame) -> float:
        """RSI 因子：调用 core.compute_rsi() 共享实现"""
        close = pd.to_numeric(df["close"], errors="coerce").dropna()
        if len(close) < 14:
            return 0.0
        rsi = compute_rsi(close, 14)
        if 30 <= rsi <= 50:  return 1.0
        if rsi < 30:         return 0.5
        if rsi > 70:         return -0.5
        return 0.0

    def _compute_macd(self, df: pd.DataFrame) -> float:
        """MACD 因子：调用 core.compute_macd_signal() 共享实现"""
        close = pd.to_numeric(df["close"], errors="coerce").dropna()
        sig = compute_macd_signal(close)
        return {"金叉": 1.0, "看多": 0.5, "中性": 0.0, "看空": -0.3, "死叉": -1.0}.get(sig, 0.0)

    def _compute_bb_position(self, df: pd.DataFrame) -> float:
        """布林带因子：调用 core.compute_bb_position() 共享实现"""
        close = pd.to_numeric(df["close"], errors="coerce").dropna()
        pos = compute_bb_position(close, 20)
        if pos < 0.2: return 1.0
        if pos < 0.4: return 0.5
        if pos > 0.8: return -0.5
        return 0.0

    def _compute_ma_trend(self, df: pd.DataFrame) -> float:
        """均线趋势因子"""
        if len(df) < 20:
            return 0.0
        ma5  = df["close"].rolling(5).mean()
        ma20 = df["close"].rolling(20).mean()
        if ma5.iloc[-2] <= ma20.iloc[-2] and ma5.iloc[-1] > ma20.iloc[-1]:
            return 1.0
        if ma5.iloc[-2] >= ma20.iloc[-2] and ma5.iloc[-1] < ma20.iloc[-1]:
            return -1.0
        return 0.5 if ma5.iloc[-1] > ma20.iloc[-1] else -0.3

    def update_params(self, new_params: Dict):
        if "weights" in new_params:
            self.weights.update(new_params["weights"])
        self.params.update(new_params)

    def get_params(self) -> Dict:
        return {"weights": self.weights, "params": self.params}


class AdaptiveSelector(StockSelector):
    """
    自适应选股器

    根据市场环境（牛/熊/震荡）自动调整因子权重
    """

    def __init__(self, params: Dict = None):
        super().__init__(params)
        self.market_regime = "unknown"

    def select(self, stock_data: Dict, cutoff_date: str, top_n: int = 5) -> List[str]:
        if stock_data:
            sample_df = next(iter(stock_data.values()))
            self.market_regime = self._detect_market_regime(sample_df)
            self._adjust_weights_for_regime()
            logger.info(f"市场环境: {self.market_regime}")
        return super().select(stock_data, cutoff_date, top_n)

    def _detect_market_regime(self, df: pd.DataFrame) -> str:
        if len(df) < 60:
            return "unknown"
        recent = df.tail(60)
        chg = (recent.iloc[-1]["close"] - recent.iloc[0]["close"]) / (recent.iloc[0]["close"] or 1) * 100
        if chg > 10:  return "bull"
        if chg < -10: return "bear"
        return "sideways"

    def _adjust_weights_for_regime(self):
        if self.market_regime == "bull":
            self.weights.update({"momentum_5d": 0.15, "momentum_20d": 0.15, "ma_golden_cross": 0.20})
        elif self.market_regime == "bear":
            self.weights.update({"low_pe": 0.20, "rsi_oversold": 0.20, "bb_lower": 0.15})
        else:
            self.weights.update({"momentum_5d": 0.10, "rsi_oversold": 0.15, "macd_bullish": 0.15})


# ============================================================
# Part 2: Alpha / Risk 因子模型
# ============================================================

@dataclass
class FactorResult:
    """因子计算结果"""
    code: str
    factor_values: Dict[str, float]
    score: float
    weight_contribution: Dict[str, float]


class DynamicFactorWeight:
    """
    动态因子权重（IC 加权）

    IC = 因子值与未来收益的相关系数
    IR = IC均值 / IC标准差
    权重 ∝ max(IR, 0.01)，归一化
    """

    def __init__(self, lookback: int = 60):
        self.lookback = lookback
        self.factor_weights: Dict[str, float] = {}
        self.factor_ic_history: Dict[str, List[float]] = {}

    def calculate_ic(
        self,
        factor_values: Dict[str, List[float]],
        future_returns: List[float],
    ) -> Dict[str, float]:
        ic_values = {}
        for name, values in factor_values.items():
            n = min(len(values), len(future_returns))
            if n < 10:
                ic_values[name] = 0
                continue
            fv = np.array(values[-n:])
            fr = np.array(future_returns[-n:])
            if np.std(fv) > 0 and np.std(fr) > 0:
                ic = float(np.corrcoef(fv, fr)[0, 1])
                ic_values[name] = ic if not np.isnan(ic) else 0
            else:
                ic_values[name] = 0
        return ic_values

    def calculate_ir(self, ic_series: List[float]) -> float:
        if len(ic_series) < 2:
            return ic_series[-1] if ic_series else 0
        arr = np.array(ic_series)
        std = np.std(arr)
        return float(np.mean(arr) / std) if std > 0 else 0

    def update_weights(self, factor_ic: Dict[str, float]) -> Dict[str, float]:
        ir_values = {}
        for name, ic in factor_ic.items():
            hist = self.factor_ic_history.setdefault(name, [])
            hist.append(ic)
            self.factor_ic_history[name] = hist[-60:]
            ir_values[name] = self.calculate_ir(hist)

        weights = {name: max(ir, 0.01) for name, ir in ir_values.items()}
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        else:
            n = max(len(weights), 1)
            weights = {k: 1.0 / n for k in weights}

        self.factor_weights = weights
        logger.info(f"动态因子权重: {weights}")
        return weights


class AlphaFactorModel:
    """
    Alpha 因子模型

    默认因子：momentum_5/10/20, rsi, oversold, volatility
    支持 IC 动态权重
    """

    DEFAULT_FACTORS = {
        "momentum_5":  {"type": "momentum", "period": 5,  "weight": 0.20},
        "momentum_10": {"type": "momentum", "period": 10, "weight": 0.15},
        "momentum_20": {"type": "momentum", "period": 20, "weight": 0.15},
        "rsi":         {"type": "reversal", "period": 14, "weight": 0.20},
        "oversold":    {"type": "reversal",               "weight": 0.15},
        "volatility":  {"type": "volatility",             "weight": 0.15},
    }

    def __init__(self, use_dynamic_weight: bool = True):
        self.use_dynamic_weight = use_dynamic_weight
        self.dynamic_weight     = DynamicFactorWeight(lookback=60)
        self.factors            = dict(self.DEFAULT_FACTORS)

    def calculate_factors(self, stock_data: Dict[str, pd.DataFrame]) -> Dict[str, FactorResult]:
        results = {}
        for code, df in stock_data.items():
            if df is None or len(df) < 60:
                continue
            try:
                fv    = self._single_stock_factors(df)
                score = self._calculate_score(fv)
                results[code] = FactorResult(code=code, factor_values=fv, score=score, weight_contribution={})
            except Exception as e:
                logger.debug(f"因子计算 {code} 失败: {e}")
        return results

    def _single_stock_factors(self, df: pd.DataFrame) -> Dict[str, float]:
        close  = df["close"].values
        volume = df["volume"].values if "volume" in df.columns else np.ones(len(close))
        fv = {}

        for p in [5, 10, 20]:
            if len(close) >= p:
                fv[f"momentum_{p}"] = (close[-1] - close[-p]) / close[-p] * 100

        if len(close) >= 14:
            rsi = self._calc_rsi(close)
            fv["rsi"]     = rsi
            fv["oversold"] = 1.0 if rsi < 30 else 0.0

        if len(close) >= 20:
            rets = np.diff(close) / close[:-1]
            fv["volatility"] = float(np.std(rets[-20:]) * 100)

        return fv

    def _calc_rsi(self, close: np.ndarray, period: int = 14) -> float:
        """调用 core.compute_rsi() 共享实现"""
        return compute_rsi(pd.Series(close), period)

    def _calculate_score(self, factor_values: Dict[str, float]) -> float:
        weights = self.dynamic_weight.factor_weights if (
            self.use_dynamic_weight and self.dynamic_weight.factor_weights
        ) else {k: v["weight"] for k, v in self.factors.items()}

        score = 0.0
        for name, value in factor_values.items():
            score += self._normalize(name, value) * weights.get(name, 0.1)
        return score

    def _normalize(self, factor_name: str, value: float) -> float:
        if factor_name.startswith("momentum_"):
            return (value + 30) / 60
        if factor_name == "rsi":
            if value < 30: return 1.0 - value / 30
            if value > 70: return 0.0
            return 0.5
        if factor_name == "oversold":
            return value
        if factor_name == "volatility":
            return max(0, 1 - value / 10)
        return 0.5

    def rank_stocks(self, stock_data: Dict[str, pd.DataFrame], top_n: int = 20) -> List[FactorResult]:
        results = self.calculate_factors(stock_data)
        return sorted(results.values(), key=lambda x: x.score, reverse=True)[:top_n]

    def update_factor_weights(self, factor_ic: Dict[str, float]):
        if self.use_dynamic_weight:
            self.dynamic_weight.update_weights(factor_ic)


class RiskFactorModel:
    """
    风险因子模型

    行业中性化（每行业最多 max_per_industry 只）
    市值中性化（大/中/小盘各选一半）
    """

    def get_industry(self, code: str) -> str:
        from config import industry_registry
        return industry_registry.get_industry(code)

    def neutralize_by_industry(
        self, stocks: List[FactorResult], max_per_industry: int = 3
    ) -> List[FactorResult]:
        counts: Dict[str, int] = {}
        result = []
        for s in stocks:
            ind = self.get_industry(s.code)
            if counts.get(ind, 0) < max_per_industry:
                result.append(s)
                counts[ind] = counts.get(ind, 0) + 1
        return result

    def neutralize_by_market_cap(
        self, stocks: List[FactorResult], market_caps: Dict[str, float]
    ) -> List[FactorResult]:
        if not stocks:
            return stocks
        by_cap = sorted(stocks, key=lambda s: market_caps.get(s.code, 0), reverse=True)
        n = len(by_cap)
        result: List[FactorResult] = []
        for group in [by_cap[:n//3], by_cap[n//3:2*n//3], by_cap[2*n//3:]]:
            result.extend(group[:max(len(group)//2, 1)])
        return result

    def apply_risk_controls(
        self,
        stocks: List[FactorResult],
        market_caps: Optional[Dict[str, float]] = None,
        max_per_industry: int = 3,
    ) -> List[FactorResult]:
        result = self.neutralize_by_industry(stocks, max_per_industry)
        if market_caps:
            result = self.neutralize_by_market_cap(result, market_caps)
        return result


# ============================================================
# Part 3: LLM 优化器
# ============================================================

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
        try:
            match = re.search(r"\{[\s\S]*\}", response)
            if match:
                data = json.loads(match.group())
                return AnalysisResult(
                    cause=data.get("cause", "未知原因"),
                    suggestions=data.get("suggestions", []),
                    strategy_adjustments=data.get("strategy_adjustments", {}),
                    new_strategy_needed=data.get("new_strategy_needed", False),
                )
        except Exception as e:
            logger.warning(f"解析 LLM 响应失败: {e}")
        return self._default_analysis(cycle_result)

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


@dataclass
class Individual:
    """遗传算法个体（策略参数组合）"""
    params:     Dict
    fitness:    float = 0.0
    generation: int   = 0


class EvolutionEngine:
    """
    遗传算法策略进化引擎

    流程：初始化种群 → 适应度评估 → 选择 → 交叉 → 变异 → 精英保留
    """

    # 参数搜索空间
    PARAM_RANGES = {
        "ma_short":       (3,    10),
        "ma_long":        (15,   60),
        "rsi_period":     (7,    21),
        "rsi_oversold":   (20,   40),
        "rsi_overbought": (60,   80),
        "stop_loss_pct":  (0.03, 0.10),
        "take_profit_pct":(0.08, 0.20),
        "position_size":  (0.10, 0.30),
    }

    def __init__(
        self,
        population_size: int  = 20,
        mutation_rate:   float = 0.10,
        crossover_rate:  float = 0.70,
        elite_size:      int   = 2,
    ):
        self.population_size = population_size
        self.mutation_rate   = mutation_rate
        self.crossover_rate  = crossover_rate
        self.elite_size      = elite_size

        self.population:       List[Individual]     = []
        self.generation:       int                   = 0
        self.best_individual:  Optional[Individual] = None

    def initialize_population(self, base_params: Optional[Dict] = None):
        """初始化种群（第一个个体使用 base_params，其余随机）"""
        self.population = []
        for i in range(self.population_size):
            params = deepcopy(base_params) if (i == 0 and base_params) else self._random_params()
            self.population.append(Individual(params=params, fitness=0.0, generation=0))
        logger.info(f"初始化种群: {self.population_size} 个个体")

    def _random_params(self) -> Dict:
        params = {}
        for name, (lo, hi) in self.PARAM_RANGES.items():
            if name in ("ma_short", "ma_long", "rsi_period"):
                params[name] = random.randint(lo, hi)
            else:
                params[name] = random.uniform(lo, hi)
        return params

    def evolve(self, fitness_scores: List[float]) -> List[Individual]:
        """
        进化一代

        Args:
            fitness_scores: 与种群等长的适应度列表（通常是收益率 %）

        Returns:
            新种群
        """
        if len(fitness_scores) != len(self.population):
            logger.warning("适应度数量与种群大小不匹配: fitness=%s population=%s，自动对齐", len(fitness_scores), len(self.population))
            if len(fitness_scores) < len(self.population):
                pad = [fitness_scores[-1] if fitness_scores else -10.0] * (len(self.population) - len(fitness_scores))
                fitness_scores = list(fitness_scores) + pad
            else:
                fitness_scores = list(fitness_scores)[:len(self.population)]

        for ind, score in zip(self.population, fitness_scores):
            ind.fitness = score

        sorted_pop = sorted(self.population, key=lambda x: x.fitness, reverse=True)
        if self.best_individual is None or sorted_pop[0].fitness > self.best_individual.fitness:
            self.best_individual = deepcopy(sorted_pop[0])

        logger.info(
            f"第 {self.generation} 代: 最优={sorted_pop[0].fitness:.2f}%, "
            f"平均={sum(fitness_scores)/len(fitness_scores):.2f}%"
        )

        parents    = self._selection()
        offspring  = self._crossover(parents)
        offspring  = self._mutation(offspring)
        elites     = sorted_pop[:self.elite_size]
        self.population = offspring[:self.population_size - self.elite_size] + elites
        self.generation += 1
        return self.population

    def _selection(self) -> List[Individual]:
        """轮盘赌选择"""
        total = sum(max(ind.fitness, 0) for ind in self.population)
        if total <= 0:
            return random.choices(self.population, k=self.population_size)

        probs = [max(ind.fitness, 0) / total for ind in self.population]
        selected = []
        for _ in range(self.population_size):
            r, cumulative = random.random(), 0.0
            for i, p in enumerate(probs):
                cumulative += p
                if r <= cumulative:
                    selected.append(deepcopy(self.population[i]))
                    break
            else:
                selected.append(deepcopy(self.population[-1]))
        return selected

    def _crossover(self, parents: List[Individual]) -> List[Individual]:
        """单点交叉"""
        offspring = list(parents[:len(parents) // 4])  # 保留部分父母
        for _ in range(len(parents) // 2):
            if random.random() < self.crossover_rate:
                p1, p2 = random.choice(parents), random.choice(parents)
                c1, c2 = deepcopy(p1.params), deepcopy(p2.params)
                common = list(set(c1) & set(c2))
                if common:
                    key = random.choice(common)
                    c1[key], c2[key] = p2.params[key], p1.params[key]
                offspring.append(Individual(params=c1, fitness=0.0, generation=self.generation))
                offspring.append(Individual(params=c2, fitness=0.0, generation=self.generation))
            else:
                offspring.append(deepcopy(random.choice(parents)))
        return offspring

    def _mutation(self, offspring: List[Individual]) -> List[Individual]:
        """高斯变异"""
        for ind in offspring:
            if random.random() < self.mutation_rate:
                name = random.choice(list(self.PARAM_RANGES))
                if name in ind.params:
                    lo, hi = self.PARAM_RANGES[name]
                    delta = (hi - lo) * 0.10
                    new_val = ind.params[name] + random.gauss(0, delta)
                    ind.params[name] = max(lo, min(hi, new_val))
        return offspring

    def get_best_params(self) -> Dict:
        if self.best_individual:
            return self.best_individual.params
        if self.population:
            return max(self.population, key=lambda x: x.fitness).params
        return {}


class StrategyEvolutionOptimizer:
    """
    策略进化优化器（遗传算法 + LLM）

    亏损周期 → LLM 分析 → 参数调整
    盈利周期 → 近期收益 > 5%时略微激进，否则保守
    """

    def __init__(self):
        self.llm_optimizer  = LLMOptimizer()
        self.best_params: Dict = {}
        self.generation       = 0

    def optimize(
        self,
        cycle_results: List[Dict],
        current_params: Dict,
    ) -> Dict:
        logger.info(f"策略优化第 {self.generation + 1} 代")
        self.generation += 1

        if cycle_results:
            latest = cycle_results[-1]
            if latest.get("return_pct", 0) < 0:
                analysis = self.llm_optimizer.analyze_loss(
                    latest, latest.get("trade_history", [])
                )
                new_params = self.llm_optimizer.generate_strategy_fix(analysis)
                logger.info(f"LLM 参数调整: {new_params}")
                self.best_params = new_params
                return new_params

        params = current_params.copy()
        if len(cycle_results) >= 3:
            avg_ret = sum(r.get("return_pct", 0) for r in cycle_results[-3:]) / 3
            if avg_ret > 5:
                params["position_size"] = min(params.get("position_size", 0.2) * 1.1, 0.30)
            elif avg_ret < 0:
                params["position_size"] = max(params.get("position_size", 0.2) * 0.8, 0.10)

        return params


# ============================================================
# Part 4: 策略库 + 集成层
# ============================================================

@dataclass
class FrozenStrategy:
    """固化策略"""
    name: str
    params: Dict[str, float]
    performance: Dict[str, float]
    frozen_date: str
    win_rate: float = 0.0
    avg_return: float = 0.0


@dataclass
class EnsembleSignal:
    """集成信号"""
    date: str
    action: str          # BUY / SELL / HOLD
    confidence: float
    contributions: Dict[str, any] = field(default_factory=dict)
    reason: str = ""


class StrategyLibrary:
    """策略库（JSON 持久化）"""

    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = Path(storage_path) if storage_path else (
            Path.home() / ".invest_ai" / "strategies"
        )
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.strategies: Dict[str, FrozenStrategy] = {}

    def add_strategy(self, strategy: FrozenStrategy):
        self.strategies[strategy.name] = strategy
        logger.info(f"策略库添加: {strategy.name}")

    def get_strategy(self, name: str) -> Optional[FrozenStrategy]:
        return self.strategies.get(name)

    def get_all_strategies(self) -> List[FrozenStrategy]:
        return list(self.strategies.values())

    def save(self):
        path = self.storage_path / "strategy_library.json"
        data = {
            name: {
                "params": s.params, "performance": s.performance,
                "frozen_date": s.frozen_date, "win_rate": s.win_rate,
                "avg_return": s.avg_return,
            }
            for name, s in self.strategies.items()
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"策略库已保存: {path}")

    def load(self):
        path = self.storage_path / "strategy_library.json"
        if not path.exists():
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for name, s in data.items():
            self.strategies[name] = FrozenStrategy(
                name=name, params=s["params"], performance=s.get("performance", {}),
                frozen_date=s.get("frozen_date", ""), win_rate=s.get("win_rate", 0),
                avg_return=s.get("avg_return", 0),
            )
        logger.info(f"策略库已加载: {len(self.strategies)} 个策略")


class DynamicWeightAllocator:
    """
    动态权重分配器

    近期表现好的策略权重高（线性加权平均）
    单策略权重上限 50%
    """

    def __init__(self, max_weight: float = 0.5, lookback: int = 10):
        self.max_weight        = max_weight
        self.lookback          = lookback
        self.performance_history: Dict[str, List[float]] = {}
        self.current_weights: Dict[str, float] = {}

    def update_performance(self, name: str, return_pct: float):
        hist = self.performance_history.setdefault(name, [])
        hist.append(return_pct)
        self.performance_history[name] = hist[-self.lookback:]

    def calculate_weights(self, strategies: List[FrozenStrategy]) -> Dict[str, float]:
        if not strategies:
            return {}

        raw = {}
        for s in strategies:
            hist = self.performance_history.get(s.name, [])
            if hist:
                w = np.linspace(1, 2, len(hist))
                score = float(np.average(hist, weights=w))
            else:
                score = s.win_rate
            raw[s.name] = max(score, 0)

        total = sum(raw.values())
        norm  = {k: v / total for k, v in raw.items()} if total > 0 else {
            k: 1.0 / len(strategies) for k in raw
        }

        capped = {k: min(v, self.max_weight) for k, v in norm.items()}
        total  = sum(capped.values())
        final  = {k: v / total for k, v in capped.items()} if total > 0 else capped

        self.current_weights = final
        logger.info(f"动态权重: {final}")
        return final


class StrategyEnsemble:
    """
    多策略集成层（加权投票）

    strategy_signals: {策略名: "BUY"/"SELL"/"HOLD"}
    加权投票 → 最终信号 + 置信度
    """

    def __init__(self):
        self.library         = StrategyLibrary()
        self.weight_allocator = DynamicWeightAllocator()

    def add_strategy(self, name: str, params: Dict, performance: Dict):
        self.library.add_strategy(FrozenStrategy(
            name=name, params=params, performance=performance,
            frozen_date=datetime.now().strftime("%Y%m%d"),
            win_rate=performance.get("win_rate", 0),
            avg_return=performance.get("avg_return", 0),
        ))

    def generate_signal(self, strategy_signals: Dict[str, str]) -> EnsembleSignal:
        strategies = self.library.get_all_strategies()
        if not strategies:
            return EnsembleSignal(date=datetime.now().strftime("%Y%m%d"), action="HOLD", confidence=0, reason="策略库为空")

        weights = self.weight_allocator.calculate_weights(strategies)
        votes: Dict[str, float] = {"BUY": 0, "SELL": 0, "HOLD": 0}
        contributions = {}

        for name, sig in strategy_signals.items():
            w = weights.get(name, 0)
            votes[sig] = votes.get(sig, 0) + w
            contributions[name] = {"signal": sig, "weight": w}

        action = max(votes, key=votes.get)
        total  = sum(votes.values())
        conf   = votes[action] / total if total > 0 else 0

        return EnsembleSignal(
            date=datetime.now().strftime("%Y%m%d"),
            action=action,
            confidence=conf,
            contributions=contributions,
            reason=f"加权投票: BUY={votes['BUY']:.2f}, SELL={votes['SELL']:.2f}, HOLD={votes['HOLD']:.2f}",
        )

    def update_performance(self, name: str, return_pct: float):
        self.weight_allocator.update_performance(name, return_pct)

    def save(self):
        self.library.save()

    def load(self):
        self.library.load()

# ============================================================
# three_stage_optimizer.py
# ============================================================

"""
三阶段优化器

第一阶段：贝叶斯优化（快速定位有效区间）
第二阶段：遗传算法（区间内精细搜索）
第三阶段：参数稳健性检验（避免过拟合）
"""

import sys
import os
import logging
from pathlib import Path
from typing import Dict, List, Optional, Callable, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import random

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class OptimizedParams:
    """优化后的参数"""
    params: Dict[str, float]
    fitness: float
    stability_score: float  # 稳健性得分
    stage: str  # 优化阶段


class GaussianProcessModel:
    """
    高斯过程模型

    用于贝叶斯优化中的代理模型
    """

    def __init__(self):
        self.X_train = []
        self.y_train = []
        self.length_scale = 1.0
        self.noise = 0.1

    def fit(self, X: np.ndarray, y: np.ndarray):
        """训练高斯过程模型"""
        self.X_train = X
        self.y_train = y

    def predict(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        预测

        Returns:
            (mean, std)
        """
        if len(self.X_train) == 0:
            return np.zeros(len(X)), np.ones(len(X))

        # 简化的RBF核函数
        X = np.array(X)
        X_train = np.array(self.X_train)

        # 计算核矩阵
        K = self._rbf_kernel(X_train, X_train) + self.noise ** 2 * np.eye(len(X_train))

        # 计算预测均值
        K_star = self._rbf_kernel(X_train, X)
        K_inv = np.linalg.pinv(K)

        # 均值
        mean = K_star.T @ K_inv @ self.y_train

        # 方差
        k_xx = self._rbf_kernel(X, X)
        var = k_xx - K_star.T @ K_inv @ K_star
        var = np.diag(var)
        var = np.maximum(var, 0)

        return mean, np.sqrt(var)

    def _rbf_kernel(self, X1: np.ndarray, X2: np.ndarray) -> np.ndarray:
        """RBF核函数"""
        X1 = np.array(X1)
        X2 = np.array(X2)

        if X1.ndim == 1:
            X1 = X1.reshape(-1, 1)
        if X2.ndim == 1:
            X2 = X2.reshape(-1, 1)

        # 计算欧氏距离
        dist = np.sum((X1[:, np.newaxis, :] - X2[np.newaxis, :, :]) ** 2, axis=2)

        return np.exp(-0.5 * dist / (self.length_scale ** 2))


class BayesianOptimizer:
    """
    贝叶斯优化器

    用于第一阶段：快速定位有效参数区间
    """

    def __init__(
        self,
        param_bounds: Dict[str, Tuple[float, float]],
        n_iter: int = 50,
        acquisition: str = "ei",  # ei, ucb
    ):
        self.param_bounds = param_bounds
        self.n_iter = n_iter
        self.acquisition = acquisition
        self.gp = GaussianProcessModel()
        self.best_params = None
        self.best_fitness = float('-inf')
        self.history = []

    def _sample_random(self) -> Dict[str, float]:
        """随机采样参数"""
        params = {}
        for name, (low, high) in self.param_bounds.items():
            params[name] = random.uniform(low, high)
        return params

    def _to_vector(self, params: Dict[str, float]) -> np.ndarray:
        """参数转向量"""
        names = list(self.param_bounds.keys())
        return np.array([params[n] for n in names])

    def _from_vector(self, vec: np.ndarray) -> Dict[str, float]:
        """向量转参数"""
        names = list(self.param_bounds.keys())
        return {names[i]: vec[i] for i in range(len(names))}

    def _acquisition_ei(self, mean: np.ndarray, std: np.ndarray, best_y: float) -> np.ndarray:
        """Expected Improvement采集函数"""
        z = (mean - best_y) / (std + 1e-8)
        ei = (mean - best_y) * self._norm_cdf(z) + std * self._norm_pdf(z)
        return ei

    def _norm_cdf(self, x):
        """正态分布CDF (使用erf)"""
        # 使用scipy或手动实现
        try:
            from scipy import stats
            return stats.norm.cdf(x)
        except ImportError:
            # 手动实现近似
            return 0.5 * (1 + np.sign(x) * np.sqrt(1 - np.exp(-2 * x * x / np.pi)))

    def _norm_pdf(self, x):
        """正态分布PDF"""
        return np.exp(-0.5 * x ** 2) / np.sqrt(2 * np.pi)

    def optimize(
        self,
        fitness_func: Callable[[Dict[str, float]], float],
    ) -> Tuple[Dict[str, float], float]:
        """
        贝叶斯优化

        Args:
            fitness_func: 适应度函数

        Returns:
            (最优参数, 最优适应度)
        """
        logger.info(f"第一阶段：贝叶斯优化 ({self.n_iter}次评估)")

        # 初始化：随机采样
        for _ in range(10):
            params = self._sample_random()
            fitness = fitness_func(params)
            self.history.append((params.copy(), fitness))

            if fitness > self.best_fitness:
                self.best_fitness = fitness
                self.best_params = params.copy()

        # 迭代优化
        for i in range(self.n_iter):
            # 训练高斯过程
            X = np.array([self._to_vector(p) for p, _ in self.history])
            y = np.array([f for _, f in self.history])
            self.gp.fit(X, y)

            # 候选点采样
            candidates = [self._sample_random() for _ in range(100)]

            # 计算采集函数值
            best_acq = float('-inf')
            best_candidate = None

            for candidate in candidates:
                x = self._to_vector(candidate).reshape(1, -1)
                mean, std = self.gp.predict(x)

                if self.acquisition == "ei":
                    acq = self._acquisition_ei(mean[0], std[0], self.best_fitness)
                else:
                    acq = mean[0] - 2 * std[0]  # UCB

                if acq > best_acq:
                    best_acq = acq
                    best_candidate = candidate

            # 评估候选点
            fitness = fitness_func(best_candidate)
            self.history.append((best_candidate.copy(), fitness))

            if fitness > self.best_fitness:
                self.best_fitness = fitness
                self.best_params = best_candidate.copy()

            if (i + 1) % 10 == 0:
                logger.info(f"  迭代 {i+1}/{self.n_iter}: 最优={self.best_fitness:.4f}")

        # 返回最优参数及其置信区间
        param_ranges = {}
        for name in self.param_bounds.keys():
            values = [p[name] for p, _ in self.history]
            low = np.percentile(values, 20)
            high = np.percentile(values, 80)
            param_ranges[name] = (low, high)

        logger.info(f"贝叶斯优化完成: 最优={self.best_fitness:.4f}")
        logger.info(f"参数置信区间: {param_ranges}")

        return self.best_params, self.best_fitness, param_ranges


class GeneticOptimizer:
    """
    遗传算法优化器

    用于第二阶段：区间内精细搜索
    """

    def __init__(
        self,
        param_bounds: Dict[str, Tuple[float, float]],
        population_size: int = 50,
        n_generations: int = 50,
        mutation_rate: float = 0.1,
        crossover_rate: float = 0.8,
        elite_ratio: float = 0.1,
        regularization: float = 0.01,
    ):
        self.param_bounds = param_bounds
        self.population_size = population_size
        self.n_generations = n_generations
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.elite_ratio = elite_ratio
        self.regularization = regularization

        self.best_params = None
        self.best_fitness = float('-inf')

    def _initialize_population(self) -> List[Dict[str, float]]:
        """初始化种群"""
        population = []
        for _ in range(self.population_size):
            individual = {}
            for name, (low, high) in self.param_bounds.items():
                individual[name] = random.uniform(low, high)
            population.append(individual)
        return population

    def _evaluate(
        self,
        population: List[Dict[str, float]],
        fitness_func: Callable[[Dict[str, float]], float],
    ) -> List[Tuple[Dict[str, float], float]]:
        """评估种群"""
        results = []
        for individual in population:
            # 适应度 = 收益 - λ × 参数复杂度
            fitness = fitness_func(individual)

            # 正则化惩罚：参数越极端，惩罚越大
            penalty = 0
            for name, (low, high) in self.param_bounds.items():
                value = individual[name]
                # 归一化到[0,1]
                normalized = (value - low) / (high - low)
                # 远离中心点(0.5)越多，惩罚越大
                penalty += abs(normalized - 0.5) * self.regularization

            fitness -= penalty
            results.append((individual, fitness))

        return results

    def _select(
        self,
        results: List[Tuple[Dict[str, float], float]],
    ) -> List[Dict[str, float]]:
        """选择（锦标赛）"""
        selected = []
        for _ in range(self.population_size):
            # 随机选3个
            candidates = random.sample(results, min(3, len(results)))
            # 选最好的
            best = max(candidates, key=lambda x: x[1])
            selected.append(best[0].copy())
        return selected

    def _crossover(
        self,
        parent1: Dict[str, float],
        parent2: Dict[str, float],
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        """交叉"""
        if random.random() > self.crossover_rate:
            return parent1.copy(), parent2.copy()

        child1, child2 = {}, {}
        for name in self.param_bounds.keys():
            if random.random() < 0.5:
                child1[name] = parent1[name]
                child2[name] = parent2[name]
            else:
                child1[name] = parent2[name]
                child2[name] = parent1[name]

        return child1, child2

    def _mutate(self, individual: Dict[str, float]) -> Dict[str, float]:
        """变异"""
        mutated = individual.copy()
        for name, (low, high) in self.param_bounds.items():
            if random.random() < self.mutation_rate:
                # 高斯变异
                std = (high - low) * 0.1
                mutated[name] = mutated[name] + random.gauss(0, std)
                # 边界处理
                mutated[name] = max(low, min(high, mutated[name]))
        return mutated

    def optimize(
        self,
        fitness_func: Callable[[Dict[str, float]], float],
        param_ranges: Dict[str, Tuple[float, float]] = None,
    ) -> Tuple[Dict[str, float], float]:
        """
        遗传算法优化

        Args:
            fitness_func: 适应度函数
            param_ranges: 参数范围（可选，用于约束搜索空间）

        Returns:
            (最优参数, 最优适应度)
        """
        # 使用给定的范围或默认范围
        bounds = param_ranges if param_ranges else self.param_bounds
        self.param_bounds = bounds

        logger.info(f"第二阶段：遗传算法 ({self.n_generations}代)")

        # 初始化
        population = self._initialize_population()

        for gen in range(self.n_generations):
            # 评估
            results = self._evaluate(population, fitness_func)

            # 记录最优
            for individual, fitness in results:
                if fitness > self.best_fitness:
                    self.best_fitness = fitness
                    self.best_params = individual.copy()

            # 排序
            results.sort(key=lambda x: x[1], reverse=True)

            # 精英保留
            elite_count = int(self.population_size * self.elite_ratio)
            new_population = [r[0].copy() for r in results[:elite_count]]

            # 选择
            selected = self._select(results)

            # 交叉和变异
            while len(new_population) < self.population_size:
                parent1, parent2 = random.sample(selected, 2)
                child1, child2 = self._crossover(parent1, parent2)
                child1 = self._mutate(child1)
                child2 = self._mutate(child2)
                new_population.append(child1)
                if len(new_population) < self.population_size:
                    new_population.append(child2)

            population = new_population

            if (gen + 1) % 10 == 0:
                logger.info(f"  代数 {gen+1}/{self.n_generations}: 最优={self.best_fitness:.4f}")

        logger.info(f"遗传算法完成: 最优={self.best_fitness:.4f}")
        return self.best_params, self.best_fitness


class RobustnessValidator:
    """
    参数稳健性检验器

    用于第三阶段：验证参数稳定性
    """

    def __init__(
        self,
        perturbation_range: float = 0.1,  # ±10%
        min_stability_score: float = 0.7,
    ):
        self.perturbation_range = perturbation_range
        self.min_stability_score = min_stability_score

    def validate(
        self,
        params: Dict[str, float],
        fitness_func: Callable[[Dict[str, float]], float],
        param_bounds: Dict[str, Tuple[float, float]],
    ) -> Tuple[float, bool]:
        """
        验证参数稳健性

        Args:
            params: 最优参数
            fitness_func: 适应度函数
            param_bounds: 参数范围

        Returns:
            (稳健性得分, 是否通过)
        """
        logger.info("第三阶段：参数稳健性检验")

        # 评估最优参数
        best_fitness = fitness_func(params)

        # 周围采样
        n_samples = 20
        perturbed_fitness = []

        for _ in range(n_samples):
            perturbed = self._perturb_params(params, param_bounds)
            fitness = fitness_func(perturbed)
            perturbed_fitness.append(fitness)

        # 计算稳健性得分
        # 稳健性 = 周围参数的平均收益 / 最优参数收益
        avg_perturbed = np.mean(perturbed_fitness)
        stability_score = avg_perturbed / best_fitness if best_fitness > 0 else 0

        # 额外检查：参数微调后收益是否剧变
        fitness_variance = np.std(perturbed_fitness)
        variance_penalty = min(fitness_variance / abs(best_fitness), 1.0) if best_fitness != 0 else 1.0

        # 最终稳健性得分
        final_score = stability_score * (1 - variance_penalty * 0.5)

        passed = final_score >= self.min_stability_score

        logger.info(f"  最优收益: {best_fitness:.4f}")
        logger.info(f"  周围平均收益: {avg_perturbed:.4f}")
        logger.info(f"  收益方差: {fitness_variance:.4f}")
        logger.info(f"  稳健性得分: {final_score:.4f} ({'通过' if passed else '未通过'})")

        return final_score, passed

    def _perturb_params(
        self,
        params: Dict[str, float],
        param_bounds: Dict[str, Tuple[float, float]],
    ) -> Dict[str, float]:
        """微调参数"""
        perturbed = params.copy()
        for name, (low, high) in param_bounds.items():
            # 随机微调±10%
            range_size = high - low
            delta = random.uniform(-1, 1) * self.perturbation_range * range_size
            perturbed[name] = params[name] + delta
            perturbed[name] = max(low, min(high, perturbed[name]))
        return perturbed


class ThreeStageOptimizer:
    """
    三阶段优化器

    1. 贝叶斯优化 → 快速定位
    2. 遗传算法 → 精细搜索
    3. 稳健性检验 → 避免过拟合
    """

    def __init__(
        self,
        param_bounds: Dict[str, Tuple[float, float]],
        # 贝叶斯优化参数
        bayesian_n_iter: int = 50,
        # 遗传算法参数
        ga_population: int = 50,
        ga_generations: int = 50,
        # 稳健性检验参数
        perturbation_range: float = 0.1,
        min_stability: float = 0.7,
    ):
        self.param_bounds = param_bounds

        self.bayesian = BayesianOptimizer(
            param_bounds=param_bounds,
            n_iter=bayesian_n_iter,
        )

        self.genetic = GeneticOptimizer(
            param_bounds=param_bounds,
            population_size=ga_population,
            n_generations=ga_generations,
        )

        self.robustness = RobustnessValidator(
            perturbation_range=perturbation_range,
            min_stability_score=min_stability,
        )

    def optimize(
        self,
        fitness_func: Callable[[Dict[str, float]], float],
    ) -> OptimizedParams:
        """
        执行三阶段优化

        Args:
            fitness_func: 适应度函数 (参数) -> 收益

        Returns:
            优化后的参数
        """
        logger.info("=" * 60)
        logger.info("开始三阶段优化")
        logger.info("=" * 60)

        # 第一阶段：贝叶斯优化
        logger.info("\n" + "=" * 40)
        best_params_1, fitness_1, param_ranges = self.bayesian.optimize(fitness_func)

        # 第二阶段：遗传算法
        logger.info("\n" + "=" * 40)
        best_params_2, fitness_2 = self.genetic.optimize(
            fitness_func,
            param_ranges=param_ranges,
        )

        # 选择第二阶段更好的结果
        if fitness_2 > fitness_1:
            best_params = best_params_2
            best_fitness = fitness_2
        else:
            best_params = best_params_1
            best_fitness = fitness_1

        # 第三阶段：稳健性检验
        logger.info("\n" + "=" * 40)
        stability_score, passed = self.robustness.validate(
            best_params,
            fitness_func,
            self.param_bounds,
        )

        # 如果未通过，返回原始参数（保守）
        if not passed:
            logger.warning("参数稳健性检验未通过，使用保守参数")
            # 可以选择返回更保守的参数，或者降低期望

        logger.info("\n" + "=" * 60)
        logger.info("三阶段优化完成")
        logger.info(f"最优参数: {best_params}")
        logger.info(f"适应度: {best_fitness:.4f}")
        logger.info(f"稳健性得分: {stability_score:.4f}")
        logger.info("=" * 60)

        return OptimizedParams(
            params=best_params,
            fitness=best_fitness,
            stability_score=stability_score,
            stage="completed" if passed else "conservative",
        )



# ============================================================
# llm_analyzer.py
# ============================================================

"""
LLM分析模块 - 细化版

结构化输入 + 可执行输出
"""

import sys
import os
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
import json

import pandas as pd
import numpy as np

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
    """

    def __init__(self, model: str = "gpt-4"):
        self.model = model
        self.prompt_builder = LLMPromptBuilder()

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

        # 调用LLM（这里需要实际API调用）
        response = self._call_llm(prompt)

        # 解析结果
        result = self._parse_response(response)

        return result

    def _call_llm(self, prompt: str) -> str:
        """
        调用LLM

        这里应该调用实际的LLM API
        暂时返回模拟响应
        """
        logger.info("提示词长度: {} 字符".format(len(prompt)))

        # TODO: 实际调用LLM API
        # 示例使用OpenAI:
        # response = openai.ChatCompletion.create(
        #     model=self.model,
        #     messages=[{"role": "user", "content": prompt}]
        # )
        # return response.choices[0].message.content

        return self._mock_response()

    def _mock_response(self) -> str:
        """模拟LLM响应"""
        return json.dumps({
            "factor_adjustments": {
                "momentum_weight": 0.12,
                "rsi_weight": 0.15,
                "reversal_weight": 0.08
            },
            "stop_loss_suggestion": 0.06,
            "take_profit_suggestion": 0.12,
            "position_size_suggestion": 0.15,
            "market_regime": "bear",
            "confidence": 0.7,
            "suggestions": [
                "降低动量因子权重，增加RSI权重",
                "将止损从5%调整到6%以减少频繁触发",
                "在熊市环境下建议降低仓位到15%"
            ]
        }, ensure_ascii=False)

    def _parse_response(self, response: str) -> LLMAnalysisResult:
        """解析LLM响应"""
        try:
            # 尝试提取JSON
            data = json.loads(response)
        except Exception:
            # 尝试从markdown中提取
            import re
            match = re.search(r'\{[\s\S]*\}', response)
            if match:
                try:
                    data = json.loads(match.group())
                except Exception:
                    logger.error("无法解析LLM响应")
                    return self._default_result()
            else:
                logger.error("响应中没有找到JSON")
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

    def _default_result(self) -> LLMAnalysisResult:
        """默认结果"""
        return LLMAnalysisResult(
            factor_adjustments={},
            stop_loss_suggestion=0.05,
            take_profit_suggestion=0.15,
            position_size_suggestion=0.2,
            market_regime="neutral",
            confidence=0.5,
            suggestions=[],
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


