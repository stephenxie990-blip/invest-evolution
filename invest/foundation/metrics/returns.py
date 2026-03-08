from __future__ import annotations

from typing import Iterable, List


def compute_total_return_pct(values: Iterable[float]) -> float:
    seq: List[float] = [float(item) for item in values]
    if len(seq) < 2 or seq[0] == 0:
        return 0.0
    return (seq[-1] - seq[0]) / seq[0] * 100
