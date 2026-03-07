import logging
from typing import Dict, List, Optional

import pandas as pd

from invest.shared import compute_bb_position, compute_macd_signal, compute_rsi

logger = logging.getLogger(__name__)


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


__all__ = ["StockSelector", "AdaptiveSelector"]
