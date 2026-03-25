from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, cast

import pandas as pd
import yaml

from invest_evolution.agent_runtime.runtime import enforce_path_within_root
from invest_evolution.config import PROJECT_ROOT
from invest_evolution.investment.contracts import AllocationPlan, GovernanceDecision
from invest_evolution.investment.foundation.compute import compute_market_stats
from invest_evolution.investment.managers import resolve_manager_config_ref
from invest_evolution.investment.runtimes import list_manager_runtime_ids
from invest_evolution.investment.governance.regime_confidence import (
    build_regime_confidence_map,
)
from invest_evolution.investment.shared.policy import (
    evaluate_governance_quality_gate,
    get_manager_style_profile,
    infer_deployment_stage,
    manager_regime_compatibility,
    normalize_freeze_gate_policy,
    normalize_promotion_gate_policy,
    resolve_governance_matrix,
)

logger = logging.getLogger(__name__)

# Governance allocation and leaderboard


DEFAULT_REGIME_PRIORS: Dict[str, Dict[str, float]] = {
    "bull": {
        "momentum": 0.45,
        "value_quality": 0.25,
        "defensive_low_vol": 0.15,
        "mean_reversion": 0.15,
    },
    "oscillation": {
        "defensive_low_vol": 0.38,
        "value_quality": 0.34,
        "mean_reversion": 0.18,
        "momentum": 0.10,
    },
    "bear": {
        "defensive_low_vol": 0.50,
        "value_quality": 0.30,
        "mean_reversion": 0.15,
        "momentum": 0.05,
    },
    "unknown": {
        "momentum": 0.25,
        "mean_reversion": 0.25,
        "value_quality": 0.25,
        "defensive_low_vol": 0.25,
    },
}

REGIME_WEIGHT_CAPS: Dict[str, Dict[str, float]] = {
    "bull": {"momentum": 0.70},
    "oscillation": {"momentum": 0.20, "mean_reversion": 0.20},
    "bear": {"momentum": 0.20, "defensive_low_vol": 0.60},
}

MIN_STYLE_ELIGIBILITY = 0.20

DEFAULT_CASH_RESERVE = {
    "bull": 0.10,
    "oscillation": 0.20,
    "bear": 0.30,
    "unknown": 0.20,
}

KNOWN_MANAGER_IDS = frozenset(str(item).strip() for item in list_manager_runtime_ids())


@dataclass
class ManagerScore:
    manager_id: str
    manager_config_ref: str
    score: float
    avg_return_pct: float
    avg_sharpe_ratio: float
    avg_max_drawdown: float
    benchmark_pass_rate: float
    avg_strategy_score: float = 0.0
    rank: int = 0
    regime_score: float = 0.0
    regime_compatibility: float = 0.55
    regime_cycles: int = 0
    regime_source: str = "overall"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _entry_manager_id(entry: Dict[str, Any]) -> str:
    return str(entry.get("manager_id") or "unknown").strip() or "unknown"


def _entry_manager_config_ref(entry: Dict[str, Any]) -> str:
    return str(entry.get("manager_config_ref") or "").strip()


def _filter_known_manager_ids(manager_ids: Iterable[Any]) -> List[str]:
    normalized_ids: List[str] = []
    for manager_id in manager_ids:
        normalized_manager_id = str(manager_id or "").strip()
        if (
            normalized_manager_id
            and normalized_manager_id in KNOWN_MANAGER_IDS
            and normalized_manager_id not in normalized_ids
        ):
            normalized_ids.append(normalized_manager_id)
    return normalized_ids


