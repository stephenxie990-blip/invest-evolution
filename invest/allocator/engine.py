from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from invest.contracts import AllocationPlan


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
    rank: int = 0



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
        candidates = self._rank_candidates(normalized_regime, leaderboard)
        weights = self._blend_weights(normalized_regime, candidates, top_n=top_n)
        selected_configs = {item.model_name: item.config_name for item in candidates if item.model_name in weights}
        active_models = [name for name, weight in sorted(weights.items(), key=lambda pair: pair[1], reverse=True) if weight > 0]
        confidence = self._confidence(candidates)
        reasoning = self._build_reasoning(normalized_regime, active_models, weights, candidates)
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
            },
        )

    def _rank_candidates(self, regime: str, leaderboard: Dict[str, Any]) -> List[ModelScore]:
        regime_board = list((leaderboard.get("regime_leaderboards") or {}).get(regime, []))
        entries = list(leaderboard.get("entries") or [])
        if regime_board:
            regime_map = {item["model_name"]: item for item in regime_board}
            chosen = []
            for entry in entries:
                model_name = entry.get("model_name")
                if model_name not in regime_map:
                    continue
                chosen.append(ModelScore(
                    model_name=model_name,
                    config_name=str(entry.get("config_name", "")),
                    score=float(entry.get("score", 0.0) or 0.0),
                    avg_return_pct=float(entry.get("avg_return_pct", 0.0) or 0.0),
                    avg_sharpe_ratio=float(entry.get("avg_sharpe_ratio", 0.0) or 0.0),
                    avg_max_drawdown=float(entry.get("avg_max_drawdown", 0.0) or 0.0),
                    benchmark_pass_rate=float(entry.get("benchmark_pass_rate", 0.0) or 0.0),
                    rank=int(regime_map[model_name].get("rank", 0) or 0),
                ))
            if chosen:
                return self._dedupe_by_model(chosen)
        fallback = [
            ModelScore(
                model_name=str(entry.get("model_name", "unknown")),
                config_name=str(entry.get("config_name", "")),
                score=float(entry.get("score", 0.0) or 0.0),
                avg_return_pct=float(entry.get("avg_return_pct", 0.0) or 0.0),
                avg_sharpe_ratio=float(entry.get("avg_sharpe_ratio", 0.0) or 0.0),
                avg_max_drawdown=float(entry.get("avg_max_drawdown", 0.0) or 0.0),
                benchmark_pass_rate=float(entry.get("benchmark_pass_rate", 0.0) or 0.0),
                rank=int(entry.get("rank", 0) or 0),
            )
            for entry in entries
        ]
        return self._dedupe_by_model(fallback)

    def _dedupe_by_model(self, items: List[ModelScore]) -> List[ModelScore]:
        best: Dict[str, ModelScore] = {}
        for item in items:
            current = best.get(item.model_name)
            if current is None or item.score > current.score:
                best[item.model_name] = item
        return sorted(best.values(), key=lambda item: (item.score, item.avg_return_pct, item.avg_sharpe_ratio), reverse=True)

    def _blend_weights(self, regime: str, candidates: List[ModelScore], *, top_n: int) -> Dict[str, float]:
        priors = dict(self.priors.get(regime, self.priors["unknown"]))
        selected = candidates[:max(1, top_n)]
        bonus_scores: Dict[str, float] = {name: 0.0 for name in priors}
        if selected:
            max_score = max(max(item.score, 0.0) for item in selected) or 1.0
            for item in selected:
                normalized = max(0.0, item.score) / max_score
                bonus_scores[item.model_name] = 0.6 * normalized + 0.4 * item.benchmark_pass_rate
        combined: Dict[str, float] = {}
        for model_name, prior in priors.items():
            combined[model_name] = max(0.0, prior * 0.80 + bonus_scores.get(model_name, 0.0) * 0.20)
        total = sum(combined.values()) or 1.0
        normalized = {name: round(value / total, 4) for name, value in combined.items() if value > 0}
        normalized = self._apply_regime_caps(regime, normalized)
        normalized = {name: weight for name, weight in normalized.items() if weight >= 0.05}
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
        top = candidates[0].score
        second = candidates[1].score if len(candidates) > 1 else top * 0.8
        gap = max(0.0, top - second)
        return round(max(0.35, min(0.9, 0.55 + gap / 50.0)), 4)

    def _build_reasoning(self, regime: str, active_models: List[str], weights: Dict[str, float], candidates: List[ModelScore]) -> str:
        if not active_models:
            return f"当前 regime={regime}，历史样本不足，维持均衡配置。"
        leading = active_models[0]
        lead_weight = weights.get(leading, 0.0)
        top_score = next((item for item in candidates if item.model_name == leading), None)
        if top_score is None:
            return f"当前 regime={regime}，按先验规则优先启用 {leading}。"
        return (
            f"当前 regime={regime}，优先分配给 {leading}（权重 {lead_weight:.0%}），"
            f"因为其历史综合得分 {top_score.score:.2f}、平均收益 {top_score.avg_return_pct:+.2f}% 、"
            f"Sharpe {top_score.avg_sharpe_ratio:.2f} 相对更优；其余模型按先验与榜单表现做补充分配。"
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
