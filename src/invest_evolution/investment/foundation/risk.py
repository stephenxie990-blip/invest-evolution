from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from invest_evolution.config import industry_registry
from .contracts import EmergencyAction, EmergencyEvent, EmergencyType

logger = logging.getLogger(__name__)


def _policy_source(policy: Optional[Dict[str, Any]] = None) -> str:
    return "explicit" if bool(policy) else "safety_fallback"


SAFETY_FALLBACK_RISK_POLICY: Dict[str, Any] = {
    "clamps": {
        "stop_loss_pct": {"min": 0.02, "max": 0.15},
        "take_profit_pct": {"min": 0.05, "max": 0.50},
        "position_size": {"min": 0.05, "max": 0.30},
    },
    "dynamic_stop": {
        "atr_period": 14,
        "stop_loss_atr_multiplier": 2.0,
        "take_profit_atr_multiplier": 3.0,
        "trailing_atr_multiplier": 1.5,
    },
    "portfolio": {
        "max_drawdown_to_reduce": 0.08,
        "max_drawdown_to_close": 0.12,
        "market_ma_period": 60,
        "bull_threshold": 1.05,
        "bear_threshold": 0.95,
        "max_industry_pct": 0.30,
        "max_correlation": 0.60,
    },
    "emergency": {
        "single_stock_crash_pct": -7.0,
        "rapid_loss_pct": -5.0,
        "rapid_loss_days": 3,
        "crash_severity_divisor": 15.0,
        "tighten_stop_severity_threshold": 0.6,
        "rapid_loss_severity_divisor": 10.0,
        "reduce_all_severity_threshold": 0.7,
        "all_positions_red_severity": 0.5,
    },
}


