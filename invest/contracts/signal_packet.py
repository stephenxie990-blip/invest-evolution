from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


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
    metadata: Dict[str, Any] = field(default_factory=dict)

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
            "metadata": dict(self.metadata),
        }
