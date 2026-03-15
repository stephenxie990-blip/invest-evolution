from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from invest.contracts import AllocationPlan
from invest.shared.model_regime import regime_compatibility


DEFAULT_REGIME_PRIORS: Dict[str, Dict[str, float]] = {
    "bull": {
        "momentum": 0.45,
        "value_quality": 0.25,
        "defensive_low_vol": 0.15,
        "mean_reversion": 0.15,
    },
    "oscillation": {
        "mean_reversion": 0.40,
        "value_quality": 0.25,
        "defensive_low_vol": 0.25,
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
    "oscillation": {"momentum": 0.25, "mean_reversion": 0.50},
    "bear": {"momentum": 0.20, "defensive_low_vol": 0.60},
}

MIN_STYLE_ELIGIBILITY = 0.20

DEFAULT_CASH_RESERVE = {
    "bull": 0.10,
    "oscillation": 0.20,
    "bear": 0.30,
    "unknown": 0.20,
}


@dataclass
class ModelScore:
    model_name: str
    config_name: str
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



def load_leaderboard(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class ModelAllocator:
    """Rule + leaderboard driven model allocator. LLM is optional explanation layer only."""

    def __init__(self, priors: Optional[Dict[str, Dict[str, float]]] = None, cash_policy: Optional[Dict[str, float]] = None):
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
        candidates, used_provisional = self._rank_candidates(normalized_regime, leaderboard)
        weights = self._blend_weights(normalized_regime, candidates, top_n=top_n)
        selected_configs = {item.model_name: item.config_name for item in candidates if item.model_name in weights}
        active_models = [name for name, weight in sorted(weights.items(), key=lambda pair: pair[1], reverse=True) if weight > 0]
        confidence = self._confidence(candidates)
        if used_provisional:
            confidence = min(confidence, 0.55)
        reasoning = self._build_reasoning(
            normalized_regime,
            active_models,
            weights,
            candidates,
            used_provisional=used_provisional,
            failed_entries=list(leaderboard.get("entries") or []),
        )
        return AllocationPlan(
            as_of_date=as_of_date or datetime.now().strftime("%Y%m%d"),
            regime=normalized_regime,
            active_models=active_models,
            model_weights=weights,
            selected_configs=selected_configs,
            cash_reserve=self.cash_policy.get(normalized_regime, 0.20),
            confidence=confidence,
            reasoning=reasoning,
            metadata={
                "top_candidates": [item.__dict__ for item in candidates[:top_n]],
                "leaderboard_generated_at": leaderboard.get("generated_at"),
                "used_provisional_leaderboard": used_provisional,
                "qualified_candidate_count": len(candidates),
                "failed_quality_entries": [
                    {
                        "model_name": str(item.get("model_name") or ""),
                        "ineligible_reason": str(item.get("ineligible_reason") or ""),
                        "deployment_stage": str(item.get("deployment_stage") or ""),
                    }
                    for item in list(leaderboard.get("entries") or [])
                    if not bool(item.get("eligible_for_routing", False))
                ][:5],
            },
        )

    @staticmethod
    def _eligible_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            entry
            for entry in entries
            if bool(entry.get("eligible_for_routing", True))
        ]

    def _rank_candidates(self, regime: str, leaderboard: Dict[str, Any]) -> tuple[List[ModelScore], bool]:
        regime_board = list((leaderboard.get("regime_leaderboards") or {}).get(regime, []))
        entries = list(leaderboard.get("entries") or [])
        filtered_entries = self._eligible_entries(entries)
        used_provisional = False
        if not filtered_entries:
            return [], used_provisional
        if regime_board:
            regime_map = {
                item["model_name"]: item
                for item in regime_board
                if bool(item.get("eligible_for_routing", True)) or used_provisional
            }
            chosen = []
            for entry in filtered_entries:
                model_name = str(entry.get("model_name", "unknown") or "unknown")
                if model_name not in regime_map:
                    continue
                compatibility = float(
                    regime_map[model_name].get("compatibility")
                    or entry.get("style_profile", {}).get(regime)
                    or regime_compatibility(model_name, regime)
                )
                chosen.append(ModelScore(
                    model_name=model_name,
                    config_name=str(entry.get("config_name", "")),
                    score=float(entry.get("score", 0.0) or 0.0),
                    avg_return_pct=float(entry.get("avg_return_pct", 0.0) or 0.0),
                    avg_sharpe_ratio=float(entry.get("avg_sharpe_ratio", 0.0) or 0.0),
                    avg_max_drawdown=float(entry.get("avg_max_drawdown", 0.0) or 0.0),
                    benchmark_pass_rate=float(entry.get("benchmark_pass_rate", 0.0) or 0.0),
                    avg_strategy_score=float(entry.get("avg_strategy_score", 0.0) or 0.0),
                    rank=int(regime_map[model_name].get("rank", 0) or 0),
                    regime_score=float(regime_map[model_name].get("regime_score", entry.get("score", 0.0)) or 0.0),
                    regime_compatibility=compatibility,
                    regime_cycles=int(regime_map[model_name].get("cycles", 0) or 0),
                    regime_source=str(regime_map[model_name].get("source") or "leaderboard"),
                ))
            if chosen:
                return self._dedupe_by_model(self._apply_style_filter(regime, chosen)), used_provisional
        fallback = [
            ModelScore(
                model_name=str(entry.get("model_name", "unknown")),
                config_name=str(entry.get("config_name", "")),
                score=float(entry.get("score", 0.0) or 0.0),
                avg_return_pct=float(entry.get("avg_return_pct", 0.0) or 0.0),
                avg_sharpe_ratio=float(entry.get("avg_sharpe_ratio", 0.0) or 0.0),
                avg_max_drawdown=float(entry.get("avg_max_drawdown", 0.0) or 0.0),
                benchmark_pass_rate=float(entry.get("benchmark_pass_rate", 0.0) or 0.0),
                avg_strategy_score=float(entry.get("avg_strategy_score", 0.0) or 0.0),
                rank=int(entry.get("rank", 0) or 0),
                regime_score=float(entry.get("score", 0.0) or 0.0) * regime_compatibility(entry.get("model_name"), regime),
                regime_compatibility=regime_compatibility(entry.get("model_name"), regime),
                regime_cycles=int(dict(entry.get("regime_performance") or {}).get(regime, {}).get("cycles", 0) or 0),
                regime_source="fallback",
            )
            for entry in filtered_entries
        ]
        return self._dedupe_by_model(self._apply_style_filter(regime, fallback)), used_provisional

    def _apply_style_filter(self, regime: str, items: List[ModelScore]) -> List[ModelScore]:
        if regime == "unknown":
            return items
        compatible = [
            item
            for item in items
            if float(item.regime_compatibility or 0.0) >= MIN_STYLE_ELIGIBILITY
        ]
        return compatible or items

    def _dedupe_by_model(self, items: List[ModelScore]) -> List[ModelScore]:
        best: Dict[str, ModelScore] = {}
        for item in items:
            current = best.get(item.model_name)
            if current is None or item.score > current.score:
                best[item.model_name] = item
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

    def _blend_weights(self, regime: str, candidates: List[ModelScore], *, top_n: int) -> Dict[str, float]:
        if not candidates:
            return {}
        priors = dict(self.priors.get(regime, self.priors["unknown"]))
        selected = candidates[:max(1, top_n)]
        bonus_scores: Dict[str, float] = {name: 0.0 for name in priors}
        if selected:
            max_score = max(max(item.regime_score, 0.0) for item in selected) or 1.0
            for item in selected:
                normalized = max(0.0, item.regime_score) / max_score
                bonus_scores[item.model_name] = (
                    0.40 * normalized
                    + 0.20 * item.benchmark_pass_rate
                    + 0.20 * max(0.0, min(1.0, item.avg_strategy_score))
                    + 0.20 * float(item.regime_compatibility or 0.0)
                )
        combined: Dict[str, float] = {}
        for model_name, prior in priors.items():
            compatibility = regime_compatibility(model_name, regime)
            combined[model_name] = max(
                0.0,
                (prior * 0.65 + bonus_scores.get(model_name, 0.0) * 0.35) * max(0.25, compatibility),
            )
        total = sum(combined.values()) or 1.0
        normalized = {name: round(value / total, 4) for name, value in combined.items() if value > 0}
        normalized = self._apply_regime_caps(regime, normalized)
        normalized = {name: weight for name, weight in normalized.items() if weight >= 0.01}
        total = sum(normalized.values()) or 1.0
        return {name: round(weight / total, 4) for name, weight in sorted(normalized.items(), key=lambda pair: pair[1], reverse=True)}

    def _apply_regime_caps(self, regime: str, weights: Dict[str, float]) -> Dict[str, float]:
        capped = dict(weights)
        caps = dict(self.weight_caps.get(regime, {}))
        if not caps:
            return capped
        for model_name, cap in caps.items():
            if model_name not in capped:
                continue
            capped[model_name] = min(capped[model_name], cap)
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

    def _confidence(self, candidates: List[ModelScore]) -> float:
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
        active_models: List[str],
        weights: Dict[str, float],
        candidates: List[ModelScore],
        *,
        used_provisional: bool,
        failed_entries: List[Dict[str, Any]],
    ) -> str:
        if not active_models:
            failed_brief = next(
                (
                    item for item in failed_entries
                    if str(item.get("ineligible_reason") or "").strip()
                ),
                {},
            )
            reason = str(failed_brief.get("ineligible_reason") or "no_qualified_routing_candidates")
            return f"当前 regime={regime}，榜单中没有通过质量门的正式候选，保持当前 active，不启用 provisional 候选；首个阻断原因为 {reason}。"
        leading = active_models[0]
        lead_weight = weights.get(leading, 0.0)
        top_score = next((item for item in candidates if item.model_name == leading), None)
        if top_score is None:
            return f"当前 regime={regime}，按先验规则优先启用 {leading}。"
        provisional_note = " 当前为 provisional 分配。" if used_provisional else ""
        return (
            f"当前 regime={regime}，优先分配给 {leading}（权重 {lead_weight:.0%}），"
            f"因为其当前市场风格兼容度 {top_score.regime_compatibility:.0%}、"
            f"regime 分数 {top_score.regime_score:.2f}、策略评分 {top_score.avg_strategy_score:.2f}、平均收益 {top_score.avg_return_pct:+.2f}% 、"
            f"Sharpe {top_score.avg_sharpe_ratio:.2f} 相对更优，且已通过正式路由质量门；其余模型按风格兼容性、先验与榜单表现做补充分配。{provisional_note}"
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