def _write_json_atomic(target: Path, payload: Dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(serialized)
            handle.flush()
            temp_path = Path(handle.name)
        os.replace(temp_path, target)
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def load_leaderboard(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class ModelAllocator:
    """Rule + leaderboard driven manager allocator. LLM is optional explanation layer only."""

    def __init__(
        self,
        priors: Optional[Dict[str, Dict[str, float]]] = None,
        cash_policy: Optional[Dict[str, float]] = None,
    ):
        self.priors = priors or DEFAULT_REGIME_PRIORS
        self.cash_policy = cash_policy or DEFAULT_CASH_RESERVE
        self.weight_caps = REGIME_WEIGHT_CAPS

    def allocate(
        self,
        regime: str,
        leaderboard: Dict[str, Any],
        *,
        as_of_date: Optional[str] = None,
        top_n: int = 3,
    ) -> AllocationPlan:
        normalized_regime = regime if regime in self.priors else "unknown"
        candidates, used_provisional = self._rank_candidates(
            normalized_regime, leaderboard
        )
        weights = self._blend_weights(normalized_regime, candidates, top_n=top_n)
        selected_manager_config_refs = {
            item.manager_id: item.manager_config_ref
            for item in candidates
            if item.manager_id in weights
        }
        active_manager_ids = [
            name
            for name, weight in sorted(
                weights.items(), key=lambda pair: pair[1], reverse=True
            )
            if weight > 0
        ]
        confidence = self._confidence(candidates)
        if used_provisional:
            confidence = min(confidence, 0.55)
        reasoning = self._build_reasoning(
            normalized_regime,
            active_manager_ids,
            weights,
            candidates,
            used_provisional=used_provisional,
            failed_entries=list(leaderboard.get("entries") or []),
        )
        return AllocationPlan(
            as_of_date=as_of_date or datetime.now().strftime("%Y%m%d"),
            regime=normalized_regime,
            active_manager_ids=active_manager_ids,
            manager_budget_weights=weights,
            selected_manager_config_refs=selected_manager_config_refs,
            cash_reserve=self.cash_policy.get(normalized_regime, 0.20),
            confidence=confidence,
            reasoning=reasoning,
            metadata={
                "top_candidates": [item.to_dict() for item in candidates[:top_n]],
                "leaderboard_generated_at": leaderboard.get("generated_at"),
                "used_provisional_leaderboard": used_provisional,
                "qualified_candidate_count": len(candidates),
                "failed_quality_entries": [
                    {
                        "manager_id": _entry_manager_id(item),
                        "ineligible_reason": str(item.get("ineligible_reason") or ""),
                        "deployment_stage": str(item.get("deployment_stage") or ""),
                        "failed_regime_names": list(
                            item.get("failed_regime_names") or []
                        ),
                        "regime_hard_fail": dict(item.get("regime_hard_fail") or {}),
                    }
                    for item in list(leaderboard.get("entries") or [])
                    if not bool(item.get("eligible_for_governance", False))
                ][:5],
            },
        )

    @staticmethod
    def _eligible_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            entry
            for entry in entries
            if bool(entry.get("eligible_for_governance", True))
        ]

    def _rank_candidates(
        self, regime: str, leaderboard: Dict[str, Any]
    ) -> tuple[List[ManagerScore], bool]:
        regime_board = list(
            (leaderboard.get("regime_leaderboards") or {}).get(regime, [])
        )
        entries = list(leaderboard.get("entries") or [])
        filtered_entries = self._eligible_entries(entries)
        used_provisional = False
        if not filtered_entries:
            return [], used_provisional
        if regime_board:
            regime_map = {
                _entry_manager_id(item): item
                for item in regime_board
                if bool(item.get("eligible_for_governance", True)) or used_provisional
            }
            chosen = []
            for entry in filtered_entries:
                manager_id = _entry_manager_id(entry)
                if manager_id not in regime_map:
                    continue
                compatibility = float(
                    regime_map[manager_id].get("compatibility")
                    or entry.get("style_profile", {}).get(regime)
                    or manager_regime_compatibility(manager_id, regime)
                )
                chosen.append(
                    ManagerScore(
                        manager_id=manager_id,
                        manager_config_ref=_entry_manager_config_ref(entry),
                        score=float(entry.get("score", 0.0) or 0.0),
                        avg_return_pct=float(entry.get("avg_return_pct", 0.0) or 0.0),
                        avg_sharpe_ratio=float(
                            entry.get("avg_sharpe_ratio", 0.0) or 0.0
                        ),
                        avg_max_drawdown=float(
                            entry.get("avg_max_drawdown", 0.0) or 0.0
                        ),
                        benchmark_pass_rate=float(
                            entry.get("benchmark_pass_rate", 0.0) or 0.0
                        ),
                        avg_strategy_score=float(
                            entry.get("avg_strategy_score", 0.0) or 0.0
                        ),
                        rank=int(regime_map[manager_id].get("rank", 0) or 0),
                        regime_score=float(
                            regime_map[manager_id].get(
                                "regime_score", entry.get("score", 0.0)
                            )
                            or 0.0
                        ),
                        regime_compatibility=compatibility,
                        regime_cycles=int(regime_map[manager_id].get("cycles", 0) or 0),
                        regime_source=str(
                            regime_map[manager_id].get("source") or "leaderboard"
                        ),
                    )
                )
            if chosen:
                return self._dedupe_by_manager(
                    self._apply_style_filter(regime, chosen)
                ), used_provisional
        fallback = [
            ManagerScore(
                manager_id=_entry_manager_id(entry),
                manager_config_ref=_entry_manager_config_ref(entry),
                score=float(entry.get("score", 0.0) or 0.0),
                avg_return_pct=float(entry.get("avg_return_pct", 0.0) or 0.0),
                avg_sharpe_ratio=float(entry.get("avg_sharpe_ratio", 0.0) or 0.0),
                avg_max_drawdown=float(entry.get("avg_max_drawdown", 0.0) or 0.0),
                benchmark_pass_rate=float(entry.get("benchmark_pass_rate", 0.0) or 0.0),
                avg_strategy_score=float(entry.get("avg_strategy_score", 0.0) or 0.0),
                rank=int(entry.get("rank", 0) or 0),
                regime_score=float(entry.get("score", 0.0) or 0.0)
                * manager_regime_compatibility(_entry_manager_id(entry), regime),
                regime_compatibility=manager_regime_compatibility(
                    _entry_manager_id(entry), regime
                ),
                regime_cycles=int(
                    dict(entry.get("regime_performance") or {})
                    .get(regime, {})
                    .get("cycles", 0)
                    or 0
                ),
                regime_source="fallback",
            )
            for entry in filtered_entries
        ]
        return self._dedupe_by_manager(
            self._apply_style_filter(regime, fallback)
        ), used_provisional

    def _apply_style_filter(
        self, regime: str, items: List[ManagerScore]
    ) -> List[ManagerScore]:
        if regime == "unknown":
            return items
        compatible = [
            item
            for item in items
            if float(item.regime_compatibility or 0.0) >= MIN_STYLE_ELIGIBILITY
        ]
        return compatible or items

    def _dedupe_by_manager(self, items: List[ManagerScore]) -> List[ManagerScore]:
        best: Dict[str, ManagerScore] = {}
        for item in items:
            current = best.get(item.manager_id)
            if current is None or item.score > current.score:
                best[item.manager_id] = item
        return sorted(
            best.values(),
            key=lambda item: (
                item.regime_score,
                item.regime_compatibility,
                item.score,
                item.avg_strategy_score,
                item.avg_return_pct,
                item.avg_sharpe_ratio,
            ),
            reverse=True,
        )

    def _blend_weights(
        self, regime: str, candidates: List[ManagerScore], *, top_n: int
    ) -> Dict[str, float]:
        if not candidates:
            return {}
        selected = candidates[: max(1, top_n)]
        if not selected:
            return {}
        regime_priors = dict(self.priors.get(regime, self.priors["unknown"]))
        selected_ids = [item.manager_id for item in selected]
        priors = {
            manager_id: float(regime_priors.get(manager_id, 0.0) or 0.0)
            for manager_id in selected_ids
        }
        if sum(priors.values()) <= 0:
            uniform_prior = 1.0 / max(len(selected_ids), 1)
            priors = {manager_id: uniform_prior for manager_id in selected_ids}
        bonus_scores: Dict[str, float] = {
            manager_id: 0.0 for manager_id in selected_ids
        }
        if selected:
            max_score = max(max(item.regime_score, 0.0) for item in selected) or 1.0
            for item in selected:
                normalized = max(0.0, item.regime_score) / max_score
                bonus_scores[item.manager_id] = (
                    0.40 * normalized
                    + 0.20 * item.benchmark_pass_rate
                    + 0.20 * max(0.0, min(1.0, item.avg_strategy_score))
                    + 0.20 * float(item.regime_compatibility or 0.0)
                )
        combined: Dict[str, float] = {}
        for item in selected:
            manager_id = item.manager_id
            prior = float(priors.get(manager_id, 0.0) or 0.0)
            compatibility = float(
                item.regime_compatibility
                or manager_regime_compatibility(manager_id, regime)
            )
            combined[manager_id] = max(
                0.0,
                (prior * 0.65 + bonus_scores.get(manager_id, 0.0) * 0.35)
                * max(0.25, compatibility),
            )
        total = sum(combined.values()) or 1.0
        normalized = {
            name: round(value / total, 4)
            for name, value in combined.items()
            if value > 0
        }
        normalized = self._apply_regime_caps(regime, normalized)
        normalized = {
            name: weight for name, weight in normalized.items() if weight >= 0.01
        }
        total = sum(normalized.values()) or 1.0
        return {
            name: round(weight / total, 4)
            for name, weight in sorted(
                normalized.items(), key=lambda pair: pair[1], reverse=True
            )
        }

    def _apply_regime_caps(
        self, regime: str, weights: Dict[str, float]
    ) -> Dict[str, float]:
        capped = dict(weights)
        caps = dict(self.weight_caps.get(regime, {}))
        if not caps:
            return capped
        for manager_id, cap in caps.items():
            if manager_id not in capped:
                continue
            capped[manager_id] = min(capped[manager_id], cap)
        total = sum(capped.values()) or 1.0
        remainder = 1.0 - total
        if remainder > 0:
            eligible = [name for name in capped if name not in caps]
            if not eligible:
                eligible = list(capped)
            share = remainder / max(len(eligible), 1)
            for name in eligible:
                capped[name] = capped.get(name, 0.0) + share
        total = sum(capped.values()) or 1.0
        return {name: value / total for name, value in capped.items()}

    def _confidence(self, candidates: List[ManagerScore]) -> float:
        if not candidates:
            return 0.35
        top = candidates[0].regime_score or candidates[0].score
        second = candidates[1].regime_score if len(candidates) > 1 else top * 0.8
        gap = max(0.0, top - second)
        compatibility_bonus = float(candidates[0].regime_compatibility or 0.0) * 0.08
        return round(max(0.35, min(0.92, 0.52 + gap / 50.0 + compatibility_bonus)), 4)

    def _build_reasoning(
        self,
        regime: str,
        active_manager_ids: List[str],
        weights: Dict[str, float],
        candidates: List[ManagerScore],
        *,
        used_provisional: bool,
        failed_entries: List[Dict[str, Any]],
    ) -> str:
        if not active_manager_ids:
            failed_brief = next(
                (
                    item
                    for item in failed_entries
                    if str(item.get("ineligible_reason") or "").strip()
                ),
                {},
            )
            reason = str(
                failed_brief.get("ineligible_reason")
                or "no_qualified_governance_candidates"
            )
            return f"当前 regime={regime}，榜单中没有通过质量门的正式候选，保持当前 active，不启用 provisional 候选；首个阻断原因为 {reason}。"
        leading = active_manager_ids[0]
        lead_weight = weights.get(leading, 0.0)
        top_score = next(
            (item for item in candidates if item.manager_id == leading), None
        )
        if top_score is None:
            return f"当前 regime={regime}，按先验规则优先启用 {leading}。"
        provisional_note = " 当前为 provisional 分配。" if used_provisional else ""
        return (
            f"当前 regime={regime}，优先分配给 {leading}（权重 {lead_weight:.0%}），"
            f"因为其当前市场风格兼容度 {top_score.regime_compatibility:.0%}、"
            f"regime 分数 {top_score.regime_score:.2f}、策略评分 {top_score.avg_strategy_score:.2f}、平均收益 {top_score.avg_return_pct:+.2f}% 、"
            f"Sharpe {top_score.avg_sharpe_ratio:.2f} 相对更优，且已通过正式治理质量门；其余经理按风格兼容性、先验与榜单表现做补充分配。{provisional_note}"
        )


def build_allocation_plan(
    regime: str,
    leaderboard_path: str | Path,
    *,
    as_of_date: Optional[str] = None,
    top_n: int = 3,
) -> AllocationPlan:
    allocator = ModelAllocator()
    leaderboard = load_leaderboard(leaderboard_path)
    return allocator.allocate(regime, leaderboard, as_of_date=as_of_date, top_n=top_n)


# Governance runtime execution


DEFAULT_GOVERNANCE_POLICY: Dict[str, Any] = {
    "bull_avg_change_20d": 3.0,
    "bull_above_ma20_ratio": 0.55,
    "bear_avg_change_20d": -3.0,
    "bear_above_ma20_ratio": 0.45,
    "high_volatility_threshold": 0.028,
    "weak_breadth_threshold": 0.42,
    "strong_breadth_threshold": 0.58,
    "index_bull_change_20d": 2.0,
    "index_bear_change_20d": -2.0,
    "default_regime": "oscillation",
    "cooldown_exception_min_candidate_weight_gap": 0.10,
    "cooldown_exception_min_confidence": 0.72,
    "cooldown_exception_min_benchmark_gap": 0.15,
    "cooldown_exception_min_return_gap": 0.75,
    "cooldown_exception_current_benchmark_pass_rate_max": 0.45,
    "cooldown_exception_current_avg_return_pct_max": 0.0,
    "min_regime_style_compatibility": 0.40,
}

DEFAULT_GOVERNANCE_ALLOWED_MANAGER_IDS = list_manager_runtime_ids()


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    return cast(pd.Series, pd.to_numeric(frame[column], errors="coerce"))


@dataclass
class MarketObservation:
    as_of_date: str
    stats: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "stats": dict(self.stats),
            "evidence": dict(self.evidence),
        }


