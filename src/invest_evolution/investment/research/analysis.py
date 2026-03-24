from __future__ import annotations

# Research contracts

import hashlib
import json
from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import TYPE_CHECKING, Any, Dict, Mapping

from invest_evolution.investment.runtimes.base import ManagerRuntime

if TYPE_CHECKING:
    from .case_store import ResearchCaseStore


RESEARCH_FEATURE_VERSION = "research.features.v1"
RESEARCH_CONTRACT_VERSION = "research.contracts.v2"
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
    manager_id: str
    manager_config_ref: str
    params: Dict[str, Any] = field(default_factory=dict)
    risk_policy: Dict[str, Any] = field(default_factory=dict)
    execution_policy: Dict[str, Any] = field(default_factory=dict)
    evaluation_policy: Dict[str, Any] = field(default_factory=dict)
    review_policy: Dict[str, Any] = field(default_factory=dict)
    agent_weights: Dict[str, Any] = field(default_factory=dict)
    governance_context: Dict[str, Any] = field(default_factory=dict)
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

def build_research_hypothesis(
    *,
    snapshot: ResearchSnapshot,
    policy: PolicySnapshot,
    scenario: Dict[str, Any],
    strategy_name: str,
    strategy_display_name: str,
) -> ResearchHypothesis:
    cross = dict(snapshot.cross_section_context or {})
    features = dict(snapshot.feature_snapshot or {})
    signal = dict(features.get("signal") or {})
    summary = dict(features.get("summary") or {})
    feature_metadata = dict(features.get("metadata") or {})
    factor_values = dict(features.get("factor_values") or signal.get("factor_values") or {})
    latest_close = (
        summary.get("close")
        or feature_metadata.get("latest_close")
        or factor_values.get("latest_close")
    )
    latest_close = float(latest_close or 0.0) if latest_close not in (None, "") else 0.0
    percentile = cross.get("percentile")
    percentile_f = float(percentile or 0.0) if percentile is not None else 0.0
    selected_by_policy = bool(cross.get("selected_by_policy"))
    raw_score = 50.0 + percentile_f * 40.0 + (8.0 if selected_by_policy else 0.0)
    score = round(max(0.0, min(100.0, raw_score)), 1)
    stance = "持有观察"
    if score >= 82:
        stance = "候选买入"
    elif score >= 68:
        stance = "偏强关注"
    elif score <= 35:
        stance = "减仓/回避"
    elif score <= 45:
        stance = "偏弱回避"
    stop_loss_pct = float(signal.get("stop_loss_pct") or 0.06)
    take_profit_pct = float(signal.get("take_profit_pct") or 0.08)
    entry_price = round(latest_close * 0.99, 2) if latest_close and stance in {"候选买入", "偏强关注"} else None
    stop_loss = round(latest_close * (1.0 - stop_loss_pct), 2) if latest_close else None
    take_profit = round(latest_close * (1.0 + take_profit_pct), 2) if latest_close and stance in {"候选买入", "偏强关注"} else None
    supporting_factors = []
    contradicting_factors = []
    canonical_flags = dict(feature_metadata.get("flags") or signal.get("flags") or {})
    canonical_matched = list(feature_metadata.get("matched_signals") or signal.get("matched_signals") or [])
    for label in canonical_matched:
        if str(label).strip():
            supporting_factors.append(str(label))
    for label, matched in canonical_flags.items():
        if matched:
            supporting_factors.append(str(label))
        else:
            contradicting_factors.append(str(label))
    supporting_factors.extend(str(item) for item in list(features.get("evidence") or signal.get("evidence") or [])[:3])
    if factor_values.get("rsi") is not None and float(factor_values.get("rsi") or 0.0) > 75.0:
        contradicting_factors.append("RSI过热")
    if cross.get("threshold_gap") is not None and float(cross.get("threshold_gap") or 0.0) < 0:
        contradicting_factors.append("未达到当前策略入选阈值")
    evaluation_protocol = {
        "clock": ["T+5", "T+10", "T+20", "T+60"],
        "label_set": ["hit", "miss", "invalidated", "timeout", "not_triggered"],
        "benchmark_code": "sh.000300",
        "strategy_lens": strategy_name,
    }
    horizon20 = dict((scenario.get("horizons") or {}).get("T+20") or {})
    expected_interval = dict(horizon20.get("interval") or {})
    probability = float(horizon20.get("positive_return_probability") or 0.5)
    confidence = round(max(0.35, min(0.95, 0.4 + probability * 0.5 + (0.08 if selected_by_policy else 0.0))), 4)
    hypothesis_hash = stable_hash(
        {
            "snapshot_id": snapshot.snapshot_id,
            "policy_id": policy.policy_id,
            "stance": stance,
            "entry": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }
    )
    return ResearchHypothesis(
        hypothesis_id=f"hypothesis_{hypothesis_hash[:16]}",
        snapshot_id=snapshot.snapshot_id,
        policy_id=policy.policy_id,
        stance=stance,
        score=score,
        rank=cross.get("rank"),
        percentile=percentile_f if percentile is not None else None,
        selected_by_policy=selected_by_policy,
        entry_rule={
            "kind": "limit_pullback" if entry_price is not None else "observe_only",
            "price": entry_price,
            "source": strategy_display_name,
        },
        invalidation_rule={
            "kind": "stop_loss",
            "price": stop_loss,
            "source": policy.manager_id,
        },
        de_risk_rule={
            "kind": "take_profit" if take_profit is not None else "reassess",
            "price": take_profit,
            "source": policy.manager_id,
        },
        supporting_factors=list(dict.fromkeys(supporting_factors))[:8],
        contradicting_factors=list(dict.fromkeys(contradicting_factors))[:8],
        scenario_distribution=dict(scenario or {}),
        expected_return_interval=expected_interval,
        confidence=confidence,
        evaluation_protocol=evaluation_protocol,
        metadata={
            "strategy_name": strategy_name,
            "strategy_display_name": strategy_display_name,
            "latest_close": latest_close,
        },
    )


