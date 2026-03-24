from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, cast

from invest_evolution.investment.shared.contracts import PositionPlan, TradingPlan
from .reports import SignalPacket, StockSummaryView

# Agent contracts




@dataclass
class AgentContext:
    """Narrative, LLM-friendly context emitted by a manager runtime."""

    as_of_date: str
    manager_id: str
    manager_config_ref: str
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


# Manager contracts

def _clean_list(values: List[str] | None = None) -> List[str]:
    ordered: List[str] = []
    for value in list(values or []):
        text = str(value or "").strip()
        if text and text not in ordered:
            ordered.append(text)
    return ordered


@dataclass
class ManagerSpec:
    """Static contract that defines a manager's mandate and runtime envelope."""

    manager_id: str
    runtime_id: str
    display_name: str = ""
    runtime_config_ref: str = ""
    mandate: str = ""
    style_profile: Dict[str, float] = field(default_factory=dict)
    factor_profile: Dict[str, float] = field(default_factory=dict)
    risk_profile: Dict[str, Any] = field(default_factory=dict)
    capability_allowlist: List[str] = field(default_factory=list)
    memory_policy: Dict[str, Any] = field(default_factory=dict)
    review_policy: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.manager_id = str(self.manager_id or "").strip()
        self.runtime_id = str(self.runtime_id or "").strip()
        self.display_name = str(self.display_name or self.manager_id or self.runtime_id).strip()
        self.runtime_config_ref = str(self.runtime_config_ref or "").strip()
        if not self.manager_id:
            raise ValueError("manager_id is required")
        if not self.runtime_id:
            raise ValueError("runtime_id is required")
        self.capability_allowlist = _clean_list(self.capability_allowlist)
        self.style_profile = {
            str(key): float(value)
            for key, value in dict(self.style_profile or {}).items()
        }
        self.factor_profile = {
            str(key): float(value)
            for key, value in dict(self.factor_profile or {}).items()
        }
        self.risk_profile = dict(self.risk_profile or {})
        self.memory_policy = dict(self.memory_policy or {})
        self.review_policy = dict(self.review_policy or {})
        self.metadata = dict(self.metadata or {})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ManagerRunContext:
    """Per-cycle context shared across every manager in the same market snapshot."""

    as_of_date: str
    regime: str
    market_stats: Dict[str, Any] = field(default_factory=dict)
    factor_snapshot: Dict[str, Any] = field(default_factory=dict)
    budget_weights: Dict[str, float] = field(default_factory=dict)
    runtime_params: Dict[str, Any] = field(default_factory=dict)
    active_manager_ids: List[str] = field(default_factory=list)
    governance_context: Dict[str, Any] = field(default_factory=dict)
    review_context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.as_of_date = str(self.as_of_date or "").strip()
        self.regime = str(self.regime or "unknown").strip() or "unknown"
        if not self.as_of_date:
            raise ValueError("as_of_date is required")
        self.market_stats = dict(self.market_stats or {})
        self.factor_snapshot = dict(self.factor_snapshot or {})
        self.budget_weights = {
            str(key): float(value)
            for key, value in dict(self.budget_weights or {}).items()
        }
        self.runtime_params = dict(self.runtime_params or {})
        self.active_manager_ids = [
            str(item).strip()
            for item in list(self.active_manager_ids or [])
            if str(item).strip()
        ]
        self.governance_context = dict(self.governance_context or {})
        self.review_context = dict(self.review_context or {})
        self.metadata = dict(self.metadata or {})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ManagerPlanPosition:
    """Single stock idea produced by an individual manager."""

    code: str
    rank: int
    target_weight: float
    score: float = 0.0
    entry_method: str = "market"
    entry_price: Optional[float] = None
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.15
    trailing_pct: Optional[float] = None
    max_hold_days: int = 30
    thesis: str = ""
    evidence: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.code = str(self.code or "").strip()
        self.rank = int(self.rank)
        self.target_weight = max(0.0, float(self.target_weight or 0.0))
        self.score = float(self.score or 0.0)
        self.entry_method = str(self.entry_method or "market").strip() or "market"
        self.entry_price = None if self.entry_price is None else float(self.entry_price)
        self.stop_loss_pct = float(self.stop_loss_pct or 0.0)
        self.take_profit_pct = float(self.take_profit_pct or 0.0)
        self.trailing_pct = None if self.trailing_pct is None else float(self.trailing_pct)
        self.max_hold_days = int(self.max_hold_days or 0)
        self.thesis = str(self.thesis or "").strip()
        self.evidence = [str(item) for item in list(self.evidence or []) if str(item).strip()]
        self.metadata = dict(self.metadata or {})
        if not self.code:
            raise ValueError("position code is required")
        if self.rank <= 0:
            raise ValueError("position rank must be positive")

    def to_position_plan(self) -> PositionPlan:
        return PositionPlan(
            code=self.code,
            priority=self.rank,
            weight=self.target_weight,
            entry_method=self.entry_method,
            entry_price=self.entry_price,
            stop_loss_pct=self.stop_loss_pct,
            take_profit_pct=self.take_profit_pct,
            trailing_pct=self.trailing_pct,
            max_hold_days=self.max_hold_days,
            reason=self.thesis,
            source="manager_runtime",
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ManagerPlan:
    """Per-manager plan contract before portfolio assembly."""

    manager_id: str
    manager_name: str
    as_of_date: str
    regime: str
    positions: List[ManagerPlanPosition] = field(default_factory=list)
    cash_reserve: float = 0.0
    max_positions: int = 0
    budget_weight: float = 1.0
    confidence: float = 0.0
    reasoning: str = ""
    source_manager_id: str = ""
    source_manager_config_ref: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.manager_id = str(self.manager_id or "").strip()
        self.manager_name = str(self.manager_name or self.manager_id).strip()
        self.as_of_date = str(self.as_of_date or "").strip()
        self.regime = str(self.regime or "unknown").strip() or "unknown"
        if not self.manager_id:
            raise ValueError("manager_id is required")
        if not self.as_of_date:
            raise ValueError("as_of_date is required")
        self.cash_reserve = max(0.0, min(1.0, float(self.cash_reserve or 0.0)))
        self.max_positions = int(self.max_positions or len(self.positions or []))
        self.budget_weight = max(0.0, float(self.budget_weight or 0.0))
        self.confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))
        self.reasoning = str(self.reasoning or "").strip()
        self.source_manager_id = str(self.source_manager_id or "").strip()
        self.source_manager_config_ref = str(self.source_manager_config_ref or "").strip()
        self.positions = [
            item if isinstance(item, ManagerPlanPosition) else ManagerPlanPosition(**dict(item))
            for item in list(self.positions or [])
        ]
        self.evidence = dict(self.evidence or {})
        self.metadata = dict(self.metadata or {})

    @property
    def selected_codes(self) -> List[str]:
        return [item.code for item in self.positions]

    def to_trading_plan(self) -> TradingPlan:
        return TradingPlan(
            date=self.as_of_date,
            positions=[item.to_position_plan() for item in self.positions],
            cash_reserve=self.cash_reserve,
            max_positions=self.max_positions or len(self.positions),
            source="manager_runtime",
            reasoning=self.reasoning,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ManagerOutput:
    """Dual-channel manager output for both machine execution and LLM reasoning."""

    manager_id: str
    manager_config_ref: str
    signal_packet: SignalPacket
    agent_context: AgentContext

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manager_id": self.manager_id,
            "manager_config_ref": self.manager_config_ref,
            "signal_packet": self.signal_packet.to_dict(),
            "agent_context": self.agent_context.to_dict(),
        }


