from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

import numpy as np


def _safe_avg(values: List[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _safe_std(values: List[float]) -> float:
    if len(values) <= 1:
        return 0.0
    return float(np.std(np.asarray(values, dtype=float), ddof=0))


def build_regime_confidence_summary(
    regime_items: List[Dict[str, Any]],
    *,
    min_cycles_per_regime: int,
) -> Dict[str, Any]:
    returns = [float(item.get("return_pct", 0.0) or 0.0) for item in regime_items]
    benchmark_passes = sum(
        1 for item in regime_items if bool(item.get("benchmark_passed", False))
    )
    sample_count = len(regime_items)
    coverage_ratio = (
        min(1.0, sample_count / max(1, int(min_cycles_per_regime)))
        if min_cycles_per_regime > 0
        else 1.0
    )
    return_std = _safe_std(returns)
    stability = 1.0 / (1.0 + max(0.0, return_std) / 10.0)
    confidence_score = round(coverage_ratio * stability, 6)
    exploratory_only = sample_count < max(1, int(min_cycles_per_regime))
    return {
        "sample_count": sample_count,
        "coverage_ratio": round(coverage_ratio, 6),
        "avg_return_pct": round(_safe_avg(returns), 6),
        "return_std_pct": round(return_std, 6),
        "benchmark_pass_rate": round(benchmark_passes / sample_count, 6)
        if sample_count
        else 0.0,
        "confidence_score": confidence_score,
        "exploratory_only": exploratory_only,
    }


def build_regime_confidence_map(
    items: List[Dict[str, Any]],
    *,
    min_cycles_per_regime: int,
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in list(items or []):
        grouped[str(item.get("regime") or "unknown")].append(item)
    return {
        regime: build_regime_confidence_summary(
            regime_items,
            min_cycles_per_regime=min_cycles_per_regime,
        )
        for regime, regime_items in sorted(grouped.items())
    }
