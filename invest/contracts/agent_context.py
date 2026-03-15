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
        explicit = _maybe_normalized_confidence(self.confidence)
        if explicit is not None:
            return explicit
        metadata = _maybe_normalized_confidence(self.metadata.get("confidence"))
        if metadata is not None:
            return metadata
        return _normalized_confidence(default, default=default)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["stock_summaries"] = [cast(StockSummaryView, item).to_dict() for item in self.stock_summaries]
        return payload


def _normalized_confidence(value: Any, *, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = float(default)
    return max(0.0, min(1.0, numeric))


def _maybe_normalized_confidence(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, numeric))


def resolve_agent_context_confidence(agent_context: Any, default: float = 0.72) -> float:
    resolver = getattr(agent_context, "effective_confidence", None)
    if callable(resolver):
        return _normalized_confidence(resolver(default=default), default=default)
    explicit_confidence = getattr(agent_context, "confidence", None)
    explicit = _maybe_normalized_confidence(explicit_confidence)
    if explicit is not None:
        return explicit
    metadata = dict(getattr(agent_context, "metadata", {}) or {})
    metadata_confidence = _maybe_normalized_confidence(metadata.get("confidence"))
    if metadata_confidence is not None:
        return metadata_confidence
    return _normalized_confidence(default, default=default)