class ResearchScenarioEngine:
    def __init__(self, case_store: ResearchCaseStore):
        self.case_store = case_store

    def estimate(self, *, snapshot: ResearchSnapshot, policy: PolicySnapshot, stance: str) -> Dict[str, Any]:
        regime = str(snapshot.market_context.get("regime") or "unknown")
        similar = list(
            self.case_store.iter_similar_attributions(
                manager_id=policy.manager_id,
                regime=regime,
                stance=stance,
            )
        )
        if not similar:
            return self._heuristic(snapshot=snapshot, policy=policy, stance=stance)
        horizons: Dict[str, Any] = {}
        for horizon in DEFAULT_HORIZONS:
            key = f"T+{horizon}"
            returns = []
            invalidated = 0
            for item in similar:
                result = dict((item.get("attribution") or {}).get("horizon_results", {}).get(key) or {})
                if not result:
                    continue
                if result.get("return_pct") is not None:
                    returns.append(float(result.get("return_pct") or 0.0))
                if result.get("label") == "invalidated":
                    invalidated += 1
            if returns:
                ordered = sorted(returns)
                horizons[key] = {
                    "sample_count": len(returns),
                    "positive_return_probability": round(sum(1 for item in returns if item > 0) / len(returns), 4),
                    "interval": {
                        "p25": round(ordered[max(0, int((len(ordered) - 1) * 0.25))], 4),
                        "p50": round(ordered[max(0, int((len(ordered) - 1) * 0.50))], 4),
                        "p75": round(ordered[max(0, int((len(ordered) - 1) * 0.75))], 4),
                    },
                    "mean_return": round(mean(returns), 4),
                    "invalidation_probability": round(invalidated / len(returns), 4),
                }
        base = horizons.get("T+20") or next(iter(horizons.values()), {})
        return {
            "engine": "case_similarity_v1",
            "sample_count": len(similar),
            "matched_case_ids": [str(item["case"].get("research_case_id") or "") for item in similar[:20]],
            "horizons": horizons,
            "bull_case": {"description": "相似样本上沿情景", "return_pct": dict(base.get("interval") or {}).get("p75")},
            "base_case": {"description": "相似样本中位情景", "return_pct": dict(base.get("interval") or {}).get("p50")},
            "bear_case": {"description": "相似样本下沿情景", "return_pct": dict(base.get("interval") or {}).get("p25")},
        }

    def _heuristic(self, *, snapshot: ResearchSnapshot, policy: PolicySnapshot, stance: str) -> Dict[str, Any]:
        percentile = float(snapshot.cross_section_context.get("percentile") or 0.5)
        selected = bool(snapshot.cross_section_context.get("selected_by_policy"))
        stance_bias = {
            "候选买入": 0.10,
            "偏强关注": 0.05,
            "持有观察": 0.0,
            "偏弱回避": -0.05,
            "减仓/回避": -0.10,
        }.get(str(stance), 0.0)
        base_probability = max(0.15, min(0.85, 0.45 + (percentile - 0.5) * 0.5 + (0.08 if selected else 0.0) + stance_bias))
        horizons: Dict[str, Any] = {}
        for horizon in DEFAULT_HORIZONS:
            scale = 0.6 if horizon <= 10 else 1.0 if horizon <= 20 else 1.2
            mid = round((base_probability - 0.5) * 24.0 * scale, 4)
            spread = round(4.0 * scale, 4)
            horizons[f"T+{horizon}"] = {
                "sample_count": 0,
                "positive_return_probability": round(base_probability, 4),
                "interval": {"p25": round(mid - spread, 4), "p50": mid, "p75": round(mid + spread, 4)},
                "mean_return": mid,
                "invalidation_probability": round(max(0.05, min(0.75, 1.0 - base_probability)), 4),
            }
        base = horizons["T+20"]
        return {
            "engine": "heuristic_bootstrap_v1",
            "sample_count": 0,
            "matched_case_ids": [],
            "horizons": horizons,
            "bull_case": {"description": f"{policy.manager_id} 强势延续", "return_pct": base["interval"]["p75"]},
            "base_case": {"description": f"{policy.manager_id} 中性演化", "return_pct": base["interval"]["p50"]},
            "bear_case": {"description": f"{policy.manager_id} 失效回撤", "return_pct": base["interval"]["p25"]},
        }


