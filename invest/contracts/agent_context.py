from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Sequence, cast

from .stock_summary import StockSummaryView


@dataclass
class AgentContext:
    """Narrative, LLM-friendly context emitted by an investment model."""

    as_of_date: str
    model_name: str
    config_name: str
    summary: str
    narrative: str
    regime: str
    confidence: float = 0.72
    market_stats: Dict[str, Any] = field(default_factory=dict)
    stock_summaries: Sequence[Mapping[str, Any]] = field(default_factory=list)
    candidate_codes: List[str] = field(default_factory=list)
    risk_hints: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.stock_summaries = [StockSummaryView.from_mapping(item) for item in list(self.stock_summaries or [])]

    def effective_confidence(self, default: float = 0.72) -> float:
        try:
            if self.confidence is not None:
                return float(self.confidence)
        except (TypeError, ValueError):
            pass
        try:
            return float(self.metadata.get("confidence", default) or default)
        except (TypeError, ValueError):
            return float(default)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["stock_summaries"] = [cast(StockSummaryView, item).to_dict() for item in self.stock_summaries]
        return payload
