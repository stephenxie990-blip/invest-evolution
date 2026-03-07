import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


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

__all__ = ["FactorResult", "DynamicFactorWeight", "AlphaFactorModel"]