DEFAULT_DATA_WINDOW = {
    "lookback_days": 120,
    "simulation_days": 30,
    "universe_definition": "max_stocks=50|min_history_days=60",
}


def _runtime_manager_id(manager_runtime: ManagerRuntime | None, fallback: str = "unknown") -> str:
    return str(getattr(manager_runtime, "manager_id", fallback) or fallback or "unknown")


def _runtime_manager_config_ref(manager_runtime: ManagerRuntime | None, fallback: str = "unknown") -> str:
    config = getattr(manager_runtime, "config", None)
    return str(getattr(config, "name", "") or fallback or "unknown")


def build_policy_signature(
    *,
    manager_runtime: ManagerRuntime | None,
    manager_id: str = "",
    governance_context: Dict[str, Any] | None = None,
    data_window: Dict[str, Any] | None = None,
    feature_version: str = RESEARCH_FEATURE_VERSION,
    code_contract_version: str = RESEARCH_CONTRACT_VERSION,
) -> Dict[str, Any]:
    resolved_governance_context = dict(governance_context or {})
    resolved_manager_id = str(
        resolved_governance_context.get("dominant_manager_id")
        or manager_id
        or _runtime_manager_id(manager_runtime, fallback="unknown")
        or "unknown"
    )
    resolved_manager_config_ref = str(
        dict(resolved_governance_context.get("allocation_plan") or {})
        .get("selected_manager_config_refs", {})
        .get(resolved_manager_id)
        or dict(resolved_governance_context.get("metadata") or {}).get("dominant_manager_config")
        or _runtime_manager_config_ref(manager_runtime, fallback="unknown")
        or "unknown"
    )
    active_runtime = manager_runtime
    return {
        "manager_id": resolved_manager_id,
        "manager_config_ref": resolved_manager_config_ref,
        "params": dict(active_runtime.effective_params() or {}) if active_runtime is not None else {},
        "risk_policy": dict(active_runtime.config_section("risk_policy", {}) or {}) if active_runtime is not None else {},
        "execution_policy": dict(active_runtime.config_section("execution", {}) or {}) if active_runtime is not None else {},
        "evaluation_policy": dict(active_runtime.config_section("evaluation_policy", {}) or {}) if active_runtime is not None else {},
        "review_policy": dict(active_runtime.config_section("review_policy", {}) or {}) if active_runtime is not None else {},
        "agent_weights": dict(active_runtime.config_section("agent_weights", {}) or {}) if active_runtime is not None else {},
        "governance_context": resolved_governance_context,
        "data_window": dict(DEFAULT_DATA_WINDOW | dict(data_window or {})),
        "feature_version": str(feature_version),
        "code_contract_version": str(code_contract_version),
    }


def resolve_policy_snapshot(
    *,
    manager_runtime: ManagerRuntime | None,
    manager_id: str = "",
    governance_context: Dict[str, Any] | None = None,
    data_window: Dict[str, Any] | None = None,
    metadata: Dict[str, Any] | None = None,
) -> PolicySnapshot:
    signature = build_policy_signature(
        manager_runtime=manager_runtime,
        manager_id=manager_id,
        governance_context=governance_context,
        data_window=data_window,
    )
    version_hash = stable_hash(signature)
    return PolicySnapshot(
        policy_id=f"policy_{version_hash[:16]}",
        manager_id=str(signature["manager_id"]),
        manager_config_ref=str(signature["manager_config_ref"]),
        params=dict(signature["params"]),
        risk_policy=dict(signature["risk_policy"]),
        execution_policy=dict(signature["execution_policy"]),
        evaluation_policy=dict(signature["evaluation_policy"]),
        review_policy=dict(signature["review_policy"]),
        agent_weights=dict(signature["agent_weights"]),
        governance_context=dict(signature["governance_context"]),
        feature_version=str(signature["feature_version"]),
        data_window=dict(signature["data_window"]),
        code_contract_version=str(signature["code_contract_version"]),
        version_hash=version_hash,
        signature=signature,
        metadata=dict(metadata or {}),
    )

__all__ = [name for name in globals() if not name.startswith('_')]