class MarketObservationService:
    def __init__(self, governance_policy: Optional[Dict[str, Any]] = None):
        self.governance_policy = {
            **dict(DEFAULT_GOVERNANCE_POLICY),
            **dict(governance_policy or {}),
        }

    def observe(
        self,
        stock_data: Dict[str, pd.DataFrame],
        cutoff_date: str,
        *,
        data_manager: Any = None,
    ) -> MarketObservation:
        stats = compute_market_stats(
            stock_data, cutoff_date, regime_policy=self.governance_policy
        )
        evidence: Dict[str, Any] = {
            "stock_universe_size": len(stock_data or {}),
            "valid_stocks": int(stats.get("valid_stocks", 0) or 0),
        }
        index_frame = pd.DataFrame()
        if data_manager is not None and hasattr(data_manager, "get_market_index_frame"):
            try:
                index_frame = data_manager.get_market_index_frame(
                    index_code="sh.000300"
                )
            except Exception:
                logger.warning(
                    "Failed to load governance market index frame: cutoff_date=%s",
                    cutoff_date,
                    exc_info=True,
                )
                index_frame = pd.DataFrame()
        if not index_frame.empty:
            enriched = self._summarize_index_frame(index_frame, cutoff_date)
            stats.update(enriched)
            evidence["index_metrics"] = enriched
        stats["observation_source"] = "market_observer"
        return MarketObservation(as_of_date=cutoff_date, stats=stats, evidence=evidence)

    @staticmethod
    def _summarize_index_frame(
        index_frame: pd.DataFrame, cutoff_date: str
    ) -> Dict[str, Any]:
        frame = index_frame.copy()
        if "trade_date" not in frame.columns or "close" not in frame.columns:
            return {}
        frame["trade_date"] = frame["trade_date"].astype(str)
        frame = cast(
            pd.DataFrame,
            frame.loc[frame["trade_date"] <= str(cutoff_date)].copy(),
        )
        frame = cast(
            pd.DataFrame,
            frame.set_index("trade_date").sort_index().reset_index(),
        )
        if len(frame) < 25:
            return {}
        close = _numeric_series(frame, "close").dropna()
        if len(close) < 25:
            return {}
        latest = float(close.iloc[-1])
        prev20 = float(close.iloc[-21]) if len(close) >= 21 else float(close.iloc[0])
        ma20 = float(close.iloc[-20:].mean())
        index_change_20d = (
            0.0 if prev20 == 0 else round((latest / prev20 - 1.0) * 100.0, 4)
        )
        return {
            "index_change_20d": index_change_20d,
            "index_above_ma20": latest > ma20,
            "index_ma20_gap_pct": round((latest / ma20 - 1.0) * 100.0, 4)
            if ma20
            else 0.0,
        }


class RegimeClassifier:
    def __init__(self, governance_policy: Optional[Dict[str, Any]] = None):
        self.governance_policy = {
            **dict(DEFAULT_GOVERNANCE_POLICY),
            **dict(governance_policy or {}),
        }

    def classify(
        self, observation: MarketObservation, *, agent: Any = None, mode: str = "rule"
    ) -> Dict[str, Any]:
        rule_result = self._rule_based(observation.stats)
        if mode not in {"hybrid", "agent"} or agent is None:
            return {**rule_result, "rule_result": rule_result, "agent_result": {}}
        agent_result = agent.analyze(dict(observation.stats or {}))
        final = dict(rule_result)
        agent_confidence = float(agent_result.get("confidence", 0.0) or 0.0)
        if mode == "agent" and agent_result.get("regime"):
            final.update(
                {
                    "regime": str(agent_result.get("regime") or rule_result["regime"]),
                    "confidence": max(rule_result["confidence"], agent_confidence),
                    "reasoning": str(
                        agent_result.get("reasoning") or rule_result["reasoning"]
                    ),
                    "suggested_exposure": float(
                        agent_result.get(
                            "suggested_exposure", rule_result["suggested_exposure"]
                        )
                    ),
                    "source": str(agent_result.get("source") or "agent"),
                }
            )
        elif mode == "hybrid" and agent_result.get("regime") == rule_result.get(
            "regime"
        ):
            final.update(
                {
                    "confidence": round(
                        min(
                            0.95,
                            max(rule_result["confidence"], agent_confidence) + 0.05,
                        ),
                        4,
                    ),
                    "reasoning": f"{rule_result['reasoning']} Agent 校验一致。",
                    "source": "hybrid_consensus",
                }
            )
        return {**final, "rule_result": rule_result, "agent_result": agent_result}

    def _rule_based(self, stats: Dict[str, Any]) -> Dict[str, Any]:
        avg_change_20d = float(stats.get("avg_change_20d", 0.0) or 0.0)
        above_ma20_ratio = float(stats.get("above_ma20_ratio", 0.5) or 0.5)
        avg_volatility = float(stats.get("avg_volatility", 0.0) or 0.0)
        market_breadth = float(stats.get("market_breadth", 0.5) or 0.5)
        index_change_20d = float(stats.get("index_change_20d", 0.0) or 0.0)
        bull = (
            avg_change_20d >= float(self.governance_policy["bull_avg_change_20d"])
            and above_ma20_ratio
            >= float(self.governance_policy["bull_above_ma20_ratio"])
            and market_breadth
            >= float(self.governance_policy["strong_breadth_threshold"])
        )
        bear = (
            avg_change_20d <= float(self.governance_policy["bear_avg_change_20d"])
            and above_ma20_ratio
            <= float(self.governance_policy["bear_above_ma20_ratio"])
            and market_breadth
            <= float(self.governance_policy["weak_breadth_threshold"])
        )
        regime = str(self.governance_policy.get("default_regime", "oscillation"))
        confidence = 0.58
        reasoning = "市场特征分布接近震荡区间，维持均衡模型选择。"
        if bull or index_change_20d >= float(
            self.governance_policy["index_bull_change_20d"]
        ):
            regime = "bull"
            confidence = 0.72 + min(0.18, max(0.0, above_ma20_ratio - 0.55))
            reasoning = "市场涨幅、广度和均线占优，趋势延续特征更明显。"
        elif bear or index_change_20d <= float(
            self.governance_policy["index_bear_change_20d"]
        ):
            regime = "bear"
            confidence = 0.74 + min(0.16, max(0.0, 0.45 - above_ma20_ratio))
            reasoning = "市场回撤、广度与均线结构偏弱，防御模型更合适。"
        elif avg_volatility >= float(
            self.governance_policy["high_volatility_threshold"]
        ):
            regime = "bear"
            confidence = 0.63
            reasoning = "波动显著抬升，优先采用防御式模型降低回撤。"
        suggested_exposure = {"bull": 0.85, "oscillation": 0.55, "bear": 0.25}.get(
            regime, 0.5
        )
        return {
            "regime": regime,
            "confidence": round(max(0.0, min(0.95, confidence)), 4),
            "reasoning": reasoning,
            "suggested_exposure": suggested_exposure,
            "source": "rule",
        }