@dataclass
class ManagerAttribution:
    """Manager-level contribution summary used by governance and review layers."""

    manager_id: str
    selected_codes: List[str] = field(default_factory=list)
    gross_budget_weight: float = 0.0
    active_exposure: float = 0.0
    code_contributions: Dict[str, float] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.manager_id = str(self.manager_id or "").strip()
        if not self.manager_id:
            raise ValueError("manager_id is required")
        self.selected_codes = [
            str(item).strip()
            for item in list(self.selected_codes or [])
            if str(item).strip()
        ]
        self.gross_budget_weight = max(0.0, float(self.gross_budget_weight or 0.0))
        self.active_exposure = max(0.0, float(self.active_exposure or 0.0))
        self.code_contributions = {
            str(key): float(value)
            for key, value in dict(self.code_contributions or {}).items()
        }
        self.evidence = dict(self.evidence or {})
        self.metadata = dict(self.metadata or {})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ManagerResult:
    """Runtime result emitted for each manager after plan generation."""

    manager_id: str
    as_of_date: str
    status: str
    plan: ManagerPlan
    selected_codes: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    attribution: ManagerAttribution | None = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.manager_id = str(self.manager_id or "").strip()
        self.as_of_date = str(self.as_of_date or "").strip()
        self.status = str(self.status or "").strip() or "planned"
        if not self.manager_id:
            raise ValueError("manager_id is required")
        if not self.as_of_date:
            raise ValueError("as_of_date is required")
        if not isinstance(self.plan, ManagerPlan):
            self.plan = ManagerPlan(**dict(self.plan))
        self.selected_codes = [
            str(item).strip()
            for item in list(self.selected_codes or self.plan.selected_codes)
            if str(item).strip()
        ]
        self.metrics = dict(self.metrics or {})
        if self.attribution is not None and not isinstance(self.attribution, ManagerAttribution):
            self.attribution = ManagerAttribution(**dict(self.attribution))
        self.evidence = dict(self.evidence or {})
        self.metadata = dict(self.metadata or {})

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        if self.attribution is None:
            payload["attribution"] = {}
        return payload


# Portfolio contracts

