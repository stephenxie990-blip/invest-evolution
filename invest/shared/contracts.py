import logging
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

logger = logging.getLogger(__name__)


@dataclass
class PositionPlan:
    """单个持仓计划"""
    code: str                              # 股票代码
    priority: int                          # 优先级（1=最高）
    weight: float = 0.20                   # 目标仓位权重 (0.0-1.0)
    entry_method: str = "market"           # "market"=开盘买 / "limit"=限价
    entry_price: Optional[float] = None    # limit 时的目标买入价
    stop_loss_pct: float = 0.05            # 止损比例（正数，跌5%止损）
    take_profit_pct: float = 0.15          # 止盈比例（正数，涨15%止盈）
    trailing_pct: Optional[float] = None   # 跟踪止盈回撤比例
    expire_days: int = 5                   # limit单有效天数
    max_hold_days: int = 30                # 最长持有天数
    reason: str = ""                       # 选股理由
    source: str = "algorithm"             # 推荐来源


@dataclass
class TradingPlan:
    """
    完整交易计划

    Selector/Meeting → TradingPlan → Trader 的唯一数据合同
    Trader 只按此计划执行，不自行选股
    """
    date: str                              # 计划生成日期
    positions: List[PositionPlan] = field(default_factory=list)
    cash_reserve: float = 0.0             # 现金储备比例 (0.0-1.0)
    max_positions: int = 2                 # 最大同时持仓数
    source: str = "algorithm"             # "algorithm" / "meeting"
    reasoning: str = ""                    # 整体决策理由

    @property
    def stock_codes(self) -> List[str]:
        """返回所有计划中的股票代码"""
        return [p.code for p in self.positions]

    def get_position_plan(self, code: str) -> Optional[PositionPlan]:
        """根据股票代码获取持仓计划"""
        for p in self.positions:
            if p.code == code:
                return p
        return None


def make_simple_plan(
    selected_stocks: List[str],
    cutoff_date: str = "",
    stock_scores: Optional[Dict[str, float]] = None,
    stop_loss_pct: float = 0.05,
    take_profit_pct: float = 0.15,
    trailing_pct: float = 0.10,
    position_size: float = 0.20,
    max_positions: int = 2,
    max_hold_days: int = 30,
) -> TradingPlan:
    """
    生成简单的算法交易计划（算法兜底，不依赖 LLM）

    Args:
        selected_stocks: 股票代码列表（已按得分排序）
        cutoff_date: 截断日期
        stock_scores: {code: score}，可选
    """
    scores = stock_scores or {}
    positions = []

    for i, code in enumerate(selected_stocks):
        score = scores.get(code, 0.0)
        positions.append(PositionPlan(
            code=code,
            priority=i + 1,
            weight=position_size,
            entry_method="market",
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_pct=trailing_pct,
            max_hold_days=max_hold_days,
            reason=f"多因子选股得分:{score:.2f}",
            source="algorithm",
        ))

    plan = TradingPlan(
        date=cutoff_date,
        positions=positions,
        cash_reserve=0.0,
        max_positions=max_positions,
        source="algorithm",
        reasoning=(
            f"算法选股: {len(positions)}只候选, "
            f"最大持仓{max_positions}, "
            f"止损{stop_loss_pct:.0%}, 止盈{take_profit_pct:.0%}"
        ),
    )

    logger.info(
        f"📋 生成交易计划: {len(positions)}只候选, "
        f"最大持仓{max_positions}, "
        f"止损{stop_loss_pct:.0%}, 止盈{take_profit_pct:.0%}"
    )
    return plan


# ============================================================

__all__ = ["PositionPlan", "TradingPlan", "make_simple_plan"]
