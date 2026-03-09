from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


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
    entry_reason: str = ""                # 入场理由
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
    source: str = ""
    entry_reason: str = ""
    exit_reason: str = ""
    exit_trigger: str = ""
    entry_date: str = ""
    entry_price: float = 0.0
    holding_days: int = 0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    trailing_pct: Optional[float] = None


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


__all__ = [
    "Action",
    "Position",
    "TradeRecord",
    "SimulationResult",
    "RiskMetrics",
    "EmergencyType",
    "EmergencyAction",
    "EmergencyEvent",
    "CandidateStock",
    "CandidatePool",
]