class GovernanceCoordinator:
    def __init__(
        self,
        *,
        governance_policy: Optional[Dict[str, Any]] = None,
        min_confidence: float = 0.6,
        cooldown_cycles: int = 2,
        hysteresis_margin: float = 0.08,
        agent_override_max_gap: float = 0.18,
    ):
        self.governance_policy = {
            **dict(DEFAULT_GOVERNANCE_POLICY),
            **dict(governance_policy or {}),
        }
        self.observer = MarketObservationService(self.governance_policy)
        self.classifier = RegimeClassifier(self.governance_policy)
        self.min_confidence = float(min_confidence)
        self.cooldown_cycles = max(0, int(cooldown_cycles))
        self.hysteresis_margin = float(hysteresis_margin)
        self.agent_override_max_gap = float(agent_override_max_gap)

    @staticmethod
    def _lookup_leaderboard_entry(
        leaderboard: Dict[str, Any],
        manager_id: str,
    ) -> Dict[str, Any]:
        for entry in list(leaderboard.get("entries") or []):
            entry_manager_id = str(entry.get("manager_id") or "").strip()
            if entry_manager_id == manager_id:
                return dict(entry)
        return {}

    def _evaluate_cooldown_exception(
        self,
        *,
        leaderboard_path: str | Path,
        current_manager_id: str,
        candidate_manager_id: str,
        current_weight: float,
        candidate_weight: float,
        decision_confidence: float,
    ) -> Dict[str, Any]:
        if candidate_manager_id == current_manager_id:
            return {
                "applied": False,
                "reason": "same_manager",
                "details": {
                    "current_manager_id": current_manager_id,
                    "candidate_manager_id": candidate_manager_id,
                },
            }
        try:
            leaderboard = load_leaderboard(leaderboard_path)
        except Exception as exc:
            return {
                "applied": False,
                "reason": "leaderboard_unavailable",
                "details": {
                    "leaderboard_path": str(leaderboard_path),
                    "error": str(exc),
                },
            }
        current_entry = self._lookup_leaderboard_entry(leaderboard, current_manager_id)
        candidate_entry = self._lookup_leaderboard_entry(
            leaderboard, candidate_manager_id
        )
        if not current_entry or not candidate_entry:
            return {
                "applied": False,
                "reason": "leaderboard_entries_missing",
                "details": {
                    "current_manager_id": current_manager_id,
                    "candidate_manager_id": candidate_manager_id,
                    "current_entry_found": bool(current_entry),
                    "candidate_entry_found": bool(candidate_entry),
                },
            }

        weight_gap = round(candidate_weight - current_weight, 4)
        benchmark_gap = round(
            float(candidate_entry.get("benchmark_pass_rate", 0.0) or 0.0)
            - float(current_entry.get("benchmark_pass_rate", 0.0) or 0.0),
            4,
        )
        return_gap = round(
            float(candidate_entry.get("avg_return_pct", 0.0) or 0.0)
            - float(current_entry.get("avg_return_pct", 0.0) or 0.0),
            4,
        )
        current_benchmark = float(current_entry.get("benchmark_pass_rate", 0.0) or 0.0)
        current_return = float(current_entry.get("avg_return_pct", 0.0) or 0.0)
        min_current_benchmark = float(
            self.governance_policy[
                "cooldown_exception_current_benchmark_pass_rate_max"
            ]
        )
        min_current_return = float(
            self.governance_policy["cooldown_exception_current_avg_return_pct_max"]
        )
        min_confidence = float(
            self.governance_policy["cooldown_exception_min_confidence"]
        )
        min_weight_gap = float(
            self.governance_policy["cooldown_exception_min_candidate_weight_gap"]
        )
        min_benchmark_gap = float(
            self.governance_policy["cooldown_exception_min_benchmark_gap"]
        )
        min_return_gap = float(
            self.governance_policy["cooldown_exception_min_return_gap"]
        )

        current_is_weak = (
            current_benchmark <= min_current_benchmark
            or current_return <= min_current_return
        )
        candidate_is_strong = (
            decision_confidence >= min_confidence
            and weight_gap >= min_weight_gap
            and (benchmark_gap >= min_benchmark_gap or return_gap >= min_return_gap)
        )
        details = {
            "current_benchmark_pass_rate": current_benchmark,
            "candidate_benchmark_pass_rate": float(
                candidate_entry.get("benchmark_pass_rate", 0.0) or 0.0
            ),
            "benchmark_gap": benchmark_gap,
            "current_avg_return_pct": current_return,
            "candidate_avg_return_pct": float(
                candidate_entry.get("avg_return_pct", 0.0) or 0.0
            ),
            "return_gap": return_gap,
            "weight_gap": weight_gap,
            "decision_confidence": decision_confidence,
            "thresholds": {
                "current_benchmark_pass_rate_max": min_current_benchmark,
                "current_avg_return_pct_max": min_current_return,
                "min_confidence": min_confidence,
                "min_candidate_weight_gap": min_weight_gap,
                "min_benchmark_gap": min_benchmark_gap,
                "min_return_gap": min_return_gap,
            },
            "checks": {
                "current_benchmark_is_weak": current_benchmark <= min_current_benchmark,
                "current_return_is_weak": current_return <= min_current_return,
                "candidate_confidence_ok": decision_confidence >= min_confidence,
                "candidate_weight_gap_ok": weight_gap >= min_weight_gap,
                "candidate_benchmark_gap_ok": benchmark_gap >= min_benchmark_gap,
                "candidate_return_gap_ok": return_gap >= min_return_gap,
            },
        }
        if not (current_is_weak and candidate_is_strong):
            if not current_is_weak:
                reason = "current_manager_not_weak"
            elif decision_confidence < min_confidence:
                reason = "candidate_confidence_too_low"
            elif weight_gap < min_weight_gap:
                reason = "candidate_weight_gap_too_small"
            else:
                reason = "candidate_performance_gap_too_small"
            return {
                "applied": False,
                "reason": reason,
                "details": details,
            }
        return {
            "applied": True,
            "reason": "candidate_outperforms_weak_current",
            "details": details,
        }

    def _build_shadow_provisional_allocation(
        self,
        *,
        regime: str,
        cutoff_date: str,
        allowed_manager_ids: List[str],
        allocator_top_n: int,
    ) -> AllocationPlan:
        normalized_regime = regime if regime in DEFAULT_REGIME_PRIORS else "unknown"
        priors = dict(
            DEFAULT_REGIME_PRIORS.get(
                normalized_regime,
                DEFAULT_REGIME_PRIORS["unknown"],
            )
        )
        explicit_allowlist = bool(list(allowed_manager_ids or []))
        candidate_manager_ids = _filter_known_manager_ids(allowed_manager_ids)
        if not candidate_manager_ids and not explicit_allowlist:
            candidate_manager_ids = list(priors)

        provisional_scores: Dict[str, float] = {}
        top_candidates: List[Dict[str, Any]] = []
        for manager_id in candidate_manager_ids:
            prior = float(priors.get(manager_id, 0.0) or 0.0)
            compatibility = float(
                manager_regime_compatibility(manager_id, normalized_regime)
            )
            provisional_scores[manager_id] = max(0.01, prior) * max(0.25, compatibility)
            top_candidates.append(
                {
                    "manager_id": manager_id,
                    "manager_config_ref": resolve_manager_config_ref(manager_id),
                    "regime_score": round(provisional_scores[manager_id], 4),
                    "regime_compatibility": round(compatibility, 4),
                    "prior_weight": round(prior, 4),
                    "source": "shadow_regime_prior",
                }
            )

        total_score = sum(provisional_scores.values()) or float(
            len(candidate_manager_ids) or 1
        )
        normalized_weights = {
            manager_id: provisional_scores.get(manager_id, 0.0) / total_score
            for manager_id in candidate_manager_ids
        }
        allocator = ModelAllocator()
        capped_weights = allocator._apply_regime_caps(
            normalized_regime,
            normalized_weights,
        )
        ordered_candidates = [
            manager_id
            for manager_id, _weight in sorted(
                capped_weights.items(),
                key=lambda item: item[1],
                reverse=True,
            )
            if _weight > 0
        ][: max(1, int(allocator_top_n or 1))]
        if not ordered_candidates:
            ordered_candidates = candidate_manager_ids[:1]
        selected_total = (
            sum(
                capped_weights.get(manager_id, 0.0) for manager_id in ordered_candidates
            )
            or 1.0
        )
        selected_weights = {
            manager_id: round(
                capped_weights.get(manager_id, 0.0) / selected_total,
                4,
            )
            for manager_id in ordered_candidates
        }
        leading_manager_id = ordered_candidates[0]
        leading_weight = float(selected_weights.get(leading_manager_id, 0.0) or 0.0)
        leading_compatibility = float(
            manager_regime_compatibility(leading_manager_id, normalized_regime)
        )
        provisional_confidence = round(
            max(
                0.62,
                min(
                    0.78,
                    0.56 + leading_weight * 0.18 + leading_compatibility * 0.08,
                ),
            ),
            4,
        )
        ordered_top_candidates = [
            item
            for item in sorted(
                top_candidates,
                key=lambda candidate: (
                    float(candidate.get("regime_score", 0.0) or 0.0),
                    float(candidate.get("regime_compatibility", 0.0) or 0.0),
                    float(candidate.get("prior_weight", 0.0) or 0.0),
                ),
                reverse=True,
            )
            if str(item.get("manager_id") or "") in ordered_candidates
        ]
        return AllocationPlan(
            as_of_date=cutoff_date,
            regime=normalized_regime,
            active_manager_ids=list(ordered_candidates),
            manager_budget_weights=selected_weights,
            selected_manager_config_refs={
                manager_id: resolve_manager_config_ref(manager_id)
                for manager_id in ordered_candidates
            },
            cash_reserve=DEFAULT_CASH_RESERVE.get(normalized_regime, 0.20),
            confidence=provisional_confidence,
            reasoning=(
                f"当前 regime={normalized_regime}，shadow 冷启动阶段暂无通过质量门的正式候选，"
                f"因此按 regime 先验与风格兼容度启用 provisional 分配，优先选择 {leading_manager_id}"
                f"（权重 {leading_weight:.0%}）。"
            ),
            metadata={
                "top_candidates": ordered_top_candidates,
                "leaderboard_generated_at": None,
                "used_provisional_leaderboard": True,
                "qualified_candidate_count": 0,
                "failed_quality_entries": [],
                "shadow_provisional_fallback": True,
                "shadow_provisional_reason": "no_qualified_governance_candidates",
                "shadow_provisional_source": "shadow_regime_prior",
            },
        )

    def decide(
        self,
        *,
        stock_data: Dict[str, pd.DataFrame],
        cutoff_date: str,
        current_manager_id: str,
        leaderboard_path: str | Path,
        allocator_top_n: int = 3,
        allowed_manager_ids: Optional[List[str]] = None,
        governance_mode: str = "rule",
        regime_agent: Any = None,
        selector_agent: Any = None,
        previous_decision: Optional[Dict[str, Any]] = None,
        current_cycle_id: Optional[int] = None,
        last_governance_change_cycle_id: Optional[int] = None,
        data_manager: Any = None,
        shadow_mode: bool = False,
    ) -> GovernanceDecision:
        previous_payload = dict(previous_decision or {})
        explicit_allowlist = allowed_manager_ids is not None
        requested_allowed_manager_ids = [
            str(item).strip()
            for item in (allowed_manager_ids or DEFAULT_GOVERNANCE_ALLOWED_MANAGER_IDS)
            if str(item).strip()
        ]
        valid_requested_allowed_manager_ids = _filter_known_manager_ids(
            requested_allowed_manager_ids
        )
        allowed = list(valid_requested_allowed_manager_ids)
        if governance_mode == "off":
            dominant_manager_config = resolve_manager_config_ref(current_manager_id)
            return GovernanceDecision(
                as_of_date=cutoff_date,
                regime="unknown",
                regime_confidence=0.0,
                decision_confidence=0.0,
                active_manager_ids=[current_manager_id],
                manager_budget_weights={current_manager_id: 1.0},
                dominant_manager_id=current_manager_id,
                cash_reserve_hint=0.0,
                portfolio_constraints={"cash_reserve": 0.0, "top_n": 1},
                decision_source="disabled",
                regime_source="disabled",
                reasoning="治理路由已关闭，维持当前经理预算。",
                evidence={},
                agent_advice={},
                allocation_plan={},
                guardrail_checks=[],
                metadata={
                    "governance_mode": governance_mode,
                    "allowed_manager_ids": allowed,
                    "dominant_manager_config": dominant_manager_config,
                    "historical": {
                        "previous_dominant_manager_id": current_manager_id,
                        "guardrail_hold": True,
                        "guardrail_hold_reason": "governance_disabled",
                        "governance_applied": False,
                    },
                },
            )
        if current_manager_id not in allowed:
            allowed.insert(0, current_manager_id)
        observation = self.observer.observe(
            stock_data, cutoff_date, data_manager=data_manager
        )
        regime_payload = self.classifier.classify(
            observation,
            agent=regime_agent,
            mode="hybrid" if governance_mode in {"hybrid", "agent"} else "rule",
        )
        allocation = build_allocation_plan(
            str(regime_payload.get("regime") or "unknown"),
            leaderboard_path,
            as_of_date=cutoff_date,
            top_n=max(1, int(allocator_top_n)),
        )
        filtered_manager_ids = [
            name for name in allocation.active_manager_ids if name in allowed
        ]
        filtered_weights = {
            name: weight
            for name, weight in allocation.manager_budget_weights.items()
            if name in allowed
        }
        no_qualified_candidates = not filtered_manager_ids
        shadow_provisional_applied = False
        if no_qualified_candidates and shadow_mode:
            shadow_allowed_manager_ids = (
                valid_requested_allowed_manager_ids
                if explicit_allowlist
                else requested_allowed_manager_ids
            )
            if shadow_allowed_manager_ids:
                provisional_allocation = self._build_shadow_provisional_allocation(
                    regime=str(regime_payload.get("regime") or "unknown"),
                    cutoff_date=cutoff_date,
                    allowed_manager_ids=shadow_allowed_manager_ids,
                    allocator_top_n=max(1, int(allocator_top_n)),
                )
                provisional_manager_ids = [
                    name
                    for name in provisional_allocation.active_manager_ids
                    if name in shadow_allowed_manager_ids
                ]
                provisional_weights = {
                    name: weight
                    for name, weight in provisional_allocation.manager_budget_weights.items()
                    if name in provisional_manager_ids
                }
                if provisional_manager_ids:
                    allocation = provisional_allocation
                    filtered_manager_ids = provisional_manager_ids
                    filtered_weights = provisional_weights
                    no_qualified_candidates = False
                    shadow_provisional_applied = True
        if not filtered_weights and filtered_manager_ids:
            filtered_weights = {filtered_manager_ids[0]: 1.0}
        rule_selected_manager_id = (
            filtered_manager_ids[0] if filtered_manager_ids else current_manager_id
        )
        agent_advice: Dict[str, Any] = {}
        candidate_manager_id = rule_selected_manager_id
        decision_source = (
            "shadow_regime_prior" if shadow_provisional_applied else "rule_allocator"
        )
        if (
            not no_qualified_candidates
            and not shadow_provisional_applied
            and governance_mode in {"hybrid", "agent"}
            and selector_agent is not None
        ):
            agent_advice = selector_agent.analyze(
                {
                    "regime": regime_payload.get("regime"),
                    "dominant_manager_id": current_manager_id,
                    "current_manager_id": current_manager_id,
                    "active_manager_ids": filtered_manager_ids,
                    "allowed_manager_ids": allowed,
                    "candidate_manager_ids": filtered_manager_ids,
                    "candidate_weights": filtered_weights,
                    "market_stats": observation.stats,
                }
            )
            advised = str(agent_advice.get("dominant_manager_id") or "").strip()
            if advised and advised in allowed:
                rule_weight = float(
                    filtered_weights.get(rule_selected_manager_id, 0.0) or 0.0
                )
                advised_weight = float(filtered_weights.get(advised, 0.0) or 0.0)
                gap = abs(rule_weight - advised_weight)
                if governance_mode == "agent" or gap <= self.agent_override_max_gap:
                    candidate_manager_id = advised
                    decision_source = (
                        "hybrid_agent"
                        if advised != rule_selected_manager_id
                        else "hybrid_consensus"
                    )
        current_weight = float(filtered_weights.get(current_manager_id, 0.0) or 0.0)
        candidate_weight = float(filtered_weights.get(candidate_manager_id, 0.0) or 0.0)
        hold_current = False
        hold_reason = ""
        guardrail_checks: List[Dict[str, Any]] = []
        regime_confidence = float(regime_payload.get("confidence", 0.0) or 0.0)
        decision_confidence = round(
            max(regime_confidence, float(allocation.confidence or 0.0)), 4
        )
        cooldown_exception = {"applied": False, "reason": "", "details": {}}
        if no_qualified_candidates:
            hold_current = True
            hold_reason = "no_qualified_governance_candidates"
            candidate_manager_id = current_manager_id
            current_weight = 1.0
            candidate_weight = 1.0
            filtered_manager_ids = [current_manager_id]
            filtered_weights = {current_manager_id: 1.0}
        elif candidate_manager_id != current_manager_id:
            min_style_compatibility = float(
                self.governance_policy.get("min_regime_style_compatibility", 0.40)
                or 0.40
            )
            candidate_style_compatibility = manager_regime_compatibility(
                candidate_manager_id,
                str(regime_payload.get("regime") or "unknown"),
            )
            current_style_compatibility = manager_regime_compatibility(
                current_manager_id,
                str(regime_payload.get("regime") or "unknown"),
            )
            if (
                candidate_style_compatibility < min_style_compatibility
                and current_style_compatibility >= candidate_style_compatibility
            ):
                hold_current = True
                hold_reason = "regime_style_mismatch"
            if decision_confidence < self.min_confidence:
                hold_current = True
                hold_reason = "governance_confidence_below_threshold"
            elif (candidate_weight - current_weight) < self.hysteresis_margin:
                hold_current = True
                hold_reason = "governance_hysteresis_hold"
            elif (
                current_cycle_id is not None
                and last_governance_change_cycle_id is not None
                and (current_cycle_id - last_governance_change_cycle_id)
                <= self.cooldown_cycles
            ):
                cooldown_exception = self._evaluate_cooldown_exception(
                    leaderboard_path=leaderboard_path,
                    current_manager_id=current_manager_id,
                    candidate_manager_id=candidate_manager_id,
                    current_weight=current_weight,
                    candidate_weight=candidate_weight,
                    decision_confidence=decision_confidence,
                )
                if cooldown_exception.get("applied"):
                    hold_current = False
                else:
                    hold_current = True
                    hold_reason = "governance_cooldown_active"
        guardrail_checks.append(
            {
                "name": "qualified_candidates_available",
                "passed": not no_qualified_candidates,
                "actual": 0 if no_qualified_candidates else len(filtered_manager_ids),
                "threshold": 1,
            }
        )
        guardrail_checks.append(
            {
                "name": "min_confidence",
                "passed": decision_confidence >= self.min_confidence,
                "actual": decision_confidence,
                "threshold": self.min_confidence,
            }
        )
        guardrail_checks.append(
            {
                "name": "hysteresis_margin",
                "passed": (candidate_weight - current_weight) >= self.hysteresis_margin
                or candidate_manager_id == current_manager_id,
                "actual": round(candidate_weight - current_weight, 4),
                "threshold": self.hysteresis_margin,
            }
        )
        guardrail_checks.append(
            {
                "name": "regime_compatibility",
                "passed": manager_regime_compatibility(
                    candidate_manager_id, str(regime_payload.get("regime") or "unknown")
                )
                >= float(
                    self.governance_policy.get("min_regime_style_compatibility", 0.40)
                    or 0.40
                )
                or candidate_manager_id == current_manager_id,
                "actual": manager_regime_compatibility(
                    candidate_manager_id, str(regime_payload.get("regime") or "unknown")
                ),
                "threshold": float(
                    self.governance_policy.get("min_regime_style_compatibility", 0.40)
                    or 0.40
                ),
            }
        )
        if current_cycle_id is not None and last_governance_change_cycle_id is not None:
            cycles_since_switch = current_cycle_id - last_governance_change_cycle_id
            guardrail_checks.append(
                {
                    "name": "cooldown_cycles",
                    "passed": cycles_since_switch > self.cooldown_cycles
                    or candidate_manager_id == current_manager_id
                    or bool(cooldown_exception.get("applied")),
                    "actual": cycles_since_switch,
                    "threshold": self.cooldown_cycles,
                    "exception_applied": bool(cooldown_exception.get("applied")),
                }
            )
        dominant_manager_id = (
            current_manager_id if hold_current else candidate_manager_id
        )
        if hold_current and shadow_provisional_applied:
            decision_source = "guardrail_hold"
        dominant_manager_config = resolve_manager_config_ref(dominant_manager_id)
        if hold_current:
            active_manager_ids = [current_manager_id]
            manager_budget_weights = {current_manager_id: 1.0}
        else:
            active_manager_ids = filtered_manager_ids or [dominant_manager_id]
            manager_budget_weights = {
                name: round(float(weight), 4)
                for name, weight in filtered_weights.items()
                if name in active_manager_ids
            }
            if not manager_budget_weights and active_manager_ids:
                manager_budget_weights = {active_manager_ids[0]: 1.0}
        reasoning = self._build_reasoning(
            current_manager_id=current_manager_id,
            dominant_manager_id=dominant_manager_id,
            active_manager_ids=active_manager_ids,
            regime_payload=regime_payload,
            allocation=allocation.to_dict(),
            agent_advice=agent_advice,
            hold_reason=hold_reason,
        )
        if shadow_provisional_applied:
            reasoning = (
                f"{reasoning} 当前采用 shadow 专用 provisional fallback，"
                "用于修正冷启动窗口下正式榜单尚未形成时的治理偏置。"
            ).strip()
        if cooldown_exception.get("applied"):
            reasoning = f"{reasoning} 满足 cooldown 切换例外，允许提前切换到 {dominant_manager_id}。".strip()
        metadata = {
            "governance_mode": governance_mode,
            "allowed_manager_ids": allowed,
            "observation_version": "governance_v2",
            "previous_decision": previous_payload,
            "cooldown_exception": cooldown_exception,
            "dominant_manager_config": dominant_manager_config,
            "shadow_mode": bool(shadow_mode),
            "shadow_provisional_fallback": {
                "applied": shadow_provisional_applied,
                "reason": (
                    "no_qualified_governance_candidates"
                    if shadow_provisional_applied
                    else ""
                ),
                "source": ("shadow_regime_prior" if shadow_provisional_applied else ""),
            },
            "historical": {
                "previous_dominant_manager_id": current_manager_id,
                "guardrail_hold": hold_current,
                "guardrail_hold_reason": hold_reason,
                "governance_applied": bool(
                    list(previous_payload.get("active_manager_ids") or [])
                    != list(active_manager_ids)
                    or dict(previous_payload.get("manager_budget_weights") or {})
                    != dict(manager_budget_weights)
                    or str(previous_payload.get("dominant_manager_id") or "")
                    != str(dominant_manager_id or "")
                ),
            },
        }
        return GovernanceDecision(
            as_of_date=cutoff_date,
            regime=str(regime_payload.get("regime") or "unknown"),
            regime_confidence=regime_confidence,
            decision_confidence=decision_confidence,
            active_manager_ids=active_manager_ids,
            manager_budget_weights=manager_budget_weights,
            dominant_manager_id=dominant_manager_id,
            cash_reserve_hint=float(allocation.cash_reserve or 0.0),
            portfolio_constraints={
                "cash_reserve": float(allocation.cash_reserve or 0.0),
                "top_n": max(1, int(allocator_top_n)),
                "allowed_manager_ids": list(allowed),
            },
            decision_source=decision_source,
            regime_source=str(regime_payload.get("source") or "rule"),
            reasoning=reasoning,
            evidence={
                "market_observation": observation.to_dict(),
                "rule_result": dict(regime_payload.get("rule_result") or {}),
                "agent_result": dict(regime_payload.get("agent_result") or {}),
                "allocator_quality": {
                    "qualified_candidate_count": int(
                        allocation.metadata.get("qualified_candidate_count", 0) or 0
                    ),
                    "failed_quality_entries": list(
                        allocation.metadata.get("failed_quality_entries") or []
                    ),
                    "top_candidates": list(
                        allocation.metadata.get("top_candidates") or []
                    ),
                    "current_manager_id_compatibility": manager_regime_compatibility(
                        current_manager_id,
                        str(regime_payload.get("regime") or "unknown"),
                    ),
                    "dominant_manager_compatibility": manager_regime_compatibility(
                        dominant_manager_id,
                        str(regime_payload.get("regime") or "unknown"),
                    ),
                },
            },
            agent_advice=agent_advice,
            allocation_plan=allocation.to_dict(),
            guardrail_checks=guardrail_checks,
            metadata=metadata,
        )

    @staticmethod
    def _build_reasoning(
        *,
        current_manager_id: str,
        dominant_manager_id: str,
        active_manager_ids: List[str],
        regime_payload: Dict[str, Any],
        allocation: Dict[str, Any],
        agent_advice: Dict[str, Any],
        hold_reason: str,
    ) -> str:
        regime = regime_payload.get("regime", "unknown")
        regime_reasoning = str(regime_payload.get("reasoning") or "")
        allocation_reasoning = str(allocation.get("reasoning") or "")
        if hold_reason:
            return f"检测到 regime={regime}，但因 {hold_reason} 维持当前主导经理 {current_manager_id}。{regime_reasoning} {allocation_reasoning}".strip()
        if dominant_manager_id == current_manager_id:
            return f"检测到 regime={regime}，维持当前主导经理 {current_manager_id}。{regime_reasoning} {allocation_reasoning}".strip()
        agent_reason = str(agent_advice.get("reasoning") or "").strip()
        tail = f" Agent 建议：{agent_reason}" if agent_reason else ""
        manager_scope = (
            ", ".join(active_manager_ids) if active_manager_ids else dominant_manager_id
        )
        return f"检测到 regime={regime}，激活经理 {manager_scope}，主导经理从 {current_manager_id} 调整为 {dominant_manager_id}。{regime_reasoning} {allocation_reasoning}{tail}".strip()


