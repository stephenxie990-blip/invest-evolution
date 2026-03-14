from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, cast

from .stock_summary import StockSummaryView


@dataclass
class StockSignal:
    """Structured per-stock signal emitted by an investment model."""

    code: str
    score: float
    rank: int
    direction: str = "long"
    weight_hint: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    trailing_pct: Optional[float] = None
    factor_values: Dict[str, float] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SignalPacketContext:
    market_stats: Dict[str, Any] = field(default_factory=dict)
    stock_summaries: Sequence[Mapping[str, Any]] = field(default_factory=list)
    raw_summaries: Sequence[Mapping[str, Any]] = field(default_factory=list)
    debug_metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.stock_summaries = [StockSummaryView.from_mapping(item) for item in list(self.stock_summaries or [])]
        self.raw_summaries = [StockSummaryView.from_mapping(item) for item in list(self.raw_summaries or [])]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market_stats": dict(self.market_stats),
            "stock_summaries": [cast(StockSummaryView, item).to_dict() for item in self.stock_summaries],
            "raw_summaries": [cast(StockSummaryView, item).to_dict() for item in self.raw_summaries],
            "debug_metadata": dict(self.debug_metadata),
        }


@dataclass
class SignalPacket:
    """Structured, machine-friendly signal bundle consumed by execution/evaluation."""

    as_of_date: str
    model_name: str
    config_name: str
    regime: str
    signals: List[StockSignal] = field(default_factory=list)
    selected_codes: List[str] = field(default_factory=list)
    max_positions: int = 0
    cash_reserve: float = 0.0
    params: Dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""
    context: SignalPacketContext = field(default_factory=SignalPacketContext)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.context, SignalPacketContext):
            self.context = SignalPacketContext(**dict(self.context or {}))

    def top_codes(self, limit: Optional[int] = None) -> List[str]:
        if self.selected_codes:
            return self.selected_codes[:limit] if limit is not None else list(self.selected_codes)
        ranked = sorted(self.signals, key=lambda item: item.score, reverse=True)
        codes = [item.code for item in ranked]
        return codes[:limit] if limit is not None else codes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "model_name": self.model_name,
            "config_name": self.config_name,
            "regime": self.regime,
            "signals": [item.to_dict() for item in self.signals],
            "selected_codes": list(self.selected_codes),
            "max_positions": self.max_positions,
            "cash_reserve": self.cash_reserve,
            "params": dict(self.params),
            "reasoning": self.reasoning,
            "context": self.context.to_dict(),
            "metadata": dict(self.metadata),
        }
