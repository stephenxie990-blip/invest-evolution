"""
投资进化系统 - 交易执行

包含：
1. 枚举与数据类：Action, Position, TradeRecord, SimulationResult, RiskMetrics
2. EmergencyType / EmergencyEvent / EmergencyDetector — 异常检测（持仓期间规则）
3. DynamicStopLoss    — 基于 ATR 的动态止损止盈
4. PortfolioRiskManager — 组合风控（行业集中度 / 回撤）
5. RiskController     — 整合所有风控逻辑的统一接口
6. CandidateStock / CandidatePool — T0 候选池
7. DailyRanker        — 每日实时评分器
8. TradingScheduler   — 交易调度器（买入/卖出决策）
9. SimulatedTrader    — 30天模拟交易引擎（核心）

交易成本（A股实际水平）：
    佣金: 万2.5（双向）
    印花税: 万5（仅卖出）
    滑点: 0.2%
    单次往返合计 ≈ 0.55%
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core import TradingPlan, PositionPlan

logger = logging.getLogger(__name__)


# ============================================================
# Part 1: 枚举与数据类
# ============================================================

class Action(str, Enum):
    BUY  = "买入"
    SELL = "卖出"
    HOLD = "持有"
    SKIP = "不操作"


@dataclass
class Position:
    """持仓"""
    ts_code: str
    name: str
    entry_date: str
    entry_price: float
    shares: int
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    trailing_pct: Optional[float] = None   # 跟踪止盈回撤比例
    highest_price: float = 0.0             # 入场后最高价
    source: str = "algorithm"              # 推荐来源
    plan_stop_loss_pct: float = 0.05
    plan_take_profit_pct: float = 0.15

    @property
    def market_value(self) -> float:
        return self.shares * self.entry_price

    def current_value(self, current_price: float) -> float:
        return self.shares * current_price

    def pnl(self, current_price: float) -> float:
        return (current_price - self.entry_price) * self.shares

    def pnl_pct(self, current_price: float) -> float:
        if self.entry_price == 0:
            return 0.0
        return (current_price - self.entry_price) / self.entry_price * 100

    def update_highest(self, current_price: float):
        if current_price > self.highest_price:
            self.highest_price = current_price


@dataclass
class TradeRecord:
    """交易记录"""
    date: str
    action: Action
    ts_code: Optional[str]
    price: float
    shares: int
    reason: str
    pnl: float = 0.0
    pnl_pct: float = 0.0
    capital_before: float = 0.0
    capital_after: float = 0.0
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    volume: float = 0.0
    amount: float = 0.0
    pct_chg: float = 0.0
    turnover_rate: float = 0.0
    market_cap: float = 0.0


@dataclass
class SimulationResult:
    """模拟交易结果"""
    initial_capital: float
    final_capital: float
    final_value: float
    return_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    positions: List[Position]
    trade_history: List[TradeRecord]
    daily_records: List[Dict] = field(default_factory=list)
    per_stock_pnl: Dict[str, float] = field(default_factory=dict)


@dataclass
class RiskMetrics:
    """风险指标"""
    portfolio_value: float
    portfolio_return: float
    max_drawdown: float
    position_count: int
    industry_exposure: Dict[str, float]
    correlation: float


# ============================================================
# Part 2: 异常检测器
# ============================================================

class EmergencyType(str, Enum):
    SINGLE_STOCK_CRASH  = "single_stock_crash"
    RAPID_PORTFOLIO_LOSS = "rapid_portfolio_loss"
    ALL_POSITIONS_RED   = "all_positions_red"


class EmergencyAction(str, Enum):
    LOG_ONLY     = "log"
    TIGHTEN_STOP = "tighten_stop"
    SELL_WORST   = "sell_worst"
    REDUCE_ALL   = "reduce_all"


@dataclass
class EmergencyEvent:
    date: str
    event_type: EmergencyType
    severity: float          # 0.0-1.0
    description: str
    action: EmergencyAction
    affected_codes: List[str] = field(default_factory=list)


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

@dataclass
class CandidateStock:
    """候选股票"""
    code: str
    strategy_type: str  # "momentum" or "reversal"
    score: float
    reasons: List[str]


class CandidatePool:
    """T0 时刻筛选出的候选池（30天内固定）"""

    def __init__(self, max_size: int = 20):
        self.max_size = max_size
        self.stocks: List[CandidateStock] = []
        self.strategy_a_count = 0
        self.strategy_b_count = 0

    def add(self, stock: CandidateStock):
        if len(self.stocks) >= self.max_size:
            return
        self.stocks.append(stock)
        if stock.strategy_type == "momentum":
            self.strategy_a_count += 1
        else:
            self.strategy_b_count += 1

    def get_all(self) -> List[CandidateStock]:
        return self.stocks

    def get_by_strategy(self, strategy_type: str) -> List[CandidateStock]:
        return [s for s in self.stocks if s.strategy_type == strategy_type]

    def __len__(self) -> int:
        return len(self.stocks)


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
        from core import compute_rsi
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

class SimulatedTrader:
    """
    30天模拟交易引擎

    核心流程（每日 step()）：
    1. 组合级风控检查 → 必要时减仓/清仓
    2. 持仓止损/止盈/跟踪止盈检查（遵守 T+1）
    3. 持仓天数递增
    4. 空余仓位时按 TradingPlan 或内部策略买入
    5. 异常检测

    支持：
    - ATR 动态止损
    - 跟踪止盈（trailing stop）
    - A 股 T+1 规则
    - Phase 3 异常检测器
    - 真实交易成本（佣金 + 印花税 + 滑点）
    """

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        max_positions: int = 5,
        position_size_pct: float = 0.20,
        commission_rate: float = 0.00025,   # 万2.5，双向
        stamp_tax_rate: float = 0.0005,     # 万5，仅卖出
        slippage_rate: float = 0.002,       # 滑点 0.2%
        enable_risk_control: bool = True,
    ):
        self.initial_capital  = initial_capital
        self.cash             = initial_capital
        self.max_positions    = max_positions
        self.position_size_pct = position_size_pct
        self.commission_rate  = commission_rate
        self.stamp_tax_rate   = stamp_tax_rate
        self.slippage_rate    = slippage_rate
        self.enable_risk_control = enable_risk_control

        # 风控
        if enable_risk_control:
            self.dynamic_stop    = DynamicStopLoss(atr_period=14)
            self.risk_controller = RiskController()

        self.positions:     List[Position]    = []
        self.trade_history: List[TradeRecord] = []
        self.daily_records: List[Dict]        = []

        self.stock_data: Dict[str, pd.DataFrame] = {}
        self.stock_info: Dict[str, dict]         = {}
        self.current_date: str = ""

        self.cooldown_days: Dict[str, int] = {}
        self.hold_days:     Dict[str, int] = {}

        self.peak_value = initial_capital
        # Cache ATR by (code, date) to avoid stale volatility values across days.
        self.atr_cache: Dict[Tuple[str, str], float] = {}

        # TradingPlan
        self.trading_plan: Optional[TradingPlan] = None
        self._plan_bought: set = set()
        self.default_stop_loss_pct   = 0.05
        self.default_take_profit_pct = 0.15

        # 异常检测
        self.emergency_detector = EmergencyDetector()
        self.emergency_events:  List[EmergencyEvent] = []

        if enable_risk_control:
            logger.info("✅ 风控系统已启用: ATR动态止损 + 组合风控 + 异常检测")

    # ===== 数据接口 =====

    def set_stock_data(self, stock_data: Dict[str, pd.DataFrame]):
        self.stock_data = stock_data
        self.atr_cache.clear()

    def set_stock_info(self, stock_info: Dict[str, dict]):
        self.stock_info = stock_info

    def set_trading_plan(self, plan: TradingPlan):
        self.trading_plan  = plan
        self.max_positions = plan.max_positions
        self._plan_bought  = set()
        if plan.positions:
            self.default_stop_loss_pct   = plan.positions[0].stop_loss_pct
            self.default_take_profit_pct = plan.positions[0].take_profit_pct
        logger.info(f"📋 TradingPlan 已设置: {len(plan.positions)} 只候选, 最大持仓 {plan.max_positions}")

    # ===== 价格查询 =====

    def get_price(self, ts_code: str, date: str) -> Optional[float]:
        if ts_code not in self.stock_data:
            return None
        df = self.stock_data[ts_code]
        if df.empty:
            return None
        row = df[df["trade_date"] == date]
        if not row.empty:
            price = float(row.iloc[0]["close"])
            return price if 0 < price <= 10000 else None
        df_b = df[df["trade_date"] < date]
        if not df_b.empty:
            return float(df_b.iloc[-1]["close"])
        return None

    def get_day_metrics(self, ts_code: str, date: str) -> dict:
        if ts_code not in self.stock_data:
            return {}
        df = self.stock_data[ts_code]
        if df.empty:
            return {}
        row = df[df["trade_date"] == date]
        if row.empty:
            df_b = df[df["trade_date"] < date]
            if df_b.empty:
                return {}
            row = df_b.iloc[-1:]
        r = row.iloc[0]
        return {
            "open": float(r.get("open", 0)),
            "high": float(r.get("high", 0)),
            "low": float(r.get("low", 0)),
            "close": float(r.get("close", 0)),
            "volume": float(r.get("volume", 0)),
            "amount": float(r.get("amount", 0)),
            "pct_chg": float(r.get("pct_chg", 0)),
        }

    def get_historical_data(
        self, ts_code: str, end_date: str, days: int = 60
    ) -> Optional[pd.DataFrame]:
        if ts_code not in self.stock_data:
            return None
        df = self.stock_data[ts_code].copy()
        df = df[df["trade_date"] <= end_date]
        return df.tail(days) if len(df) > days else df

    def get_total_value(self) -> float:
        total = self.cash
        for pos in self.positions:
            price = self.get_price(pos.ts_code, self.current_date)
            if price:
                total += pos.shares * price
        return total

    # ===== 买入 / 卖出 =====

    def can_buy(self) -> bool:
        return len(self.positions) < self.max_positions

    def buy(
        self,
        ts_code: str,
        date: str,
        price: float,
        reason: str = "",
        stop_loss_pct: float = 0.05,
        take_profit_pct: float = 0.15,
        position_weight: Optional[float] = None,
        trailing_pct: Optional[float] = None,
        source: str = "algorithm",
    ) -> bool:
        if not self.can_buy():
            return False

        buy_price = price * (1 + self.slippage_rate)

        if not buy_price or buy_price <= 0 or math.isnan(self.cash) or math.isinf(self.cash) or self.cash <= 0:
            logger.warning(f"无效状态 {ts_code}: cash={self.cash}, price={buy_price}")
            return False

        weight = position_weight if position_weight is not None else self.position_size_pct
        available_cash = min(self.initial_capital * weight, self.initial_capital * 0.5, self.cash)

        shares = (int(available_cash / buy_price) // 100) * 100
        if shares <= 0:
            return False

        cost      = shares * buy_price
        commission = cost * self.commission_rate
        total_cost = cost + commission

        if total_cost > self.cash:
            shares = ((int((self.cash - commission) / buy_price)) // 100) * 100
            if shares <= 0:
                return False
            cost = shares * buy_price
            total_cost = cost + commission

        self.cash -= total_cost

        name = self.stock_info.get(ts_code, {}).get("name", ts_code)
        stop_loss = buy_price * (1 - stop_loss_pct)
        take_profit = None if (trailing_pct and trailing_pct > 0) else buy_price * (1 + take_profit_pct)

        pos = Position(
            ts_code=ts_code,
            name=name,
            entry_date=date,
            entry_price=buy_price,
            shares=shares,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_pct=trailing_pct,
            highest_price=buy_price,
            source=source,
            plan_stop_loss_pct=stop_loss_pct,
            plan_take_profit_pct=take_profit_pct,
        )
        self.positions.append(pos)
        self.hold_days[ts_code] = 0

        metrics = self.get_day_metrics(ts_code, date)
        self.trade_history.append(TradeRecord(
            date=date, action=Action.BUY, ts_code=ts_code,
            price=buy_price, shares=shares, reason=reason,
            capital_before=self.cash + total_cost, capital_after=self.cash,
            open_price=metrics.get("open", 0), high_price=metrics.get("high", 0),
            low_price=metrics.get("low", 0), volume=metrics.get("volume", 0),
            amount=metrics.get("amount", 0), pct_chg=metrics.get("pct_chg", 0),
        ))
        logger.info(
            f"买入 {ts_code} {shares}股 @ ¥{buy_price:.2f} | {date} | "
            f"涨跌幅:{metrics.get('pct_chg',0):+.2f}%"
        )
        return True

    def sell(
        self,
        position: Position,
        date: str,
        price: float,
        reason: str = "",
    ) -> float:
        return self._sell_partial(position, date, price, position.shares, reason)

    def _sell_partial(
        self,
        position: Position,
        date: str,
        price: float,
        shares: int,
        reason: str = "",
    ) -> float:
        if shares <= 0:
            return 0.0
        if shares > position.shares:
            shares = position.shares

        sell_price = price * (1 - self.slippage_rate)
        proceeds   = shares * sell_price
        sell_commission = proceeds * self.commission_rate
        stamp_tax  = proceeds * self.stamp_tax_rate
        net_proceeds = proceeds - sell_commission - stamp_tax

        buy_commission = shares * position.entry_price * self.commission_rate
        cost = shares * position.entry_price
        pnl  = net_proceeds - cost - buy_commission
        self.cash += net_proceeds

        metrics = self.get_day_metrics(position.ts_code, date)
        self.trade_history.append(TradeRecord(
            date=date, action=Action.SELL, ts_code=position.ts_code,
            price=sell_price, shares=shares, reason=reason,
            pnl=pnl, pnl_pct=pnl / cost * 100 if cost else 0,
            capital_before=self.cash - net_proceeds, capital_after=self.cash,
            open_price=metrics.get("open", 0), high_price=metrics.get("high", 0),
            low_price=metrics.get("low", 0), volume=metrics.get("volume", 0),
            amount=metrics.get("amount", 0), pct_chg=metrics.get("pct_chg", 0),
        ))

        position.shares -= shares
        if position.shares <= 0:
            self.positions = [p for p in self.positions if p != position]
            self.cooldown_days[position.ts_code] = 3
            self.hold_days.pop(position.ts_code, None)

        logger.info(
            f"卖出 {position.ts_code} {shares}股 @ ¥{sell_price:.2f} | {date} | "
            f"盈亏: {pnl:+.2f} ({pnl/cost*100:+.2f}%)"
        )
        return pnl

    # ===== 止损止盈检查 =====

    def check_and_close_positions(self, date: str) -> List[float]:
        pnl_list = []
        for pos in self.positions[:]:
            if self.hold_days.get(pos.ts_code, 0) < 1:
                continue  # T+1

            current_price = self.get_price(pos.ts_code, date)
            if not current_price:
                # No executable market data for this symbol on/before date.
                # Never fall back to another symbol's price.
                continue
            pos.update_highest(current_price)

            # 跟踪止盈
            if pos.trailing_pct and pos.trailing_pct > 0 and pos.highest_price > 0:
                drop = (pos.highest_price - current_price) / pos.highest_price
                if drop >= pos.trailing_pct:
                    pnl_list.append(self.sell(
                        pos, date, current_price,
                        f"跟踪止盈(最高{pos.highest_price:.2f},回落{drop:.1%})"
                    ))
                    continue

            # 固定止损
            if pos.stop_loss and current_price <= pos.stop_loss:
                pnl_list.append(self.sell(pos, date, current_price, "止损"))
                continue

            # 固定止盈
            if pos.take_profit and current_price >= pos.take_profit:
                pnl_list.append(self.sell(pos, date, current_price, "止盈"))
                continue

            # ATR 动态止损
            if self.enable_risk_control:
                atr = self._get_atr(pos.ts_code, date)
                if atr and atr > 0:
                    stop_p = pos.entry_price - 2 * atr
                    take_p = pos.entry_price + 3 * atr
                    if current_price <= stop_p:
                        pnl_list.append(self.sell(pos, date, current_price, f"ATR止损({atr:.2f})"))
                        continue
                    if current_price >= take_p:
                        pnl_list.append(self.sell(pos, date, current_price, f"ATR止盈({atr:.2f})"))
                        continue

        return pnl_list

    def _get_atr(self, ts_code: str, date: str) -> Optional[float]:
        cache_key = (ts_code, date)
        if cache_key in self.atr_cache:
            return self.atr_cache[cache_key]
        df = self.stock_data.get(ts_code)
        if df is None:
            return None
        df_b = df[df["trade_date"] <= date]
        if len(df_b) < 20:
            return None
        atr = self.dynamic_stop.calculate_atr(df_b)
        if atr > 0:
            self.atr_cache[cache_key] = atr
        return atr if atr > 0 else None

    # ===== 风控动作 =====

    def check_portfolio_risk(self, date: str) -> dict:
        if not self.enable_risk_control:
            return {"action": "NONE", "reason": "", "drawdown": 0}
        current_value = self.get_total_value()
        if current_value > self.peak_value:
            self.peak_value = current_value
        drawdown = (self.peak_value - current_value) / self.peak_value if self.peak_value > 0 else 0
        if drawdown > 0.12:
            return {"action": "CLOSE_ALL", "reason": f"回撤{drawdown:.1%}>12%，清仓", "drawdown": drawdown}
        if drawdown > 0.08:
            return {"action": "REDUCE", "reason": f"回撤{drawdown:.1%}>8%，减仓50%", "drawdown": drawdown}
        return {"action": "NONE", "reason": "", "drawdown": drawdown}

    def check_can_open_position(self, ts_code: str, weight: float = 0.20) -> Tuple[bool, str]:
        if not self.enable_risk_control:
            return True, "风控未启用"
        if weight > 0.20:
            return False, f"单股仓位{weight:.1%}>20%"
        if hasattr(self, "risk_controller"):
            sector = self.risk_controller.portfolio_risk.get_industry(ts_code)
            sector_exp = weight + sum(
                pos.shares * pos.entry_price / max(self.get_total_value(), 1)
                for pos in self.positions
                if self.risk_controller.portfolio_risk.get_industry(pos.ts_code) == sector
            )
            if sector_exp > 0.30:
                return False, f"行业'{sector}'{sector_exp:.1%}>30%"
        return True, "通过"

    def force_close_all(self, date: str, reason: str = "风控清仓") -> List[float]:
        return [self.sell(pos, date, self.get_price(pos.ts_code, date) or pos.entry_price, reason)
                for pos in self.positions[:]]

    def reduce_positions(self, date: str, reduce_pct: float = 0.5) -> List[float]:
        pnl_list = []
        n = int(len(self.positions) * reduce_pct)
        for pos in self.positions[:n]:
            price = self.get_price(pos.ts_code, date)
            if not price:
                continue
            half = pos.shares // 2
            if half <= 0:
                continue
            pnl = self._sell_partial(pos, date, price, half, "风控减仓50%")
            pnl_list.append(pnl)
        return pnl_list

    # ===== 按计划执行买入 =====

    def _execute_plan_step(self, date: str):
        if not self.trading_plan:
            return
        for pos_plan in self.trading_plan.positions:
            if not self.can_buy():
                break
            code = pos_plan.code
            if code in self._plan_bought:
                continue
            if any(p.ts_code == code for p in self.positions):
                continue
            if code in self.cooldown_days:
                continue
            price = self.get_price(code, date)
            if not price or price <= 0:
                continue
            if self.enable_risk_control:
                ok, reason = self.check_can_open_position(code, pos_plan.weight)
                if not ok:
                    logger.debug(f"风控拦截 {code}: {reason}")
                    continue
            success = self.buy(
                ts_code=code, date=date, price=price, reason=pos_plan.reason,
                stop_loss_pct=pos_plan.stop_loss_pct, take_profit_pct=pos_plan.take_profit_pct,
                position_weight=pos_plan.weight, trailing_pct=pos_plan.trailing_pct,
                source=pos_plan.source,
            )
            if success:
                self._plan_bought.add(code)

    # ===== 内部兜底选股 =====

    def select_stocks(self, date: str) -> List[dict]:
        candidates = []
        for ts_code in self.stock_data:
            if any(p.ts_code == ts_code for p in self.positions):
                continue
            if ts_code in self.cooldown_days:
                continue
            hist = self.get_historical_data(ts_code, date, days=60)
            if hist is None or len(hist) < 30:
                continue
            recent = hist.tail(5)
            if recent.empty:
                continue
            score = recent["pct_chg"].mean() * 0.6 + recent.iloc[-1]["pct_chg"] * 0.4
            candidates.append({"ts_code": ts_code, "score": score, "price": float(recent.iloc[-1]["close"])})
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:5]

    # ===== 异常处理 =====

    def _handle_emergency(self, event: EmergencyEvent, date: str):
        if event.action == EmergencyAction.TIGHTEN_STOP:
            for pos in self.positions:
                if event.affected_codes and pos.ts_code not in event.affected_codes:
                    continue
                price = self.get_price(pos.ts_code, date)
                if price:
                    new_stop = price * 0.98
                    if pos.stop_loss is None or new_stop > pos.stop_loss:
                        pos.stop_loss = new_stop

        elif event.action == EmergencyAction.SELL_WORST:
            for code in (event.affected_codes or []):
                for pos in self.positions[:]:
                    if pos.ts_code == code and self.hold_days.get(code, 0) >= 1:
                        price = self.get_price(code, date)
                        if price:
                            self.sell(pos, date, price, f"异常卖出:{event.description}")
                        break

        elif event.action == EmergencyAction.REDUCE_ALL:
            self.reduce_positions(date, 0.5)

        self.emergency_events.append(event)

    # ===== 单日步 =====

    def step(self, date: str) -> Dict:
        self.current_date = date
        trade_count_before = len(self.trade_history)

        # 冷却期递减
        for code in list(self.cooldown_days):
            self.cooldown_days[code] -= 1
            if self.cooldown_days[code] <= 0:
                del self.cooldown_days[code]

        result = {
            "date": date,
            "action": Action.HOLD,
            "risk_action": "NONE",
        }

        # 组合级风控
        if self.enable_risk_control and self.positions:
            risk = self.check_portfolio_risk(date)
            if risk["action"] == "CLOSE_ALL":
                logger.warning(f"⚠️ 风控: {risk['reason']}")
                self.force_close_all(date)
                result["risk_action"] = "CLOSE_ALL"
            elif risk["action"] == "REDUCE":
                logger.warning(f"⚠️ 风控: {risk['reason']}")
                self.reduce_positions(date)
                result["risk_action"] = "REDUCE"

        # 持仓止损/止盈
        self.check_and_close_positions(date)

        # 持仓天数 +1（T+1）
        for code in list(self.hold_days):
            self.hold_days[code] += 1

        # 买入
        if self.can_buy():
            if self.trading_plan:
                self._execute_plan_step(date)
            else:
                for c in self.select_stocks(date):
                    if not self.can_buy():
                        break
                    if self.enable_risk_control:
                        ok, _ = self.check_can_open_position(c["ts_code"], self.position_size_pct)
                        if not ok:
                            continue
                    if self.buy(c["ts_code"], date, c["price"], f"选股得分:{c['score']:.2f}"):
                        result["action"] = Action.BUY

        # 异常检测
        if self.enable_risk_control and self.positions:
            events = self.emergency_detector.check(self, date)
            for e in events:
                self._handle_emergency(e, date)
            if events:
                result["emergency_events"] = [
                    {"type": e.event_type.value, "action": e.action.value, "description": e.description}
                    for e in events
                ]

        trades_today = self.trade_history[trade_count_before:]
        if trades_today:
            has_buy = any(t.action == Action.BUY for t in trades_today)
            has_sell = any(t.action == Action.SELL for t in trades_today)
            if has_buy and not has_sell:
                result["action"] = Action.BUY
            elif has_sell and not has_buy:
                result["action"] = Action.SELL
            else:
                result["action"] = Action.HOLD

        end_position_value = sum(
            pos.shares * (self.get_price(pos.ts_code, date) or pos.entry_price)
            for pos in self.positions
        )
        result["cash"] = self.cash
        result["positions"] = len(self.positions)
        result["position_value"] = end_position_value
        result["total_value"] = self.cash + end_position_value
        result["trades_today"] = len(trades_today)

        self.daily_records.append(result)
        return result

    # ===== 完整模拟 =====

    def run_simulation(
        self,
        start_date: str,
        trading_dates: List[str],
    ) -> SimulationResult:
        """
        运行完整模拟交易

        Args:
            start_date: 起始日期 YYYYMMDD
            trading_dates: 所有交易日期列表

        Returns:
            SimulationResult
        """
        logger.info(f"开始模拟: {start_date}, 共 {len(trading_dates)} 天")
        self.emergency_detector.reset()
        self.emergency_events.clear()

        start_idx = next((i for i, d in enumerate(trading_dates) if d >= start_date), 0)

        for i in range(start_idx, len(trading_dates)):
            date = trading_dates[i]
            self.step(date)
            if i % 5 == 0:
                logger.info(
                    f"第{i - start_idx + 1}天: {date}, "
                    f"现金{self.cash:.2f}, 持仓{len(self.positions)}只"
                )

        # 结算
        final_date = trading_dates[-1]
        for pos in self.positions[:]:
            price = self.get_price(pos.ts_code, final_date) or pos.entry_price
            self.sell(pos, final_date, price, "结算平仓")

        final_value = self.cash
        total_pnl   = final_value - self.initial_capital
        return_pct  = total_pnl / self.initial_capital * 100

        wins   = [t for t in self.trade_history if t.action == Action.SELL and t.pnl > 0]
        losses = [t for t in self.trade_history if t.action == Action.SELL and t.pnl <= 0]

        per_stock_pnl: Dict[str, float] = {}
        for t in self.trade_history:
            if t.action == Action.SELL:
                per_stock_pnl[t.ts_code] = per_stock_pnl.get(t.ts_code, 0) + t.pnl

        em_summary = self.emergency_detector.get_summary()
        if em_summary["total_events"] > 0:
            logger.info(f"⚠️ 异常事件: {em_summary['total_events']}次 ({em_summary['by_type']})")

        logger.info(f"模拟完成: 收益率{return_pct:.2f}%, 赢{len(wins)}/输{len(losses)}")

        return SimulationResult(
            initial_capital=self.initial_capital,
            final_capital=self.cash,
            final_value=final_value,
            return_pct=return_pct,
            total_trades=len(wins) + len(losses),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=len(wins) / (len(wins) + len(losses)) if (wins or losses) else 0,
            total_pnl=total_pnl,
            positions=self.positions,
            trade_history=self.trade_history,
            daily_records=self.daily_records,
            per_stock_pnl=per_stock_pnl,
        )

# ============================================================
# day_by_day_simulator.py
