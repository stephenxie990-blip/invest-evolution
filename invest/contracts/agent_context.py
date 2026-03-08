from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class AgentContext:
    """Narrative, LLM-friendly context emitted by an investment model."""

    as_of_date: str
    model_name: str
    config_name: str
    summary: str
    narrative: str
    regime: str
    market_stats: Dict[str, Any] = field(default_factory=dict)
    stock_summaries: List[Dict[str, Any]] = field(default_factory=list)
    candidate_codes: List[str] = field(default_factory=list)
    risk_hints: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
