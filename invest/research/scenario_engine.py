from __future__ import annotations

from statistics import mean
from typing import Any, Dict

from .case_store import ResearchCaseStore
from .contracts import DEFAULT_HORIZONS, PolicySnapshot, ResearchSnapshot


class ResearchScenarioEngine:
    def __init__(self, case_store: ResearchCaseStore):
        self.case_store = case_store

    def estimate(self, *, snapshot: ResearchSnapshot, policy: PolicySnapshot, stance: str) -> Dict[str, Any]:
        regime = str(snapshot.market_context.get("regime") or "unknown")
        similar = list(
            self.case_store.iter_similar_attributions(
                model_name=policy.model_name,
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
            "bull_case": {"description": f"{policy.model_name} 强势延续", "return_pct": base["interval"]["p75"]},
            "base_case": {"description": f"{policy.model_name} 中性演化", "return_pct": base["interval"]["p50"]},
            "bear_case": {"description": f"{policy.model_name} 失效回撤", "return_pct": base["interval"]["p25"]},
        }
