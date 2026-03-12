from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Mapping


RESEARCH_FEATURE_VERSION = "research.features.v1"
RESEARCH_CONTRACT_VERSION = "research.contracts.v1"
DEFAULT_HORIZONS = (5, 10, 20, 60)


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(val) for key, val in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    return value


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(_canonicalize(dict(payload)), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass
class ResearchSnapshot:
    snapshot_id: str
    as_of_date: str
    scope: str
    security: Dict[str, Any] = field(default_factory=dict)
    universe: Dict[str, Any] = field(default_factory=dict)
    market_context: Dict[str, Any] = field(default_factory=dict)
    cross_section_context: Dict[str, Any] = field(default_factory=dict)
    feature_snapshot: Dict[str, Any] = field(default_factory=dict)
    data_lineage: Dict[str, Any] = field(default_factory=dict)
    feature_version: str = RESEARCH_FEATURE_VERSION
    readiness: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PolicySnapshot:
    policy_id: str
    model_name: str
    config_name: str
    params: Dict[str, Any] = field(default_factory=dict)
    risk_policy: Dict[str, Any] = field(default_factory=dict)
    execution_policy: Dict[str, Any] = field(default_factory=dict)
    evaluation_policy: Dict[str, Any] = field(default_factory=dict)
    review_policy: Dict[str, Any] = field(default_factory=dict)
    agent_weights: Dict[str, Any] = field(default_factory=dict)
    routing_context: Dict[str, Any] = field(default_factory=dict)
    feature_version: str = RESEARCH_FEATURE_VERSION
    data_window: Dict[str, Any] = field(default_factory=dict)
    code_contract_version: str = RESEARCH_CONTRACT_VERSION
    version_hash: str = ""
    signature: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchHypothesis:
    hypothesis_id: str
    snapshot_id: str
    policy_id: str
    stance: str
    score: float
    rank: int | None = None
    percentile: float | None = None
    selected_by_policy: bool = False
    entry_rule: Dict[str, Any] = field(default_factory=dict)
    invalidation_rule: Dict[str, Any] = field(default_factory=dict)
    de_risk_rule: Dict[str, Any] = field(default_factory=dict)
    supporting_factors: list[str] = field(default_factory=list)
    contradicting_factors: list[str] = field(default_factory=list)
    scenario_distribution: Dict[str, Any] = field(default_factory=dict)
    expected_return_interval: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    evaluation_protocol: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OutcomeAttribution:
    attribution_id: str
    hypothesis_id: str
    thesis_result: str
    horizon_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    factor_attribution: Dict[str, Any] = field(default_factory=dict)
    timing_attribution: Dict[str, Any] = field(default_factory=dict)
    risk_attribution: Dict[str, Any] = field(default_factory=dict)
    execution_attribution: Dict[str, Any] = field(default_factory=dict)
    calibration_metrics: Dict[str, Any] = field(default_factory=dict)
    policy_update_candidates: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
