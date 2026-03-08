import logging
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..engine.contracts import EmergencyAction, EmergencyEvent, EmergencyType, Position, RiskMetrics

logger = logging.getLogger(__name__)


def clamp_stop_loss_pct(value: float, lower: float = 0.02, upper: float = 0.15) -> float:
    return max(lower, min(upper, float(value)))


def clamp_take_profit_pct(value: float, lower: float = 0.05, upper: float = 0.50) -> float:
    return max(lower, min(upper, float(value)))


def clamp_position_size(value: float, lower: float = 0.05, upper: float = 0.30) -> float:
    return max(lower, min(upper, float(value)))


def sanitize_risk_params(params: Dict[str, float]) -> Dict[str, float]:
    clean: Dict[str, float] = {}
    for key, value in (params or {}).items():
        if value is None:
            continue
        if key == "stop_loss_pct":
            clean[key] = clamp_stop_loss_pct(value)
        elif key == "take_profit_pct":
            clean[key] = clamp_take_profit_pct(value)
        elif key == "position_size":
            clean[key] = clamp_position_size(value)
        else:
            clean[key] = float(value)
    return clean


class EmergencyDetector:
    """
    异常检测器（纯规则，不调 LLM）

    每个交易日调用 check()，返回检测到的异常事件列表
    """

    def __init__(
        self,
        single_stock_crash_pct: float = None,
        rapid_loss_pct: float = None,
        rapid_loss_days: int = 3,
    ):
        from config import config
        params = config.emergency_params
        
        self.single_stock_crash_pct = single_stock_crash_pct if single_stock_crash_pct is not None else params.get("single_stock_crash_pct", -7.0)
        self.rapid_loss_pct = rapid_loss_pct if rapid_loss_pct is not None else params.get("rapid_loss_pct", -5.0)
        self.rapid_loss_days = rapid_loss_days
        self.events: List[EmergencyEvent] = []
        self.portfolio_values: List[float] = []

    def check(self, trader, date: str) -> List[EmergencyEvent]:
        events = []

        if not trader.positions:
            self.portfolio_values.append(trader.cash)
            return events

        event = self._check_single_crash(trader, date)
        if event:
            events.append(event)

        current_value = trader.get_total_value()
        self.portfolio_values.append(current_value)

        event = self._check_rapid_loss(date)
        if event:
            events.append(event)

        event = self._check_all_red(trader, date)
        if event:
            events.append(event)

        self.events.extend(events)

        for e in events:
            logger.warning(
                f"⚠️ [{e.event_type.value}] 严重度{e.severity:.0%}: {e.description} → {e.action.value}"
            )

        return events

    def _check_single_crash(self, trader, date: str) -> Optional[EmergencyEvent]:
        worst_code, worst_pct = None, 0.0
        for pos in trader.positions:
            metrics = trader.get_day_metrics(pos.ts_code, date)
            pct = metrics.get("pct_chg", 0)
            if pct < self.single_stock_crash_pct and pct < worst_pct:
                worst_pct, worst_code = pct, pos.ts_code

        if worst_code:
            severity = min(1.0, abs(worst_pct) / 15.0)
            return EmergencyEvent(
                date=date,
                event_type=EmergencyType.SINGLE_STOCK_CRASH,
                severity=severity,
                description=f"{worst_code}单日跌{worst_pct:+.1f}%",
                action=EmergencyAction.TIGHTEN_STOP if severity < 0.6 else EmergencyAction.SELL_WORST,
                affected_codes=[worst_code],
            )
        return None

    def _check_rapid_loss(self, date: str) -> Optional[EmergencyEvent]:
        if len(self.portfolio_values) < self.rapid_loss_days + 1:
            return None
        current = self.portfolio_values[-1]
        past = self.portfolio_values[-(self.rapid_loss_days + 1)]
        if past <= 0:
            return None
        change_pct = (current / past - 1) * 100
        if change_pct < self.rapid_loss_pct:
            severity = min(1.0, abs(change_pct) / 10.0)
            return EmergencyEvent(
                date=date,
                event_type=EmergencyType.RAPID_PORTFOLIO_LOSS,
                severity=severity,
                description=f"组合{self.rapid_loss_days}日亏损{change_pct:+.1f}%",
                action=EmergencyAction.REDUCE_ALL if severity > 0.7 else EmergencyAction.TIGHTEN_STOP,
            )
        return None

    def _check_all_red(self, trader, date: str) -> Optional[EmergencyEvent]:
        if len(trader.positions) < 2:
            return None
        codes = [
            pos.ts_code for pos in trader.positions
            if (p := trader.get_price(pos.ts_code, date)) and p < pos.entry_price
        ]
        if len(codes) == len(trader.positions):
            return EmergencyEvent(
                date=date,
                event_type=EmergencyType.ALL_POSITIONS_RED,
                severity=0.5,
                description=f"全部{len(codes)}只持仓亏损",
                action=EmergencyAction.LOG_ONLY,
                affected_codes=codes,
            )
        return None

    def reset(self):
        self.portfolio_values.clear()

    def get_summary(self) -> dict:
        type_counts: Dict[str, int] = {}
        for e in self.events:
            t = e.event_type.value
            type_counts[t] = type_counts.get(t, 0) + 1
        return {"total_events": len(self.events), "by_type": type_counts}


# ============================================================
# Part 3: 风控体系
# ============================================================

