from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class ModelRoutingDecision:
    """Structured pre-selection routing decision emitted before model execution."""

    as_of_date: str
    current_model: str
    selected_model: str
    selected_config: str = ""
    regime: str = "unknown"
    regime_confidence: float = 0.0
    decision_confidence: float = 0.0
    candidate_models: List[str] = field(default_factory=list)
    candidate_weights: Dict[str, float] = field(default_factory=dict)
    cash_reserve_hint: float = 0.0
    decision_source: str = "rule"
    regime_source: str = "rule"
    switch_applied: bool = False
    hold_current: bool = False
    hold_reason: str = ""
    reasoning: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    agent_advice: Dict[str, Any] = field(default_factory=dict)
    allocation_plan: Dict[str, Any] = field(default_factory=dict)
    guardrail_checks: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