@dataclass
class PortfolioPlanPosition:
    """Merged position after manager plans have been assembled into one portfolio."""

    code: str
    target_weight: float
    rank: int = 0
    source_managers: List[str] = field(default_factory=list)
    manager_weights: Dict[str, float] = field(default_factory=dict)
    entry_method: str = "market"
    entry_price: Optional[float] = None
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.15
    trailing_pct: Optional[float] = None
    max_hold_days: int = 30
    thesis: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.code = str(self.code or "").strip()
        self.target_weight = max(0.0, float(self.target_weight or 0.0))
        self.rank = int(self.rank or 0)
        self.source_managers = [
            str(item).strip()
            for item in list(self.source_managers or [])
            if str(item).strip()
        ]
        self.manager_weights = {
            str(key): float(value)
            for key, value in dict(self.manager_weights or {}).items()
        }
        self.entry_method = str(self.entry_method or "market").strip() or "market"
        self.entry_price = None if self.entry_price is None else float(self.entry_price)
        self.stop_loss_pct = float(self.stop_loss_pct or 0.0)
        self.take_profit_pct = float(self.take_profit_pct or 0.0)
        self.trailing_pct = None if self.trailing_pct is None else float(self.trailing_pct)
        self.max_hold_days = int(self.max_hold_days or 0)
        self.thesis = str(self.thesis or "").strip()
        self.metadata = dict(self.metadata or {})
        if not self.code:
            raise ValueError("portfolio position code is required")

    def to_position_plan(self) -> PositionPlan:
        return PositionPlan(
            code=self.code,
            priority=max(1, self.rank or 1),
            weight=self.target_weight,
            entry_method=self.entry_method,
            entry_price=self.entry_price,
            stop_loss_pct=self.stop_loss_pct,
            take_profit_pct=self.take_profit_pct,
            trailing_pct=self.trailing_pct,
            max_hold_days=self.max_hold_days,
            reason=self.thesis,
            source="portfolio_assembler",
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PortfolioPlan:
    """Portfolio-level contract emitted by the multi-manager governance layer."""

    as_of_date: str
    regime: str
    positions: List[PortfolioPlanPosition] = field(default_factory=list)
    cash_reserve: float = 0.0
    active_manager_ids: List[str] = field(default_factory=list)
    manager_weights: Dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.as_of_date = str(self.as_of_date or "").strip()
        self.regime = str(self.regime or "unknown").strip() or "unknown"
        if not self.as_of_date:
            raise ValueError("as_of_date is required")
        self.positions = [
            item if isinstance(item, PortfolioPlanPosition) else PortfolioPlanPosition(**dict(item))
            for item in list(self.positions or [])
        ]
        self.cash_reserve = max(0.0, min(1.0, float(self.cash_reserve or 0.0)))
        self.active_manager_ids = [
            str(item).strip()
            for item in list(self.active_manager_ids or [])
            if str(item).strip()
        ]
        self.manager_weights = {
            str(key): float(value)
            for key, value in dict(self.manager_weights or {}).items()
        }
        self.confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))
        self.reasoning = str(self.reasoning or "").strip()
        self.metadata = dict(self.metadata or {})

    @property
    def selected_codes(self) -> List[str]:
        return [item.code for item in self.positions]

    def to_trading_plan(self) -> TradingPlan:
        source = "portfolio_assembler"
        if str(dict(self.metadata or {}).get("assembly_mode") or "") == "dominant_manager_only":
            source = "manager_runtime"
        return TradingPlan(
            date=self.as_of_date,
            positions=[item.to_position_plan() for item in self.positions],
            cash_reserve=self.cash_reserve,
            max_positions=len(self.positions),
            source=source,
            reasoning=self.reasoning,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AllocationPlan:
    """Structured manager-allocation plan emitted by allocator layer."""

    as_of_date: str
    regime: str
    active_manager_ids: List[str] = field(default_factory=list)
    manager_budget_weights: Dict[str, float] = field(default_factory=dict)
    selected_manager_config_refs: Dict[str, str] = field(default_factory=dict)
    cash_reserve: float = 0.0
    confidence: float = 0.0
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PositionSnapshot:
    code: str
    shares: int
    entry_price: float
    current_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TradeRecordContract:
    date: str
    action: str
    code: Optional[str]
    price: float
    shares: int
    reason: str
    pnl: float = 0.0
    pnl_pct: float = 0.0
    source: str = ""
    entry_reason: str = ""
    exit_reason: str = ""
    exit_trigger: str = ""
    entry_date: str = ""
    entry_price: float = 0.0
    holding_days: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

__all__ = [
    'AgentContext',
    'resolve_agent_context_confidence',
    'ManagerSpec',
    'ManagerRunContext',
    'ManagerPlanPosition',
    'ManagerPlan',
    'ManagerOutput',
    'ManagerAttribution',
    'PortfolioPlanPosition',
    'PortfolioPlan',
    'AllocationPlan',
    'PositionSnapshot',
    'TradeRecordContract',
]