# Governance leaderboard reporting


DEFAULT_LEADERBOARD_POLICY: Dict[str, Any] = {
    "min_cycles": 3,
    "min_cycles_per_regime": 2,
}
MAX_LEADERBOARD_CYCLE_BYTES = 2 * 1024 * 1024

EXCLUDED_CYCLE_DIR_NAMES = {
    "config_snapshots",
    "control_plane_snapshots",
    "details",
    "proposal_store",
    "validation",
}


def _is_excluded_cycle_path(path: Path) -> bool:
    if path.name.endswith("_config_snapshot.json"):
        return True
    if any(part in EXCLUDED_CYCLE_DIR_NAMES for part in path.parts):
        return True
    parts = path.parts
    for index in range(len(parts) - 1):
        if parts[index] == "state" and parts[index + 1] == "snapshots":
            return True
    return False


def _is_cycle_file_oversized(path: Path) -> bool:
    try:
        return path.stat().st_size > MAX_LEADERBOARD_CYCLE_BYTES
    except OSError:
        return True


def _infer_manager_id(payload: Dict[str, Any], path: Path) -> str:
    candidates = [
        str(payload.get("manager_id") or ""),
        str(payload.get("manager_config_ref") or ""),
        str(payload.get("config_snapshot_path") or ""),
        str(path),
        str(path.parent),
    ]
    params = dict(payload.get("params") or {})
    if "min_defensive_score" in params or "max_volatility" in params:
        return "defensive_low_vol"
    if (
        "min_value_quality_score" in params
        or "max_pe_ttm" in params
        or "min_roe" in params
    ):
        return "value_quality"
    if (
        "min_reversion_score" in params
        or "oversold_rsi" in params
        or "max_5d_drop" in params
    ):
        return "mean_reversion"
    if any(key in params for key in ("signal_threshold", "ma_short", "ma_long")):
        inferred = "momentum"
    else:
        inferred = "unknown"
    haystack = " ".join(candidates).lower()
    for name in ("defensive_low_vol", "value_quality", "mean_reversion", "momentum"):
        if name in haystack:
            return name
    return inferred


