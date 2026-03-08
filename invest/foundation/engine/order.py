from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass
class OrderIntent:
    code: str
    action: str
    weight: float
    stop_loss_pct: float
    take_profit_pct: float
    trailing_pct: Optional[float] = None
    reason: str = ""
    metadata: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
