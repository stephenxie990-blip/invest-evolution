from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class EvalReport:
    """Structured evaluation report used by review/evolution/orchestration."""

    cycle_id: int
    as_of_date: str
    return_pct: float
    total_pnl: float
    total_trades: int
    win_rate: float
    regime: str
    is_profit: bool = False
    selected_codes: List[str] = field(default_factory=list)
    benchmark_passed: bool = False
    benchmark_strict_passed: bool = False
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    excess_return: float = 0.0
    data_mode: str = "unknown"
    selection_mode: str = "unknown"
    agent_used: bool = False
    llm_used: bool = False
    review_applied: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