def _merge_policy(policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged: Dict[str, Any] = {
        "clamps": dict(SAFETY_FALLBACK_RISK_POLICY["clamps"]),
        "dynamic_stop": dict(SAFETY_FALLBACK_RISK_POLICY["dynamic_stop"]),
        "portfolio": dict(SAFETY_FALLBACK_RISK_POLICY["portfolio"]),
        "emergency": dict(SAFETY_FALLBACK_RISK_POLICY["emergency"]),
    }
    for section, value in (policy or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(section), dict):
            nested = dict(merged[section])
            for key, nested_value in value.items():
                if isinstance(nested_value, dict) and isinstance(nested.get(key), dict):
                    nested_inner = dict(nested[key])
                    nested_inner.update(nested_value)
                    nested[key] = nested_inner
                else:
                    nested[key] = nested_value
            merged[section] = nested
        else:
            merged[section] = value
    return merged


def _clamp_range(policy: Dict[str, Any], key: str) -> Tuple[float, float]:
    section = dict((policy.get("clamps") or {}).get(key, {}) or {})
    lower = float(section.get("min", SAFETY_FALLBACK_RISK_POLICY["clamps"][key]["min"]))
    upper = float(section.get("max", SAFETY_FALLBACK_RISK_POLICY["clamps"][key]["max"]))
    return lower, upper


def _section_fallback(section: str, key: str) -> Any:
    return SAFETY_FALLBACK_RISK_POLICY[section][key]


def clamp_stop_loss_pct(value: float, lower: float | None = None, upper: float | None = None) -> float:
    lower = float(_section_fallback("clamps", "stop_loss_pct")["min"] if lower is None else lower)
    upper = float(_section_fallback("clamps", "stop_loss_pct")["max"] if upper is None else upper)
    return max(lower, min(upper, float(value)))


def clamp_take_profit_pct(value: float, lower: float | None = None, upper: float | None = None) -> float:
    lower = float(_section_fallback("clamps", "take_profit_pct")["min"] if lower is None else lower)
    upper = float(_section_fallback("clamps", "take_profit_pct")["max"] if upper is None else upper)
    return max(lower, min(upper, float(value)))


def clamp_position_size(value: float, lower: float | None = None, upper: float | None = None) -> float:
    lower = float(_section_fallback("clamps", "position_size")["min"] if lower is None else lower)
    upper = float(_section_fallback("clamps", "position_size")["max"] if upper is None else upper)
    return max(lower, min(upper, float(value)))


def sanitize_risk_params(params: Dict[str, float], policy: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    active_policy = _merge_policy(policy)
    clean: Dict[str, float] = {}
    stop_lower, stop_upper = _clamp_range(active_policy, "stop_loss_pct")
    profit_lower, profit_upper = _clamp_range(active_policy, "take_profit_pct")
    pos_lower, pos_upper = _clamp_range(active_policy, "position_size")
    for key, value in (params or {}).items():
        if value is None:
            continue
        if key == "stop_loss_pct":
            clean[key] = clamp_stop_loss_pct(value, lower=stop_lower, upper=stop_upper)
        elif key == "take_profit_pct":
            clean[key] = clamp_take_profit_pct(value, lower=profit_lower, upper=profit_upper)
        elif key == "position_size":
            clean[key] = clamp_position_size(value, lower=pos_lower, upper=pos_upper)
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
        single_stock_crash_pct: float | None = None,
        rapid_loss_pct: float | None = None,
        rapid_loss_days: Optional[int] = None,
        policy: Optional[Dict[str, Any]] = None,
    ):
        self.policy_source = _policy_source(policy)
        self.policy = _merge_policy(policy)
        emergency = dict(self.policy.get("emergency", {}))
        self.single_stock_crash_pct = float(
            single_stock_crash_pct
            if single_stock_crash_pct is not None
            else emergency.get("single_stock_crash_pct", SAFETY_FALLBACK_RISK_POLICY["emergency"]["single_stock_crash_pct"])
        )
        self.rapid_loss_pct = float(
            rapid_loss_pct
            if rapid_loss_pct is not None
            else emergency.get("rapid_loss_pct", SAFETY_FALLBACK_RISK_POLICY["emergency"]["rapid_loss_pct"])
        )
        self.rapid_loss_days = int(
            rapid_loss_days if rapid_loss_days is not None else emergency.get("rapid_loss_days", SAFETY_FALLBACK_RISK_POLICY["emergency"]["rapid_loss_days"])
        )
        self.crash_severity_divisor = float(emergency.get("crash_severity_divisor", _section_fallback("emergency", "crash_severity_divisor")) or _section_fallback("emergency", "crash_severity_divisor"))
        self.tighten_stop_severity_threshold = float(emergency.get("tighten_stop_severity_threshold", _section_fallback("emergency", "tighten_stop_severity_threshold")) or _section_fallback("emergency", "tighten_stop_severity_threshold"))
        self.rapid_loss_severity_divisor = float(emergency.get("rapid_loss_severity_divisor", _section_fallback("emergency", "rapid_loss_severity_divisor")) or _section_fallback("emergency", "rapid_loss_severity_divisor"))
        self.reduce_all_severity_threshold = float(emergency.get("reduce_all_severity_threshold", _section_fallback("emergency", "reduce_all_severity_threshold")) or _section_fallback("emergency", "reduce_all_severity_threshold"))
        self.all_positions_red_severity = float(emergency.get("all_positions_red_severity", _section_fallback("emergency", "all_positions_red_severity")) or _section_fallback("emergency", "all_positions_red_severity"))
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
            severity = min(1.0, abs(worst_pct) / self.crash_severity_divisor)
            return EmergencyEvent(
                date=date,
                event_type=EmergencyType.SINGLE_STOCK_CRASH,
                severity=severity,
                description=f"{worst_code}单日跌{worst_pct:+.1f}%",
                action=EmergencyAction.TIGHTEN_STOP if severity < self.tighten_stop_severity_threshold else EmergencyAction.SELL_WORST,
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
            severity = min(1.0, abs(change_pct) / self.rapid_loss_severity_divisor)
            return EmergencyEvent(
                date=date,
                event_type=EmergencyType.RAPID_PORTFOLIO_LOSS,
                severity=severity,
                description=f"组合{self.rapid_loss_days}日亏损{change_pct:+.1f}%",
                action=EmergencyAction.REDUCE_ALL if severity > self.reduce_all_severity_threshold else EmergencyAction.TIGHTEN_STOP,
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
                severity=self.all_positions_red_severity,
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


class DynamicStopLoss:
    """
    基于 ATR 的动态止损止盈

    止损 = entry - stop_loss_atr_multiplier×ATR
    止盈 = entry + take_profit_atr_multiplier×ATR
    移动止盈 = 最高价 - trailing_atr_multiplier×ATR（不低于成本价）
    """

    def __init__(self, atr_period: int = 14, policy: Optional[Dict[str, Any]] = None):
        self.policy_source = _policy_source(policy)
        self.policy = _merge_policy(policy)
        dynamic_stop = dict(self.policy.get("dynamic_stop", {}))
        self.atr_period = int(dynamic_stop.get("atr_period", atr_period) or atr_period)
        self.stop_loss_atr_multiplier = float(dynamic_stop.get("stop_loss_atr_multiplier", _section_fallback("dynamic_stop", "stop_loss_atr_multiplier")) or _section_fallback("dynamic_stop", "stop_loss_atr_multiplier"))
        self.take_profit_atr_multiplier = float(dynamic_stop.get("take_profit_atr_multiplier", _section_fallback("dynamic_stop", "take_profit_atr_multiplier")) or _section_fallback("dynamic_stop", "take_profit_atr_multiplier"))
        self.trailing_atr_multiplier = float(dynamic_stop.get("trailing_atr_multiplier", _section_fallback("dynamic_stop", "trailing_atr_multiplier")) or _section_fallback("dynamic_stop", "trailing_atr_multiplier"))
        self.highest_price: Dict[str, float] = {}

    def calculate_atr(self, df: pd.DataFrame) -> float:
        if len(df) < self.atr_period + 1:
            return 0
        high = np.asarray(df["high"], dtype=float)
        low = np.asarray(df["low"], dtype=float)
        close = np.asarray(df["close"], dtype=float)
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

        stop_loss = entry_price - self.stop_loss_atr_multiplier * atr
        take_profit = entry_price + self.take_profit_atr_multiplier * atr
        trailing_stop = max(self.highest_price[code] - self.trailing_atr_multiplier * atr, entry_price)
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

    Level 1: 单股仓位 ≤ 模型配置上限
    Level 2: 行业集中度 ≤ 模型配置上限
    Level 3: 回撤超过模型阈值时减仓/清仓
    Level 4: 大盘相对均线状态限制开仓
    """

    def __init__(
        self,
        max_drawdown_to_reduce: float = 0.08,
        max_drawdown_to_close: float = 0.12,
        market_ma_period: int = 60,
        max_industry_pct: float = 0.30,
        max_correlation: float = 0.60,
        policy: Optional[Dict[str, Any]] = None,
    ):
        self.policy_source = _policy_source(policy)
        self.policy = _merge_policy(policy)
        portfolio = dict(self.policy.get("portfolio", {}))
        self.max_drawdown_to_reduce = float(portfolio.get("max_drawdown_to_reduce", max_drawdown_to_reduce) or max_drawdown_to_reduce)
        self.max_drawdown_to_close = float(portfolio.get("max_drawdown_to_close", max_drawdown_to_close) or max_drawdown_to_close)
        self.market_ma_period = int(portfolio.get("market_ma_period", market_ma_period) or market_ma_period)
        self.max_industry_pct = float(portfolio.get("max_industry_pct", max_industry_pct) or max_industry_pct)
        self.max_correlation = float(portfolio.get("max_correlation", max_correlation) or max_correlation)
        self.market_bull_threshold = float(portfolio.get("bull_threshold", _section_fallback("portfolio", "bull_threshold")) or _section_fallback("portfolio", "bull_threshold"))
        self.market_bear_threshold = float(portfolio.get("bear_threshold", _section_fallback("portfolio", "bear_threshold")) or _section_fallback("portfolio", "bear_threshold"))

    def get_industry(self, code: str) -> str:
        return industry_registry.get_industry(code)

    def check_market_state(self, hs300_data: Optional[pd.DataFrame]) -> str:
        if hs300_data is None or len(hs300_data) < self.market_ma_period:
            return "normal"
        close = np.asarray(hs300_data["close"], dtype=float)
        ma = float(np.mean(close[-self.market_ma_period:]))
        current = float(close[-1])
        if current > ma * self.market_bull_threshold:
            return "bull"
        if current < ma * self.market_bear_threshold:
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
        drawdown = (initial_capital - current_capital) / max(initial_capital, 1)

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

    def __init__(self, policy: Optional[Dict[str, Any]] = None):
        self.policy_source = _policy_source(policy)
        self.policy = _merge_policy(policy)
        self.dynamic_stop = DynamicStopLoss(policy=policy)
        self.portfolio_risk = PortfolioRiskManager(policy=policy)

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
