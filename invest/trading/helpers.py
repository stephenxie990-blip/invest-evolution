import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from invest.shared import PositionPlan, TradingPlan
from .contracts import CandidatePool, CandidateStock, Position

logger = logging.getLogger(__name__)


class DailyRanker:
    """每日实时评分器（基于当日可用数据，无未来信息）"""

    def rank(
        self,
        candidate_pool: CandidatePool,
        stock_data: Dict[str, pd.DataFrame],
        current_date: str,
    ) -> List[CandidateStock]:
        scored = []
        for candidate in candidate_pool.get_all():
            code = candidate.code
            if code not in stock_data:
                continue
            df = stock_data[code]
            df_before = df[df["trade_date"] < current_date]
            if len(df_before) < 20:
                continue
            candidate.score = self._calculate_score(df_before, candidate.strategy_type)
            scored.append(candidate)
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored

    def _calculate_score(self, df: pd.DataFrame, strategy_type: str) -> float:
        close  = df["close"].values
        volume = df["volume"].values
        score  = 0.0

        if strategy_type == "momentum":
            if len(close) >= 20:
                score += min((close[-1] - close[-20]) / close[-20] * 100, 20)
            rsi = self._calc_rsi(close)
            if 40 < rsi < 80:
                score += 10
            if self._macd_bullish(close):
                score += 15
            if len(volume) >= 5 and np.mean(volume[-5:]) / np.mean(volume[-20:]) > 1.2:
                score += 10
        else:  # reversal
            rsi = self._calc_rsi(close)
            if rsi < 35:
                score += 20
            elif rsi < 40:
                score += 10
            if len(close) >= 20:
                dd = (max(close[-20:]) - close[-1]) / max(close[-20:]) * 100
                if dd > 15:
                    score += min(dd, 20)
            if self._macd_bullish(close):
                score += 10
            if len(volume) >= 10 and np.mean(volume[-5:]) > np.mean(volume[-10:-5]) * 1.1:
                score += 10

        return score

    def _calc_rsi(self, close: np.ndarray, period: int = 14) -> float:
        from invest.core import compute_rsi
        return compute_rsi(pd.Series(close), period)

    def _macd_bullish(self, close: np.ndarray) -> bool:
        if len(close) < 34:
            return False
        ema12 = self._ema(close, 12)
        ema26 = self._ema(close, 26)
        dif = ema12 - ema26
        dea = self._ema(dif, 9)
        return bool(dif[-1] > dea[-1])

    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        ema = np.zeros_like(data)
        ema[0] = data[0]
        k = 2 / (period + 1)
        for i in range(1, len(data)):
            ema[i] = data[i] * k + ema[i - 1] * (1 - k)
        return ema


class TradingScheduler:
    """交易调度器（从候选池选择买入/卖出股票）"""

    def __init__(self, max_positions: int = 2, position_size_pct: float = 0.2):
        self.max_positions    = max_positions
        self.position_size_pct = position_size_pct

    def should_buy(
        self,
        ranked_candidates: List[CandidateStock],
        current_positions: List[str],
    ) -> Optional[CandidateStock]:
        if len(current_positions) >= self.max_positions:
            return None
        for candidate in ranked_candidates:
            if candidate.code not in current_positions:
                return candidate
        return None

    def select_sell(
        self,
        current_holdings: Dict[str, Dict],
        stock_data: Dict[str, pd.DataFrame],
        current_date: str,
    ) -> List[str]:
        to_sell = []
        for code, holding in current_holdings.items():
            if code not in stock_data:
                continue
            df = stock_data[code]
            df_b = df[df["trade_date"] < current_date]
            if len(df_b) == 0:
                continue
            current_price = float(df_b.iloc[-1]["close"])
            buy_price = holding.get("buy_price", 0)
            if buy_price == 0:
                continue
            pnl_pct = (current_price - buy_price) / buy_price
            if pnl_pct <= -0.05:
                to_sell.append(code); continue
            if pnl_pct >= 0.15:
                to_sell.append(code); continue
            if len(df_b) >= 20:
                ma5  = df_b["close"].rolling(5).mean().iloc[-1]
                ma20 = df_b["close"].rolling(20).mean().iloc[-1]
                if ma5 < ma20:
                    to_sell.append(code)
        return to_sell


# ============================================================
# Part 5: 30 天模拟交易引擎
# ============================================================

__all__ = ["DailyRanker", "TradingScheduler"]