class DynamicStopLoss:
    """
    基于 ATR 的动态止损止盈

    止损 = entry - 2×ATR
    止盈 = entry + 3×ATR
    移动止盈 = 最高价 - 1.5×ATR（不低于成本价）
    """

    def __init__(self, atr_period: int = 14):
        self.atr_period = atr_period
        self.highest_price: Dict[str, float] = {}

    def calculate_atr(self, df: pd.DataFrame) -> float:
        if len(df) < self.atr_period + 1:
            return 0
        high = df["high"].values
        low  = df["low"].values
        close = df["close"].values
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
        )
        return float(np.mean(tr[-self.atr_period:]))

    def get_stop_levels(
        self,
        code: str,
        entry_price: float,
        current_price: float,
        df: pd.DataFrame,
    ) -> Dict[str, float]:
        atr = self.calculate_atr(df)
        self.highest_price.setdefault(code, entry_price)
        if current_price > self.highest_price[code]:
            self.highest_price[code] = current_price

        stop_loss     = entry_price - 2 * atr
        take_profit   = entry_price + 3 * atr
        trailing_stop = max(self.highest_price[code] - 1.5 * atr, entry_price)
        effective_stop = max(stop_loss, trailing_stop)

        return {
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trailing_stop": trailing_stop,
            "effective_stop": effective_stop,
            "atr": atr,
        }

    def reset(self, code: str):
        self.highest_price.pop(code, None)


class PortfolioRiskManager:
    """
    组合风险管理器

    Level 1: 单股仓位 ≤ 20%
    Level 2: 行业集中度 ≤ 30%
    Level 3: 回撤 > 8% 减仓 / > 12% 清仓
    Level 4: 大盘跌破 MA60 限制仓位
    """

    def __init__(
        self,
        max_drawdown_to_reduce: float = 0.08,
        max_drawdown_to_close: float = 0.12,
        market_ma_period: int = 60,
        max_industry_pct: float = 0.30,
        max_correlation: float = 0.60,
    ):
        self.max_drawdown_to_reduce = max_drawdown_to_reduce
        self.max_drawdown_to_close  = max_drawdown_to_close
        self.market_ma_period       = market_ma_period
        self.max_industry_pct       = max_industry_pct
        self.max_correlation        = max_correlation

    def get_industry(self, code: str) -> str:
        from config import industry_registry
        return industry_registry.get_industry(code)

    def check_market_state(self, hs300_data: Optional[pd.DataFrame]) -> str:
        if hs300_data is None or len(hs300_data) < self.market_ma_period:
            return "normal"
        close = hs300_data["close"].values
        ma = np.mean(close[-self.market_ma_period:])
        current = close[-1]
        if current > ma * 1.05:
            return "bull"
        if current < ma * 0.95:
            return "bear"
        return "normal"

    def check_portfolio_risk(
        self,
        positions: Dict,
        initial_capital: float,
        current_capital: float,
        hs300_data: Optional[pd.DataFrame] = None,
    ) -> Dict:
        market_state = self.check_market_state(hs300_data)
        drawdown = (initial_capital - current_capital) / initial_capital

        industry_exposure: Dict[str, float] = {}
        for code, pos in positions.items():
            value = pos.get("shares", 0) * pos.get("current_price", 0)
            industry = self.get_industry(code)
            industry_exposure[industry] = industry_exposure.get(industry, 0) + value
        for ind in industry_exposure:
            industry_exposure[ind] /= max(current_capital, 1)

        action, reason = "NONE", ""

        if drawdown >= self.max_drawdown_to_close:
            action = "CLOSE_ALL"
            reason = f"回撤{drawdown*100:.1f}%超{self.max_drawdown_to_close*100:.1f}%"
        elif drawdown >= self.max_drawdown_to_reduce:
            action = "REDUCE"
            reason = f"回撤{drawdown*100:.1f}%达{self.max_drawdown_to_reduce*100:.1f}%，减仓50%"

        if market_state != "normal":
            reason += f" | 市场:{market_state}"

        for ind, pct in industry_exposure.items():
            if pct > self.max_industry_pct:
                if action == "NONE":
                    action = "REDUCE"
                if reason:
                    reason += f" | 行业'{ind}'{pct*100:.1f}%超{self.max_industry_pct*100:.1f}%"
                else:
                    reason = f"行业'{ind}'{pct*100:.1f}%超{self.max_industry_pct*100:.1f}%"

        return {
            "action": action,
            "reason": reason,
            "drawdown": drawdown,
            "market_state": market_state,
            "industry_exposure": industry_exposure,
            "can_open_new": action == "NONE" and market_state != "bear",
        }


class RiskController:
    """整合所有风控逻辑的统一接口"""

    def __init__(self):
        self.dynamic_stop    = DynamicStopLoss(atr_period=14)
        self.portfolio_risk  = PortfolioRiskManager()

    def should_stop_loss(self, code, entry_price, current_price, df) -> bool:
        levels = self.dynamic_stop.get_stop_levels(code, entry_price, current_price, df)
        return current_price <= levels["effective_stop"]

    def should_take_profit(self, code, entry_price, current_price, df) -> bool:
        levels = self.dynamic_stop.get_stop_levels(code, entry_price, current_price, df)
        return current_price >= levels["take_profit"]

    def reset_position(self, code: str):
        self.dynamic_stop.reset(code)

    def check_portfolio(
        self,
        positions: Dict,
        initial_capital: float,
        current_capital: float,
        hs300_data: Optional[pd.DataFrame] = None,
    ) -> Dict:
        return self.portfolio_risk.check_portfolio_risk(
            positions, initial_capital, current_capital, hs300_data
        )


# ============================================================
# Part 4: 候选池与交易调度
# ============================================================

__all__ = [
    "clamp_position_size",
    "clamp_stop_loss_pct",
    "clamp_take_profit_pct",
    "sanitize_risk_params",
    "EmergencyDetector",
    "DynamicStopLoss",
    "PortfolioRiskManager",
    "RiskController",
]
