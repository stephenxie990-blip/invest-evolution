from __future__ import annotations

from typing import Dict


def compute_per_stock_contribution(per_stock_pnl: Dict[str, float]) -> Dict[str, float]:
    total = sum(float(value) for value in per_stock_pnl.values())
    if total == 0:
        return {code: 0.0 for code in per_stock_pnl}
    return {code: float(value) / total for code, value in per_stock_pnl.items()}