def _normalize_manager_config_ref(
    payload: Dict[str, Any], path: Path, manager_id: str
) -> str:
    raw = str(payload.get("manager_config_ref") or "").strip()
    if raw.endswith(".yaml"):
        return Path(raw).stem
    if "config_snapshots" in raw:
        return f"{manager_id}_runtime"
    if raw and raw != "unknown":
        return raw
    run_name = path.parent.name.strip()
    if run_name:
        return run_name
    return f"{manager_id}_default"


def load_cycle_record(path: str | Path) -> Dict[str, Any]:
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["_path"] = str(path)
    payload["_dir"] = str(path.parent)
    manager_id = _infer_manager_id(payload, path)
    payload["manager_id"] = manager_id
    payload["manager_config_ref"] = _normalize_manager_config_ref(
        payload, path, manager_id
    )
    payload["regime"] = str(
        (payload.get("self_assessment") or {}).get("regime")
        or payload.get("regime")
        or "unknown"
    )
    return payload


def collect_cycle_records(root_dir: str | Path) -> List[Dict[str, Any]]:
    root_path = Path(root_dir)
    if not root_path.exists():
        return []
    records: List[Dict[str, Any]] = []
    for path in sorted(root_path.rglob("cycle_*.json")):
        if not (path.name.startswith("cycle_") and path.name.endswith(".json")):
            continue
        if _is_excluded_cycle_path(path):
            continue
        if _is_cycle_file_oversized(path):
            logger.warning(
                "Skipped oversized cycle record %s (%s bytes > %s bytes)",
                path,
                path.stat().st_size if path.exists() else "missing",
                MAX_LEADERBOARD_CYCLE_BYTES,
            )
            continue
        try:
            records.append(load_cycle_record(path))
        except Exception as exc:
            logger.warning("Skipped invalid cycle record %s: %s", path, exc)
            continue
    return records


def _safe_avg(values: Iterable[float]) -> float:
    data = list(values)
    return float(sum(data) / len(data)) if data else 0.0


