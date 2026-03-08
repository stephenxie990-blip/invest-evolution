from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class StrategyAdvice:
    """Structured advice emitted by Agents/Meetings."""

    source: str
    selected_codes: List[str] = field(default_factory=list)
    selected_meta: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    reasoning: str = ""
    strategy_suggestions: List[str] = field(default_factory=list)
    param_adjustments: Dict[str, float] = field(default_factory=dict)
    agent_weight_adjustments: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
