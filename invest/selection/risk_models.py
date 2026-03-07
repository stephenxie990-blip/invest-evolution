from typing import Dict, List, Optional

import numpy as np
import pandas as pd


class RiskFactorModel:
    """
    风险因子模型

    行业中性化（每行业最多 max_per_industry 只）
    市值中性化（大/中/小盘各选一半）
    """

    def get_industry(self, code: str) -> str:
        from config import industry_registry
        return industry_registry.get_industry(code)

    def neutralize_by_industry(
        self, stocks: List[FactorResult], max_per_industry: int = 3
    ) -> List[FactorResult]:
        counts: Dict[str, int] = {}
        result = []
        for s in stocks:
            ind = self.get_industry(s.code)
            if counts.get(ind, 0) < max_per_industry:
                result.append(s)
                counts[ind] = counts.get(ind, 0) + 1
        return result

    def neutralize_by_market_cap(
        self, stocks: List[FactorResult], market_caps: Dict[str, float]
    ) -> List[FactorResult]:
        if not stocks:
            return stocks
        by_cap = sorted(stocks, key=lambda s: market_caps.get(s.code, 0), reverse=True)
        n = len(by_cap)
        result: List[FactorResult] = []
        for group in [by_cap[:n//3], by_cap[n//3:2*n//3], by_cap[2*n//3:]]:
            result.extend(group[:max(len(group)//2, 1)])
        return result

    def apply_risk_controls(
        self,
        stocks: List[FactorResult],
        market_caps: Optional[Dict[str, float]] = None,
        max_per_industry: int = 3,
    ) -> List[FactorResult]:
        result = self.neutralize_by_industry(stocks, max_per_industry)
        if market_caps:
            result = self.neutralize_by_market_cap(result, market_caps)
        return result


__all__ = ["RiskFactorModel"]
