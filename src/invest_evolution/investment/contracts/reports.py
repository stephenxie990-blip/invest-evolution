from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, cast

# Review and governance reports



@dataclass
class ManagerReviewReport:
    """Manager-specific review artifact."""

    manager_id: str
    as_of_date: str
    verdict: str
    findings: List[str] = field(default_factory=list)
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.manager_id = str(self.manager_id or "").strip()
        self.as_of_date = str(self.as_of_date or "").strip()
        self.verdict = str(self.verdict or "").strip()
        if not self.manager_id:
            raise ValueError("manager_id is required")
        if not self.as_of_date:
            raise ValueError("as_of_date is required")
        self.findings = [str(item) for item in list(self.findings or []) if str(item).strip()]
        self.strengths = [str(item) for item in list(self.strengths or []) if str(item).strip()]
        self.weaknesses = [str(item) for item in list(self.weaknesses or []) if str(item).strip()]
        self.risk_flags = [str(item) for item in list(self.risk_flags or []) if str(item).strip()]
        self.evidence = dict(self.evidence or {})
        self.metadata = dict(self.metadata or {})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AllocationReviewReport:
    """Portfolio/allocation review artifact across managers."""

    as_of_date: str
    regime: str
    verdict: str
    active_manager_ids: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)
    allocation_weights: Dict[str, float] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.as_of_date = str(self.as_of_date or "").strip()
        self.regime = str(self.regime or "unknown").strip() or "unknown"
        self.verdict = str(self.verdict or "").strip()
        if not self.as_of_date:
            raise ValueError("as_of_date is required")
        self.active_manager_ids = [
            str(item).strip()
            for item in list(self.active_manager_ids or [])
            if str(item).strip()
        ]
        self.findings = [str(item) for item in list(self.findings or []) if str(item).strip()]
        self.risk_flags = [str(item) for item in list(self.risk_flags or []) if str(item).strip()]
        self.allocation_weights = {
            str(key): float(value)
            for key, value in dict(self.allocation_weights or {}).items()
        }
        self.evidence = dict(self.evidence or {})
        self.metadata = dict(self.metadata or {})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvalReport:
    """Structured evaluation report used by review/evolution/orchestration."""

    cycle_id: int
    as_of_date: str
    return_pct: float
    total_pnl: float
    total_trades: int
    win_rate: float
    regime: str
    is_profit: bool = False
    selected_codes: List[str] = field(default_factory=list)
    benchmark_passed: bool = False
    benchmark_strict_passed: bool = False
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    excess_return: float = 0.0
    data_mode: str = "unknown"
    selection_mode: str = "unknown"
    agent_used: bool = False
    llm_used: bool = False
    review_applied: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GovernanceDecision:
    """Structured manager-governance decision emitted before manager execution."""

    as_of_date: str
    regime: str = "unknown"
    regime_confidence: float = 0.0
    decision_confidence: float = 0.0
    active_manager_ids: List[str] = field(default_factory=list)
    manager_budget_weights: Dict[str, float] = field(default_factory=dict)
    dominant_manager_id: str = ""
    cash_reserve_hint: float = 0.0
    portfolio_constraints: Dict[str, Any] = field(default_factory=dict)
    decision_source: str = "rule"
    regime_source: str = "rule"
    reasoning: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    agent_advice: Dict[str, Any] = field(default_factory=dict)
    allocation_plan: Dict[str, Any] = field(default_factory=dict)
    guardrail_checks: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    compatibility_fields: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload.pop("compatibility_fields", None)
        return payload

    @property
    def hold_reason(self) -> str:
        historical = dict(self.metadata.get("historical") or {})
        return str(
            historical.get("guardrail_hold_reason")
            or ""
        )


# Market payload contracts

@dataclass
class StockSummaryView(Mapping[str, Any]):
    code: str
    close: float | None = None
    change_5d: float | None = None
    change_20d: float | None = None
    ma_trend: str | None = None
    rsi: float | None = None
    macd: str | None = None
    bb_pos: float | None = None
    vol_ratio: float | None = None
    volatility: float | None = None
    algo_score: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | "StockSummaryView") -> "StockSummaryView":
        if isinstance(payload, StockSummaryView):
            return payload
        data = dict(payload or {})
        known = {
            "code",
            "close",
            "change_5d",
            "change_20d",
            "ma_trend",
            "rsi",
            "macd",
            "bb_pos",
            "vol_ratio",
            "volatility",
            "algo_score",
        }
        return cls(
            code=str(data.get("code") or ""),
            close=_coerce_float(data.get("close")),
            change_5d=_coerce_float(data.get("change_5d")),
            change_20d=_coerce_float(data.get("change_20d")),
            ma_trend=_coerce_text(data.get("ma_trend")),
            rsi=_coerce_float(data.get("rsi")),
            macd=_coerce_text(data.get("macd")),
            bb_pos=_coerce_float(data.get("bb_pos")),
            vol_ratio=_coerce_float(data.get("vol_ratio")),
            volatility=_coerce_float(data.get("volatility")),
            algo_score=_coerce_float(data.get("algo_score")),
            extras={key: value for key, value in data.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "close": self.close,
            "change_5d": self.change_5d,
            "change_20d": self.change_20d,
            "ma_trend": self.ma_trend,
            "rsi": self.rsi,
            "macd": self.macd,
            "bb_pos": self.bb_pos,
            "vol_ratio": self.vol_ratio,
            "volatility": self.volatility,
            "algo_score": self.algo_score,
        }
        return {key: value for key, value in {**payload, **self.extras}.items() if value is not None}

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


@dataclass
class StockSignal:
    """Structured per-stock signal emitted by a manager runtime."""

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
    manager_id: str
    manager_config_ref: str
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
            "manager_id": self.manager_id,
            "manager_config_ref": self.manager_config_ref,
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

__all__ = [
    'ManagerReviewReport',
    'AllocationReviewReport',
    'EvalReport',
    'GovernanceDecision',
    'StockSummaryView',
    'StockSignal',
    'SignalPacketContext',
    'SignalPacket',
    'StrategyAdvice',
]