def _resolved_train_policy_payload(
    *,
    train_policy: Dict[str, Any] | None = None,
    quality_gate_matrix: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = dict(train_policy or {})
    return {
        "promotion_gate": normalize_promotion_gate_policy(
            dict(payload.get("promotion_gate") or {})
        ),
        "freeze_gate": normalize_freeze_gate_policy(
            dict(payload.get("freeze_gate") or {})
        ),
        "quality_gate_matrix": resolve_governance_matrix(
            dict(quality_gate_matrix or payload.get("quality_gate_matrix") or {})
        ),
    }


def _load_train_policy_from_runtime_config_ref(
    runtime_config_ref: str,
) -> Dict[str, Any] | None:
    text = str(runtime_config_ref or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = path.resolve()
    try:
        path = enforce_path_within_root(PROJECT_ROOT, path)
    except ValueError:
        return None
    if not path.exists() or path.suffix.lower() not in {".yaml", ".yml"}:
        return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning(
            "Failed to load train policy from %s: %s", path, exc, exc_info=True
        )
        return None
    if not isinstance(payload, dict):
        logger.warning(
            "Runtime config payload must be a mapping when loading train policy: path=%s type=%s",
            path,
            type(payload).__name__,
        )
        return None
    train_policy = payload.get("train") or {}
    if not isinstance(train_policy, dict):
        logger.warning(
            "Runtime train policy section must be a mapping: path=%s type=%s",
            path,
            type(train_policy).__name__,
        )
        return {}
    return dict(train_policy)


def _resolve_runtime_train_policy(
    *,
    records: List[Dict[str, Any]],
    resolved_policy: Dict[str, Any],
    governance_matrix: Dict[str, Any],
) -> Dict[str, Any]:
    override_train = dict(resolved_policy.get("train") or {})
    if override_train:
        return _resolved_train_policy_payload(
            train_policy=override_train,
            quality_gate_matrix=dict(
                resolved_policy.get("quality_gate_matrix")
                or override_train.get("quality_gate_matrix")
                or governance_matrix
                or {}
            ),
        )
    if not records:
        return _resolved_train_policy_payload(
            train_policy={},
            quality_gate_matrix=governance_matrix,
        )

    latest_record = max(
        records,
        key=lambda item: (
            int(item.get("cycle_id", 0) or 0),
            str(item.get("_path", "")),
        ),
    )
    run_context = dict(latest_record.get("run_context") or {})
    resolved_train_policy = dict(run_context.get("resolved_train_policy") or {})
    if resolved_train_policy:
        return _resolved_train_policy_payload(
            train_policy=resolved_train_policy,
            quality_gate_matrix=dict(
                resolved_train_policy.get("quality_gate_matrix")
                or run_context.get("quality_gate_matrix")
                or governance_matrix
                or {}
            ),
        )

    lineage_record = dict(latest_record.get("lineage_record") or {})
    for runtime_config_ref in (
        lineage_record.get("active_runtime_config_ref"),
        run_context.get("active_runtime_config_ref"),
        lineage_record.get("candidate_runtime_config_ref"),
        run_context.get("candidate_runtime_config_ref"),
        latest_record.get("manager_config_ref"),
    ):
        train_policy = _load_train_policy_from_runtime_config_ref(
            str(runtime_config_ref or "")
        )
        if train_policy is not None:
            return _resolved_train_policy_payload(
                train_policy=train_policy,
                quality_gate_matrix=dict(
                    train_policy.get("quality_gate_matrix")
                    or run_context.get("quality_gate_matrix")
                    or governance_matrix
                    or {}
                ),
            )

    return _resolved_train_policy_payload(
        train_policy={},
        quality_gate_matrix=dict(
            run_context.get("quality_gate_matrix") or governance_matrix or {}
        ),
    )


def _entry_key(record: Dict[str, Any]) -> str:
    return f"{record.get('manager_id', 'unknown')}::{record.get('manager_config_ref', 'unknown')}"


def _extract_scoring_change_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    events = list(item.get("optimization_events") or [])
    scoring_events = []
    changed_keys = set()
    for event in events:
        applied = dict(event.get("applied_change") or {})
        scoring = dict(applied.get("scoring") or {})
        if not scoring:
            continue
        scoring_events.append(scoring)
        for section_name, section_values in scoring.items():
            if isinstance(section_values, dict):
                for key in section_values.keys():
                    changed_keys.add(f"{section_name}.{key}")
    return {
        "scoring_mutation_count": len(scoring_events),
        "scoring_changed_keys": sorted(changed_keys),
    }


def _eligibility_for_entry(
    *,
    cycle_count: int,
    dominant_regime: str,
    regimes: Dict[str, int],
    policy: Dict[str, Any],
) -> tuple[bool, str, Dict[str, Any]]:
    min_cycles = max(1, int(policy.get("min_cycles", 1) or 1))
    min_cycles_per_regime = max(1, int(policy.get("min_cycles_per_regime", 1) or 1))
    dominant_regime_cycles = int(regimes.get(dominant_regime, 0) or 0)
    if cycle_count < min_cycles:
        return (
            False,
            "min_cycles",
            {
                "min_cycles": min_cycles,
                "observed_cycles": cycle_count,
                "min_cycles_per_regime": min_cycles_per_regime,
                "dominant_regime": dominant_regime,
                "dominant_regime_cycles": dominant_regime_cycles,
            },
        )
    if dominant_regime_cycles < min_cycles_per_regime:
        return (
            False,
            "min_regime_cycles",
            {
                "min_cycles": min_cycles,
                "observed_cycles": cycle_count,
                "min_cycles_per_regime": min_cycles_per_regime,
                "dominant_regime": dominant_regime,
                "dominant_regime_cycles": dominant_regime_cycles,
            },
        )
    return (
        True,
        "",
        {
            "min_cycles": min_cycles,
            "observed_cycles": cycle_count,
            "min_cycles_per_regime": min_cycles_per_regime,
            "dominant_regime": dominant_regime,
            "dominant_regime_cycles": dominant_regime_cycles,
        },
    )


def _build_regime_performance(
    items: List[Dict[str, Any]],
    *,
    manager_id: str,
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in items:
        grouped[str(item.get("regime") or "unknown")].append(item)

    performance: Dict[str, Dict[str, Any]] = {}
    for regime_name, regime_items in grouped.items():
        returns = [float(item.get("return_pct", 0.0) or 0.0) for item in regime_items]
        sharpes = [
            float((item.get("self_assessment") or {}).get("sharpe_ratio", 0.0) or 0.0)
            for item in regime_items
        ]
        drawdowns = [
            float((item.get("self_assessment") or {}).get("max_drawdown", 0.0) or 0.0)
            for item in regime_items
        ]
        strategy_scores = [
            float(
                (item.get("self_assessment") or {}).get(
                    "overall_score",
                    (item.get("strategy_scores") or {}).get("overall_score", 0.0),
                )
                or 0.0
            )
            for item in regime_items
        ]
        benchmark_pass_rate = (
            sum(1 for item in regime_items if bool(item.get("benchmark_passed", False)))
            / len(regime_items)
            if regime_items
            else 0.0
        )
        win_rate = (
            sum(1 for item in regime_items if bool(item.get("is_profit", False)))
            / len(regime_items)
            if regime_items
            else 0.0
        )
        regime_score = (
            _safe_avg(returns) * 0.35
            + _safe_avg(sharpes) * 9.0
            + _safe_avg(strategy_scores) * 12.0
            + benchmark_pass_rate * 15.0
            - _safe_avg(drawdowns) * 0.40
        ) * max(0.25, manager_regime_compatibility(manager_id, regime_name))
        performance[regime_name] = {
            "cycles": len(regime_items),
            "avg_return_pct": round(_safe_avg(returns), 6),
            "avg_sharpe_ratio": round(_safe_avg(sharpes), 6),
            "avg_max_drawdown": round(_safe_avg(drawdowns), 6),
            "avg_strategy_score": round(_safe_avg(strategy_scores), 6),
            "benchmark_pass_rate": round(benchmark_pass_rate, 6),
            "win_rate": round(win_rate, 6),
            "score": round(regime_score, 6),
            "compatibility": manager_regime_compatibility(manager_id, regime_name),
        }
    return performance


def _string_list(values: Any) -> List[str]:
    items: List[str] = []
    for value in list(values or []):
        text = str(value or "").strip()
        if text and text not in items:
            items.append(text)
    return items


def _extract_regime_hard_fail_summary(quality_gate: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(quality_gate or {})
    regime_hard_fail = dict(payload.get("regime_hard_fail") or {})
    failed_regime_names = _string_list(
        regime_hard_fail.get("failed_regime_names") or []
    )

    if not failed_regime_names:
        prefix = "regime_hard_fail."
        for check in list(payload.get("failed_checks") or []):
            check_name = str(dict(check or {}).get("name") or "").strip()
            if not check_name.startswith(prefix):
                continue
            regime_name = check_name[len(prefix) :].strip()
            if regime_name and regime_name not in failed_regime_names:
                failed_regime_names.append(regime_name)

    if failed_regime_names:
        if not regime_hard_fail:
            regime_hard_fail = {
                "enabled": True,
                "passed": False,
                "failed_regime_names": list(failed_regime_names),
                "failed_regimes": [{"regime": name} for name in failed_regime_names],
            }
        elif "failed_regime_names" not in regime_hard_fail:
            regime_hard_fail["failed_regime_names"] = list(failed_regime_names)

    return {
        "regime_hard_fail": regime_hard_fail,
        "failed_regime_names": failed_regime_names,
    }


def build_leaderboard(
    records: List[Dict[str, Any]], policy: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    resolved_policy = {
        **dict(DEFAULT_LEADERBOARD_POLICY),
        **dict(policy or {}),
    }
    governance_matrix = resolve_governance_matrix(
        dict(
            resolved_policy.get("quality_gate_matrix")
            or dict(resolved_policy.get("train") or {}).get("quality_gate_matrix")
            or {}
        )
    )
    policy_payload = dict(resolved_policy)
    policy_payload["train"] = _resolve_runtime_train_policy(
        records=records,
        resolved_policy=resolved_policy,
        governance_matrix=governance_matrix,
    )
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[_entry_key(record)].append(record)

    entries: List[Dict[str, Any]] = []
    regime_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for key, items in grouped.items():
        items = sorted(items, key=lambda item: int(item.get("cycle_id", 0)))
        returns = [float(item.get("return_pct", 0.0) or 0.0) for item in items]
        sharpes = [
            float((item.get("self_assessment") or {}).get("sharpe_ratio", 0.0) or 0.0)
            for item in items
        ]
        drawdowns = [
            float((item.get("self_assessment") or {}).get("max_drawdown", 0.0) or 0.0)
            for item in items
        ]
        excess_returns = [
            float((item.get("self_assessment") or {}).get("excess_return", 0.0) or 0.0)
            for item in items
        ]
        strategy_scores = [
            float(
                (item.get("self_assessment") or {}).get(
                    "overall_score",
                    (item.get("strategy_scores") or {}).get("overall_score", 0.0),
                )
                or 0.0
            )
            for item in items
        ]
        wins = sum(1 for item in items if bool(item.get("is_profit", False)))
        benchmark_passes = sum(
            1 for item in items if bool(item.get("benchmark_passed", False))
        )
        regimes: Dict[str, int] = defaultdict(int)
        for item in items:
            regimes[str(item.get("regime", "unknown"))] += 1
        dominant_regime = (
            max(regimes.items(), key=lambda pair: pair[1])[0] if regimes else "unknown"
        )
        regime_confidence = build_regime_confidence_map(
            items,
            min_cycles_per_regime=max(
                1, int(resolved_policy.get("min_cycles_per_regime", 1) or 1)
            ),
        )
        eligible_for_governance, ineligible_reason, sample_gate = (
            _eligibility_for_entry(
                cycle_count=len(items),
                dominant_regime=dominant_regime,
                regimes=regimes,
                policy=resolved_policy,
            )
        )
        undercovered_regimes = [
            regime_name
            for regime_name, summary in regime_confidence.items()
            if bool(dict(summary or {}).get("exploratory_only", False))
        ]
        sample_gate = {
            **sample_gate,
            "undercovered_regimes": undercovered_regimes,
            "exploratory_only": bool(undercovered_regimes),
        }
        latest_item = items[-1]
        latest_run_context = dict(latest_item.get("run_context") or {})
        latest_lineage_record = dict(latest_item.get("lineage_record") or {})
        latest_promotion_record = dict(latest_item.get("promotion_record") or {})
        governance_stage = infer_deployment_stage(
            run_context=latest_run_context,
            optimization_events=list(latest_item.get("optimization_events") or []),
        )
        deployment_stage = str(
            latest_lineage_record.get("deployment_stage")
            or latest_run_context.get("deployment_stage")
            or governance_stage.get("deployment_stage")
            or "active"
        )
        composite_score = (
            _safe_avg(returns) * 0.30
            + _safe_avg(sharpes) * 10.0
            + _safe_avg(excess_returns) * 0.15
            + _safe_avg(strategy_scores) * 15.0
            + (benchmark_passes / len(items) if items else 0.0) * 18.0
            - _safe_avg(drawdowns) * 0.45
        )
        scoring_summaries = [_extract_scoring_change_summary(item) for item in items]
        objective_profile = {
            "benchmark_pass_rate": round(benchmark_passes / len(items), 6)
            if items
            else 0.0,
            "avg_sharpe_ratio": round(_safe_avg(sharpes), 6),
            "avg_return_pct": round(_safe_avg(returns), 6),
            "avg_max_drawdown": round(_safe_avg(drawdowns), 6),
        }
        entry = {
            "key": key,
            "manager_id": str(items[0].get("manager_id", "unknown")),
            "manager_config_ref": str(items[0].get("manager_config_ref", "unknown")),
            "run_dirs": sorted({str(item.get("_dir", "")) for item in items}),
            "cycles": len(items),
            "profit_cycles": wins,
            "profit_rate": wins / len(items) if items else 0.0,
            "avg_return_pct": round(_safe_avg(returns), 6),
            "avg_sharpe_ratio": round(_safe_avg(sharpes), 6),
            "avg_max_drawdown": round(_safe_avg(drawdowns), 6),
            "avg_excess_return": round(_safe_avg(excess_returns), 6),
            "avg_strategy_score": round(_safe_avg(strategy_scores), 6),
            "benchmark_pass_rate": round(benchmark_passes / len(items), 6)
            if items
            else 0.0,
            "dominant_regime": dominant_regime,
            "regime_breakdown": dict(sorted(regimes.items())),
            "latest_cycle_id": int(items[-1].get("cycle_id", 0) or 0),
            "latest_cutoff_date": str(items[-1].get("cutoff_date", "")),
            "latest_return_pct": float(items[-1].get("return_pct", 0.0) or 0.0),
            "score": round(composite_score, 6),
            "deployment_stage": deployment_stage,
            "promotion_gate_status": str(
                latest_promotion_record.get("gate_status") or ""
            ),
            "promotion_status": str(latest_promotion_record.get("status") or ""),
            "sample_gate": sample_gate,
            "regime_confidence": regime_confidence,
            "exploratory_only": bool(sample_gate.get("exploratory_only", False)),
            "quality_gate": {},
            "eligible_for_governance": False,
            "ineligible_reason": ineligible_reason,
            "style_profile": get_manager_style_profile(
                str(items[0].get("manager_id", "unknown"))
            ),
            "regime_performance": _build_regime_performance(
                items,
                manager_id=str(items[0].get("manager_id", "unknown")),
            ),
            "objective_profile": objective_profile,
            "objective_eligible_after_governance": False,
            "scoring_mutation_count": sum(
                item.get("scoring_mutation_count", 0) for item in scoring_summaries
            ),
            "scoring_changed_keys": sorted(
                {
                    key
                    for item in scoring_summaries
                    for key in item.get("scoring_changed_keys", [])
                }
            ),
        }
        quality_gate = evaluate_governance_quality_gate(
            entry,
            policy=dict((governance_matrix.get("governance") or {})),
        )
        regime_hard_fail_summary = _extract_regime_hard_fail_summary(quality_gate)
        entry["quality_gate"] = quality_gate
        entry["regime_hard_fail"] = dict(
            regime_hard_fail_summary.get("regime_hard_fail") or {}
        )
        entry["failed_regime_names"] = list(
            regime_hard_fail_summary.get("failed_regime_names") or []
        )
        entry["objective_eligible_after_governance"] = bool(
            quality_gate.get("passed", False)
        )
        if eligible_for_governance and quality_gate.get("passed", False):
            entry["eligible_for_governance"] = True
            entry["ineligible_reason"] = ""
        elif not entry["ineligible_reason"] and not quality_gate.get("passed", False):
            failed_checks = list(quality_gate.get("failed_checks") or [])
            entry["ineligible_reason"] = (
                f"quality_gate:{failed_checks[0].get('name')}"
                if failed_checks
                else "quality_gate"
            )
            if (
                entry["ineligible_reason"] == "quality_gate"
                and entry["failed_regime_names"]
            ):
                entry["ineligible_reason"] = (
                    f"quality_gate:regime_hard_fail.{entry['failed_regime_names'][0]}"
                )
        entries.append(entry)
        if entry["eligible_for_governance"]:
            raw_regime_performance = entry.get("regime_performance")
            regime_performance = (
                raw_regime_performance
                if isinstance(raw_regime_performance, dict)
                else {}
            )
            for regime_name, performance in regime_performance.items():
                performance_payload = (
                    performance if isinstance(performance, dict) else {}
                )
                if int(performance_payload.get("cycles", 0) or 0) <= 0:
                    continue
                enriched = dict(entry)
                enriched["_regime_name"] = regime_name
                enriched["_regime_score"] = float(
                    performance_payload.get("score", 0.0) or 0.0
                )
                regime_groups[regime_name].append(enriched)

    entries.sort(
        key=lambda item: (
            bool(item.get("eligible_for_governance")),
            item["score"],
            item["avg_return_pct"],
            item["avg_sharpe_ratio"],
        ),
        reverse=True,
    )
    eligible_rank = 1
    for idx, entry in enumerate(entries, start=1):
        entry["provisional_rank"] = idx
        if entry.get("eligible_for_governance"):
            entry["rank"] = eligible_rank
            eligible_rank += 1
        else:
            entry["rank"] = 0

    regime_leaderboards: Dict[str, List[Dict[str, Any]]] = {}
    for regime, items in regime_groups.items():
        ranked = sorted(
            items,
            key=lambda item: (
                float(item.get("_regime_score", item["score"]) or 0.0),
                float(
                    dict(item.get("regime_performance") or {})
                    .get(regime, {})
                    .get("compatibility", 0.0)
                    or 0.0
                ),
                item["score"],
                item["avg_return_pct"],
            ),
            reverse=True,
        )
        regime_leaderboards[regime] = [
            {
                "rank": idx,
                "manager_id": item["manager_id"],
                "manager_config_ref": item["manager_config_ref"],
                "score": item["score"],
                "regime_score": float(item.get("_regime_score", item["score"]) or 0.0),
                "avg_return_pct": item["avg_return_pct"],
                "avg_sharpe_ratio": item["avg_sharpe_ratio"],
                "benchmark_pass_rate": item["benchmark_pass_rate"],
                "eligible_for_governance": True,
                "cycles": int(
                    dict(item.get("regime_performance") or {})
                    .get(regime, {})
                    .get("cycles", 0)
                    or 0
                ),
                "compatibility": float(
                    dict(item.get("regime_performance") or {})
                    .get(regime, {})
                    .get("compatibility", 0.0)
                    or 0.0
                ),
                "source": "observed_regime",
                "scoring_mutation_count": item["scoring_mutation_count"],
            }
            for idx, item in enumerate(ranked, start=1)
        ]

    best_entry = next(
        (entry for entry in entries if entry.get("eligible_for_governance")), None
    )
    if best_entry is None:
        best_entry = entries[0] if entries else None

    return {
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "total_records": len(records),
        "total_managers": len(entries),
        "eligible_managers": sum(
            1 for entry in entries if entry.get("eligible_for_governance")
        ),
        "policy": policy_payload,
        "quality_gate_matrix": governance_matrix,
        "entries": entries,
        "best_entry": best_entry,
        "regime_leaderboards": regime_leaderboards,
    }


def build_leaderboard_payload(
    root_dir: str | Path,
    *,
    policy: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    root_path = Path(root_dir)
    records = collect_cycle_records(root_path)
    return build_leaderboard(records, policy=policy)


def write_leaderboard(
    root_dir: str | Path,
    output_path: str | Path | None = None,
    *,
    policy: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    root_path = Path(root_dir)
    leaderboard = build_leaderboard_payload(root_path, policy=policy)
    target = (
        Path(output_path) if output_path is not None else root_path / "leaderboard.json"
    )
    _write_json_atomic(target, leaderboard)
    return leaderboard


__all__ = [name for name in globals() if not name.startswith("_")]
