from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class AllocationPlan:
    """Structured model-allocation plan emitted by allocator layer."""

    as_of_date: str
    regime: str
    active_models: List[str] = field(default_factory=list)
    model_weights: Dict[str, float] = field(default_factory=dict)
    selected_configs: Dict[str, str] = field(default_factory=dict)
    cash_reserve: float = 0.0
    confidence: float = 0.0
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
